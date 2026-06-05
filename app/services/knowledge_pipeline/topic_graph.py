from dataclasses import dataclass, asdict
import uuid
from typing import Optional, List
from .metadata_state import MetadataState

@dataclass
class TopicNode:
    id: str
    name: str
    parent_id: Optional[str]
    children_ids: List[str]
    aliases: List[str]
    path: List[str]
    replacement_target_id: Optional[str]
    lineage: List[str]
    summary_version: str
    detail_version: str
    status: str

@dataclass
class TopicGraphSnapshot:
    version: str
    nodes: List[dict]

@dataclass
class MutationProposal:
    type: str
    proposal_identity: str
    affected_node_ids: List[str]
    affected_unresolved_names: List[str]
    target_parent_path: List[str]
    target_name: str
    target_replacement_node_id: Optional[str]
    target_paths: List[List[str]]
    confidence: float
    impacted_existing_nodes: int
    replaced_canonical_paths: int
    reason: str

@dataclass
class MutationDecision:
    status: str
    impacted_node_ids: List[str]

@dataclass
class GraphApplyResult:
    changed_node_ids: List[str]
    impacted_node_ids: List[str]

@dataclass
class DeferredMutationRecord:
    proposal_identity: str
    proposed_mutation_type: str
    lifecycle_status: str
    affected_node_ids: List[str]
    affected_unresolved_names: List[str]
    target_parent_path: List[str]
    target_name: str
    target_replacement_node_id: Optional[str]
    target_paths: List[List[str]]
    confidence_score: float
    reason: str
    supporting_source_note_paths: List[str]
    supporting_source_count: int
    created_at: str
    resolved_at: Optional[str]

@dataclass
class SecondaryPlacementResult:
    requested_path: List[str]
    canonical_node_id: Optional[str]
    placement_path: List[str]
    placement_mode: str
    deferred_path: Optional[List[str]]

@dataclass
class GraphPlacementResult:
    canonical_primary_path: List[str]
    canonical_primary_node_id: Optional[str]
    placement_path: List[str]
    placement_mode: str
    deferred_primary_path: Optional[List[str]]
    highest_confidence_replacement_path: Optional[List[str]]
    secondary_placements: List[SecondaryPlacementResult]
    secondary_node_ids: List[str]
    ancestor_node_ids: List[str]
    secondary_ancestor_node_ids: List[str]
    deferred_mutation_records: List[DeferredMutationRecord]

@dataclass
class TopicResolution:
    requested_primary_path: List[str]
    secondary_paths: List[List[str]]
    mutation_proposals: List[MutationProposal]
    source_identity: dict

class TopicGraph:
    def __init__(self, nodes: dict[str, TopicNode], version: str):
        self.nodes = nodes
        self.version = version

    @classmethod
    def empty(cls) -> "TopicGraph":
        return cls(nodes={}, version="topic-graph-v1")

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "TopicGraph":
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("nodes"), list):
            raise ValueError("invalid topic graph snapshot")
        nodes = {
            item["id"]: TopicNode(**item)
            for item in snapshot["nodes"]
        }
        return cls(nodes=nodes, version=snapshot.get("version", "topic-graph-v1"))

    def to_snapshot(self) -> dict:
        return {
            "version": self.version,
            "nodes": [asdict(node) for node in self.nodes.values()],
        }

    async def load(self, metadata_state: MetadataState) -> "TopicGraph":
        snapshot = await metadata_state.load_topic_graph()
        graph = TopicGraph.from_snapshot(snapshot)
        self.nodes = graph.nodes
        self.version = graph.version
        return self

    async def save(self, metadata_state: MetadataState) -> None:
        metadata_state._assert_write_lock_held()
        await metadata_state.save_topic_graph(self.to_snapshot())

    def _new_node_id(self) -> str:
        return str(uuid.uuid4())

    def create_node(self, name: str, parent_path: List[str], aliases: List[str], replacement_target_id: Optional[str], lineage: List[str], summary_version: str, detail_version: str, status: str) -> TopicNode:
        parent = self.get_node_by_path(parent_path) if parent_path else None
        node = TopicNode(
            id=self._new_node_id(),
            name=name,
            parent_id=parent.id if parent else None,
            children_ids=[],
            aliases=aliases,
            path=[*parent_path, name],
            replacement_target_id=replacement_target_id,
            lineage=lineage,
            summary_version=summary_version,
            detail_version=detail_version,
            status=status,
        )
        self.nodes[node.id] = node
        if parent:
            parent.children_ids.append(node.id)
        return node

    def apply_new_leaf(self, parent_path: List[str], child_name: str) -> GraphApplyResult:
        node = self.create_node(name=child_name, parent_path=parent_path, aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        parent = self.get_node_by_path(parent_path)
        impacted = [node.id, *([parent.id] if parent else [])]
        return GraphApplyResult(changed_node_ids=[node.id], impacted_node_ids=impacted)

    def get_node(self, node_id: str) -> TopicNode:
        return self.nodes[node_id]

    def get_node_by_path(self, path: List[str]) -> Optional[TopicNode]:
        for node in self.nodes.values():
            if node.path == path and node.status == "active":
                return node
        return None

    def get_path(self, node_id: Optional[str]) -> List[str]:
        return [] if node_id is None else self.get_node(node_id).path

    def relative_suffix(self, node_id: str, ancestor_id: str) -> List[str]:
        node = self.get_node(node_id)
        ancestor = self.get_node(ancestor_id)
        # Note: when ancestor's path just changed, the descendant's path may not match the old ancestor path exactly if it was evaluated before the ancestor changed. 
        # But according to plan we update the descendant using `[*node.path, *self.relative_suffix(...)]` which is wrong because we want the relative path from the *original* hierarchy.
        # However, the relative_suffix just needs to return the part of the child's path after the ancestor's *old* path length, 
        # but wait, the plan specifically does: 
        # `descendant.path = [*node.path, *self.relative_suffix(descendant.id, ancestor_id=node_id)]`
        # and `relative_suffix` does `return node.path[len(ancestor.path):]`.
        # The problem is `node.path` in `relative_suffix` is the *descendant's* path, but `ancestor.path` has ALREADY BEEN UPDATED!
        # So `len(ancestor.path)` is the NEW length of the ancestor. If the move changed the length of the ancestor path, this breaks.
        # Actually, let's fix relative_suffix to take the new ancestor path length? No, relative suffix is about the distance.
        # The true relative suffix is from the graph hierarchy itself, not the path strings.
        # Let's compute it by walking up from descendant to ancestor.
        suffix = []
        curr = node
        while curr and curr.id != ancestor.id:
            suffix.insert(0, curr.name)
            curr = self.get_node(curr.parent_id) if curr.parent_id else None
        return suffix

    def deepest_existing_path(self, path: List[str]) -> List[str]:
        for size in range(len(path), -1, -1):
            candidate = path[:size]
            if not candidate or self.get_node_by_path(candidate):
                return candidate
        return []

    def get_ancestor_ids(self, path: List[str]) -> List[str]:
        ancestors: List[str] = []
        for size in range(1, len(path) + 1):
            node = self.get_node_by_path(path[:size])
            if node:
                ancestors.append(node.id)
        return ancestors

    def get_descendants(self, node_id: str) -> List[TopicNode]:
        descendants: List[TopicNode] = []
        queue = list(self.get_node(node_id).children_ids)
        while queue:
            current = self.get_node(queue.pop(0))
            descendants.append(current)
            queue.extend(current.children_ids)
        return descendants

    def evaluate_mutation(self, proposal: MutationProposal) -> MutationDecision:
        if (
            proposal.type in {"create_leaf", "add_alias"}
            and proposal.confidence >= 0.85
            and proposal.impacted_existing_nodes <= 5
            and proposal.replaced_canonical_paths <= 1
        ):
            if proposal.type == "create_leaf":
                parent = self.get_node_by_path(proposal.target_parent_path)
                if parent is None or parent.status != "active":
                    return MutationDecision(status="pending", impacted_node_ids=proposal.affected_node_ids)
            return MutationDecision(status="auto_apply", impacted_node_ids=proposal.affected_node_ids)
        return MutationDecision(status="pending", impacted_node_ids=proposal.affected_node_ids)

    def rename_node(self, node_id: str, new_name: str) -> None:
        node = self.get_node(node_id)
        old_path = "/".join(node.path)
        old_name = node.name
        
        # Capture relative suffixes for all descendants BEFORE changing the node's path
        descendants = self.get_descendants(node_id)
        suffixes = {desc.id: self.relative_suffix(desc.id, node_id) for desc in descendants}
        
        node.name = new_name
        node.aliases.append(old_name)
        node.lineage.append(old_path)
        parent_path = self.get_path(node.parent_id) if node.parent_id else []
        node.path = [*parent_path, new_name]
        
        for descendant in descendants:
            descendant.path = [*node.path, *suffixes[descendant.id]]

    def move_node(self, node_id: str, target_parent_path: List[str]) -> GraphApplyResult:
        node = self.get_node(node_id)
        old_path = "/".join(node.path)
        old_parent = self.get_node(node.parent_id) if node.parent_id else None
        new_parent = self.get_node_by_path(target_parent_path)
        node.parent_id = new_parent.id if new_parent else None
        if old_parent:
            old_parent.children_ids = [child_id for child_id in old_parent.children_ids if child_id != node_id]
        if new_parent:
            new_parent.children_ids.append(node_id)
        node.lineage.append(old_path)
        node.path = [*target_parent_path, node.name]
        descendants = self.get_descendants(node_id)
        for descendant in descendants:
            descendant.path = [*node.path, *self.relative_suffix(descendant.id, ancestor_id=node_id)]
        impacted = [node_id, new_parent.id, *([old_parent.id] if old_parent else []), *[item.id for item in descendants]]
        return GraphApplyResult(changed_node_ids=[node_id, *[item.id for item in descendants]], impacted_node_ids=impacted)

    def merge_nodes(self, source_node_ids: List[str], replacement_node_id: str) -> GraphApplyResult:
        changed = set(source_node_ids + [replacement_node_id])
        impacted = set(changed)
        for node_id in source_node_ids:
            node = self.get_node(node_id)
            node.status = "merged"
            node.replacement_target_id = replacement_node_id
            node.lineage.append("/".join(node.path))
            impacted.update(desc.id for desc in self.get_descendants(node_id))
        return GraphApplyResult(changed_node_ids=sorted(changed), impacted_node_ids=sorted(impacted))

    def split_node(self, source_node_id: str, target_paths: List[List[str]]) -> GraphApplyResult:
        source = self.get_node(source_node_id)
        source.status = "deprecated"
        source.lineage.append("/".join(source.path))
        created = [self.apply_new_leaf(parent_path=path[:-1], child_name=path[-1]).changed_node_ids[0] for path in target_paths]
        impacted = [source_node_id, *created, *[item.id for item in self.get_descendants(source_node_id)]]
        return GraphApplyResult(changed_node_ids=[source_node_id, *created], impacted_node_ids=impacted)

    def replace_node(self, source_node_id: str, replacement_node_id: str) -> GraphApplyResult:
        source = self.get_node(source_node_id)
        source.status = "deprecated"
        source.replacement_target_id = replacement_node_id
        source.lineage.append("/".join(source.path))
        impacted = [source_node_id, replacement_node_id, *[item.id for item in self.get_descendants(source_node_id)]]
        return GraphApplyResult(changed_node_ids=[source_node_id, replacement_node_id], impacted_node_ids=impacted)
    def apply_auto_mutation(self, proposal: MutationProposal) -> GraphApplyResult:
        if proposal.type == "create_leaf":
            return self.apply_new_leaf(parent_path=proposal.target_parent_path, child_name=proposal.target_name)
        if proposal.type == "add_alias":
            node = self.get_node(proposal.affected_node_ids[0])
            node.aliases.append(proposal.target_name)
            return GraphApplyResult(changed_node_ids=[node.id], impacted_node_ids=[node.id])
        raise ValueError("proposal is not auto-applicable")

    def build_deferred_mutation_record(self, proposal: MutationProposal, source_identity: dict[str, str]) -> DeferredMutationRecord:
        from datetime import datetime, timezone
        return DeferredMutationRecord(
            proposal_identity=proposal.proposal_identity,
            proposed_mutation_type=proposal.type,
            lifecycle_status="pending",
            affected_node_ids=proposal.affected_node_ids,
            affected_unresolved_names=proposal.affected_unresolved_names,
            target_parent_path=proposal.target_parent_path,
            target_name=proposal.target_name,
            target_replacement_node_id=proposal.target_replacement_node_id,
            target_paths=proposal.target_paths,
            confidence_score=proposal.confidence,
            reason=proposal.reason,
            supporting_source_note_paths=[source_identity["source_inbox_path"]],
            supporting_source_count=1,
            created_at=datetime.now(timezone.utc).isoformat(),
            resolved_at=None,
        )

    def _find_primary_path_proposal(self, primary_path: List[str], proposals: List[MutationProposal]) -> Optional[MutationProposal]:
        for proposal in proposals:
            if primary_path in proposal.target_paths:
                return proposal
        return None

    def finalize_additional_path(self, requested_path: List[str], proposals: List[MutationProposal], source_identity: dict[str, str]) -> tuple[SecondaryPlacementResult, List[DeferredMutationRecord]]:
        proposal = self._find_primary_path_proposal(requested_path, proposals)
        if proposal is None:
            node = self.get_node_by_path(requested_path)
            return SecondaryPlacementResult(
                requested_path=requested_path,
                canonical_node_id=node.id if node else None,
                placement_path=requested_path,
                placement_mode="canonical",
                deferred_path=None,
            ), []
        decision = self.evaluate_mutation(proposal)
        if decision.status == "auto_apply":
            self.apply_auto_mutation(proposal)
            node = self.get_node_by_path(proposal.target_paths[0])
            return SecondaryPlacementResult(
                requested_path=requested_path,
                canonical_node_id=node.id if node else None,
                placement_path=proposal.target_paths[0],
                placement_mode="canonical",
                deferred_path=None,
            ), []
        existing_ancestor = self.deepest_existing_path(proposal.target_paths[0])
        return SecondaryPlacementResult(
            requested_path=requested_path,
            canonical_node_id=self.get_node_by_path(existing_ancestor).id if self.get_node_by_path(existing_ancestor) else None,
            placement_path=existing_ancestor,
            placement_mode="deferred_to_existing_ancestor",
            deferred_path=proposal.target_paths[0],
        ), [self.build_deferred_mutation_record(proposal, source_identity)]

    def finalize_resolution(self, resolution: TopicResolution) -> GraphPlacementResult:
        deferred_records: List[DeferredMutationRecord] = []
        primary_path = resolution.requested_primary_path
        primary_proposal = self._find_primary_path_proposal(primary_path, resolution.mutation_proposals)
        for proposal in resolution.mutation_proposals:
            decision = self.evaluate_mutation(proposal)
            if decision.status == "auto_apply":
                self.apply_auto_mutation(proposal)
                if proposal is primary_proposal:
                    primary_path = proposal.target_paths[0]
                continue
            if proposal is not primary_proposal:
                deferred_records.append(self.build_deferred_mutation_record(proposal, resolution.source_identity))
        secondary_placements: List[SecondaryPlacementResult] = []
        for path in resolution.secondary_paths:
            placement, secondary_records = self.finalize_additional_path(path, resolution.mutation_proposals, resolution.source_identity)
            secondary_placements.append(placement)
            deferred_records.extend(secondary_records)
            
        primary_node = self.get_node_by_path(primary_path)
        if primary_proposal is None or primary_proposal not in resolution.mutation_proposals:
            return GraphPlacementResult(
                canonical_primary_path=primary_path,
                canonical_primary_node_id=primary_node.id if primary_node else None,
                placement_path=primary_path,
                placement_mode="canonical",
                deferred_primary_path=None,
                highest_confidence_replacement_path=None,
                secondary_placements=secondary_placements,
                secondary_node_ids=[item.canonical_node_id for item in secondary_placements if item.canonical_node_id],
                ancestor_node_ids=self.get_ancestor_ids(primary_path),
                secondary_ancestor_node_ids=sorted({ancestor for item in secondary_placements for ancestor in self.get_ancestor_ids(item.placement_path)}),
                deferred_mutation_records=deferred_records,
            )
            
        if all(record.proposal_identity != primary_proposal.proposal_identity for record in deferred_records):
            return GraphPlacementResult(
                canonical_primary_path=primary_path,
                canonical_primary_node_id=primary_node.id if primary_node else None,
                placement_path=primary_path,
                placement_mode="canonical",
                deferred_primary_path=None,
                highest_confidence_replacement_path=None,
                secondary_placements=secondary_placements,
                secondary_node_ids=[item.canonical_node_id for item in secondary_placements if item.canonical_node_id],
                ancestor_node_ids=self.get_ancestor_ids(primary_path),
                secondary_ancestor_node_ids=sorted({ancestor for item in secondary_placements for ancestor in self.get_ancestor_ids(item.placement_path)}),
                deferred_mutation_records=deferred_records,
            )
            
        existing_ancestor = self.deepest_existing_path(primary_proposal.target_paths[0])
        ancestor_node = self.get_node_by_path(existing_ancestor)
        return GraphPlacementResult(
            canonical_primary_path=existing_ancestor,
            canonical_primary_node_id=ancestor_node.id if ancestor_node else None,
            placement_path=existing_ancestor,
            placement_mode="deferred_to_existing_ancestor",
            deferred_primary_path=primary_proposal.target_paths[0],
            highest_confidence_replacement_path=None,
            secondary_placements=secondary_placements,
            secondary_node_ids=[item.canonical_node_id for item in secondary_placements if item.canonical_node_id],
            ancestor_node_ids=self.get_ancestor_ids(existing_ancestor),
            secondary_ancestor_node_ids=sorted({ancestor for item in secondary_placements for ancestor in self.get_ancestor_ids(item.placement_path)}),
            deferred_mutation_records=deferred_records,
        )
