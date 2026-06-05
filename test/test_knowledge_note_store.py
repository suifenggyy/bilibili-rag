import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock
from app.services.knowledge_pipeline.knowledge_note_store import KnowledgeNoteStore, KnowledgeNoteFileMetadata
from app.services.knowledge_pipeline.topic_graph import GraphPlacementResult, TopicGraph

class KnowledgeNoteStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_moves_note_when_primary_path_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            graph = TopicGraph.empty()
            graph.create_node(name="", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
            node = graph.create_node(name="T", parent_path=[""], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
            
            store = KnowledgeNoteStore(tmp, graph)
            
            existing_mapping = {
                "knowledge_note_path": str(tmp / "old" / "note.md"),
                "knowledge_note_id": "n1",
                "primary_topic_node_id": node.id
            }
            (tmp / "old").mkdir()
            (tmp / "old" / "note.md").write_text("old content")
            
            placement_result = GraphPlacementResult(
                canonical_primary_path=["", "T"],
                canonical_primary_node_id=node.id,
                placement_path=["", "T"],
                placement_mode="canonical",
                deferred_primary_path=None,
                highest_confidence_replacement_path=None,
                secondary_placements=[],
                secondary_node_ids=[],
                ancestor_node_ids=[node.id],
                secondary_ancestor_node_ids=[],
                deferred_mutation_records=[]
            )
            
            result, processed_mapping = await store.write_note(
                knowledge_note_id="n1",
                mapping_record=existing_mapping,
                source_mapping_seed={"source_inbox_path": "a.md", "source_content_fingerprint": "fp1", "persisted_first_seen_inbox_path": "a.md"},
                placement=placement_result,
                note_metadata=KnowledgeNoteFileMetadata(title="T", published_date="2026-06-03"),
                rendered_markdown="# x",
            )
            
            self.assertTrue(result.final_path.name.endswith(".md"))
            self.assertEqual(processed_mapping["source_processing_status"], "processed")
            self.assertIn(existing_mapping["knowledge_note_path"], processed_mapping["prior_knowledge_note_paths"])

    def test_store_selects_primary_path_by_stability_priority(self):
        graph = TopicGraph.empty()
        store = KnowledgeNoteStore(Path("/tmp"), graph)
        
        placement = GraphPlacementResult(
            canonical_primary_path=["A", "B"],
            canonical_primary_node_id="n1",
            placement_path=["A", "B"],
            placement_mode="canonical",
            deferred_primary_path=None,
            highest_confidence_replacement_path=None,
            secondary_placements=[],
            secondary_node_ids=[],
            ancestor_node_ids=[],
            secondary_ancestor_node_ids=[],
            deferred_mutation_records=[]
        )
        
        # 1. if mapping has active primary node id, use it
        node = graph.create_node(name="B", parent_path=["A"], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        self.assertEqual(store.choose_storage_primary_path({"primary_topic_node_id": node.id}, placement), ["A", "B"])
        
        # 2. if active primary node missing but has highest_confidence_replacement_path, use it
        placement.highest_confidence_replacement_path = ["C", "D"]
        self.assertEqual(store.choose_storage_primary_path(None, placement), ["C", "D"])
        
        # 3. fallback to canonical
        placement.highest_confidence_replacement_path = None
        self.assertEqual(store.choose_storage_primary_path(None, placement), ["A", "B"])

    async def test_store_generates_stable_note_id_and_collision_safe_path(self):
        from app.services.knowledge_pipeline.knowledge_note_identity import build_knowledge_note_id
        note_id = build_knowledge_note_id({
            "source_url": "https://example.com/a",
            "published_date": "2026-06-03",
            "persisted_first_seen_inbox_path": "inbox/douyin/a.md",
            "title": "T",
        })
        
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            graph = TopicGraph.empty()
            node = graph.create_node(name="T", parent_path=[""], aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
            store = KnowledgeNoteStore(tmp, graph)
            
            placement_result = GraphPlacementResult(
                canonical_primary_path=["", "T"],
                canonical_primary_node_id=node.id,
                placement_path=["", "T"],
                placement_mode="canonical",
                deferred_primary_path=None,
                highest_confidence_replacement_path=None,
                secondary_placements=[],
                secondary_node_ids=[],
                ancestor_node_ids=[],
                secondary_ancestor_node_ids=[],
                deferred_mutation_records=[]
            )
            
            # create collision file
            (tmp / "" / "T").mkdir(parents=True)
            (tmp / "" / "T" / "2026-06-03-t.md").write_text("other")
            
            result, processed_mapping = await store.write_note(
                knowledge_note_id=note_id,
                mapping_record=None,
                source_mapping_seed={"source_inbox_path": "a.md", "source_content_fingerprint": "fp1", "persisted_first_seen_inbox_path": "a.md"},
                placement=placement_result,
                note_metadata=KnowledgeNoteFileMetadata(title="T", published_date="2026-06-03"),
                rendered_markdown="# x",
            )
            
            self.assertTrue(result.final_path.name.endswith(f"-{note_id[:8]}.md"))
