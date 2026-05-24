"""Tests for ProcessingStatusService."""
import unittest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.models import Base, ContentProcessingRecord
from app.services.processing_status import ProcessingStatusService


class ProcessingStatusServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_get_or_create_creates_new_record(self):
        svc = ProcessingStatusService()
        async with self.Session() as db:
            rec = await svc.get_or_create(db, "bilibili", "BV1abc", "测试视频")
            await db.commit()
            self.assertEqual(rec.stage, "pending")

    async def test_get_or_create_returns_existing(self):
        svc = ProcessingStatusService()
        async with self.Session() as db:
            await svc.get_or_create(db, "bilibili", "BV1abc", "测试视频")
            await db.commit()
        async with self.Session() as db:
            rec = await svc.get_or_create(db, "bilibili", "BV1abc", "新标题")
            # Stage should remain as-is (pending), not reset
            self.assertEqual(rec.stage, "pending")

    async def test_mark_asr_done(self):
        svc = ProcessingStatusService()
        async with self.Session() as db:
            rec = await svc.get_or_create(db, "youtube", "yt1", "YT video")
            await svc.mark_asr_done(db, rec, "原始转写文本")
            await db.commit()
            self.assertEqual(rec.stage, "asr_done")
            self.assertEqual(rec.asr_raw_text, "原始转写文本")

    async def test_mark_completed(self):
        svc = ProcessingStatusService()
        async with self.Session() as db:
            rec = await svc.get_or_create(db, "xiaoyuzhou", "ep1", "播客集数")
            await svc.mark_completed(db, rec)
            await db.commit()
            self.assertTrue(svc.is_completed(rec))

    async def test_mark_failed(self):
        svc = ProcessingStatusService()
        async with self.Session() as db:
            rec = await svc.get_or_create(db, "bilibili", "BV_fail", "失败视频")
            await svc.mark_failed(db, rec, "asr", "网络超时")
            await db.commit()
            self.assertEqual(rec.stage, "failed")
            self.assertEqual(rec.failed_stage, "asr")
            self.assertIn("网络超时", rec.error_message)

    async def test_is_completed_false_for_pending(self):
        svc = ProcessingStatusService()
        rec = ContentProcessingRecord(platform="bilibili", content_id="x", stage="pending")
        self.assertFalse(svc.is_completed(rec))

    async def test_list_records_by_stage(self):
        svc = ProcessingStatusService()
        async with self.Session() as db:
            for i in range(3):
                r = await svc.get_or_create(db, "bilibili", f"BV{i}", f"video {i}")
                if i < 2:
                    await svc.mark_completed(db, r)
            await db.commit()
        async with self.Session() as db:
            recs = await svc.list_records(db, platform="bilibili", stage="completed")
            self.assertEqual(len(recs), 2)


if __name__ == "__main__":
    unittest.main()
