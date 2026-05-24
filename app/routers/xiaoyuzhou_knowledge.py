"""
小宇宙播客知识库路由

提供小宇宙登录、订阅管理和知识库构建端点。
"""
import asyncio
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, get_db_context
from app.models import (
    PlatformContentCache,
    PodcastSubscription,
    XiaoyuzhouSession,
    VideoContent,
    ContentSource,
)
from app.services.xiaoyuzhou import XiaoyuzhouService
from app.services.xiaoyuzhou_fetcher import XiaoyuzhouContentFetcher
from app.services.asr_factory import create_asr_service
from app.services.processing_status import ProcessingStatusService
from app.routers.knowledge import get_rag_service

router = APIRouter(prefix="/xiaoyuzhou", tags=["小宇宙播客"])

# 后台任务状态（内存存储，重启后清空）
_build_tasks: dict[str, dict] = {}
_proc_svc = ProcessingStatusService()

# ==================== 请求 / 响应模型 ====================


class SendSmsRequest(BaseModel):
    session_id: str
    phone: str


class LoginRequest(BaseModel):
    session_id: str
    phone: str
    code: str


class LoginResponse(BaseModel):
    uid: Optional[str] = None
    nickname: Optional[str] = None
    message: str


class SessionStatus(BaseModel):
    logged_in: bool
    phone: Optional[str] = None
    nickname: Optional[str] = None
    uid: Optional[str] = None


class AddSubscriptionRequest(BaseModel):
    """手动添加播客订阅（RSS URL）"""
    session_id: str
    rss_url: str


class AddSubscriptionResponse(BaseModel):
    subscription_id: int
    podcast_id: str
    title: str
    message: str


class SubscriptionInfo(BaseModel):
    id: int
    podcast_id: str
    title: str
    author: Optional[str]
    rss_url: Optional[str]
    cover_url: Optional[str]
    is_selected: bool
    last_sync_at: Optional[datetime]
    created_at: datetime


class EpisodeInfo(BaseModel):
    episode_id: str
    title: str
    description: Optional[str]
    duration: Optional[int]
    audio_url: Optional[str]
    pub_date: Optional[str]


class BuildRequest(BaseModel):
    session_id: str
    subscription_ids: list[int]
    asr_backend: str = "auto"
    episode_limit: int = 10     # 每个播客最多处理集数，0 = 不限制


class BuildStatus(BaseModel):
    task_id: str
    status: str
    progress: int
    current_step: str
    total_episodes: int
    processed_episodes: int
    message: str
    logs: list[str] = []

    model_config = {"extra": "ignore"}


class SyncSubscriptionsResponse(BaseModel):
    added: int
    total: int
    message: str


# ==================== 认证 ====================


@router.post("/auth/send-sms")
async def send_sms(req: SendSmsRequest):
    """发送短信验证码"""
    xyz = XiaoyuzhouService()
    ok = await xyz.send_sms_code(req.phone)
    if not ok:
        raise HTTPException(status_code=502, detail="发送验证码失败，请稍后重试")
    return {"message": f"验证码已发送至 {req.phone}"}


@router.post("/auth/login", response_model=LoginResponse)
async def login(
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """短信验证码登录小宇宙"""
    xyz = XiaoyuzhouService()
    result = await xyz.login_with_sms(req.phone, req.code)
    if not result:
        raise HTTPException(status_code=401, detail="登录失败，请检查验证码")

    # 保存 Token 到数据库
    db_result = await db.execute(
        select(XiaoyuzhouSession).where(XiaoyuzhouSession.session_id == req.session_id)
    )
    xsess = db_result.scalar_one_or_none()
    if xsess is None:
        xsess = XiaoyuzhouSession(
            session_id=req.session_id,
            phone=req.phone,
            access_token=result["access_token"],
            refresh_token=result["refresh_token"],
            uid=result.get("uid"),
            nickname=result.get("nickname"),
        )
        db.add(xsess)
    else:
        xsess.phone = req.phone
        xsess.access_token = result["access_token"]
        xsess.refresh_token = result["refresh_token"]
        xsess.uid = result.get("uid")
        xsess.nickname = result.get("nickname")
        xsess.updated_at = datetime.utcnow()
    await db.commit()

    return LoginResponse(
        uid=result.get("uid"),
        nickname=result.get("nickname"),
        message="登录成功",
    )


@router.get("/auth/status", response_model=SessionStatus)
async def get_session_status(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """查询小宇宙登录状态"""
    result = await db.execute(
        select(XiaoyuzhouSession).where(XiaoyuzhouSession.session_id == session_id)
    )
    xsess = result.scalar_one_or_none()
    if not xsess or not xsess.access_token:
        return SessionStatus(logged_in=False)
    return SessionStatus(
        logged_in=True,
        phone=xsess.phone,
        nickname=xsess.nickname,
        uid=xsess.uid,
    )


@router.delete("/auth/logout")
async def logout(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """退出小宇宙登录"""
    result = await db.execute(
        select(XiaoyuzhouSession).where(XiaoyuzhouSession.session_id == session_id)
    )
    xsess = result.scalar_one_or_none()
    if xsess:
        xsess.access_token = None
        xsess.refresh_token = None
        await db.commit()
    return {"message": "已退出登录"}


# ==================== 订阅管理 ====================


@router.post("/subscriptions/sync", response_model=SyncSubscriptionsResponse)
async def sync_subscriptions(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """从小宇宙账号同步订阅列表（需要已登录）"""
    xyz = await _build_xyz_service(session_id, db)
    subscriptions = await xyz.get_subscriptions(limit=200)

    added = 0
    for sub in subscriptions:
        pid = sub["podcast_id"]
        db_result = await db.execute(
            select(PodcastSubscription).where(
                PodcastSubscription.session_id == session_id,
                PodcastSubscription.podcast_id == pid,
            )
        )
        existing = db_result.scalar_one_or_none()
        if existing is None:
            ps = PodcastSubscription(
                session_id=session_id,
                podcast_id=pid,
                title=sub["title"],
                author=sub.get("author"),
                description=sub.get("description"),
                rss_url=sub.get("rss_url"),
                cover_url=sub.get("cover_url"),
                is_selected=True,
            )
            db.add(ps)
            added += 1
    await db.commit()

    return SyncSubscriptionsResponse(
        added=added,
        total=len(subscriptions),
        message=f"同步完成，新增 {added} 个播客",
    )


@router.post("/subscriptions", response_model=AddSubscriptionResponse)
async def add_subscription(
    req: AddSubscriptionRequest,
    db: AsyncSession = Depends(get_db),
):
    """手动添加播客订阅（通过 RSS URL，无需登录）"""
    xyz = XiaoyuzhouService()
    info = await xyz.get_podcast_info_from_rss(req.rss_url)
    if not info:
        raise HTTPException(status_code=400, detail="无法解析 RSS，请检查地址是否正确")

    pid = info["podcast_id"]
    db_result = await db.execute(
        select(PodcastSubscription).where(
            PodcastSubscription.session_id == req.session_id,
            PodcastSubscription.podcast_id == pid,
        )
    )
    existing = db_result.scalar_one_or_none()
    if existing:
        return AddSubscriptionResponse(
            subscription_id=existing.id,
            podcast_id=pid,
            title=existing.title,
            message="播客已存在，无需重复添加",
        )

    ps = PodcastSubscription(
        session_id=req.session_id,
        podcast_id=pid,
        title=info["title"],
        author=info.get("author"),
        description=info.get("description"),
        rss_url=req.rss_url,
        cover_url=info.get("cover_url"),
        is_selected=True,
    )
    db.add(ps)
    await db.commit()
    await db.refresh(ps)

    return AddSubscriptionResponse(
        subscription_id=ps.id,
        podcast_id=pid,
        title=info["title"],
        message="播客订阅添加成功",
    )


@router.get("/subscriptions", response_model=list[SubscriptionInfo])
async def list_subscriptions(
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """列出所有播客订阅"""
    result = await db.execute(
        select(PodcastSubscription)
        .where(PodcastSubscription.session_id == session_id)
        .order_by(PodcastSubscription.created_at.desc())
    )
    subs = result.scalars().all()
    return [_sub_to_info(s) for s in subs]


@router.delete("/subscriptions/{sub_id}")
async def delete_subscription(
    sub_id: int,
    session_id: str,
    db: AsyncSession = Depends(get_db),
):
    """删除播客订阅"""
    result = await db.execute(
        select(PodcastSubscription).where(
            PodcastSubscription.id == sub_id,
            PodcastSubscription.session_id == session_id,
        )
    )
    ps = result.scalar_one_or_none()
    if not ps:
        raise HTTPException(status_code=404, detail="订阅不存在")
    await db.delete(ps)
    await db.commit()
    return {"message": "订阅已删除"}


@router.get("/subscriptions/{sub_id}/episodes", response_model=list[EpisodeInfo])
async def get_episodes(
    sub_id: int,
    session_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """获取播客单集列表（从 RSS 解析）"""
    result = await db.execute(
        select(PodcastSubscription).where(
            PodcastSubscription.id == sub_id,
            PodcastSubscription.session_id == session_id,
        )
    )
    ps = result.scalar_one_or_none()
    if not ps:
        raise HTTPException(status_code=404, detail="订阅不存在")

    rss_url = ps.rss_url or XiaoyuzhouService().build_rss_url(ps.podcast_id)
    xyz = XiaoyuzhouService()
    episodes = await xyz.get_episodes_from_rss(rss_url, limit=limit)
    return [EpisodeInfo(**e) for e in episodes]


# ==================== 知识库构建 ====================


@router.post("/build", response_model=BuildStatus)
async def build_knowledge(
    req: BuildRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """启动小宇宙知识库构建后台任务"""
    result = await db.execute(
        select(PodcastSubscription).where(
            PodcastSubscription.session_id == req.session_id,
            PodcastSubscription.id.in_(req.subscription_ids),
        )
    )
    subs = result.scalars().all()
    if not subs:
        raise HTTPException(status_code=400, detail="未找到指定订阅")

    task_id = str(uuid.uuid4())
    _build_tasks[task_id] = {
        "status": "pending",
        "progress": 0,
        "current_step": "准备中",
        "total_episodes": 0,
        "processed_episodes": 0,
        "message": "",
        "logs": [],
    }

    background_tasks.add_task(
        _run_build,
        task_id=task_id,
        subs=[_sub_to_dict(s) for s in subs],
        asr_backend=req.asr_backend,
        episode_limit=req.episode_limit,
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
    subs: list[dict],
    asr_backend: str,
    episode_limit: int,
):
    task = _build_tasks[task_id]
    task["status"] = "running"

    def _log(msg: str) -> None:
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        logger.info(f"[Xiaoyuzhou] {msg}")
        task["logs"].append(entry)
        if len(task["logs"]) > 200:
            task["logs"] = task["logs"][-200:]

    try:
        _log(f"开始构建小宇宙知识库，ASR 后端: {asr_backend}")
        asr = create_asr_service(asr_backend)
        fetcher = XiaoyuzhouContentFetcher(asr_service=asr)
        rag = get_rag_service()
        xyz = XiaoyuzhouService()

        # 收集所有集数
        _log("收集播客集数列表...")
        all_episodes: list[tuple[dict, str]] = []  # (episode_info, podcast_title)
        for sub in subs:
            rss_url = sub.get("rss_url") or xyz.build_rss_url(sub["podcast_id"])
            try:
                episodes = await xyz.get_episodes_from_rss(rss_url, limit=episode_limit)
                for ep in episodes:
                    all_episodes.append((ep, sub["title"]))
                _log(f"「{sub['title']}」: 获取到 {len(episodes)} 集")
            except Exception as e:
                logger.warning(f"[Xiaoyuzhou] 获取集数失败 [{sub['podcast_id']}]: {e}")
                _log(f"⚠️ 获取「{sub['title']}」集数失败: {str(e)[:60]}")

        total = len(all_episodes)
        task["total_episodes"] = total
        task["current_step"] = f"共 {total} 集，开始处理"
        _log(f"共 {total} 集，开始处理...")

        done_count = 0
        concurrency = getattr(settings, "asr_concurrency", 2)
        sem = asyncio.Semaphore(concurrency)

        async def _process_episode(i: int, ep: dict, podcast_title: str) -> None:
            nonlocal done_count
            episode_id = ep.get("episode_id", "")
            ep_title = ep.get("title", episode_id)
            task["current_step"] = f"处理: {ep_title[:40]}"

            try:
                # Skip if already fully indexed
                async with get_db_context() as db:
                    full_title_check = f"[{podcast_title}] {ep_title}" if podcast_title else ep_title
                    proc_rec = await _proc_svc.get_or_create(db, "xiaoyuzhou", episode_id, full_title_check)
                    await db.commit()
                if _proc_svc.is_completed(proc_rec):
                    done_count += 1
                    task["processed_episodes"] = done_count
                    task["progress"] = int((done_count / max(total, 1)) * 90)
                    _log(f"⏭ 已完成，跳过：{ep_title[:40]}")
                    return

                _log(f"🔊 ASR 转写中：{ep_title[:50]}")
                task["message"] = f"转写: {ep_title[:30]}..."
                async with sem:
                    content = await fetcher.fetch_content(ep, podcast_title=podcast_title)
                source_label = "ASR ✅" if content.content_source == "asr" else "基本信息 ⚠️"
                _log(f"✅ 完成（{source_label}）：{ep_title[:40]}")

                async with get_db_context() as db:
                    result = await db.execute(
                        select(PlatformContentCache).where(
                            PlatformContentCache.platform == "xiaoyuzhou",
                            PlatformContentCache.content_id == episode_id,
                        )
                    )
                    cache = result.scalar_one_or_none()
                    full_title = f"[{podcast_title}] {ep_title}" if podcast_title else ep_title
                    if cache is None:
                        cache = PlatformContentCache(
                            platform="xiaoyuzhou",
                            content_id=episode_id,
                            url=ep.get("audio_url", ""),
                            title=full_title,
                            author=podcast_title,
                            description=ep.get("description", "")[:500],
                            duration=ep.get("duration"),
                            cover_url=ep.get("cover_url", ""),
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
                    bvid=f"xyz_{episode_id[:50]}",
                    title=full_title,
                    content=content.content or "",
                    source=ContentSource.ASR if content.content_source == "asr" else ContentSource.BASIC_INFO,
                    summary_block=content.summary_block,
                )
                rag.add_video_content(video_content)

                # Track processing stages
                try:
                    async with get_db_context() as db:
                        r = await _proc_svc.get_or_create(db, "xiaoyuzhou", episode_id, full_title)
                        if content.asr_raw_text:
                            await _proc_svc.mark_asr_done(db, r, content.asr_raw_text)
                            await _proc_svc.mark_correction_done(db, r, content.content or "")
                        if content.summary_block:
                            await _proc_svc.mark_summary_done(db, r, content.summary_block)
                        await _proc_svc.mark_completed(db, r)
                        await db.commit()
                except Exception as _e:
                    logger.warning(f"[Xiaoyuzhou] 处理状态记录失败（非关键）[{episode_id}]: {_e}")

            except Exception as e:
                _log(f"❌ 失败：{ep_title[:40]} — {str(e)[:80]}")
                logger.error(f"[Xiaoyuzhou] 处理集数失败 [{episode_id}]: {e}")
                try:
                    async with get_db_context() as db_err:
                        r_err = await _proc_svc.get_or_create(db_err, "xiaoyuzhou", episode_id)
                        await _proc_svc.mark_failed(db_err, r_err, "index", str(e))
                        await db_err.commit()
                except Exception:
                    pass

            done_count += 1
            task["processed_episodes"] = done_count
            task["progress"] = int((done_count / max(total, 1)) * 90)

        await asyncio.gather(*[_process_episode(i, ep, pt) for i, (ep, pt) in enumerate(all_episodes)])

        task["status"] = "completed"
        task["progress"] = 100
        task["current_step"] = "完成"
        task["message"] = f"成功处理 {task['processed_episodes']} 集"
        _log(f"🎉 任务完成，共处理 {task['processed_episodes']} 集")

    except Exception as e:
        logger.error(f"[Xiaoyuzhou] 构建任务失败 [{task_id}]: {e}")
        _log(f"❌ 任务失败: {str(e)[:100]}")
        task["status"] = "failed"
        task["message"] = str(e)
        task["current_step"] = "失败"


# ==================== 工具函数 ====================


async def _build_xyz_service(session_id: str, db: AsyncSession) -> XiaoyuzhouService:
    result = await db.execute(
        select(XiaoyuzhouSession).where(XiaoyuzhouSession.session_id == session_id)
    )
    xsess = result.scalar_one_or_none()
    if not xsess or not xsess.access_token:
        raise HTTPException(status_code=401, detail="未登录小宇宙，请先登录")
    return XiaoyuzhouService(
        access_token=xsess.access_token,
        refresh_token=xsess.refresh_token,
    )


def _sub_to_dict(s: PodcastSubscription) -> dict:
    return {
        "id": s.id,
        "podcast_id": s.podcast_id,
        "title": s.title,
        "author": s.author,
        "rss_url": s.rss_url,
        "cover_url": s.cover_url,
    }


def _sub_to_info(s: PodcastSubscription) -> SubscriptionInfo:
    return SubscriptionInfo(
        id=s.id,
        podcast_id=s.podcast_id,
        title=s.title,
        author=s.author,
        rss_url=s.rss_url,
        cover_url=s.cover_url,
        is_selected=s.is_selected,
        last_sync_at=s.last_sync_at,
        created_at=s.created_at,
    )
