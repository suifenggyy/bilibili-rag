"""
知识库归档器。

将 inbox 文件移动（复制）到 knowledge/<category>/YYYY-MM-DD-slug.md，
并在 YAML frontmatter 中追加分类结果字段。
"""
from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger


class KnowledgeArchiver:
    """
    将已解析、已分类的文章归档到 knowledge/<category>/ 目录。

    - 生成 YYYY-MM-DD-slug.md 文件名
    - 在 frontmatter 中写入 category / topics / quality_score / processing_log
    - 归档成功后将 inbox 文件移入 inbox/done/（不删除）
    - 失败时将 inbox 文件移入 inbox/failed/
    """

    def __init__(self, knowledge_dir: Optional[Path] = None):
        if knowledge_dir is None:
            from app.services.content_storage import ContentStorageManager
            knowledge_dir = ContentStorageManager().get_knowledge_dir()
        self._knowledge_dir = Path(knowledge_dir)

    def archive(self, inbox_path: Path, doc, classification) -> Path:
        """
        执行归档。

        Args:
            inbox_path: inbox 中的源文件路径
            doc: ParsedKnowledgeDocument 实例
            classification: ClassificationResult 实例

        Returns:
            归档后的目标文件 Path
        """
        category = classification.category or "未分类"
        category_dir = self._knowledge_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)

        # 生成目标文件名
        filename = self._build_filename(doc.date_str, doc.title)
        target_path = self._unique_path(category_dir / filename)

        # 读取原始文件内容，注入处理结果到 frontmatter
        try:
            original_text = Path(inbox_path).read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            logger.error(f"[Archiver] 读取 inbox 文件失败: {inbox_path} - {exc}")
            raise

        enriched_text = self._enrich_frontmatter(original_text, classification)

        try:
            target_path.write_text(enriched_text, encoding="utf-8")
        except Exception as exc:
            logger.error(f"[Archiver] 写入归档文件失败: {target_path} - {exc}")
            raise

        logger.info(f"[Archiver] 归档成功: {inbox_path.name} → {target_path}")
        return target_path

    # ==================== Internal ====================

    def _build_filename(self, date_str: str, title: str) -> str:
        slug = self._slugify(title)
        safe_date = (date_str or "").strip() or datetime.now().strftime("%Y-%m-%d")
        return f"{safe_date}-{slug}.md"

    @staticmethod
    def _slugify(name: str, max_len: int = 60) -> str:
        clean = re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip())
        clean = re.sub(r"\s+", "_", clean).strip("._")
        return clean[:max_len] if clean else "untitled"

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        stem, suffix, parent = path.stem, path.suffix, path.parent
        counter = 1
        while True:
            candidate = parent / f"{stem}_{counter}{suffix}"
            if not candidate.exists():
                return candidate
            counter += 1

    def _enrich_frontmatter(self, text: str, classification) -> str:
        """在 YAML frontmatter 末尾插入分类结果字段。"""
        if not text.startswith("---\n"):
            return text

        end_idx = text.find("\n---\n", 4)
        if end_idx == -1:
            return text

        yaml_block = text[4:end_idx]
        body = text[end_idx + 5:]

        processed_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        topics_yaml = "\n".join(f"  - {t}" for t in classification.topics)

        extra_lines = [
            f"category: {classification.category}",
            "topics:",
            topics_yaml if topics_yaml else "  []",
            f"processed_at: {processed_at}",
            f"quality_score: {classification.quality_score:.2f}",
            f"processing_log: {classification.processing_log}",
        ]
        enriched_yaml = yaml_block.rstrip("\n") + "\n" + "\n".join(extra_lines)
        return f"---\n{enriched_yaml}\n---\n{body}"
