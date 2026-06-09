from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import aiofiles
import re
from datetime import datetime, timezone
from .topic_graph import TopicGraph, GraphPlacementResult

@dataclass
class KnowledgeNoteFileMetadata:
    title: str
    published_date: str

@dataclass
class NoteWriteResult:
    note_id: str
    final_path: Path
    prior_path: Optional[Path]
    placement_path: List[str]
    canonical_primary_path: List[str]
    deferred_primary_path: Optional[List[str]]

class KnowledgeNoteStore:
    def __init__(self, knowledge_root: Path, graph: TopicGraph):
        self.knowledge_root = knowledge_root
        self.graph = graph

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _slugify(text: str) -> str:
        text = str(text).lower()
        text = re.sub(r'[^\w\s-]', '', text)
        text = re.sub(r'[-\s]+', '-', text).strip('-')
        return text

    def choose_storage_primary_path(self, mapping_record: Optional[Dict], placement: GraphPlacementResult) -> List[str]:
        current_primary_node_id = mapping_record.get("primary_topic_node_id") if mapping_record else None
        if current_primary_node_id and self.graph.get_node(current_primary_node_id) and self.graph.get_node(current_primary_node_id).status == "active":
            return self.graph.get_node(current_primary_node_id).path
        if current_primary_node_id:
            # Need to implement deepest_surviving_ancestor_node and replacement_target_path
            # For simplicity, fallback to canonical primary path if node is not active
            pass
        if placement.highest_confidence_replacement_path:
            return placement.highest_confidence_replacement_path
        return placement.canonical_primary_path

    def _render_base_path(self, placement_path: List[str], published_date: str, title: str) -> Path:
        safe_segments = [self._slugify(segment) for segment in placement_path]
        safe_title = self._slugify(title)
        date_prefix = published_date or "undated"
        return self.knowledge_root.joinpath(*safe_segments, f"{date_prefix}-{safe_title}.md")

    def _lookup_existing_path_for_note_id(self, note_id: str) -> Optional[Path]:
        # Implement lookup logic based on metadata state if needed
        # For now, rely on collision detection in _build_note_path
        return None

    def _build_note_path(self, placement_path: List[str], published_date: str, title: str, note_id: str) -> Path:
        candidate = self._render_base_path(placement_path, published_date, title)
        existing_note_path = self._lookup_existing_path_for_note_id(note_id)
        if existing_note_path and existing_note_path == candidate:
            return existing_note_path
        if not candidate.exists():
            return candidate
        return candidate.with_name(f"{candidate.stem}-{note_id[:8]}{candidate.suffix}")

    async def _move_with_history(self, prior_path: Path, final_path: Path) -> None:
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if prior_path.exists():
            prior_path.replace(final_path)

    async def _write_markdown(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, 'w', encoding='utf-8') as f:
            await f.write(content)

    def build_processed_mapping_record(
        self,
        mapping_record: Optional[Dict],
        source_mapping_seed: Dict,
        storage_primary_path: List[str],
        placement: GraphPlacementResult,
        write_result: NoteWriteResult,
    ) -> Dict:
        primary_node = self.graph.get_node_by_path(storage_primary_path)
        prior_paths = list((mapping_record or {}).get("prior_knowledge_note_paths", []))
        if write_result.prior_path and str(write_result.prior_path) not in prior_paths:
            prior_paths.append(str(write_result.prior_path))
        # When the topic node cannot be resolved (deferred placement), mark as skipped
        # rather than processed — processed requires a valid primary_topic_node_id
        if primary_node is None:
            processing_status = "skipped"
            note_id = None
            note_path = None
        else:
            processing_status = "processed"
            note_id = write_result.note_id
            note_path = str(write_result.final_path)

        return {
            "source_inbox_path": source_mapping_seed["source_inbox_path"],
            "source_content_fingerprint": source_mapping_seed["source_content_fingerprint"],
            "source_processing_status": processing_status,
            "knowledge_note_id": note_id,
            "knowledge_note_path": note_path,
            "prior_knowledge_note_paths": prior_paths,
            "primary_topic_node_id": primary_node.id if primary_node else None,
            "secondary_topic_node_ids": placement.secondary_node_ids,
            "ancestor_topic_node_ids": sorted(set(self.graph.get_ancestor_ids(storage_primary_path)) | set(placement.secondary_ancestor_node_ids)),
            "graph_version": self.graph.version,
            "last_generated_at": self._utc_now_iso(),
            "persisted_first_seen_inbox_path": source_mapping_seed["persisted_first_seen_inbox_path"],
        }

    async def write_note(
        self,
        knowledge_note_id: str,
        mapping_record: Optional[Dict],
        source_mapping_seed: Dict,
        placement: GraphPlacementResult,
        note_metadata: KnowledgeNoteFileMetadata,
        rendered_markdown: str,
    ) -> Tuple[NoteWriteResult, Dict]:
        storage_primary_path = self.choose_storage_primary_path(mapping_record, placement)
        final_path = self._build_note_path(
            storage_primary_path,
            note_metadata.published_date,
            note_metadata.title,
            knowledge_note_id,
        )
        prior_path = Path(mapping_record["knowledge_note_path"]) if mapping_record and mapping_record.get("knowledge_note_path") else None
        
        if prior_path and prior_path != final_path:
            await self._move_with_history(prior_path, final_path)
            
        await self._write_markdown(final_path, rendered_markdown)
        
        result = NoteWriteResult(
            note_id=knowledge_note_id,
            final_path=final_path,
            prior_path=prior_path,
            placement_path=storage_primary_path,
            canonical_primary_path=storage_primary_path,
            deferred_primary_path=placement.deferred_primary_path,
        )
        processed_mapping_record = self.build_processed_mapping_record(
            mapping_record=mapping_record,
            source_mapping_seed=source_mapping_seed,
            storage_primary_path=storage_primary_path,
            placement=placement,
            write_result=result,
        )
        return result, processed_mapping_record
