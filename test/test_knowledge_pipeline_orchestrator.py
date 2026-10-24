import unittest
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

class KnowledgePipelineOrchestratorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_orchestrator_generates_note_graph_and_topic_updates(self):
        from app.services.knowledge_pipeline.orchestrator import KnowledgePipelineOrchestrator
        from app.services.knowledge_pipeline.classifier import ClassificationResult
        from app.services.knowledge_pipeline.parser import ParsedKnowledgeDocument

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            inbox_dir = tmp / "inbox"
            knowledge_dir = tmp / "knowledge"
            meta_dir = tmp / "_meta"
            inbox_dir.mkdir()
            meta_dir.mkdir()
            knowledge_dir.mkdir()

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
                inbox_dir=inbox_dir,
                classifier=mock_classifier,
                meta_dir=meta_dir
            )

            # Inject test processors for distiller and resolver
            orchestrator._distill_processor = AsyncMock(return_value={
                "summary": "Mock summary for test",
                "concepts": ["概念1"],
                "methods": ["方法1"],
                "decision_rules": [],
                "examples": [],
                "risks": [],
                "quotes": [],
            })
            orchestrator._path_processor = AsyncMock(return_value={
                "primary_path": ["技术", "AI"],
                "secondary_paths": [],
                "mutation_proposals": [
                    {
                        "type": "create_leaf",
                        "target_parent_path": ["技术"],
                        "target_name": "AI",
                        "target_paths": [["技术", "AI"]],
                        "confidence": 0.9,
                        "reason": "test"
                    }
                ],
            })
            
            # This requires actual wiring inside the orchestrator
            result = await orchestrator.process_files([md_file])
            if result.file_results and not result.file_results[0].success:
                print(f"File processing error: {result.file_results[0].error}")

            self.assertEqual(result.completed, 1)
            self.assertTrue((meta_dir / "topic-graph.json").exists())
            self.assertTrue((meta_dir / "source-topic-map.json").exists())
            # Knowledge note should exist
            notes = list(knowledge_dir.rglob("*.md"))
            self.assertTrue(len(notes) > 0)

    async def test_orchestrator_writes_source_progress_log(self):
        from app.services.knowledge_pipeline.orchestrator import KnowledgePipelineOrchestrator
        from app.services.knowledge_pipeline.classifier import ClassificationResult

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            inbox_dir = tmp / "inbox"
            knowledge_dir = tmp / "knowledge"
            meta_dir = tmp / "_meta"
            knowledge_dir.mkdir(parents=True)
            meta_dir.mkdir(parents=True)

            # 嵌套路径 inbox/bilibili/2026-06-12/，且放两个文件，只处理一个 → 未完成态。
            date_dir = inbox_dir / "bilibili" / "2026-06-12"
            date_dir.mkdir(parents=True)
            md_body = (
                "---\ntitle: Test\ndate: 2026-06-12\nsource: https://x.com\n"
                "summary: s\n---\n\n# Test\n\n正文"
            )
            md_file = date_dir / "file1.md"
            md_file.write_text(md_body, encoding="utf-8")
            (date_dir / "file2.md").write_text(md_body, encoding="utf-8")

            mock_classifier = MagicMock()
            mock_classifier.classify = AsyncMock(return_value=ClassificationResult(
                category="AI与技术", topics=["AI大模型"], quality_score=0.8, processing_log="ok"
            ))

            orchestrator = KnowledgePipelineOrchestrator(
                vault_root=tmp,
                knowledge_dir=knowledge_dir,
                inbox_dir=inbox_dir,
                classifier=mock_classifier,
                meta_dir=meta_dir,
            )
            orchestrator._distill_processor = AsyncMock(return_value={
                "summary": "Mock summary for test",
                "concepts": ["概念1"],
                "methods": ["方法1"],
                "decision_rules": [],
                "examples": [],
                "risks": [],
                "quotes": [],
            })
            orchestrator._path_processor = AsyncMock(return_value={
                "primary_path": ["技术", "AI"],
                "secondary_paths": [],
                "mutation_proposals": [
                    {
                        "type": "create_leaf",
                        "target_parent_path": ["技术"],
                        "target_name": "AI",
                        "target_paths": [["技术", "AI"]],
                        "confidence": 0.9,
                        "reason": "test",
                    }
                ],
            })

            result = await orchestrator.process_files([md_file])
            if result.file_results and not result.file_results[0].success:
                print(f"File processing error: {result.file_results[0].error}")
            self.assertEqual(result.completed, 1)

            # source 目录下应生成进度日志，记录已处理的 file1，且因 file2 未处理而保持未完成。
            progress_log = inbox_dir / "bilibili" / "_pipeline_progress.log"
            self.assertTrue(progress_log.exists())
            text = progress_log.read_text(encoding="utf-8")
            self.assertIn("2026-06-12", text)
            self.assertIn("  - file1.md", text)
            self.assertNotIn("  - file2.md", text)

    async def test_orchestrator_skips_file_when_body_fetch_failed(self):
        from app.services.knowledge_pipeline.orchestrator import KnowledgePipelineOrchestrator

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            inbox_dir = tmp / "inbox"
            knowledge_dir = tmp / "knowledge"
            meta_dir = tmp / "_meta"
            inbox_dir.mkdir(parents=True)
            knowledge_dir.mkdir(parents=True)
            meta_dir.mkdir(parents=True)

            # Simulate a video whose ASR failed — export script writes a placeholder body.
            md_file = inbox_dir / "2026-06-12-failed.md"
            md_file.write_text(
                "---\ntitle: Failed\ndate: 2026-06-12\nsource: https://x.com\n"
                "summary: s\n---\n\n# Failed\n\n## 转写内容\n\n_（未获取到有效内容）_\n",
                encoding="utf-8",
            )

            # If the skip works, the distill processor must never be called.
            distill_mock = AsyncMock()
            path_mock = AsyncMock()

            orchestrator = KnowledgePipelineOrchestrator(
                vault_root=tmp,
                knowledge_dir=knowledge_dir,
                inbox_dir=inbox_dir,
                meta_dir=meta_dir,
            )
            orchestrator._distill_processor = distill_mock
            orchestrator._path_processor = path_mock

            result = await orchestrator.process_files([md_file])

            self.assertEqual(result.completed, 0)
            self.assertEqual(result.failed, 1)
            self.assertEqual(result.file_results[0].error, "body_fetch_failed")
            distill_mock.assert_not_called()
            path_mock.assert_not_called()
            # No knowledge notes should be generated for a skipped file.
            self.assertEqual(list(knowledge_dir.rglob("*.md")), [])

    async def test_orchestrator_skips_instapaper_article_extraction_failed(self):
        from app.services.knowledge_pipeline.orchestrator import KnowledgePipelineOrchestrator

        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            inbox_dir = tmp / "inbox" / "instapaper"
            knowledge_dir = tmp / "knowledge"
            meta_dir = tmp / "_meta"
            inbox_dir.mkdir(parents=True)
            knowledge_dir.mkdir(parents=True)
            meta_dir.mkdir(parents=True)

            # Simulate an Instapaper article whose body extraction failed.
            md_file = inbox_dir / "2026-06-12-article.md"
            md_file.write_text(
                "---\ntitle: Article\ndate: 2026-06-12\nsource: https://x.com\n"
                "summary: s\n---\n\n# Article\n\n## 正文\n\n"
                "_（正文提取失败，请访问原始链接查看）_\n",
                encoding="utf-8",
            )

            distill_mock = AsyncMock()
            orchestrator = KnowledgePipelineOrchestrator(
                vault_root=tmp,
                knowledge_dir=knowledge_dir,
                inbox_dir=inbox_dir.parent,
                meta_dir=meta_dir,
            )
            orchestrator._distill_processor = distill_mock

            result = await orchestrator.process_files([md_file])

            self.assertEqual(result.completed, 0)
            self.assertEqual(result.file_results[0].error, "body_fetch_failed")
            distill_mock.assert_not_called()
