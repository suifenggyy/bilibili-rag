"""
知识库 Markdown 解析器。

从 inbox 文件中提取 YAML frontmatter + 正文，生成 ParsedKnowledgeDocument。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class FrontmatterMissingError(ValueError):
    """Markdown 文件缺少 YAML frontmatter 分隔符。"""


class FrontmatterFieldMissingError(ValueError):
    """YAML frontmatter 缺少必要字段。"""


REQUIRED_FIELDS = ("title", "date", "source")


@dataclass
class ParsedKnowledgeDocument:
    """解析后的知识文档。"""
    title: str
    date_str: str
    source_url: str
    summary: str
    body: str
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)


class KnowledgeMarkdownParser:
    """
    解析 inbox Markdown 文件，提取 frontmatter 和正文。

    验证规则：
    - frontmatter 缺失（文件不以 --- 开头）→ FrontmatterMissingError
    - title / date / source 缺失 → FrontmatterFieldMissingError
    - summary 允许为空，但字段必须存在（缺失时视为空字符串，不报错）
    """

    def parse_text(self, text: str) -> ParsedKnowledgeDocument:
        text = text or ""
        if not text.startswith("---\n"):
            raise FrontmatterMissingError(
                "Markdown 文件缺少 YAML frontmatter（文件必须以 '---' 开头）"
            )

        # Find closing ---
        end_idx = text.find("\n---\n", 4)
        if end_idx == -1:
            # Try end of file closing with trailing newline
            if text.rstrip().endswith("\n---"):
                end_idx = text.rstrip().rfind("\n---")
            else:
                raise FrontmatterMissingError("YAML frontmatter 未找到关闭分隔符 '---'")

        yaml_text = text[4:end_idx]
        body = text[end_idx + 5:]  # skip "\n---\n"

        frontmatter = self._parse_yaml(yaml_text)

        # Validate required fields
        missing = [f for f in REQUIRED_FIELDS if not frontmatter.get(f)]
        if missing:
            raise FrontmatterFieldMissingError(
                f"frontmatter 缺少必要字段: {', '.join(missing)}"
            )

        return ParsedKnowledgeDocument(
            title=str(frontmatter["title"]),
            date_str=str(frontmatter["date"]),
            source_url=str(frontmatter["source"]),
            summary=str(frontmatter.get("summary") or ""),
            body=body,
            raw_frontmatter=frontmatter,
        )

    def parse_file(self, path) -> ParsedKnowledgeDocument:
        from pathlib import Path
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        return self.parse_text(text)

    @staticmethod
    def _parse_yaml(yaml_text: str) -> dict[str, Any]:
        """解析 YAML 文本为字典，优先使用 PyYAML，失败时使用简单正则回退。"""
        try:
            import yaml  # type: ignore[import-untyped]
            result = yaml.safe_load(yaml_text)
            if isinstance(result, dict):
                return result
        except Exception:
            pass

        # Simple line-by-line fallback for basic key: value
        data: dict[str, Any] = {}
        for line in yaml_text.splitlines():
            if ": " in line:
                key, _, val = line.partition(": ")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                data[key] = val
            elif line.endswith(":"):
                key = line.rstrip(":").strip()
                data[key] = ""
        return data
