"""
Tests for DouyinAuthService (QR code login).
"""
import base64
import io
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class DouyinAuthServiceQRCodeTests(unittest.IsolatedAsyncioTestCase):

    async def test_generate_qrcode_returns_token_and_base64_image(self):
        from app.services.douyin_auth import DouyinAuthService

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.text = '{"error_code":0}'
        mock_response.json.return_value = {
            "error_code": 0,
            "data": {
                "qrcode_index_url": "https://sso.douyin.com/qr/abc123",
                "token": "test_token_xyz",
            },
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        with patch(
            "app.services.douyin_auth.httpx.AsyncClient",
            return_value=mock_client,
        ):
            svc = DouyinAuthService()
            result = await svc.generate_qrcode()

        self.assertEqual(result["token"], "test_token_xyz")
        self.assertEqual(result["qrcode_url"], "https://sso.douyin.com/qr/abc123")
        self.assertTrue(result["qrcode_image_base64"].startswith("data:image/png;base64,"))

    async def test_generate_qrcode_raises_on_api_error(self):
        from app.services.douyin_auth import DouyinAuthService

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.text = '{"error_code":1001}'
        mock_response.json.return_value = {
            "error_code": 1001,
            "description": "频率限制",
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=MagicMock(cookies=MagicMock(get=lambda k, d=None: "fake_ttwid")))
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        with patch(
            "app.services.douyin_auth.httpx.AsyncClient",
            return_value=mock_client,
        ):
            svc = DouyinAuthService()
            with self.assertRaises(RuntimeError) as ctx:
                await svc.generate_qrcode()

        self.assertIn("error_code=1001", str(ctx.exception))

    async def test_poll_qrcode_status_waiting(self):
        from app.services.douyin_auth import DouyinAuthService

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.cookies = MagicMock(items=lambda: [])
        mock_response.json.return_value = {
            "error_code": 0,
            "data": {"status": 1},
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        with patch(
            "app.services.douyin_auth.httpx.AsyncClient",
            return_value=mock_client,
        ):
            svc = DouyinAuthService()
            result = await svc.poll_qrcode_status("tok123")

        self.assertEqual(result["status"], "waiting")
        self.assertIsNone(result["cookie_str"])

    async def test_poll_qrcode_status_scanned(self):
        from app.services.douyin_auth import DouyinAuthService

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.cookies = MagicMock(items=lambda: [])
        mock_response.json.return_value = {
            "error_code": 0,
            "data": {"status": 2},
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        with patch(
            "app.services.douyin_auth.httpx.AsyncClient",
            return_value=mock_client,
        ):
            svc = DouyinAuthService()
            result = await svc.poll_qrcode_status("tok123")

        self.assertEqual(result["status"], "scanned")

    async def test_poll_qrcode_status_expired(self):
        from app.services.douyin_auth import DouyinAuthService

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.cookies = MagicMock(items=lambda: [])
        mock_response.json.return_value = {
            "error_code": 0,
            "data": {"status": 4},
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()

        with patch(
            "app.services.douyin_auth.httpx.AsyncClient",
            return_value=mock_client,
        ):
            svc = DouyinAuthService()
            result = await svc.poll_qrcode_status("tok123")

        self.assertEqual(result["status"], "expired")

    async def test_poll_qrcode_status_confirmed_returns_cookie_str(self):
        import httpx
        from app.services.douyin_auth import DouyinAuthService

        # First GET response (poll)
        poll_response = MagicMock()
        poll_response.status_code = 200
        poll_response.cookies = httpx.Cookies({"sso_token": "abc"})
        poll_response.json.return_value = {
            "error_code": 0,
            "data": {
                "status": 3,
                "redirect_url": "https://www.douyin.com/login/callback?code=x",
            },
        }

        # Second GET response (follow redirect)
        redirect_response = MagicMock()
        redirect_response.cookies = httpx.Cookies({"sessionid": "sess123", "odin_tt": "odin_val"})
        redirect_response.headers = MagicMock(get_list=lambda _: [])

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=[poll_response, redirect_response]
        )
        mock_client.aclose = AsyncMock()

        with patch(
            "app.services.douyin_auth.httpx.AsyncClient",
            return_value=mock_client,
        ):
            svc = DouyinAuthService()
            result = await svc.poll_qrcode_status("tok_confirmed")

        self.assertEqual(result["status"], "confirmed")
        self.assertIsNotNone(result["cookie_str"])
        # Cookie string should contain all gathered cookies
        self.assertIn("sessionid=sess123", result["cookie_str"])
        self.assertIn("odin_tt=odin_val", result["cookie_str"])


class DouyinAuthServiceQRImageTests(unittest.TestCase):

    def test_make_qr_image_returns_valid_png_base64(self):
        from app.services.douyin_auth import _make_qr_image

        b64 = _make_qr_image("https://www.douyin.com/test")
        self.assertTrue(b64.startswith("data:image/png;base64,"))
        raw = base64.b64decode(b64.split(",", 1)[1])
        # PNG magic bytes
        self.assertEqual(raw[:4], b"\x89PNG")

    def test_validate_cookie_str_valid(self):
        from app.services.douyin_auth import DouyinAuthService

        good = "ttwid=xxx; sessionid=abc123; odin_tt=yyy"
        self.assertTrue(DouyinAuthService.validate_cookie_str(good))

    def test_validate_cookie_str_invalid(self):
        from app.services.douyin_auth import DouyinAuthService

        bad = "ttwid=xxx; msToken=yyy"
        self.assertFalse(DouyinAuthService.validate_cookie_str(bad))


if __name__ == "__main__":
    unittest.main()
