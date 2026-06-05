import unittest

class MetadataStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_state_merges_equivalent_pending_mutations_by_identity(self):
        import tempfile
        from app.services.knowledge_pipeline.metadata_state import MetadataState
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()
            
            def first_record_for(identity, path):
                return {
                    "proposal_identity": identity,
                    "supporting_source_note_paths": [path],
                    "confidence_score": 0.8
                }
            
            async with state.write_lock():
                await state.merge_pending_mutations([
                    first_record_for("merge|n1,n2|replacement:n3", "inbox/a.md"),
                    first_record_for("merge|n1,n2|replacement:n3", "inbox/b.md"),
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
            graph = TopicGraph.empty()
            graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
            graph.create_node(name="短线交易", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
            
            resolution = await TopicPathResolver(fake_processor).resolve(units, graph)
            placement = graph.finalize_resolution(resolution)
            
            async with state.write_lock():
                await state.merge_pending_mutations([asdict(item) for item in placement.deferred_mutation_records])
            
            snapshot = await state.load_pending_mutations()
            self.assertEqual(snapshot["items"][0]["proposal_identity"], placement.deferred_mutation_records[0].proposal_identity)
