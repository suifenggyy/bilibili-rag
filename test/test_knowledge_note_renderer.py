import unittest
from app.services.knowledge_pipeline.knowledge_note_renderer import KnowledgeNoteRenderer
from app.services.knowledge_pipeline.knowledge_distiller import DistilledKnowledge
from app.services.knowledge_pipeline.topic_graph import GraphPlacementResult, SecondaryPlacementResult


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

    def test_renderer_includes_secondary_paths_and_generation_version(self):
        units = DistilledKnowledge(
            source_identity={"source_inbox_path": "inbox/a.md", "published_date": "2026-06-03", "title": "T", "source_url": "https://a"},
            summary="This is summary",
            concepts=["概念1"],
            methods=["方法1"],
            decision_rules=[],
            examples=[],
            risks=[],
            quotes=[],
            source_excerpt_fingerprints=[]
        )
        placement = GraphPlacementResult(
            canonical_primary_path=["投资", "短线交易"],
            canonical_primary_node_id="node-1",
            placement_path=["投资", "短线交易"],
            placement_mode="canonical",
            deferred_primary_path=None,
            highest_confidence_replacement_path=None,
            secondary_placements=[
                SecondaryPlacementResult(
                    requested_path=["投资", "风险控制"],
                    canonical_node_id="node-2",
                    placement_path=["投资", "风险控制"],
                    placement_mode="canonical",
                    deferred_path=None,
                )
            ],
            secondary_node_ids=["node-2"],
            ancestor_node_ids=["node-root", "node-1"],
            secondary_ancestor_node_ids=["node-root"],
            deferred_mutation_records=[],
        )
        markdown = KnowledgeNoteRenderer().render(units, "note-id-abc", placement=placement)
        self.assertIn("secondary_topic_paths:", markdown)
        self.assertIn("generation_version:", markdown)
        self.assertIn("deferred_primary_path:", markdown)
        self.assertIn("primary_topic_path:", markdown)
        self.assertIn("topic_node_ids:", markdown)
        self.assertIn("generated_at:", markdown)
