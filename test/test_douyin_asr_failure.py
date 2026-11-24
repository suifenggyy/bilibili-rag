import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.services.content_fetcher import ASRProcessingError
from app.services.content_storage import ContentStorageManager
from app.services.douyin_fetcher import DouyinContentFetcher, DouyinVideoContent
from scripts import export_douyin_to_md


class DouyinASRFailureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage = ContentStorageManager(
            workspace_root=str(Path(self.directory.name) / "work"),
            export_root=str(Path(self.directory.name) / "export"),
        )
        self.asr = SimpleNamespace(transcribe_local_file=AsyncMock(return_value=None))
        self.postprocessor = SimpleNamespace(postprocess=AsyncMock(return_value="corrected text"))
        self.summary = SimpleNamespace(summarize=AsyncMock(return_value=""))
        self.fetcher = DouyinContentFetcher(
            self.asr, storage_manager=self.storage,
            text_postprocessor=self.postprocessor, summary_service=self.summary,
        )
        self.video = {"aweme_id": "123", "title": "failed video",
                      "play_urls": ["https://example.com/one", "https://example.com/two"]}

    async def test_failed_asr_stops_without_retrying_other_video_urls(self):
        with (
            patch.object(self.fetcher, "_download_video", AsyncMock(return_value=True)) as download,
            patch.object(self.fetcher, "_extract_and_transcribe", AsyncMock(return_value=None)) as transcribe,
        ):
            with self.assertRaises(ASRProcessingError):
                await self.fetcher.fetch_content(self.video)
        download.assert_awaited_once()
        transcribe.assert_awaited_once()
        self.postprocessor.postprocess.assert_not_awaited()
        self.summary.summarize.assert_not_awaited()
        self.assertIsNone(self.storage.read_work_text("douyin", "failed video", "asr_raw.txt"))

    async def test_failed_asr_from_cached_audio_does_not_redownload(self):
        self.storage.build_work_file_path("douyin", "failed video", "audio.wav").write_bytes(b"x" * 2048)
        with patch.object(self.fetcher, "_download_video", AsyncMock()) as download:
            with self.assertRaises(ASRProcessingError):
                await self.fetcher.fetch_content(self.video)
        self.asr.transcribe_local_file.assert_awaited_once()
        download.assert_not_awaited()
        self.summary.summarize.assert_not_awaited()

    async def test_failed_asr_from_cached_video_does_not_redownload(self):
        self.storage.build_work_file_path("douyin", "failed video", "video.mp4").write_bytes(b"x" * 11000)
        with (
            patch.object(self.fetcher, "_download_video", AsyncMock()) as download,
            patch.object(self.fetcher, "_extract_and_transcribe", AsyncMock(return_value=None)) as transcribe,
        ):
            with self.assertRaises(ASRProcessingError):
                await self.fetcher.fetch_content(self.video)
        transcribe.assert_awaited_once()
        download.assert_not_awaited()

    async def test_whitespace_transcript_is_a_failure(self):
        with (
            patch.object(self.fetcher, "_download_video", AsyncMock(return_value=True)),
            patch.object(self.fetcher, "_extract_and_transcribe", AsyncMock(return_value=" \n")),
        ):
            with self.assertRaises(ASRProcessingError):
                await self.fetcher.fetch_content(self.video)
        self.summary.summarize.assert_not_awaited()

    async def test_raised_asr_failure_is_not_retried(self):
        with (
            patch.object(self.fetcher, "_download_video", AsyncMock(return_value=True)),
            patch.object(self.fetcher, "_extract_and_transcribe",
                         AsyncMock(side_effect=RuntimeError("ASR unavailable"))) as transcribe,
        ):
            with self.assertRaisesRegex(RuntimeError, "ASR unavailable"):
                await self.fetcher.fetch_content(self.video)
        transcribe.assert_awaited_once()

    async def test_missing_asr_service_is_a_failure(self):
        self.fetcher.asr = None
        with self.assertRaises(ASRProcessingError):
            await self.fetcher.fetch_content(self.video)

    async def test_failed_export_does_not_write_md_or_mark_completed(self):
        await self._export_failure(self.fetcher)

    async def test_export_rejects_basic_info_fallback(self):
        fallback = SimpleNamespace(fetch_content=AsyncMock(return_value=DouyinVideoContent(
            aweme_id="123", title="failed video", content="metadata", content_source="basic_info",
        )))
        await self._export_failure(fallback)

    async def test_failed_asr_does_not_overwrite_existing_markdown(self):
        await self._export_failure(self.fetcher, existing_markdown="previous successful export")

    async def test_short_valid_cached_audio_transcript_is_accepted(self):
        self.storage.build_work_file_path("douyin", "failed video", "audio.wav").write_bytes(b"x" * 2048)
        self.asr.transcribe_local_file.return_value = "short transcript"
        with patch.object(self.fetcher, "_download_video", AsyncMock()) as download:
            content = await self.fetcher.fetch_content(self.video)
        self.assertEqual(content.content_source, "asr")
        self.assertEqual(content.asr_raw_text, "short transcript")
        self.asr.transcribe_local_file.assert_awaited_once()
        download.assert_not_awaited()

    async def test_valid_asr_cache_does_not_call_unavailable_service(self):
        self.storage.write_work_text("douyin", "failed video", "asr_raw.txt", "cached transcript")
        content = await self.fetcher.fetch_content(self.video)
        self.assertEqual(content.content_source, "asr")
        self.assertEqual(content.asr_raw_text, "cached transcript")
        self.asr.transcribe_local_file.assert_not_awaited()

    async def _export_failure(self, fetcher, existing_markdown=None):
        output = Path(self.directory.name) / "export"
        output.mkdir(exist_ok=True)
        md_path = output / "failed video.md"
        if existing_markdown is not None:
            md_path.write_text(existing_markdown, encoding="utf-8")
        status = Mock()
        status.get_or_create = AsyncMock(return_value=SimpleNamespace())
        status.is_exported.return_value = False
        status.is_completed.return_value = False
        status.mark_failed = AsyncMock()
        status.mark_completed = AsyncMock()

        @asynccontextmanager
        async def session():
            yield SimpleNamespace(commit=AsyncMock())

        with (
            patch.object(export_douyin_to_md, "get_db_context", session),
            patch.object(export_douyin_to_md, "ProcessingStatusService", return_value=status),
            patch("app.services.douyin.DouyinService.parse_video_info", return_value=self.video),
            patch.object(self.fetcher, "_download_video", AsyncMock(return_value=True)),
            patch.object(self.fetcher, "_extract_and_transcribe", AsyncMock(return_value=None)),
            patch.object(export_douyin_to_md, "_build_markdown", return_value="should not be written") as render,
        ):
            result = await export_douyin_to_md.export_videos(fetcher, [{}], output, concurrency=1)
        self.assertEqual(result, (0, 1, 0))
        if existing_markdown is None:
            self.assertEqual(list(output.glob("*.md")), [])
        else:
            self.assertEqual(md_path.read_text(encoding="utf-8"), existing_markdown)
        render.assert_not_called()
        status.mark_completed.assert_not_awaited()
        status.mark_failed.assert_awaited_once()
