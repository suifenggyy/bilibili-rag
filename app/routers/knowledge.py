"""
Bilibili RAG 知识库系统

知识库路由 - 构建和管理知识库
"""
from datetime import datetime
import asyncio
from fastapi import APIRouter, HTTPException, Query, BackgroundTasks, Depends
from loguru import logger
from typing import List, Optional, Callable
from pydantic import BaseModel
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, get_db_context
from app.models import FavoriteFolder, FavoriteVideo, VideoCache, UserSession, ContentSource, VideoContent, BiliCreator
from app.services.bilibili import BilibiliService
from app.services.content_fetcher import ContentFetcher
from app.services.asr_factory import create_asr_service
from app.services.rag import RAGService
from app.services.processing_status import ProcessingStatusService
from app.routers.auth import get_session

router = APIRouter(prefix="/knowledge", tags=["知识库"])

# 全局 RAG 服务实例
_rag_service: Optional[RAGService] = None
_proc_svc = ProcessingStatusService()

# 构建任务状态
build_tasks = {}


def get_rag_service() -> RAGService:
    """获取 RAG 服务实例"""
    global _rag_service
    if _rag_service is None:
        _rag_service = RAGService()
    return _rag_service


class BuildRequest(BaseModel):
    """知识库构建请求"""
    folder_ids: List[int]  # 要处理的收藏夹 ID 列表
    exclude_bvids: Optional[List[str]] = None  # 排除的视频


class BuildStatus(BaseModel):
    """构建状态"""
    task_id: str
    status: str  # pending / running / completed / failed
    progress: int  # 0-100
    current_step: str
    total_videos: int
    processed_videos: int
    message: str


class FolderStatus(BaseModel):
    """收藏夹入库状态"""
    media_id: int
    indexed_count: int
    media_count: Optional[int] = None
    last_sync_at: Optional[datetime] = None


class SyncRequest(BaseModel):
    """同步请求"""
    folder_ids: Optional[List[int]] = None


class CreatorRequest(BaseModel):
    """添加 UP主请求"""
    uid: str
    nickname: Optional[str] = None
    after_date: Optional[str] = None   # YYYY-MM-DD


class CreatorResponse(BaseModel):
    """UP主信息响应"""
    id: int
    uid: str
    nickname: Optional[str] = None
    after_date: Optional[str] = None


class SyncResult(BaseModel):
    """同步结果"""
    folder_id: int
    total: int
    added: int
    removed: int
    indexed: int
    message: str
    last_sync_at: Optional[datetime] = None


async def _get_or_create_folder(
    db: AsyncSession,
    session_id: str,
    media_id: int,
    title: Optional[str] = None,
    media_count: Optional[int] = None,
) -> FavoriteFolder:
    """获取或创建收藏夹记录"""
    result = await db.execute(
        select(FavoriteFolder).where(
            FavoriteFolder.session_id == session_id,
            FavoriteFolder.media_id == media_id,
        )
    )
    folder = result.scalar_one_or_none()

    if folder is None:
        folder = FavoriteFolder(
            session_id=session_id,
            media_id=media_id,
            title=title or "",
            media_count=media_count or 0,
            is_selected=True,
        )
        db.add(folder)
        await db.flush()
    else:
        if title:
            folder.title = title
        if media_count is not None:
            folder.media_count = media_count

    return folder


def _extract_video_info(media: dict) -> tuple[str, str, Optional[int]]:
    """抽取视频关键信息"""
    bvid = media.get("bvid") or media.get("bv_id")
    title = media.get("title", bvid)
    cid = None
    ugc = media.get("ugc") or {}
    if ugc.get("first_cid"):
        cid = ugc.get("first_cid")
    else:
        cid = media.get("cid") or media.get("id")
    return bvid, title, cid


async def _upsert_video_cache(db: AsyncSession, bvid: str, meta: dict) -> None:
    """写入或更新视频缓存信息"""
    result = await db.execute(select(VideoCache).where(VideoCache.bvid == bvid))
    cache = result.scalar_one_or_none()

    if cache is None:
        cache = VideoCache(
            bvid=bvid,
            title=meta.get("title") or bvid,
            description=meta.get("intro"),
            owner_name=meta.get("owner_name"),
            owner_mid=meta.get("owner_mid"),
            duration=meta.get("duration"),
            pic_url=meta.get("cover"),
            is_processed=False,
        )
        db.add(cache)
        return

    cache.title = meta.get("title") or cache.title
    if meta.get("intro") is not None:
        cache.description = meta.get("intro")
    if meta.get("owner_name") is not None:
        cache.owner_name = meta.get("owner_name")
    if meta.get("owner_mid") is not None:
        cache.owner_mid = meta.get("owner_mid")
    if meta.get("duration") is not None:
        cache.duration = meta.get("duration")
    if meta.get("cover") is not None:
        cache.pic_url = meta.get("cover")


async def _sync_folder(
    db: AsyncSession,
    bili: BilibiliService,
    rag: RAGService,
    content_fetcher: ContentFetcher,
    session_id: str,
    folder_id: int,
    exclude_bvids: Optional[set[str]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """同步单个收藏夹到向量库"""
    info = {}
    try:
        info_result = await bili.get_favorite_content(folder_id, pn=1, ps=1)
        info = info_result.get("info", {})
    except Exception as e:
        logger.warning(f"获取收藏夹信息失败 [{folder_id}]: {e}")

    videos = await bili.get_all_favorite_videos(folder_id)
    total_in_folder = info.get("media_count", len(videos))

    # 保护：接口异常返回空列表时，避免误删
    if not videos:
        if total_in_folder and total_in_folder > 0:
            logger.warning(f"[{folder_id}] 收藏夹返回空列表，跳过删除逻辑")
            existing_count = await db.scalar(
                select(func.count(FavoriteVideo.bvid))
                .where(FavoriteVideo.folder_id == folder_id)
            )
            return {
                "folder_id": folder_id,
                "total": total_in_folder,
                "added": 0,
                "removed": 0,
                "indexed": existing_count or 0,
                "message": "本次同步异常：空列表，已跳过",
                "last_sync_at": datetime.utcnow(),
            }

    video_map = {}
    skipped_invalid = 0
    for media in videos:
        bvid, title, cid = _extract_video_info(media)
        if not bvid:
            continue
        if exclude_bvids and bvid in exclude_bvids:
            continue
        
        # 过滤失效视频（被删除、下架等）
        # attr 字段: 0=正常, 9=已失效, 1=私密等
        attr = media.get("attr", 0)
        if attr == 9 or title in ["已失效视频", "已删除视频"]:
            skipped_invalid += 1
            logger.debug(f"跳过失效视频: {bvid} - {title}")
            continue
        
        owner = media.get("upper") or {}
        video_map[bvid] = {
            "title": title,
            "cid": cid,
            "intro": media.get("intro"),
            "cover": media.get("cover"),
            "duration": media.get("duration"),
            "owner_name": owner.get("name"),
            "owner_mid": owner.get("mid"),
        }
    
    if skipped_invalid > 0:
        logger.info(f"[{folder_id}] 过滤了 {skipped_invalid} 个失效视频")

    # 以有效视频数作为统计口径（过滤失效视频）
    valid_count = len(video_map)
    current_bvids = set(video_map.keys())

    folder = await _get_or_create_folder(
        db,
        session_id=session_id,
        media_id=folder_id,
        title=info.get("title"),
        media_count=valid_count,
    )

    existing_rows = await db.execute(
        select(FavoriteVideo.bvid).where(FavoriteVideo.folder_id == folder.id)
    )
    existing_bvids = {row[0] for row in existing_rows.fetchall()}

    added = current_bvids - existing_bvids
    removed = existing_bvids - current_bvids

    # 写入标题/简介等信息
    for bvid, meta in video_map.items():
        await _upsert_video_cache(db, bvid, meta)

    source_priority = {
        ContentSource.BASIC_INFO.value: 1,
        ContentSource.AI_SUMMARY.value: 2,
        ContentSource.SUBTITLE.value: 3,
        ContentSource.ASR.value: 4,
    }

    def _is_better_source(new_source: str, old_source: Optional[str]) -> bool:
        return source_priority.get(new_source, 0) > source_priority.get(old_source or "", 0)

    def _should_refresh_cache(cache: Optional[VideoCache]) -> bool:
        if not cache:
            return True
        text = (cache.content or "").strip()
        if len(text) < 50:
            return True
        if cache.content_source in (None, "", ContentSource.BASIC_INFO.value):
            return True
        return False

    def _is_asr_cache_usable(cache: Optional[VideoCache]) -> bool:
        if not cache:
            return False
        if cache.content_source != ContentSource.ASR.value:
            return False
        text = (cache.content or "").strip()
        return len(text) >= 50

    # 需要更新的已存在视频（缓存过少或来源较弱）
    update_candidates: set[str] = set()
    for bvid in current_bvids & existing_bvids:
        if bvid in added:
            continue
        result = await db.execute(select(VideoCache).where(VideoCache.bvid == bvid))
        cache = result.scalar_one_or_none()
        if _should_refresh_cache(cache):
            update_candidates.add(bvid)

    # 新增/更新向量与关联
    targets = list(added) + list(update_candidates)
    total_targets = len(targets)
    processed_targets = 0
    if progress_callback:
        progress_callback("准备处理", processed_targets, total_targets)

    # ── Phase 1 (sequential): read cache state for each video ───────────────
    fetch_infos: list[dict] = []
    for bvid in targets:
        meta = video_map[bvid]

        # Skip if already fully indexed
        async with get_db_context() as db_proc:
            proc_rec = await _proc_svc.get_or_create(db_proc, "bilibili", bvid, meta.get("title"))
            await db_proc.commit()
        if _proc_svc.is_completed(proc_rec):
            logger.info(f"[{bvid}] 已完成索引，跳过")
            processed_targets += 1
            if progress_callback:
                progress_callback("跳过（已完成）", processed_targets, total_targets)
            # Still write FavoriteVideo record
            try:
                exists_row = await db.execute(
                    select(FavoriteVideo.id).where(
                        FavoriteVideo.folder_id == folder.id,
                        FavoriteVideo.bvid == bvid,
                    )
                )
                if exists_row.scalar_one_or_none() is None:
                    db.add(FavoriteVideo(folder_id=folder.id, bvid=bvid, is_selected=True))
            except Exception as e:
                logger.error(f"写入数据库失败 [{bvid}]: {e}")
            continue

        global_count = await db.scalar(
            select(func.count()).select_from(FavoriteVideo).where(FavoriteVideo.bvid == bvid)
        )
        result = await db.execute(select(VideoCache).where(VideoCache.bvid == bvid))
        cache = result.scalar_one_or_none()
        old_content = (cache.content or "").strip() if cache else ""
        old_source = cache.content_source if cache else None

        fetch_infos.append({
            "bvid": bvid,
            "meta": meta,
            "cache": cache,
            "global_count": global_count,
            "old_content": old_content,
            "old_source": old_source,
            "needs_fetch": _should_refresh_cache(cache),
        })

    # ── Phase 2 (concurrent): fetch content for videos that need it ─────────
    concurrency = getattr(settings, "asr_concurrency", 2)
    sem = asyncio.Semaphore(concurrency)

    async def _fetch_one(info: dict):
        bvid = info["bvid"]
        meta = info["meta"]
        if not info["needs_fetch"]:
            return None
        try:
            async with sem:
                return await content_fetcher.fetch_content(
                    bvid, cid=meta["cid"], title=meta["title"]
                )
        except Exception as e:
            logger.warning(f"[{bvid}] 并发 fetch 失败: {e}")
            return None

    fetched_contents: list = await asyncio.gather(*[_fetch_one(i) for i in fetch_infos])

    # ── Phase 3 (sequential): save results, update vectors, write DB ─────────
    for info, content in zip(fetch_infos, fetched_contents):
        bvid = info["bvid"]
        meta = info["meta"]
        cache = info["cache"]
        global_count = info["global_count"]
        old_content = info["old_content"]
        old_source = info["old_source"]

        # 尝试添加到向量库（可能失败，但不影响记录入库）
        try:
            should_update_cache = False
            should_reindex = False

            if content is not None:
                new_text = (content.content or "").strip()
                new_source = content.source.value if content else None

                if not old_content:
                    should_update_cache = True
                    should_reindex = True
                elif new_source and _is_better_source(new_source, old_source):
                    should_update_cache = True
                    should_reindex = True
                elif new_text and new_text != old_content:
                    should_update_cache = True
                    should_reindex = True

                if cache and should_update_cache:
                    cache.content = content.content
                    cache.content_source = content.source.value
                    cache.outline_json = content.outline
                    cache.is_processed = True
                    logger.info(f"[{bvid}] 已写入缓存: source={cache.content_source}")

            # 需要重建向量：新增/升级/内容变化 或 向量缺失
            if (global_count == 0) or should_reindex:
                if not content:
                    if _is_asr_cache_usable(cache):
                        content = VideoContent(
                            bvid=bvid,
                            title=meta["title"],
                            content=(cache.content or "").strip(),
                            source=ContentSource.ASR,
                            outline=cache.outline_json,
                        )
                        cache.is_processed = True
                        logger.info(f"[{bvid}] 使用缓存 ASR 内容重建向量")
                    else:
                        content = await content_fetcher.fetch_content(
                            bvid, cid=meta["cid"], title=meta["title"]
                        )
                        if cache:
                            cache.content = content.content
                            cache.content_source = content.source.value
                            cache.outline_json = content.outline
                            cache.is_processed = True
                            logger.info(f"[{bvid}] 已写入缓存: source={cache.content_source}")
                try:
                    rag.delete_video(bvid)
                except Exception as e:
                    logger.warning(f"删除旧向量失败 [{bvid}]: {e}")
                chunks = rag.add_video_content(content)
                logger.info(f"[{bvid}] 向量化完成，块数={chunks}")
                # Mark processing stages
                try:
                    async with get_db_context() as db_s:
                        r_s = await _proc_svc.get_or_create(db_s, "bilibili", bvid, meta.get("title"))
                        if content.asr_raw_text:
                            await _proc_svc.mark_asr_done(db_s, r_s, content.asr_raw_text)
                            await _proc_svc.mark_correction_done(db_s, r_s, content.content or "")
                        if content.summary_block:
                            await _proc_svc.mark_summary_done(db_s, r_s, content.summary_block)
                        await _proc_svc.mark_completed(db_s, r_s)
                        await db_s.commit()
                except Exception as _e:
                    logger.warning(f"[{bvid}] 处理状态记录失败（非关键）: {_e}")
            else:
                logger.info(f"[{bvid}] 内容未变化或无需升级，跳过向量化")
        except Exception as e:
            logger.warning(f"添加向量失败 [{bvid}]: {e} (仍会记录到数据库)")
            try:
                async with get_db_context() as db_err:
                    r_err = await _proc_svc.get_or_create(db_err, "bilibili", bvid)
                    await _proc_svc.mark_failed(db_err, r_err, "index", str(e))
                    await db_err.commit()
            except Exception:
                pass

        # 无论向量是否添加成功，都写入 FavoriteVideo 记录
        try:
            exists_row = await db.execute(
                select(FavoriteVideo.id).where(
                    FavoriteVideo.folder_id == folder.id,
                    FavoriteVideo.bvid == bvid,
                )
            )
            if exists_row.scalar_one_or_none() is None:
                db.add(FavoriteVideo(folder_id=folder.id, bvid=bvid, is_selected=True))
            processed_targets += 1
            if progress_callback:
                progress_callback(meta["title"], processed_targets, total_targets)
        except Exception as e:
            logger.error(f"写入数据库失败 [{bvid}]: {e}")

    # 删除无效向量
    if removed:
        for bvid in removed:
            other_count = await db.scalar(
                select(func.count())
                .select_from(FavoriteVideo)
                .where(
                    FavoriteVideo.bvid == bvid,
                    FavoriteVideo.folder_id != folder.id,
                )
            )
            if other_count == 0:
                try:
                    rag.delete_video(bvid)
                except Exception as e:
                    logger.warning(f"删除向量失败 [{bvid}]: {e}")

        await db.execute(
            delete(FavoriteVideo).where(
                FavoriteVideo.folder_id == folder.id,
                FavoriteVideo.bvid.in_(removed),
            )
        )

    folder.last_sync_at = datetime.utcnow()

    await db.commit()

    indexed_count = await db.scalar(
        select(func.count(func.distinct(FavoriteVideo.bvid)))
        .select_from(FavoriteVideo)
        .where(FavoriteVideo.folder_id == folder.id)
    )

    return {
        "folder_id": folder_id,
        "total": valid_count,
        "added": len(added),
        "removed": len(removed),
        "indexed": indexed_count or 0,
        "message": "同步完成",
        "last_sync_at": folder.last_sync_at,
    }


@router.get("/stats")
async def get_knowledge_stats():
    """获取知识库统计信息"""
    try:
        rag = get_rag_service()
        stats = rag.get_collection_stats()
        return stats
    except Exception as e:
        logger.error(f"获取统计信息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/folders/status", response_model=List[FolderStatus])
async def get_folder_status(
    session_id: str = Query(..., description="会话ID"),
    db: AsyncSession = Depends(get_db),
):
    """获取收藏夹入库状态（跨 Session 查找同一用户的数据）"""
    
    # 1. 先查当前 Session 对应的用户 MID
    result = await db.execute(
        select(UserSession.bili_mid).where(UserSession.session_id == session_id)
    )
    mid = result.scalar()
    
    target_session_ids = [session_id]
    
    if mid:
        # 2. 如果有 MID，查找该用户所有的 Session ID
        result = await db.execute(
            select(UserSession.session_id).where(UserSession.bili_mid == mid)
        )
        target_session_ids = [row[0] for row in result.fetchall()]
    
    # 3. 查询所有关联 Session 的收藏夹状态
    # 使用 group_by media_id 来去重，取最新的那个
    rows = await db.execute(
        select(FavoriteFolder.id, FavoriteFolder.media_id, FavoriteFolder.last_sync_at)
        .where(FavoriteFolder.session_id.in_(target_session_ids))
        .order_by(FavoriteFolder.updated_at.desc())
    )
    
    # 手动按 media_id 去重，保留最新的
    folders_map = {}
    for row in rows.fetchall():
        fid, media_id, last_sync = row
        if media_id not in folders_map:
            folders_map[media_id] = (fid, last_sync)
            
    if not folders_map:
        return []

    folder_ids = [v[0] for v in folders_map.values()]
    
    # 4. 统计视频数量
    counts = await db.execute(
        select(FavoriteVideo.folder_id, func.count(func.distinct(FavoriteVideo.bvid)))
        .where(FavoriteVideo.folder_id.in_(folder_ids))
        .group_by(FavoriteVideo.folder_id)
    )
    count_map = {row[0]: row[1] for row in counts.fetchall()}

    result = []
    for media_id, (folder_id, last_sync_at) in folders_map.items():
        # 读取有效视频数（过滤失效后的口径）
        folder_row = await db.execute(
            select(FavoriteFolder.media_count).where(FavoriteFolder.id == folder_id)
        )
        media_count = folder_row.scalar()
        result.append(
            FolderStatus(
                media_id=media_id,
                indexed_count=count_map.get(folder_id, 0),
                media_count=media_count,
                last_sync_at=last_sync_at,
            )
        )
    return result


@router.post("/folders/sync", response_model=List[SyncResult])
async def sync_folders(
    request: SyncRequest,
    session_id: str = Query(..., description="会话ID"),
    db: AsyncSession = Depends(get_db),
):
    """同步收藏夹到向量库"""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")

    cookies = session.get("cookies", {})
    user_info = session.get("user_info", {})

    bili = BilibiliService(
        sessdata=cookies.get("SESSDATA"),
        bili_jct=cookies.get("bili_jct"),
        dedeuserid=cookies.get("DedeUserID"),
    )
    rag = get_rag_service()
    asr_service = create_asr_service()
    content_fetcher = ContentFetcher(bili, asr_service)

    try:
        folder_ids = request.folder_ids or []
        if not folder_ids:
            mid = user_info.get("mid") or cookies.get("DedeUserID")
            if not mid:
                raise HTTPException(status_code=400, detail="无法获取用户信息")
            folders = await bili.get_user_favorites(mid=mid)
            folder_ids = [folder.get("id") for folder in folders if folder.get("id")]

        results: List[SyncResult] = []
        for folder_id in folder_ids:
            try:
                result = await _sync_folder(
                    db,
                    bili,
                    rag,
                    content_fetcher,
                    session_id,
                    folder_id,
                )
                results.append(SyncResult(**result))
            except Exception as e:
                logger.error(f"同步收藏夹失败 [{folder_id}]: {e}")
                results.append(
                    SyncResult(
                        folder_id=folder_id,
                        total=0,
                        added=0,
                        removed=0,
                        indexed=0,
                        message=f"同步失败: {e}",
                        last_sync_at=None,
                    )
                )

        return results
    finally:
        await bili.close()


@router.post("/build")
async def build_knowledge_base(
    request: BuildRequest,
    background_tasks: BackgroundTasks,
    session_id: str = Query(..., description="会话ID"),
):
    """构建知识库（后台任务）"""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")

    import uuid
    task_id = str(uuid.uuid4())

    build_tasks[task_id] = {
        "status": "pending",
        "progress": 0,
        "current_step": "初始化中...",
        "total_videos": 0,
        "processed_videos": 0,
        "message": "",
    }

    background_tasks.add_task(
        _build_knowledge_base_task,
        task_id,
        session_id,
        session,
        request.folder_ids,
        request.exclude_bvids or [],
    )

    return {"task_id": task_id, "message": "构建任务已启动"}


async def _build_knowledge_base_task(
    task_id: str,
    session_id: str,
    session: dict,
    folder_ids: List[int],
    exclude_bvids: List[str],
):
    """后台构建任务"""
    cookies = session.get("cookies", {})

    try:
        build_tasks[task_id]["status"] = "running"
        build_tasks[task_id]["current_step"] = "同步收藏夹..."

        bili = BilibiliService(
            sessdata=cookies.get("SESSDATA"),
            bili_jct=cookies.get("bili_jct"),
            dedeuserid=cookies.get("DedeUserID"),
        )
        asr_service = create_asr_service()
        content_fetcher = ContentFetcher(bili, asr_service)
        rag = get_rag_service()

        try:
            total_folders = len(folder_ids)
            if total_folders == 0:
                build_tasks[task_id]["status"] = "completed"
                build_tasks[task_id]["progress"] = 100
                build_tasks[task_id]["message"] = "没有需要处理的收藏夹"
                return

            processed = 0
            total_added = 0
            total_removed = 0

            async with get_db_context() as db:
                for idx, folder_id in enumerate(folder_ids, start=1):
                    build_tasks[task_id]["current_step"] = f"同步收藏夹 {folder_id}"

                    def progress_cb(title: str, processed_count: int = 0, total_count: int = 0):
                        build_tasks[task_id]["current_step"] = f"处理: {title}"
                        if total_count:
                            build_tasks[task_id]["total_videos"] = total_count
                        if processed_count:
                            build_tasks[task_id]["processed_videos"] = processed_count
                            if build_tasks[task_id]["total_videos"]:
                                build_tasks[task_id]["progress"] = int(
                                    (processed_count / build_tasks[task_id]["total_videos"]) * 100
                                )

                    result = await _sync_folder(
                        db,
                        bili,
                        rag,
                        content_fetcher,
                        session_id,
                        folder_id,
                        exclude_bvids=set(exclude_bvids),
                        progress_callback=progress_cb,
                    )

                    processed = idx
                    total_added += result["added"]
                    total_removed += result["removed"]

            build_tasks[task_id]["status"] = "completed"
            build_tasks[task_id]["progress"] = 100
            build_tasks[task_id]["processed_videos"] = total_folders
            build_tasks[task_id]["current_step"] = "完成"
            build_tasks[task_id]["message"] = f"同步完成：新增 {total_added}，移除 {total_removed}"

            logger.info(f"知识库构建完成: 新增 {total_added}，移除 {total_removed}")
        finally:
            await bili.close()

    except Exception as e:
        logger.error(f"构建任务失败: {e}")
        build_tasks[task_id]["status"] = "failed"
        build_tasks[task_id]["message"] = str(e)


@router.get("/build/status/{task_id}", response_model=BuildStatus)
async def get_build_status(task_id: str):
    """获取构建任务状态"""
    if task_id not in build_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")

    task = build_tasks[task_id]
    return BuildStatus(
        task_id=task_id,
        status=task["status"],
        progress=task["progress"],
        current_step=task["current_step"],
        total_videos=task["total_videos"],
        processed_videos=task["processed_videos"],
        message=task["message"],
    )


@router.delete("/clear")
async def clear_knowledge_base():
    """清空知识库"""
    try:
        rag = get_rag_service()
        rag.clear_collection()
        return {"message": "知识库已清空"}
    except Exception as e:
        logger.error(f"清空知识库失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/video/{bvid}")
async def delete_video_from_knowledge(bvid: str):
    """从知识库中删除指定视频"""
    try:
        rag = get_rag_service()
        rag.delete_video(bvid)
        return {"message": f"已删除视频 {bvid}"}
    except Exception as e:
        logger.error(f"删除视频失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== UP主创作者管理 ====================

@router.post("/creators", response_model=CreatorResponse)
async def add_creator(
    request: CreatorRequest,
    session_id: str = Query(..., description="会话ID"),
    db: AsyncSession = Depends(get_db),
):
    """添加 UP主（创作者）配置"""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")

    uid = request.uid.strip()
    if not uid:
        raise HTTPException(status_code=400, detail="uid 不能为空")

    # 若未提供昵称，尝试从 B站 API 获取
    nickname = request.nickname
    if not nickname:
        cookies = session.get("cookies", {})
        bili = BilibiliService(
            sessdata=cookies.get("SESSDATA"),
            bili_jct=cookies.get("bili_jct"),
            dedeuserid=cookies.get("DedeUserID"),
        )
        try:
            info = await bili.get_creator_info(uid)
            nickname = info.get("name") or uid
        except Exception as e:
            logger.warning(f"[Bilibili] 获取 UP主信息失败: {e}")
            nickname = uid
        finally:
            await bili.close()

    # 已存在则更新，否则新建
    result = await db.execute(
        select(BiliCreator).where(
            BiliCreator.session_id == session_id,
            BiliCreator.uid == uid,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.nickname = nickname
        existing.after_date = request.after_date
        await db.commit()
        await db.refresh(existing)
        return CreatorResponse(id=existing.id, uid=existing.uid, nickname=existing.nickname, after_date=existing.after_date)

    creator = BiliCreator(
        session_id=session_id,
        uid=uid,
        nickname=nickname,
        after_date=request.after_date,
    )
    db.add(creator)
    await db.commit()
    await db.refresh(creator)
    return CreatorResponse(id=creator.id, uid=creator.uid, nickname=creator.nickname, after_date=creator.after_date)


@router.get("/creators", response_model=List[CreatorResponse])
async def list_creators(
    session_id: str = Query(..., description="会话ID"),
    db: AsyncSession = Depends(get_db),
):
    """获取已配置的 UP主列表"""
    result = await db.execute(
        select(BiliCreator).where(BiliCreator.session_id == session_id)
    )
    creators = result.scalars().all()
    return [CreatorResponse(id=c.id, uid=c.uid, nickname=c.nickname, after_date=c.after_date) for c in creators]


@router.delete("/creators/{creator_id}")
async def delete_creator(
    creator_id: int,
    session_id: str = Query(..., description="会话ID"),
    db: AsyncSession = Depends(get_db),
):
    """删除 UP主配置"""
    result = await db.execute(
        select(BiliCreator).where(
            BiliCreator.id == creator_id,
            BiliCreator.session_id == session_id,
        )
    )
    creator = result.scalar_one_or_none()
    if not creator:
        raise HTTPException(status_code=404, detail="未找到该 UP主配置")
    await db.delete(creator)
    await db.commit()
    return {"message": "已删除"}


@router.post("/creators/sync")
async def sync_creator_videos(
    background_tasks: BackgroundTasks,
    session_id: str = Query(..., description="会话ID"),
    db: AsyncSession = Depends(get_db),
):
    """同步所有已配置 UP主的作品到知识库（后台任务）"""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")

    result = await db.execute(
        select(BiliCreator).where(BiliCreator.session_id == session_id)
    )
    creators = result.scalars().all()
    if not creators:
        raise HTTPException(status_code=400, detail="尚未配置任何 UP主")

    creator_list = [
        {"id": c.id, "uid": c.uid, "nickname": c.nickname, "after_date": c.after_date}
        for c in creators
    ]

    import uuid
    task_id = str(uuid.uuid4())
    build_tasks[task_id] = {
        "status": "pending",
        "progress": 0,
        "current_step": "初始化中...",
        "total_videos": 0,
        "processed_videos": 0,
        "message": "",
    }

    background_tasks.add_task(
        _sync_creators_task,
        task_id,
        session_id,
        session,
        creator_list,
    )

    return {"task_id": task_id, "message": "UP主作品同步任务已启动"}


async def _sync_creators_task(
    task_id: str,
    session_id: str,
    session: dict,
    creator_list: list,
):
    """后台任务：同步多个 UP主的全部作品到知识库"""
    cookies = session.get("cookies", {})
    bili = BilibiliService(
        sessdata=cookies.get("SESSDATA"),
        bili_jct=cookies.get("bili_jct"),
        dedeuserid=cookies.get("DedeUserID"),
    )
    asr_service = create_asr_service()
    content_fetcher = ContentFetcher(bili, asr_service)
    rag = get_rag_service()

    try:
        build_tasks[task_id]["status"] = "running"
        total_indexed = 0

        for ci, creator in enumerate(creator_list, start=1):
            uid = creator["uid"]
            nickname = creator.get("nickname") or uid
            after_date = creator.get("after_date")

            build_tasks[task_id]["current_step"] = f"获取 UP主 {nickname} 的作品列表..."
            logger.info(f"[Creator Sync] 开始同步 UP主 {nickname} (uid={uid}), after_date={after_date}")

            try:
                videos = await bili.get_all_creator_videos(uid, after_date=after_date)
            except Exception as e:
                logger.error(f"[Creator Sync] 获取 UP主 {uid} 视频列表失败: {e}")
                continue

            logger.info(f"[Creator Sync] UP主 {nickname} 共 {len(videos)} 个视频")
            build_tasks[task_id]["total_videos"] = build_tasks[task_id].get("total_videos", 0) + len(videos)

            async with get_db_context() as db:
                for vi, video in enumerate(videos, start=1):
                    bvid = video.get("bvid")
                    title = video.get("title") or bvid
                    if not bvid:
                        continue

                    build_tasks[task_id]["current_step"] = f"处理: {title[:30]}"

                    try:
                        # 检查是否已处理过
                        res = await db.execute(select(VideoCache).where(VideoCache.bvid == bvid))
                        cache = res.scalar_one_or_none()
                        if cache and cache.is_processed:
                            build_tasks[task_id]["processed_videos"] = build_tasks[task_id].get("processed_videos", 0) + 1
                            continue

                        # 获取内容并入库
                        video_info = {
                            "bvid": bvid,
                            "title": title,
                            "intro": video.get("description") or "",
                            "owner_name": video.get("author") or nickname,
                            "owner_mid": uid,
                            "duration": video.get("length"),
                            "cover": video.get("pic"),
                        }
                        await _upsert_video_cache(db, bvid, video_info)
                        await db.commit()

                        content_result = await content_fetcher.fetch_content(bvid)
                        if content_result:
                            await rag.add_video(bvid, title, content_result)
                            res2 = await db.execute(select(VideoCache).where(VideoCache.bvid == bvid))
                            cache2 = res2.scalar_one_or_none()
                            if cache2:
                                cache2.is_processed = True
                                cache2.content = content_result[:5000]
                                await db.commit()
                            total_indexed += 1

                    except Exception as e:
                        logger.error(f"[Creator Sync] 处理视频 {bvid} 失败: {e}")

                    build_tasks[task_id]["processed_videos"] = build_tasks[task_id].get("processed_videos", 0) + 1
                    total = build_tasks[task_id].get("total_videos", 1)
                    proc = build_tasks[task_id].get("processed_videos", 0)
                    build_tasks[task_id]["progress"] = min(99, int(proc / max(total, 1) * 100))

        build_tasks[task_id]["status"] = "completed"
        build_tasks[task_id]["progress"] = 100
        build_tasks[task_id]["current_step"] = "完成"
        build_tasks[task_id]["message"] = f"UP主作品同步完成，入库 {total_indexed} 个视频"
        logger.info(f"[Creator Sync] 完成，共入库 {total_indexed} 个视频")

    except Exception as e:
        logger.error(f"[Creator Sync] 任务失败: {e}")
        build_tasks[task_id]["status"] = "failed"
        build_tasks[task_id]["message"] = str(e)
    finally:
        await bili.close()

