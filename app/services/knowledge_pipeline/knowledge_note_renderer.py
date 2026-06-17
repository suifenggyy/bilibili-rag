from .knowledge_distiller import DistilledKnowledge
from .topic_graph import GraphPlacementResult
import yaml
import re
from datetime import datetime, timezone
from pathlib import Path


class KnowledgeNoteRenderer:

    @staticmethod
    def _source_inbox_path_to_obsidian_link(inbox_path: str) -> tuple[str, str]:
        """Convert an absolute inbox path to Obsidian-friendly link parts.

        Two problems with absolute paths in [[wikilink]]:
        1. Obsidian only resolves vault-relative paths, not absolute ones.
        2. Filenames containing '?' or '#' break wikilink parsing
           ('#' is the heading anchor separator, '?' is stripped).

        Returns:
            (vault_relative_path, display_title) tuple.
            - vault_relative_path: vault-relative path with ?/# stripped
            - display_title: readable title for link display
        """
        # Convert to vault-relative path
        vault_relative = inbox_path
        for marker in ("inbox/", "knowledge/", "daily/"):
            idx = inbox_path.find(marker)
            if idx >= 0:
                vault_relative = inbox_path[idx:]
                break

        # Extract readable title (filename without extension)
        filename = Path(vault_relative).stem

        # Sanitize the path for wikilink: remove ? (full+half width) and #
        # These characters break Obsidian's [[wikilink]] parser
        sanitized = vault_relative.replace("?", "").replace("？", "").replace("#", "")
        sanitized = re.sub(r"  +", " ", sanitized)

        # Also sanitize the display title
        display_title = filename.replace("?", "").replace("？", "").replace("#", "")
        display_title = re.sub(r"  +", " ", display_title).strip()

        return sanitized, display_title

    def render(
        self,
        units: DistilledKnowledge,
        knowledge_note_id: str,
        placement: GraphPlacementResult | None = None,
    ) -> str:
        source_id = units.source_identity
        raw_inbox_path = source_id.get("source_inbox_path", "")
        # Store vault-relative path in frontmatter (more useful than absolute)
        vault_relative_path = raw_inbox_path
        for marker in ("inbox/", "knowledge/", "daily/"):
            idx = raw_inbox_path.find(marker)
            if idx >= 0:
                vault_relative_path = raw_inbox_path[idx:]
                break
        frontmatter = {
            "type": "knowledge_note",
            "knowledge_note_id": knowledge_note_id,
            "source_inbox_path": vault_relative_path,
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

        # Build a working Obsidian link for the source file
        sanitized_path, display_title = self._source_inbox_path_to_obsidian_link(raw_inbox_path)
        # Use pipe syntax wikilink: [[path|display title]]
        # If the sanitized filename matches the display, omit the alias
        source_link = f"[[{sanitized_path}|{display_title}]]"

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
- {source_link}
- {source_id.get("source_url", "") or "无外部原始链接"}
"""
