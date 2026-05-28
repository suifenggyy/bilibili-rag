"""
知识库流水线 Orchestrator 测试
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch


class KnowledgePipelineOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_files_runs_parse_classify_archive_topic_log_flow(self):
        from app.services.knowledge_pipeline.orchestrator import (
            KnowledgePipelineOrchestrator,
            PipelineResult,
        )
        from app.services.knowledge_pipeline.classifier import ClassificationResult

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            inbox_dir = tmp / "inbox"
            knowledge_dir = tmp / "knowledge"
            inbox_dir.mkdir()

            # Create a valid inbox file
            md_file = inbox_dir / "2026-05-28-test.md"
            md_file.write_text(
                "---\ntitle: Test\ndate: 2026-05-28\nsource: https://x.com\nsummary: s\n---\n\n# Test\n\n正文",
                encoding="utf-8",
            )

            fake_classification = ClassificationResult(
                category="AI与技术",
                topics=["AI大模型"],
                quality_score=0.8,
                processing_log="ok",
            )

            mock_classifier = MagicMock()
            mock_classifier.classify = AsyncMock(return_value=fake_classification)

            orchestrator = KnowledgePipelineOrchestrator(
                vault_root=tmp,
                knowledge_dir=knowledge_dir,
                classifier=mock_classifier,
            )
            result = await orchestrator.process_files([md_file])

            self.assertIsInstance(result, PipelineResult)
            self.assertEqual(result.completed, 1)
            self.assertEqual(result.failed, 0)

    async def test_process_files_counts_failed_on_parse_error(self):
        from app.services.knowledge_pipeline.orchestrator import (
            KnowledgePipelineOrchestrator,
        )

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            inbox_dir = tmp / "inbox"
            inbox_dir.mkdir()

            # Bad file - no frontmatter
            bad_file = inbox_dir / "bad.md"
            bad_file.write_text("# No frontmatter\n\n正文", encoding="utf-8")

            orchestrator = KnowledgePipelineOrchestrator(
                vault_root=tmp,
                knowledge_dir=tmp / "knowledge",
            )
            result = await orchestrator.process_files([bad_file])
            self.assertEqual(result.failed, 1)
            self.assertEqual(result.completed, 0)

    async def test_process_inbox_finds_all_inbox_markdown_files(self):
        from app.services.knowledge_pipeline.orchestrator import (
            KnowledgePipelineOrchestrator,
        )
        from app.services.knowledge_pipeline.classifier import ClassificationResult

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            inbox_dir = tmp / "inbox"
            inbox_dir.mkdir()

            for i in range(3):
                f = inbox_dir / f"2026-05-28-article{i}.md"
                f.write_text(
                    f"---\ntitle: 文章{i}\ndate: 2026-05-28\nsource: https://x.com/{i}\nsummary: s\n---\n\n# 文章{i}",
                    encoding="utf-8",
                )

            fake_cl = ClassificationResult(
                category="技术", topics=[], quality_score=0.5, processing_log="ok"
            )
            mock_classifier = MagicMock()
            mock_classifier.classify = AsyncMock(return_value=fake_cl)

            orchestrator = KnowledgePipelineOrchestrator(
                vault_root=tmp,
                knowledge_dir=tmp / "knowledge",
                inbox_dir=inbox_dir,
                classifier=mock_classifier,
            )
            result = await orchestrator.process_inbox()
            self.assertEqual(result.completed, 3)


if __name__ == "__main__":
    unittest.main()
