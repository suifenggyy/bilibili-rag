import unittest
from app.services.knowledge_pipeline.knowledge_note_renderer import KnowledgeNoteRenderer
from app.services.knowledge_pipeline.knowledge_distiller import DistilledKnowledge

class KnowledgeNoteRendererTests(unittest.TestCase):
    def test_renderer_excludes_full_source_body(self):
        units = DistilledKnowledge(
            source_identity={"source_inbox_path": "inbox/a.md", "published_date": "2026-06-03", "title": "T", "source_url": "https://a"},
            summary="This is summary", 
            concepts=[], 
            methods=[], 
            decision_rules=[], 
            examples=[], 
            risks=[], 
            quotes=[], 
            source_excerpt_fingerprints=[]
        )
        markdown = KnowledgeNoteRenderer().render(units, "topic-id-123")
        self.assertIn("## 核心结论", markdown)
        self.assertNotIn("原始正文整段", markdown)
