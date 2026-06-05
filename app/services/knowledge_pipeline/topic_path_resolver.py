from typing import List, Callable, Awaitable, Any
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
            proposal_type = item["type"]
            if proposal_type not in {"create_leaf", "add_alias", "rename", "merge", "split", "move", "replace"}:
                raise ValueError("invalid topic path payload")
            if not isinstance(item.get("confidence"), (int, float)) or not (0.0 <= float(item["confidence"]) <= 1.0):
                raise ValueError("invalid topic path payload")
            if proposal_type == "create_leaf":
                if not item.get("target_parent_path") or not item.get("target_name") or item.get("affected_node_ids"):
                    raise ValueError("invalid topic path payload")
                item["affected_unresolved_names"] = item.get("affected_unresolved_names") or [item["target_name"]]
                item["target_paths"] = item.get("target_paths") or [[*item["target_parent_path"], item["target_name"]]]
            else:
                if not item.get("affected_node_ids") or any(graph.get_node(node_id) is None for node_id in item["affected_node_ids"]):
                    raise ValueError("invalid topic path payload")
                if proposal_type in {"merge", "replace"} and not item.get("target_replacement_node_id"):
                    raise ValueError("invalid topic path payload")
                if proposal_type == "move" and not item.get("target_parent_path"):
                    raise ValueError("invalid topic path payload")
                if proposal_type == "rename" and not item.get("target_name"):
                    raise ValueError("invalid topic path payload")
                if proposal_type == "split" and not item.get("target_paths"):
                    raise ValueError("invalid topic path payload")
            if proposal_type == "add_alias" and len(item["affected_node_ids"]) != 1:
                raise ValueError("invalid topic path payload")
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
            
        if graph.get_node_by_path(primary_path) is None and not any(primary_path in proposal.target_paths for proposal in normalized):
            raise ValueError("invalid topic path payload")
        for path in secondary_paths_requiring_canonical_check:
            if graph.get_node_by_path(path) is None and not any(path in proposal.target_paths for proposal in normalized):
                raise ValueError("invalid topic path payload")
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
