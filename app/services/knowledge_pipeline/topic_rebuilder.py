from typing import Dict, List, Any
from pathlib import Path
from loguru import logger

from .topic_graph import TopicGraph
from .metadata_state import MetadataState
from .knowledge_distiller import KnowledgeDistiller
from .knowledge_note_store import KnowledgeNoteStore
from .topic_page_renderer import TopicPageRenderer


class TopicRebuilder:
    def __init__(
        self,
        graph: TopicGraph,
        metadata_state: MetadataState,
        distiller: KnowledgeDistiller,
        store: KnowledgeNoteStore,
        renderer: TopicPageRenderer
    ):
        self.graph = graph
        self.metadata_state = metadata_state
        self.distiller = distiller
        self.store = store
        self.renderer = renderer

    async def rebuild_nodes(self, node_ids: List[str], force_summary: bool = False) -> Dict[str, Dict[str, Any]]:
        results = {}
        for node_id in node_ids:
            node = self.graph.get_node(node_id)
            if not node or node.status != "active":
                continue

            # Find associated notes from source mapping
            mapping_snapshot = await self.metadata_state.load_source_mapping()
            node_records = [
                r for r in mapping_snapshot["items"]
                if r.get("primary_topic_node_id") == node_id
            ]

            updated_summary = False
            summary_content = ""

            if node_records:
                # Read note contents from file system
                contents = []
                for r in node_records:
                    note_path_str = r.get("knowledge_note_path", "")
                    if note_path_str:
                        content = await self._read_note_content(note_path_str)
                        if content:
                            contents.append(content)

                if contents or force_summary:
                    # Use the distiller's processor to generate a topic summary
                    try:
                        summary_content = await self.distiller.distill_topic_summary(
                            topic_name=node.path[-1],
                            note_contents=contents
                        )
                        updated_summary = True
                    except Exception as exc:
                        logger.warning(f"[TopicRebuilder] failed to distill summary for {node.path}: {exc}")
                        summary_content = self._build_summary_from_notes(node_records, contents)

            # Prepare payload for renderer
            subtopics = []
            for child in self.graph.get_descendants(node_id):
                if len(child.path) == len(node.path) + 1 and child.status == "active":
                    subtopics.append({
                        "id": child.id,
                        "name": child.path[-1],
                        "path": child.path,
                    })

            knowledge_notes = []
            for r in node_records:
                knowledge_notes.append({
                    "id": r.get("knowledge_note_id", ""),
                    "path": r.get("knowledge_note_path", ""),
                    "title": r.get("persisted_first_seen_inbox_path", "").split("/")[-1] if r.get("persisted_first_seen_inbox_path") else "",
                })

            payload = {
                "topic_id": node.id,
                "topic_path": node.path,
                "summary_content": summary_content or "暂无概览信息",
                "subtopics": subtopics,
                "knowledge_notes": knowledge_notes,
                "status": node.status
            }

            markdown = self.renderer.render(payload)

            results[node_id] = {
                "updated_summary": updated_summary,
                "markdown": markdown
            }

        return results

    async def _read_note_content(self, path_str: str) -> str | None:
        """Read note content from file system."""
        path = Path(path_str)
        if not path.exists():
            return None
        try:
            import aiofiles
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                return await f.read()
        except Exception as exc:
            logger.warning(f"[TopicRebuilder] failed to read note {path_str}: {exc}")
            return None

    def _build_summary_from_notes(self, records: list[dict], contents: list[str]) -> str:
        """Fallback: build a simple summary from note records when LLM distillation fails."""
        lines = []
        for i, r in enumerate(records):
            title = r.get("persisted_first_seen_inbox_path", "").split("/")[-1] if r.get("persisted_first_seen_inbox_path") else f"Note {i+1}"
            lines.append(f"- {title}")
        return "\n".join(lines) if lines else "暂无概览信息"
