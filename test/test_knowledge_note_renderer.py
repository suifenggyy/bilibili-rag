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

    def test_renderer_uses_vault_relative_source_inbox_path_in_frontmatter(self):
        """source_inbox_path in frontmatter should be vault-relative, not absolute."""
        units = DistilledKnowledge(
            source_identity={
                "source_inbox_path": "/Users/gongyongyue/Obsidian/jarvis/inbox/douyin/2026-06-03/test.md",
                "published_date": "2026-06-03",
                "title": "T",
                "source_url": "",
            },
            summary="s",
            concepts=[], methods=[], decision_rules=[], examples=[], risks=[], quotes=[],
            source_excerpt_fingerprints=[]
        )
        markdown = KnowledgeNoteRenderer().render(units, "id-1")
        # Frontmatter should have vault-relative path
        self.assertIn("inbox/douyin/2026-06-03/test.md", markdown)
        # Should NOT contain the absolute path
        self.assertNotIn("/Users/gongyongyue/Obsidian/jarvis/inbox", markdown)

    def test_renderer_wikilink_strips_special_chars(self):
        """Wikilinks should not contain ? or # which break Obsidian parsing."""
        units = DistilledKnowledge(
            source_identity={
                "source_inbox_path": "/Users/gongyongyue/Obsidian/jarvis/inbox/douyin/2026-06-03/财富自由要靠投资而不是工作？ #财富思维 #财富自由_7645370844755037475.md",
                "published_date": "2026-06-03",
                "title": "财富自由要靠投资而不是工作",
                "source_url": "https://www.douyin.com/video/7645370844755037475",
            },
            summary="s",
            concepts=[], methods=[], decision_rules=[], examples=[], risks=[], quotes=[],
            source_excerpt_fingerprints=[]
        )
        markdown = KnowledgeNoteRenderer().render(units, "id-2")
        # The [[wikilink]] should not contain ? or # (half or full width)
        import re
        wikilinks = re.findall(r'\[\[(.+?)\]\]', markdown)
        self.assertTrue(len(wikilinks) >= 1, "Expected at least one wikilink")
        for link in wikilinks:
            self.assertNotIn("?", link, f"Wikilink should not contain '?': [[{link}]]")
            self.assertNotIn("？", link, f"Wikilink should not contain '？': [[{link}]]")
            self.assertNotIn("#", link, f"Wikilink should not contain '#': [[{link}]]")

    def test_source_inbox_path_to_obsidian_link(self):
        """Test the path conversion helper."""
        # Absolute path with special chars
        sanitized, display = KnowledgeNoteRenderer._source_inbox_path_to_obsidian_link(
            "/Users/gongyongyue/Obsidian/jarvis/inbox/douyin/2026-06-03/财富自由要靠投资而不是工作？ #财富思维 #财富自由_7645370844755037475.md"
        )
        self.assertNotIn("?", sanitized)
        self.assertNotIn("？", sanitized)
        self.assertNotIn("#", sanitized)
        self.assertNotIn("/Users/", sanitized)
        self.assertIn("inbox/", sanitized)
        self.assertIn("财富自由", display)

        # Simple path without special chars
        sanitized2, display2 = KnowledgeNoteRenderer._source_inbox_path_to_obsidian_link(
            "inbox/douyin/test.md"
        )
        self.assertEqual(sanitized2, "inbox/douyin/test.md")
        self.assertEqual(display2, "test")

        # Path that's already vault-relative
        sanitized3, display3 = KnowledgeNoteRenderer._source_inbox_path_to_obsidian_link(
            "inbox/a.md"
        )
        self.assertIn("inbox/a.md", sanitized3)
        self.assertEqual(display3, "a")
