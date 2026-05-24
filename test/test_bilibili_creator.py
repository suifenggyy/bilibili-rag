"""Tests for BilibiliService creator methods (get_all_creator_videos)."""

import unittest
from unittest.mock import AsyncMock, patch, MagicMock
import time


class TestBilibiliCreatorVideos(unittest.IsolatedAsyncioTestCase):
    def _make_service(self):
        from app.services.bilibili import BilibiliService
        svc = BilibiliService.__new__(BilibiliService)
        svc.cookie = ""
        svc._wbi_keys_cache = None
        return svc

    def _make_page(self, videos, has_more=False):
        return {"list": videos, "has_more": has_more}

    def _video(self, bvid, pubdate):
        return {"bvid": bvid, "title": f"Video {bvid}", "created": pubdate, "pubdate": pubdate}

    async def test_returns_all_videos_when_no_after_date(self):
        svc = self._make_service()
        now = int(time.time())
        page1 = self._make_page([self._video("BV1", now - 100), self._video("BV2", now - 200)], has_more=True)
        page2 = self._make_page([self._video("BV3", now - 300)], has_more=False)

        svc.get_creator_videos_page = AsyncMock(side_effect=[page1, page2])

        result = await svc.get_all_creator_videos("12345")
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["bvid"], "BV1")

    async def test_stops_early_when_after_date_reached(self):
        svc = self._make_service()
        # Videos at day 100, 50, and 10 days ago
        now = int(time.time())
        t_100 = now - 86400 * 100
        t_50  = now - 86400 * 50
        t_10  = now - 86400 * 10

        # after_date = 30 days ago
        import datetime
        after_date = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

        # page1: two videos newer than cutoff (t_10, t_50 — wait, t_50 is 50 days ago < 30 days)
        # after_ts is 30 days ago, so t_50 < after_ts → stop early
        page1 = self._make_page([self._video("BV1", t_10), self._video("BV2", t_50)], has_more=True)

        svc.get_creator_videos_page = AsyncMock(return_value=page1)

        result = await svc.get_all_creator_videos("12345", after_date=after_date)
        # BV1 (10 days ago) is newer than cutoff → included
        # BV2 (50 days ago) is older than cutoff → triggers early stop
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["bvid"], "BV1")
        # Should have called get_creator_videos_page only once (stopped early)
        svc.get_creator_videos_page.assert_awaited_once()

    async def test_returns_empty_when_all_videos_too_old(self):
        svc = self._make_service()
        now = int(time.time())
        old_ts = now - 86400 * 100

        import datetime
        after_date = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()

        page1 = self._make_page([self._video("BV1", old_ts)], has_more=False)
        svc.get_creator_videos_page = AsyncMock(return_value=page1)

        result = await svc.get_all_creator_videos("12345", after_date=after_date)
        self.assertEqual(result, [])

    async def test_returns_empty_on_empty_first_page(self):
        svc = self._make_service()
        svc.get_creator_videos_page = AsyncMock(return_value={"list": [], "has_more": False})
        result = await svc.get_all_creator_videos("12345")
        self.assertEqual(result, [])

    async def test_invalid_after_date_ignored(self):
        """Invalid after_date format should not crash — treats as no filter."""
        svc = self._make_service()
        now = int(time.time())
        page = self._make_page([self._video("BV1", now - 100)], has_more=False)
        svc.get_creator_videos_page = AsyncMock(return_value=page)
        result = await svc.get_all_creator_videos("12345", after_date="not-a-date")
        self.assertEqual(len(result), 1)

    async def test_paginates_until_has_more_false(self):
        svc = self._make_service()
        now = int(time.time())
        pages = [
            self._make_page([self._video(f"BV{i}", now - i * 60)], has_more=True)
            for i in range(1, 4)
        ]
        pages[-1]["has_more"] = False
        svc.get_creator_videos_page = AsyncMock(side_effect=pages)
        result = await svc.get_all_creator_videos("12345")
        self.assertEqual(len(result), 3)


if __name__ == "__main__":
    unittest.main()
