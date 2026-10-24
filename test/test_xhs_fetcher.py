import shutil
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from app.services.content_storage import ContentStorageManager
from app.services.xhs_fetcher import XhsContentFetcher
from app.services.xhs_models import XhsNoteInfo


class FakeClient:
    def __init__(self):
        self.download_media = AsyncMock(side_effect=self._download)
        self.comment_pages = []
        self.comment_calls = []

    async def _download(self, url, destination):
        Path(destination).write_bytes(b"media")
        return Path(destination)

    async def get_comments(self, note_id, token, cursor=""):
        self.comment_calls.append(cursor)
        return self.comment_pages.pop(0)


class XhsFetcherTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(".xhs-fetcher-test-data")
        shutil.rmtree(self.root, ignore_errors=True)
        self.storage = ContentStorageManager(
            workspace_root=str(self.root / "workspace"),
            export_root=str(self.root / "export"),
        )
        self.client = FakeClient()
        self.summary = type("FakeSummary", (), {"summarize": AsyncMock(return_value="summary")})()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _fetcher(self, **kwargs):
        return XhsContentFetcher(
            self.client,
            summary_service=self.summary,
            storage_manager=self.storage,
            **kwargs,
        )

    def _note(self, *, videos=(), images=()):
        return XhsNoteInfo(
            note_id="note-id",
            title="Example",
            description="Original description",
            tags=("one", "two"),
            image_urls=images,
            video_urls=videos,
            xsec_token="token",
        )

    async def test_reuses_cached_image_and_skips_cached_asr(self):
        note = self._note(images=("https://image.example/one.jpg",), videos=("https://video.example/one.mp4",))
        title = "Example_note-id"
        image = self.storage.build_work_file_path("xhs", title, "image_01.jpg")
        image.write_bytes(b"cached image")
        self.storage.write_work_text("xhs", title, "asr_raw.txt", "cached raw")

        content = await self._fetcher().fetch_content(note)

        self.client.download_media.assert_not_awaited()
        self.assertEqual("", content.transcript)
        self.assertEqual("basic_info", content.content_source)
        self.assertEqual([str(image)], content.image_paths)

    async def test_video_notes_skip_transcription_and_summarize_note_text(self):
        note = self._note(videos=("https://video.example/one.mp4",))
        content = await self._fetcher().fetch_content(note)

        self.client.download_media.assert_not_awaited()
        self.assertEqual("", content.transcript)
        self.assertEqual("basic_info", content.content_source)
        self.summary.summarize.assert_awaited_once_with("Original description")

    async def test_optional_failures_warn_and_preserve_core_note(self):
        note = self._note(images=("https://image.example/fail.jpg",), videos=("https://video.example/fail.mp4",))
        self.client.download_media = AsyncMock(side_effect=RuntimeError("network"))

        content = await self._fetcher().fetch_content(note)

        self.assertEqual("Original description", content.original_description)
        self.assertEqual("basic_info", content.content_source)
        self.assertEqual(1, len(content.warnings))
        self.assertIn("Image download failed", content.warnings[0])

    async def test_comments_paginate_to_limit_and_summary_combines_all_content(self):
        self.client.comment_pages = [
            {
                "comments": [
                    {"content": "first", "user_info": {"nickname": "Alice"}},
                    {"content": "second", "user_info": {"nickname": "Bob"}},
                ],
                "has_more": True,
                "cursor": "next",
            },
            {
                "comments": [{"content": "third", "user_info": {"nickname": "Carol"}}],
                "has_more": False,
                "cursor": "",
            },
        ]
        note = self._note(videos=("https://video.example/one.mp4",))
        fetcher = self._fetcher(comment_limit=3)
        content = await fetcher.fetch_content(note)

        self.assertEqual(["", "next"], self.client.comment_calls)
        self.assertIn("**Alice**：first", content.comments_section)
        self.assertIn("**Carol**：third", content.comments_section)
        self.summary.summarize.assert_awaited_once()
        summary_input = self.summary.summarize.await_args.args[0]
        self.assertIn("Original description", summary_input)
        self.assertNotIn("transcript", summary_input)
        self.assertIn("**Alice**：first", summary_input)

    async def test_comment_failure_does_not_mark_content_partial(self):
        self.client.comment_pages = []
        self.client.get_comments = AsyncMock(side_effect=RuntimeError("comment failure"))

        content = await self._fetcher(comment_limit=2).fetch_content(self._note())

        self.assertEqual([], content.warnings)


if __name__ == "__main__":
    unittest.main()
