import unittest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from typing import Dict, List, Any
from app.services.knowledge_pipeline.topic_rebuilder import TopicRebuilder


class TopicRebuilderTests(unittest.IsolatedAsyncioTestCase):
    async def test_rebuilder_rewrites_summary_only_when_facets_change(self):
        # Mock dependencies
        mock_graph = Mock()
        mock_graph.get_node = Mock(return_value=Mock(id="topic-1", path=["技术"], status="active"))
        mock_graph.get_descendants = Mock(return_value=[])

        mock_metadata_state = Mock()
        mock_metadata_state.load_source_mapping = AsyncMock(return_value={
            "items": [
                {"primary_topic_node_id": "topic-1", "knowledge_note_path": "/path/1.md", "knowledge_note_id": "n1", "persisted_first_seen_inbox_path": "inbox/note1.md"}
            ]
        })

        mock_distiller = Mock()
        mock_distiller.distill_topic_summary = AsyncMock(return_value="Updated summary based on notes.")

        mock_store = Mock()

        mock_renderer = Mock()
        mock_renderer.render = Mock(return_value="Rendered Markdown")

        rebuilder = TopicRebuilder(
            graph=mock_graph,
            metadata_state=mock_metadata_state,
            distiller=mock_distiller,
            store=mock_store,
            renderer=mock_renderer
        )

        # Mock the _read_note_content to return content
        rebuilder._read_note_content = AsyncMock(return_value="Content of Note 1")

        result = await rebuilder.rebuild_nodes(["topic-1"])

        self.assertIn("topic-1", result)
        self.assertTrue(result["topic-1"]["updated_summary"])
        self.assertEqual(result["topic-1"]["markdown"], "Rendered Markdown")

        mock_distiller.distill_topic_summary.assert_called_once()
        mock_renderer.render.assert_called_once()

    async def test_rebuilder_skips_summary_if_no_new_notes(self):
        # Mock dependencies
        mock_graph = Mock()
        mock_graph.get_node = Mock(return_value=Mock(id="topic-2", path=["科学"], status="active"))
        mock_graph.get_descendants = Mock(return_value=[])

        mock_metadata_state = Mock()
        mock_metadata_state.load_source_mapping = AsyncMock(return_value={"items": []})

        mock_distiller = Mock()

        mock_store = Mock()

        mock_renderer = Mock()
        mock_renderer.render = Mock(return_value="Rendered Markdown without summary update")

        rebuilder = TopicRebuilder(
            graph=mock_graph,
            metadata_state=mock_metadata_state,
            distiller=mock_distiller,
            store=mock_store,
            renderer=mock_renderer
        )

        result = await rebuilder.rebuild_nodes(["topic-2"], force_summary=False)

        self.assertIn("topic-2", result)
        self.assertFalse(result["topic-2"]["updated_summary"])
        mock_distiller.distill_topic_summary.assert_not_called()
