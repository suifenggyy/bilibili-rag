import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.douyin_fetcher import DouyinContentFetcher


class DouyinContentFetcherPostprocessTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_content_uses_corrected_text_when_postprocessor_succeeds(self):
        asr = type("FakeASR", (), {})()
        postprocessor = type("FakePostprocessor", (), {})()
        postprocessor.postprocess = AsyncMock(return_value="纠错后的抖音文本。")
        summary_service = type("FakeSummaryService", (), {})()
        summary_service.summarize = AsyncMock(return_value="")

        fetcher = DouyinContentFetcher(asr, tmp_dir="data/douyin_tmp_test")
        fetcher.text_postprocessor = postprocessor
        fetcher.summary_service = summary_service

        video_info = {
            "aweme_id": "123456",
            "title": "测试抖音",
            "play_urls": ["https://example.com/video.mp4"],
        }

        with (
            patch.object(fetcher, "_download_video", AsyncMock(return_value=True)),
            patch.object(fetcher, "_extract_and_transcribe", AsyncMock(return_value="原始抖音 asr 文本")),
        ):
            content = await fetcher.fetch_content(video_info)

        self.assertEqual(content.content, "纠错后的抖音文本。")
        self.assertEqual(content.content_source, "asr")
        postprocessor.postprocess.assert_awaited_once_with("原始抖音 asr 文本")

    async def test_fetch_content_keeps_downloaded_video_and_asr_files_in_workspace(self):
        from app.services.content_storage import ContentStorageManager

        asr = type("FakeASR", (), {})()
        postprocessor = type("FakePostprocessor", (), {})()
        postprocessor.postprocess = AsyncMock(return_value="纠错后的抖音文本。")

        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as output_dir:
            summary_service = type("FakeSummaryService", (), {})()
            summary_service.summarize = AsyncMock(return_value="")
            fetcher = DouyinContentFetcher(
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

            video_info = {
                "aweme_id": "123456",
                "title": "测试抖音",
                "play_urls": ["https://example.com/video.mp4"],
            }

            async def fake_download(_url: str, dest_path: str) -> bool:
                Path(dest_path).write_bytes(b"video-bytes")
                return True

            with (
                patch.object(fetcher, "_download_video", AsyncMock(side_effect=fake_download)),
                patch.object(fetcher, "_extract_and_transcribe", AsyncMock(return_value="原始抖音 asr 文本")),
            ):
                await fetcher.fetch_content(video_info)

            work_dir = (
                Path(workspace_dir)
                / "douyin"
                / datetime.now().strftime("%Y-%m-%d")
                / "测试抖音"
            )
            self.assertTrue((work_dir / "video.mp4").exists())
            self.assertEqual((work_dir / "asr_raw.txt").read_text(encoding="utf-8"), "原始抖音 asr 文本")
            self.assertEqual((work_dir / "asr_corrected.txt").read_text(encoding="utf-8"), "纠错后的抖音文本。")

    async def test_fetch_content_generates_summary_block_from_corrected_text(self):
        asr = type("FakeASR", (), {})()
        postprocessor = type("FakePostprocessor", (), {})()
        postprocessor.postprocess = AsyncMock(return_value="纠错后的抖音文本。")
        summary_service = type("FakeSummaryService", (), {})()
        summary_service.summarize = AsyncMock(
            return_value="<!-- AI_SUMMARY_START -->\n```yaml\ntags:\n  - 抖音\n```\n<!-- AI_SUMMARY_END -->"
        )

        fetcher = DouyinContentFetcher(asr, tmp_dir="data/douyin_tmp_test")
        fetcher.text_postprocessor = postprocessor
        fetcher.summary_service = summary_service

        video_info = {
            "aweme_id": "123456",
            "title": "测试抖音",
            "play_urls": ["https://example.com/video.mp4"],
        }

        with (
            patch.object(fetcher, "_download_video", AsyncMock(return_value=True)),
            patch.object(fetcher, "_extract_and_transcribe", AsyncMock(return_value="原始抖音 asr 文本")),
        ):
            content = await fetcher.fetch_content(video_info)

        summary_service.summarize.assert_awaited_once_with("纠错后的抖音文本。")
        self.assertIn("AI_SUMMARY_START", content.summary_block)


if __name__ == "__main__":
    unittest.main()
