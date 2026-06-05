"""
处理日志写入器。

维护 _meta/logs/YYYY-MM-DD.log，每次流水线处理时追加一条日志行。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger


class ProcessingLogger:
    """将单文件处理日志追加写入 _meta/logs/YYYY-MM-DD.log。"""

    def __init__(self, meta_dir: Optional[Path] = None):
        if meta_dir is None:
            from app.services.content_storage import ContentStorageManager
            meta_dir = ContentStorageManager().get_meta_dir()
        self._logs_dir = Path(meta_dir) / "logs"

    async def log_classification(
        self,
        article_title: str,
        category: str,
        topics: list[str],
        quality_score: float,
        elapsed_seconds: float = 0.0,
        day: Optional[datetime] = None,
        writer=None,
    ) -> None:
        """记录分类结果日志行。"""
        now = day or datetime.now()
        ts = now.strftime("%Y-%m-%dT%H:%M:%S")
        topics_str = f"[{', '.join(topics)}]" if topics else "[]"
        line1 = f"{ts} [INFO] 文章: {article_title} | 分类: {category} | topics: {topics_str}\n"
        line2 = f"{ts} [INFO] quality_score: {quality_score:.2f} | 耗时: {elapsed_seconds:.1f}s\n"
        await self._append(line1 + line2, now, writer=writer)

    async def log_error(
        self,
        article_title: str,
        error_message: str,
        day: Optional[datetime] = None,
        writer=None,
    ) -> None:
        """记录处理失败日志行。"""
        now = day or datetime.now()
        ts = now.strftime("%Y-%m-%dT%H:%M:%S")
        line = f"{ts} [ERROR] 文章: {article_title} | 错误: {error_message}\n"
        await self._append(line, now, writer=writer)

    async def _append(self, text: str, day: datetime, writer=None) -> None:
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = self._logs_dir / day.strftime("%Y-%m-%d.log")
        if writer is not None:
            try:
                vault_relative_path = log_file.relative_to(writer.vault_root)
            except Exception:
                with log_file.open("a", encoding="utf-8") as f:
                    f.write(text)
            else:
                existing = ""
                if log_file.exists():
                    existing = log_file.read_text(encoding="utf-8")
                await writer.write_text(str(vault_relative_path), existing + text)
        else:
            with log_file.open("a", encoding="utf-8") as f:
                f.write(text)
        logger.debug(f"[ProcessingLogger] 写入日志: {log_file.name}")
