"""
Source 目录进度日志（per-source progress log）。

在每个 source 目录（如 ``inbox/bilibili/``）下维护一份 ``_pipeline_progress.log``，
按日期记录知识库流水线的处理进度：

  - 裸日期行            → 该日期下所有文件已处理完成
  - 日期 + 缩进行文件名  → 该日期部分完成（列出的是已处理文件名）

每处理完一个文件就**原子地重写**一次日志（写临时文件 + ``os.replace``），
这样即使进程被异常 kill，磁盘上也只会留下「上一次完整状态」或「本次完整状态」，
不会出现截断/损坏的中间态。

本日志仅为可见性 + 冗余的进度记录，不参与跳过逻辑——是否已处理仍以
``_meta/source-topic-map.json`` 为准（见 orchestrator._load_processed_state）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from loguru import logger

_DATE_SEG_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LOG_FILENAME = "_pipeline_progress.log"
_HEADER = (
    "# Pipeline progress log (auto-generated, do not edit)\n"
    "# 裸日期行 = 该日期下所有文件已处理完成\n"
    '# 日期 + 缩进 "- 文件名" = 该日期部分完成（列出的为已处理文件）\n'
)


class SourceProgressLogger:
    """在每个 source 目录下维护逐文件处理进度的日志。"""

    def __init__(self, inbox_root: Path):
        self._inbox_root = Path(inbox_root)

    # ==================== Public API ====================

    async def record_processed(self, path: Path) -> None:
        """将 ``path`` 标记为已处理，更新其所属 source/date 的进度日志。

        无法解析出 source/date（路径不在 inbox 内、或路径中无 YYYY-MM-DD 段）
        时静默跳过——此类文件仍由 source-topic-map 正常追踪。
        """
        resolved = self._resolve_source_and_date(path)
        if resolved is None:
            return
        source_dir, date_str = resolved

        all_files = self._list_date_files(source_dir / date_str)
        if not all_files:
            # 日期目录为空或不存在：无可记录内容，跳过。
            return
        all_set = set(all_files)
        fname = Path(path).name

        log_path = source_dir / _LOG_FILENAME
        entries = self._parse_log(log_path)

        # 已有该日期的 processed 集合：完成态(None) 视为当时全部已处理，
        # 用当前文件集合代表；否则取记录中的文件名集合。
        existing: set[str] = set()
        idx: Optional[int] = None
        for i, (d, files) in enumerate(entries):
            if d == date_str:
                idx = i
                existing = set(all_files) if files is None else set(files)
                break

        this_processed = existing | {fname}
        # 丢弃已不存在的旧记录，但刚处理的文件始终保留。
        persisted = {n for n in this_processed if n in all_set or n == fname}
        complete = all_set.issubset(this_processed)

        new_entry: tuple[str, Optional[set[str]]] = (
            date_str,
            None if complete else persisted,
        )
        if idx is None:
            entries.append(new_entry)
        else:
            entries[idx] = new_entry

        # 按日期升序输出，保持稳定 diff。
        entries.sort(key=lambda e: e[0])
        self._atomic_write(log_path, entries)

    # ==================== Helpers ====================

    def _resolve_source_and_date(
        self, path: Path
    ) -> Optional[tuple[Path, str]]:
        """从 inbox 相对路径中解析 (source_dir, date_str)。

        source_dir = inbox_root 下、首个日期段之前的路径前缀
        （如 ``inbox/bilibili``）；若日期段直接位于 inbox 下（无 source 子目录），
        则 source_dir = inbox_root。

        返回 None 表示路径不在 inbox 内或找不到日期段（如扁平文件
        ``inbox/2026-05-28-test.md``），调用方应静默跳过。
        """
        try:
            rel = Path(path).relative_to(self._inbox_root)
        except ValueError:
            return None
        parts = rel.parts
        date_idx = next(
            (i for i, p in enumerate(parts) if _DATE_SEG_RE.match(p)), None
        )
        if date_idx is None:
            return None
        if date_idx == 0:
            source_dir = self._inbox_root
        else:
            source_dir = self._inbox_root.joinpath(*parts[:date_idx])
        return source_dir, parts[date_idx]

    @staticmethod
    def _list_date_files(date_dir: Path) -> list[str]:
        """列出日期目录下直接子级的 ``*.md`` 文件名（排序）。"""
        if not date_dir.exists():
            return []
        return sorted(p.name for p in date_dir.glob("*.md") if p.is_file())

    @staticmethod
    def _parse_log(log_path: Path) -> list[tuple[str, Optional[set[str]]]]:
        """解析进度日志，返回有序的 ``[(date, processed_set | None)]``。

        ``None`` 表示该日期已完成（裸日期行）；``set`` 表示部分完成
        （已处理文件名集合）。文件不存在或损坏时返回空列表。
        """
        if not log_path.exists():
            return []
        try:
            text = log_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                f"[SourceProgressLogger] 读取进度日志失败，按空状态处理: "
                f"{log_path} - {exc}"
            )
            return []

        entries: list[tuple[str, Optional[set[str]]]] = []
        current_date: Optional[str] = None
        current_files: Optional[set[str]] = None

        def _flush() -> None:
            if current_date is not None:
                entries.append((current_date, current_files))

        for raw in text.splitlines():
            line = raw.rstrip()
            if not line or line.startswith("#"):
                continue
            if _DATE_SEG_RE.match(line):
                _flush()
                current_date = line
                current_files = None  # 裸日期 → 完成态；遇到文件行再转为 set
                continue
            stripped = line.lstrip()
            if stripped.startswith("- ") and current_date is not None:
                name = stripped[2:].strip()
                if current_files is None:
                    current_files = set()
                current_files.add(name)
            # 其余畸形行忽略
        _flush()
        return entries

    @staticmethod
    def _atomic_write(
        log_path: Path, entries: list[tuple[str, Optional[set[str]]]]
    ) -> None:
        """原子重写进度日志：先写 ``.tmp`` 并 fsync，再 ``os.replace`` 覆盖。"""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [_HEADER]
        for date_str, files in entries:
            if not files:
                lines.append(f"{date_str}\n")
            else:
                lines.append(f"{date_str}\n")
                for name in sorted(files):
                    lines.append(f"  - {name}\n")
        content = "".join(lines)

        tmp_path = log_path.with_name(log_path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, log_path)
