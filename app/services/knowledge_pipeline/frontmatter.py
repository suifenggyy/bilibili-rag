"""
统一 Markdown frontmatter 构建/解析工具。

供所有导出脚本调用，确保输出到 vault/inbox/ 的文件包含规范化 YAML frontmatter。
"""
from __future__ import annotations

import re
from typing import Optional


# ==================== YAML scalar helpers ====================

def _yaml_scalar(value: str) -> str:
    """
    将字符串序列化为安全的 YAML 标量。
    仅在必要时用双引号包裹（避免误判合法字符如 URL 中的 / - . 等）。
    """
    if not value:
        return '""'
    # 需要引号的条件：
    # 1. 开头有 YAML 特殊字符
    # 2. 包含 ": "（冒号后接空格，YAML mapping indicator）
    # 3. 包含 " #"（注释字符）
    # 4. 包含双引号或单引号
    needs_quote = (
        value[0] in '{[>|&*?!%@`'
        or ": " in value
        or " #" in value
        or '"' in value
        or "'" in value
        or value.endswith(":")
    )
    if needs_quote:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _normalize_single_line(value: str) -> str:
    """将多行字符串压缩为单行（去除换行符和多余空白）。"""
    return re.sub(r"\s+", " ", (value or "").replace("\n", " ")).strip()


# ==================== Frontmatter builder ====================

def build_export_frontmatter(
    *,
    title: str,
    date_str: str,
    source: str,
    summary: str,
    platform: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> str:
    """
    构建规范化 YAML frontmatter。

    返回以 "---\n" 开头、以 "---\n\n" 结尾的字符串，可直接前缀到 Markdown 正文。

    Args:
        title: 内容标题
        date_str: 发布日期（YYYY-MM-DD 格式）
        source: 原始内容 URL
        summary: 单行摘要；多行将自动压缩
        platform: 可选平台标识（bilibili / instapaper / douyin 等）
        tags: 可选标签列表
    """
    safe_summary = _normalize_single_line(summary)
    lines = [
        "---",
        f"title: {_yaml_scalar(title)}",
        f"date: {date_str}",
        f"source: {_yaml_scalar(source)}",
        f"summary: {_yaml_scalar(safe_summary)}",
    ]
    if platform:
        lines.append(f"platform: {_yaml_scalar(platform)}")
    if tags:
        lines.append("tags:")
        for tag in tags:
            lines.append(f"  - {_yaml_scalar(tag)}")
    lines += ["---", ""]
    return "\n".join(lines) + "\n"


# ==================== Summary extractor ====================

def extract_plain_summary(summary_block: str) -> str:
    """
    从现有 AI_SUMMARY YAML 区块中提取纯文本摘要。

    AI_SUMMARY 区块格式：
        <!-- AI_SUMMARY_START -->
        ```yaml
        summary: |
          文字内容
        key_points:
          - ...
        ```
        <!-- AI_SUMMARY_END -->

    若解析失败或输入为空，返回空字符串。
    """
    if not summary_block or not summary_block.strip():
        return ""

    # 提取 ```yaml ... ``` 内的 YAML 内容
    yaml_match = re.search(r"```yaml\s*(.*?)```", summary_block, re.DOTALL)
    if not yaml_match:
        return ""

    yaml_content = yaml_match.group(1).strip()

    # 优先用简单解析提取 summary 字段（不强依赖 PyYAML）
    try:
        import yaml  # type: ignore[import-untyped]
        data = yaml.safe_load(yaml_content)
        if isinstance(data, dict):
            raw = data.get("summary", "")
            if raw:
                return _normalize_single_line(str(raw))
    except Exception:
        pass

    # Fallback：正则提取 summary: | 或 summary: "..." 或 summary: text
    pattern = re.compile(
        r"^summary:\s*(?:\|[-+]?\s*\n((?:[ \t]+.+\n?)+)|['\"](.+?)['\"]|(.+))$",
        re.MULTILINE,
    )
    m = pattern.search(yaml_content)
    if m:
        raw = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        return _normalize_single_line(raw)

    return ""
