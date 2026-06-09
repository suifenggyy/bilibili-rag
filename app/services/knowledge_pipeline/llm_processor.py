"""
LLM processor adapters for KnowledgeDistiller and TopicPathResolver.

These adapters bridge the gap between:
  - TextPostProcessor interface (string-in / string-out via .postprocess())
  - KnowledgeDistiller / TopicPathResolver interface (kwargs-in / dict-out via __call__)

Each adapter formats its specific prompt, calls the LLM, and parses the YAML/JSON output.
"""
from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from loguru import logger


def _parse_yaml_output(text: str) -> dict:
    """Try to parse LLM output as YAML (or JSON fallback)."""
    import yaml
    text = text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:yaml|yml|json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()
    try:
        result = yaml.safe_load(text)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    # JSON fallback
    import json
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    raise ValueError(f"LLM output is not valid YAML/JSON: {text[:200]}")


class DistillerProcessor:
    """Adapter: KnowledgeDistiller → TextPostProcessor → LLM → parse YAML dict."""

    def __init__(self, postprocessor=None, prompt: str | None = None):
        self._postprocessor = postprocessor
        self._prompt = prompt

    def _get_postprocessor(self):
        if self._postprocessor is not None:
            return self._postprocessor
        from app.services.text_postprocessor_factory import create_text_postprocessor
        from app.config import settings
        return create_text_postprocessor(
            prompt_template=self._prompt or settings.knowledge_note_distill_prompt,
        )

    async def __call__(
        self,
        *,
        title: str,
        summary: str,
        key_points: list[str],
        body: str,
    ) -> dict:
        processor = self._get_postprocessor()
        user_content = (
            f"标题：{title}\n"
            f"摘要：{summary}\n"
            f"关键要点：{key_points}\n"
            f"正文：\n{body}"
        )
        raw = await processor.postprocess(user_content, title=title)
        return _parse_yaml_output(raw)


class TopicPathProcessor:
    """Adapter: TopicPathResolver → TextPostProcessor → LLM → parse YAML dict."""

    def __init__(self, postprocessor=None, prompt: str | None = None):
        self._postprocessor = postprocessor
        self._prompt = prompt

    def _get_postprocessor(self):
        if self._postprocessor is not None:
            return self._postprocessor
        from app.services.text_postprocessor_factory import create_text_postprocessor
        from app.config import settings
        return create_text_postprocessor(
            prompt_template=self._prompt or settings.knowledge_topic_path_prompt,
        )

    async def __call__(
        self,
        *,
        units: Any,
        graph_snapshot: dict,
    ) -> dict:
        processor = self._get_postprocessor()
        from dataclasses import asdict
        units_dict = asdict(units) if hasattr(units, "__dataclass_fields__") else vars(units)
        # Build a concise representation for the LLM
        source_identity = units_dict.get("source_identity", {})
        user_content = (
            f"来源标题：{source_identity.get('title', '')}\n"
            f"来源URL：{source_identity.get('source_url', '')}\n"
            f"摘要：{units_dict.get('summary', '')}\n"
            f"核心概念：{units_dict.get('concepts', [])}\n"
            f"方法：{units_dict.get('methods', [])}\n"
            f"决策规则：{units_dict.get('decision_rules', [])}\n"
            f"示例：{units_dict.get('examples', [])}\n"
            f"风险：{units_dict.get('risks', [])}\n"
            f"\n已有话题图：{graph_snapshot}\n"
            f"\n已有别名：{[node.get('aliases', []) for node in graph_snapshot.get('nodes', [])]}"
        )
        raw = await processor.postprocess(user_content, title=source_identity.get("title", ""))
        return _parse_yaml_output(raw)


class TopicSummaryDecisionProcessor:
    """Adapter for deciding whether to rewrite a topic summary."""

    def __init__(self, postprocessor=None, prompt: str | None = None):
        self._postprocessor = postprocessor
        self._prompt = prompt

    def _get_postprocessor(self):
        if self._postprocessor is not None:
            return self._postprocessor
        from app.services.text_postprocessor_factory import create_text_postprocessor
        from app.config import settings
        return create_text_postprocessor(
            prompt_template=self._prompt or settings.knowledge_topic_summary_decision_prompt,
        )

    async def __call__(self, **kwargs) -> dict:
        processor = self._get_postprocessor()
        raw = await processor.postprocess(str(kwargs))
        return _parse_yaml_output(raw)


class TopicSummaryProcessor:
    """Adapter for rewriting a topic summary."""

    def __init__(self, postprocessor=None, prompt: str | None = None):
        self._postprocessor = postprocessor
        self._prompt = prompt

    def _get_postprocessor(self):
        if self._postprocessor is not None:
            return self._postprocessor
        from app.services.text_postprocessor_factory import create_text_postprocessor
        from app.config import settings
        return create_text_postprocessor(
            prompt_template=self._prompt or settings.knowledge_topic_summary_prompt,
        )

    async def __call__(self, **kwargs) -> dict:
        processor = self._get_postprocessor()
        raw = await processor.postprocess(str(kwargs))
        return _parse_yaml_output(raw)


class TopicDetailProcessor:
    """Adapter for extracting topic detail items."""

    def __init__(self, postprocessor=None, prompt: str | None = None):
        self._postprocessor = postprocessor
        self._prompt = prompt

    def _get_postprocessor(self):
        if self._postprocessor is not None:
            return self._postprocessor
        from app.services.text_postprocessor_factory import create_text_postprocessor
        from app.config import settings
        return create_text_postprocessor(
            prompt_template=self._prompt or settings.knowledge_topic_detail_prompt,
        )

    async def __call__(self, **kwargs) -> dict:
        processor = self._get_postprocessor()
        raw = await processor.postprocess(str(kwargs))
        return _parse_yaml_output(raw)
