import json
import asyncio
from pathlib import Path
from loguru import logger
import aiofiles

class MetadataState:
    def __init__(self, meta_dir: Path):
        self.meta_dir = meta_dir
        self._lock = asyncio.Lock()
        self._lock_holder = None

    async def bootstrap(self):
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        await self._ensure_managed_file("topic-graph.json", {"version": "topic-graph-v1", "nodes": []})

    def write_lock(self):
        class LockContext:
            def __init__(self, state):
                self.state = state
            async def __aenter__(self):
                await self.state._lock.acquire()
                self.state._lock_holder = asyncio.current_task()
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                self.state._lock_holder = None
                self.state._lock.release()
        return LockContext(self)

    def _assert_write_lock_held(self):
        if self._lock_holder != asyncio.current_task():
            raise RuntimeError("Must hold write lock")

    async def _ensure_managed_file(self, filename: str, default_data: dict):
        path = self.meta_dir / filename
        if not path.exists():
            async with self.write_lock():
                await self._save_json(filename, default_data)

    async def _load_json(self, filename: str, default: dict) -> dict:
        path = self.meta_dir / filename
        if not path.exists():
            return default
        try:
            async with aiofiles.open(path, 'r', encoding='utf-8') as f:
                content = await f.read()
                return json.loads(content)
        except json.JSONDecodeError:
            raise ValueError(f"invalid JSON in {filename}")

    async def _save_json(self, filename: str, data: dict):
        self._assert_write_lock_held()
        path = self.meta_dir / filename
        tmp_path = path.with_suffix('.json.tmp')
        async with aiofiles.open(tmp_path, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        tmp_path.replace(path)

    async def load_topic_graph(self) -> dict:
        return await self._load_json("topic-graph.json", {"version": "topic-graph-v1", "nodes": []})

    async def save_topic_graph(self, snapshot: dict) -> None:
        self._assert_write_lock_held()
        await self._save_json("topic-graph.json", snapshot)

    async def load_pending_mutations(self) -> dict:
        return await self._load_json("pending-topic-mutations.json", {"items": []})

    async def save_pending_mutations(self, data: dict) -> None:
        await self._save_json("pending-topic-mutations.json", data)

    async def merge_pending_mutations(self, records: list[dict]) -> None:
        self._assert_write_lock_held()
        snapshot = await self.load_pending_mutations()
        by_identity = {item["proposal_identity"]: item for item in snapshot["items"]}
        for record in records:
            current = by_identity.get(record["proposal_identity"])
            if current:
                current["supporting_source_note_paths"] = sorted(set(current["supporting_source_note_paths"]) | set(record["supporting_source_note_paths"]))
                current["supporting_source_count"] = len(current["supporting_source_note_paths"])
                current["confidence_score"] = max(current["confidence_score"], record["confidence_score"])
            else:
                by_identity[record["proposal_identity"]] = record
        await self.save_pending_mutations({"items": list(by_identity.values())})

    async def reconcile_pending_mutations(self, records: list[dict]) -> None:
        self._assert_write_lock_held()
        snapshot = await self.load_pending_mutations()
        by_identity = {item["proposal_identity"]: item for item in snapshot["items"]}
        incoming_identities = {record["proposal_identity"] for record in records}
        for item in by_identity.values():
            if item["proposal_identity"] not in incoming_identities and item["lifecycle_status"] == "pending":
                item["lifecycle_status"] = "superseded"
        for record in records:
            current = by_identity.get(record["proposal_identity"])
            if current and current["lifecycle_status"] in {"rejected", "superseded"}:
                current["lifecycle_status"] = "pending"
                current["resolved_at"] = None
        await self.save_pending_mutations({"items": list(by_identity.values())})

    async def load_source_mapping(self) -> dict:
        return await self._load_json("source-topic-map.json", {"items": []})
        
    async def save_source_mapping(self, data: dict) -> None:
        await self._save_json("source-topic-map.json", data)

    async def find_source_mapping_by_path(self, source_inbox_path: str) -> dict | None:
        snapshot = await self.load_source_mapping()
        for item in snapshot["items"]:
            if item["source_inbox_path"] == source_inbox_path:
                return item
        return None

    async def find_source_mapping_by_fingerprint(self, source_fingerprint: str) -> dict | None:
        snapshot = await self.load_source_mapping()
        for item in snapshot["items"]:
            if item.get("source_content_fingerprint") == source_fingerprint:
                return item
        return None

    async def get_or_create_source_identity(self, source_inbox_path: str, source_fingerprint: str, doc) -> dict[str, str]:
        existing = await self.find_source_mapping_by_path(source_inbox_path)
        if existing is None:
            existing = await self.find_source_mapping_by_fingerprint(source_fingerprint)
        persisted_first_seen = existing["persisted_first_seen_inbox_path"] if existing else source_inbox_path
        return {
            "source_inbox_path": source_inbox_path,
            "persisted_first_seen_inbox_path": persisted_first_seen,
            "source_url": doc.source_url or "",
            "published_date": doc.date_str or "",
            "title": doc.title,
        }

    async def upsert_source_mapping(self, record: dict) -> None:
        self._assert_write_lock_held()
        snapshot = await self.load_source_mapping()
        items = {item["persisted_first_seen_inbox_path"]: item for item in snapshot["items"]}
        if record.get("knowledge_note_id"):
            items = {
                key: item
                for key, item in items.items()
                if item.get("knowledge_note_id") != record["knowledge_note_id"] or key == record["persisted_first_seen_inbox_path"]
            }
        items[record["persisted_first_seen_inbox_path"]] = record
        await self.save_source_mapping({"items": list(items.values())})
