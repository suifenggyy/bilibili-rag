from typing import List, Callable, Awaitable, Any
from loguru import logger
from .knowledge_distiller import DistilledKnowledge
from .topic_graph import TopicGraph, MutationProposal, TopicResolution, build_mutation_identity

class TopicPathResolver:
    def __init__(self, processor: Callable[..., Awaitable[dict]]):
        self.processor = processor

    def _validate_payload(self, payload: dict) -> dict:
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
        payload = self._validate_payload(payload_raw)
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
