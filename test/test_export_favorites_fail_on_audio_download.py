import asyncio
import tempfile
import unittest
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.content_fetcher import ASRProcessingError, AudioDownloadError
from scripts import export_favorites_to_md
from scripts.export_favorites_to_md import export_folder


class ExportFavoritesFailOnAudioDownloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_folder_skips_markdown_when_audio_download_fails(self):
        bili = type("FakeBili", (), {})()
        bili.get_all_favorite_videos = AsyncMock(
            return_value=[
                {
                    "bvid": "BV1nb421J7QS",
                    "title": "失败视频",
                    "ugc": {"first_cid": 1653694932},
                }
            ]
        )
        fetcher = type("FakeFetcher", (), {})()
        fetcher.fetch_content = AsyncMock(side_effect=AudioDownloadError("音频下载失败"))
        folder = {"id": 1, "title": "收藏夹", "media_count": 1}

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("scripts.export_favorites_to_md.asyncio.sleep", AsyncMock()):
                success, failed = await export_folder(
                    bili,
                    None,
                    fetcher,
                    folder,
                    Path(tmpdir),
                )
            files = list(Path(tmpdir).rglob("*.md"))

        self.assertEqual(success, 0)
        self.assertEqual(failed, 1)
        self.assertEqual(files, [])

    async def test_export_folder_skips_markdown_when_asr_processing_fails(self):
        bili = type("FakeBili", (), {})()
        bili.get_all_favorite_videos = AsyncMock(
            return_value=[
                {
                    "bvid": "BV1nb421J7QS",
                    "title": "ASR失败视频",
                    "ugc": {"first_cid": 1653694932},
                }
            ]
        )
        fetcher = type("FakeFetcher", (), {})()
        fetcher.fetch_content = AsyncMock(side_effect=ASRProcessingError("ASR失败"))
        folder = {"id": 1, "title": "收藏夹", "media_count": 1}

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("scripts.export_favorites_to_md.asyncio.sleep", AsyncMock()):
                success, failed = await export_folder(
                    bili,
                    None,
                    fetcher,
                    folder,
                    Path(tmpdir),
                )
            files = list(Path(tmpdir).rglob("*.md"))

        self.assertEqual(success, 0)
        self.assertEqual(failed, 1)
        self.assertEqual(files, [])


class ExportFavoritesEnvDefaultsTests(unittest.TestCase):
    def test_export_folder_writes_markdown_directly_into_date_directory(self):
        bili = type("FakeBili", (), {})()
        bili.get_all_favorite_videos = AsyncMock(
            return_value=[
                {
                    "bvid": "BV1ok411",
                    "title": "导出标题",
                    "ugc": {"first_cid": 1001},
                }
            ]
        )
        fetcher = type("FakeFetcher", (), {})()
        fetcher.fetch_content = AsyncMock(
            return_value=type(
                "VideoContent",
                (),
                {"content": "正文", "source": type("Source", (), {"value": "asr"})()},
            )()
        )
        folder = {"id": 1, "title": "收藏夹", "media_count": 1}

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "bilibili" / datetime.now().strftime("%Y-%m-%d")
            output_dir.mkdir(parents=True, exist_ok=True)
            with patch("scripts.export_favorites_to_md.asyncio.sleep", AsyncMock()):
                success, failed = asyncio.run(
                    export_folder(
                        bili,
                        None,
                        fetcher,
                        folder,
                        output_dir,
                    )
                )

            expected_file = output_dir / "导出标题_BV1ok411.md"
            self.assertEqual(success, 1)
            self.assertEqual(failed, 0)
            self.assertTrue(expected_file.exists())
            self.assertFalse((output_dir / "收藏夹").exists())

    def test_parser_default_backend_is_ollama_without_env_override(self):
        with patch.dict(os.environ, {"ASR_BACKEND": "dashscope"}, clear=False):
            parser = export_favorites_to_md.build_parser()
            args = parser.parse_args([])

        self.assertEqual(args.asr_backend, "ollama")


if __name__ == "__main__":
    unittest.main()
