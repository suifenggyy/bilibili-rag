"""Tests for douyin_bridge path setup and DouyinService backward compatibility."""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]


class TestDouyinBridgePathSetup(unittest.TestCase):
    def test_submodule_root_appended_to_syspath(self):
        """douyin_bridge should append (not prepend) the submodule root to sys.path."""
        from app.services import douyin_bridge  # noqa: F401

        submodule_root = str(ROOT / "douyin_tiktok_download_api" / "src")
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
        if not (submodule_root / "src" / "dtk").exists():
            self.skipTest("Submodule not initialized")

        from app.services.douyin_bridge import is_available

        self.assertTrue(is_available())


class TestDouyinCollectionRequest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        src = str(ROOT / "douyin_tiktok_download_api" / "src")
        if src not in sys.path:
            sys.path.append(src)
        from dtk.transport.base import RawResponse
        from app.config import settings

        self.settings_patch = patch.multiple(
            settings,
            douyin_api_base_url="https://www-hj.douyin.com",
            douyin_user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Safari/537.36"
            ),
        )
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)
        self.transport = MagicMock()
        self.transport.request = AsyncMock(return_value=RawResponse(
            status=200, body=b'{"status_code":0,"aweme_list":[{"aweme_id":"123"}]}',
        ))
        self.transport.close = AsyncMock()
        self.transport_patch = patch(
            "dtk.transport.wreq_transport.WreqTransport", return_value=self.transport
        )
        self.transport_patch.start()
        self.addCleanup(self.transport_patch.stop)

    async def test_collection_uses_current_signature_and_post_endpoint(self):
        from app.services import douyin_bridge

        cookie = (
            "sessionid=test; msToken=current-token; UIFID=visitor; s_v_web_id=verify_test; "
            "dy_swidth=1728; dy_sheight=1117; device_web_cpu_core=10; device_web_memory_size=16"
        )
        result = await douyin_bridge.fetch_collection_videos(cookie, cursor=10, count=5)

        self.assertEqual(result["aweme_list"], [{"aweme_id": "123"}])
        identity, spec = self.transport.request.call_args.args
        url = urlsplit(spec.url)
        self.assertEqual(url.netloc, "www-hj.douyin.com")
        self.assertEqual(url.path, "/aweme/v1/web/aweme/listcollection/")
        query = parse_qs(url.query)
        self.assertTrue(query["a_bogus"][0])
        self.assertTrue(query["x-secsdk-web-signature"][0])
        self.assertEqual(query["uifid"], ["visitor"])
        self.assertEqual(query["verifyFp"], ["verify_test"])
        self.assertNotIn("X-Bogus", query)
        self.assertEqual(query["msToken"], ["current-token"])
        self.assertEqual(query["browser_platform"], ["MacIntel"])
        self.assertEqual(query["browser_version"], ["158.0.0.0"])
        self.assertEqual(query["screen_width"], ["1728"])
        self.assertEqual(query["screen_height"], ["1117"])
        self.assertEqual(identity.fingerprint.screen, "1728x1117")
        self.assertEqual(identity.fingerprint.hardware_concurrency, 10)
        self.assertEqual(spec.method, "POST")
        self.assertEqual(spec.body, b"count=5&cursor=10")
        self.assertIn("application/x-www-form-urlencoded", spec.headers["Content-Type"])
        self.assertEqual(identity.cookies["sessionid"], "test")
        self.transport.close.assert_awaited_once()

    async def test_creator_request_forwards_session_cookie(self):
        from app.services.douyin import DouyinService

        await DouyinService(cookie="sessionid=creator").get_creator_videos_page(
            "sec_user_123", max_cursor=25, count=3
        )
        identity, spec = self.transport.request.call_args.args
        self.assertEqual(identity.cookies["sessionid"], "creator")
        self.assertEqual(spec.method, "GET")
        query = parse_qs(urlsplit(spec.url).query)
        self.assertEqual(query["sec_user_id"], ["sec_user_123"])
        self.assertEqual(query["max_cursor"], ["25"])
        self.assertEqual(query["count"], ["3"])

    async def test_http_forbidden_is_explicit_and_does_not_leak_tokens(self):
        from app.services import douyin_bridge
        from dtk.transport.base import RawResponse

        self.transport.request.return_value = RawResponse(status=403, body=b"Forbidden")
        with self.assertRaisesRegex(RuntimeError, "HTTP 403") as error:
            await douyin_bridge.fetch_collection_videos("sessionid=secret-value")
        self.assertNotIn("secret-value", str(error.exception))
        self.transport.close.assert_awaited_once()

    async def test_comments_use_v5_bridge(self):
        from app.services.comments import fetch_douyin_comments
        from dtk.transport.base import RawResponse

        self.transport.request.return_value = RawResponse(
            status=200,
            body=b'{"status_code":0,"comments":[{"text":"hello","digg_count":4,"user":{"nickname":"Author"}}]}',
        )
        result = await fetch_douyin_comments("123", "sessionid=comments", limit=2)
        self.assertEqual(result, [{"author": "Author", "content": "hello", "likes": 4}])
        identity, spec = self.transport.request.call_args.args
        self.assertEqual(identity.cookies["sessionid"], "comments")
        self.assertEqual(urlsplit(spec.url).path, "/aweme/v1/web/comment/list/")

    async def test_network_failure_closes_transport(self):
        from app.services import douyin_bridge

        self.transport.request.side_effect = RuntimeError("network unavailable")
        with self.assertRaisesRegex(RuntimeError, "network unavailable"):
            await douyin_bridge.fetch_collection_videos("sessionid=test")
        self.transport.close.assert_awaited_once()

    async def test_transport_failure_does_not_expose_signed_url_in_traceback(self):
        import traceback
        from app.services import douyin_bridge
        from dtk.transport.base import TransportFailure

        signed_url = "https://www-hj.douyin.com/?msToken=secret-token&a_bogus=secret-signature"
        self.transport.request.side_effect = TransportFailure(
            f"POST {signed_url} failed", identity_id="test",
            url=signed_url, elapsed_ms=10,
        )
        with self.assertRaisesRegex(RuntimeError, "network") as error:
            await douyin_bridge.fetch_collection_videos("sessionid=test")
        rendered = "".join(traceback.format_exception(error.exception))
        self.assertNotIn("secret-token", rendered)
        self.assertNotIn("secret-signature", rendered)
        self.transport.close.assert_awaited_once()

    async def test_untrusted_api_host_is_rejected_before_cookie_is_sent(self):
        from app.config import settings
        from app.services import douyin_bridge

        with patch.object(settings, "douyin_api_base_url", "https://example.com"):
            with self.assertRaisesRegex(ValueError, "DOUYIN_API_BASE_URL"):
                await douyin_bridge.fetch_collection_videos("sessionid=test")
        self.transport.request.assert_not_awaited()

    async def test_cookie_header_prefix_and_equals_in_values_are_preserved(self):
        from app.services import douyin_bridge

        await douyin_bridge.fetch_collection_videos("Cookie: sessionid=test==; msToken=token==")
        identity, spec = self.transport.request.call_args.args
        self.assertEqual(identity.cookies["sessionid"], "test==")
        self.assertEqual(parse_qs(urlsplit(spec.url).query)["msToken"], ["token=="])

    async def test_collection_failure_is_not_reported_as_an_empty_export(self):
        from app.services.douyin import DouyinService

        service = DouyinService(cookie="test")
        with patch.object(
            service, "get_collection_videos_page", new=AsyncMock(side_effect=RuntimeError("HTTP 403"))
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                await service.get_all_collection_videos()

    async def test_creator_failure_is_not_reported_as_an_empty_export(self):
        from app.services.douyin import DouyinService

        service = DouyinService(cookie="test")
        with patch.object(
            service, "get_creator_videos_page", new=AsyncMock(side_effect=RuntimeError("HTTP 403"))
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                await service.get_all_creator_videos("sec_user")

    async def test_cookie_validation_rejects_api_error(self):
        from app.services.douyin import DouyinService
        from app.services import douyin_bridge

        with patch.object(
            douyin_bridge, "fetch_collection_videos",
            new=AsyncMock(return_value={"status_code": 8, "status_msg": "denied"}),
        ):
            self.assertFalse(await DouyinService(cookie="test").check_cookie_valid())

    async def test_collection_missing_response_is_not_an_empty_collection(self):
        from app.services.douyin import DouyinService
        from app.services import douyin_bridge

        with patch.object(
            douyin_bridge, "fetch_collection_videos", new=AsyncMock(return_value={})
        ):
            with self.assertRaisesRegex(RuntimeError, "收藏夹"):
                await DouyinService(cookie="test").get_collection_videos_page()

    async def test_valid_empty_collection_is_accepted(self):
        from app.services.douyin import DouyinService
        from app.services import douyin_bridge

        with patch.object(
            douyin_bridge, "fetch_collection_videos",
            new=AsyncMock(return_value={"status_code": 0, "aweme_list": []}),
        ):
            self.assertTrue(await DouyinService(cookie="test").check_cookie_valid())


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
