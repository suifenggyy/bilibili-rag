"""
Topic 主题页更新器。

为每个 topic 维护 knowledge/_topics/<topic>.md 文件：
- 新 topic：创建完整模板（包含 ## 核心观点 和 ## 相关文章 dataview block）
- 已有 topic：只在 ## 核心观点 区块末尾追加新观点，不覆盖已有内容
- 幂等性：同一文章的同一观点不会被追加两次
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger


_TOPIC_TEMPLATE = """\
# {topic}

## 核心观点
{first_insight}

## 相关文章
```dataview
LIST FROM "knowledge" WHERE contains(topics, "{topic}") SORT date DESC
```
"""


class TopicUpdater:
    """更新 knowledge/_topics/<topic>.md 文件。"""

    def __init__(self, topics_dir: Optional[Path] = None):
        if topics_dir is None:
            from app.services.content_storage import ContentStorageManager
            topics_dir = ContentStorageManager().get_topics_dir()
        self._topics_dir = Path(topics_dir)

    async def update_topic(
        self,
        topic: str,
        article_title: str,
        article_date: str,
        new_insight: str,
        article_link: Optional[str] = None,
    ) -> Path:
        """
        更新单个 topic 页面。

        Args:
            topic: topic 名称（用于文件名和文件内 H1 标题）
            article_title: 文章标题
            article_date: 文章日期（YYYY-MM-DD）
            new_insight: 本次文章带来的观点摘要
            article_link: Obsidian wiki-link（可选），如 [[文章标题]]

        Returns:
            topic 文件路径
        """
        self._topics_dir.mkdir(parents=True, exist_ok=True)
        safe_topic = self._safe_filename(topic)
        topic_file = self._topics_dir / f"{safe_topic}.md"

        # Build insight line
        link_text = article_link or f"[[{article_title}]]"
        insight_line = f"**[{article_date} 更新 {link_text}]** {new_insight}"

        if not topic_file.exists():
            content = _TOPIC_TEMPLATE.format(
                topic=topic,
                first_insight=insight_line,
            )
            topic_file.write_text(content, encoding="utf-8")
            logger.info(f"[TopicUpdater] 创建 topic 页: {topic_file.name}")
        else:
            current = topic_file.read_text(encoding="utf-8")
            # Idempotency: skip if this insight is already in the file
            if new_insight in current:
                logger.debug(f"[TopicUpdater] 已存在观点，跳过: {topic}")
                return topic_file
            updated = self._append_insight(current, insight_line)
            topic_file.write_text(updated, encoding="utf-8")
            logger.info(f"[TopicUpdater] 追加观点到 topic 页: {topic_file.name}")

        return topic_file

    # ==================== Internal ====================

    @staticmethod
    def _safe_filename(name: str, max_len: int = 100) -> str:
        import re
        clean = re.sub(r'[\\/:*?"<>|]', "_", (name or "").strip())
        return clean[:max_len]

    @staticmethod
    def _append_insight(content: str, insight_line: str) -> str:
        """在 ## 核心观点 区块末尾插入新观点行。"""
        marker = "## 核心观点"
        if marker not in content:
            return content + f"\n{insight_line}"

        idx = content.index(marker) + len(marker)
        # Find the next ## heading or end of file
        rest = content[idx:]
        next_section = rest.find("\n## ")
        if next_section == -1:
            # Append before dataview block or at end
            dataview_idx = rest.find("```dataview")
            insert_at = idx + (dataview_idx - 1 if dataview_idx != -1 else len(rest))
        else:
            insert_at = idx + next_section

        return content[:insert_at].rstrip("\n") + "\n" + insight_line + "\n\n" + content[insert_at:].lstrip("\n")
