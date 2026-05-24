"""Tests for ContentProcessingRecord ORM model."""
import unittest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from app.models import Base, ContentProcessingRecord


class ContentProcessingRecordTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_create_and_query_record(self):
        async with self.Session() as db:
            rec = ContentProcessingRecord(
                platform="bilibili",
                content_id="BV1test",
                title="测试视频",
                stage="pending",
            )
            db.add(rec)
            await db.commit()
            result = await db.execute(
                select(ContentProcessingRecord).where(
                    ContentProcessingRecord.platform == "bilibili",
                    ContentProcessingRecord.content_id == "BV1test",
                )
            )
            row = result.scalar_one_or_none()
            self.assertIsNotNone(row)
            self.assertEqual(row.stage, "pending")

    async def test_unique_constraint_prevents_duplicate(self):
        from sqlalchemy.exc import IntegrityError
        async with self.Session() as db:
            db.add(ContentProcessingRecord(platform="youtube", content_id="yt123", stage="pending"))
            await db.commit()
        async with self.Session() as db:
            db.add(ContentProcessingRecord(platform="youtube", content_id="yt123", stage="asr_done"))
            with self.assertRaises(IntegrityError):
                await db.commit()

    async def test_update_stage_and_asr_raw_text(self):
        async with self.Session() as db:
            rec = ContentProcessingRecord(platform="xiaoyuzhou", content_id="ep1", stage="pending")
            db.add(rec)
            await db.commit()
            rec.stage = "asr_done"
            rec.asr_raw_text = "这是原始 ASR 文本"
            await db.commit()
        async with self.Session() as db:
            result = await db.execute(
                select(ContentProcessingRecord).where(
                    ContentProcessingRecord.platform == "xiaoyuzhou",
                    ContentProcessingRecord.content_id == "ep1",
                )
            )
            row = result.scalar_one_or_none()
            self.assertEqual(row.stage, "asr_done")
            self.assertEqual(row.asr_raw_text, "这是原始 ASR 文本")


if __name__ == "__main__":
    unittest.main()
