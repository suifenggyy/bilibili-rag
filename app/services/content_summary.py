"""
正文总结服务。
"""

from typing import Optional

from app.config import settings
from app.services.text_model_prompts import DEFAULT_TEXT_MODEL_SUMMARY_PROMPT
from app.services.text_postprocessor import TextPostProcessor
from app.services.text_postprocessor_factory import create_text_postprocessor

SUMMARY_BLOCK_START = "<!-- AI_SUMMARY_START -->"
SUMMARY_BLOCK_END = "<!-- AI_SUMMARY_END -->"
SUMMARY_PROMPT = DEFAULT_TEXT_MODEL_SUMMARY_PROMPT


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


def build_summary_block(summary_yaml: str) -> str:
    cleaned = _normalize_summary_yaml(summary_yaml)
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
    """使用现有 ollama_text_model 生成正文总结。"""

    def __init__(self, processor: Optional[TextPostProcessor] = None):
        self.processor = processor or create_text_postprocessor(
            prompt_template=settings.text_model_summary_prompt,
        )

    async def summarize(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return ""
        result = await self.processor.postprocess(cleaned)
        return build_summary_block(result)
