"""Tests for DouyinService creator methods (get_all_creator_videos)."""

import unittest
from unittest.mock import AsyncMock
import time
import datetime


class TestDouyinCreatorVideos(unittest.IsolatedAsyncioTestCase):
    def _make_service(self):
        from app.services.douyin import DouyinService
        svc = DouyinService.__new__(DouyinService)
        svc.cookie = ""
        svc.evil0ctal_url = "http://localhost:2333"
        return svc

    def _make_page(self, videos, has_more=False, max_cursor=0):
        return {"aweme_list": videos, "has_more": has_more, "max_cursor": max_cursor}

    def _video(self, aweme_id, create_time):
        return {"aweme_id": aweme_id, "desc": f"Video {aweme_id}", "create_time": create_time}

    async def test_returns_all_when_no_after_date(self):
        svc = self._make_service()
        now = int(time.time())
        page1 = self._make_page([self._video("v1", now - 100), self._video("v2", now - 200)], has_more=True, max_cursor=200)
        page2 = self._make_page([self._video("v3", now - 300)], has_more=False)
        svc.get_creator_videos_page = AsyncMock(side_effect=[page1, page2])

        result = await svc.get_all_creator_videos("sec_uid_abc")
        self.assertEqual(len(result), 3)

    async def test_stops_early_on_after_date(self):
        svc = self._make_service()
        now = int(time.time())
        t_10 = now - 86400 * 10
        t_50 = now - 86400 * 50

        after_date = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

        page1 = self._make_page([self._video("v1", t_10), self._video("v2", t_50)], has_more=True, max_cursor=100)
        svc.get_creator_videos_page = AsyncMock(return_value=page1)

        result = await svc.get_all_creator_videos("sec_uid_abc", after_date=after_date)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["aweme_id"], "v1")
        svc.get_creator_videos_page.assert_awaited_once()

    async def test_returns_empty_when_all_too_old(self):
        svc = self._make_service()
        now = int(time.time())
        old_ts = now - 86400 * 100

        after_date = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
        page = self._make_page([self._video("v1", old_ts)], has_more=False)
        svc.get_creator_videos_page = AsyncMock(return_value=page)

        result = await svc.get_all_creator_videos("sec_uid_abc", after_date=after_date)
        self.assertEqual(result, [])

    async def test_stops_when_no_videos_in_page(self):
        svc = self._make_service()
        svc.get_creator_videos_page = AsyncMock(return_value={"aweme_list": [], "has_more": False})
        result = await svc.get_all_creator_videos("sec_uid_abc")
        self.assertEqual(result, [])

    async def test_invalid_after_date_ignored(self):
        svc = self._make_service()
        now = int(time.time())
        page = self._make_page([self._video("v1", now - 100)], has_more=False)
        svc.get_creator_videos_page = AsyncMock(return_value=page)
        result = await svc.get_all_creator_videos("sec_uid_abc", after_date="invalid-date")
        self.assertEqual(len(result), 1)

    async def test_paginates_multiple_pages(self):
        svc = self._make_service()
        now = int(time.time())
        pages = [
            self._make_page([self._video(f"v{i}", now - i * 60)], has_more=True, max_cursor=i * 100)
            for i in range(1, 4)
        ]
        pages[-1]["has_more"] = False
        svc.get_creator_videos_page = AsyncMock(side_effect=pages)

        result = await svc.get_all_creator_videos("sec_uid_abc")
        self.assertEqual(len(result), 3)
        self.assertEqual(svc.get_creator_videos_page.await_count, 3)


if __name__ == "__main__":
    unittest.main()
