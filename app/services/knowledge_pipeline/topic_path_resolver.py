from typing import List, Callable, Awaitable, Any, TYPE_CHECKING
from loguru import logger
from .knowledge_distiller import DistilledKnowledge
from .topic_graph import TopicGraph, MutationProposal, TopicResolution, build_mutation_identity
import re

if TYPE_CHECKING:
    from .topic_similarity import TopicSimilarityChecker, SimilarityCandidate


def _normalize_leaf_name(name: str) -> str:
    """Normalize a topic leaf name for fuzzy comparison.

    Strips English suffixes appended after Chinese characters
    (e.g. "财富自由financial-independence" → "财富自由"),
    removes whitespace, and lowercases.
    """
    # Strip trailing English/ASCII suffix after Chinese chars
    # Pattern: Chinese text followed by a run of ASCII without space
    cleaned = re.sub(r'([一-鿿])\s*[a-zA-Z][\w\-]*$', r'\1', name)
    return cleaned.strip().lower()


def _leaf_names_are_similar(name_a: str, name_b: str) -> bool:
    """Check if two leaf names are similar enough to be the same topic.

    Matches when:
    - Exact match (case-insensitive)
    - One is a prefix of the other after stripping English suffixes
    - Normalized forms match
    """
    if not name_a or not name_b:
        return False
    a_lower = name_a.strip().lower()
    b_lower = name_b.strip().lower()
    if a_lower == b_lower:
        return True
    norm_a = _normalize_leaf_name(name_a)
    norm_b = _normalize_leaf_name(name_b)
    if norm_a == norm_b:
        return True
    # One is a prefix of the other
    if norm_a and norm_b:
        if norm_a.startswith(norm_b) or norm_b.startswith(norm_a):
            return True
    return False


class TopicPathResolver:
    def __init__(
        self,
        processor: Callable[..., Awaitable[dict]],
        similarity_checker: "TopicSimilarityChecker | None" = None,
    ):
        self.processor = processor
        self.similarity_checker = similarity_checker

    async def _validate_payload(self, payload: dict, graph: TopicGraph) -> dict:
        primary_path = payload.get("primary_path")
        secondary_paths = payload.get("secondary_paths", [])
        if not isinstance(primary_path, list) or not all(isinstance(item, str) and item.strip() for item in primary_path):
            raise ValueError("invalid topic path payload")
        if not isinstance(secondary_paths, list) or any(not isinstance(path, list) for path in secondary_paths):
            raise ValueError("invalid topic path payload")
        normalized_secondary = []
        seen = set()
        for path in secondary_paths:
            if not all(isinstance(item, str) and item.strip() for item in path):
                raise ValueError("invalid topic path payload")
            key = tuple(path)
            if key != tuple(primary_path) and key not in seen:
                normalized_secondary.append(path)
                seen.add(key)
        # --- Dedup: drop secondary paths whose leaf is similar to primary leaf ---
        primary_leaf = primary_path[-1] if primary_path else ""
        primary_parent = tuple(primary_path[:-1]) if len(primary_path) > 1 else ()
        filtered_secondary = []
        # Collect candidates for LLM semantic check
        llm_candidates_phase1: list[tuple[int, Any]] = []  # (index_in_normalized_secondary, path)
        for path in normalized_secondary:
            sec_leaf = path[-1] if path else ""
            sec_parent = tuple(path[:-1]) if len(path) > 1 else ()
            # Same parent + similar leaf name → treat as duplicate (lexical)
            if sec_parent == primary_parent and _leaf_names_are_similar(primary_leaf, sec_leaf):
                logger.info(
                    f"[TopicPathResolver] dropping similar secondary path "
                    f"{path} (leaf '{sec_leaf}' ≈ primary leaf '{primary_leaf}')"
                )
                continue
            # Same parent + no lexical match → collect for LLM check
            if sec_parent == primary_parent and self.similarity_checker is not None:
                llm_candidates_phase1.append((len(filtered_secondary), path))
            filtered_secondary.append(path)
        normalized_secondary = filtered_secondary

        # --- LLM semantic dedup: check remaining secondaries vs primary ---
        if llm_candidates_phase1 and self.similarity_checker is not None:
            from .topic_similarity import SimilarityCandidate
            candidates = [
                SimilarityCandidate(
                    name_a=primary_leaf,
                    name_b=path[-1] if path else "",
                    context_a=list(primary_parent),
                    context_b=list(path[:-1]) if len(path) > 1 else [],
                )
                for _, path in llm_candidates_phase1
            ]
            try:
                results = await self.similarity_checker.check_batch(candidates)
                # Remove secondaries that are semantically similar to primary
                indices_to_remove = set()
                for (orig_idx, path), result in zip(llm_candidates_phase1, results):
                    if result.is_similar:
                        logger.info(
                            f"[TopicPathResolver] LLM dedup: dropping similar secondary path "
                            f"{path} (leaf '{path[-1]}' ≈ primary leaf '{primary_leaf}', "
                            f"confidence={result.confidence:.2f}, reason={result.reason})"
                        )
                        indices_to_remove.add(orig_idx)
                if indices_to_remove:
                    normalized_secondary = [
                        p for i, p in enumerate(normalized_secondary)
                        if i not in indices_to_remove
                    ]
            except Exception as exc:
                logger.warning(f"[TopicPathResolver] LLM semantic dedup failed, skipping: {exc}")
        # --- Dedup: merge secondary paths that share the same parent and similar leaves ---
        deduped_secondary = []
        seen_pairs = set()
        for path in normalized_secondary:
            sec_parent = tuple(path[:-1]) if len(path) > 1 else ()
            sec_leaf = path[-1] if path else ""
            pair_key = (sec_parent, _normalize_leaf_name(sec_leaf))
            if pair_key not in seen_pairs:
                deduped_secondary.append(path)
                seen_pairs.add(pair_key)
            else:
                logger.info(
                    f"[TopicPathResolver] dropping duplicate secondary path {path} "
                    f"(parent={list(sec_parent)}, leaf≈'{_normalize_leaf_name(sec_leaf)}')"
                )
        normalized_secondary = deduped_secondary
        # --- Limit: at most 1 secondary path per parent ---
        # If multiple secondaries share the same parent, keep only the first.
        # This prevents the same note from being indexed under many sibling topics
        # (e.g. 投资/盘口指标, 投资/盘面指标, 投资/短线交易).
        seen_parents = set()
        parent_limited_secondary = []
        for path in normalized_secondary:
            sec_parent = tuple(path[:-1]) if len(path) > 1 else ()
            if sec_parent in seen_parents:
                logger.info(
                    f"[TopicPathResolver] dropping extra secondary under same parent "
                    f"{list(sec_parent)}: {path}"
                )
                continue
            seen_parents.add(sec_parent)
            parent_limited_secondary.append(path)
        normalized_secondary = parent_limited_secondary
        # --- Resolve secondary paths to existing graph nodes when possible ---
        resolved_secondary = []
        # Collect unresolved paths for LLM semantic redirect
        unresolved_for_llm: list[tuple[int, list[str], list[str]]] = []  # (index, path, parent_path)
        for path in normalized_secondary:
            existing = graph.get_node_by_path(path)
            if existing:
                # Path already exists in graph — keep as-is
                resolved_secondary.append(path)
                continue
            # Check if a sibling under the same parent has a similar leaf name (lexical)
            parent_path = path[:-1] if len(path) > 1 else []
            leaf = path[-1] if path else ""
            parent_node = graph.get_node_by_path(parent_path) if parent_path else None
            if parent_node and leaf:
                merged = False
                for child_id in parent_node.children_ids:
                    child = graph.get_node(child_id)
                    if child and child.status == "active" and _leaf_names_are_similar(child.name, leaf):
                        logger.info(
                            f"[TopicPathResolver] redirecting secondary path {path} → "
                            f"existing node {child.path} (leaf '{child.name}' ≈ '{leaf}')"
                        )
                        resolved_secondary.append(child.path)
                        merged = True
                        break
                if merged:
                    continue
                # No lexical match — collect for LLM semantic check
                if self.similarity_checker is not None:
                    unresolved_for_llm.append((len(resolved_secondary), path))
            resolved_secondary.append(path)

        # --- LLM semantic redirect: check unresolved secondaries against existing siblings ---
        if unresolved_for_llm and self.similarity_checker is not None:
            from .topic_similarity import SimilarityCandidate
            # Build candidates: compare each unresolved leaf with all active siblings
            llm_candidates = []
            llm_meta = []  # (index_in_resolved, path, list of (child_node, candidate_index))
            for idx, path in unresolved_for_llm:
                parent_path = path[:-1] if len(path) > 1 else []
                leaf = path[-1] if path else ""
                parent_node = graph.get_node_by_path(parent_path) if parent_path else None
                if not parent_node or not leaf:
                    continue
                meta_children = []
                for child_id in parent_node.children_ids:
                    child = graph.get_node(child_id)
                    if child and child.status == "active":
                        llm_candidates.append(SimilarityCandidate(
                            name_a=child.name,
                            name_b=leaf,
                            context_a=list(parent_path),
                            context_b=list(parent_path),
                        ))
                        meta_children.append(child)
                llm_meta.append((idx, path, meta_children))

            if llm_candidates:
                try:
                    results = await self.similarity_checker.check_batch(llm_candidates)
                    # Process results — redirect if any sibling is semantically similar
                    result_idx = 0
                    for idx, path, meta_children in llm_meta:
                        redirected = False
                        for child in meta_children:
                            if result_idx < len(results) and results[result_idx].is_similar:
                                result = results[result_idx]
                                logger.info(
                                    f"[TopicPathResolver] LLM redirect: secondary path {path} → "
                                    f"existing node {child.path} (leaf '{child.name}' ≈ '{path[-1]}', "
                                    f"confidence={result.confidence:.2f}, reason={result.reason})"
                                )
                                # Replace the unresolved path with the existing node's path
                                resolved_secondary[idx] = child.path
                                redirected = True
                                result_idx += 1
                                break
                            result_idx += 1
                        if not redirected:
                            result_idx += len(meta_children)
                except Exception as exc:
                    logger.warning(f"[TopicPathResolver] LLM semantic redirect failed, skipping: {exc}")

        normalized_secondary = resolved_secondary
        proposed_target_paths = {
            tuple(target_path)
            for proposal in payload.get("mutation_proposals", [])
            for target_path in proposal.get("target_paths", [])
        }
        for path in normalized_secondary:
            if tuple(path) not in proposed_target_paths:
                payload.setdefault("_secondary_paths_requiring_canonical_check", []).append(path)
        payload["secondary_paths"] = normalized_secondary
        return payload

    def _estimate_impacted_existing_nodes(self, item: dict, graph: TopicGraph) -> int:
        affected = set(item.get("affected_node_ids", []))
        for node_id in item.get("affected_node_ids", []):
            affected.update(desc.id for desc in graph.get_descendants(node_id))
        return max(1, len(affected))

    def _estimate_replaced_canonical_paths(self, item: dict) -> int:
        if item["type"] in {"merge", "replace"}:
            return 1
        return 0

    def _normalize_proposals(self, proposals: list[dict], graph: TopicGraph, primary_path: list[str], secondary_paths_requiring_canonical_check: list[list[str]]) -> list[MutationProposal]:
        normalized: list[MutationProposal] = []
        for item in proposals:
            proposal_type = item.get("type", "")
            if proposal_type not in {"create_leaf", "add_alias", "rename", "merge", "split", "move", "replace"}:
                logger.warning(f"[TopicPathResolver] skipping unknown mutation type: {proposal_type}")
                continue
            # Normalize confidence — accept string numbers too
            confidence = item.get("confidence", 0.5)
            if isinstance(confidence, str):
                try:
                    confidence = float(confidence)
                except ValueError:
                    confidence = 0.5
            if not isinstance(confidence, (int, float)) or not (0.0 <= float(confidence) <= 1.0):
                confidence = 0.5
            item["confidence"] = float(confidence)

            if proposal_type == "create_leaf":
                # create_leaf: clear affected_node_ids if present (LLM may fill them)
                item["affected_node_ids"] = []
                if not item.get("target_parent_path") or not item.get("target_name"):
                    # Derive from target_paths if possible
                    target_paths = item.get("target_paths", [])
                    if target_paths and isinstance(target_paths[0], list) and len(target_paths[0]) >= 2:
                        item["target_parent_path"] = target_paths[0][:-1]
                        item["target_name"] = target_paths[0][-1]
                    else:
                        logger.warning(f"[TopicPathResolver] skipping malformed create_leaf proposal: {item}")
                        continue
                item["affected_unresolved_names"] = item.get("affected_unresolved_names") or [item["target_name"]]
                item["target_paths"] = item.get("target_paths") or [[*item["target_parent_path"], item["target_name"]]]
            else:
                # For non-create_leaf mutations, affected_node_ids must resolve to real nodes.
                # If they don't, skip this proposal rather than failing.
                affected_node_ids = item.get("affected_node_ids", [])
                if not affected_node_ids:
                    logger.warning(f"[TopicPathResolver] skipping {proposal_type} proposal without affected_node_ids")
                    continue
                # Filter out node IDs that don't exist in the graph
                valid_ids = [nid for nid in affected_node_ids if graph.get_node(nid) is not None]
                if not valid_ids:
                    logger.warning(f"[TopicPathResolver] skipping {proposal_type} proposal with no valid affected_node_ids")
                    continue
                item["affected_node_ids"] = valid_ids
                if proposal_type in {"merge", "replace"} and not item.get("target_replacement_node_id"):
                    logger.warning(f"[TopicPathResolver] skipping {proposal_type} proposal without target_replacement_node_id")
                    continue
                if proposal_type == "move" and not item.get("target_parent_path"):
                    logger.warning(f"[TopicPathResolver] skipping move proposal without target_parent_path")
                    continue
                if proposal_type == "rename" and not item.get("target_name"):
                    logger.warning(f"[TopicPathResolver] skipping rename proposal without target_name")
                    continue
                if proposal_type == "split" and not item.get("target_paths"):
                    logger.warning(f"[TopicPathResolver] skipping split proposal without target_paths")
                    continue
            if proposal_type == "add_alias" and len(item.get("affected_node_ids", [])) != 1:
                logger.warning(f"[TopicPathResolver] skipping add_alias proposal with !=1 affected_node_ids")
                continue
            item["impacted_existing_nodes"] = item.get("impacted_existing_nodes", self._estimate_impacted_existing_nodes(item, graph))
            item["replaced_canonical_paths"] = item.get("replaced_canonical_paths", self._estimate_replaced_canonical_paths(item))
            item["proposal_identity"] = item.get("proposal_identity") or build_mutation_identity(
                mutation_type=item["type"],
                affected_node_ids=item.get("affected_node_ids", []),
                affected_unresolved_names=item.get("affected_unresolved_names", []),
                target_parent_path=item.get("target_parent_path"),
                target_replacement_node_id=item.get("target_replacement_node_id"),
            )
            item.setdefault("reason", "")
            item.setdefault("target_replacement_node_id", None)
            item.setdefault("affected_unresolved_names", [])
            item.setdefault("target_parent_path", [])
            item.setdefault("target_name", "")
            item.setdefault("affected_node_ids", [])
            item.setdefault("target_paths", [])
            normalized.append(MutationProposal(**item))

        # Ensure primary_path is covered — if no matching mutation exists and path
        # doesn't exist in graph, add a create_leaf for it automatically
        if graph.get_node_by_path(primary_path) is None and not any(primary_path in proposal.target_paths for proposal in normalized):
            # Auto-create a create_leaf proposal for the primary path
            parent_path = primary_path[:-1] if len(primary_path) > 1 else []
            leaf_name = primary_path[-1] if primary_path else "未分类"
            auto_proposal = MutationProposal(
                type="create_leaf",
                proposal_identity=build_mutation_identity(
                    mutation_type="create_leaf",
                    affected_node_ids=[],
                    affected_unresolved_names=[leaf_name],
                    target_parent_path=parent_path,
                    target_replacement_node_id=None,
                ),
                affected_node_ids=[],
                affected_unresolved_names=[leaf_name],
                target_parent_path=parent_path,
                target_name=leaf_name,
                target_replacement_node_id=None,
                target_paths=[primary_path],
                confidence=0.9,
                impacted_existing_nodes=1,
                replaced_canonical_paths=0,
                reason="auto-created for primary path",
            )
            normalized.append(auto_proposal)

        for path in secondary_paths_requiring_canonical_check:
            if graph.get_node_by_path(path) is None and not any(path in proposal.target_paths for proposal in normalized):
                logger.debug(f"[TopicPathResolver] secondary path {path} not covered by any proposal, will be checked at placement time")

        return normalized

    async def resolve(self, units: DistilledKnowledge, graph: TopicGraph) -> TopicResolution:
        payload_raw = await self.processor(units=units, graph_snapshot=graph.to_snapshot())
        payload = await self._validate_payload(payload_raw, graph)
        mutation_proposals = self._normalize_proposals(
            payload.get("mutation_proposals", []),
            graph,
            payload["primary_path"],
            payload.get("_secondary_paths_requiring_canonical_check", []),
        )
        primary_candidate = payload["primary_path"]
        secondary_paths = payload.get("secondary_paths", [])
        return TopicResolution(
            requested_primary_path=primary_candidate,
            secondary_paths=secondary_paths,
            mutation_proposals=mutation_proposals,
            source_identity=units.source_identity,
        )
