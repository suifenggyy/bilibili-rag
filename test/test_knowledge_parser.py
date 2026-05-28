"""
知识库 Markdown 解析器和分类图谱测试
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class KnowledgeParserTests(unittest.TestCase):
    def test_parser_reads_frontmatter_and_body(self):
        markdown = """---
title: 标题
date: 2026-05-28
source: https://example.com
summary: 摘要
---

# 标题

正文
"""
        from app.services.knowledge_pipeline.parser import KnowledgeMarkdownParser

        article = KnowledgeMarkdownParser().parse_text(markdown)
        self.assertEqual(article.title, "标题")
        self.assertEqual(article.summary, "摘要")
        self.assertEqual(article.body.strip(), "# 标题\n\n正文")

    def test_parser_raises_on_missing_frontmatter(self):
        from app.services.knowledge_pipeline.parser import (
            KnowledgeMarkdownParser,
            FrontmatterMissingError,
        )

        with self.assertRaises(FrontmatterMissingError):
            KnowledgeMarkdownParser().parse_text("# 无 frontmatter\n\n正文")

    def test_parser_raises_on_missing_required_fields(self):
        from app.services.knowledge_pipeline.parser import (
            KnowledgeMarkdownParser,
            FrontmatterFieldMissingError,
        )

        md = "---\ntitle: 标题\n---\n\n# 标题"
        with self.assertRaises(FrontmatterFieldMissingError):
            KnowledgeMarkdownParser().parse_text(md)

    def test_parser_allows_empty_summary(self):
        from app.services.knowledge_pipeline.parser import KnowledgeMarkdownParser

        md = '---\ntitle: T\ndate: 2026-01-01\nsource: https://x.com\nsummary: ""\n---\n\n# T'
        article = KnowledgeMarkdownParser().parse_text(md)
        self.assertEqual(article.summary, "")

    def test_parser_exposes_raw_frontmatter_dict(self):
        from app.services.knowledge_pipeline.parser import KnowledgeMarkdownParser

        md = "---\ntitle: T\ndate: 2026-01-01\nsource: https://x.com\nsummary: s\nplatform: bilibili\n---\n\n# T"
        article = KnowledgeMarkdownParser().parse_text(md)
        self.assertEqual(article.raw_frontmatter.get("platform"), "bilibili")


class CategoryMapRepositoryTests(unittest.TestCase):
    def test_category_map_creates_file_on_first_access(self):
        from app.services.knowledge_pipeline.category_map import CategoryMapRepository

        with TemporaryDirectory() as tmpdir:
            meta_dir = Path(tmpdir) / "_meta"
            repo = CategoryMapRepository(meta_dir=meta_dir)
            categories = repo.list_categories()
            self.assertIsInstance(categories, list)
            self.assertTrue((meta_dir / "category-map.json").exists())

    def test_category_map_prefers_existing_category_names(self):
        from app.services.knowledge_pipeline.category_map import CategoryMapRepository

        with TemporaryDirectory() as tmpdir:
            meta_dir = Path(tmpdir) / "_meta"
            repo = CategoryMapRepository(meta_dir=meta_dir)
            repo.merge_classification("AI与技术", ["AI大模型"])
            repo.save()

            # New repo instance reads from file
            repo2 = CategoryMapRepository(meta_dir=meta_dir)
            cats = repo2.list_categories()
            self.assertIn("AI与技术", cats)
            topics = repo2.get_topics("AI与技术")
            self.assertIn("AI大模型", topics)

    def test_category_map_deduplicates_topics(self):
        from app.services.knowledge_pipeline.category_map import CategoryMapRepository

        with TemporaryDirectory() as tmpdir:
            meta_dir = Path(tmpdir) / "_meta"
            repo = CategoryMapRepository(meta_dir=meta_dir)
            repo.merge_classification("技术", ["Python", "Python", "Go"])
            topics = repo.get_topics("技术")
            self.assertEqual(topics.count("Python"), 1)

    def test_category_map_merge_adds_new_topics(self):
        from app.services.knowledge_pipeline.category_map import CategoryMapRepository

        with TemporaryDirectory() as tmpdir:
            meta_dir = Path(tmpdir) / "_meta"
            repo = CategoryMapRepository(meta_dir=meta_dir)
            repo.merge_classification("技术", ["Python"])
            repo.merge_classification("技术", ["Go", "Rust"])
            topics = repo.get_topics("技术")
            self.assertIn("Python", topics)
            self.assertIn("Go", topics)
            self.assertIn("Rust", topics)


if __name__ == "__main__":
    unittest.main()
