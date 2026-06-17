import unittest
from unittest.mock import AsyncMock
from app.services.knowledge_pipeline.topic_path_resolver import TopicPathResolver
from app.services.knowledge_pipeline.topic_graph import TopicGraph
from app.services.knowledge_pipeline.knowledge_distiller import DistilledKnowledge
from app.services.knowledge_pipeline.topic_similarity import LLMTopicSimilarityChecker, SimilarityCandidate, SimilarityResult

class TopicPathResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolver_returns_primary_and_secondary_paths(self):
        fake_processor = AsyncMock(return_value={
            "primary_path": ["投资", "股票交易", "做T"],
            "secondary_paths": [["投资", "风险控制"]],
            "mutation_proposals": []
        })
        units = DistilledKnowledge(
            source_identity={"source_inbox_path": "inbox/a.md", "published_date": "2026-06-03"},
            summary="", concepts=[], methods=[], decision_rules=[], examples=[], risks=[], quotes=[], source_excerpt_fingerprints=[]
        )
        graph = TopicGraph.empty()
        graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        graph.create_node(name="股票交易", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        graph.create_node(name="做T", parent_path=["投资", "股票交易"], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        graph.create_node(name="风险控制", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        
        result = await TopicPathResolver(fake_processor).resolve(units, graph)
        self.assertEqual(result.requested_primary_path, ["投资", "股票交易", "做T"])
        self.assertIn(["投资", "风险控制"], result.secondary_paths)

    async def test_graph_finalizes_deferred_primary_path_to_nearest_existing_canonical_ancestor(self):
        graph = TopicGraph.empty()
        graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        graph.create_node(name="短线交易", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        
        fake_processor = AsyncMock(return_value={
            "primary_path": ["投资", "短线交易", "做T"],
            "secondary_paths": [],
            "mutation_proposals": [
                {
                    "type": "create_leaf", 
                    "confidence": 0.5, # low confidence to force defer
                    "target_parent_path": ["投资", "短线交易"],
                    "target_name": "做T",
                    "reason": "new topic"
                }
            ]
        })
        units = DistilledKnowledge(
            source_identity={"source_inbox_path": "inbox/a.md", "published_date": "2026-06-03"},
            summary="", concepts=[], methods=[], decision_rules=[], examples=[], risks=[], quotes=[], source_excerpt_fingerprints=[]
        )
        resolution = await TopicPathResolver(fake_processor).resolve(units, graph)
        placement = await graph.finalize_resolution(resolution)
        self.assertEqual(placement.canonical_primary_path, ["投资", "短线交易"])
        self.assertEqual(placement.placement_path, ["投资", "短线交易"])
        self.assertEqual(placement.deferred_primary_path, ["投资", "短线交易", "做T"])
        self.assertEqual(placement.placement_mode, "deferred_to_existing_ancestor")
        fake_processor = AsyncMock(return_value={
            "primary_path": "not-a-list",
            "secondary_paths": [],
            "mutation_proposals": [{"type": "create_leaf", "confidence": "high"}],
        })
        units = DistilledKnowledge(
            source_identity={"source_inbox_path": "inbox/a.md", "published_date": "2026-06-03"},
            summary="", concepts=[], methods=[], decision_rules=[], examples=[], risks=[], quotes=[], source_excerpt_fingerprints=[]
        )
        graph = TopicGraph.empty()
        with self.assertRaisesRegex(ValueError, "invalid topic path payload"):
            await TopicPathResolver(fake_processor).resolve(units, graph)

    async def test_resolver_rejects_invalid_llm_payload(self):
        fake_processor = AsyncMock(return_value={
            "primary_path": "not-a-list",
            "secondary_paths": [],
            "mutation_proposals": [{"type": "create_leaf", "confidence": "high"}],
        })
        units = DistilledKnowledge(
            source_identity={"source_inbox_path": "inbox/a.md", "published_date": "2026-06-03"},
            summary="", concepts=[], methods=[], decision_rules=[], examples=[], risks=[], quotes=[], source_excerpt_fingerprints=[]
        )
        graph = TopicGraph.empty()
        with self.assertRaisesRegex(ValueError, "invalid topic path payload"):
            await TopicPathResolver(fake_processor).resolve(units, graph)

    async def test_resolver_drops_semantically_similar_secondary_with_llm_checker(self):
        """Test that a semantic similarity checker drops secondaries that are
        semantically similar to the primary even when lexical check fails."""
        # Mock LLM dedup processor: says "盘面指标" ≈ "盘口指标"
        mock_dedup = AsyncMock(return_value={
            "results": [
                {"name_a": "盘口指标", "name_b": "盘面指标", "is_similar": True, "confidence": 0.95, "reason": "synonym"},
            ]
        })
        similarity_checker = LLMTopicSimilarityChecker(processor=mock_dedup)

        fake_processor = AsyncMock(return_value={
            "primary_path": ["投资", "盘口指标"],
            "secondary_paths": [["投资", "盘面指标"]],
            "mutation_proposals": []
        })
        units = DistilledKnowledge(
            source_identity={"source_inbox_path": "inbox/a.md", "published_date": "2026-06-03"},
            summary="", concepts=[], methods=[], decision_rules=[], examples=[], risks=[], quotes=[], source_excerpt_fingerprints=[]
        )
        graph = TopicGraph.empty()
        graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        graph.create_node(name="盘口指标", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")

        result = await TopicPathResolver(fake_processor, similarity_checker=similarity_checker).resolve(units, graph)
        # "盘面指标" should be dropped because it's semantically similar to primary "盘口指标"
        self.assertEqual(result.requested_primary_path, ["投资", "盘口指标"])
        self.assertEqual(result.secondary_paths, [])
