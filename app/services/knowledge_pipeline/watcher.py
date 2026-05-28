"""
Inbox 目录 watchdog 监听器。

监听 inbox 目录中的新 .md 文件，触发流水线处理。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from loguru import logger


class InboxWatcher:
    """
    基于 watchdog 的 inbox 目录监听器。

    检测到新 .md 文件后立即触发 KnowledgePipelineOrchestrator.process_single_file()。
    """

    def __init__(
        self,
        inbox_dir: Path,
        orchestrator=None,
        vault_root: Optional[Path] = None,
    ):
        self._inbox_dir = Path(inbox_dir)
        self._orchestrator = orchestrator
        self._vault_root = vault_root
        self._observer = None

    def _get_orchestrator(self):
        if self._orchestrator is not None:
            return self._orchestrator
        from app.services.knowledge_pipeline.orchestrator import (
            KnowledgePipelineOrchestrator,
        )
        return KnowledgePipelineOrchestrator(vault_root=self._vault_root)

    def start(self) -> None:
        """启动 watchdog observer（阻塞前调用）。"""
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler
        except ImportError:
            raise ImportError(
                "watchdog 未安装。请执行: uv pip install watchdog"
            )

        orchestrator = self._get_orchestrator()
        loop = asyncio.new_event_loop()

        class _Handler(FileSystemEventHandler):
            def on_created(self, event):
                if event.is_directory:
                    return
                p = Path(event.src_path)
                if p.suffix.lower() != ".md":
                    return
                logger.info(f"[InboxWatcher] 检测到新文件: {p.name}")
                loop.run_until_complete(orchestrator.process_single_file(p))

        self._observer = Observer()
        self._observer.schedule(
            _Handler(), str(self._inbox_dir), recursive=False
        )
        self._observer.start()
        logger.info(f"[InboxWatcher] 开始监听 inbox: {self._inbox_dir}")

    def stop(self) -> None:
        if self._observer and self._observer.is_alive():
            self._observer.stop()
            self._observer.join()
            logger.info("[InboxWatcher] 已停止监听")
