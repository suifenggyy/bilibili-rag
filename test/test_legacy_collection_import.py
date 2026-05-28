"""
历史 collection/ 导入工具测试
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class LegacyCollectionImportTests(unittest.TestCase):
    def test_importer_copies_existing_collection_markdown_into_inbox(self):
        """历史 Markdown 文件被复制到 inbox 目录，文件名为 YYYY-MM-DD-slug.md。"""
        from app.services.knowledge_pipeline.legacy_import import LegacyCollectionImporter

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            collection_dir = tmp / "collection" / "bilibili" / "2026-05-24"
            collection_dir.mkdir(parents=True)
            inbox_dir = tmp / "inbox"
            inbox_dir.mkdir()

            # 两个旧文件
            (collection_dir / "title-one_BV1xx.md").write_text(
                "# 标题一\n\n正文一", encoding="utf-8"
            )
            (collection_dir / "title-two_BV2xx.md").write_text(
                "# 标题二\n\n正文二", encoding="utf-8"
            )

            importer = LegacyCollectionImporter(
                collection_root=collection_dir.parent.parent,
                inbox_dir=inbox_dir,
            )
            result = importer.import_sources(["bilibili"])

            self.assertEqual(result.imported_count, 2)
            self.assertEqual(result.skipped_count, 0)
            # inbox 中应有 2 个文件
            imported_files = list(inbox_dir.glob("*.md"))
            self.assertEqual(len(imported_files), 2)

    def test_importer_skips_duplicates_using_source_hash(self):
        """同一文件第二次导入应被跳过（基于内容哈希去重）。"""
        from app.services.knowledge_pipeline.legacy_import import LegacyCollectionImporter

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            collection_dir = tmp / "collection" / "instapaper" / "2026-05-20"
            collection_dir.mkdir(parents=True)
            inbox_dir = tmp / "inbox"
            inbox_dir.mkdir()
            hash_store = tmp / "import_hashes.json"

            (collection_dir / "article_one.md").write_text(
                "# 文章\n\n正文", encoding="utf-8"
            )

            importer = LegacyCollectionImporter(
                collection_root=collection_dir.parent.parent,
                inbox_dir=inbox_dir,
                hash_store_path=hash_store,
            )
            # First import
            result1 = importer.import_sources(["instapaper"])
            self.assertEqual(result1.imported_count, 1)

            # Second import - same content - should skip
            result2 = importer.import_sources(["instapaper"])
            self.assertEqual(result2.imported_count, 0)
            self.assertEqual(result2.skipped_count, 1)

    def test_importer_adds_frontmatter_to_files_without_it(self):
        """没有 frontmatter 的旧文件在导入时应自动补写 frontmatter。"""
        from app.services.knowledge_pipeline.legacy_import import LegacyCollectionImporter

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            collection_dir = tmp / "collection" / "bilibili" / "2026-01-01"
            collection_dir.mkdir(parents=True)
            inbox_dir = tmp / "inbox"
            inbox_dir.mkdir()

            (collection_dir / "old-article_BV1xx.md").write_text(
                "# 旧文章标题\n\n正文内容", encoding="utf-8"
            )

            importer = LegacyCollectionImporter(
                collection_root=collection_dir.parent.parent,
                inbox_dir=inbox_dir,
            )
            importer.import_sources(["bilibili"])

            imported = list(inbox_dir.glob("*.md"))
            self.assertEqual(len(imported), 1)
            content = imported[0].read_text(encoding="utf-8")
            self.assertTrue(content.startswith("---\n"))

    def test_inbox_entry_model_has_required_fields(self):
        """InboxEntry ORM 模型包含规划要求的所有字段。"""
        from app.models import InboxEntry

        entry = InboxEntry()
        for field in [
            "source_platform",
            "source_identifier",
            "inbox_path",
            "archive_path",
            "content_hash",
            "status",
            "category",
            "topics_json",
            "quality_score",
            "error_message",
            "processed_at",
        ]:
            self.assertTrue(
                hasattr(entry, field),
                f"InboxEntry missing field: {field}",
            )


if __name__ == "__main__":
    unittest.main()
