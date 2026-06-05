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
