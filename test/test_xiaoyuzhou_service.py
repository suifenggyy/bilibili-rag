"""
Tests for XiaoyuzhouService RSS parsing and login helpers.
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


_SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>测试播客</title>
    <itunes:author>测试作者</itunes:author>
    <itunes:image href="https://example.com/cover.jpg"/>
    <item>
      <title>第一集</title>
      <guid>ep-001</guid>
      <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg" length="1000"/>
      <itunes:duration>1800</itunes:duration>
      <pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate>
      <description>第一集内容描述</description>
    </item>
    <item>
      <title>第二集</title>
      <guid>ep-002</guid>
      <enclosure url="https://example.com/ep2.mp3" type="audio/mpeg" length="2000"/>
      <itunes:duration>3600</itunes:duration>
    </item>
  </channel>
</rss>
"""


class XiaoyuzhouRSSParsingTests(unittest.TestCase):

    def test_parse_podcast_info_extracts_title_and_author(self):
        from app.services.xiaoyuzhou import XiaoyuzhouService

        info = XiaoyuzhouService._parse_podcast_info(_SAMPLE_RSS, "https://feeds.example.com/podcast/abc")
        self.assertEqual(info["title"], "测试播客")
        self.assertEqual(info["author"], "测试作者")
        self.assertEqual(info["cover_url"], "https://example.com/cover.jpg")

    def test_parse_episodes_returns_all_items(self):
        from app.services.xiaoyuzhou import XiaoyuzhouService

        episodes = XiaoyuzhouService._parse_episodes(_SAMPLE_RSS)
        self.assertEqual(len(episodes), 2)

    def test_parse_episodes_first_episode_fields(self):
        from app.services.xiaoyuzhou import XiaoyuzhouService

        episodes = XiaoyuzhouService._parse_episodes(_SAMPLE_RSS)
        ep = episodes[0]
        self.assertEqual(ep["title"], "第一集")
        self.assertEqual(ep["episode_id"], "ep-001")
        self.assertEqual(ep["audio_url"], "https://example.com/ep1.mp3")
        self.assertEqual(ep["duration"], 1800)

    def test_parse_episodes_limit(self):
        from app.services.xiaoyuzhou import XiaoyuzhouService

        # Patching _fetch_rss not needed — test get_episodes_from_rss via sync _parse_episodes
        episodes = XiaoyuzhouService._parse_episodes(_SAMPLE_RSS)
        limited = episodes[:1]
        self.assertEqual(len(limited), 1)
        self.assertEqual(limited[0]["episode_id"], "ep-001")


class XiaoyuzhouServiceDeviceIdTests(unittest.TestCase):

    def test_device_id_is_stable_for_same_token(self):
        from app.services.xiaoyuzhou import XiaoyuzhouService

        svc = XiaoyuzhouService(access_token="tok_abc")
        did1 = svc.device_id
        did2 = svc.device_id
        self.assertEqual(did1, did2)

    def test_device_id_differs_for_different_tokens(self):
        from app.services.xiaoyuzhou import XiaoyuzhouService

        svc1 = XiaoyuzhouService(access_token="tok_aaa")
        svc2 = XiaoyuzhouService(access_token="tok_bbb")
        self.assertNotEqual(svc1.device_id, svc2.device_id)

    def test_build_rss_url(self):
        from app.services.xiaoyuzhou import XiaoyuzhouService

        svc = XiaoyuzhouService()
        url = svc.build_rss_url("abc123")
        self.assertEqual(url, "https://feeds.xiaoyuzhoufm.com/podcast/abc123")


class XiaoyuzhouServiceLoginTests(unittest.IsolatedAsyncioTestCase):

    async def test_send_sms_code_returns_true_on_200(self):
        from app.services.xiaoyuzhou import XiaoyuzhouService

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        async_ctx = AsyncMock()
        async_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        async_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.xiaoyuzhou.httpx.AsyncClient", return_value=async_ctx):
            svc = XiaoyuzhouService()
            result = await svc.send_sms_code("13800138000")

        self.assertTrue(result)

    async def test_send_sms_code_returns_false_on_error(self):
        from app.services.xiaoyuzhou import XiaoyuzhouService

        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.text = "bad request"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        async_ctx = AsyncMock()
        async_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        async_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.xiaoyuzhou.httpx.AsyncClient", return_value=async_ctx):
            svc = XiaoyuzhouService()
            result = await svc.send_sms_code("13800138000")

        self.assertFalse(result)

    async def test_login_with_sms_extracts_token_from_headers(self):
        from app.services.xiaoyuzhou import XiaoyuzhouService

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "x-jike-access-token": "access_abc",
            "x-jike-refresh-token": "refresh_xyz",
        }
        mock_resp.json.return_value = {
            "userInfo": {"uid": "u001", "nickname": "测试用户"}
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        async_ctx = AsyncMock()
        async_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        async_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.xiaoyuzhou.httpx.AsyncClient", return_value=async_ctx):
            svc = XiaoyuzhouService()
            result = await svc.login_with_sms("13800138000", "123456")

        self.assertIsNotNone(result)
        self.assertEqual(result["access_token"], "access_abc")
        self.assertEqual(result["refresh_token"], "refresh_xyz")
        self.assertEqual(result["nickname"], "测试用户")
        self.assertEqual(svc.access_token, "access_abc")

    async def test_login_with_sms_returns_none_on_no_token(self):
        from app.services.xiaoyuzhou import XiaoyuzhouService

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}  # No token in headers
        mock_resp.json.return_value = {}  # No token in body either

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        async_ctx = AsyncMock()
        async_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        async_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.services.xiaoyuzhou.httpx.AsyncClient", return_value=async_ctx):
            svc = XiaoyuzhouService()
            result = await svc.login_with_sms("13800138000", "000000")

        self.assertIsNone(result)


class XiaoyuzhouGetEpisodesFromRSSTests(unittest.IsolatedAsyncioTestCase):

    async def test_get_episodes_from_rss_returns_all_items(self):
        from app.services.xiaoyuzhou import XiaoyuzhouService

        with patch.object(XiaoyuzhouService, "_fetch_rss", return_value=_SAMPLE_RSS):
            svc = XiaoyuzhouService()
            episodes = await svc.get_episodes_from_rss(
                "https://feeds.xiaoyuzhoufm.com/podcast/test123"
            )

        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0]["episode_id"], "ep-001")

    async def test_get_episodes_from_rss_respects_limit(self):
        from app.services.xiaoyuzhou import XiaoyuzhouService

        with patch.object(XiaoyuzhouService, "_fetch_rss", return_value=_SAMPLE_RSS):
            svc = XiaoyuzhouService()
            episodes = await svc.get_episodes_from_rss(
                "https://feeds.xiaoyuzhoufm.com/podcast/test123",
                limit=1,
            )

        self.assertEqual(len(episodes), 1)
        self.assertEqual(episodes[0]["title"], "第一集")

    async def test_get_episodes_from_rss_returns_empty_on_failure(self):
        from app.services.xiaoyuzhou import XiaoyuzhouService

        with patch.object(
            XiaoyuzhouService, "_fetch_rss", side_effect=RuntimeError("network error")
        ):
            svc = XiaoyuzhouService()
            episodes = await svc.get_episodes_from_rss("https://bad.url/")

        self.assertEqual(episodes, [])


if __name__ == "__main__":
    unittest.main()
