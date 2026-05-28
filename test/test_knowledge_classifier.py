"""
知识库分类器测试
"""
import unittest
from unittest.mock import AsyncMock, MagicMock


class KnowledgeClassifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_classifier_returns_category_topics_quality_and_reason(self):
        from app.services.knowledge_pipeline.classifier import (
            KnowledgeClassifier,
            ClassificationResult,
        )

        fake_processor = MagicMock()
        fake_processor.postprocess = AsyncMock(
            return_value="""category: AI与技术
topics:
  - AI大模型
quality_score: 0.85
processing_log: 摘要提及 GPT-4o 与 Prompt 优化
"""
        )
        classifier = KnowledgeClassifier(processor=fake_processor)
        result = await classifier.classify(
            title="GPT-4o 实用指南",
            summary="介绍 GPT-4o 与 Prompt 优化技巧",
            existing_categories=["AI与技术", "生活"],
        )

        self.assertIsInstance(result, ClassificationResult)
        self.assertEqual(result.category, "AI与技术")
        self.assertIn("AI大模型", result.topics)
        self.assertAlmostEqual(result.quality_score, 0.85, places=2)
        self.assertIsInstance(result.processing_log, str)

    async def test_classifier_falls_back_on_llm_error(self):
        from app.services.knowledge_pipeline.classifier import (
            KnowledgeClassifier,
            ClassificationResult,
        )

        fake_processor = MagicMock()
        fake_processor.postprocess = AsyncMock(side_effect=Exception("LLM timeout"))
        classifier = KnowledgeClassifier(processor=fake_processor)

        result = await classifier.classify(
            title="任意标题",
            summary="任意摘要",
            existing_categories=[],
        )

        self.assertIsInstance(result, ClassificationResult)
        self.assertEqual(result.category, "未分类")
        self.assertEqual(result.topics, [])
        self.assertEqual(result.quality_score, 0.0)
        self.assertIn("LLM", result.processing_log)

    async def test_classifier_clamps_quality_score(self):
        from app.services.knowledge_pipeline.classifier import KnowledgeClassifier

        fake_processor = MagicMock()
        # LLM returns score > 1.0
        fake_processor.postprocess = AsyncMock(
            return_value="category: X\ntopics:\n  - Y\nquality_score: 1.5\nprocessing_log: ok\n"
        )
        classifier = KnowledgeClassifier(processor=fake_processor)
        result = await classifier.classify("T", "S", [])
        self.assertLessEqual(result.quality_score, 1.0)

    async def test_classifier_deduplicates_topics(self):
        from app.services.knowledge_pipeline.classifier import KnowledgeClassifier

        fake_processor = MagicMock()
        fake_processor.postprocess = AsyncMock(
            return_value="category: X\ntopics:\n  - Python\n  - Python\n  - Go\nquality_score: 0.5\nprocessing_log: ok\n"
        )
        classifier = KnowledgeClassifier(processor=fake_processor)
        result = await classifier.classify("T", "S", [])
        self.assertEqual(result.topics.count("Python"), 1)


if __name__ == "__main__":
    unittest.main()
