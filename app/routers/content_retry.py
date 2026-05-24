"""
内容处理状态查询与重试路由

GET  /api/processing/list               - 列出处理记录（可按 platform/stage 过滤）
POST /api/processing/{platform}/{id}/retry - 从指定阶段重试
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from loguru import logger

from app.database import get_db, get_db_context
from app.models import ContentProcessingRecord, VideoContent, ContentSource
from app.services.processing_status import ProcessingStatusService
from app.services.asr_factory import create_asr_service
from app.services.text_postprocessor_factory import create_text_postprocessor
from app.services.content_summary import ContentSummaryService
from app.routers.knowledge import get_rag_service

router = APIRouter(prefix="/api/processing", tags=["processing"])
_proc_svc = ProcessingStatusService()

_PLATFORM_RAG_PREFIX = {
    "bilibili": "",
    "youtube": "yt_",
    "xiaoyuzhou": "xyz_",
}


# ==================== 请求/响应模型 ====================

class ContentProcessingRecordOut(BaseModel):
    platform: str
    content_id: str
    title: Optional[str]
    stage: str
    failed_stage: Optional[str]
    error_message: Optional[str]
    has_asr_raw: bool
    has_corrected: bool
    has_summary: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    @classmethod
    def from_orm(cls, rec: ContentProcessingRecord) -> "ContentProcessingRecordOut":
        return cls(
            platform=rec.platform,
            content_id=rec.content_id,
            title=rec.title,
            stage=rec.stage,
            failed_stage=rec.failed_stage,
            error_message=rec.error_message,
            has_asr_raw=bool(rec.asr_raw_text),
            has_corrected=bool(rec.corrected_text),
            has_summary=bool(rec.summary_block),
            created_at=rec.created_at.isoformat() if rec.created_at else None,
            updated_at=rec.updated_at.isoformat() if rec.updated_at else None,
        )


class ListResponse(BaseModel):
    records: list[ContentProcessingRecordOut]
    total: int


class RetryRequest(BaseModel):
    stage: str                     # asr | correction | summary | index
    asr_backend: Optional[str] = None


class RetryResponse(BaseModel):
    status: str
    message: str


# ==================== 路由 ====================

@router.get("/list", response_model=ListResponse)
async def list_processing_records(
    platform: Optional[str] = None,
    stage: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """列出处理记录（可按平台/阶段/标题过滤）"""
    records = await _proc_svc.list_records(db, platform=platform, stage=stage,
                                            title_search=q, limit=limit, offset=offset)
    return ListResponse(
        records=[ContentProcessingRecordOut.from_orm(r) for r in records],
        total=len(records),
    )


@router.get("/{platform}/{content_id}/content")
async def get_content(
    platform: str,
    content_id: str,
    db: AsyncSession = Depends(get_db),
):
    """获取处理记录的文本内容（用于预览 MD 文档）"""
    result = await db.execute(
        select(ContentProcessingRecord).where(
            ContentProcessingRecord.platform == platform,
            ContentProcessingRecord.content_id == content_id,
        )
    )
    rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="处理记录不存在")
    content = rec.corrected_text or rec.asr_raw_text or ""
    return {
        "platform": rec.platform,
        "content_id": rec.content_id,
        "title": rec.title or content_id,
        "stage": rec.stage,
        "content": content,
        "summary_block": rec.summary_block or "",
    }


@router.post("/{platform}/{content_id}/retry", response_model=RetryResponse)
async def retry_processing(
    platform: str,
    content_id: str,
    req: RetryRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """从指定阶段重试内容处理"""
    result = await db.execute(
        select(ContentProcessingRecord).where(
            ContentProcessingRecord.platform == platform,
            ContentProcessingRecord.content_id == content_id,
        )
    )
    rec = result.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="处理记录不存在")

    if req.stage == "correction":
        if not rec.asr_raw_text:
            raise HTTPException(status_code=400, detail="无可用的原始 ASR 文本，请先运行 stage=asr 重试")
        background_tasks.add_task(
            _retry_correction,
            platform=platform,
            content_id=content_id,
            title=rec.title or content_id,
            asr_raw_text=rec.asr_raw_text,
        )
        return RetryResponse(status="started", message="纠错重试任务已启动")

    elif req.stage == "summary":
        if not rec.corrected_text:
            raise HTTPException(status_code=400, detail="无可用的纠错文本，请先运行 stage=correction 重试")
        background_tasks.add_task(
            _retry_summary,
            platform=platform,
            content_id=content_id,
        )
        return RetryResponse(status="started", message="摘要重试任务已启动")

    elif req.stage == "index":
        if platform not in _PLATFORM_RAG_PREFIX:
            raise HTTPException(status_code=400, detail=f"平台 {platform} 不支持索引重试（仅支持 bilibili/youtube/xiaoyuzhou）")
        corrected = rec.corrected_text or rec.asr_raw_text
        if not corrected:
            raise HTTPException(status_code=400, detail="无可用的文本内容，请先运行 stage=correction 重试")
        background_tasks.add_task(
            _retry_index,
            platform=platform,
            content_id=content_id,
            title=rec.title or content_id,
            corrected_text=corrected,
            summary_block=rec.summary_block,
        )
        return RetryResponse(status="started", message="索引重建任务已启动")

    elif req.stage == "asr":
        background_tasks.add_task(
            _retry_asr,
            platform=platform,
            content_id=content_id,
            title=rec.title or content_id,
            asr_backend=req.asr_backend,
        )
        return RetryResponse(status="started", message="ASR 重试任务已启动（将重新下载音频并转写）")

    else:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的重试阶段: {req.stage}。支持: asr, correction, summary, index"
        )


# ==================== 后台任务 ====================

async def _retry_correction(
    platform: str,
    content_id: str,
    title: str,
    asr_raw_text: str,
):
    """后台任务：从 asr_raw_text 重新运行纠错 → 摘要 → 重建索引"""
    try:
        text_proc = create_text_postprocessor()
        corrected = await text_proc.postprocess(asr_raw_text, title=title)
        if not (corrected or "").strip():
            corrected = asr_raw_text

        summary_svc = ContentSummaryService()
        summary_block = await summary_svc.summarize(corrected)

        async with get_db_context() as db:
            r = await _proc_svc.get_or_create(db, platform, content_id, title)
            await _proc_svc.mark_correction_done(db, r, corrected)
            if summary_block:
                await _proc_svc.mark_summary_done(db, r, summary_block)
            await db.commit()

        if platform in _PLATFORM_RAG_PREFIX:
            await _do_index(platform, content_id, title, corrected, summary_block or None)

        logger.info(f"[Retry] 纠错重试完成: {platform}/{content_id}")

    except Exception as e:
        logger.error(f"[Retry] 纠错重试失败: {platform}/{content_id}: {e}")
        try:
            async with get_db_context() as db:
                r = await _proc_svc.get_or_create(db, platform, content_id)
                await _proc_svc.mark_failed(db, r, "correction", str(e))
                await db.commit()
        except Exception:
            pass


async def _retry_index(
    platform: str,
    content_id: str,
    title: str,
    corrected_text: str,
    summary_block: Optional[str],
):
    try:
        await _do_index(platform, content_id, title, corrected_text, summary_block)
        logger.info(f"[Retry] 索引重建完成: {platform}/{content_id}")
    except Exception as e:
        logger.error(f"[Retry] 索引重建失败: {platform}/{content_id}: {e}")
        try:
            async with get_db_context() as db:
                r = await _proc_svc.get_or_create(db, platform, content_id)
                await _proc_svc.mark_failed(db, r, "index", str(e))
                await db.commit()
        except Exception:
            pass


async def _retry_summary(platform: str, content_id: str):
    try:
        async with get_db_context() as db:
            result = await db.execute(
                select(ContentProcessingRecord).where(
                    ContentProcessingRecord.platform == platform,
                    ContentProcessingRecord.content_id == content_id,
                )
            )
            rec = result.scalar_one_or_none()
            if not rec or not rec.corrected_text:
                return

        summary_svc = ContentSummaryService()
        summary_block = await summary_svc.summarize(rec.corrected_text)

        async with get_db_context() as db:
            r = await _proc_svc.get_or_create(db, platform, content_id)
            await _proc_svc.mark_summary_done(db, r, summary_block)
            await db.commit()

        logger.info(f"[Retry] 摘要重试完成: {platform}/{content_id}")
    except Exception as e:
        logger.error(f"[Retry] 摘要重试失败: {platform}/{content_id}: {e}")


async def _retry_asr(
    platform: str,
    content_id: str,
    title: str,
    asr_backend: Optional[str],
):
    """
    后台任务：重新下载音频并从头运行 ASR → 纠错 → 摘要 → 索引。
    不缓存音视频文件 — 若工作目录文件已被清理，则重新下载。
    """
    try:
        # Reset stage to pending
        async with get_db_context() as db:
            r = await _proc_svc.get_or_create(db, platform, content_id, title)
            r.stage = "pending"
            r.asr_raw_text = None
            r.corrected_text = None
            r.summary_block = None
            r.failed_stage = None
            r.error_message = None
            await db.commit()

        asr = create_asr_service(asr_backend)
        text_proc = create_text_postprocessor()
        summary_svc = ContentSummaryService()

        if platform == "bilibili":
            from app.models import VideoCache
            from app.services.bilibili import BilibiliService
            from app.services.content_fetcher import ContentFetcher
            async with get_db_context() as db:
                result = await db.execute(
                    select(VideoCache).where(VideoCache.bvid == content_id)
                )
                cache = result.scalar_one_or_none()
            if not cache:
                raise ValueError(f"Bilibili 元数据不可用，请重新触发完整构建: {content_id}")
            bili_svc = BilibiliService()
            fetcher = ContentFetcher(
                bilibili_service=bili_svc,
                asr_service=asr,
                text_postprocessor=text_proc,
                summary_service=summary_svc,
            )
            content = await fetcher.fetch_content(
                content_id, cid=cache.cid, title=cache.title
            )
            corrected = content.content or ""
            raw_asr = content.asr_raw_text or corrected
            summary_block = content.summary_block or ""

        elif platform in ("youtube", "xiaoyuzhou"):
            from app.models import PlatformContentCache
            async with get_db_context() as db:
                result = await db.execute(
                    select(PlatformContentCache).where(
                        PlatformContentCache.platform == platform,
                        PlatformContentCache.content_id == content_id,
                    )
                )
                cache = result.scalar_one_or_none()
            if not cache:
                raise ValueError(f"{platform} 元数据不可用，请重新触发完整构建: {content_id}")

            if platform == "youtube":
                from app.services.youtube import YouTubeService
                from app.services.youtube_fetcher import YouTubeContentFetcher
                yt_svc = YouTubeService()
                fetcher = YouTubeContentFetcher(
                    asr_service=asr,
                    youtube_service=yt_svc,
                    text_postprocessor=text_proc,
                    summary_service=summary_svc,
                )
                video_info = {
                    "video_id": content_id,
                    "title": cache.title,
                    "url": cache.url,
                    "description": cache.description,
                    "channel": cache.author,
                    "duration": cache.duration,
                }
                content = await fetcher.fetch_content(video_info)
            else:  # xiaoyuzhou
                from app.services.xiaoyuzhou_fetcher import XiaoyuzhouContentFetcher
                fetcher = XiaoyuzhouContentFetcher(
                    asr_service=asr,
                    text_postprocessor=text_proc,
                    summary_service=summary_svc,
                )
                ep_info = {
                    "episode_id": content_id,
                    "title": cache.title,
                    "audio_url": cache.url,
                    "description": cache.description,
                    "duration": cache.duration,
                    "cover_url": cache.cover_url,
                }
                content = await fetcher.fetch_content(ep_info, podcast_title=cache.author or "")

            corrected = content.content or ""
            raw_asr = getattr(content, "asr_raw_text", "") or corrected
            summary_block = getattr(content, "summary_block", "") or ""

        else:
            raise ValueError(f"不支持的平台 ASR 重试: {platform}")

        # Persist stages
        async with get_db_context() as db:
            r = await _proc_svc.get_or_create(db, platform, content_id, title)
            if raw_asr:
                await _proc_svc.mark_asr_done(db, r, raw_asr)
            await _proc_svc.mark_correction_done(db, r, corrected)
            if summary_block:
                await _proc_svc.mark_summary_done(db, r, summary_block)
            await db.commit()

        # Re-index
        if platform in _PLATFORM_RAG_PREFIX:
            await _do_index(platform, content_id, title, corrected, summary_block or None)

        logger.info(f"[Retry] ASR 重试完成: {platform}/{content_id}")

    except Exception as e:
        logger.error(f"[Retry] ASR 重试失败: {platform}/{content_id}: {e}")
        try:
            async with get_db_context() as db:
                r = await _proc_svc.get_or_create(db, platform, content_id)
                await _proc_svc.mark_failed(db, r, "asr", str(e))
                await db.commit()
        except Exception:
            pass


async def _do_index(
    platform: str,
    content_id: str,
    title: str,
    corrected_text: str,
    summary_block: Optional[str],
):
    """删除旧向量、重建向量、标记 completed"""
    prefix = _PLATFORM_RAG_PREFIX.get(platform, "")
    if platform == "xiaoyuzhou":
        rag_id = f"{prefix}{content_id[:50]}"
    else:
        rag_id = f"{prefix}{content_id}"

    rag = get_rag_service()
    try:
        rag.delete_video(rag_id)
    except Exception:
        pass

    vc = VideoContent(
        bvid=rag_id,
        title=title,
        content=corrected_text,
        source=ContentSource.ASR,
        summary_block=summary_block,
    )
    rag.add_video_content(vc)

    async with get_db_context() as db:
        r = await _proc_svc.get_or_create(db, platform, content_id, title)
        await _proc_svc.mark_completed(db, r)
        await db.commit()
