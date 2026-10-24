"""
正文总结服务。
"""

from __future__ import annotations

import asyncio
from typing import Optional

from loguru import logger

from app.services.text_postprocessor import TextPostProcessor
from app.services.text_postprocessor_factory import create_text_postprocessor

# 摘要默认走本地 LLM（content_summary 角色），本地推理服务并发能力弱，
# 多个文档同时请求会排队甚至超时，导致部分摘要被静默丢弃。
# 用一把进程级共享锁把摘要调用串行化，避免并发打满本地服务。
_summary_lock: asyncio.Lock = asyncio.Lock()

SUMMARY_BLOCK_START = "<!-- AI_SUMMARY_START -->"
SUMMARY_BLOCK_END = "<!-- AI_SUMMARY_END -->"


def _normalize_summary_yaml(summary_yaml: str) -> str:
    cleaned = (summary_yaml or "").strip()
    if not cleaned.startswith("```"):
        return cleaned

    lines = cleaned.splitlines()
    if len(lines) < 3 or lines[-1].strip() != "```":
        return cleaned

    opening = lines[0].strip()
    if not opening.startswith("```"):
        return cleaned

    return "\n".join(lines[1:-1]).strip()


def _dedupe_summary_yaml(summary_yaml: str) -> str:
    """保留首组 summary/key_points/tags，截断 LLM 漫游产生的重复字段。

    本地模型有时会在输出完一组完整字段后继续生成第二份 summary/key_points/tags，
    导致同一 YAML 块内出现两组内容。这里按行首 ``summary:`` 计数定位，
    只保留第一个 ``summary:`` 到第二个 ``summary:`` 之前的内容。
    """
    cleaned = (summary_yaml or "").strip()
    if not cleaned:
        return cleaned

    lines = cleaned.splitlines()
    summary_starts = [i for i, line in enumerate(lines) if line.strip().startswith("summary:")]
    if len(summary_starts) <= 1:
        return cleaned

    # 保留从首个 summary: 到第二个 summary: 之前的全部内容（即首组完整字段）。
    keep = lines[: summary_starts[1]]
    # 去掉末尾空行，保持整洁。
    while keep and not keep[-1].strip():
        keep.pop()
    logger.debug(
        "检测到 LLM 输出多组 summary/key_points/tags，已截断保留首组 "
        f"(丢弃 {len(lines) - len(keep)} 行)"
    )
    return "\n".join(keep)


def build_summary_block(summary_yaml: str) -> str:
    cleaned = _normalize_summary_yaml(summary_yaml)
    cleaned = _dedupe_summary_yaml(cleaned)
    if not cleaned:
        return ""
    return "\n".join(
        [
            SUMMARY_BLOCK_START,
            "```yaml",
            cleaned,
            "```",
            SUMMARY_BLOCK_END,
        ]
    )


def append_summary_section(lines: list[str], summary_block: str) -> None:
    cleaned = (summary_block or "").strip()
    if not cleaned:
        return
    lines.extend(["", "---", "", "## AI总结", "", cleaned])


class ContentSummaryService:
    """使用统一 LLM 工厂生成正文总结。"""

    def __init__(
        self,
        processor: Optional[TextPostProcessor] = None,
        prompt_template: Optional[str] = None,
    ):
        if processor:
            self.processor = processor
        elif prompt_template is not None:
            self.processor = create_text_postprocessor(
                role="content_summary",
                prompt_template=prompt_template,
            )
        else:
            self.processor = create_text_postprocessor(role="content_summary")

    async def summarize(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return ""
        # 串行化摘要请求：本地 LLM 并发能力弱，并发会排队/超时导致摘要丢失。
        # 锁在内容已确定需要总结之后再获取，避免空文本也占用锁。
        async with _summary_lock:
            result = await self.processor.postprocess(cleaned)
        return build_summary_block(result)
