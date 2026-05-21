import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


class ArticleFetcherStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_content_persists_article_raw_and_metadata(self):
        from app.services.article_fetcher import ArticleFetcher
        from app.services.content_storage import ContentStorageManager

        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as output_dir:
            fetcher = ArticleFetcher(
                storage_manager=ContentStorageManager(
                    workspace_root=workspace_dir,
                    export_root=output_dir,
                    max_total_size_bytes=1024 * 1024,
                    retention_days=3,
                )
            )
            fetcher.summary_service = type("FakeSummaryService", (), {"summarize": AsyncMock(return_value="")})()

            with patch.object(
                fetcher,
                "_extract_with_requests",
                return_value=("提取到的正文内容" * 30, "提取标题"),
            ):
                content = await fetcher.fetch_content("https://example.com/post", "原始标题")

            work_dir = Path(workspace_dir) / "instapaper" / datetime.now().strftime("%Y-%m-%d") / "原始标题"
            self.assertEqual(
                (work_dir / "article_raw.md").read_text(encoding="utf-8"),
                "提取到的正文内容" * 30,
            )
            metadata = json.loads((work_dir / "metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["url"], "https://example.com/post")
            self.assertEqual(metadata["source"], "trafilatura")
            self.assertEqual(content["title"], "提取标题")

    async def test_fetch_content_uses_requests_extraction_before_playwright(self):
        from app.services.article_fetcher import ArticleFetcher

        fetcher = ArticleFetcher()
        fetcher.summary_service = type("FakeSummaryService", (), {"summarize": AsyncMock(return_value="")})()

        with (
            patch.object(
                fetcher,
                "_extract_with_requests",
                return_value=("足够长的正文" * 40, "正常标题"),
            ) as requests_extract,
            patch.object(fetcher, "_extract_with_playwright", new=AsyncMock()) as playwright_extract,
        ):
            content = await fetcher.fetch_content("https://example.com/post", "原始标题")

        requests_extract.assert_called_once_with("https://example.com/post")
        playwright_extract.assert_not_called()
        self.assertEqual(content["source"], "trafilatura")
        self.assertEqual(content["title"], "正常标题")

    async def test_fetch_content_falls_back_when_requests_title_is_environment_error(self):
        from app.services.article_fetcher import ArticleFetcher

        fetcher = ArticleFetcher()
        fetcher.summary_service = type("FakeSummaryService", (), {"summarize": AsyncMock(return_value="")})()

        with (
            patch.object(
                fetcher,
                "_extract_with_requests",
                return_value=("这是一个看起来存在内容但不可信的正文片段" * 4, "环境异常"),
            ) as requests_extract,
            patch.object(
                fetcher,
                "_extract_with_playwright",
                return_value=("这是 playwright 抓取到的有效正文。" * 20, "真实标题"),
            ) as playwright_extract,
        ):
            content = await fetcher.fetch_content("https://example.com/post", "原始标题")

        requests_extract.assert_called_once_with("https://example.com/post")
        playwright_extract.assert_called_once_with("https://example.com/post")
        self.assertEqual(content["title"], "真实标题")
        self.assertEqual(content["text"], "这是 playwright 抓取到的有效正文。" * 20)

    async def test_fetch_content_falls_back_when_requests_body_is_too_short(self):
        from app.services.article_fetcher import ArticleFetcher

        fetcher = ArticleFetcher()
        fetcher.summary_service = type("FakeSummaryService", (), {"summarize": AsyncMock(return_value="")})()

        with (
            patch.object(
                fetcher,
                "_extract_with_requests",
                return_value=("正文过短", "正常标题"),
            ) as requests_extract,
            patch.object(
                fetcher,
                "_extract_with_playwright",
                return_value=("", ""),
            ) as playwright_extract,
        ):
            content = await fetcher.fetch_content("https://example.com/post", "原始标题")

        requests_extract.assert_called_once_with("https://example.com/post")
        playwright_extract.assert_called_once_with("https://example.com/post")
        self.assertEqual(content["source"], "basic_info")
        self.assertEqual(content["text"], "")

    async def test_fetch_content_uses_relaxed_playwright_fallback_after_normal_playwright_fails(self):
        from app.services.article_fetcher import ArticleFetcher

        fetcher = ArticleFetcher()
        fetcher.summary_service = type("FakeSummaryService", (), {"summarize": AsyncMock(return_value="")})()

        with (
            patch.object(
                fetcher,
                "_extract_with_requests",
                return_value=("正文过短", "正常标题"),
            ) as requests_extract,
            patch.object(
                fetcher,
                "_extract_with_playwright",
                return_value=("", ""),
            ) as playwright_extract,
            patch.object(
                fetcher,
                "_extract_with_playwright_relaxed",
                return_value=("这是宽松模式抓取到的有效正文。" * 20, "宽松标题"),
            ) as relaxed_playwright_extract,
        ):
            content = await fetcher.fetch_content("https://example.com/post", "原始标题")

        requests_extract.assert_called_once_with("https://example.com/post")
        playwright_extract.assert_called_once_with("https://example.com/post")
        relaxed_playwright_extract.assert_called_once_with("https://example.com/post")
        self.assertEqual(content["source"], "trafilatura")
        self.assertEqual(content["title"], "宽松标题")

    async def test_extract_from_html_preserves_links_and_images_by_default(self):
        from app.services.article_fetcher import ArticleFetcher

        fetcher = ArticleFetcher()
        fetcher.summary_service = type("FakeSummaryService", (), {"summarize": AsyncMock(return_value="")})()
        fake_trafilatura = SimpleNamespace(
            extract=Mock(return_value="带链接和图片的正文"),
            extract_metadata=Mock(return_value=SimpleNamespace(title="提取标题")),
        )

        with patch.dict("sys.modules", {"trafilatura": fake_trafilatura}):
            text, title = fetcher._extract_from_html("<html></html>", "https://example.com/post", "requests")

        fake_trafilatura.extract.assert_called_once_with(
            "<html></html>",
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            include_links=True,
            include_images=True,
            no_fallback=False,
            favor_recall=True,
        )
        self.assertEqual(text, "带链接和图片的正文")
        self.assertEqual(title, "提取标题")

    async def test_extract_with_playwright_uses_preserving_mode(self):
        from app.services.article_fetcher import ArticleFetcher

        fetcher = ArticleFetcher()

        with patch.object(
            fetcher,
            "_extract_with_playwright_mode",
            new=AsyncMock(return_value=("正文", "标题")),
        ) as mode_extract:
            text, title = await fetcher._extract_with_playwright("https://example.com/post")

        mode_extract.assert_called_once_with(
            "https://example.com/post",
            include_links=True,
            include_images=True,
        )
        self.assertEqual(text, "正文")
        self.assertEqual(title, "标题")

    async def test_fetch_content_generates_summary_block_for_extracted_article(self):
        from app.services.article_fetcher import ArticleFetcher

        fetcher = ArticleFetcher()
        summary_service = type("FakeSummaryService", (), {})()
        summary_service.summarize = AsyncMock(
            return_value="<!-- AI_SUMMARY_START -->\n```yaml\ntags:\n  - 文章\n```\n<!-- AI_SUMMARY_END -->"
        )
        fetcher.summary_service = summary_service

        with patch.object(
            fetcher,
            "_extract_with_requests",
            return_value=("提取到的正文内容" * 30, "提取标题"),
        ):
            content = await fetcher.fetch_content("https://example.com/post", "原始标题")

        summary_service.summarize.assert_awaited_once_with("提取到的正文内容" * 30)
        self.assertIn("AI_SUMMARY_START", content["summary_block"])

    def test_build_markdown_places_summary_block_before_body(self):
        from app.services.article_fetcher import ArticleFetcher

        markdown = ArticleFetcher.build_markdown(
            {"title": "文章标题", "url": "https://example.com/post"},
            {
                "text": "正文内容",
                "title": "文章标题",
                "source": "trafilatura",
                "url": "https://example.com/post",
                "summary_block": "<!-- AI_SUMMARY_START -->\n```yaml\ntags:\n  - 文章\n```\n<!-- AI_SUMMARY_END -->",
            },
        )

        self.assertLess(markdown.index("AI_SUMMARY_START"), markdown.index("## 正文"))


class InstapaperCliExportStorageTests(unittest.TestCase):
    def test_export_folder_writes_markdown_directly_into_date_directory(self):
        from scripts.export_instapaper_to_md import export_folder

        svc = type("FakeSvc", (), {})()
        svc.get_all_bookmarks = AsyncMock(
            return_value=[
                {
                    "bookmark_id": "1001",
                    "title": "文章标题",
                    "url": "https://example.com/post",
                }
            ]
        )
        fetcher = type("FakeFetcher", (), {})()
        fetcher.fetch_content = AsyncMock(
            return_value={
                "text": "正文",
                "title": "文章标题",
                "source": "trafilatura",
                "url": "https://example.com/post",
            }
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "instapaper" / datetime.now().strftime("%Y-%m-%d")
            output_dir.mkdir(parents=True, exist_ok=True)

            with patch("scripts.export_instapaper_to_md.asyncio.sleep", AsyncMock()):
                success, failed = asyncio.run(
                    export_folder(svc, fetcher, "starred", "星标收藏", output_dir)
                )

            expected = output_dir / "文章标题_1001.md"
            self.assertEqual(success, 1)
            self.assertEqual(failed, 0)
            self.assertTrue(expected.exists())
            self.assertFalse((output_dir / "星标收藏").exists())


class InstapaperWebExportStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_instapaper_export_writes_markdown_to_shared_output_dir(self):
        from app.routers import instapaper_export

        req = instapaper_export.InstapaperExportRequest(
            consumer_key="key",
            consumer_secret="secret",
            email="u@example.com",
            password="pwd",
            folders=["starred"],
            limit=0,
        )

        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as output_dir:
            job_id = "job-1"
            instapaper_export.instapaper_export_tasks[job_id] = {
                "job_id": job_id,
                "status": "pending",
                "progress": 0,
                "total_articles": 0,
                "processed_articles": 0,
                "current_article": "",
                "message": "",
                "file_count": 0,
                "created_at": datetime.now().isoformat(),
                "completed_at": None,
            }

            fake_service = type("FakeSvc", (), {})()
            fake_service.login = AsyncMock()
            fake_service.get_folders = AsyncMock(return_value=[])
            fake_service.get_all_bookmarks = AsyncMock(
                return_value=[
                    {
                        "bookmark_id": "1002",
                        "title": "网页导出标题",
                        "url": "https://example.com/page",
                    }
                ]
            )
            fake_service.close = AsyncMock()

            with (
                patch("app.services.instapaper.InstapaperService", return_value=fake_service),
                patch(
                    "app.services.article_fetcher.ArticleFetcher.fetch_content",
                    new=AsyncMock(
                        return_value={
                            "text": "网页正文",
                            "title": "网页导出标题",
                            "source": "trafilatura",
                            "url": "https://example.com/page",
                        }
                    ),
                ),
                patch("app.routers.instapaper_export.settings.content_workspace_root", workspace_dir),
                patch("app.routers.instapaper_export.settings.collection_output_dir", output_dir),
                patch("app.routers.instapaper_export.asyncio.sleep", AsyncMock()),
            ):
                await instapaper_export._run_instapaper_export(job_id, req)

            expected = Path(output_dir) / "instapaper" / datetime.now().strftime("%Y-%m-%d") / "网页导出标题_1002.md"
            self.assertTrue(expected.exists())
            self.assertEqual(instapaper_export.instapaper_export_tasks[job_id]["file_count"], 1)


if __name__ == "__main__":
    unittest.main()
