"""Tests for douyin_bridge path setup and DouyinService backward compatibility."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TestDouyinBridgePathSetup(unittest.TestCase):
    def test_submodule_root_appended_to_syspath(self):
        """douyin_bridge should append (not prepend) the submodule root to sys.path."""
        from app.services import douyin_bridge  # noqa: F401

        submodule_root = str(ROOT / "douyin_tiktok_download_api")
        self.assertIn(submodule_root, sys.path)

        # Project root should appear before the submodule root in sys.path
        project_root = str(ROOT)
        if project_root in sys.path:
            self.assertLess(
                sys.path.index(project_root),
                sys.path.index(submodule_root),
                "Project root must appear before submodule root in sys.path",
            )

    def test_our_app_package_not_shadowed(self):
        """Importing `app` should return our project's app, not the submodule's."""
        import app

        # Our app package is under ROOT/app/__init__.py
        self.assertIn(str(ROOT), str(Path(app.__file__).parents[1]))

    def test_is_available_returns_bool(self):
        from app.services.douyin_bridge import is_available

        result = is_available()
        self.assertIsInstance(result, bool)

    def test_is_available_true_when_submodule_present(self):
        """is_available() should return True when submodule is initialized."""
        submodule_root = ROOT / "douyin_tiktok_download_api"
        if not (submodule_root / "crawlers").exists():
            self.skipTest("Submodule not initialized")

        from app.services.douyin_bridge import is_available

        self.assertTrue(is_available())


class TestDouyinServiceBackwardCompat(unittest.TestCase):
    def test_init_with_evil0ctal_url(self):
        """DouyinService should accept evil0ctal_url without raising."""
        from app.services.douyin import DouyinService

        svc = DouyinService(cookie="test", evil0ctal_url="http://localhost:2333")
        self.assertEqual(svc.cookie, "test")

    def test_init_without_evil0ctal_url(self):
        from app.services.douyin import DouyinService

        svc = DouyinService(cookie="test")
        self.assertEqual(svc.cookie, "test")

    def test_init_with_custom_url_logs_warning(self):
        """Passing a non-default evil0ctal_url should not crash (logs warning only)."""
        from app.services.douyin import DouyinService

        svc = DouyinService(cookie="test", evil0ctal_url="http://custom:9999")
        self.assertEqual(svc.cookie, "test")

    def test_parse_video_info_static(self):
        from app.services.douyin import DouyinService

        raw = {
            "aweme_id": "123",
            "desc": "Test video",
            "author": {"nickname": "Author", "uid": "uid1"},
            "create_time": 1000000,
            "video": {
                "duration": 30000,
                "cover": {"url_list": ["http://cover.jpg"]},
                "play_addr": {"url_list": ["http://play.mp4"]},
            },
        }
        info = DouyinService.parse_video_info(raw)
        self.assertEqual(info["aweme_id"], "123")
        self.assertEqual(info["title"], "Test video")
        self.assertEqual(info["author"], "Author")
        self.assertEqual(info["play_urls"], ["http://play.mp4"])
        self.assertEqual(info["cover_url"], "http://cover.jpg")
        self.assertEqual(info["share_url"], "https://www.douyin.com/video/123")

    def test_parse_video_info_fallback_fields(self):
        """parse_video_info should handle missing fields gracefully."""
        from app.services.douyin import DouyinService

        info = DouyinService.parse_video_info({})
        self.assertEqual(info["aweme_id"], "")
        self.assertEqual(info["title"], "")
        self.assertEqual(info["play_urls"], [])
        self.assertEqual(info["cover_url"], "")

    def test_check_evil0ctal_available_returns_bool(self):
        import asyncio
        from app.services.douyin import DouyinService

        svc = DouyinService(cookie="dummy")
        result = asyncio.run(svc.check_evil0ctal_available())
        self.assertIsInstance(result, bool)


if __name__ == "__main__":
    unittest.main()
