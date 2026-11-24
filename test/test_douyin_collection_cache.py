import inspect
import io
import unittest
from contextlib import asynccontextmanager, redirect_stdout
from datetime import datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app import models
from app.services.douyin import DouyinService


def page(*ids, has_more=False, cursor=0):
    return {
        "aweme_list": [{"aweme_id": value, "desc": f"video {value}"} for value in ids],
        "has_more": int(has_more),
        "max_cursor": cursor,
    }


class DouyinCollectionCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(models.Base.metadata.create_all)

        @asynccontextmanager
        async def session():
            async with AsyncSession(self.engine, expire_on_commit=False) as db:
                yield db

        self.session = session
        self.db_patch = patch("app.services.douyin.get_db_context", session, create=True)
        self.db_patch.start()
        self.sleep_patch = patch("app.services.douyin.asyncio.sleep", AsyncMock())
        self.sleep_patch.start()
        self.service = DouyinService(cookie="uid_tt=account-one; sessionid=first")

    async def asyncTearDown(self):
        self.sleep_patch.stop()
        self.db_patch.stop()
        await self.engine.dispose()

    async def seed(self, **kwargs):
        self.service.get_collection_videos_page = AsyncMock(return_value=page("2", "1", "0"))
        return await self.service.get_all_collection_videos(**kwargs)

    async def test_first_fetch_persists_raw_list_in_db(self):
        self.service.get_collection_videos_page = AsyncMock(side_effect=[
            page("2", "1", has_more=True, cursor=100), page("0"),
        ])
        result = await self.service.get_all_collection_videos()
        self.assertEqual([item["aweme_id"] for item in result], ["2", "1", "0"])
        self.assertTrue(hasattr(models, "DouyinCollectionCache"), "Collection DB cache model is missing")
        async with self.session() as db:
            rows = (await db.execute(select(models.DouyinCollectionCache))).scalars().all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].videos, result)
        self.assertNotIn("account-one", rows[0].cache_key)
        self.assertNotIn("sessionid", rows[0].cache_key)

    async def test_cached_id_stops_pagination_and_keeps_cached_tail(self):
        await self.seed()
        self.service.get_collection_videos_page = AsyncMock(side_effect=[
            page("3", "2", has_more=True, cursor=100), page(),
        ])
        result = await self.service.get_all_collection_videos()
        self.assertEqual([item["aweme_id"] for item in result], ["3", "2", "1", "0"])
        self.service.get_collection_videos_page.assert_awaited_once_with(max_cursor=0)

    async def test_persistence_is_reused_by_new_service_and_rotated_session_cookie(self):
        await self.seed()
        restarted = DouyinService(cookie="sessionid=rotated; uid_tt=account-one")
        restarted.get_collection_videos_page = AsyncMock(side_effect=[
            page("2", has_more=True, cursor=100), page(),
        ])
        result = await restarted.get_all_collection_videos()
        self.assertEqual([item["aweme_id"] for item in result], ["2", "1", "0"])
        restarted.get_collection_videos_page.assert_awaited_once()

    async def test_entire_hit_page_is_kept_and_metadata_is_refreshed(self):
        await self.seed()
        fresh = page("3", "2", "extra", has_more=True, cursor=100)
        fresh["aweme_list"][1]["desc"] = "updated title"
        self.service.get_collection_videos_page = AsyncMock(side_effect=[fresh, page()])
        result = await self.service.get_all_collection_videos()
        self.assertEqual([item["aweme_id"] for item in result], ["3", "2", "extra", "1", "0"])
        self.assertEqual(result[1]["desc"], "updated title")
        self.service.get_collection_videos_page.assert_awaited_once()

    async def test_new_pages_are_fetched_until_first_cached_id(self):
        await self.seed()
        self.service.get_collection_videos_page = AsyncMock(side_effect=[
            page("4", "3", has_more=True, cursor=200),
            page("2", has_more=True, cursor=100), page(),
        ])
        result = await self.service.get_all_collection_videos()
        self.assertEqual([item["aweme_id"] for item in result], ["4", "3", "2", "1", "0"])
        self.assertEqual(self.service.get_collection_videos_page.await_count, 2)

    async def test_duplicates_in_uncached_pages_do_not_trigger_early_stop(self):
        self.service.get_collection_videos_page = AsyncMock(side_effect=[
            page("3", "2", has_more=True, cursor=200),
            page("2", "1", has_more=True, cursor=100), page("0"),
        ])
        result = await self.service.get_all_collection_videos()
        self.assertEqual([item["aweme_id"] for item in result], ["3", "2", "1", "0"])
        self.assertEqual(self.service.get_collection_videos_page.await_count, 3)

    async def test_different_accounts_do_not_share_cache(self):
        await self.seed()
        other = DouyinService(cookie="uid_tt=account-two; sessionid=other")
        other.get_collection_videos_page = AsyncMock(side_effect=[
            page("2", has_more=True, cursor=100), page("other"),
        ])
        result = await other.get_all_collection_videos()
        self.assertEqual([item["aweme_id"] for item in result], ["2", "other"])
        self.assertEqual(other.get_collection_videos_page.await_count, 2)

    async def test_broader_date_filter_does_not_reuse_narrow_snapshot(self):
        await self.seed(after_date="2026-11-01")
        self.service.get_collection_videos_page = AsyncMock(side_effect=[
            page("2", has_more=True, cursor=100), page("older"),
        ])
        result = await self.service.get_all_collection_videos()
        self.assertEqual([item["aweme_id"] for item in result], ["2", "older"])
        self.assertEqual(self.service.get_collection_videos_page.await_count, 2)

    async def test_larger_page_budget_does_not_reuse_truncated_snapshot(self):
        await self.seed(max_pages=1)
        self.service.get_collection_videos_page = AsyncMock(side_effect=[
            page("2", has_more=True, cursor=100), page("older"),
        ])
        result = await self.service.get_all_collection_videos(max_pages=2)
        self.assertEqual([item["aweme_id"] for item in result], ["2", "older"])
        self.assertEqual(self.service.get_collection_videos_page.await_count, 2)

    async def test_date_filter_still_stops_at_page_cursor(self):
        cursor = int(datetime(2026, 10, 31).timestamp()) * 1_000_000
        self.service.get_collection_videos_page = AsyncMock(return_value=page(
            "published-long-ago", has_more=True, cursor=cursor,
        ))
        result = await self.service.get_all_collection_videos(after_date="2026-11-01")
        self.assertEqual([item["aweme_id"] for item in result], ["published-long-ago"])
        self.service.get_collection_videos_page.assert_awaited_once()

    async def test_failed_page_does_not_replace_previous_cache(self):
        await self.seed()
        self.service.get_collection_videos_page = AsyncMock(side_effect=[
            page("3", has_more=True, cursor=100), RuntimeError("request rejected"),
        ])
        with self.assertRaisesRegex(RuntimeError, "request rejected"):
            await self.service.get_all_collection_videos()
        self.service.get_collection_videos_page = AsyncMock(side_effect=[
            page("2", has_more=True, cursor=100), page(),
        ])
        result = await self.service.get_all_collection_videos()
        self.assertEqual([item["aweme_id"] for item in result], ["2", "1", "0"])

    async def test_cached_list_does_not_hide_first_page_request_failure(self):
        await self.seed()
        self.service.get_collection_videos_page = AsyncMock(side_effect=RuntimeError("request rejected"))
        with self.assertRaisesRegex(RuntimeError, "request rejected"):
            await self.service.get_all_collection_videos()

    async def test_force_refresh_bypasses_cache_and_replaces_removed_favorites(self):
        self.assertIn("refresh_cache", inspect.signature(self.service.get_all_collection_videos).parameters)
        await self.seed()
        self.service.get_collection_videos_page = AsyncMock(side_effect=[
            page("2", has_more=True, cursor=100), page("fresh"),
        ])
        result = await self.service.get_all_collection_videos(refresh_cache=True)
        self.assertEqual([item["aweme_id"] for item in result], ["2", "fresh"])
        self.assertEqual(self.service.get_collection_videos_page.await_count, 2)
        self.service.get_collection_videos_page = AsyncMock(return_value=page("2"))
        result = await self.service.get_all_collection_videos()
        self.assertEqual([item["aweme_id"] for item in result], ["2", "fresh"])

    async def test_empty_remote_collection_clears_previous_cache(self):
        await self.seed()
        self.service.get_collection_videos_page = AsyncMock(return_value=page())
        self.assertEqual(await self.service.get_all_collection_videos(), [])
        self.service.get_collection_videos_page = AsyncMock(side_effect=[
            page("2", has_more=True, cursor=100), page("fresh"),
        ])
        result = await self.service.get_all_collection_videos()
        self.assertEqual([item["aweme_id"] for item in result], ["2", "fresh"])
        self.assertEqual(self.service.get_collection_videos_page.await_count, 2)


class DouyinCollectionCacheWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_cli_documents_force_refresh_option(self):
        from scripts.export_douyin_to_md import main

        output = io.StringIO()
        with patch("sys.argv", ["export_douyin_to_md.py", "--help"]), redirect_stdout(output):
            with self.assertRaises(SystemExit) as exit_result:
                await main()
        self.assertEqual(exit_result.exception.code, 0)
        self.assertIn("--refresh-collection-cache", output.getvalue())

    async def test_export_api_passes_force_refresh_to_collection_service(self):
        from app.routers import douyin_export

        self.assertIn("refresh_collection_cache", douyin_export.DouyinExportRequest.model_fields)
        request = douyin_export.DouyinExportRequest(cookie="test", refresh_collection_cache=True)
        service = AsyncMock()
        service.get_all_collection_videos.return_value = []
        task = {"logs": [], "output_files": []}
        with (
            patch.dict(douyin_export.douyin_export_tasks, {"cache-test": task}, clear=True),
            patch("app.services.douyin.DouyinService", return_value=service),
            patch.object(douyin_export, "_build_asr_service", return_value=None),
            patch("app.services.douyin_fetcher.DouyinContentFetcher"),
            patch.object(douyin_export.settings, "douyin_after_date", ""),
        ):
            await douyin_export._run_douyin_export("cache-test", request)
        service.get_all_collection_videos.assert_awaited_once_with(after_date=None, refresh_cache=True)
        service.close.assert_awaited_once()
