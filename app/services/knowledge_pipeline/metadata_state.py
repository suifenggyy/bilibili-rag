import json
import asyncio
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from loguru import logger
import aiofiles


# --- Schema defaults ---

SOURCE_MAPPING_EMPTY = {"items": []}
TOPIC_DETAIL_EMPTY = {"items": []}
PENDING_MUTATION_EMPTY = {"items": []}
RUN_LOG_EMPTY = {"items": []}

SOURCE_STATUS_VALUES = {"processed", "skipped", "failed", "tombstoned"}
PENDING_MUTATION_STATUS_VALUES = {"pending", "superseded", "rejected"}
DETAIL_TYPE_VALUES = {"example", "case", "exception", "quote", "tactic"}
RUN_SCOPE_VALUES = {"single_note", "repair", "migration"}
RUN_STATUS_VALUES = {"started", "success", "failed"}
MUTATION_TYPE_VALUES = {"create_leaf", "add_alias", "rename", "merge", "split", "move", "replace"}


# --- Validation helpers ---

def validate_source_mapping_item(item: dict) -> None:
    if not isinstance(item["source_inbox_path"], str) or not item["source_inbox_path"]:
        raise ValueError("invalid source_inbox_path")
    if item["source_processing_status"] not in SOURCE_STATUS_VALUES:
        raise ValueError("invalid source_processing_status")
    if not isinstance(item["secondary_topic_node_ids"], list) or not isinstance(item["ancestor_topic_node_ids"], list):
        raise ValueError("topic node id collections must be lists")
    if item["source_processing_status"] == "processed" and not item.get("knowledge_note_id"):
        raise ValueError("processed items require knowledge_note_id")
    if item["source_processing_status"] == "processed" and not item.get("knowledge_note_path"):
        raise ValueError("processed items require knowledge_note_path")
    if item["source_processing_status"] == "processed" and not item.get("primary_topic_node_id"):
        raise ValueError("processed items require primary_topic_node_id")
    if item["source_processing_status"] == "tombstoned" and not item.get("knowledge_note_id"):
        raise ValueError("tombstoned items require knowledge_note_id")
    if item["source_processing_status"] == "tombstoned" and not item.get("knowledge_note_path"):
        raise ValueError("tombstoned items require knowledge_note_path")
    if item["source_processing_status"] == "tombstoned" and not item.get("primary_topic_node_id"):
        raise ValueError("tombstoned items require primary_topic_node_id")


def validate_pending_mutation_item(item: dict) -> None:
    if item["lifecycle_status"] not in PENDING_MUTATION_STATUS_VALUES:
        raise ValueError("invalid lifecycle_status")
    if item["proposed_mutation_type"] not in MUTATION_TYPE_VALUES:
        raise ValueError("invalid mutation type")
    if not isinstance(item["affected_node_ids"], list) or not isinstance(item["affected_unresolved_names"], list):
        raise ValueError("affected node collections must be lists")
    if item["proposed_mutation_type"] == "create_leaf" and not item["affected_unresolved_names"]:
        raise ValueError("create_leaf requires affected_unresolved_names")
    if item["proposed_mutation_type"] == "add_alias" and not item["affected_node_ids"]:
        raise ValueError("add_alias requires affected_node_ids")
    if not isinstance(item["target_paths"], list) or not isinstance(item["supporting_source_note_paths"], list):
        raise ValueError("target_paths and supporting_source_note_paths must be lists")
    if not isinstance(item["confidence_score"], (int, float)) or not 0.0 <= item["confidence_score"] <= 1.0:
        raise ValueError("invalid confidence_score")
    if item["supporting_source_count"] != len(set(item["supporting_source_note_paths"])):
        raise ValueError("supporting_source_count must match distinct inbox note paths")
    if item["lifecycle_status"] in {"superseded", "rejected"} and not item.get("resolved_at"):
        raise ValueError("terminal pending mutation requires resolved_at")


def validate_topic_detail_item(item: dict) -> None:
    if item["detail_type"] not in DETAIL_TYPE_VALUES:
        raise ValueError("invalid detail_type")
    if not isinstance(item["supporting_source_inbox_paths"], list):
        raise ValueError("supporting_source_inbox_paths must be a list")


def validate_run_log_item(item: dict) -> None:
    if "source_note_paths" not in item and "batch_selector" not in item:
        raise ValueError("run log requires source_note_paths or batch_selector")
    if item["run_scope"] not in RUN_SCOPE_VALUES:
        raise ValueError("invalid run_scope")
    if item["status"] not in RUN_STATUS_VALUES:
        raise ValueError("invalid run status")
    if "batch_selector" in item and not isinstance(item["batch_selector"], dict):
        raise ValueError("batch_selector must be an object")
    if not isinstance(item["files_intended"], list) or not isinstance(item["files_written"], list):
        raise ValueError("files_intended/files_written must be lists")
    if item["status"] in {"success", "failed"} and not item.get("completed_at"):
        raise ValueError("terminal run-log record requires completed_at")


def validate_snapshot(name: str, data: dict) -> None:
    if not isinstance(data, dict) or "items" not in data or not isinstance(data["items"], list):
        raise ValueError(f"{name} must be a dict with items list")
    if name == "source-topic-map.json":
        required = {
            "source_inbox_path",
            "source_content_fingerprint",
            "source_processing_status",
            "knowledge_note_id",
            "knowledge_note_path",
            "primary_topic_node_id",
            "secondary_topic_node_ids",
            "ancestor_topic_node_ids",
            "graph_version",
            "last_generated_at",
            "persisted_first_seen_inbox_path",
        }
        for item in data["items"]:
            if required - item.keys():
                raise ValueError("source-topic-map.json item missing required keys")
            validate_source_mapping_item(item)
    elif name == "pending-topic-mutations.json":
        required = {
            "proposal_identity",
            "proposed_mutation_type",
            "lifecycle_status",
            "affected_node_ids",
            "affected_unresolved_names",
            "target_parent_path",
            "target_name",
            "target_replacement_node_id",
            "target_paths",
            "confidence_score",
            "reason",
            "supporting_source_note_paths",
            "supporting_source_count",
            "created_at",
            "resolved_at",
        }
        for item in data["items"]:
            if required - item.keys():
                raise ValueError("pending-topic-mutations.json item missing required keys")
            validate_pending_mutation_item(item)
    elif name == "topic-detail-index.json":
        required = {
            "topic_node_id",
            "detail_fingerprint",
            "detail_type",
            "normalized_semantic_statement",
            "supporting_source_inbox_paths",
            "last_updated_at",
        }
        for item in data["items"]:
            if required - item.keys():
                raise ValueError("topic-detail-index.json item missing required keys")
            validate_topic_detail_item(item)
    elif name == "pipeline-run-log.json":
        required = {
            "run_id",
            "run_scope",
            "status",
            "files_intended",
            "files_written",
            "graph_changed",
            "mapping_changed",
            "started_at",
            "completed_at",
        }
        for item in data["items"]:
            if required - item.keys():
                raise ValueError("pipeline-run-log.json item missing required keys")
            validate_run_log_item(item)


# --- Main class ---

class MetadataWriteLockTimeout(TimeoutError):
    def __init__(self, timeout_seconds: float):
        super().__init__(f"metadata write lock timed out after {timeout_seconds}s")
        self.timeout_seconds = timeout_seconds


class MetadataState:
    LOCK_FILE = ".write.lock"
    LOCK_RETRY_INTERVAL_SECONDS = 0.05

    def __init__(self, meta_dir: Path):
        self.meta_dir = meta_dir
        self._write_lock_fd: int | None = None
        self._async_lock = asyncio.Lock()

    # ---- Bootstrap ----

    async def bootstrap(self) -> None:
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        managed_files = {
            "source-topic-map.json",
            "topic-detail-index.json",
            "pending-topic-mutations.json",
            "topic-graph.json",
            "pipeline-run-log.json",
        }
        existing_entries = {path.name for path in self.meta_dir.iterdir() if path.is_file() and path.name != self.LOCK_FILE}
        existing_managed = managed_files & existing_entries
        missing_managed = managed_files - existing_entries
        # If some managed files exist and some don't, it could be a partially-initialized state.
        # We allow bootstrap to complete by creating the missing ones.
        for filename in existing_managed:
            if filename == "topic-graph.json":
                # topic-graph.json has a different schema (no "items" key)
                continue
            try:
                await self._read_json(filename)
            except ValueError:
                logger.warning(f"[MetadataState] bootstrap found corrupted file: {filename}")
        async with self.write_lock():
            await self._ensure_file("source-topic-map.json", deepcopy(SOURCE_MAPPING_EMPTY))
            await self._ensure_file("topic-detail-index.json", deepcopy(TOPIC_DETAIL_EMPTY))
            await self._ensure_file("pending-topic-mutations.json", deepcopy(PENDING_MUTATION_EMPTY))
            await self._ensure_file("topic-graph.json", {"version": "topic-graph-v1", "nodes": []})
            await self._ensure_file("pipeline-run-log.json", deepcopy(RUN_LOG_EMPTY))

    # ---- Write lock (in-process asyncio + file-based for cross-process) ----

    def write_lock(self, timeout_seconds: float | None = 5.0):
        state = self

        class _LockContext:
            async def __aenter__(self_inner):
                # In-process serialization via asyncio.Lock
                if timeout_seconds is not None:
                    try:
                        await asyncio.wait_for(state._async_lock.acquire(), timeout=timeout_seconds)
                    except asyncio.TimeoutError:
                        raise MetadataWriteLockTimeout(timeout_seconds=timeout_seconds)
                else:
                    await state._async_lock.acquire()

                # Cross-process serialization via file lock
                state.meta_dir.mkdir(parents=True, exist_ok=True)
                deadline = None if timeout_seconds is None else __import__("time").monotonic() + timeout_seconds
                while True:
                    try:
                        fd = os.open(state.meta_dir / state.LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        state._write_lock_fd = fd
                        break
                    except FileExistsError:
                        if deadline is not None and __import__("time").monotonic() >= deadline:
                            state._async_lock.release()
                            raise MetadataWriteLockTimeout(timeout_seconds=timeout_seconds)
                        await asyncio.sleep(state.LOCK_RETRY_INTERVAL_SECONDS)
                return self_inner

            async def __aexit__(self_inner, exc_type, exc_val, exc_tb):
                if state._write_lock_fd is not None:
                    os.close(state._write_lock_fd)
                    state._write_lock_fd = None
                lock_path = state.meta_dir / state.LOCK_FILE
                try:
                    lock_path.unlink(missing_ok=True)
                except FileNotFoundError:
                    pass
                state._async_lock.release()

        return _LockContext()

    def _assert_write_lock_held(self) -> None:
        if self._write_lock_fd is None:
            raise RuntimeError("metadata mutations require write_lock()")

    # ---- Transactional write with lock-contention logging ----

    def transactional_write(self, run_scope: str, context: dict, timeout_seconds: float = 5.0):
        state = self

        class _TransactionalContext:
            async def __aenter__(self_inner):
                self_inner._lock_ctx = state.write_lock(timeout_seconds=timeout_seconds)
                try:
                    await self_inner._lock_ctx.__aenter__()
                except MetadataWriteLockTimeout as exc:
                    await state._record_lock_contention_after_release(
                        run_scope=run_scope,
                        context=context,
                        timeout_seconds=timeout_seconds,
                        lock_error=str(exc),
                    )
                    raise
                return self_inner

            async def __aexit__(self_inner, exc_type, exc_val, exc_tb):
                await self_inner._lock_ctx.__aexit__(exc_type, exc_val, exc_tb)

        return _TransactionalContext()

    async def _record_lock_contention_after_release(
        self,
        run_scope: str,
        context: dict,
        timeout_seconds: float,
        lock_error: str,
    ) -> str:
        failure_record = {
            "run_id": f"lock-failure-{uuid4().hex[:12]}",
            "run_scope": run_scope,
            "status": "failed",
            "files_intended": context.get("files_intended", []),
            "files_written": [],
            "graph_changed": False,
            "mapping_changed": False,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "failure_reason": "lock_contention",
            "lock_wait_timeout_seconds": timeout_seconds,
            "lock_error": lock_error,
        }
        if "source_note_paths" in context:
            failure_record["source_note_paths"] = context["source_note_paths"]
        if "batch_selector" in context:
            failure_record["batch_selector"] = context["batch_selector"]
        async with self.write_lock(timeout_seconds=None):
            run_log = await self.load_run_log()
            run_log["items"].append(failure_record)
            validate_snapshot("pipeline-run-log.json", run_log)
            await self._atomic_write_json("pipeline-run-log.json", run_log)
        return failure_record["run_id"]

    # ---- Low-level I/O ----

    async def _read_json(self, filename: str) -> dict:
        path = self.meta_dir / filename
        if not path.exists():
            raise ValueError(f"missing metadata file: {filename}; run bootstrap() or repair first")
        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                content = await f.read()
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"corrupted metadata file: {filename}") from exc
        return data

    async def _atomic_write_json(self, filename: str, data: dict) -> None:
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self.meta_dir / f"{filename}.tmp"
        async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))
        tmp_path.replace(self.meta_dir / filename)

    async def _ensure_file(self, filename: str, default: dict) -> None:
        path = self.meta_dir / filename
        if not path.exists():
            await self._atomic_write_json(filename, deepcopy(default))

    # ---- Source mapping ----

    async def load_source_mapping(self) -> dict:
        data = await self._read_json("source-topic-map.json")
        return data

    async def save_source_mapping(self, data: dict) -> None:
        self._assert_write_lock_held()
        # Migrate legacy records that have "processed" status but missing required fields
        for item in data.get("items", []):
            if item.get("source_processing_status") == "processed" and not item.get("primary_topic_node_id"):
                item["source_processing_status"] = "skipped"
                item["knowledge_note_id"] = None
                item["knowledge_note_path"] = None
        validate_snapshot("source-topic-map.json", data)
        await self._atomic_write_json("source-topic-map.json", data)

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

    # ---- Topic detail index ----

    async def load_topic_detail_index(self) -> dict:
        data = await self._read_json("topic-detail-index.json")
        validate_snapshot("topic-detail-index.json", data)
        return data

    async def save_topic_detail_index(self, data: dict) -> None:
        self._assert_write_lock_held()
        validate_snapshot("topic-detail-index.json", data)
        await self._atomic_write_json("topic-detail-index.json", data)

    # ---- Pending mutations ----

    async def load_pending_mutations(self) -> dict:
        data = await self._read_json("pending-topic-mutations.json")
        return data

    async def save_pending_mutations(self, data: dict) -> None:
        self._assert_write_lock_held()
        validate_snapshot("pending-topic-mutations.json", data)
        await self._atomic_write_json("pending-topic-mutations.json", data)

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

    # ---- Topic graph ----

    async def load_topic_graph(self) -> dict:
        return await self._read_json("topic-graph.json")

    async def save_topic_graph(self, snapshot: dict) -> None:
        self._assert_write_lock_held()
        await self._atomic_write_json("topic-graph.json", snapshot)

    # ---- Run log ----

    async def load_run_log(self) -> dict:
        data = await self._read_json("pipeline-run-log.json")
        return data

    async def append_run_log_start(self, record: dict) -> str:
        self._assert_write_lock_held()
        if not ("source_note_paths" in record or "batch_selector" in record):
            raise ValueError("run log requires source_note_paths or batch_selector")
        run_log = await self.load_run_log()
        run_log["items"].append(record)
        validate_snapshot("pipeline-run-log.json", run_log)
        await self._atomic_write_json("pipeline-run-log.json", run_log)
        return record["run_id"]

    async def finalize_run_log(self, run_id: str, status: str, updates: dict) -> None:
        self._assert_write_lock_held()
        run_log = await self.load_run_log()
        for item in run_log["items"]:
            if item["run_id"] == run_id:
                item["status"] = status
                item.update(updates)
                break
        else:
            raise ValueError(f"unknown run_id: {run_id}")
        validate_snapshot("pipeline-run-log.json", run_log)
        await self._atomic_write_json("pipeline-run-log.json", run_log)
