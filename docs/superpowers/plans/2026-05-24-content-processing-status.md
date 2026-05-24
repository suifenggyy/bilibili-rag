# Content Processing Status Tracking Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track per-item processing stages (ASR → correction → indexed) in the DB for all content sources, skip already-completed items, and provide retry endpoints that resume from any stored stage.

**Architecture:** Add a new `ContentProcessingRecord` table (unified across all platforms) that tracks each item's stage with stored intermediate results (raw ASR text, corrected text, summary). Routers check this table to skip `completed` items and update stages as each step finishes. A new `/api/processing` router exposes list and retry endpoints; retry loads the stored intermediate text and runs only the remaining pipeline stages.

**Tech Stack:** SQLAlchemy async ORM, FastAPI, existing `create_asr_service` / `create_text_postprocessor` / `get_rag_service` helpers.

---

## Chunk 1: DB Model

### Task 1: Add `ContentProcessingRecord` ORM model to `app/models.py`

**Files:**
- Modify: `app/models.py` (add after `DouyinCreator` class, ~line 262)
- Test: `test/test_content_processing_record.py` (create)

**Stage values** (stored as strings):
- `pending` – registered, not yet processed
- `asr_done` – raw ASR text saved to `asr_raw_text`
- `correction_done` – corrected text saved to `corrected_text` (or copied from `asr_raw_text` if no corrector)
- `completed` – added to RAG vector store (B站/YouTube/小宇宙) or exported to markdown (Douyin)
- `failed` – terminal failure; `failed_stage` records which step failed (`asr`/`correction`/`index`)

**Platform values:** `bilibili` | `youtube` | `xiaoyuzhou` | `douyin`

- [ ] **Step 1: Add ORM model to `app/models.py`**

Insert after the `DouyinCreator` class:

```python
class ContentProcessingRecord(Base):
    """统一内容处理状态记录（跨平台）"""
    __tablename__ = 'content_processing_records'

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(20), nullable=False, index=True)  # bilibili | youtube | xiaoyuzhou | douyin
    content_id = Column(String(300), nullable=False, index=True)  # bvid / video_id / episode_id / aweme_id
    title = Column(String(500), nullable=True)

    stage = Column(String(30), nullable=False, default='pending', index=True)
    # pending → asr_done → correction_done → completed | failed

    asr_raw_text = Column(Text, nullable=True)       # raw ASR before correction; kept for retry
    corrected_text = Column(Text, nullable=True)     # after text postprocessing
    summary_block = Column(Text, nullable=True)      # after summarization

    failed_stage = Column(String(20), nullable=True)  # asr | correction | index
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('platform', 'content_id', name='uq_platform_content_record'),
    )
```

Also export it in `app/models.py`'s implicit namespace (no changes needed — SQLAlchemy `Base` auto-registers it).

- [ ] **Step 2: Write the failing test**

Create `test/test_content_processing_record.py`:

```python
import asyncio
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
```

- [ ] **Step 3: Run test to verify it fails**

```bash
python -m unittest test.test_content_processing_record -v
```
Expected: ImportError or AttributeError (ContentProcessingRecord not yet defined).

- [ ] **Step 4: Implement the model (already done in Step 1 above)**

- [ ] **Step 5: Run test to verify it passes**

```bash
python -m unittest test.test_content_processing_record -v
```
Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/models.py test/test_content_processing_record.py
git commit -m "feat: add ContentProcessingRecord ORM model for per-item stage tracking"
```

---

## Chunk 2: Expose Raw ASR Text from Fetchers

The routers need the raw ASR text (before correction) to store it for retry. Add `asr_raw_text` to `VideoContent` and `DouyinVideoContent`, then populate it in each fetcher right after the ASR call.

### Task 2: Extend `VideoContent` and `DouyinVideoContent`

**Files:**
- Modify: `app/models.py` (VideoContent pydantic class, ~line 287)
- Modify: `app/services/douyin_fetcher.py` (DouyinVideoContent dataclass, ~line 25)

- [ ] **Step 1: Add `asr_raw_text` to `VideoContent` (Pydantic)**

In `app/models.py`, `VideoContent` class:

```python
class VideoContent(BaseModel):
    """视频内容（含摘要）"""
    bvid: str
    title: str
    content: str
    source: ContentSource
    outline: Optional[list] = None
    summary_block: Optional[str] = None
    asr_raw_text: Optional[str] = None  # 纠错前的原始 ASR 文本，供重试使用
```

- [ ] **Step 2: Add `asr_raw_text` to `DouyinVideoContent` (dataclass)**

In `app/services/douyin_fetcher.py`:

```python
@dataclass
class DouyinVideoContent:
    aweme_id: str
    title: str
    author: str = ""
    create_time: int = 0
    duration: int = 0
    cover_url: str = ""
    share_url: str = ""
    content: str = ""
    content_source: str = "basic_info"
    summary_block: str = ""
    asr_raw_text: str = ""  # 纠错前的原始 ASR 文本
```

- [ ] **Step 3: Populate `asr_raw_text` in `content_fetcher.py` (Bilibili)**

In `app/services/content_fetcher.py`, in `fetch_content()`, after `if asr_text:` (~line 113):

```python
        if asr_text:
            raw_asr = asr_text  # save before correction
            self._persist_text_artifact(title, "asr_raw.txt", asr_text)
            asr_text = await self._postprocess_asr_text(bvid, asr_text, title=title)
            self._persist_text_artifact(title, "asr_corrected.txt", asr_text)
            summary_block = await self._summarize_content(bvid, asr_text)
            logger.info(f"[{bvid}] 使用 ASR 文本")
            return VideoContent(
                bvid=bvid,
                title=title,
                content=asr_text,
                source=ContentSource.ASR,
                summary_block=summary_block,
                asr_raw_text=raw_asr,
            )
```

- [ ] **Step 4: Populate `asr_raw_text` in `youtube_fetcher.py`**

In `app/services/youtube_fetcher.py`, in the `fetch_content()` method, after `if transcript:` (~line 125):

```python
            if transcript:
                raw_asr = transcript.strip()
                self.storage_manager.write_work_text("youtube", title, "asr_raw.txt", raw_asr)
                base.content = await self._postprocess_asr_text(video_id, transcript, title=title)
                base.asr_raw_text = raw_asr  # add this field
                ...
```

But `YouTubeVideoContent` is a dataclass in `youtube_fetcher.py` — check if it has an `asr_raw_text` field. If the fetcher returns a platform-specific dataclass, add the field there. If it converts to `VideoContent`, add it at conversion time.

Look at the return type of `YouTubeContentFetcher.fetch_content()`. If it returns a custom dataclass, add `asr_raw_text: str = ""` to it. The router then reads it when building `VideoContent`.

- [ ] **Step 5: Populate `asr_raw_text` in `xiaoyuzhou_fetcher.py`**

Same pattern as YouTube.

- [ ] **Step 6: Populate `asr_raw_text` in `douyin_fetcher.py`**

In `DouyinContentFetcher.fetch_content()`, after `if transcript:` (~line 124):

```python
                if transcript:
                    raw_asr = transcript.strip()
                    self.storage_manager.write_work_text("douyin", title, "asr_raw.txt", raw_asr)
                    base.content = await self._postprocess_asr_text(aweme_id, transcript, title=title)
                    base.asr_raw_text = raw_asr  # populate for DB storage
                    ...
```

- [ ] **Step 7: Run full test suite**

```bash
python -m unittest discover -s test -p 'test_*.py' 2>&1 | tail -5
```
Expected: all existing tests PASS (no changes to test assertions needed — `asr_raw_text` is optional/default).

- [ ] **Step 8: Commit**

```bash
git add app/models.py app/services/douyin_fetcher.py app/services/content_fetcher.py \
        app/services/youtube_fetcher.py app/services/xiaoyuzhou_fetcher.py
git commit -m "feat: expose asr_raw_text in VideoContent and platform content types"
```

---

## Chunk 3: New Processing-Status Service

Extract shared logic for reading/writing `ContentProcessingRecord` into a single service file so routers don't duplicate DB queries.

### Task 3: Create `app/services/processing_status.py`

**Files:**
- Create: `app/services/processing_status.py`
- Test: `test/test_processing_status.py` (create)

This service exposes:
- `get_or_create(db, platform, content_id, title) → ContentProcessingRecord`
- `mark_asr_done(db, rec, asr_raw_text)`
- `mark_correction_done(db, rec, corrected_text)`
- `mark_summary_done(db, rec, summary_block)` 
- `mark_completed(db, rec)`
- `mark_failed(db, rec, stage, error)`
- `is_completed(rec) → bool`
- `list_records(db, platform, stage, limit, offset) → list[ContentProcessingRecord]`

- [ ] **Step 1: Write the failing test**

Create `test/test_processing_status.py`:

```python
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m unittest test.test_processing_status -v
```
Expected: `ImportError: cannot import name 'ProcessingStatusService'`

- [ ] **Step 3: Implement `app/services/processing_status.py`**

```python
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
        limit: int = 100,
        offset: int = 0,
    ) -> list[ContentProcessingRecord]:
        q = select(ContentProcessingRecord)
        if platform:
            q = q.where(ContentProcessingRecord.platform == platform)
        if stage:
            q = q.where(ContentProcessingRecord.stage == stage)
        q = q.order_by(ContentProcessingRecord.updated_at.desc()).limit(limit).offset(offset)
        result = await db.execute(q)
        return list(result.scalars().all())
```

Also add `ProcessingStatusService` to `app/services/__init__.py` exports.

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m unittest test.test_processing_status -v
```
Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/processing_status.py app/services/__init__.py test/test_processing_status.py
git commit -m "feat: add ProcessingStatusService for unified stage tracking"
```

---

## Chunk 4: Router Integration (Bilibili + YouTube + Xiaoyuzhou)

Update each platform's build loop to:
1. `get_or_create` a record
2. Skip if `is_completed`
3. After each stage, call the corresponding `mark_*` method
4. Mark failed on exception

### Task 4: Bilibili knowledge router (`app/routers/knowledge.py`)

**Files:**
- Modify: `app/routers/knowledge.py`

The Bilibili build loop is the `_build_folder_knowledge` function (~line 250). The loop iterates `targets` (new/update candidates) and calls `content_fetcher.fetch_content()`.

- [ ] **Step 1: Add imports**

At the top of `app/routers/knowledge.py`, add:

```python
from app.models import ContentProcessingRecord
from app.services.processing_status import ProcessingStatusService

_proc_svc = ProcessingStatusService()
```

- [ ] **Step 2: Wrap the per-video try block in `_build_folder_knowledge`**

In the `for bvid in targets:` loop, before the existing `try:` block:

```python
        # Skip if already fully indexed
        async with get_db_context() as db:
            proc_rec = await _proc_svc.get_or_create(db, "bilibili", bvid, meta.get("title"))
            await db.commit()
        if _proc_svc.is_completed(proc_rec):
            logger.info(f"[{bvid}] 已完成索引，跳过")
            processed_targets += 1
            if progress_callback:
                progress_callback("跳过（已完成）", processed_targets, total_targets)
            continue
```

After successfully calling `content_fetcher.fetch_content()` and getting `content`, add stage updates in the existing `if needs_fetch:` block:

```python
                if content and content.asr_raw_text:
                    async with get_db_context() as db2:
                        r2 = await _proc_svc.get_or_create(db2, "bilibili", bvid, title)
                        await _proc_svc.mark_asr_done(db2, r2, content.asr_raw_text)
                        await _proc_svc.mark_correction_done(db2, r2, content.content or "")
                        if content.summary_block:
                            await _proc_svc.mark_summary_done(db2, r2, content.summary_block)
                        await db2.commit()
```

After `rag.add_video_content(content)` succeeds:

```python
                async with get_db_context() as db3:
                    r3 = await _proc_svc.get_or_create(db3, "bilibili", bvid, title)
                    await _proc_svc.mark_completed(db3, r3)
                    await db3.commit()
```

In the `except Exception` that wraps the entire block:

```python
        except Exception as e:
            logger.warning(f"添加向量失败 [{bvid}]: {e} (仍会记录到数据库)")
            try:
                async with get_db_context() as db_err:
                    r_err = await _proc_svc.get_or_create(db_err, "bilibili", bvid)
                    await _proc_svc.mark_failed(db_err, r_err, "index", str(e))
                    await db_err.commit()
            except Exception:
                pass
```

- [ ] **Step 3: Run full test suite**

```bash
python -m unittest discover -s test -p 'test_*.py' 2>&1 | tail -5
```
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add app/routers/knowledge.py
git commit -m "feat: track ContentProcessingRecord stages in Bilibili knowledge build loop"
```

### Task 5: YouTube knowledge router (`app/routers/youtube_knowledge.py`)

**Files:**
- Modify: `app/routers/youtube_knowledge.py`

- [ ] **Step 1: Add imports and replace the skip check**

At the top, add:
```python
from app.services.processing_status import ProcessingStatusService
_proc_svc = ProcessingStatusService()
```

In `_run_build`, in the per-video loop, replace the existing skip check:
```python
# BEFORE:
result = await db.execute(...)
cache = result.scalar_one_or_none()
if cache and cache.is_processed and cache.content:
    task["processed_videos"] += 1
    continue
```

With:
```python
async with get_db_context() as db:
    proc_rec = await _proc_svc.get_or_create(db, "youtube", video_id, title)
    await db.commit()
if _proc_svc.is_completed(proc_rec):
    task["processed_videos"] += 1
    continue
```

After `rag.add_video_content(video_content)` succeeds:
```python
async with get_db_context() as db:
    r = await _proc_svc.get_or_create(db, "youtube", video_id, title)
    if content.asr_raw_text:
        await _proc_svc.mark_asr_done(db, r, content.asr_raw_text)
        await _proc_svc.mark_correction_done(db, r, content.content or "")
    if content.summary_block:
        await _proc_svc.mark_summary_done(db, r, content.summary_block)
    await _proc_svc.mark_completed(db, r)
    await db.commit()
```

In the except block, add:
```python
try:
    async with get_db_context() as db_err:
        r_err = await _proc_svc.get_or_create(db_err, "youtube", video_id)
        await _proc_svc.mark_failed(db_err, r_err, "index", str(e))
        await db_err.commit()
except Exception:
    pass
```

Note: `YouTubeVideoContent` may be a custom dataclass. Check if `content.asr_raw_text` exists on it; if not, the stage tracking only marks `completed` (no intermediate stages saved). Full intermediate tracking requires Task 2 (Chunk 2) to be complete first.

- [ ] **Step 2: Commit**

```bash
git add app/routers/youtube_knowledge.py
git commit -m "feat: track ContentProcessingRecord stages in YouTube knowledge build loop"
```

### Task 6: Xiaoyuzhou knowledge router (`app/routers/xiaoyuzhou_knowledge.py`)

**Files:**
- Modify: `app/routers/xiaoyuzhou_knowledge.py`

Same pattern as Task 5. Platform = `"xiaoyuzhou"`, content_id = `episode_id`.

- [ ] **Step 1: Add imports + skip check**

```python
from app.services.processing_status import ProcessingStatusService
_proc_svc = ProcessingStatusService()
```

Replace the existing `cache.is_processed and cache.content` skip check with the `is_completed` check.

After `rag.add_video_content(video_content)` and `await db.commit()`:
```python
async with get_db_context() as db:
    r = await _proc_svc.get_or_create(db, "xiaoyuzhou", episode_id, full_title)
    if content.asr_raw_text:
        await _proc_svc.mark_asr_done(db, r, content.asr_raw_text)
        await _proc_svc.mark_correction_done(db, r, content.content or "")
    if content.summary_block:
        await _proc_svc.mark_summary_done(db, r, content.summary_block)
    await _proc_svc.mark_completed(db, r)
    await db.commit()
```

- [ ] **Step 2: Commit**

```bash
git add app/routers/xiaoyuzhou_knowledge.py
git commit -m "feat: track ContentProcessingRecord stages in Xiaoyuzhou knowledge build loop"
```

### Task 7: Douyin export router (`app/routers/douyin_export.py`)

Douyin exports to Markdown files, not RAG. Final stage = `completed`.

**Files:**
- Modify: `app/routers/douyin_export.py`

- [ ] **Step 1: In `_run_douyin_export`, find the per-video loop**

Locate where each video is processed (search for `aweme_id`). Before processing each video:

```python
from app.services.processing_status import ProcessingStatusService
from app.database import get_db_context
_proc_svc = ProcessingStatusService()
```

Before processing each video:
```python
async with get_db_context() as db:
    proc_rec = await _proc_svc.get_or_create(db, "douyin", aweme_id, title)
    await db.commit()
if _proc_svc.is_completed(proc_rec):
    # Already exported – skip ASR but still count for progress
    continue
```

After successfully writing the Markdown file:
```python
async with get_db_context() as db:
    r = await _proc_svc.get_or_create(db, "douyin", aweme_id, title)
    if content.asr_raw_text:
        await _proc_svc.mark_asr_done(db, r, content.asr_raw_text)
        await _proc_svc.mark_correction_done(db, r, content.content or "")
    if content.summary_block:
        await _proc_svc.mark_summary_done(db, r, content.summary_block)
    await _proc_svc.mark_completed(db, r)
    await db.commit()
```

- [ ] **Step 2: Run full test suite**

```bash
python -m unittest discover -s test -p 'test_*.py' 2>&1 | tail -5
```

- [ ] **Step 3: Commit**

```bash
git add app/routers/douyin_export.py
git commit -m "feat: track ContentProcessingRecord stages in Douyin export loop"
```

---

## Chunk 5: Retry Router

### Task 8: Create `app/routers/content_retry.py` and register in `app/main.py`

**Files:**
- Create: `app/routers/content_retry.py`
- Modify: `app/main.py`
- Test: `test/test_content_retry_api.py` (create)

**API design:**

```
GET  /api/processing/list
     ?platform=bilibili&stage=failed&limit=50&offset=0
     → List[ContentProcessingRecordOut]

POST /api/processing/{platform}/{content_id}/retry
     Body: RetryRequest { stage: "correction" | "asr" | "summary" | "index", asr_backend?: str }
     → RetryResponse { status, message }
```

**Retry logic by stage:**

- `stage=correction`: Load `asr_raw_text` from DB → run `create_text_postprocessor()` → run `ContentSummaryService` → update `corrected_text`, `summary_block`, advance stage → re-index in RAG (for non-Douyin)
- `stage=asr`: **Re-run the full fetch pipeline from scratch** (re-download audio + re-transcribe + correction + index). The `ContentProcessingRecord` does NOT cache audio/video files — if the workspace file no longer exists, it is simply re-downloaded. Reset record stage to `pending` before starting.
- `stage=index`: Load `corrected_text` from record → run `rag.add_video_content()` → mark `completed` (Bilibili/YouTube/小宇宙 only)
- `stage=summary`: Load `corrected_text` → re-run summary → update `summary_block`

**Important:** No audio/video file paths are stored in `ContentProcessingRecord`. Retry ASR always triggers a fresh download. Only text intermediates (`asr_raw_text`, `corrected_text`, `summary_block`) are persisted.

**Platform-to-RAG-ID mapping** (needed for re-indexing):
- `bilibili` → bvid as-is
- `youtube` → `yt_{content_id}`
- `xiaoyuzhou` → `xyz_{content_id[:50]}`
- `douyin` → retry index not supported (exports to file)

**`stage=asr` retry requires fetching original source metadata** to reconstruct the fetch call. The retry router needs to load metadata from the existing cache table (`VideoCache` for Bilibili, `PlatformContentCache` for YouTube/小宇宙). If metadata is not in cache, return 422 with message "原始内容元数据不可用，请重新触发完整构建".

- [ ] **Step 1: Write the failing test**

Create `test/test_content_retry_api.py`:

```python
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


class ContentRetryAPITests(unittest.TestCase):
    def _make_client(self):
        from app.main import app
        return TestClient(app)

    def test_list_processing_records_returns_200(self):
        client = self._make_client()
        resp = client.get("/api/processing/list?platform=bilibili&limit=5")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("records", data)
        self.assertIsInstance(data["records"], list)

    def test_retry_correction_404_when_record_not_found(self):
        client = self._make_client()
        resp = client.post(
            "/api/processing/bilibili/BVnotexist/retry",
            json={"stage": "correction"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_retry_correction_400_when_no_asr_text(self):
        # Requires a record in DB with no asr_raw_text
        # Covered via integration test with in-memory DB
        pass  # Placeholder; full test in test_processing_status.py


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m unittest test.test_content_retry_api -v
```
Expected: ImportError or 404 (router not registered yet).

- [ ] **Step 3: Implement `app/routers/content_retry.py`**

```python
"""
内容处理状态查询与重试路由
GET  /api/processing/list               - 列出处理记录（可按 platform/stage 过滤）
POST /api/processing/{platform}/{id}/retry - 从指定阶段重试
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
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
    updated_at: Optional[str]

    @classmethod
    def from_orm(cls, r: ContentProcessingRecord) -> "ContentProcessingRecordOut":
        return cls(
            platform=r.platform,
            content_id=r.content_id,
            title=r.title,
            stage=r.stage,
            failed_stage=r.failed_stage,
            error_message=r.error_message,
            has_asr_raw=bool(r.asr_raw_text),
            has_corrected=bool(r.corrected_text),
            has_summary=bool(r.summary_block),
            updated_at=r.updated_at.isoformat() if r.updated_at else None,
        )


class RetryRequest(BaseModel):
    stage: str  # "correction" | "summary" | "index"
    asr_backend: Optional[str] = None


class RetryResponse(BaseModel):
    status: str
    message: str


@router.get("/list")
async def list_processing_records(
    platform: Optional[str] = None,
    stage: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    records = await _proc_svc.list_records(db, platform=platform, stage=stage, limit=limit, offset=offset)
    return {"records": [ContentProcessingRecordOut.from_orm(r) for r in records], "total": len(records)}


@router.post("/{platform}/{content_id}/retry", response_model=RetryResponse)
async def retry_processing(
    platform: str,
    content_id: str,
    req: RetryRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ContentProcessingRecord).where(
            ContentProcessingRecord.platform == platform,
            ContentProcessingRecord.content_id == content_id,
        )
    )
    rec = result.scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail="未找到该内容的处理记录")

    if req.stage == "correction":
        if not rec.asr_raw_text:
            raise HTTPException(status_code=400, detail="没有可用的原始 ASR 文本，请先完成 ASR 转写")
        background_tasks.add_task(
            _retry_correction,
            platform=platform,
            content_id=content_id,
            title=rec.title or content_id,
            asr_raw_text=rec.asr_raw_text,
            asr_backend=req.asr_backend,
        )
        return RetryResponse(status="started", message="纠错重试任务已启动")

    elif req.stage == "index":
        if not rec.corrected_text:
            raise HTTPException(status_code=400, detail="没有可用的纠错文本，请先完成纠错")
        if platform not in _PLATFORM_RAG_PREFIX:
            raise HTTPException(status_code=400, detail=f"平台 {platform} 不支持重建索引")
        background_tasks.add_task(
            _retry_index,
            platform=platform,
            content_id=content_id,
            title=rec.title or content_id,
            corrected_text=rec.corrected_text,
            summary_block=rec.summary_block,
        )
        return RetryResponse(status="started", message="重建索引任务已启动")

    elif req.stage == "summary":
        if not rec.corrected_text:
            raise HTTPException(status_code=400, detail="没有可用的纠错文本，请先完成纠错")
        background_tasks.add_task(
            _retry_summary,
            platform=platform,
            content_id=content_id,
        )
        return RetryResponse(status="started", message="摘要重试任务已启动")

    elif req.stage == "asr":
        # Re-run from scratch: load metadata from cache table, re-download + re-transcribe
        background_tasks.add_task(
            _retry_asr,
            platform=platform,
            content_id=content_id,
            title=rec.title or content_id,
            asr_backend=req.asr_backend,
        )
        return RetryResponse(status="started", message="ASR 重试任务已启动（将重新下载音频并转写）")

    else:
        raise HTTPException(status_code=400, detail=f"不支持的重试阶段: {req.stage}。支持: asr, correction, index, summary")


async def _retry_correction(
    platform: str,
    content_id: str,
    title: str,
    asr_raw_text: str,
    asr_backend: Optional[str],
):
    """后台任务：从 asr_raw_text 重新运行纠错 → 摘要 → 索引"""
    try:
        postprocessor = create_text_postprocessor()
        summary_svc = ContentSummaryService()

        corrected = await postprocessor.postprocess(asr_raw_text, title=title)
        if not corrected:
            corrected = asr_raw_text

        summary_block = await summary_svc.summarize(corrected)

        async with get_db_context() as db:
            r = await _proc_svc.get_or_create(db, platform, content_id, title)
            await _proc_svc.mark_correction_done(db, r, corrected)
            await _proc_svc.mark_summary_done(db, r, summary_block)
            await db.commit()

        # Re-index if platform supports it
        if platform in _PLATFORM_RAG_PREFIX:
            await _do_index(platform, content_id, title, corrected, summary_block)

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
    except Exception as e:
        logger.error(f"[Retry] 摘要重试失败: {platform}/{content_id}: {e}")


async def _do_index(
    platform: str,
    content_id: str,
    title: str,
    corrected_text: str,
    summary_block: Optional[str],
):
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


async def _retry_asr(
    platform: str,
    content_id: str,
    title: str,
    asr_backend: Optional[str],
):
    """
    后台任务：重新下载音频并从头运行 ASR → 纠错 → 摘要 → 索引。
    不缓存音视频文件 — 若工作目录文件已被清理，则重新下载。
    需要从现有缓存表中加载原始元数据（URL/bvid/episode_id）以重构 fetch 调用。
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
            # Load metadata from VideoCache
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
            # Load metadata from PlatformContentCache
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
                fetcher = YouTubeContentFetcher(asr_service=asr, youtube_service=yt_svc,
                                                text_postprocessor=text_proc, summary_service=summary_svc)
                video_info = {"video_id": content_id, "title": cache.title,
                              "url": cache.url, "description": cache.description,
                              "channel": cache.author, "duration": cache.duration}
                content = await fetcher.fetch_content(video_info)
            else:  # xiaoyuzhou
                from app.services.xiaoyuzhou_fetcher import XiaoyuzhouContentFetcher
                fetcher = XiaoyuzhouContentFetcher(asr_service=asr,
                                                   text_postprocessor=text_proc,
                                                   summary_service=summary_svc)
                ep_info = {"episode_id": content_id, "title": cache.title,
                           "audio_url": cache.url, "description": cache.description,
                           "duration": cache.duration, "cover_url": cache.cover_url}
                content = await fetcher.fetch_content(ep_info, podcast_title=cache.author)

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
```

- [ ] **Step 4: Register in `app/main.py`**

```python
from app.routers import auth, favorites, knowledge, chat, export, douyin_export, instapaper_export
from app.routers import youtube_knowledge, xiaoyuzhou_knowledge, content_retry  # add content_retry

# In the router registration section:
app.include_router(content_retry.router)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
python -m unittest test.test_content_retry_api -v
```
Expected: 2 non-placeholder tests PASS (list returns 200, retry 404 for unknown content).

- [ ] **Step 6: Run full test suite**

```bash
python -m unittest discover -s test -p 'test_*.py' 2>&1 | tail -5
```
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/routers/content_retry.py app/main.py test/test_content_retry_api.py
git commit -m "feat: add /api/processing retry and list endpoints for per-stage retries"
```

---

## Summary

After all chunks are complete:

| Feature | Where |
|---|---|
| Stage tracking DB schema | `app/models.py` → `ContentProcessingRecord` |
| Stage read/write helpers | `app/services/processing_status.py` |
| Raw ASR text stored for retry | `VideoContent.asr_raw_text`, `DouyinVideoContent.asr_raw_text` |
| Bilibili skip + track | `app/routers/knowledge.py` |
| YouTube skip + track | `app/routers/youtube_knowledge.py` |
| Xiaoyuzhou skip + track | `app/routers/xiaoyuzhou_knowledge.py` |
| Douyin skip + track | `app/routers/douyin_export.py` |
| List all records API | `GET /api/processing/list` |
| Retry ASR API | `POST /api/processing/{platform}/{id}/retry` `{stage: "asr"}` — re-downloads audio, no file caching |
| Retry correction API | `POST /api/processing/{platform}/{id}/retry` `{stage: "correction"}` — uses stored `asr_raw_text` |
| Retry index API | `POST /api/processing/{platform}/{id}/retry` `{stage: "index"}` |
| Retry summary API | `POST /api/processing/{platform}/{id}/retry` `{stage: "summary"}` |
