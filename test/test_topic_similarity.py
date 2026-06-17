import unittest
from unittest.mock import AsyncMock, MagicMock

from app.services.knowledge_pipeline.topic_similarity import (
    SimilarityCandidate,
    SimilarityResult,
    LexicalTopicSimilarityChecker,
    LLMTopicSimilarityChecker,
    scan_duplicate_topics,
)
from app.services.knowledge_pipeline.topic_graph import TopicGraph


class SimilarityCandidateTests(unittest.TestCase):
    def test_candidate_stores_fields(self):
        cand = SimilarityCandidate(name_a="盘口指标", name_b="盘面指标", context_a=["投资"], context_b=["投资"])
        self.assertEqual(cand.name_a, "盘口指标")
        self.assertEqual(cand.name_b, "盘面指标")
        self.assertEqual(cand.context_a, ["投资"])


class SimilarityResultTests(unittest.TestCase):
    def test_result_stores_fields(self):
        cand = SimilarityCandidate(name_a="a", name_b="b", context_a=[], context_b=[])
        result = SimilarityResult(candidate=cand, is_similar=True, confidence=0.95, reason="synonym")
        self.assertTrue(result.is_similar)
        self.assertEqual(result.confidence, 0.95)


class LexicalTopicSimilarityCheckerTests(unittest.IsolatedAsyncioTestCase):
    async def test_identical_names_are_similar(self):
        checker = LexicalTopicSimilarityChecker()
        candidates = [
            SimilarityCandidate(name_a="投资", name_b="投资", context_a=[], context_b=[]),
        ]
        results = await checker.check_batch(candidates)
        self.assertTrue(results[0].is_similar)

    async def test_prefix_match_is_similar(self):
        checker = LexicalTopicSimilarityChecker()
        # _leaf_names_are_similar: prefix match after normalization
        candidates = [
            SimilarityCandidate(name_a="副业", name_b="副业决策", context_a=[], context_b=[]),
        ]
        results = await checker.check_batch(candidates)
        self.assertTrue(results[0].is_similar)

    async def test_unrelated_names_are_not_similar(self):
        checker = LexicalTopicSimilarityChecker()
        candidates = [
            SimilarityCandidate(name_a="投资", name_b="健身", context_a=[], context_b=[]),
        ]
        results = await checker.check_batch(candidates)
        self.assertFalse(results[0].is_similar)

    async def test_empty_batch_returns_empty(self):
        checker = LexicalTopicSimilarityChecker()
        results = await checker.check_batch([])
        self.assertEqual(results, [])


class LLMTopicSimilarityCheckerTests(unittest.IsolatedAsyncioTestCase):
    async def test_lexical_match_skips_llm(self):
        """When lexical check says similar, LLM is not called."""
        mock_processor = AsyncMock()
        checker = LLMTopicSimilarityChecker(processor=mock_processor)
        candidates = [
            SimilarityCandidate(name_a="投资", name_b="投资", context_a=[], context_b=[]),
        ]
        results = await checker.check_batch(candidates)
        self.assertTrue(results[0].is_similar)
        mock_processor.assert_not_called()

    async def test_llm_called_for_lexically_different_pairs(self):
        """When lexical check says not similar, LLM is called."""
        mock_processor = AsyncMock(return_value={
            "results": [
                {"name_a": "盘口指标", "name_b": "盘面指标", "is_similar": True, "confidence": 0.95, "reason": "synonym"},
            ]
        })
        checker = LLMTopicSimilarityChecker(processor=mock_processor)
        candidates = [
            SimilarityCandidate(name_a="盘口指标", name_b="盘面指标", context_a=["投资"], context_b=["投资"]),
        ]
        results = await checker.check_batch(candidates)
        self.assertTrue(results[0].is_similar)
        self.assertEqual(results[0].confidence, 0.95)
        mock_processor.assert_called_once()

    async def test_llm_below_threshold_treated_as_not_similar(self):
        """Confidence below 0.8 threshold is treated as not similar."""
        mock_processor = AsyncMock(return_value={
            "results": [
                {"name_a": "投资", "name_b": "理财", "is_similar": True, "confidence": 0.6, "reason": "related but not same"},
            ]
        })
        checker = LLMTopicSimilarityChecker(processor=mock_processor)
        candidates = [
            SimilarityCandidate(name_a="投资", name_b="理财", context_a=[], context_b=[]),
        ]
        results = await checker.check_batch(candidates)
        self.assertFalse(results[0].is_similar)

    async def test_llm_error_falls_back_to_lexical(self):
        """When LLM call fails, fall back to lexical results."""
        mock_processor = AsyncMock(side_effect=Exception("LLM unavailable"))
        checker = LLMTopicSimilarityChecker(processor=mock_processor)
        candidates = [
            SimilarityCandidate(name_a="盘口指标", name_b="盘面指标", context_a=["投资"], context_b=["投资"]),
        ]
        results = await checker.check_batch(candidates)
        # Lexical says not similar (prefix match won't catch this), so fallback says not similar
        self.assertFalse(results[0].is_similar)

    async def test_llm_returns_fewer_results_falls_back(self):
        """When LLM returns fewer results than candidates, fall back for missing ones."""
        mock_processor = AsyncMock(return_value={
            "results": [
                {"name_a": "a", "name_b": "b", "is_similar": True, "confidence": 0.9, "reason": ""},
            ]
        })
        checker = LLMTopicSimilarityChecker(processor=mock_processor)
        candidates = [
            SimilarityCandidate(name_a="a", name_b="b", context_a=[], context_b=[]),
            SimilarityCandidate(name_a="c", name_b="d", context_a=[], context_b=[]),
        ]
        results = await checker.check_batch(candidates)
        self.assertTrue(results[0].is_similar)
        # Second result falls back to lexical (c vs d are not similar)
        self.assertFalse(results[1].is_similar)

    async def test_mixed_lexical_and_llm(self):
        """First candidate matched by lexical, second needs LLM."""
        mock_processor = AsyncMock(return_value={
            "results": [
                {"name_a": "盘口指标", "name_b": "盘面指标", "is_similar": True, "confidence": 0.95, "reason": "synonym"},
            ]
        })
        checker = LLMTopicSimilarityChecker(processor=mock_processor)
        candidates = [
            SimilarityCandidate(name_a="投资", name_b="投资", context_a=[], context_b=[]),  # lexical match
            SimilarityCandidate(name_a="盘口指标", name_b="盘面指标", context_a=["投资"], context_b=["投资"]),  # needs LLM
        ]
        results = await checker.check_batch(candidates)
        self.assertTrue(results[0].is_similar)
        self.assertTrue(results[1].is_similar)
        # LLM only called once (for the second pair)
        mock_processor.assert_called_once()

    async def test_empty_batch_returns_empty(self):
        checker = LLMTopicSimilarityChecker()
        results = await checker.check_batch([])
        self.assertEqual(results, [])


class ScanDuplicateTopicsTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_finds_lexical_duplicates(self):
        graph = TopicGraph.empty()
        graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        graph.create_node(name="盘口指标", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        graph.create_node(name="盘口指标2", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        # Use a lexical checker — no LLM
        checker = LexicalTopicSimilarityChecker()
        results = await scan_duplicate_topics(graph, similarity_checker=checker)
        # "盘口指标" and "盘口指标2" — prefix match by _leaf_names_are_similar
        # (盘口指标 is a prefix of 盘口指标2 after normalization)
        lexical_similar = [r for r in results if r.is_similar]
        self.assertGreaterEqual(len(lexical_similar), 1)

    async def test_scan_with_no_siblings(self):
        graph = TopicGraph.empty()
        graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        checker = LexicalTopicSimilarityChecker()
        results = await scan_duplicate_topics(graph, similarity_checker=checker)
        self.assertEqual(results, [])

    async def test_scan_finds_llm_semantic_duplicates(self):
        graph = TopicGraph.empty()
        graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        graph.create_node(name="盘口指标", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        graph.create_node(name="盘面指标", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        mock_processor = AsyncMock(return_value={
            "results": [
                {"name_a": "盘口指标", "name_b": "盘面指标", "is_similar": True, "confidence": 0.95, "reason": "synonym"},
            ]
        })
        checker = LLMTopicSimilarityChecker(processor=mock_processor)
        results = await scan_duplicate_topics(graph, similarity_checker=checker)
        similar = [r for r in results if r.is_similar]
        self.assertEqual(len(similar), 1)
        self.assertEqual(similar[0].candidate.name_a, "盘口指标")
