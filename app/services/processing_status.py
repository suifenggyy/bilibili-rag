"""
内容处理状态服务
统一管理 ContentProcessingRecord 的读写，供各平台路由调用。
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import ContentProcessingRecord


class ProcessingStatusService:

    async def get_or_create(
        self,
        db: AsyncSession,
        platform: str,
        content_id: str,
        title: Optional[str] = None,
    ) -> ContentProcessingRecord:
        """获取已有记录，或创建 pending 记录。不 commit，由调用方决定。"""
        result = await db.execute(
            select(ContentProcessingRecord).where(
                ContentProcessingRecord.platform == platform,
                ContentProcessingRecord.content_id == content_id,
            )
        )
        rec = result.scalar_one_or_none()
        if rec is None:
            rec = ContentProcessingRecord(
                platform=platform,
                content_id=content_id,
                title=title or content_id,
                stage="pending",
            )
            db.add(rec)
        elif title and rec.title != title:
            rec.title = title
        return rec

    async def mark_asr_done(
        self, db: AsyncSession, rec: ContentProcessingRecord, asr_raw_text: str
    ) -> None:
        rec.asr_raw_text = asr_raw_text
        rec.stage = "asr_done"
        rec.updated_at = datetime.utcnow()

    async def mark_correction_done(
        self, db: AsyncSession, rec: ContentProcessingRecord, corrected_text: str
    ) -> None:
        rec.corrected_text = corrected_text
        rec.stage = "correction_done"
        rec.updated_at = datetime.utcnow()

    async def mark_summary_done(
        self, db: AsyncSession, rec: ContentProcessingRecord, summary_block: str
    ) -> None:
        rec.summary_block = summary_block
        rec.updated_at = datetime.utcnow()

    async def mark_completed(
        self, db: AsyncSession, rec: ContentProcessingRecord
    ) -> None:
        rec.stage = "completed"
        rec.updated_at = datetime.utcnow()

    async def mark_failed(
        self,
        db: AsyncSession,
        rec: ContentProcessingRecord,
        failed_stage: str,
        error: str,
    ) -> None:
        rec.stage = "failed"
        rec.failed_stage = failed_stage
        rec.error_message = str(error)[:1000]
        rec.updated_at = datetime.utcnow()

    def is_completed(self, rec: ContentProcessingRecord) -> bool:
        return rec.stage == "completed"

    async def list_records(
        self,
        db: AsyncSession,
        platform: Optional[str] = None,
        stage: Optional[str] = None,
        title_search: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ContentProcessingRecord]:
        q = select(ContentProcessingRecord)
        if platform:
            q = q.where(ContentProcessingRecord.platform == platform)
        if stage:
            q = q.where(ContentProcessingRecord.stage == stage)
        if title_search:
            q = q.where(ContentProcessingRecord.title.ilike(f"%{title_search}%"))
        q = q.order_by(ContentProcessingRecord.updated_at.desc()).limit(limit).offset(offset)
        result = await db.execute(q)
        return list(result.scalars().all())
