import unittest
import json

class MetadataStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_state_merges_equivalent_pending_mutations_by_identity(self):
        import tempfile
        from app.services.knowledge_pipeline.metadata_state import MetadataState
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()

            def make_record(identity, path):
                return {
                    "proposal_identity": identity,
                    "proposed_mutation_type": "merge",
                    "lifecycle_status": "pending",
                    "affected_node_ids": ["n1", "n2"],
                    "affected_unresolved_names": [],
                    "target_parent_path": ["投资"],
                    "target_name": "做T",
                    "target_replacement_node_id": "n3",
                    "target_paths": [["投资", "做T"]],
                    "confidence_score": 0.8,
                    "reason": "语义重复",
                    "supporting_source_note_paths": [path],
                    "supporting_source_count": 1,
                    "created_at": "2026-06-03T18:00:00",
                    "resolved_at": None,
                }

            async with state.write_lock():
                await state.merge_pending_mutations([
                    make_record("merge|n1,n2|replacement:n3", "inbox/a.md"),
                    make_record("merge|n1,n2|replacement:n3", "inbox/b.md"),
                ])
            snapshot = await state.load_pending_mutations()
            self.assertEqual(snapshot["items"][0]["supporting_source_count"], 2)

    async def test_finalize_resolution_records_are_persisted_into_pending_mutations(self):
        import tempfile
        from unittest.mock import AsyncMock
        from pathlib import Path
        from app.services.knowledge_pipeline.metadata_state import MetadataState
        from app.services.knowledge_pipeline.topic_path_resolver import TopicPathResolver
        from app.services.knowledge_pipeline.knowledge_distiller import DistilledKnowledge
        from app.services.knowledge_pipeline.topic_graph import TopicGraph
        from dataclasses import asdict

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()

            fake_processor = AsyncMock(return_value={
                "primary_path": ["投资", "短线交易", "做T"],
                "secondary_paths": [],
                "mutation_proposals": [
                    {
                        "type": "create_leaf",
                        "confidence": 0.5,
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
            graph = TopicGraph.empty()
            graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
            graph.create_node(name="短线交易", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")

            resolution = await TopicPathResolver(fake_processor).resolve(units, graph)
            placement = await graph.finalize_resolution(resolution)

            async with state.write_lock():
                await state.merge_pending_mutations([asdict(item) for item in placement.deferred_mutation_records])

            snapshot = await state.load_pending_mutations()
            self.assertEqual(snapshot["items"][0]["proposal_identity"], placement.deferred_mutation_records[0].proposal_identity)

    async def test_metadata_state_rejects_corrupted_snapshot_on_load(self):
        import tempfile
        from app.services.knowledge_pipeline.metadata_state import MetadataState
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            meta_dir = tmp / "_meta"
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / "topic-detail-index.json").write_text("{bad json", encoding="utf-8")
            state = MetadataState(meta_dir=meta_dir)
            with self.assertRaisesRegex(ValueError, "corrupted metadata file"):
                await state.load_topic_detail_index()

    async def test_metadata_state_rejects_valid_json_with_invalid_detail_type(self):
        import tempfile
        from app.services.knowledge_pipeline.metadata_state import MetadataState
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            meta_dir = tmp / "_meta"
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / "topic-detail-index.json").write_text(json.dumps({
                "items": [{
                    "topic_node_id": "t1",
                    "detail_fingerprint": "fp1",
                    "detail_type": "heuristic",
                    "normalized_semantic_statement": "invalid enum",
                    "supporting_source_inbox_paths": ["inbox/a.md"],
                    "last_updated_at": "2026-06-03T18:00:00"
                }]
            }), encoding="utf-8")
            state = MetadataState(meta_dir=meta_dir)
            with self.assertRaisesRegex(ValueError, "invalid detail_type"):
                await state.load_topic_detail_index()

    async def test_metadata_state_rejects_processed_source_without_note_identity(self):
        import tempfile
        from app.services.knowledge_pipeline.metadata_state import MetadataState
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()
            async with state.write_lock():
                with self.assertRaisesRegex(ValueError, "processed items require knowledge_note_id"):
                    await state.save_source_mapping({
                        "items": [{
                            "source_inbox_path": "inbox/a.md",
                            "source_content_fingerprint": "sha256:1",
                            "source_processing_status": "processed",
                            "knowledge_note_id": None,
                            "knowledge_note_path": "knowledge/a.md",
                            "primary_topic_node_id": "topic-1",
                            "secondary_topic_node_ids": [],
                            "ancestor_topic_node_ids": [],
                            "graph_version": "v1",
                            "last_generated_at": "2026-06-03T18:00:00",
                            "persisted_first_seen_inbox_path": "inbox/a.md"
                        }]
                    })

    async def test_metadata_state_accepts_tombstoned_source_with_note_identity(self):
        import tempfile
        from app.services.knowledge_pipeline.metadata_state import MetadataState
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()
            async with state.write_lock():
                await state.save_source_mapping({
                    "items": [{
                        "source_inbox_path": "inbox/a.md",
                        "source_content_fingerprint": "sha256:1",
                        "source_processing_status": "tombstoned",
                        "knowledge_note_id": "note-1",
                        "knowledge_note_path": "knowledge/a.md",
                        "primary_topic_node_id": "topic-1",
                        "secondary_topic_node_ids": [],
                        "ancestor_topic_node_ids": [],
                        "graph_version": "v1",
                        "last_generated_at": "2026-06-03T18:00:00",
                        "persisted_first_seen_inbox_path": "inbox/a.md"
                    }]
                })

    async def test_metadata_state_rejects_unlocked_mutation(self):
        import tempfile
        from app.services.knowledge_pipeline.metadata_state import MetadataState
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()
            with self.assertRaises(RuntimeError):
                await state.save_topic_detail_index({"items": []})

    async def test_metadata_state_accepts_repair_run_log_record(self):
        import tempfile
        from app.services.knowledge_pipeline.metadata_state import MetadataState
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()
            async with state.write_lock():
                await state.append_run_log_start({
                    "run_id": "repair-1",
                    "run_scope": "repair",
                    "batch_selector": {"mode": "dry-run", "target_paths": ["knowledge/投资"]},
                    "status": "started",
                    "files_intended": [],
                    "files_written": [],
                    "graph_changed": False,
                    "mapping_changed": False,
                    "started_at": "2026-06-03T18:00:00",
                    "completed_at": None
                })
            run_log = await state.load_run_log()
            self.assertEqual(run_log["items"][0]["run_scope"], "repair")

    async def test_metadata_state_accepts_migration_run_log_record(self):
        import tempfile
        from app.services.knowledge_pipeline.metadata_state import MetadataState
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()
            async with state.write_lock():
                await state.append_run_log_start({
                    "run_id": "migration-1",
                    "run_scope": "migration",
                    "batch_selector": {"mode": "full-library", "target_paths": ["inbox", "knowledge"]},
                    "status": "started",
                    "files_intended": [],
                    "files_written": [],
                    "graph_changed": False,
                    "mapping_changed": False,
                    "started_at": "2026-06-03T18:00:00",
                    "completed_at": None
                })
            run_log = await state.load_run_log()
            self.assertEqual(run_log["items"][0]["run_scope"], "migration")

    async def test_metadata_state_rejects_invalid_mutation_type_and_confidence(self):
        import tempfile
        from app.services.knowledge_pipeline.metadata_state import MetadataState
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()
            async with state.write_lock():
                with self.assertRaises(ValueError):
                    await state.save_pending_mutations({
                        "items": [{
                            "proposal_identity": "unknown|n1|parent:投资",
                            "proposed_mutation_type": "unknown",
                            "lifecycle_status": "pending",
                            "affected_node_ids": ["n1"],
                            "affected_unresolved_names": [],
                            "target_parent_path": ["投资"],
                            "target_name": "做T",
                            "target_replacement_node_id": None,
                            "target_paths": [],
                            "confidence_score": 1.2,
                            "reason": "bad payload",
                            "supporting_source_note_paths": ["inbox/a.md"],
                            "supporting_source_count": 1,
                            "created_at": "2026-06-03T18:00:00",
                            "resolved_at": None
                        }]
                    })

    async def test_metadata_state_exposes_cross_instance_write_lock(self):
        import tempfile
        from app.services.knowledge_pipeline.metadata_state import MetadataState, MetadataWriteLockTimeout
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            state_a = MetadataState(meta_dir=tmp / "_meta")
            state_b = MetadataState(meta_dir=tmp / "_meta")
            await state_a.bootstrap()
            # Clean up any leftover lock from bootstrap
            lock_path = tmp / "_meta" / ".write.lock"
            lock_path.unlink(missing_ok=True)

            async with state_a.write_lock():
                self.assertTrue(lock_path.exists())
                # state_b should timeout since state_a holds the file lock
                with self.assertRaises(MetadataWriteLockTimeout):
                    async with state_b.write_lock(timeout_seconds=0.01):
                        pass

    async def test_metadata_state_records_prelock_contention_failure(self):
        import tempfile
        import asyncio
        from app.services.knowledge_pipeline.metadata_state import MetadataState, MetadataWriteLockTimeout
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            state_a = MetadataState(meta_dir=tmp / "_meta")
            state_b = MetadataState(meta_dir=tmp / "_meta")
            await state_a.bootstrap()
            # Clean up any leftover lock from bootstrap
            lock_path = tmp / "_meta" / ".write.lock"
            lock_path.unlink(missing_ok=True)

            # Use an event to coordinate: state_b tries to write while state_a holds the lock,
            # then state_a releases so state_b can record the contention failure.
            release_event = asyncio.Event()
            contention_result = []

            async def state_b_task():
                try:
                    async with state_b.transactional_write(
                        run_scope="single_note",
                        context={
                            "source_note_paths": ["inbox/a.md"],
                            "files_intended": [],
                        },
                        timeout_seconds=0.01,
                    ):
                        pass
                except MetadataWriteLockTimeout as exc:
                    contention_result.append(exc)
                    # Now state_a should release soon, allowing _record_lock_contention_after_release to proceed

            async with state_a.write_lock():
                # Start state_b's task concurrently - it will timeout and then wait for state_a to release
                task = asyncio.create_task(state_b_task())
                # Give state_b time to hit the timeout
                await asyncio.sleep(0.1)
                # state_b has timed out and is now waiting for the lock to record the failure
                # Release the lock so state_b can record
                # (exiting the 'async with' block releases the lock)

            # Wait for state_b's contention recording to complete
            await task

            self.assertTrue(contention_result, "state_b should have hit lock contention")
            run_log = await state_b.load_run_log()
            failed_record = next(
                item for item in run_log["items"]
                if item.get("failure_reason") == "lock_contention"
            )
            self.assertEqual(failed_record["status"], "failed")
            self.assertEqual(failed_record["failure_reason"], "lock_contention")
            self.assertEqual(failed_record["lock_wait_timeout_seconds"], 0.01)


class MutationIdentityTests(unittest.TestCase):
    def test_build_mutation_identity_distinguishes_new_leaf_names(self):
        from app.services.knowledge_pipeline.topic_graph import build_mutation_identity
        self.assertNotEqual(
            build_mutation_identity(
                mutation_type="create_leaf",
                affected_node_ids=[],
                affected_unresolved_names=["做T"],
                target_parent_path=["投资", "短线交易"],
                target_replacement_node_id=None,
            ),
            build_mutation_identity(
                mutation_type="create_leaf",
                affected_node_ids=[],
                affected_unresolved_names=["止损"],
                target_parent_path=["投资", "短线交易"],
                target_replacement_node_id=None,
            ),
        )
