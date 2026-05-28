"""
历史 collection/ Markdown 文件迁移工具。

将旧版 collection/<source>/<date>/<file>.md 文件复制到 vault/inbox/，
补写缺失的 YAML frontmatter，并通过内容哈希避免重复导入。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass
class ImportResult:
    imported_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    imported_paths: list[Path] = field(default_factory=list)


class LegacyCollectionImporter:
    """
    将旧版 collection/ 子目录 Markdown 文件导入到 inbox/ 目录。

    特性：
    - 基于内容 SHA-256 去重，同一文件第二次导入自动跳过
    - 对没有 YAML frontmatter 的旧文件自动补写
    - 支持按 source 名称过滤（bilibili / douyin / instapaper 等）
    """

    HASH_STORE_FILENAME = "import_hashes.json"

    def __init__(
        self,
        collection_root: Optional[Path] = None,
        inbox_dir: Optional[Path] = None,
        hash_store_path: Optional[Path] = None,
    ):
        from app.config import settings
        from app.services.content_storage import ContentStorageManager

        if collection_root is None:
            collection_root = ContentStorageManager().export_root
        if inbox_dir is None:
            inbox_dir = ContentStorageManager().get_inbox_dir()

        self.collection_root = Path(collection_root)
        self.inbox_dir = Path(inbox_dir)
        self.hash_store_path = hash_store_path or (
            self.inbox_dir.parent / "_meta" / self.HASH_STORE_FILENAME
        )
        self._hashes: dict[str, str] = self._load_hashes()

    # ==================== Public API ====================

    def import_sources(self, sources: list[str]) -> ImportResult:
        """
        导入指定 source 名称列表中的全部 Markdown 文件。

        Args:
            sources: 平台名称列表，例如 ["bilibili", "instapaper"]
        """
        result = ImportResult()
        for source in sources:
            source_dir = self.collection_root / source
            if not source_dir.exists():
                logger.debug(f"[LegacyImport] 跳过不存在的 source: {source_dir}")
                continue
            for md_file in sorted(source_dir.rglob("*.md")):
                self._import_file(md_file, source, result)
        self._save_hashes()
        return result

    def import_all(self) -> ImportResult:
        """导入 collection_root 下所有 source 的全部 Markdown 文件。"""
        sources = [
            p.name for p in self.collection_root.iterdir() if p.is_dir()
        ]
        return self.import_sources(sources)

    # ==================== Internal helpers ====================

    def _import_file(self, md_file: Path, source: str, result: ImportResult) -> None:
        try:
            raw_text = md_file.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.warning(f"[LegacyImport] 读取失败: {md_file} - {exc}")
            result.failed_count += 1
            return

        content_hash = hashlib.sha256(raw_text.encode()).hexdigest()

        if content_hash in self._hashes:
            logger.debug(f"[LegacyImport] 跳过（已导入）: {md_file.name}")
            result.skipped_count += 1
            return

        # 补写 frontmatter（如果旧文件没有）
        text_to_write = self._ensure_frontmatter(raw_text, md_file, source)

        # 目标文件名：YYYY-MM-DD-slug.md
        dest_name = self._build_dest_filename(md_file, source)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        dest_path = self._unique_path(self.inbox_dir / dest_name)

        try:
            dest_path.write_text(text_to_write, encoding="utf-8")
        except Exception as exc:
            logger.warning(f"[LegacyImport] 写入失败: {dest_path} - {exc}")
            result.failed_count += 1
            return

        self._hashes[content_hash] = str(dest_path)
        result.imported_count += 1
        result.imported_paths.append(dest_path)
        logger.info(f"[LegacyImport] 已导入: {md_file.name} → {dest_path.name}")

    def _ensure_frontmatter(self, text: str, md_file: Path, source: str) -> str:
        """若文件无 frontmatter，自动从标题和路径推断并补写。"""
        if text.startswith("---\n"):
            return text

        from app.services.knowledge_pipeline.frontmatter import build_export_frontmatter

        # 从路径推断日期
        date_str = self._extract_date_from_path(md_file)
        # 从第一行标题推断 title
        title = self._extract_title_from_text(text) or md_file.stem

        frontmatter = build_export_frontmatter(
            title=title,
            date_str=date_str,
            source="",
            summary="",
            platform=source,
        )
        return frontmatter + text

    def _extract_date_from_path(self, path: Path) -> str:
        """从路径分段中提取 YYYY-MM-DD 格式日期，找不到时返回今日日期。"""
        date_pattern = re.compile(r"\d{4}-\d{2}-\d{2}")
        for part in reversed(path.parts):
            m = date_pattern.search(part)
            if m:
                return m.group()
        return datetime.now().strftime("%Y-%m-%d")

    def _extract_title_from_text(self, text: str) -> str:
        """从 Markdown 正文第一个 # 标题行提取标题文本。"""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("# "):
                return stripped[2:].strip()
        return ""

    def _build_dest_filename(self, md_file: Path, source: str) -> str:
        """生成目标文件名：YYYY-MM-DD-slug.md"""
        date_str = self._extract_date_from_path(md_file)
        slug = self._slugify(md_file.stem)
        return f"{date_str}-{slug}.md"

    @staticmethod
    def _slugify(name: str, max_len: int = 60) -> str:
        clean = re.sub(r'[\\/:*?"<>|]', "_", name)
        clean = re.sub(r"\s+", "_", clean).strip("._")
        return clean[:max_len]

    @staticmethod
    def _unique_path(path: Path) -> Path:
        """若目标路径已存在则追加数字后缀。"""
        if not path.exists():
            return path
        stem = path.stem
        suffix = path.suffix
        parent = path.parent
        counter = 1
        while True:
            candidate = parent / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _load_hashes(self) -> dict[str, str]:
        if self.hash_store_path.exists():
            try:
                return json.loads(self.hash_store_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_hashes(self) -> None:
        self.hash_store_path.parent.mkdir(parents=True, exist_ok=True)
        self.hash_store_path.write_text(
            json.dumps(self._hashes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
