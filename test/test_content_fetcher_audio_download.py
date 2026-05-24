import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.models import ContentSource
from app.services.content_fetcher import AudioDownloadError, ASRProcessingError, ContentFetcher


class ContentFetcherAudioDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_content_uses_corrected_asr_text_when_postprocessor_succeeds(self):
        bili = type("FakeBili", (), {})()
        asr = type("FakeASR", (), {})()
        postprocessor = type("FakePostprocessor", (), {})()
        postprocessor.postprocess = AsyncMock(return_value="纠错后的文本。")
        summary_service = type("FakeSummaryService", (), {})()
        summary_service.summarize = AsyncMock(return_value="")

        fetcher = ContentFetcher(bili, asr)
        fetcher.text_postprocessor = postprocessor
        fetcher.summary_service = summary_service

        with patch.object(fetcher, "_try_asr", AsyncMock(return_value="原始 asr 文本")):
            content = await fetcher.fetch_content("BV1test", cid=123, title="测试标题")

        self.assertEqual(content.content, "纠错后的文本。")
        self.assertEqual(content.source, ContentSource.ASR)
        postprocessor.postprocess.assert_awaited_once_with("原始 asr 文本", title="测试标题")
        from app.services.content_storage import ContentStorageManager

        bili = type("FakeBili", (), {})()
        asr = type("FakeASR", (), {})()
        postprocessor = type("FakePostprocessor", (), {})()
        postprocessor.postprocess = AsyncMock(return_value="纠错后的文本。")

        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as output_dir:
            summary_service = type("FakeSummaryService", (), {})()
            summary_service.summarize = AsyncMock(return_value="")
            fetcher = ContentFetcher(
                bili,
                asr,
                text_postprocessor=postprocessor,
                summary_service=summary_service,
                storage_manager=ContentStorageManager(
                    workspace_root=workspace_dir,
                    export_root=output_dir,
                    max_total_size_bytes=1024 * 1024,
                    retention_days=3,
                ),
            )

            with patch.object(fetcher, "_try_asr", AsyncMock(return_value="原始 asr 文本")):
                await fetcher.fetch_content("BV1test", cid=123, title="测试标题")

            work_dir = (
                Path(workspace_dir)
                / "bilibili"
                / datetime.now().strftime("%Y-%m-%d")
                / "测试标题"
            )
            self.assertEqual((work_dir / "asr_raw.txt").read_text(encoding="utf-8"), "原始 asr 文本")
            self.assertEqual((work_dir / "asr_corrected.txt").read_text(encoding="utf-8"), "纠错后的文本。")

    async def test_fetch_content_falls_back_to_raw_asr_text_when_postprocessor_fails(self):
        bili = type("FakeBili", (), {})()
        asr = type("FakeASR", (), {})()
        postprocessor = type("FakePostprocessor", (), {})()
        postprocessor.postprocess = AsyncMock(side_effect=RuntimeError("ollama boom"))
        summary_service = type("FakeSummaryService", (), {})()
        summary_service.summarize = AsyncMock(return_value="")

        fetcher = ContentFetcher(bili, asr)
        fetcher.text_postprocessor = postprocessor
        fetcher.summary_service = summary_service

        with patch.object(fetcher, "_try_asr", AsyncMock(return_value="原始 asr 文本")):
            content = await fetcher.fetch_content("BV1test", cid=123, title="测试标题")

        self.assertEqual(content.content, "原始 asr 文本")
        self.assertEqual(content.source, ContentSource.ASR)
        postprocessor.postprocess.assert_awaited_once_with("原始 asr 文本", title="测试标题")
        bili = type("FakeBili", (), {})()
        asr = type("FakeASR", (), {})()
        postprocessor = type("FakePostprocessor", (), {})()
        postprocessor.postprocess = AsyncMock(return_value="纠错后的文本。")
        summary_service = type("FakeSummaryService", (), {})()
        summary_service.summarize = AsyncMock(
            return_value="<!-- AI_SUMMARY_START -->\n```yaml\nsummary: |\n  重点总结\n```\n<!-- AI_SUMMARY_END -->"
        )

        fetcher = ContentFetcher(bili, asr)
        fetcher.text_postprocessor = postprocessor
        fetcher.summary_service = summary_service

        with patch.object(fetcher, "_try_asr", AsyncMock(return_value="原始 asr 文本")):
            content = await fetcher.fetch_content("BV1test", cid=123, title="测试标题")

        summary_service.summarize.assert_awaited_once_with("纠错后的文本。")
        self.assertIn("AI_SUMMARY_START", content.summary_block)

    async def test_fetch_content_strict_mode_raises_audio_download_error_with_details(self):
        bili = type("FakeBili", (), {})()
        asr = type("FakeASR", (), {})()

        bili.get_audio_url = AsyncMock(return_value="https://example.com/audio.m4s")
        bili.download_bvid_audio_to_file = AsyncMock(side_effect=RuntimeError("yt-dlp boom"))
        bili.download_audio_to_file = AsyncMock(side_effect=RuntimeError("direct boom"))

        fetcher = ContentFetcher(bili, asr)

        with patch.object(fetcher, "_probe_audio_url", AsyncMock(return_value=403)):
            with self.assertRaises(AudioDownloadError) as cm:
                await fetcher.fetch_content(
                    "BV1nb421J7QS",
                    cid=1653694932,
                    title="test title",
                    fail_on_audio_download_error=True,
                )

        self.assertIn("yt-dlp boom", str(cm.exception))
        self.assertIn("direct boom", str(cm.exception))

    async def test_fetch_content_strict_asr_mode_raises_when_transcription_fails(self):
        bili = type("FakeBili", (), {})()
        asr = type("FakeASR", (), {})()

        bili.get_audio_url = AsyncMock(return_value="https://example.com/audio.m4s")
        bili.download_bvid_audio_to_file = AsyncMock(return_value=True)
        bili.download_audio_to_file = AsyncMock(return_value=False)
        asr.transcribe_local_file = AsyncMock(return_value=None)

        fetcher = ContentFetcher(bili, asr)

        with tempfile.TemporaryDirectory() as tmpdir:
            original_join = os.path.join

            def fake_join(*parts: str) -> str:
                if parts == ("data", "asr_tmp"):
                    return tmpdir
                return original_join(*parts)

            with (
                patch("app.services.content_fetcher.os.path.join", side_effect=fake_join),
                patch.object(fetcher, "_probe_audio_url", AsyncMock(return_value=403)),
                self.assertRaises(ASRProcessingError) as cm,
            ):
                await fetcher.fetch_content(
                    "BV1nb421J7QS",
                    cid=1653694932,
                    title="test title",
                    fail_on_audio_download_error=True,
                    fail_on_asr_error=True,
                )

        self.assertIn("ASR", str(cm.exception))

    async def test_try_asr_with_local_audio_prefers_shared_yt_dlp_downloader(self):
        bili = type("FakeBili", (), {})()
        asr = type("FakeASR", (), {})()

        async def write_audio(_bvid: str, _cid: int, file_path: str, **_kwargs) -> bool:
            Path(file_path).write_bytes(b"x" * 2048)
            return True

        bili.download_bvid_audio_to_file = AsyncMock(side_effect=write_audio)
        bili.download_audio_to_file = AsyncMock(return_value=False)
        asr.transcribe_local_file = AsyncMock(return_value="a" * 80)

        fetcher = ContentFetcher(bili, asr)

        with tempfile.TemporaryDirectory() as tmpdir:
            original_join = os.path.join

            def fake_join(*parts: str) -> str:
                if parts == ("data", "asr_tmp"):
                    return tmpdir
                return original_join(*parts)

            with patch("app.services.content_fetcher.os.path.join", side_effect=fake_join):
                text = await fetcher._try_asr_with_local_audio(
                    "BV1r2RWB6EQN", 123456, "https://example.com/audio.m4s"
                )

        self.assertEqual(text, "a" * 80)
        bili.download_bvid_audio_to_file.assert_awaited_once()
        bili.download_audio_to_file.assert_not_awaited()
        asr.transcribe_local_file.assert_awaited_once()

    async def test_try_asr_with_local_audio_uses_title_in_temp_filename(self):
        bili = type("FakeBili", (), {})()
        asr = type("FakeASR", (), {})()
        seen_paths = []

        async def write_audio(_bvid: str, _cid: int, file_path: str, **_kwargs) -> bool:
            seen_paths.append(file_path)
            Path(file_path).write_bytes(b"x" * 2048)
            return True

        bili.download_bvid_audio_to_file = AsyncMock(side_effect=write_audio)
        bili.download_audio_to_file = AsyncMock(return_value=False)
        asr.transcribe_local_file = AsyncMock(return_value="a" * 80)

        fetcher = ContentFetcher(bili, asr)

        with tempfile.TemporaryDirectory() as tmpdir:
            original_join = os.path.join

            def fake_join(*parts: str) -> str:
                if parts == ("data", "asr_tmp"):
                    return tmpdir
                return original_join(*parts)

            with patch("app.services.content_fetcher.os.path.join", side_effect=fake_join):
                await fetcher._try_asr_with_local_audio(
                    "BV1r2RWB6EQN", 123456, "https://example.com/audio.m4s", title="可读 标题"
                )

        self.assertEqual(len(seen_paths), 1)
        self.assertIn("可读_标题", Path(seen_paths[0]).name)

    async def test_try_asr_with_local_audio_falls_back_to_direct_download_when_yt_dlp_fails(self):
        bili = type("FakeBili", (), {})()
        asr = type("FakeASR", (), {})()

        bili.download_bvid_audio_to_file = AsyncMock(side_effect=RuntimeError("yt-dlp failed"))

        async def write_audio(_audio_url: str, file_path: str, **_kwargs) -> bool:
            Path(file_path).write_bytes(b"y" * 2048)
            return True

        bili.download_audio_to_file = AsyncMock(side_effect=write_audio)
        asr.transcribe_local_file = AsyncMock(return_value="b" * 80)

        fetcher = ContentFetcher(bili, asr)

        with tempfile.TemporaryDirectory() as tmpdir:
            original_join = os.path.join

            def fake_join(*parts: str) -> str:
                if parts == ("data", "asr_tmp"):
                    return tmpdir
                return original_join(*parts)

            with patch("app.services.content_fetcher.os.path.join", side_effect=fake_join):
                text = await fetcher._try_asr_with_local_audio(
                    "BV1r2RWB6EQN", 123456, "https://example.com/audio.m4s"
                )

        self.assertEqual(text, "b" * 80)
        bili.download_bvid_audio_to_file.assert_awaited_once()
        bili.download_audio_to_file.assert_awaited_once()
        asr.transcribe_local_file.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
