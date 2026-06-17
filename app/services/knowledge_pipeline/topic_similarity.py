"""
Topic similarity detection for the knowledge pipeline.

Two-layer approach:
  1. Lexical check (fast, free) via _leaf_names_are_similar
  2. LLM semantic check (slower, accurate) via TopicDedupProcessor

Also provides a global scan function to detect duplicate siblings
across the entire topic graph.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from loguru import logger

from .topic_path_resolver import _leaf_names_are_similar


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SimilarityCandidate:
    name_a: str
    name_b: str
    context_a: list[str]  # ancestor path for name_a
    context_b: list[str]  # ancestor path for name_b


@dataclass
class SimilarityResult:
    candidate: SimilarityCandidate
    is_similar: bool
    confidence: float  # 0.0–1.0
    reason: str


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class TopicSimilarityChecker(Protocol):
    async def check_batch(
        self,
        candidates: list[SimilarityCandidate],
    ) -> list[SimilarityResult]: ...


# ---------------------------------------------------------------------------
# Lexical-only implementation (wraps existing _leaf_names_are_similar)
# ---------------------------------------------------------------------------

class LexicalTopicSimilarityChecker:
    """Fast lexical-only similarity checker.

    Uses the existing _leaf_names_are_similar function for comparison.
    No LLM calls — useful as a pre-filter or as a standalone checker
    when LLM is unavailable.
    """

    async def check_batch(
        self,
        candidates: list[SimilarityCandidate],
    ) -> list[SimilarityResult]:
        results: list[SimilarityResult] = []
        for cand in candidates:
            is_similar = _leaf_names_are_similar(cand.name_a, cand.name_b)
            results.append(SimilarityResult(
                candidate=cand,
                is_similar=is_similar,
                confidence=1.0 if is_similar else 0.0,
                reason="lexical match" if is_similar else "lexical mismatch",
            ))
        return results


# ---------------------------------------------------------------------------
# LLM-backed implementation
# ---------------------------------------------------------------------------

class LLMTopicSimilarityChecker:
    """Two-phase similarity checker: lexical first, then LLM for remaining.

    Phase 1: Lexical check — if _leaf_names_are_similar returns True,
             trust it (no need for LLM).
    Phase 2: Batch LLM call for candidates that failed lexical check.
             Confidence threshold of 0.8 is enforced.
    """

    CONFIDENCE_THRESHOLD = 0.8

    def __init__(self, processor=None):
        """Initialize with an optional TopicDedupProcessor.

        Args:
            processor: A callable matching TopicDedupProcessor's interface.
                       If None, a default TopicDedupProcessor is created.
        """
        self._processor = processor

    def _get_processor(self):
        if self._processor is not None:
            return self._processor
        from .llm_processor import TopicDedupProcessor
        return TopicDedupProcessor()

    async def check_batch(
        self,
        candidates: list[SimilarityCandidate],
    ) -> list[SimilarityResult]:
        if not candidates:
            return []

        # Phase 1: Lexical pre-filter
        lexical_checker = LexicalTopicSimilarityChecker()
        lexical_results = await lexical_checker.check_batch(candidates)

        # Separate: lexical says "similar" → done; "not similar" → needs LLM
        need_llm: list[tuple[int, SimilarityCandidate]] = []
        result_map: dict[int, SimilarityResult] = {}
        for i, (cand, lex_res) in enumerate(zip(candidates, lexical_results)):
            if lex_res.is_similar:
                result_map[i] = lex_res
            else:
                need_llm.append((i, cand))

        if not need_llm:
            return [result_map[i] for i in range(len(candidates))]

        # Phase 2: Batch LLM call
        llm_input = [
            {
                "name_a": cand.name_a,
                "name_b": cand.name_b,
                "context_a": cand.context_a,
                "context_b": cand.context_b,
            }
            for _, cand in need_llm
        ]

        try:
            processor = self._get_processor()
            llm_output = await processor(candidates=llm_input)
        except Exception as exc:
            logger.warning(
                f"[TopicSimilarity] LLM call failed, falling back to lexical: {exc}"
            )
            return lexical_results

        # Parse LLM results — maintain order correspondence
        llm_results_list = llm_output.get("results", [])
        for j, (orig_idx, cand) in enumerate(need_llm):
            if j < len(llm_results_list):
                item = llm_results_list[j]
                is_similar = bool(item.get("is_similar", False))
                confidence = float(item.get("confidence", 0.0))
                if confidence < self.CONFIDENCE_THRESHOLD:
                    is_similar = False
                result_map[orig_idx] = SimilarityResult(
                    candidate=cand,
                    is_similar=is_similar,
                    confidence=confidence,
                    reason=item.get("reason", ""),
                )
            else:
                # LLM didn't return enough results — fall back to lexical
                result_map[orig_idx] = lexical_results[orig_idx]

        return [result_map[i] for i in range(len(candidates))]


# ---------------------------------------------------------------------------
# Global scan function
# ---------------------------------------------------------------------------

async def scan_duplicate_topics(
    graph,
    similarity_checker: TopicSimilarityChecker | None = None,
    batch_size: int = 20,
) -> list[SimilarityResult]:
    """Scan the entire topic graph for semantically similar siblings.

    Groups active nodes by parent, then checks all sibling pairs
    within each group using the similarity checker.

    Args:
        graph: TopicGraph instance to scan.
        similarity_checker: Checker to use. If None, creates an
                            LLMTopicSimilarityChecker by default.
        batch_size: Max candidates per LLM call.

    Returns:
        List of SimilarityResult for pairs that are semantically similar.
    """
    if similarity_checker is None:
        similarity_checker = LLMTopicSimilarityChecker()

    # Group active nodes by parent_id
    siblings_by_parent: dict[str | None, list] = {}
    for node in graph.nodes.values():
        if node.status != "active":
            continue
        siblings_by_parent.setdefault(node.parent_id, []).append(node)

    all_results: list[SimilarityResult] = []

    for parent_id, siblings in siblings_by_parent.items():
        if len(siblings) < 2:
            continue

        # Generate all pairs within this sibling group
        candidates: list[SimilarityCandidate] = []
        pairs: list[tuple] = []
        for i in range(len(siblings)):
            for j in range(i + 1, len(siblings)):
                a, b = siblings[i], siblings[j]
                # Skip if lexical match already says similar (would already be merged)
                if _leaf_names_are_similar(a.name, b.name):
                    all_results.append(SimilarityResult(
                        candidate=SimilarityCandidate(
                            name_a=a.name,
                            name_b=b.name,
                            context_a=a.path[:-1],
                            context_b=b.path[:-1],
                        ),
                        is_similar=True,
                        confidence=1.0,
                        reason="lexical match (pre-existing duplicate)",
                    ))
                    continue
                candidates.append(SimilarityCandidate(
                    name_a=a.name,
                    name_b=b.name,
                    context_a=a.path[:-1],
                    context_b=b.path[:-1],
                ))
                pairs.append((a, b))

        # Process in batches
        for start in range(0, len(candidates), batch_size):
            batch = candidates[start:start + batch_size]
            batch_pairs = pairs[start:start + batch_size]
            results = await similarity_checker.check_batch(batch)
            for result, (a, b) in zip(results, batch_pairs):
                if result.is_similar:
                    all_results.append(result)

    return all_results
