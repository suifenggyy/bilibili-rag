"""
ASR 临时文件管理。
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable

from loguru import logger

from app.services.content_storage import ContentStorageManager


class ASRTempFileManager:
    """管理 ASR 临时音频和结果文件，并按阈值清理旧文件。"""

    def __init__(
        self,
        base_dir: str | None = None,
        max_total_size_bytes: int = 1024 * 1024 * 1024,
        retention_days: int = 3,
        source: str = "bilibili",
        storage_manager: ContentStorageManager | None = None,
    ):
        self.base_dir = base_dir
        self.max_total_size_bytes = max_total_size_bytes
        self.retention_days = retention_days
        self.source = source
        self.storage_manager = storage_manager

        if self.base_dir is not None:
            os.makedirs(self.base_dir, exist_ok=True)
        else:
            self.storage_manager = storage_manager or ContentStorageManager(
                max_total_size_bytes=max_total_size_bytes,
                retention_days=retention_days,
            )

    def build_path(self, title: str, suffix: str, prefix: str = "tmp") -> str:
        if self.base_dir is None:
            filename = self._resolve_filename(title=title, prefix=prefix, suffix=suffix)
            return str(self.storage_manager.build_work_file_path(self.source, title, filename))

        safe_title = self._sanitize_title(title)
        normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{prefix}_{safe_title}_{timestamp}{normalized_suffix}"
        return os.path.join(self.base_dir, filename)

    def write_result(self, title: str, text: str, prefix: str = "asr_result") -> str:
        if self.base_dir is None:
            filename = self._resolve_result_filename(prefix)
            path = self.storage_manager.write_work_text(self.source, title, filename, text)
            return str(path)

        path = self.build_path(title, ".txt", prefix=prefix)
        Path(path).write_text(text, encoding="utf-8")
        self.cleanup_if_needed()
        return path

    def cleanup_if_needed(self) -> None:
        if self.base_dir is None:
            self.storage_manager.cleanup_workspace_if_needed()
            return

        files = list(self._iter_files())
        total_size = sum(path.stat().st_size for path in files if path.exists())
        if total_size <= self.max_total_size_bytes:
            return

        cutoff = time.time() - self.retention_days * 24 * 60 * 60
        removed_files = 0
        removed_bytes = 0
        for path in sorted(files, key=lambda item: item.stat().st_mtime):
            if not path.exists():
                continue
            if path.stat().st_mtime >= cutoff:
                continue

            file_size = path.stat().st_size
            try:
                path.unlink()
                removed_files += 1
                removed_bytes += file_size
            except Exception as e:
                logger.warning(f"ASR 临时文件清理失败: {path} - {e}")

        if removed_files:
            logger.info(
                "ASR 临时目录超限，已清理旧文件: removed_files={}, removed_bytes={}KB",
                removed_files,
                removed_bytes // 1024,
            )

    def _iter_files(self) -> Iterable[Path]:
        return (path for path in Path(self.base_dir).iterdir() if path.is_file())

    def _sanitize_title(self, title: str, max_len: int = 80) -> str:
        cleaned = (title or "").strip()
        if not cleaned:
            return "untitled"

        cleaned = re.sub(r'[\\/:*?"<>|]', "_", cleaned)
        cleaned = re.sub(r"\s+", "_", cleaned)
        cleaned = re.sub(r"_+", "_", cleaned).strip("._")
        return cleaned[:max_len] if len(cleaned) > max_len else cleaned

    def _resolve_filename(self, title: str, prefix: str, suffix: str) -> str:
        normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        if prefix == "audio":
            return f"audio_{self._sanitize_title(title)}{normalized_suffix}"
        return f"{prefix}{normalized_suffix}"

    def _resolve_result_filename(self, prefix: str) -> str:
        if prefix == "asr_result":
            return "asr_raw.txt"
        return f"{prefix}.txt"
