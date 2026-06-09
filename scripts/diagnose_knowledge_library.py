#!/usr/bin/env python
"""
Diagnose and repair knowledge library graph state.

Usage:
    python scripts/diagnose_knowledge_library.py --dry-run   # Report issues only
    python scripts/diagnose_knowledge_library.py --apply     # Apply fixes
"""
import argparse
import asyncio
import sys
from pathlib import Path

from loguru import logger


async def async_main():
    parser = argparse.ArgumentParser(description="Diagnose and repair knowledge library graph state")
    parser.add_argument("--dry-run", action="store_true", help="Report issues without changing anything")
    parser.add_argument("--apply", action="store_true", help="Apply fixes to the knowledge library")
    parser.add_argument("--vault-root", type=str, default=None, help="Override OBSIDIAN_VAULT_ROOT")

    args = parser.parse_args()

    if not args.dry_run and not args.apply:
        parser.error("Must specify --dry-run or --apply")

    from app.services.knowledge_pipeline.metadata_state import MetadataState
    from app.services.knowledge_pipeline.topic_graph import TopicGraph

    # Resolve vault root
    if args.vault_root:
        vault_root = Path(args.vault_root)
    else:
        from app.config import settings
        vault_root = Path(settings.obsidian_vault_root)

    meta_dir = vault_root / (getattr(settings, 'obsidian_meta_dir', '_meta') if not args.vault_root else '_meta')
    knowledge_dir = vault_root / (getattr(settings, 'obsidian_knowledge_dir', 'knowledge') if not args.vault_root else 'knowledge')

    logger.info(f"[Diagnose] vault_root={vault_root}, meta_dir={meta_dir}, knowledge_dir={knowledge_dir}")

    # Bootstrap metadata state
    state = MetadataState(meta_dir=meta_dir)
    await state.bootstrap()

    issues: list[dict] = []

    # 1. Check for orphan topic files (files in knowledge/ that have no corresponding graph node)
    graph_snapshot = await state.load_topic_graph()
    graph = TopicGraph.from_snapshot(graph_snapshot)
    active_paths = {"/".join(node.path) for node in graph.nodes.values() if node.status == "active"}

    if knowledge_dir.exists():
        for topic_file in knowledge_dir.rglob("*.md"):
            # Skip knowledge notes (files with dates in name) — only check topic pages
            rel = topic_file.relative_to(knowledge_dir)
            # Topic pages are typically under _topics/ or named without dates
            if rel.parts and rel.parts[0] == "_topics":
                topic_name = rel.stem
                # Check if any graph node matches this topic name
                found = any(node.path[-1] == topic_name and node.status == "active" for node in graph.nodes.values())
                if not found:
                    issues.append({
                        "type": "orphan_topic_file",
                        "path": str(topic_file),
                        "description": f"Topic file '{topic_name}' has no active node in graph",
                    })

    # 2. Check for stale source mappings (mapping points to non-existent note file)
    mapping_snapshot = await state.load_source_mapping()
    for item in mapping_snapshot["items"]:
        if item.get("source_processing_status") == "processed" and item.get("knowledge_note_path"):
            note_path = Path(item["knowledge_note_path"])
            if not note_path.exists():
                issues.append({
                    "type": "stale_mapping",
                    "path": item["knowledge_note_path"],
                    "source_inbox_path": item.get("source_inbox_path", ""),
                    "description": f"Mapped note file missing: {item['knowledge_note_path']}",
                })

    # 3. Check for missing note files (mapping has path but file doesn't exist)
    # (covered by stale_mapping check above)

    # 4. Check for pending mutations needing review
    pending_snapshot = await state.load_pending_mutations()
    for item in pending_snapshot["items"]:
        if item.get("lifecycle_status") == "pending":
            issues.append({
                "type": "pending_mutation",
                "proposal_identity": item["proposal_identity"],
                "mutation_type": item.get("proposed_mutation_type", ""),
                "description": f"Pending mutation: {item.get('proposed_mutation_type', 'unknown')} - {item.get('reason', '')}",
            })

    # 5. Check for graph nodes with missing parent references
    for node in graph.nodes.values():
        if node.parent_id and node.parent_id not in graph.nodes:
            issues.append({
                "type": "broken_parent_ref",
                "node_id": node.id,
                "missing_parent_id": node.parent_id,
                "description": f"Node '{node.name}' references missing parent {node.parent_id}",
            })

    # Report
    print("=" * 60)
    print("Knowledge Library Diagnosis Report")
    print("=" * 60)
    print(f"Mode: {'DRY RUN (no changes)' if args.dry_run else 'APPLY (fixing issues)'}")
    print(f"Graph nodes: {len(graph.nodes)} ({len(active_paths)} active)")
    print(f"Source mappings: {len(mapping_snapshot['items'])}")
    print(f"Pending mutations: {len([i for i in pending_snapshot['items'] if i.get('lifecycle_status') == 'pending'])}")
    print(f"Issues found: {len(issues)}")
    print()

    if not issues:
        print("✅ No issues found.")
        return

    for i, issue in enumerate(issues, 1):
        print(f"  {i}. [{issue['type']}] {issue['description']}")

    # Apply fixes
    if args.apply:
        fixed = 0
        async with state.write_lock():
            # Remove stale mappings
            stale_indices = set()
            for idx, item in enumerate(mapping_snapshot["items"]):
                if item.get("source_processing_status") == "processed" and item.get("knowledge_note_path"):
                    if not Path(item["knowledge_note_path"]).exists():
                        stale_indices.add(idx)

            if stale_indices:
                new_items = [item for idx, item in enumerate(mapping_snapshot["items"]) if idx not in stale_indices]
                mapping_snapshot["items"] = new_items
                await state.save_source_mapping(mapping_snapshot)
                fixed += len(stale_indices)
                print(f"\n  Removed {len(stale_indices)} stale mapping(s)")

            # Remove orphan topic files
            orphan_count = 0
            for issue in issues:
                if issue["type"] == "orphan_topic_file":
                    try:
                        Path(issue["path"]).unlink()
                        orphan_count += 1
                    except Exception as exc:
                        logger.warning(f"Failed to remove orphan file {issue['path']}: {exc}")
            if orphan_count:
                fixed += orphan_count
                print(f"  Removed {orphan_count} orphan topic file(s)")

        print(f"\n✅ Fixed {fixed} issue(s).")
    else:
        print("\nRun with --apply to fix these issues.")


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
