"""
知识库日报生成器测试
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch


class DailyReporterTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_reporter_builds_focus_today_trends_and_watchlist_sections(self):
        from app.services.knowledge_pipeline.daily_reporter import DailyReporter

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            knowledge_dir = tmp / "knowledge"
            daily_dir = tmp / "daily"

            # Create a test article archived today
            cat_dir = knowledge_dir / "AI与技术"
            cat_dir.mkdir(parents=True)
            article = cat_dir / "2026-05-28-test-article.md"
            article.write_text(
                "---\ntitle: AI模型测试\ndate: 2026-05-28\nsource: https://x.com/a\n"
                "category: AI与技术\ntopics: [AI大模型, 深度学习]\nquality_score: 0.9\n---\n\n# AI模型测试\n正文",
                encoding="utf-8",
            )

            reporter = DailyReporter(
                knowledge_dir=knowledge_dir,
                daily_dir=daily_dir,
                tavily_api_key="",  # no external calls in unit test
            )
            report = await reporter.generate(day=date(2026, 5, 28))

            self.assertIn("# 知识库日报 2026-05-28", report)
            self.assertIn("## 重点关注", report)
            self.assertIn("## 今日新增", report)
            self.assertIn("## 近期趋势", report)
            self.assertIn("## 待关注信号", report)

    async def test_daily_reporter_saves_to_daily_dir(self):
        from app.services.knowledge_pipeline.daily_reporter import DailyReporter

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            knowledge_dir = tmp / "knowledge"
            knowledge_dir.mkdir()
            daily_dir = tmp / "daily"

            reporter = DailyReporter(
                knowledge_dir=knowledge_dir,
                daily_dir=daily_dir,
                tavily_api_key="",
            )
            await reporter.generate_and_save(day=date(2026, 5, 28))

            saved = daily_dir / "2026-05-28.md"
            self.assertTrue(saved.exists(), "日报文件应被写入 daily/YYYY-MM-DD.md")
            content = saved.read_text(encoding="utf-8")
            self.assertIn("# 知识库日报 2026-05-28", content)

    async def test_daily_reporter_counts_todays_articles(self):
        from app.services.knowledge_pipeline.daily_reporter import DailyReporter

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            knowledge_dir = tmp / "knowledge"
            cat_dir = knowledge_dir / "AI与技术"
            cat_dir.mkdir(parents=True)

            for i in range(3):
                f = cat_dir / f"2026-05-28-article{i}.md"
                f.write_text(
                    f"---\ntitle: 文章{i}\ndate: 2026-05-28\nsource: https://x.com/{i}\n"
                    f"category: AI与技术\ntopics: [AI]\nquality_score: 0.7\n---\n\n# 文章{i}",
                    encoding="utf-8",
                )

            reporter = DailyReporter(
                knowledge_dir=knowledge_dir,
                daily_dir=tmp / "daily",
                tavily_api_key="",
            )
            signals = await reporter.collect_internal_topic_signals(day=date(2026, 5, 28))

            # AI topic should have 3 signals from today's articles
            topic_names = [s.topic for s in signals]
            self.assertIn("AI", topic_names)
            ai_signal = next(s for s in signals if s.topic == "AI")
            self.assertGreater(ai_signal.score, 0)


if __name__ == "__main__":
    unittest.main()
