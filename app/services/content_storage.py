"""
统一管理抓取工作目录和最终导出目录。
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

from app.config import settings


class ContentStorageManager:
    """负责来源/日期/标题分层的工作目录与导出目录，以及 Obsidian Vault 路径。"""

    def __init__(
        self,
        workspace_root: Optional[str] = None,
        export_root: Optional[str] = None,
        vault_root: Optional[str] = None,
        inbox_dir: Optional[str] = None,
        max_total_size_bytes: Optional[int] = None,
        retention_days: Optional[int] = None,
    ):
        self.workspace_root = Path(workspace_root or settings.content_workspace_root).expanduser()
        self._vault_root = Path(vault_root or settings.obsidian_vault_root).expanduser()
        self._inbox_dir = inbox_dir or settings.obsidian_inbox_dir
        self._custom_export_root = Path(export_root).expanduser() if export_root else None
        self.max_total_size_bytes = max_total_size_bytes or settings.content_workspace_max_size_bytes
        self.retention_days = retention_days or settings.content_workspace_retention_days

    # ==================== 工作区缓存读取 ====================

    def find_work_file_path(
        self,
        source: str,
        title: str,
        filename: str,
    ) -> Optional[Path]:
        """跨日期目录搜索工作区文件（优先最新日期）。

        与 build_work_file_path 不同，此方法不会创建目录，是纯只读查找。
        适用于重新导出时复用之前已下载/已识别的缓存文件。
        """
        source_dir = self.workspace_root / self._sanitize_segment(source)
        if not source_dir.exists():
            return None
        safe_title = self._sanitize_segment(title)
        date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        date_dirs: list[Path] = []
        for entry in source_dir.iterdir():
            if entry.is_dir() and date_pattern.match(entry.name):
                date_dirs.append(entry)
        # 最新的日期优先
        for date_dir in sorted(date_dirs, key=lambda d: d.name, reverse=True):
            candidate = date_dir / safe_title / filename
            if candidate.exists():
                return candidate
        return None

    def read_work_text(
        self,
        source: str,
        title: str,
        filename: str,
    ) -> Optional[str]:
        """读取工作区文本文件。不存在或为空返回 None。"""
        path = self.find_work_file_path(source=source, title=title, filename=filename)
        if not path:
            return None
        try:
            text = path.read_text(encoding="utf-8").strip()
            return text if text else None
        except Exception:
            return None

    def work_file_exists(
        self,
        source: str,
        title: str,
        filename: str,
        min_size: int = 0,
    ) -> bool:
        """检查工作区文件是否存在，可选检查最小文件大小。"""
        path = self.find_work_file_path(source=source, title=title, filename=filename)
        if not path:
            return False
        if min_size > 0:
            try:
                return path.stat().st_size >= min_size
            except OSError:
                return False
        return True

    # ==================== Vault 路径 helpers ====================

    def get_vault_root(self) -> Path:
        return self._vault_root

    def get_inbox_dir(self) -> Path:
        return self._vault_root / self._inbox_dir

    def get_failed_inbox_dir(self) -> Path:
        return self.get_inbox_dir() / "failed"

    def get_knowledge_dir(self) -> Path:
        return self._vault_root / settings.obsidian_knowledge_dir

    def get_topics_dir(self) -> Path:
        return self._vault_root / settings.obsidian_topics_dir

    def get_daily_dir(self) -> Path:
        return self._vault_root / settings.obsidian_daily_dir

    def get_meta_dir(self) -> Path:
        return self._vault_root / settings.obsidian_meta_dir

    def _get_export_root(self) -> Path:
        """Return the effective export root: custom if set, otherwise inbox."""
        return self._custom_export_root if self._custom_export_root else self.get_inbox_dir()

    @property
    def export_root(self) -> Path:
        """Backward-compat alias for _get_export_root()."""
        return self._get_export_root()

    def get_work_dir(self, source: str, title: str, day: Optional[date] = None) -> Path:
        work_dir = (
            self.workspace_root
            / self._sanitize_segment(source)
            / self._format_day(day)
            / self._sanitize_segment(title)
        )
        work_dir.mkdir(parents=True, exist_ok=True)
        return work_dir

    def build_work_file_path(
        self,
        source: str,
        title: str,
        filename: str,
        day: Optional[date] = None,
    ) -> Path:
        return self.get_work_dir(source=source, title=title, day=day) / filename

    def write_work_text(
        self,
        source: str,
        title: str,
        filename: str,
        text: str,
        day: Optional[date] = None,
    ) -> Path:
        path = self.build_work_file_path(source=source, title=title, filename=filename, day=day)
        path.write_text(text, encoding="utf-8")
        self.cleanup_workspace_if_needed()
        return path

    def get_export_dir(self, source: str, day: Optional[date] = None) -> Path:
        export_dir = self._get_export_root() / self._sanitize_segment(source) / self._format_day(day)
        export_dir.mkdir(parents=True, exist_ok=True)
        return export_dir

    def resolve_after_date(self, source: str, config_after_date: str = "") -> str | None:
        """Determine the effective after_date for fetching content.

        If the Obsidian inbox directory already contains date-named
        sub-folders (YYYY-MM-DD), use (latest_folder_date - 1 day) as the
        filter so that consecutive runs overlap by one day and nothing is
        missed due to timezone / collection-time differences.

        If no date folders exist, fall back to ``config_after_date``.
        """
        import re
        inbox_dir = self.get_inbox_dir()
        if not inbox_dir.exists():
            return config_after_date.strip() if config_after_date and config_after_date.strip() else None

        date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        dates = []
        for entry in inbox_dir.iterdir():
            if entry.is_dir() and date_pattern.match(entry.name):
                try:
                    dates.append(datetime.strptime(entry.name, "%Y-%m-%d").date())
                except ValueError:
                    continue

        if dates:
            latest = max(dates)
            effective = latest - timedelta(days=1)
            return effective.strftime("%Y-%m-%d")

        return config_after_date.strip() if config_after_date and config_after_date.strip() else None

    def build_markdown_path(
        self,
        source: str,
        title: str,
        identifier: str,
        day: Optional[date] = None,
    ) -> Path:
        export_dir = self.get_export_dir(source=source, day=day)
        safe_title = self._sanitize_segment(title)
        safe_identifier = self._sanitize_filename_part(identifier)
        return export_dir / f"{safe_title}_{safe_identifier}.md"

    def cleanup_workspace_if_needed(self, now: Optional[float] = None) -> None:
        if not self.workspace_root.exists():
            return

        files = [path for path in self.workspace_root.rglob("*") if path.is_file()]
        total_size = sum(path.stat().st_size for path in files if path.exists())
        if total_size <= self.max_total_size_bytes:
            return

        cutoff = (now or time.time()) - self.retention_days * 24 * 60 * 60
        removed_files = 0
        removed_bytes = 0

        for path in sorted(files, key=lambda item: item.stat().st_mtime):
            if not path.exists() or path.stat().st_mtime >= cutoff:
                continue

            file_size = path.stat().st_size
            try:
                path.unlink()
                removed_files += 1
                removed_bytes += file_size
            except Exception as exc:
                logger.warning(f"工作目录清理失败: {path} - {exc}")

        self._remove_empty_dirs()

        if removed_files:
            logger.info(
                "工作目录超限，已清理旧文件: removed_files={}, removed_bytes={}KB",
                removed_files,
                removed_bytes // 1024,
            )

    def _remove_empty_dirs(self) -> None:
        for path in sorted(
            (candidate for candidate in self.workspace_root.rglob("*") if candidate.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            try:
                path.rmdir()
            except OSError:
                continue

    def _format_day(self, day: Optional[date]) -> str:
        return (day or datetime.now().date()).strftime("%Y-%m-%d")

    def _sanitize_segment(self, value: str, max_len: int = 80) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            return "untitled"

        # Replace characters that are invalid in filenames or break Obsidian wikilinks
        # Half-width: \ / : * ? " < > | #
        # Full-width: ？ (U+FF1F) — common in Chinese text from video titles
        cleaned = re.sub(r'[\\/:*?"<>|#]', "_", cleaned)
        cleaned = cleaned.replace("？", "_")
        cleaned = re.sub(r"_+", "_", cleaned).strip("._")
        return cleaned[:max_len] if len(cleaned) > max_len else cleaned

    def _sanitize_filename_part(self, value: str, max_len: int = 80) -> str:
        cleaned = self._sanitize_segment(value, max_len=max_len)
        return re.sub(r"\s+", "_", cleaned)
