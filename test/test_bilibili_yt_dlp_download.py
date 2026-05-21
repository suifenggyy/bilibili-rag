import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.bilibili import BilibiliService


class BilibiliYtDlpDownloadTests(unittest.IsolatedAsyncioTestCase):
    def test_download_audio_with_yt_dlp_uses_cookiefile_not_cookie_header(self):
        captured = {}

        class FakeYDL:
            def __init__(self, opts):
                captured["opts"] = opts

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def download(self, _urls):
                outtmpl = captured["opts"]["outtmpl"]
                Path(outtmpl.replace(".%(ext)s", ".mp3")).write_bytes(b"x" * 2048)

        fake_module = types.SimpleNamespace(
            version=types.SimpleNamespace(__version__="test-version"),
            YoutubeDL=FakeYDL,
        )

        service = BilibiliService(sessdata="sess", bili_jct="csrf", dedeuserid="123")

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = f"{tmpdir}/audio.mp3"
            with patch.dict(sys.modules, {"yt_dlp": fake_module}):
                ok = service._download_audio_with_yt_dlp_sync(
                    "https://www.bilibili.com/video/BV1r2RWB6EQN/",
                    output_path,
                )

        self.assertTrue(ok)
        self.assertIn("cookiefile", captured["opts"])
        self.assertNotIn("Cookie", captured["opts"]["http_headers"])

    def test_download_audio_with_yt_dlp_logs_success_filename_and_size(self):
        captured = {}

        class FakeYDL:
            def __init__(self, opts):
                captured["opts"] = opts

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def download(self, _urls):
                outtmpl = captured["opts"]["outtmpl"]
                Path(outtmpl.replace(".%(ext)s", ".mp3")).write_bytes(b"x" * 2048)

        fake_module = types.SimpleNamespace(
            version=types.SimpleNamespace(__version__="test-version"),
            YoutubeDL=FakeYDL,
        )

        service = BilibiliService()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = f"{tmpdir}/audio.mp3"
            with (
                patch.dict(sys.modules, {"yt_dlp": fake_module}),
                patch("app.services.bilibili.logger.info") as log_info,
            ):
                ok = service._download_audio_with_yt_dlp_sync(
                    "https://www.bilibili.com/video/BV1r2RWB6EQN/",
                    output_path,
                )

        self.assertTrue(ok)
        logged = "\n".join(str(call.args[0]) for call in log_info.call_args_list)
        self.assertIn("yt-dlp(test-version) 下载音频成功", logged)
        self.assertIn("audio.mp3", logged)
        self.assertIn("2048", logged)

    async def test_download_bvid_audio_to_file_returns_false_when_cid_cannot_map_to_page(self):
        service = BilibiliService()
        service.get_video_info = AsyncMock(
            return_value={
                "cid": 111,
                "pages": [
                    {"cid": 111},
                    {"cid": 222},
                ],
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = f"{tmpdir}/audio.mp3"
            with patch.object(
                service,
                "_download_audio_with_yt_dlp_sync",
                return_value=True,
            ) as downloader:
                ok = await service.download_bvid_audio_to_file("BV1r2RWB6EQN", 999, output_path)

        self.assertFalse(ok)
        downloader.assert_not_called()
        await service.close()


if __name__ == "__main__":
    unittest.main()
