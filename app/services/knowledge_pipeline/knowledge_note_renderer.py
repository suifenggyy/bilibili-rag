from .knowledge_distiller import DistilledKnowledge
from .topic_graph import GraphPlacementResult
import yaml
from datetime import datetime, timezone


class KnowledgeNoteRenderer:
    def render(
        self,
        units: DistilledKnowledge,
        knowledge_note_id: str,
        placement: GraphPlacementResult | None = None,
    ) -> str:
        source_id = units.source_identity
        frontmatter = {
            "type": "knowledge_note",
            "knowledge_note_id": knowledge_note_id,
            "source_inbox_path": source_id.get("source_inbox_path", ""),
            "source_url": source_id.get("source_url", ""),
            "primary_topic_path": placement.canonical_primary_path if placement else [],
            "secondary_topic_paths": [sp.requested_path for sp in placement.secondary_placements] if placement else [],
            "placement_path": placement.placement_path if placement else [],
            "deferred_primary_path": placement.deferred_primary_path if placement else None,
            "generation_version": "v1",
            "topic_node_ids": {
                "primary": placement.canonical_primary_node_id if placement else None,
                "secondary": placement.secondary_node_ids if placement else [],
                "ancestors": placement.ancestor_node_ids if placement else [],
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "distilled",
        }

        methods_md = "\n".join(f"- {m}" for m in units.methods)
        concepts_md = "\n".join(f"- {c}" for c in units.concepts)
        rules_md = "\n".join(f"- {r}" for r in units.decision_rules)
        examples_md = "\n".join(f"- {e}" for e in units.examples)
        risks_md = "\n".join(f"- {r}" for r in units.risks)
        quotes_md = "\n".join(f"> {q.get('text', '')}" for q in units.quotes)

        return f"""---
{yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False).strip()}
---

## 核心概念
{concepts_md}

## 核心结论
{units.summary}

## 方法 / 框架
{methods_md}

## 判断标准
{rules_md}

## 场景与案例
{examples_md}

## 风险与边界
{risks_md}

## 关键摘录
{quotes_md}

## 来源
- [[{source_id.get("source_inbox_path", "")}]]
- {source_id.get("source_url", "") or "无外部原始链接"}
"""
