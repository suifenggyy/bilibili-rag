import unittest
from unittest.mock import AsyncMock
from app.services.knowledge_pipeline.knowledge_distiller import KnowledgeDistiller
from app.services.knowledge_pipeline.parser import ParsedKnowledgeDocument

class KnowledgeDistillerTests(unittest.IsolatedAsyncioTestCase):
    async def test_distiller_returns_structured_units(self):
        fake_processor = AsyncMock(return_value={
            "summary": "副业筛选应先看时间/专业度匹配",
            "concepts": [],
            "methods": ["四象限筛选"],
            "decision_rules": [],
            "examples": ["真实案例"],
            "risks": ["风险控制"],
            "quotes": [{"text": "不要一开始就追求三角全占"}]
        })
        doc = ParsedKnowledgeDocument(title="T", date_str="2026-06-03", source_url="https://x.com", summary="", body="充足的内容", raw_frontmatter={}, key_points=[])
        source_identity = {"source_inbox_path": "inbox/douyin/a.md", "published_date": "2026-06-03"}
        result = await KnowledgeDistiller(fake_processor, min_body_chars=2).distill(doc, source_identity)
        self.assertEqual(result.status, "processed")
        self.assertEqual(result.knowledge.summary, "副业筛选应先看时间/专业度匹配")
        self.assertEqual(result.knowledge.methods[0], "四象限筛选")
        self.assertIn("风险控制", result.knowledge.risks)
        self.assertIn("真实案例", result.knowledge.examples)
        self.assertEqual(result.knowledge.quotes[0]["text"], "不要一开始就追求三角全占")

    async def test_distiller_marks_weak_signal_as_skipped(self):
        fake_processor = AsyncMock()
        doc = ParsedKnowledgeDocument(title="T", date_str="2026-06-03", source_url="https://x.com", summary="", body="太短", raw_frontmatter={}, key_points=[])
        source_identity = {"source_inbox_path": "inbox/douyin/a.md", "published_date": "2026-06-03"}
        result = await KnowledgeDistiller(fake_processor, min_body_chars=80).distill(doc, source_identity)
        self.assertEqual(result.status, "skipped")

    async def test_distiller_uses_parser_summary_when_body_is_short(self):
        fake_processor = AsyncMock(return_value={
            "summary": "有效摘要",
            "concepts": [],
            "methods": [],
            "decision_rules": [],
            "examples": [],
            "risks": [],
            "quotes": []
        })
        doc = ParsedKnowledgeDocument(title="T", date_str="2026-06-03", source_url="https://x.com", summary="这是一条有效摘要", body="短", raw_frontmatter={}, key_points=[])
        source_identity = {"source_inbox_path": "inbox/douyin/a.md", "published_date": "2026-06-03"}
        result = await KnowledgeDistiller(fake_processor, min_body_chars=80).distill(doc, source_identity)
        self.assertEqual(result.status, "processed")

    async def test_distiller_marks_processor_failure_as_failed(self):
        fake_processor = AsyncMock(side_effect=RuntimeError("model timeout"))
        doc = ParsedKnowledgeDocument(title="T", date_str="2026-06-03", source_url="https://x.com", summary="", body="充足的内容" * 10, raw_frontmatter={}, key_points=[])
        source_identity = {"source_inbox_path": "inbox/douyin/a.md", "published_date": "2026-06-03"}
        result = await KnowledgeDistiller(fake_processor, min_body_chars=2).distill(doc, source_identity)
        self.assertEqual(result.status, "failed")
