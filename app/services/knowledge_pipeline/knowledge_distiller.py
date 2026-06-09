from dataclasses import dataclass
from typing import Optional, List, Dict, Any, Callable, Awaitable
from .parser import ParsedKnowledgeDocument

@dataclass
class DistilledKnowledge:
    source_identity: dict[str, str]
    summary: str
    concepts: list[str]
    methods: list[str]
    decision_rules: list[str]
    examples: list[str]
    risks: list[str]
    quotes: list[dict[str, str]]
    source_excerpt_fingerprints: list[str]

@dataclass
class DistillationResult:
    status: str  # processed | skipped | failed
    knowledge: Optional[DistilledKnowledge]
    failure_reason: Optional[str]

class KnowledgeDistiller:
    def __init__(self, processor: Callable[..., Awaitable[dict]], min_body_chars: int = 80):
        self.processor = processor
        self.min_body_chars = min_body_chars

    def _validate_payload(self, payload: dict) -> dict:
        required_list_fields = ["concepts", "methods", "decision_rules", "examples", "risks", "quotes"]
        if not isinstance(payload.get("summary"), str) or not payload["summary"].strip():
            raise ValueError("invalid distillation payload")
        for field in required_list_fields:
            if not isinstance(payload.get(field, []), list):
                raise ValueError("invalid distillation payload")
        return payload

    async def distill(self, doc: ParsedKnowledgeDocument, source_identity: dict[str, str]) -> DistillationResult:
        if len(doc.body.strip()) < self.min_body_chars and not doc.key_points and not doc.summary.strip():
            return DistillationResult(status="skipped", knowledge=None, failure_reason="weak_signal")
        try:
            payload = await self.processor(
                title=doc.title,
                summary=doc.summary,
                key_points=doc.key_points,
                body=doc.body,
            )
        except Exception as exc:
            return DistillationResult(status="failed", knowledge=None, failure_reason=str(exc))
        try:
            payload = self._validate_payload(payload)
        except ValueError as exc:
            return DistillationResult(status="failed", knowledge=None, failure_reason=str(exc))
        knowledge = DistilledKnowledge(
            source_identity=source_identity,
            summary=payload["summary"],
            concepts=payload.get("concepts", []),
            methods=payload.get("methods", []),
            decision_rules=payload.get("decision_rules", []),
            examples=payload.get("examples", []),
            risks=payload.get("risks", []),
            quotes=payload.get("quotes", []),
            source_excerpt_fingerprints=[item.get("text", "") for item in payload.get("quotes", [])],
        )
        return DistillationResult(status="processed", knowledge=knowledge, failure_reason=None)

    async def distill_topic_summary(self, topic_name: str, note_contents: list[str]) -> str:
        """Distill a topic summary from aggregated note contents.

        Uses the summary decision prompt + summary prompt to generate
        a structured topic summary.
        """
        from app.services.knowledge_pipeline.llm_processor import TopicSummaryProcessor
        summary_processor = TopicSummaryProcessor()
        try:
            result = await summary_processor(
                topic_name=topic_name,
                note_contents=note_contents,
            )
            if isinstance(result, dict):
                # The LLM may return the summary directly or as markdown
                return result.get("summary", str(result))
            return str(result)
        except Exception as exc:
            logger.warning(f"[KnowledgeDistiller] topic summary distillation failed: {exc}")
            # Fallback: return a simple concatenation
            return "\n".join(note_contents[:3]) if note_contents else ""
