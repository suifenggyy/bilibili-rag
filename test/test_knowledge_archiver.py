"""
归档器和 Topic 更新器测试
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch


class KnowledgeArchiverTests(unittest.IsolatedAsyncioTestCase):
    async def test_archiver_writes_processed_frontmatter_to_category_folder(self):
        from app.services.knowledge_pipeline.archiver import KnowledgeArchiver
        from app.services.knowledge_pipeline.parser import ParsedKnowledgeDocument
        from app.services.knowledge_pipeline.classifier import ClassificationResult

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            inbox_dir = tmp / "inbox"
            inbox_dir.mkdir()
            knowledge_dir = tmp / "knowledge"

            # Create a test inbox file
            inbox_file = inbox_dir / "2026-05-28-test-article.md"
            inbox_file.write_text(
                "---\ntitle: 文章标题\ndate: 2026-05-28\nsource: https://example.com\nsummary: 摘要\n---\n\n# 文章标题\n\n正文",
                encoding="utf-8",
            )

            doc = ParsedKnowledgeDocument(
                title="文章标题",
                date_str="2026-05-28",
                source_url="https://example.com",
                summary="摘要",
                body="# 文章标题\n\n正文",
                raw_frontmatter={},
            )
            result = ClassificationResult(
                category="AI与技术",
                topics=["AI大模型"],
                quality_score=0.85,
                processing_log="依据：摘要提及技术主题",
            )

            archiver = KnowledgeArchiver(knowledge_dir=knowledge_dir)
            archive_path = await archiver.archive(
                inbox_path=inbox_file,
                doc=doc,
                classification=result,
            )

            self.assertTrue(archive_path.exists())
            written_markdown = archive_path.read_text(encoding="utf-8")
            self.assertIn("category: AI与技术", written_markdown)
            self.assertIn("AI大模型", written_markdown)
            self.assertTrue(str(archive_path).endswith(".md"))
            # Category folder should be under knowledge/AI与技术/
            self.assertIn("AI与技术", str(archive_path))

    async def test_archiver_generates_dated_slug_filename(self):
        from app.services.knowledge_pipeline.archiver import KnowledgeArchiver
        from app.services.knowledge_pipeline.parser import ParsedKnowledgeDocument
        from app.services.knowledge_pipeline.classifier import ClassificationResult

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            inbox_file = tmp / "inbox" / "2026-05-28-文章.md"
            inbox_file.parent.mkdir()
            inbox_file.write_text(
                "---\ntitle: 文章\ndate: 2026-05-28\nsource: https://x.com\nsummary: s\n---\n\n正文",
                encoding="utf-8",
            )
            doc = ParsedKnowledgeDocument(
                title="文章",
                date_str="2026-05-28",
                source_url="https://x.com",
                summary="s",
                body="正文",
            )
            cl = ClassificationResult(
                category="未分类", topics=[], quality_score=0.5, processing_log="ok"
            )
            archiver = KnowledgeArchiver(knowledge_dir=tmp / "knowledge")
            path = await archiver.archive(inbox_file, doc, cl)
            self.assertTrue(path.name.startswith("2026-05-28-"))


class TopicUpdaterTests(unittest.IsolatedAsyncioTestCase):
    async def test_topic_updater_creates_topic_file_with_dataview_block(self):
        from app.services.knowledge_pipeline.topic_updater import TopicUpdater

        with TemporaryDirectory() as tmpdir:
            topics_dir = Path(tmpdir) / "knowledge" / "_topics"
            updater = TopicUpdater(topics_dir=topics_dir)
            await updater.update_topic(
                topic="AI大模型",
                article_title="GPT-4o 指南",
                article_date="2026-05-28",
                new_insight="摘要提及 GPT-4o 与 Prompt 优化",
            )

            topic_file = topics_dir / "AI大模型.md"
            self.assertTrue(topic_file.exists())
            content = topic_file.read_text(encoding="utf-8")
            self.assertIn('LIST FROM "knowledge"', content)
            self.assertIn("AI大模型", content)

    async def test_topic_updater_appends_new_insight_without_overwriting(self):
        from app.services.knowledge_pipeline.topic_updater import TopicUpdater

        with TemporaryDirectory() as tmpdir:
            topics_dir = Path(tmpdir) / "_topics"
            updater = TopicUpdater(topics_dir=topics_dir)

            await updater.update_topic(
                topic="Python",
                article_title="文章A",
                article_date="2026-05-01",
                new_insight="第一次更新",
            )
            await updater.update_topic(
                topic="Python",
                article_title="文章B",
                article_date="2026-05-28",
                new_insight="第二次更新",
            )

            content = (topics_dir / "Python.md").read_text(encoding="utf-8")
            self.assertIn("第一次更新", content)
            self.assertIn("第二次更新", content)


if __name__ == "__main__":
    unittest.main()
