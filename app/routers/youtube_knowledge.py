"""
YouTube 知识库路由

提供 YouTube 视频/播放列表的来源管理和知识库构建端点。
"""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, get_db_context
from app.models import (
    PlatformContentCache,
    YoutubeSource,
    YoutubeSession,
    VideoContent,
    ContentSource,
)
from app.services.youtube import YouTubeService
from app.services.youtube_fetcher import YouTubeContentFetcher
from app.services.asr_factory import create_asr_service
from app.services.processing_status import ProcessingStatusService
from app.routers.knowledge import get_rag_service

router = APIRouter(prefix="/youtube", tags=["YouTube"])

# 后台任务状态（内存存储，重启后清空）
_build_tasks: dict[str, dict] = {}
_proc_svc = ProcessingStatusService()

# ==================== 请求 / 响应模型 ====================


class AddSourceRequest(BaseModel):
    """添加 YouTube 来源"""
    session_id: str
    source_url: str
    source_type: str = "auto"   # auto | video | playlist | channel | liked | watch_later
    after_date: Optional[str] = None   # YYYY-MM-DD，仅获取该日期之后的视频


class AddSourceResponse(BaseModel):
    source_id: int
    source_type: str
    title: Optional[str] = None
    message: str


class YouTubeSourceInfo(BaseModel):
    id: int
    session_id: str
    source_type: str
    source_url: str
    title: Optional[str]
    after_date: Optional[str] = None
    is_selected: bool
    last_sync_at: Optional[datetime]
    created_at: datetime


class BuildRequest(BaseModel):
    session_id: str
    source_ids: list[int]
    asr_backend: str = "auto"
    limit_per_source: int = 0   # 0 = 不限制


class BuildStatus(BaseModel):
    task_id: str
    status: str                 # pending | running | completed | failed
    progress: int               # 0-100
    current_step: str
    total_videos: int
    processed_videos: int
    message: str


class UploadCookieRequest(BaseModel):
    session_id: str
    cookie_content: str     # Netscape 格式 Cookie 文本


# ==================== Cookie 管理 ====================


@router.post("/cookie")
async def upload_cookie(
    req: UploadCookieRequest,
    db: AsyncSession = Depends(get_db),
):
    """保存 Netscape 格式 YouTube Cookie（粘贴文本）"""
    result = await db.execute(
        select(YoutubeSession).where(YoutubeSession.session_id == req.session_id)
    )
    ys = result.scalar_one_or_none()
    if ys is None:
        ys = YoutubeSession(session_id=req.session_id, cookie_content=req.cookie_content)
        db.add(ys)
    else:
        ys.cookie_content = req.cookie_content
        ys.updated_at = datetime.utcnow()
    await db.commit()
    return {"message": "Cookie 保存成功"}


@router.get("/cookie/status")
async def get_cookie_status(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """查询 Cookie 是否已配置"""
    result = await db.execute(
        select(YoutubeSession).where(YoutubeSession.session_id == session_id)
    )
    ys = result.scalar_one_or_none()
    has_cookie = bool(ys and ys.cookie_content)
    return {"has_cookie": has_cookie}


@router.delete("/cookie")
async def delete_cookie(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """删除已保存的 Cookie"""
    result = await db.execute(
        select(YoutubeSession).where(YoutubeSession.session_id == session_id)
    )
    ys = result.scalar_one_or_none()
    if ys:
        ys.cookie_content = None
        ys.cookie_file_path = None
        await db.commit()
    return {"message": "Cookie 已删除"}


# ==================== 来源管理 ====================


@router.post("/sources", response_model=AddSourceResponse)
async def add_source(
    req: AddSourceRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    添加 YouTube 来源（单视频 / 播放列表 / 频道 / 点赞列表 / 稍后观看）

    source_url 示例：
    - https://www.youtube.com/watch?v=xxx
    - https://www.youtube.com/playlist?list=xxx
    - https://www.youtube.com/@channel/videos
    - liked  (需 Cookie)
    - watch_later (需 Cookie)
    """
    source_type = _infer_source_type(req.source_url) if req.source_type == "auto" else req.source_type
    actual_url = req.source_url

    # 私人列表使用虚拟 URL
    if source_type in ("liked", "watch_later"):
        virtual = YouTubeService.PRIVATE_URLS.get(source_type)
        if virtual:
            actual_url = virtual

    # 尝试获取标题
    title = None
    try:
        yt = await _build_yt_service(req.session_id, db)
        if source_type == "video":
            info = await yt.extract_video_info(actual_url)
            title = info.get("title") if info else None
        elif source_type in ("playlist", "channel"):
            info = await _get_playlist_title(yt, actual_url)
            title = info
        elif source_type == "liked":
            title = "YouTube 点赞视频"
        elif source_type == "watch_later":
            title = "YouTube 稍后观看"
    except Exception as e:
        logger.debug(f"[YouTube] 获取标题失败（非关键）: {e}")

    source = YoutubeSource(
        session_id=req.session_id,
        source_type=source_type,
        source_url=actual_url,
        title=title,
        after_date=req.after_date,
        is_selected=True,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    return AddSourceResponse(
        source_id=source.id,
        source_type=source_type,
        title=title,
        message="来源添加成功",
    )


@router.get("/sources", response_model=list[YouTubeSourceInfo])
async def list_sources(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """列出已添加的 YouTube 来源"""
    result = await db.execute(
        select(YoutubeSource)
        .where(YoutubeSource.session_id == session_id)
        .order_by(YoutubeSource.created_at.desc())
    )
    sources = result.scalars().all()
    return [YouTubeSourceInfo(**_source_to_dict(s)) for s in sources]


@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: int,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """删除 YouTube 来源"""
    result = await db.execute(
        select(YoutubeSource).where(
            YoutubeSource.id == source_id,
            YoutubeSource.session_id == session_id,
        )
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="来源不存在")
    await db.delete(source)
    await db.commit()
    return {"message": "来源已删除"}


@router.get("/sources/{source_id}/videos")
async def preview_source_videos(
    source_id: int,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """预览来源中的视频列表（不下载）"""
    result = await db.execute(
        select(YoutubeSource).where(
            YoutubeSource.id == source_id,
            YoutubeSource.session_id == session_id,
        )
    )
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(status_code=404, detail="来源不存在")

    yt = await _build_yt_service(session_id, db)
    if source.source_type == "video":
        info = await yt.extract_video_info(source.source_url)
        videos = [info] if info else []
    else:
        videos = await yt.extract_playlist_videos(source.source_url)

    return {"total": len(videos), "videos": videos[:50]}  # 最多预览 50 条


# ==================== 知识库构建 ====================


@router.post("/build", response_model=BuildStatus)
async def build_knowledge(
    req: BuildRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """启动 YouTube 知识库构建后台任务"""
    # 验证来源归属
    result = await db.execute(
        select(YoutubeSource).where(
            YoutubeSource.session_id == req.session_id,
            YoutubeSource.id.in_(req.source_ids),
        )
    )
    sources = result.scalars().all()
    if not sources:
        raise HTTPException(status_code=400, detail="未找到指定来源")

    task_id = str(uuid.uuid4())
    _build_tasks[task_id] = {
        "status": "pending",
        "progress": 0,
        "current_step": "准备中",
        "total_videos": 0,
        "processed_videos": 0,
        "message": "",
    }

    # 保存 Cookie 到临时文件供 yt-dlp 使用
    cookie_file = await _save_cookie_file(req.session_id, db)

    background_tasks.add_task(
        _run_build,
        task_id=task_id,
        sources=[_source_to_dict(s) for s in sources],
        asr_backend=req.asr_backend,
        cookie_file=cookie_file,
        limit_per_source=req.limit_per_source,
    )

    return BuildStatus(task_id=task_id, **_build_tasks[task_id])


@router.get("/build/{task_id}", response_model=BuildStatus)
async def get_build_status(task_id: str):
    """查询构建任务状态"""
    task = _build_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return BuildStatus(task_id=task_id, **task)


# ==================== 后台任务 ====================


async def _run_build(
    task_id: str,
    sources: list[dict],
    asr_backend: str,
    cookie_file: Optional[str],
    limit_per_source: int,
):
    """后台知识库构建任务"""
    task = _build_tasks[task_id]
    task["status"] = "running"

    try:
        yt_service = YouTubeService(cookie_file=cookie_file)
        asr = create_asr_service(asr_backend)
        fetcher = YouTubeContentFetcher(asr_service=asr, youtube_service=yt_service)
        rag = get_rag_service()

        # 收集所有视频
        all_videos: list[dict] = []
        for source in sources:
            source_type = source["source_type"]
            source_url = source["source_url"]
            try:
                if source_type == "video":
                    info = await yt_service.extract_video_info(source_url)
                    if info:
                        all_videos.append(info)
                else:
                    after_date = source.get("after_date")
                    videos = await yt_service.extract_playlist_videos(source_url, after_date=after_date)
                    if limit_per_source > 0:
                        videos = videos[:limit_per_source]
                    all_videos.extend(videos)
            except Exception as e:
                logger.warning(f"[YouTube] 来源 {source_url} 提取失败: {e}")

        total = len(all_videos)
        task["total_videos"] = total
        task["current_step"] = f"共 {total} 个视频，开始处理"

        for i, video_info in enumerate(all_videos):
            video_id = video_info.get("video_id", "")
            title = video_info.get("title", video_id)
            task["current_step"] = f"处理: {title[:40]}"
            task["progress"] = int((i / max(total, 1)) * 90)

            try:
                # Skip if already fully indexed
                async with get_db_context() as db:
                    proc_rec = await _proc_svc.get_or_create(db, "youtube", video_id, title)
                    await db.commit()
                if _proc_svc.is_completed(proc_rec):
                    task["processed_videos"] += 1
                    continue

                # 获取内容（ASR）
                content = await fetcher.fetch_content(video_info)

                async with get_db_context() as db:
                    result = await db.execute(
                        select(PlatformContentCache).where(
                            PlatformContentCache.platform == "youtube",
                            PlatformContentCache.content_id == video_id,
                        )
                    )
                    cache = result.scalar_one_or_none()
                    if cache is None:
                        cache = PlatformContentCache(
                            platform="youtube",
                            content_id=video_id,
                            url=content.url,
                            title=content.title,
                            author=content.channel,
                            description=content.description[:500] if content.description else "",
                            duration=content.duration,
                            cover_url=content.cover_url,
                            content=content.content,
                            content_source=content.content_source,
                            is_processed=True,
                        )
                        db.add(cache)
                    else:
                        cache.content = content.content
                        cache.content_source = content.content_source
                        cache.is_processed = True
                    await db.commit()

                # 加入向量库
                video_content = VideoContent(
                    bvid=f"yt_{video_id}",
                    title=content.title,
                    content=content.content or "",
                    source=ContentSource.ASR if content.content_source == "asr" else ContentSource.BASIC_INFO,
                    summary_block=content.summary_block,
                )
                rag.add_video_content(video_content)

                # Track processing stages
                try:
                    async with get_db_context() as db:
                        r = await _proc_svc.get_or_create(db, "youtube", video_id, content.title)
                        if content.asr_raw_text:
                            await _proc_svc.mark_asr_done(db, r, content.asr_raw_text)
                            await _proc_svc.mark_correction_done(db, r, content.content or "")
                        if content.summary_block:
                            await _proc_svc.mark_summary_done(db, r, content.summary_block)
                        await _proc_svc.mark_completed(db, r)
                        await db.commit()
                except Exception as _e:
                    logger.warning(f"[YouTube] 处理状态记录失败（非关键）[{video_id}]: {_e}")

            except Exception as e:
                logger.error(f"[YouTube] 处理视频失败 [{video_id}]: {e}")
                try:
                    async with get_db_context() as db_err:
                        r_err = await _proc_svc.get_or_create(db_err, "youtube", video_id)
                        await _proc_svc.mark_failed(db_err, r_err, "index", str(e))
                        await db_err.commit()
                except Exception:
                    pass

            task["processed_videos"] = i + 1

        task["status"] = "completed"
        task["progress"] = 100
        task["current_step"] = "完成"
        task["message"] = f"成功处理 {task['processed_videos']} 个视频"

    except Exception as e:
        logger.error(f"[YouTube] 构建任务失败 [{task_id}]: {e}")
        task["status"] = "failed"
        task["message"] = str(e)
        task["current_step"] = "失败"


# ==================== 工具函数 ====================


def _infer_source_type(url: str) -> str:
    if url in ("liked", "watch_later", "subscriptions"):
        return url
    if url.startswith(":yt"):
        return "liked"
    if "youtube.com/watch" in url or "youtu.be/" in url:
        return "video"
    if "youtube.com/playlist" in url:
        return "playlist"
    if "youtube.com/@" in url or "youtube.com/channel/" in url or "youtube.com/c/" in url:
        return "channel"
    if "youtube.com/shorts/" in url:
        return "video"
    return "video"


async def _get_playlist_title(yt: YouTubeService, url: str) -> Optional[str]:
    try:
        info = await yt._extract_playlist_sync.__func__(yt, url) if False else None
        # 简化：直接从 URL 提取频道名作为备用
        if "@" in url:
            return url.split("@")[1].split("/")[0]
    except Exception:
        pass
    return None


async def _build_yt_service(session_id: str, db: AsyncSession) -> YouTubeService:
    """构建 YouTubeService，尝试加载已保存的 Cookie"""
    cookie_file = await _save_cookie_file(session_id, db)
    return YouTubeService(cookie_file=cookie_file)


async def _save_cookie_file(session_id: str, db: AsyncSession) -> Optional[str]:
    """将 DB 中的 Cookie 内容写入临时文件，供 yt-dlp 使用"""
    import tempfile, os
    result = await db.execute(
        select(YoutubeSession).where(YoutubeSession.session_id == session_id)
    )
    ys = result.scalar_one_or_none()
    if not ys or not ys.cookie_content:
        return None
    cookie_dir = "data/youtube_cookies"
    os.makedirs(cookie_dir, exist_ok=True)
    cookie_path = f"{cookie_dir}/{session_id}.txt"
    with open(cookie_path, "w", encoding="utf-8") as f:
        f.write(ys.cookie_content)
    return cookie_path


def _source_to_dict(s: YoutubeSource) -> dict:
    return {
        "id": s.id,
        "session_id": s.session_id,
        "source_type": s.source_type,
        "source_url": s.source_url,
        "title": s.title,
        "after_date": s.after_date,
        "is_selected": s.is_selected,
        "last_sync_at": s.last_sync_at,
        "created_at": s.created_at,
    }
