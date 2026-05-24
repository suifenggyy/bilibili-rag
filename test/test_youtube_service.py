"""
Tests for YouTubeService (yt-dlp wrapper).
"""
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class YouTubeServiceNormalizeTests(unittest.TestCase):

    def _svc(self):
        from app.services.youtube import YouTubeService
        return YouTubeService()

    def test_normalize_info_extracts_standard_fields(self):
        svc = self._svc()
        raw = {
            "id": "dQw4w9WgXcQ",
            "title": "Never Gonna Give You Up",
            "uploader": "Rick Astley",
            "duration": 213,
            "description": "Great song",
            "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hq720.jpg",
            "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        }
        info = svc._normalize_info(raw)
        self.assertEqual(info["video_id"], "dQw4w9WgXcQ")
        self.assertEqual(info["title"], "Never Gonna Give You Up")
        self.assertEqual(info["channel"], "Rick Astley")
        self.assertEqual(info["duration"], 213)
        self.assertEqual(info["url"], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_normalize_info_handles_missing_fields(self):
        svc = self._svc()
        raw = {"id": "abc"}
        info = svc._normalize_info(raw)
        self.assertEqual(info["video_id"], "abc")
        self.assertEqual(info["title"], "abc")
        self.assertEqual(info["channel"], "")
        self.assertIsNone(info["duration"])  # yt-dlp returns None when missing

    def test_normalize_info_falls_back_uploader_to_channel(self):
        svc = self._svc()
        raw = {
            "id": "xyz",
            "title": "Test Video",
            "channel": "My Channel",
        }
        info = svc._normalize_info(raw)
        self.assertEqual(info["channel"], "My Channel")

    def test_get_private_url_liked(self):
        from app.services.youtube import YouTubeService
        svc = YouTubeService()
        self.assertEqual(svc.get_private_url("liked"), ":ytfav")

    def test_get_private_url_watch_later(self):
        from app.services.youtube import YouTubeService
        svc = YouTubeService()
        self.assertEqual(svc.get_private_url("watch_later"), ":ytwatchlater")

    def test_get_private_url_unknown_returns_none(self):
        from app.services.youtube import YouTubeService
        svc = YouTubeService()
        self.assertIsNone(svc.get_private_url("not_a_list"))


class YouTubeServiceBuildOptsTests(unittest.TestCase):

    def test_build_ydl_opts_extract_only_uses_flat(self):
        from app.services.youtube import YouTubeService
        svc = YouTubeService()
        opts = svc._build_ydl_opts(download=False)
        self.assertTrue(opts["extract_flat"])
        self.assertNotIn("outtmpl", opts)

    def test_build_ydl_opts_download_includes_format_and_output(self):
        from app.services.youtube import YouTubeService
        svc = YouTubeService()
        opts = svc._build_ydl_opts(download=True, output_path="/tmp/audio")
        self.assertFalse(opts["extract_flat"])
        self.assertEqual(opts["format"], "bestaudio/best")
        self.assertEqual(opts["outtmpl"], "/tmp/audio")

    def test_build_ydl_opts_includes_cookiefile_when_set(self):
        from app.services.youtube import YouTubeService
        import tempfile, os

        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"# Netscape HTTP Cookie File\n")
            tmp_path = f.name

        try:
            svc = YouTubeService(cookie_file=tmp_path)
            opts = svc._build_ydl_opts(download=False)
            self.assertEqual(opts["cookiefile"], tmp_path)
        finally:
            os.unlink(tmp_path)

    def test_build_ydl_opts_no_cookiefile_when_file_missing(self):
        from app.services.youtube import YouTubeService
        svc = YouTubeService(cookie_file="/nonexistent/path.txt")
        opts = svc._build_ydl_opts(download=False)
        self.assertNotIn("cookiefile", opts)


class YouTubeServiceFindDownloadedFileTests(unittest.TestCase):

    def test_find_downloaded_file_returns_exact_match(self):
        from app.services.youtube import YouTubeService
        import tempfile, os

        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "audio"
            mp3_path = target.with_suffix(".mp3")
            mp3_path.write_bytes(b"0" * 100)

            result = YouTubeService._find_downloaded_file(target)
            self.assertEqual(result, mp3_path)

    def test_find_downloaded_file_returns_none_when_no_file(self):
        from app.services.youtube import YouTubeService

        result = YouTubeService._find_downloaded_file(Path("/nonexistent/audio"))
        self.assertIsNone(result)


class YouTubeServiceExtractInfoTests(unittest.IsolatedAsyncioTestCase):

    async def test_extract_video_info_returns_none_on_yt_dlp_error(self):
        from app.services.youtube import YouTubeService

        svc = YouTubeService()
        with patch.object(svc, "_extract_info_sync", side_effect=Exception("yt-dlp error")):
            result = await svc.extract_video_info("https://www.youtube.com/watch?v=bad")

        self.assertIsNone(result)

    async def test_extract_playlist_videos_returns_empty_on_error(self):
        from app.services.youtube import YouTubeService

        svc = YouTubeService()
        with patch.object(
            svc, "_extract_playlist_sync", side_effect=Exception("network error")
        ):
            result = await svc.extract_playlist_videos("https://www.youtube.com/playlist?list=bad")

        self.assertEqual(result, [])

    async def test_extract_playlist_videos_normalizes_entries(self):
        from app.services.youtube import YouTubeService

        fake_playlist = {
            "entries": [
                {"id": "v1", "title": "Video 1", "uploader": "Chan", "duration": 100,
                 "webpage_url": "https://youtube.com/watch?v=v1"},
                None,  # yt-dlp sometimes returns None entries
                {"id": "v2", "title": "Video 2", "duration": 200,
                 "webpage_url": "https://youtube.com/watch?v=v2"},
            ]
        }

        svc = YouTubeService()
        with patch.object(svc, "_extract_playlist_sync", return_value=fake_playlist):
            results = await svc.extract_playlist_videos(":ytfav")

        # None entry should be skipped
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["video_id"], "v1")
        self.assertEqual(results[1]["video_id"], "v2")


if __name__ == "__main__":
    unittest.main()
