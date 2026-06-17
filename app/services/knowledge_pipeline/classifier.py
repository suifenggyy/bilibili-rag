"""
知识库内容分类器。

使用统一 LLM 工厂层（通过 get_llm_service）对文章进行自动分类，
输出 category / topics / quality_score / processing_log。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from app.services.llm.types import LLMMessage
from app.services.llm.factory import get_llm_service

# ==================== 分类 Prompt ====================

CLASSIFICATION_PROMPT = """你是知识库分类器。根据输入内容，输出结构化 YAML 分类结果。

输入：
1. 标题：{title}
2. 摘要：{summary}
3. 已有分类列表：{categories}

要求：
- category：从已有分类列表中选择最匹配的分类；若无合适分类，可新建一个简洁的中文分类名
- topics：3 个以内的关键主题词（中文），去重、去空白
- quality_score：0.0~1.0 之间的内容质量评分（浮点数，依据信息量和摘要完整度）
- processing_log：一句话说明分类依据

严格按以下 YAML 格式输出，不要添加任何额外说明：
category: <分类名>
topics:
  - <主题1>
quality_score: 0.00
processing_log: <分类依据>
"""


# ==================== 数据契约 ====================

@dataclass
class ClassificationResult:
    """分类结果契约。"""
    category: str
    topics: list[str] = field(default_factory=list)
    quality_score: float = 0.0
    processing_log: str = ""


# ==================== 分类器 ====================

class KnowledgeClassifier:
    """
    基于 LLM 的知识库文章分类器。

    优先复用 existing category-map 中的分类名，超时/失败时返回安全 fallback。
    """

    FALLBACK_CATEGORY = "未分类"

    def __init__(self, llm_service=None, prompt: str | None = None):
        """
        Args:
            llm_service: LLMService 实例（依赖注入，方便测试）；
                         为 None 时通过 get_llm_service("knowledge_classify") 创建。
            prompt: 自定义分类 prompt，默认使用 CLASSIFICATION_PROMPT。
        """
        self._llm = llm_service
        self._prompt = prompt

    def _get_llm(self):
        if self._llm is not None:
            return self._llm
        return get_llm_service("knowledge_classify")

    async def classify(
        self,
        title: str,
        summary: str,
        existing_categories: list[str],
    ) -> ClassificationResult:
        """
        对单篇文章执行 LLM 分类。

        Args:
            title: 文章标题
            summary: 文章摘要
            existing_categories: 现有分类列表，供 LLM 优先复用

        Returns:
            ClassificationResult（失败时返回安全 fallback）
        """
        llm = self._get_llm()
        prompt = self._prompt or CLASSIFICATION_PROMPT
        user_content = (
            f"标题：{title}\n"
            f"摘要：{summary}\n"
            f"已有分类：{', '.join(existing_categories) or '无'}"
        )

        try:
            from app.config import settings
            timeout = settings.knowledge_classification_timeout
        except Exception:
            timeout = 120

        try:
            messages = [
                LLMMessage(role="system", content=prompt),
                LLMMessage(role="user", content=user_content),
            ]
            response = await llm.complete(messages, timeout=timeout)
            return self._parse_result(response.content)
        except Exception as exc:
            logger.warning(f"[KnowledgeClassifier] LLM 分类失败，使用 fallback: {exc}")
            return ClassificationResult(
                category=self.FALLBACK_CATEGORY,
                topics=[],
                quality_score=0.0,
                processing_log=f"LLM 分类失败: {exc}",
            )

    # ==================== Internal ====================

    def _parse_result(self, raw: str) -> ClassificationResult:
        """从 LLM 输出的 YAML 文本解析 ClassificationResult。"""
        # Try PyYAML first
        try:
            import yaml  # type: ignore[import-untyped]
            data = yaml.safe_load(raw)
            if isinstance(data, dict):
                return self._build_from_dict(data)
        except Exception:
            pass

        # Fallback: line-by-line regex parsing
        return self._parse_with_regex(raw)

    def _build_from_dict(self, data: dict) -> ClassificationResult:
        category = str(data.get("category") or self.FALLBACK_CATEGORY).strip()
        raw_topics = data.get("topics") or []
        if isinstance(raw_topics, list):
            topics = [str(t).strip() for t in raw_topics if str(t).strip()]
        else:
            topics = []
        # Deduplicate
        seen: set[str] = set()
        deduped = []
        for t in topics:
            if t not in seen:
                seen.add(t)
                deduped.append(t)
        # Clamp quality_score
        try:
            score = float(data.get("quality_score", 0.0))
            score = max(0.0, min(1.0, score))
        except (TypeError, ValueError):
            score = 0.0

        processing_log = str(data.get("processing_log") or "").strip()
        return ClassificationResult(
            category=category,
            topics=deduped,
            quality_score=score,
            processing_log=processing_log,
        )

    def _parse_with_regex(self, raw: str) -> ClassificationResult:
        """正则回退解析，处理 LLM 输出非标准 YAML 的情况。"""
        category = self.FALLBACK_CATEGORY
        topics: list[str] = []
        quality_score = 0.0
        processing_log = ""

        cat_m = re.search(r"^category:\s*(.+)$", raw, re.MULTILINE)
        if cat_m:
            category = cat_m.group(1).strip()

        topic_m = re.findall(r"^\s*-\s+(.+)$", raw, re.MULTILINE)
        seen: set[str] = set()
        for t in topic_m:
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                topics.append(t)

        score_m = re.search(r"^quality_score:\s*([\d.]+)$", raw, re.MULTILINE)
        if score_m:
            try:
                quality_score = max(0.0, min(1.0, float(score_m.group(1))))
            except ValueError:
                pass

        log_m = re.search(r"^processing_log:\s*(.+)$", raw, re.MULTILINE)
        if log_m:
            processing_log = log_m.group(1).strip()

        return ClassificationResult(
            category=category,
            topics=topics,
            quality_score=quality_score,
            processing_log=processing_log,
        )
