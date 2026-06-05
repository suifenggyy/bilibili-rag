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
