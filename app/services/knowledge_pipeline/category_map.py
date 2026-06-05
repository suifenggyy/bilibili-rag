"""
分类图谱持久化层。

管理 _meta/category-map.json，记录已知分类与 topic 映射。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from loguru import logger

DEFAULT_CATEGORY_MAP_FILENAME = "category-map.json"


class CategoryMapRepository:
    """
    读写 _meta/category-map.json。

    文件格式：
    {
      "categories": {
        "AI与技术": {
          "slug": "AI与技术",
          "topics": ["AI大模型", "Prompt工程"]
        }
      }
    }
    """

    def __init__(
        self,
        meta_dir: Optional[Path] = None,
        filename: str = DEFAULT_CATEGORY_MAP_FILENAME,
    ):
        if meta_dir is None:
            from app.services.content_storage import ContentStorageManager
            meta_dir = ContentStorageManager().get_meta_dir()
        self._meta_dir = Path(meta_dir)
        self._file_path = self._meta_dir / filename
        self._data: dict = self._load()

    # ==================== Public API ====================

    def list_categories(self) -> list[str]:
        """返回已知分类名称列表（按首次出现顺序）。"""
        return list(self._data.get("categories", {}).keys())

    def get_topics(self, category: str) -> list[str]:
        """返回指定分类下的 topic 列表。"""
        cats = self._data.get("categories", {})
        return list(cats.get(category, {}).get("topics", []))

    def merge_classification(self, category: str, topics: list[str]) -> None:
        """
        将新的分类和 topics 合并到图谱中。

        - 分类不存在时自动创建
        - topics 按精确文本去重，保留首次出现顺序
        """
        cats = self._data.setdefault("categories", {})
        if category not in cats:
            cats[category] = {"slug": category, "topics": []}

        existing = cats[category]["topics"]
        existing_set = set(existing)
        for topic in topics:
            t = (topic or "").strip()
            if t and t not in existing_set:
                existing.append(t)
                existing_set.add(t)

    async def save(self, writer=None) -> None:
        """将当前图谱写回文件。"""
        text = json.dumps(self._data, ensure_ascii=False, indent=2)
        if writer is not None:
            try:
                vault_relative_path = self._file_path.relative_to(writer.vault_root)
            except Exception:
                self._meta_dir.mkdir(parents=True, exist_ok=True)
                self._file_path.write_text(text, encoding="utf-8")
            else:
                await writer.write_text(str(vault_relative_path), text)
        else:
            self._meta_dir.mkdir(parents=True, exist_ok=True)
            self._file_path.write_text(text, encoding="utf-8")
        logger.debug(f"[CategoryMap] saved → {self._file_path}")

    # ==================== Internal ====================

    def _load(self) -> dict:
        if self._file_path.exists():
            try:
                data = json.loads(self._file_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception as exc:
                logger.warning(f"[CategoryMap] 读取失败，使用空图谱: {exc}")
        return {"categories": {}}
