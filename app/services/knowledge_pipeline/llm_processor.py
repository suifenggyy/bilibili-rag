"""
LLM processor adapters for KnowledgeDistiller and TopicPathResolver.

These adapters bridge the gap between:
  - LLMService interface (unified factory, role-based config)
  - KnowledgeDistiller / TopicPathResolver interface (kwargs-in / dict-out via __call__)

Each adapter formats its specific prompt, calls the LLM via the factory,
and parses the YAML/JSON output.
"""

from __future__ import annotations

import re
from typing import Any, Awaitable, Callable

from loguru import logger

from app.services.llm.types import LLMMessage, LLMResponse
from app.services.llm.factory import get_llm_service


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
    """Adapter: KnowledgeDistiller → LLMService → parse YAML dict."""

    def __init__(self, llm_service=None, prompt: str | None = None):
        self._llm = llm_service
        self._prompt = prompt

    def _get_llm(self):
        if self._llm is not None:
            return self._llm
        return get_llm_service("knowledge_distill")

    async def __call__(
        self,
        *,
        title: str,
        summary: str,
        key_points: list[str],
        body: str,
    ) -> dict:
        llm = self._get_llm()
        from app.config import settings
        prompt = self._prompt or settings.knowledge_note_distill_prompt
        user_content = (
            f"标题：{title}\n"
            f"摘要：{summary}\n"
            f"关键要点：{key_points}\n"
            f"正文：\n{body}"
        )
        messages = [
            LLMMessage(role="system", content=prompt),
            LLMMessage(role="user", content=user_content),
        ]
        response = await llm.complete(messages)
        return _parse_yaml_output(response.content)


class TopicPathProcessor:
    """Adapter: TopicPathResolver → LLMService → parse YAML dict."""

    def __init__(self, llm_service=None, prompt: str | None = None):
        self._llm = llm_service
        self._prompt = prompt

    def _get_llm(self):
        if self._llm is not None:
            return self._llm
        return get_llm_service("knowledge_topic_path")

    async def __call__(
        self,
        *,
        units: Any,
        graph_snapshot: dict,
    ) -> dict:
        llm = self._get_llm()
        from app.config import settings
        prompt = self._prompt or settings.knowledge_topic_path_prompt
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
        messages = [
            LLMMessage(role="system", content=prompt),
            LLMMessage(role="user", content=user_content),
        ]
        response = await llm.complete(messages)
        return _parse_yaml_output(response.content)


class TopicSummaryDecisionProcessor:
    """Adapter for deciding whether to rewrite a topic summary."""

    def __init__(self, llm_service=None, prompt: str | None = None):
        self._llm = llm_service
        self._prompt = prompt

    def _get_llm(self):
        if self._llm is not None:
            return self._llm
        return get_llm_service("knowledge_topic_summary")

    async def __call__(self, **kwargs) -> dict:
        llm = self._get_llm()
        from app.config import settings
        prompt = self._prompt or settings.knowledge_topic_summary_decision_prompt
        messages = [
            LLMMessage(role="system", content=prompt),
            LLMMessage(role="user", content=str(kwargs)),
        ]
        response = await llm.complete(messages)
        return _parse_yaml_output(response.content)


class TopicSummaryProcessor:
    """Adapter for rewriting a topic summary."""

    def __init__(self, llm_service=None, prompt: str | None = None):
        self._llm = llm_service
        self._prompt = prompt

    def _get_llm(self):
        if self._llm is not None:
            return self._llm
        return get_llm_service("knowledge_topic_summary")

    async def __call__(self, **kwargs) -> dict:
        llm = self._get_llm()
        from app.config import settings
        prompt = self._prompt or settings.knowledge_topic_summary_prompt
        messages = [
            LLMMessage(role="system", content=prompt),
            LLMMessage(role="user", content=str(kwargs)),
        ]
        response = await llm.complete(messages)
        return _parse_yaml_output(response.content)


class TopicDetailProcessor:
    """Adapter for extracting topic detail items."""

    def __init__(self, llm_service=None, prompt: str | None = None):
        self._llm = llm_service
        self._prompt = prompt

    def _get_llm(self):
        if self._llm is not None:
            return self._llm
        return get_llm_service("knowledge_topic_detail")

    async def __call__(self, **kwargs) -> dict:
        llm = self._get_llm()
        from app.config import settings
        prompt = self._prompt or settings.knowledge_topic_detail_prompt
        messages = [
            LLMMessage(role="system", content=prompt),
            LLMMessage(role="user", content=str(kwargs)),
        ]
        response = await llm.complete(messages)
        return _parse_yaml_output(response.content)


class TopicDedupProcessor:
    """Adapter: topic dedup similarity check → LLMService → parse YAML dict."""

    def __init__(self, llm_service=None, prompt: str | None = None):
        self._llm = llm_service
        self._prompt = prompt

    def _get_llm(self):
        if self._llm is not None:
            return self._llm
        return get_llm_service("knowledge_topic_dedup")

    async def __call__(
        self,
        *,
        candidates: list[dict],
    ) -> dict:
        """Check batch of topic-name pairs for semantic similarity.

        Args:
            candidates: list of dicts with keys name_a, name_b,
                        context_a (list[str]), context_b (list[str]).
        Returns:
            dict with key "results" → list of similarity verdicts.
        """
        llm = self._get_llm()
        from app.config import settings
        prompt = self._prompt or settings.knowledge_topic_dedup_prompt
        import yaml
        user_content = f"待判断的主题对：\n{yaml.dump(candidates, allow_unicode=True, default_flow_style=False)}"
        messages = [
            LLMMessage(role="system", content=prompt),
            LLMMessage(role="user", content=user_content),
        ]
        response = await llm.complete(messages)
        return _parse_yaml_output(response.content)
