import asyncio
import shutil
import unittest
from datetime import datetime
from pathlib import Path

from app.services.content_storage import ContentStorageManager
from app.services.xhs_client import XhsNoteUnavailableError
from app.services.xhs_models import (
    XhsDiscoveryKind,
    XhsNoteContent,
    XhsNoteInfo,
    XhsNoteReference,
)
from scripts.export_xhs_to_md import (
    ExportCounts,
    SourceKind,
    _write_export,
    build_parser,
    main,
    run_export,
)


class FakeAuth:
    async def open(self):
        return object()

    async def close(self):
        return None


class FailingAuth(FakeAuth):
    async def open(self):
        raise RuntimeError("authentication failed")


class FakeClient:
    async def close(self):
        return None


class FakeService:
    def __init__(self, sources, notes, failures=(), unavailable=()):
        self.sources = sources
        self.notes = notes
        self.failures = set(failures)
        self.unavailable = set(unavailable)
        self.calls = []

    async def references_from_urls(self, urls):
        self.calls.append(("note_url", urls[0]))
        return list(self.sources[("note_url", urls[0])])

    async def iter_creator_references(self, creator, *, after_date=None):
        self.calls.append(("creator", creator))
        for reference in self.sources[("creator", creator)]:
            yield reference

    async def iter_search_references(self, keyword, *, after_date=None):
        self.calls.append(("keyword", keyword))
        for reference in self.sources[("keyword", keyword)]:
            yield reference

    async def iter_favorite_references(self, *, after_date=None):
        self.calls.append(("favorites", ""))
        for reference in self.sources[("favorites", "")]:
            yield reference

    async def resolve_note(self, reference):
        if reference.note_id in self.unavailable:
            raise XhsNoteUnavailableError("note unavailable")
        if reference.note_id in self.failures:
            raise RuntimeError("detail unavailable")
        return self.notes[reference.note_id]


class FakeFetcher:
    def __init__(self, contents):
        self.contents = contents

    async def fetch_content(self, note):
        return self.contents[note.note_id]


class FakeFactory:
    def __init__(self, service, fetcher, storage, auth=None):
        self.service = service
        self.fetcher = fetcher
        self.storage = storage
        self.auth = auth or FakeAuth()

    def create_auth(self, args):
        return self.auth

    def create_client(self, session, args):
        return FakeClient()

    def create_service(self, client):
        return self.service

    def create_summary_service(self):
        return object()

    def create_storage(self, args):
        return self.storage

    def create_fetcher(self, client, summary_service, storage, args):
        return self.fetcher


class XhsExportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(".xhs-export-test-data")
        shutil.rmtree(self.root, ignore_errors=True)
        self.storage = ContentStorageManager(
            workspace_root=str(self.root / "workspace"),
            export_root=str(self.root / "output"),
        )
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    @staticmethod
    def _reference(note_id, kind= XhsDiscoveryKind.SEARCH, token="token"):
        return XhsNoteReference(
            note_id=note_id,
            source_url=f"https://www.xiaohongshu.com/explore/{note_id}",
            xsec_token=token,
            xsec_source="pc_search",
            discovery_kind=kind,
        )

    @staticmethod
    def _note(note_id, title="Example"):
        return XhsNoteInfo(
            note_id=note_id,
            title=title,
            description="Original text",
            author_name="Author",
            published_at=int(datetime(2026, 8, 8).timestamp()),
            note_type="video",
            tags=("one", "two"),
            liked_count="1",
            collected_count="2",
            comment_count="3",
            source_url=f"https://www.xiaohongshu.com/explore/{note_id}",
        )

    def _content(self, note, *, warnings=None, image_paths=None):
        return XhsNoteContent(
            note=note,
            original_description=note.description,
            formatted_tags="#one #two",
            transcript="Transcript text",
            content_source="asr",
            summary_block=(
                "<!-- AI_SUMMARY_START -->\n```yaml\nsummary: |\n"
                "  Plain summary\n```\n<!-- AI_SUMMARY_END -->"
            ),
            comments_section="## 热门评论\n\n- **Alice**：Great note",
            warnings=warnings or [],
            image_paths=image_paths or [],
        )

    def _factory(self, args, *, sources, notes, contents, failures=(), unavailable=(), auth=None):
        return FakeFactory(
            FakeService(sources, notes, failures, unavailable),
            FakeFetcher(contents),
            self.storage,
            auth=auth,
        )

    def test_parser_requires_source(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args([])

    def test_parser_defaults_to_ten_notes(self):
        args = build_parser().parse_args(["--note-url", "abc"])

        self.assertEqual(10, args.limit)

    def test_parser_preserves_interleaved_source_order(self):
        args = build_parser().parse_args(
            ["--keyword", "first", "--note-url", "second", "--creator", "third", "--keyword", "fourth"]
        )
        self.assertEqual(
            [
                (SourceKind.KEYWORD, "first"),
                (SourceKind.NOTE_URL, "second"),
                (SourceKind.CREATOR, "third"),
                (SourceKind.KEYWORD, "fourth"),
            ],
            [(request.kind, request.value) for request in args.source_requests],
        )

    def test_favorites_source_is_discovered(self):
        args = build_parser().parse_args(
            ["--favorites", "--limit", "2"]
        )
        one = self._reference("one", XhsDiscoveryKind.FAVORITES)
        two = self._reference("two", XhsDiscoveryKind.FAVORITES)
        notes = {note_id: self._note(note_id) for note_id in ("one", "two")}
        contents = {note_id: self._content(note) for note_id, note in notes.items()}
        factory = self._factory(
            args,
            sources={
                ("favorites", ""): [one, two],
            },
            notes=notes,
            contents=contents,
        )

        counts = asyncio.run(run_export(args, dependency_factory=factory))

        self.assertEqual(ExportCounts(successful=2), counts)
        self.assertEqual([("favorites", "")], factory.service.calls)

    def test_favorites_source_and_legacy_alias_have_an_empty_value(self):
        args = build_parser().parse_args(["--favorites", "--all-favorite-boards"])

        self.assertEqual(
            [(SourceKind.FAVORITES, ""), (SourceKind.FAVORITES, "")],
            [(request.kind, request.value) for request in args.source_requests],
        )

    def test_global_limit_deduplicates_and_stops_later_sources(self):
        args = build_parser().parse_args(
            ["--keyword", "first", "--note-url", "duplicate", "--creator", "unused", "--limit", "2"]
        )
        first = self._reference("one", XhsDiscoveryKind.SEARCH)
        duplicate = self._reference("one", XhsDiscoveryKind.EXPLICIT, token="url-token")
        second = self._reference("two", XhsDiscoveryKind.EXPLICIT)
        sources = {
            ("keyword", "first"): [first],
            ("note_url", "duplicate"): [duplicate, second],
            ("creator", "unused"): [self._reference("three", XhsDiscoveryKind.CREATOR)],
        }
        notes = {note_id: self._note(note_id) for note_id in ("one", "two")}
        contents = {note_id: self._content(note) for note_id, note in notes.items()}
        factory = self._factory(args, sources=sources, notes=notes, contents=contents)

        counts = asyncio.run(run_export(args, dependency_factory=factory))

        self.assertEqual(ExportCounts(successful=2, skipped=1), counts)
        self.assertEqual(
            [("keyword", "first"), ("note_url", "duplicate")],
            factory.service.calls,
        )

    def test_writes_markdown_and_copies_only_images(self):
        args = build_parser().parse_args(["--note-url", "abc"])
        note = self._note("abc")
        image = self.storage.build_work_file_path("xhs", "Example_abc", "image_01.jpg")
        video = self.storage.build_work_file_path("xhs", "Example_abc", "video.mp4")
        wav = self.storage.build_work_file_path("xhs", "Example_abc", "audio.wav")
        image.write_bytes(b"image")
        video.write_bytes(b"video")
        wav.write_bytes(b"wav")
        content = self._content(note, image_paths=[str(image), str(video), str(wav)])
        factory = self._factory(
            args,
            sources={("note_url", "abc"): [self._reference("abc", XhsDiscoveryKind.EXPLICIT)]},
            notes={"abc": note},
            contents={"abc": content},
        )

        counts = asyncio.run(run_export(args, dependency_factory=factory))

        output_dir = self.root / "output" / "xhs" / "2026-08-08"
        markdown = output_dir / "Example_abc.md"
        media = output_dir / "Example_abc.media"
        rendered = markdown.read_text(encoding="utf-8")
        self.assertEqual(ExportCounts(successful=1), counts)
        self.assertTrue((media / "image_01.jpg").is_file())
        self.assertFalse((media / "video.mp4").exists())
        self.assertFalse((media / "audio.wav").exists())
        self.assertIn("platform: xhs", rendered)
        self.assertIn("source: https://www.xiaohongshu.com/explore/abc", rendered)
        self.assertIn("date: 2026-08-08", rendered)
        self.assertIn("summary: Plain summary", rendered)
        self.assertIn("Original text", rendered)
        self.assertIn("#one #two", rendered)
        self.assertIn("Example_abc.media/image_01.jpg", rendered)
        self.assertIn("Transcript text", rendered)
        self.assertIn("**Alice**：Great note", rendered)

    def test_write_export_returns_document_path(self):
        note = self._note("abc")

        markdown_path = _write_export(self._content(note), self.storage)

        self.assertTrue(markdown_path.is_absolute())
        self.assertTrue(markdown_path.is_file())

    def test_partial_export_returns_zero_exit_status(self):
        args = build_parser().parse_args(["--note-url", "partial"])
        note = self._note("partial")
        factory = self._factory(
            args,
            sources={("note_url", "partial"): [self._reference("partial", XhsDiscoveryKind.EXPLICIT)]},
            notes={"partial": note},
            contents={"partial": self._content(note, warnings=["summary failed"])},
        )

        self.assertEqual(0, main(["--note-url", "partial"], dependency_factory=factory))

    def test_core_failure_returns_nonzero_exit_status(self):
        args = build_parser().parse_args(["--note-url", "missing"])
        factory = self._factory(
            args,
            sources={("note_url", "missing"): [self._reference("missing", XhsDiscoveryKind.EXPLICIT)]},
            notes={},
            contents={},
            failures={"missing"},
        )

        self.assertEqual(1, main(["--note-url", "missing"], dependency_factory=factory))

    def test_unavailable_note_is_skipped(self):
        args = build_parser().parse_args(["--note-url", "missing"])
        factory = self._factory(
            args,
            sources={("note_url", "missing"): [self._reference("missing")]},
            notes={},
            contents={},
            unavailable={"missing"},
        )

        counts = asyncio.run(run_export(args, dependency_factory=factory))

        self.assertEqual(ExportCounts(skipped=1), counts)

    def test_task_level_auth_failure_returns_nonzero_exit_status(self):
        args = build_parser().parse_args(["--note-url", "abc"])
        factory = self._factory(args, sources={}, notes={}, contents={}, auth=FailingAuth())

        self.assertEqual(1, main(["--note-url", "abc"], dependency_factory=factory))


if __name__ == "__main__":
    unittest.main()
