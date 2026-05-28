"""
Topic 更新器测试 (additional standalone tests)
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class TopicUpdaterTests(unittest.IsolatedAsyncioTestCase):
    async def test_topic_updater_idempotent_same_article(self):
        """同一篇文章同一 topic 重复更新，不应产生重复观点行。"""
        from app.services.knowledge_pipeline.topic_updater import TopicUpdater

        with TemporaryDirectory() as tmpdir:
            topics_dir = Path(tmpdir) / "_topics"
            updater = TopicUpdater(topics_dir=topics_dir)

            for _ in range(2):
                await updater.update_topic(
                    topic="Go",
                    article_title="Go 并发教程",
                    article_date="2026-05-28",
                    new_insight="goroutine 并发模型",
                    article_link="[[Go 并发教程]]",
                )

            content = (topics_dir / "Go.md").read_text(encoding="utf-8")
            self.assertEqual(content.count("goroutine 并发模型"), 1)


if __name__ == "__main__":
    unittest.main()
