import unittest
from app.services.knowledge_pipeline.topic_graph import TopicGraph, MutationProposal

class TopicGraphTests(unittest.IsolatedAsyncioTestCase):
    def test_topic_graph_creates_parent_child_nodes(self):
        graph = TopicGraph.empty()
        graph.create_node(
            name="",
            parent_path=[],
            aliases=[],
            replacement_target_id=None,
            lineage=[],
            summary_version="s0",
            detail_version="d0",
            status="active",
        )
        graph.apply_new_leaf(parent_path=[""], child_name="T")
        self.assertEqual(graph.get_node_by_path(["", "T"]).parent_id, graph.get_node_by_path([""]).id)

    def test_merge_mutation_is_deferred(self):
        graph = TopicGraph.empty()
        result = graph.evaluate_mutation(MutationProposal(type="merge", proposal_identity="merge|a,b|replacement:c", affected_node_ids=["a", "b"], affected_unresolved_names=[], target_parent_path=[], target_name="", target_replacement_node_id="c", target_paths=[], confidence=0.99, impacted_existing_nodes=2, replaced_canonical_paths=1, reason="overlap"))
        self.assertEqual(result.status, "pending")

    def test_topic_graph_round_trips_full_node_contract(self):
        graph = TopicGraph.empty()
        node = graph.create_node(
            name="做T",
            parent_path=["投资", "短线交易"],
            aliases=["日内回转"],
            replacement_target_id=None,
            lineage=["投资/日内交易/做T"],
            summary_version="sum-v1",
            detail_version="detail-v1",
            status="active",
        )
        snapshot = graph.to_snapshot()
        restored = TopicGraph.from_snapshot(snapshot)
        restored_node = restored.get_node(node.id)
        self.assertEqual(restored_node.path, ["投资", "短线交易", "做T"])
        self.assertEqual(restored_node.lineage, ["投资/日内交易/做T"])
        self.assertEqual(restored_node.summary_version, "sum-v1")
        self.assertEqual(restored_node.detail_version, "detail-v1")

    def test_add_alias_auto_applies_only_within_thresholds(self):
        graph = TopicGraph.empty()
        proposal = MutationProposal(
            type="add_alias",
            proposal_identity="alias", affected_node_ids=["topic-1"], affected_unresolved_names=[], target_parent_path=[], target_name="", target_replacement_node_id="", target_paths=[], reason="",
            confidence=0.90,
            impacted_existing_nodes=1,
            replaced_canonical_paths=0,
        )
        self.assertEqual(graph.evaluate_mutation(proposal).status, "auto_apply")

        risky = MutationProposal(
            type="add_alias",
            proposal_identity="alias", affected_node_ids=["topic-1"], affected_unresolved_names=[], target_parent_path=[], target_name="", target_replacement_node_id="", target_paths=[], reason="",
            confidence=0.90,
            impacted_existing_nodes=6,
            replaced_canonical_paths=0,
        )
        self.assertEqual(graph.evaluate_mutation(risky).status, "pending")

    def test_create_leaf_only_auto_applies_under_existing_canonical_parent(self):
        graph = TopicGraph.empty()
        graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        graph.create_node(name="短线交易", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        safe = MutationProposal(
            type="create_leaf",
            proposal_identity="leaf", affected_node_ids=[], affected_unresolved_names=[], target_replacement_node_id="", target_paths=[], reason="",
            confidence=0.90,
            target_parent_path=["投资", "短线交易"],
            target_name="做T",
            impacted_existing_nodes=1,
            replaced_canonical_paths=0,
        )
        self.assertEqual(graph.evaluate_mutation(safe).status, "auto_apply")

        # When parent path doesn't exist but no conflicts, create_leaf auto-applies
        # (it will create the missing ancestor nodes automatically)
        missing_parent = MutationProposal(
            type="create_leaf",
            proposal_identity="leaf", affected_node_ids=[], affected_unresolved_names=[], target_replacement_node_id="", target_paths=[], reason="",
            confidence=0.95,
            target_parent_path=["投资", "新子主题"],
            target_name="做T",
            impacted_existing_nodes=1,
            replaced_canonical_paths=0,
        )
        self.assertEqual(graph.evaluate_mutation(missing_parent).status, "auto_apply")

        # But if a segment along the path conflicts with a non-active node, defer
        graph.get_node_by_path(["投资"]).status = "deprecated"
        conflicting_parent = MutationProposal(
            type="create_leaf",
            proposal_identity="leaf", affected_node_ids=[], affected_unresolved_names=[], target_replacement_node_id="", target_paths=[], reason="",
            confidence=0.95,
            target_parent_path=["投资", "新子主题"],
            target_name="做T",
            impacted_existing_nodes=1,
            replaced_canonical_paths=0,
        )
        self.assertEqual(graph.evaluate_mutation(conflicting_parent).status, "pending")

    def test_finalize_resolution_keeps_non_primary_pending_mutations(self):
        from app.services.knowledge_pipeline.topic_graph import TopicResolution
        graph = TopicGraph.empty()
        graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        graph.create_node(name="短线交易", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        resolution = TopicResolution(
            requested_primary_path=["投资", "短线交易"],
            secondary_paths=[],
            mutation_proposals=[
                MutationProposal(
                    type="rename",
                    proposal_identity="rename|topic-1|短线做T",
                    affected_node_ids=["topic-1"],
                    affected_unresolved_names=[], target_parent_path=[], target_name="短线做T", target_replacement_node_id="",
                    target_paths=[["投资", "短线做T"]],
                    confidence=0.92,
                    impacted_existing_nodes=2,
                    replaced_canonical_paths=1,
                    reason="更准确命名",
                )
            ],
            source_identity={"source_inbox_path": "inbox/douyin/a.md"},
        )
        placement = graph.finalize_resolution(resolution)
        self.assertEqual(len(placement.deferred_mutation_records), 1)

    def test_rename_keeps_node_id_and_backfills_alias_and_lineage(self):
        graph = TopicGraph.empty()
        graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        node = graph.create_node(name="做T", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        graph.rename_node(node.id, new_name="日内回转")
        renamed = graph.get_node(node.id)
        self.assertEqual(renamed.id, node.id)
        self.assertIn("做T", renamed.aliases)
        self.assertIn("投资/做T", renamed.lineage)
        self.assertEqual(renamed.path, ["投资", "日内回转"])

    def test_move_node_rewrites_descendant_paths_and_backfills_lineage(self):
        graph = TopicGraph.empty()
        graph.create_node(name="", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        graph.create_node(name="短线交易", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        parent = graph.create_node(name="做T", parent_path=["投资", "短线交易"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        child = graph.create_node("仓位控制", parent_path=["投资", "短线交易", "做T"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        graph.move_node(parent.id, target_parent_path=["投资"])
        self.assertEqual(graph.get_node(parent.id).path, ["投资", "做T"])
        self.assertIn("投资/短线交易/做T", graph.get_node(parent.id).lineage)
        self.assertEqual(graph.get_node(child.id).path, ["投资", "做T", "仓位控制"])
        self.assertNotIn(parent.id, graph.get_node_by_path(["投资", "短线交易"]).children_ids)
        self.assertIn(parent.id, graph.get_node_by_path(["投资"]).children_ids)

    def test_merge_split_and_replace_backfill_lineage_and_replacement_target(self):
        graph = TopicGraph.empty()
        graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        left = graph.create_node(name="短线交易", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        right = graph.create_node(name="日内交易", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        replacement = graph.create_node(name="日内回转", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        source = graph.create_node(name="旧节点", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
        merge_result = graph.merge_nodes([left.id, right.id], replacement_node_id=replacement.id)
        self.assertIn(replacement.id, merge_result.changed_node_ids)
        self.assertEqual(graph.get_node(left.id).replacement_target_id, replacement.id)
        split_result = graph.split_node(source.id, [["投资", "短线交易策略"], ["投资", "波段交易"]])
        self.assertIn(source.id, split_result.changed_node_ids)
        replace_result = graph.replace_node(left.id, replacement_node_id=replacement.id)
        self.assertEqual(replace_result.changed_node_ids, [left.id, replacement.id])

    async def test_topic_graph_save_is_atomic(self):
        import tempfile
        from pathlib import Path
        from app.services.knowledge_pipeline.metadata_state import MetadataState
        
        graph = TopicGraph.empty()
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            state = MetadataState(meta_dir=tmp_path / "_meta")
            await state.bootstrap()
            async with state.write_lock():
                await graph.save(state)
            self.assertTrue((tmp_path / "_meta" / "topic-graph.json").exists())
            self.assertFalse((tmp_path / "_meta" / "topic-graph.json.tmp").exists())

    async def test_topic_graph_load_rejects_corrupted_snapshot(self):
        import tempfile
        from pathlib import Path
        from app.services.knowledge_pipeline.metadata_state import MetadataState
        
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            state = MetadataState(meta_dir=tmp_path / "_meta")
            await state.bootstrap()
            (tmp_path / "_meta" / "topic-graph.json").write_text('{"version":"topic-graph-v1","nodes":"bad"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid topic graph snapshot"):
                await TopicGraph.empty().load(state)
