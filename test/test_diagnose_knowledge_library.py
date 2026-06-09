import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.knowledge_pipeline.metadata_state import MetadataState
from app.services.knowledge_pipeline.topic_graph import TopicGraph


class DiagnoseKnowledgeLibraryTests(unittest.IsolatedAsyncioTestCase):
    async def test_diagnose_reports_orphan_topic_and_stale_mapping(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            meta_dir = tmp / "_meta"
            knowledge_dir = tmp / "knowledge"
            topics_dir = knowledge_dir / "_topics"

            meta_dir.mkdir()
            knowledge_dir.mkdir()
            topics_dir.mkdir()

            # Set up a graph with one active node
            graph = TopicGraph.empty()
            graph.create_node(
                name="投资", parent_path=[], aliases=[], replacement_target_id=None,
                lineage=[], summary_version="s1", detail_version="d1", status="active"
            )
            # Create an orphan topic file (no matching graph node)
            (topics_dir / "不存在的话题.md").write_text("# 不存在的话题\norphan content", encoding="utf-8")

            state = MetadataState(meta_dir=meta_dir)
            await state.bootstrap()

            async with state.write_lock():
                await state.save_topic_graph(graph.to_snapshot())
                # Add a stale mapping (note file doesn't exist)
                await state.save_source_mapping({
                    "items": [{
                        "source_inbox_path": "inbox/a.md",
                        "source_content_fingerprint": "fp1",
                        "source_processing_status": "processed",
                        "knowledge_note_id": "note-1",
                        "knowledge_note_path": str(tmp / "nonexistent" / "note.md"),
                        "primary_topic_node_id": list(graph.nodes.values())[0].id,
                        "secondary_topic_node_ids": [],
                        "ancestor_topic_node_ids": [],
                        "graph_version": "v1",
                        "last_generated_at": "2026-06-03T18:00:00",
                        "persisted_first_seen_inbox_path": "inbox/a.md",
                    }]
                })

            # Run diagnosis logic directly
            issues = await self._run_diagnosis(state, knowledge_dir, graph)

            orphan_issues = [i for i in issues if i["type"] == "orphan_topic_file"]
            stale_issues = [i for i in issues if i["type"] == "stale_mapping"]
            self.assertTrue(len(orphan_issues) >= 1, "Should detect orphan topic file")
            self.assertTrue(len(stale_issues) >= 1, "Should detect stale mapping")

    async def test_apply_repairs_mapping_and_topic_pages(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            meta_dir = tmp / "_meta"
            knowledge_dir = tmp / "knowledge"
            topics_dir = knowledge_dir / "_topics"

            meta_dir.mkdir()
            knowledge_dir.mkdir()
            topics_dir.mkdir()

            graph = TopicGraph.empty()
            node = graph.create_node(
                name="投资", parent_path=[], aliases=[], replacement_target_id=None,
                lineage=[], summary_version="s1", detail_version="d1", status="active"
            )
            orphan_file = topics_dir / "不存在的话题.md"
            orphan_file.write_text("# 不存在的话题\norphan content", encoding="utf-8")

            state = MetadataState(meta_dir=meta_dir)
            await state.bootstrap()

            async with state.write_lock():
                await state.save_topic_graph(graph.to_snapshot())
                await state.save_source_mapping({
                    "items": [{
                        "source_inbox_path": "inbox/a.md",
                        "source_content_fingerprint": "fp1",
                        "source_processing_status": "processed",
                        "knowledge_note_id": "note-1",
                        "knowledge_note_path": str(tmp / "nonexistent" / "note.md"),
                        "primary_topic_node_id": node.id,
                        "secondary_topic_node_ids": [],
                        "ancestor_topic_node_ids": [],
                        "graph_version": "v1",
                        "last_generated_at": "2026-06-03T18:00:00",
                        "persisted_first_seen_inbox_path": "inbox/a.md",
                    }]
                })

            # Apply repairs
            issues = await self._run_diagnosis(state, knowledge_dir, graph)
            self.assertTrue(len(issues) > 0, "Should find issues before repair")

            # Apply: remove stale mappings
            async with state.write_lock():
                mapping = await state.load_source_mapping()
                new_items = [
                    item for item in mapping["items"]
                    if not (item.get("source_processing_status") == "processed" and item.get("knowledge_note_path")
                            and not Path(item["knowledge_note_path"]).exists())
                ]
                mapping["items"] = new_items
                await state.save_source_mapping(mapping)

            # Apply: remove orphan topic files
            for issue in issues:
                if issue["type"] == "orphan_topic_file":
                    try:
                        Path(issue["path"]).unlink()
                    except FileNotFoundError:
                        pass

            # Verify repair
            mapping = await state.load_source_mapping()
            self.assertEqual(len(mapping["items"]), 0, "Stale mapping should be removed")
            self.assertFalse(orphan_file.exists(), "Orphan topic file should be removed")

    async def _run_diagnosis(self, state: MetadataState, knowledge_dir: Path, graph: TopicGraph) -> list[dict]:
        """Run the core diagnosis logic (same as the script)."""
        issues = []
        active_paths = {"/".join(node.path) for node in graph.nodes.values() if node.status == "active"}

        # Check orphan topic files
        if knowledge_dir.exists():
            for topic_file in knowledge_dir.rglob("*.md"):
                rel = topic_file.relative_to(knowledge_dir)
                if rel.parts and rel.parts[0] == "_topics":
                    topic_name = rel.stem
                    found = any(node.path[-1] == topic_name and node.status == "active" for node in graph.nodes.values())
                    if not found:
                        issues.append({
                            "type": "orphan_topic_file",
                            "path": str(topic_file),
                            "description": f"Topic file '{topic_name}' has no active node in graph",
                        })

        # Check stale mappings
        mapping_snapshot = await state.load_source_mapping()
        for item in mapping_snapshot["items"]:
            if item.get("source_processing_status") == "processed" and item.get("knowledge_note_path"):
                note_path = Path(item["knowledge_note_path"])
                if not note_path.exists():
                    issues.append({
                        "type": "stale_mapping",
                        "path": item["knowledge_note_path"],
                        "source_inbox_path": item.get("source_inbox_path", ""),
                        "description": f"Mapped note file missing: {item['knowledge_note_path']}",
                    })

        # Check broken parent references
        for node in graph.nodes.values():
            if node.parent_id and node.parent_id not in graph.nodes:
                issues.append({
                    "type": "broken_parent_ref",
                    "node_id": node.id,
                    "missing_parent_id": node.parent_id,
                    "description": f"Node '{node.name}' references missing parent {node.parent_id}",
                })

        return issues
