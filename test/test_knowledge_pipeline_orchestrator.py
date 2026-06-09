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
