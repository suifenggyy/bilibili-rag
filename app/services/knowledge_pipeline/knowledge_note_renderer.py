from .knowledge_distiller import DistilledKnowledge
import yaml

class KnowledgeNoteRenderer:
    def render(self, units: DistilledKnowledge, knowledge_note_id: str) -> str:
        frontmatter = {
            "type": "knowledge_note",
            "knowledge_note_id": knowledge_note_id,
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

## 核心结论
{units.summary}

## 概念与方法
{concepts_md}
{methods_md}

## 决策与执行
{rules_md}

## 场景与案例
{examples_md}

## 风险与边界
{risks_md}

## 关键摘录
{quotes_md}

## 来源
- [[{units.source_identity.get("source_inbox_path", "")}]]
- {units.source_identity.get("source_url", "") or "无外部原始链接"}
"""
