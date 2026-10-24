import asyncio
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

import httpx

from app.services.xhs_auth import XhsBrowserSession
from app.services.xhs_client import (
    XhsAuthenticationError,
    XhsClient,
    XhsDataFetchError,
    XhsNoteUnavailableError,
    XhsRiskControlError,
)
from app.services.xhs_models import XhsNoteReference


class SigningPage:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.sign_inputs = []
        self.browser_requests = []
        self.b1 = "b1-one"

    async def evaluate(self, expression, argument=None):
        if "fetch(" in expression:
            self.browser_requests.append(argument)
            return {"status": 200, "body": '{"success":true,"data":{"notes":[]}}'}
        if "userInfo" in expression:
            return {}
        if "_webmsxyw" in expression:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            self.sign_inputs.append(argument)
            return {"X-s": "native-signature", "X-t": 123456}
        if "mnsv2" in expression:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            self.sign_inputs.append(argument["sign_string"])
            return "signed-" + argument["md5"]
        if "localStorage" in expression:
            return self.b1
        raise AssertionError(expression)


class XhsClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.page = SigningPage()
        self.session = XhsBrowserSession(
            page=self.page,
            cookie_dict={"a1": "a1-one", "web_session": "session"},
            cookie_header="a1=a1-one; web_session=session",
            user_agent="test-agent",
        )

    def _client(self, handler, **kwargs):
        return XhsClient(
            self.session,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            request_interval=0,
            **kwargs,
        )

    async def test_concurrent_signing_is_serialized(self):
        client = self._client(lambda request: httpx.Response(200, json={"success": True, "data": {}}))

        await asyncio.gather(
            client.build_signed_headers("/one", params={"a": "1"}),
            client.build_signed_headers("/two", params={"b": "2"}),
        )

        self.assertEqual(1, self.page.max_active)
        await client.close()

    async def test_get_and_post_signing_use_expected_input_formats(self):
        client = self._client(lambda request: httpx.Response(200, json={"success": True, "data": {}}))

        await client.build_signed_headers("/get", params={"q": "中文 text", "many": ["a", "b"]})
        await client.build_signed_headers("/post", payload={"text": "中文", "number": 1})

        self.assertEqual(
            "/get?q=%E4%B8%AD%E6%96%87%20text&many=a&many=b",
            self.page.sign_inputs[0],
        )
        self.assertEqual('/post{"text":"中文","number":1}', self.page.sign_inputs[1])
        await client.close()

    async def test_empty_get_query_is_included_in_signature(self):
        client = self._client(lambda request: httpx.Response(200, json={"success": True, "data": {}}))

        await client.get_current_user()

        self.assertEqual("/api/sns/web/v1/user/selfinfo?", self.page.sign_inputs[0])
        await client.close()

    async def test_common_header_uses_a1_and_b1(self):
        client = self._client(lambda request: httpx.Response(200, json={"success": True, "data": {}}))

        one = await client.build_signed_headers("/test", params={"a": "1"})
        self.page.b1 = "b1-two"
        two = await client.build_signed_headers("/test", params={"a": "1"})
        self.session = XhsBrowserSession(
            page=self.page, cookie_dict={"a1": "a1-two"}, cookie_header="a1=a1-two", user_agent="test-agent"
        )
        client.session = self.session
        three = await client.build_signed_headers("/test", params={"a": "1"})

        self.assertEqual({"X-S", "X-T", "X-B3-Traceid"}, set(one))
        self.assertEqual("native-signature", one["X-S"])
        self.assertEqual("123456", one["X-T"])
        self.assertEqual(one["X-S"], two["X-S"])
        self.assertEqual(two["X-S"], three["X-S"])
        await client.close()

    async def test_missing_signing_state_is_rejected(self):
        self.session.cookie_dict.pop("a1")
        client = self._client(
            lambda request: httpx.Response(200, json={"success": True, "data": {}})
        )
        with self.assertRaises(XhsAuthenticationError):
            await client.build_signed_headers("/test", params={"a": "1"})
        await client.close()

    async def test_get_signature_matches_transmitted_query(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"success": True, "data": {}})

        client = self._client(handler)
        await client.get_creator_notes("user", "", "token", "pc_feed")

        self.assertEqual(
            requests[0].url.query.decode(),
            self.page.sign_inputs[0].split("?", 1)[1],
        )
        await client.close()

    async def test_search_uses_browser_compatible_api_headers(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"success": True, "data": {"items": []}})

        client = self._client(handler)

        await client.search_notes("test", 1, 20, "id")

        headers = requests[0].headers
        self.assertEqual("application/json, text/plain, */*", headers["accept"])
        self.assertEqual("zh-CN,zh;q=0.9", headers["accept-language"])
        self.assertEqual("application/json;charset=UTF-8", headers["content-type"])
        self.assertEqual("test-agent", headers["user-agent"])
        self.assertEqual("same-site", headers["sec-fetch-site"])
        await client.close()

    async def test_favorite_requests_use_signed_collect_endpoint(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"success": True, "data": {"items": []}})

        client = self._client(handler)

        await client.get_current_user()
        await client.get_user_collect_notes("user-id", "next-note")

        self.assertEqual(
            "/api/sns/web/v1/user/selfinfo",
            requests[0].url.path,
        )
        self.assertEqual(
            "/api/sns/web/v2/note/collect/page?user_id=user-id&num=30&cursor=next-note",
            self.page.browser_requests[0]["signing_input"],
        )
        await client.close()

    async def test_note_html_includes_search_security_parameters(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, text="<html></html>", request=request)

        client = self._client(handler)
        reference = XhsNoteReference(
            note_id="note-id",
            source_url="https://www.xiaohongshu.com/explore/note-id",
            xsec_token="search-token",
            xsec_source="",
        )

        await client.get_note_html(reference, authenticated=True)

        query = dict(requests[0].url.params)
        self.assertEqual("search-token", query["xsec_token"])
        self.assertEqual("pc_search", query["xsec_source"])
        await client.close()

    async def test_sensitive_headers_are_only_sent_to_authenticated_xhs_requests(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                200,
                json={"success": True, "data": {}},
                request=request,
            )

        client = self._client(handler)
        await client.search_notes("test", 1, 20, "id")
        await client.resolve_shared_url("https://example.com/share")
        destination = Path("xhs-client-header-media.bin")
        self.addCleanup(lambda: destination.unlink(missing_ok=True))
        await client.download_media("https://cdn.example.com/file", destination)

        self.assertIn("cookie", requests[0].headers)
        self.assertNotIn("cookie", requests[1].headers)
        self.assertNotIn("cookie", requests[2].headers)
        await client.close()

    async def test_signed_request_does_not_follow_cross_origin_redirect(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                302,
                headers={"location": "https://attacker.example/collect"},
                request=request,
            )

        client = self._client(handler)
        with self.assertRaises(XhsDataFetchError):
            await client.search_notes("test", 1, 20, "id")

        self.assertEqual(1, len(requests))
        self.assertEqual("edith.xiaohongshu.com", requests[0].url.host)
        await client.close()

    async def test_authenticated_html_refuses_untrusted_host(self):
        client = self._client(
            lambda request: httpx.Response(200, text="<html></html>", request=request)
        )
        with self.assertRaises(XhsDataFetchError):
            await client._request_html("https://example.com/note", authenticated=True)
        with self.assertRaises(XhsDataFetchError):
            await client._request_html(
                "http://www.xiaohongshu.com/explore/note",
                authenticated=True,
            )
        await client.close()

    async def test_risk_http_status_is_not_retried(self):
        calls = 0

        def handler(_request):
            nonlocal calls
            calls += 1
            return httpx.Response(461)

        client = self._client(handler)
        with self.assertRaises(XhsRiskControlError):
            await client.search_notes("test", 1, 20, "id")
        self.assertEqual(1, calls)
        await client.close()

    async def test_api_errors_are_classified(self):
        cases = [
            ({"success": False, "code": -101, "msg": "not logged in"}, XhsAuthenticationError),
            (
                {"success": False, "code": 300001, "msg": "您当前登录的账号没有权限访问"},
                XhsAuthenticationError,
            ),
            ({"success": False, "code": 300012, "msg": "blocked"}, XhsRiskControlError),
            ({"success": False, "code": -510001, "msg": "Note status abnormal"}, XhsNoteUnavailableError),
        ]
        for body, error in cases:
            client = self._client(lambda _request, value=body: httpx.Response(200, json=value))
            with self.assertRaises(error):
                await client.search_notes("test", 1, 20, "id")
            await client.close()

    async def test_malformed_json_is_classified(self):
        client = self._client(lambda _request: httpx.Response(200, text="not-json"))

        with self.assertRaises(XhsDataFetchError):
            await client.search_notes("test", 1, 20, "id")
        await client.close()

    async def test_transport_errors_retry_at_most_three_times(self):
        http_client = AsyncMock()
        http_client.request = AsyncMock(side_effect=httpx.ConnectError("offline"))
        client = XhsClient(self.session, http_client=http_client, request_interval=0)

        with self.assertRaises(XhsDataFetchError):
            await client.search_notes("test", 1, 20, "id")

        self.assertEqual(3, http_client.request.await_count)

    async def test_download_media_is_atomic_and_rejects_invalid_responses(self):
        destination = Path("xhs-client-test-media.bin")
        self.addCleanup(lambda: destination.unlink(missing_ok=True))
        self.addCleanup(lambda: destination.with_suffix(".bin.part").unlink(missing_ok=True))

        client = self._client(lambda _request: httpx.Response(200, content=b"media"))
        result = await client.download_media("https://media.example/file", destination)
        self.assertEqual(destination, result)
        self.assertEqual(b"media", destination.read_bytes())
        self.assertFalse(destination.with_suffix(".bin.part").exists())
        await client.close()

        rejected = self._client(lambda _request: httpx.Response(404))
        with self.assertRaises(XhsDataFetchError):
            await rejected.download_media("https://media.example/missing", destination)
        await rejected.close()

        oversized = self._client(
            lambda _request: httpx.Response(200, headers={"content-length": "8"}, content=b"12345678"),
            max_media_bytes=4,
        )
        with self.assertRaises(XhsDataFetchError):
            await oversized.download_media("https://media.example/large", destination)
        self.assertFalse(destination.with_suffix(".bin.part").exists())
        await oversized.close()

        truncated = self._client(
            lambda _request: httpx.Response(200, headers={"content-length": "8"}, content=b"short"),
        )
        with self.assertRaises(XhsDataFetchError):
            await truncated.download_media("https://media.example/truncated", destination)
        self.assertFalse(destination.with_suffix(".bin.part").exists())
        await truncated.close()


if __name__ == "__main__":
    unittest.main()
