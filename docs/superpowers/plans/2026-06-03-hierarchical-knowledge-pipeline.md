# Hierarchical Knowledge Pipeline Implementation Plan

> **Status: ✅ COMPLETE** — All chunks implemented and tests passing (2026-06-09).

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Rebuild the knowledge pipeline so inbox notes generate hierarchical topic graph state, distilled knowledge notes, layered topic pages, and a repair workflow instead of flat copied archives.

**Architecture:** Keep `inbox/` as the immutable fact source, add explicit metadata state under `_meta/`, and split the current monolithic archive/topic flow into focused units: parser normalization, graph state, note distillation, note storage, topic rebuilding, and diagnosis/repair. Execute in phases so the old flat pipeline is replaced only after the new graph-backed path can generate notes, rebuild impacted topics, and repair drift deterministically.

**Tech Stack:** Python 3, asyncio, existing `loguru`, existing YAML parsing helpers, existing `python -m unittest` test suite, Obsidian filesystem/REST writer, `.env` config via `pydantic-settings`

## Implementation Progress

| Chunk | Task | Status | Notes |
|-------|------|--------|-------|
| 1 | Task 1: Config & Parser | ✅ Done | Full prompts, env vars, parser normalization |
| 1 | Task 2: Metadata State | ✅ Done | Full schema validation, file-based lock + asyncio lock, run log, source/detail/pending mutation management |
| 2 | Task 3: Topic Graph | ✅ Done | Full node/edge model, mutation evaluation, rename/move/merge/split/replace, placement finalization |
| 3 | Task 4: Distiller & Resolver | ✅ Done | Distillation with processed/skipped/failed, path resolution with payload validation |
| 4 | Task 5: Note Render & Store | ✅ Done | Full frontmatter, stable note IDs, collision-safe paths, path history |
| 5 | Task 6: Topic Rebuild | ✅ Done | Renderer + rebuilder using real APIs |
| 5 | Task 7: Orchestrator | ✅ Done | Rewired to graph-backed flow |
| 5 | Task 8: Diagnosis & Repair | ✅ Done | Real orphan/stale/broken-parent detection with --dry-run/--apply |

### Known deviations from original plan

1. **MetadataState lock**: Uses a hybrid `asyncio.Lock` + file-based lock (not file-only). This avoids deadlocks within a single-process async event loop while still providing cross-process protection.
2. **KnowledgeNoteRenderer.render()**: Accepts optional `placement` parameter (plan called it `KnowledgeNotePayload`). When `placement` is `None`, frontmatter fields default to empty lists/None.
3. **KnowledgeNoteStore.build_processed_mapping_record**: When `primary_node` is `None` (deferred placement with no matching graph node), status is set to `"skipped"` rather than `"processed"` to satisfy schema validation.
4. **Orchestrator**: Still uses a `DummyProcessor` for the LLM call. Real LLM integration requires wiring the distiller/resolver to actual DashScope/OpenAI calls.
5. **TopicRebuilder**: Uses `_read_note_content()` from filesystem instead of a store method. Falls back to simple listing when LLM distillation fails.
6. **Diagnose script**: Only checks orphan topic files under `_topics/`, stale source mappings, and broken parent references. Does not auto-apply pending mutations or relink notes — those require human review.

---

## File Structure

### Existing files to modify

- `app/config.py`
  - add env-backed prompt settings and any graph/repair thresholds
- `app/services/knowledge_pipeline/parser.py`
  - normalize source summary and `key_points`
- `app/services/knowledge_pipeline/orchestrator.py`
  - replace flat category/archive/topic flow with graph-backed pipeline coordination
- `app/services/knowledge_pipeline/archiver.py`
  - replace raw-copy archive behavior with a thin compatibility wrapper or remove responsibility in favor of knowledge note storage
- `app/services/knowledge_pipeline/topic_updater.py`
  - replace flat append behavior with topic rebuild orchestration or retire it
- `app/services/knowledge_pipeline/category_map.py`
  - retire or migrate to compatibility-only shim while graph state takes over
- `docs/knowledge-pipeline-verification.md`
  - document new pipeline and repair script
- `.env.example`
  - add default prompt/env keys for new LLM stages

### New files to create

- `app/services/knowledge_pipeline/metadata_state.py`
  - own `_meta/source-topic-map.json`, `_meta/topic-detail-index.json`, `_meta/pending-topic-mutations.json`, and run log
- `app/services/knowledge_pipeline/topic_graph.py`
  - own `_meta/topic-graph.json`
- `app/services/knowledge_pipeline/knowledge_distiller.py`
  - convert parsed inbox content into structured knowledge units
- `app/services/knowledge_pipeline/topic_path_resolver.py`
  - produce primary/secondary paths and mutation proposals
- `app/services/knowledge_pipeline/knowledge_note_renderer.py`
  - render distilled note markdown
- `app/services/knowledge_pipeline/knowledge_note_store.py`
  - place/move note files and preserve path history
- `app/services/knowledge_pipeline/topic_page_renderer.py`
  - render summary/detail/source sections for topic pages
- `app/services/knowledge_pipeline/topic_rebuilder.py`
  - rebuild affected topic nodes and mapping deltas
- `scripts/diagnose_knowledge_library.py`
  - dry-run/apply diagnosis and repair entry point

### Tests to create

- `test/test_knowledge_pipeline_parser_v2.py`
- `test/test_metadata_state.py`
- `test/test_topic_graph.py`
- `test/test_knowledge_distiller.py`
- `test/test_topic_path_resolver.py`
- `test/test_knowledge_note_renderer.py`
- `test/test_knowledge_note_store.py`
- `test/test_topic_page_renderer.py`
- `test/test_topic_rebuilder.py`
- `test/test_diagnose_knowledge_library.py`

### Tests to modify

- `test/test_knowledge_pipeline_config.py`
- `test/test_knowledge_pipeline_orchestrator.py`
- `test/test_knowledge_archiver.py`
- `test/test_knowledge_parser.py`

## Chunk 1: Foundations and Metadata State

### Task 1: Expand config and parser contracts

**Files:**
- Modify: `app/config.py`
- Create: `app/services/knowledge_pipeline/prompt_defaults.py`
- Modify: `app/services/knowledge_pipeline/parser.py`
- Modify: `.env.example`
- Modify: `test/test_knowledge_parser.py`
- Test: `test/test_knowledge_pipeline_config.py`
- Test: `test/test_knowledge_pipeline_parser_v2.py`

- [x] **Step 1: Write the failing config test**

```python
class KnowledgePipelineConfigTests(unittest.TestCase):
    def test_settings_expose_hierarchical_prompt_fields(self):
        from app.config import Settings

        s = Settings(_env_file=None)
        self.assertIn("primary_path", s.knowledge_topic_path_prompt)
        self.assertIn("summary", s.knowledge_note_distill_prompt)
        self.assertIn("rewrite_summary", s.knowledge_topic_summary_decision_prompt)
        self.assertIn("## 概览", s.knowledge_topic_summary_prompt)
        self.assertIn("detail_items", s.knowledge_topic_detail_prompt)
        self.assertIn("repair_actions", s.knowledge_repair_prompt)

    def test_settings_allow_env_override_for_hierarchical_prompts(self):
        import os
        from unittest.mock import patch
        from app.config import Settings

        with patch.dict(os.environ, {
            "KNOWLEDGE_TOPIC_PATH_PROMPT": "override-path",
            "KNOWLEDGE_MIN_BODY_CHARS": "42",
        }, clear=False):
            s = Settings(_env_file=None)
        self.assertEqual(s.knowledge_topic_path_prompt, "override-path")
        self.assertEqual(s.knowledge_min_body_chars, 42)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_knowledge_pipeline_config.py' -v`
Expected: FAIL with missing settings attributes for the new prompt fields.

- [x] **Step 3: Write the failing parser tests**

```python
class KnowledgeParserV2Tests(unittest.TestCase):
    def test_parser_extracts_summary_and_key_points(self):
        md = \"\"\"---
title: T
date: 2026-06-03
source: https://x.com
summary: one line
key_points:
  - p1
  - p2
---

正文\"\"\"
        doc = KnowledgeMarkdownParser().parse_text(md)
        self.assertEqual(doc.summary, "one line")
        self.assertEqual(doc.key_points, ["p1", "p2"])


class KnowledgeParserCompatibilityTests(unittest.TestCase):
    def test_parser_keeps_existing_field_contract(self):
        md = \"\"\"---
title: T
date: 2026-06-03
source: https://x.com
summary: one line
key_points:
  - p1
---

正文\"\"\"
        doc = KnowledgeMarkdownParser().parse_text(md)
        self.assertEqual(doc.date_str, "2026-06-03")
        self.assertEqual(doc.source_url, "https://x.com")
        self.assertEqual(doc.body.strip(), "正文")
        self.assertEqual(doc.raw_frontmatter["key_points"], ["p1"])
```

- [x] **Step 4: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_knowledge_pipeline_parser_v2.py' -v && python -m unittest discover -s test -p 'test_knowledge_parser.py' -v`
Expected: FAIL because `ParsedKnowledgeDocument` does not yet expose normalized `key_points` while preserving the current parser field contract.

- [x] **Step 5: Implement minimal config changes**

```python
_knowledge_prompt_defaults_path = os.path.join(
    os.path.dirname(__file__),
    "services",
    "knowledge_pipeline",
    "prompt_defaults.py",
)
_knowledge_prompt_spec = importlib.util.spec_from_file_location(
    "knowledge_prompt_defaults",
    _knowledge_prompt_defaults_path,
)
_knowledge_prompt_mod = importlib.util.module_from_spec(_knowledge_prompt_spec)  # type: ignore[arg-type]
_knowledge_prompt_spec.loader.exec_module(_knowledge_prompt_mod)  # type: ignore[union-attr]

DEFAULT_TOPIC_PATH_PROMPT = _knowledge_prompt_mod.DEFAULT_TOPIC_PATH_PROMPT
DEFAULT_KNOWLEDGE_NOTE_DISTILL_PROMPT = _knowledge_prompt_mod.DEFAULT_KNOWLEDGE_NOTE_DISTILL_PROMPT
DEFAULT_TOPIC_SUMMARY_DECISION_PROMPT = _knowledge_prompt_mod.DEFAULT_TOPIC_SUMMARY_DECISION_PROMPT
DEFAULT_TOPIC_SUMMARY_PROMPT = _knowledge_prompt_mod.DEFAULT_TOPIC_SUMMARY_PROMPT
DEFAULT_TOPIC_DETAIL_PROMPT = _knowledge_prompt_mod.DEFAULT_TOPIC_DETAIL_PROMPT
DEFAULT_KNOWLEDGE_REPAIR_PROMPT = _knowledge_prompt_mod.DEFAULT_KNOWLEDGE_REPAIR_PROMPT

knowledge_topic_path_prompt: str = Field(default=DEFAULT_TOPIC_PATH_PROMPT, env="KNOWLEDGE_TOPIC_PATH_PROMPT")
knowledge_note_distill_prompt: str = Field(default=DEFAULT_KNOWLEDGE_NOTE_DISTILL_PROMPT, env="KNOWLEDGE_NOTE_DISTILL_PROMPT")
knowledge_topic_summary_decision_prompt: str = Field(default=DEFAULT_TOPIC_SUMMARY_DECISION_PROMPT, env="KNOWLEDGE_TOPIC_SUMMARY_DECISION_PROMPT")
knowledge_topic_summary_prompt: str = Field(default=DEFAULT_TOPIC_SUMMARY_PROMPT, env="KNOWLEDGE_TOPIC_SUMMARY_PROMPT")
knowledge_topic_detail_prompt: str = Field(default=DEFAULT_TOPIC_DETAIL_PROMPT, env="KNOWLEDGE_TOPIC_DETAIL_PROMPT")
knowledge_repair_prompt: str = Field(default=DEFAULT_KNOWLEDGE_REPAIR_PROMPT, env="KNOWLEDGE_REPAIR_PROMPT")
knowledge_min_body_chars: int = Field(default=80, env="KNOWLEDGE_MIN_BODY_CHARS")
```

- [x] **Step 6: Create concrete default prompts**

```python
DEFAULT_TOPIC_PATH_PROMPT = \"\"\"你会收到 source metadata、summary、key_points、body、existing topic graph、existing aliases。
任务：选择一个 primary_path，最多三个 secondary_paths，并给出 mutation_proposals。
输出 YAML，且只能包含这些键：
primary_path: ["一级主题", "二级主题"]
secondary_paths:
  - ["一级主题", "二级主题", "三级主题"]
mutation_proposals:
  - type: create_leaf|add_alias|rename|merge|split|move|replace
    confidence: 0.0-1.0
    affected_unresolved_names: ["做T"]
    target_parent_path: ["投资", "短线交易"]
    target_name: "做T"
    affected_node_ids: []
    target_replacement_node_id: "topic-intraday-trading"
    target_paths:
      - ["投资", "短线交易", "做T"]
    reason: "新增知识稳定落在短线交易语义下"
规则：primary_path 必填；secondary_paths 不得重复 primary_path；create_leaf 必须用 `affected_unresolved_names` 表示待创建名称且 `affected_node_ids` 为空；add_alias 必须用 `affected_node_ids` 指向已有 canonical node，并用 `target_name` 表示待添加 alias 文本；不要输出解释性文字。\"\"\"

DEFAULT_KNOWLEDGE_NOTE_DISTILL_PROMPT = \"\"\"你会收到 source summary、key_points、body。
任务：整理成面向知识库的派生知识笔记，而不是复述原文。
输出 YAML，且只能包含这些键：
summary: "2-4句主题化摘要"
concepts:
  - "核心概念或对象"
methods:
  - "方法/框架/判断标准"
decision_rules:
  - "适用条件、判断标准、决策规则"
examples:
  - "可复用案例或反例"
risks:
  - "风险、误区、限制条件"
quotes:
  - text: "保留原句，仅在必须保留语气或细节时输出"
    reason: "为什么需要该摘录"
要求：优先综合 key_points 与正文；quotes 最多 3 条；缺项返回空列表。\"\"\"

DEFAULT_TOPIC_SUMMARY_DECISION_PROMPT = \"\"\"你会收到当前 topic summary、候选新增知识、最近一次 detail 增量。
判断是否需要重写 topic 总结区。
输出 YAML，且只能包含：
rewrite_summary: true|false
changed_facets:
  - "定义|框架|判断标准|风险边界|上下位关系"
reason: "一句话说明"
规则：只有 topic 的核心认知发生变化时才允许 rewrite_summary=true。\"\"\"

DEFAULT_TOPIC_SUMMARY_PROMPT = \"\"\"你会收到某个 topic 的现有总结与所有聚合后的知识要点。
输出 markdown，总结区必须严格包含：
## 概览
## 核心框架
## 关键结论
## 上下位关系
可选补充：
## 适用边界
要求：写 topic 本身的稳定知识，不要写“某篇文章为什么属于这个 topic”。\"\"\"

DEFAULT_TOPIC_DETAIL_PROMPT = \"\"\"你会收到 topic 当前 detail 指纹索引、候选新事实、支持来源。
输出 YAML，且只能包含：
detail_items:
  - statement: "新增且不重复的细节"
    detail_type: example|case|exception|quote|tactic
    supporting_source_note_paths:
      - "inbox/douyin/2026-06-03/example.md"
规则：只输出真正新增的细节；如果全部重复则返回 detail_items: []。\"\"\"

DEFAULT_KNOWLEDGE_REPAIR_PROMPT = \"\"\"你会收到 graph snapshot、mapping snapshot、topic detail index、pending mutations、detected issues。
输出 YAML，且只能包含：
repair_actions:
  - action: rebuild_mapping|rebuild_topic_page|relink_note|remove_stale_topic_file|repair_parent_child|apply_pending_mutation|reject_pending_mutation
    target: "knowledge/投资/短线交易/做T/2026-06-03-example.md"
    pending_mutation_identity: "merge|n1,n2|replacement:n3"
    reason: "映射存在但 topic 页缺失"
manual_review_items:
  - issue: "merge proposal between 做T and 日内回转 remains ambiguous"
    reason: "缺少足够独立来源支撑自动决策"
规则：不要自动决定 merge/split/move 等语义性结构变更；只有输入明确要求 apply/reject 某个 pending mutation 时，才能输出 apply_pending_mutation / reject_pending_mutation，否则进入 manual_review_items。若无动作，返回 repair_actions: [] 和 manual_review_items: []。\"\"\"
```

- [x] **Step 7: Implement minimal parser normalization**

```python
@dataclass
class ParsedKnowledgeDocument:
    title: str
    date_str: str
    source_url: str
    summary: str
    body: str
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)
    key_points: list[str] = field(default_factory=list)


def _normalize_summary(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _normalize_key_points(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = value.splitlines()
    elif isinstance(value, list):
        items = value
    else:
        raise ValueError("key_points must be a list or newline-delimited string")
    return [str(item).strip() for item in items if str(item).strip()]


summary = _normalize_summary(frontmatter.get("summary"))
key_points = _normalize_key_points(frontmatter.get("key_points"))

return ParsedKnowledgeDocument(
    title=str(frontmatter["title"]),
    date_str=str(frontmatter["date"]),
    source_url=str(frontmatter["source"]),
    summary=summary,
    body=body,
    raw_frontmatter=frontmatter,
    key_points=key_points,
)
```

- [x] **Step 8: Update `.env.example` with real env keys**

```env
KNOWLEDGE_TOPIC_PATH_PROMPT='输出 YAML。字段固定为 primary_path:["投资","短线交易"], secondary_paths:[["投资","风险控制"]], mutation_proposals:[{type:"create_leaf",confidence:0.92,affected_unresolved_names:["做T"],target_parent_path:["投资","短线交易"],target_name:"做T",affected_node_ids:[],target_replacement_node_id:null,target_paths:[["投资","短线交易","做T"]],reason:"新增知识稳定落在短线交易语义下"}]。'
KNOWLEDGE_NOTE_DISTILL_PROMPT='输出 YAML。字段固定为 summary:"2-4句主题化摘要", concepts:["核心概念"], methods:["方法/框架/判断标准"], decision_rules:["适用条件或判断规则"], examples:["典型案例"], risks:["风险或误区"], quotes:[{text:"必要摘录",reason:"保留 nuance"}]；综合 key_points 与正文，不要复述原文。'
KNOWLEDGE_TOPIC_SUMMARY_DECISION_PROMPT='输出 YAML。字段固定为 rewrite_summary:true|false, changed_facets:["定义","框架","判断标准","风险边界","上下位关系"], reason:"一句话说明"；仅在 topic 核心认知变化时重写总结区。'
KNOWLEDGE_TOPIC_SUMMARY_PROMPT='输出 markdown，总结区必须包含 ## 概览 / ## 核心框架 / ## 关键结论 / ## 上下位关系，不要写文章归类理由。'
KNOWLEDGE_TOPIC_DETAIL_PROMPT='输出 YAML。字段固定为 detail_items:[{statement:"新增细节",detail_type:"example|case|exception|quote|tactic",supporting_source_note_paths:["inbox/douyin/2026-06-03/example.md"]}]；仅返回新增细节。'
KNOWLEDGE_REPAIR_PROMPT='输出 YAML。字段固定为 repair_actions:[{action:"apply_pending_mutation",target:"knowledge/投资/短线交易/做T/2026-06-03-example.md",pending_mutation_identity:"merge|n1,n2|replacement:n3",reason:"明确要求处理该 pending mutation"}], manual_review_items:[{issue:"merge proposal remains ambiguous",reason:"独立来源不足"}]；未明确要求时禁止自动 apply/reject pending mutation。'
KNOWLEDGE_MIN_BODY_CHARS=80
```

- [x] **Step 9: Run tests to verify they pass**

Run: `python -m unittest discover -s test -p 'test_knowledge_pipeline_config.py' -v && python -m unittest discover -s test -p 'test_knowledge_pipeline_parser_v2.py' -v && python -m unittest discover -s test -p 'test_knowledge_parser.py' -v`
Expected: PASS for default prompt loading, env override behavior, parser normalization, and backward-compatible parser fields used by the current pipeline.

- [x] **Step 10: Commit**

```bash
git add app/config.py app/services/knowledge_pipeline/prompt_defaults.py app/services/knowledge_pipeline/parser.py .env.example test/test_knowledge_pipeline_config.py test/test_knowledge_pipeline_parser_v2.py test/test_knowledge_parser.py
git commit -m "feat: add hierarchical knowledge parser config"
```

### Task 2: Create metadata state manager

**Files:**
- Create: `app/services/knowledge_pipeline/metadata_state.py`
- Test: `test/test_metadata_state.py`

- [x] **Step 1: Write the failing metadata state test**

```python
class MetadataStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_state_round_trips_all_meta_files(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()
            async with state.write_lock():
                await state.save_source_mapping({
                    "items": [{
                        "source_inbox_path": "inbox/a.md",
                        "source_content_fingerprint": "sha256:1",
                        "source_processing_status": "failed",
                        "knowledge_note_id": None,
                        "knowledge_note_path": None,
                        "primary_topic_node_id": None,
                        "secondary_topic_node_ids": [],
                        "ancestor_topic_node_ids": [],
                        "graph_version": "v1",
                        "last_generated_at": None,
                        "persisted_first_seen_inbox_path": "inbox/a.md"
                    }]
                })
                await state.save_topic_detail_index({
                    "items": [{
                        "topic_node_id": "t1",
                        "detail_fingerprint": "fp1",
                        "detail_type": "example",
                        "normalized_semantic_statement": "布林线收口后放量突破",
                        "supporting_source_inbox_paths": ["inbox/a.md"],
                        "last_updated_at": "2026-06-03T18:00:00"
                    }]
                })
                await state.save_pending_mutations({
                    "items": [{
                        "proposal_identity": "merge|n1,n2|replacement:n3",
                        "proposed_mutation_type": "merge",
                        "lifecycle_status": "pending",
                        "affected_node_ids": ["n1", "n2"],
                        "affected_unresolved_names": [],
                        "target_parent_path": ["投资"],
                        "target_name": "短线交易",
                        "target_replacement_node_id": "n3",
                        "target_paths": [["投资", "短线交易"]],
                        "confidence_score": 0.90,
                        "reason": "两个节点语义重复",
                        "supporting_source_note_paths": ["inbox/a.md"],
                        "supporting_source_count": 1,
                        "created_at": "2026-06-03T18:00:00",
                        "resolved_at": None
                    }]
                })
                run_id = await state.append_run_log_start({
                    "run_id": "r1",
                    "run_scope": "single_note",
                    "source_note_paths": ["inbox/a.md"],
                    "status": "started",
                    "files_intended": ["knowledge/a.md"],
                    "files_written": [],
                    "graph_changed": False,
                    "mapping_changed": False,
                    "started_at": "2026-06-03T18:00:00",
                    "completed_at": None
                })
            run_log = await state.load_run_log()
            self.assertTrue((tmp / "_meta" / "source-topic-map.json").exists())
            self.assertTrue(run_id)
            self.assertEqual(run_log["items"][0]["files_intended"], ["knowledge/a.md"])
            self.assertEqual(run_log["items"][0]["graph_changed"], False)

    async def test_metadata_state_rejects_corrupted_snapshot_on_load(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            meta_dir = tmp / "_meta"
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / "topic-detail-index.json").write_text("{bad json", encoding="utf-8")
            state = MetadataState(meta_dir=meta_dir)
            with self.assertRaisesRegex(ValueError, "corrupted metadata file"):
                await state.load_topic_detail_index()

    async def test_metadata_state_rejects_valid_json_with_invalid_detail_type(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            meta_dir = tmp / "_meta"
            meta_dir.mkdir(parents=True, exist_ok=True)
            (meta_dir / "topic-detail-index.json").write_text(json.dumps({
                "items": [{
                    "topic_node_id": "t1",
                    "detail_fingerprint": "fp1",
                    "detail_type": "heuristic",
                    "normalized_semantic_statement": "invalid enum",
                    "supporting_source_inbox_paths": ["inbox/a.md"],
                    "last_updated_at": "2026-06-03T18:00:00"
                }]
            }), encoding="utf-8")
            state = MetadataState(meta_dir=meta_dir)
            with self.assertRaisesRegex(ValueError, "invalid detail_type"):
                await state.load_topic_detail_index()

    async def test_metadata_state_rejects_processed_source_without_note_identity(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()
            async with state.write_lock():
                with self.assertRaisesRegex(ValueError, "processed items require knowledge_note_id"):
                    await state.save_source_mapping({
                        "items": [{
                            "source_inbox_path": "inbox/a.md",
                            "source_content_fingerprint": "sha256:1",
                            "source_processing_status": "processed",
                            "knowledge_note_id": None,
                            "knowledge_note_path": "knowledge/a.md",
                            "primary_topic_node_id": "topic-1",
                            "secondary_topic_node_ids": [],
                            "ancestor_topic_node_ids": [],
                            "graph_version": "v1",
                            "last_generated_at": "2026-06-03T18:00:00",
                            "persisted_first_seen_inbox_path": "inbox/a.md"
                        }]
                    })

    async def test_metadata_state_accepts_tombstoned_source_with_note_identity(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()
            async with state.write_lock():
                await state.save_source_mapping({
                    "items": [{
                        "source_inbox_path": "inbox/a.md",
                        "source_content_fingerprint": "sha256:1",
                        "source_processing_status": "tombstoned",
                        "knowledge_note_id": "note-1",
                        "knowledge_note_path": "knowledge/a.md",
                        "primary_topic_node_id": "topic-1",
                        "secondary_topic_node_ids": [],
                        "ancestor_topic_node_ids": [],
                        "graph_version": "v1",
                        "last_generated_at": "2026-06-03T18:00:00",
                        "persisted_first_seen_inbox_path": "inbox/a.md"
                    }]
                })

    async def test_metadata_state_rejects_unlocked_mutation(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()
            with self.assertRaisesRegex(RuntimeError, "require write_lock"):
                await state.save_topic_detail_index({"items": []})

    async def test_metadata_state_accepts_repair_run_log_record(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()
            async with state.write_lock():
                await state.append_run_log_start({
                    "run_id": "repair-1",
                    "run_scope": "repair",
                    "batch_selector": {"mode": "dry-run", "target_paths": ["knowledge/投资"]},
                    "status": "started",
                    "files_intended": [],
                    "files_written": [],
                    "graph_changed": False,
                    "mapping_changed": False,
                    "started_at": "2026-06-03T18:00:00",
                    "completed_at": None
                })
            run_log = await state.load_run_log()
            self.assertEqual(run_log["items"][0]["run_scope"], "repair")

    async def test_metadata_state_accepts_migration_run_log_record(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()
            async with state.write_lock():
                await state.append_run_log_start({
                    "run_id": "migration-1",
                    "run_scope": "migration",
                    "batch_selector": {"mode": "full-library", "target_paths": ["inbox", "knowledge"]},
                    "status": "started",
                    "files_intended": [],
                    "files_written": [],
                    "graph_changed": False,
                    "mapping_changed": False,
                    "started_at": "2026-06-03T18:00:00",
                    "completed_at": None
                })
            run_log = await state.load_run_log()
            self.assertEqual(run_log["items"][0]["run_scope"], "migration")

    async def test_metadata_state_rejects_invalid_mutation_type_and_confidence(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state = MetadataState(meta_dir=tmp / "_meta")
            await state.bootstrap()
            async with state.write_lock():
                with self.assertRaisesRegex(ValueError, "invalid mutation type"):
                    await state.save_pending_mutations({
                        "items": [{
                            "proposal_identity": "unknown|n1|parent:投资",
                            "proposed_mutation_type": "unknown",
                            "lifecycle_status": "pending",
                            "affected_node_ids": ["n1"],
                            "affected_unresolved_names": [],
                            "target_parent_path": ["投资"],
                            "target_name": "做T",
                            "target_replacement_node_id": None,
                            "target_paths": [],
                            "confidence_score": 1.2,
                            "reason": "bad payload",
                            "supporting_source_note_paths": ["inbox/a.md"],
                            "supporting_source_count": 1,
                            "created_at": "2026-06-03T18:00:00",
                            "resolved_at": None
                        }]
                    })


class MutationIdentityTests(unittest.TestCase):
    def test_build_mutation_identity_distinguishes_new_leaf_names(self):
        self.assertNotEqual(
            build_mutation_identity(
                mutation_type="create_leaf",
                affected_node_ids=[],
                affected_unresolved_names=["做T"],
                target_parent_path=["投资", "短线交易"],
                target_replacement_node_id=None,
            ),
            build_mutation_identity(
                mutation_type="create_leaf",
                affected_node_ids=[],
                affected_unresolved_names=["止损"],
                target_parent_path=["投资", "短线交易"],
                target_replacement_node_id=None,
            ),
        )
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_metadata_state.py' -v`
Expected: FAIL with `ModuleNotFoundError` or missing `MetadataState`.

- [x] **Step 3: Write the failing lock test**

```python
class MetadataStateLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_state_exposes_cross_instance_write_lock(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state_a = MetadataState(meta_dir=tmp / "_meta")
            state_b = MetadataState(meta_dir=tmp / "_meta")
            await state_a.bootstrap()
            async with state_a.write_lock():
                self.assertTrue((tmp / "_meta" / ".write.lock").exists())
                with self.assertRaises(TimeoutError):
                    async with state_b.write_lock(timeout_seconds=0.01):
                        pass

    async def test_metadata_state_records_prelock_contention_failure(self):
        with TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            state_a = MetadataState(meta_dir=tmp / "_meta")
            state_b = MetadataState(meta_dir=tmp / "_meta")
            await state_a.bootstrap()
            async with state_a.write_lock():
                with self.assertRaises(MetadataWriteLockTimeout):
                    async with state_b.transactional_write(
                        run_scope="single_note",
                        context={
                            "source_note_paths": ["inbox/a.md"],
                            "files_intended": [],
                        },
                        timeout_seconds=0.01,
                    ):
                        pass
            run_log = await state_b.load_run_log()
            failed_record = next(
                item for item in run_log["items"]
                if item["failure_reason"] == "lock_contention"
                and item.get("source_note_paths") == ["inbox/a.md"]
            )
            self.assertEqual(failed_record["status"], "failed")
            self.assertEqual(failed_record["failure_reason"], "lock_contention")
            self.assertEqual(failed_record["lock_wait_timeout_seconds"], 0.01)
```

- [x] **Step 4: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_metadata_state.py' -v`
Expected: FAIL because no shared lock context exists.

- [x] **Step 5: Implement minimal state manager**

```python
class MetadataWriteLockTimeout(TimeoutError):
    def __init__(self, timeout_seconds: float):
        super().__init__(f"metadata write lock timed out after {timeout_seconds}s")
        self.timeout_seconds = timeout_seconds


@dataclass
class MetadataState:
    meta_dir: Path
    RUN_LOG_FILE = "pipeline-run-log.json"
    LOCK_FILE = ".write.lock"
    LOCK_RETRY_INTERVAL_SECONDS = 0.05
    _write_lock_fd: int | None = field(default=None, init=False, repr=False)

    async def load_source_mapping(self) -> dict:
        return await self._read_json("source-topic-map.json", default=SOURCE_MAPPING_EMPTY)

    async def save_source_mapping(self, data: dict) -> None:
        self._assert_write_lock_held()
        self.validate_snapshot("source-topic-map.json", data)
        await self._atomic_write_json("source-topic-map.json", data)

    async def load_topic_detail_index(self) -> dict:
        return await self._read_json("topic-detail-index.json", default=TOPIC_DETAIL_EMPTY)

    async def save_topic_detail_index(self, data: dict) -> None:
        self._assert_write_lock_held()
        self.validate_snapshot("topic-detail-index.json", data)
        await self._atomic_write_json("topic-detail-index.json", data)

    async def load_pending_mutations(self) -> dict:
        return await self._read_json("pending-topic-mutations.json", default=PENDING_MUTATION_EMPTY)

    async def save_pending_mutations(self, data: dict) -> None:
        self._assert_write_lock_held()
        self.validate_snapshot("pending-topic-mutations.json", data)
        await self._atomic_write_json("pending-topic-mutations.json", data)

    async def load_run_log(self) -> dict:
        return await self._read_json(self.RUN_LOG_FILE, default=RUN_LOG_EMPTY)

    async def bootstrap(self) -> None:
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        managed_files = {
            "source-topic-map.json",
            "topic-detail-index.json",
            "pending-topic-mutations.json",
            self.RUN_LOG_FILE,
        }
        existing_entries = {path.name for path in self.meta_dir.iterdir() if path.is_file()}
        existing_managed_files = managed_files & existing_entries
        missing_managed_files = managed_files - existing_entries
        if existing_managed_files and missing_managed_files:
            raise ValueError("bootstrap refuses partially missing _meta state; run repair instead")
        for filename in existing_managed_files:
            await self._read_json(filename, default={})
        async with self.write_lock():
            await self._ensure_file("source-topic-map.json", SOURCE_MAPPING_EMPTY)
            await self._ensure_file("topic-detail-index.json", TOPIC_DETAIL_EMPTY)
            await self._ensure_file("pending-topic-mutations.json", PENDING_MUTATION_EMPTY)
            await self._ensure_file(self.RUN_LOG_FILE, RUN_LOG_EMPTY)

    @asynccontextmanager
    async def transactional_write(
        self,
        run_scope: str,
        context: dict,
        timeout_seconds: float = 5.0,
    ):
        try:
            async with self.write_lock(timeout_seconds=timeout_seconds):
                yield
        except MetadataWriteLockTimeout as exc:
            await self._record_lock_contention_after_release(
                run_scope=run_scope,
                context=context,
                timeout_seconds=timeout_seconds,
                lock_error=str(exc),
            )
            raise

    @asynccontextmanager
    async def write_lock(
        self,
        timeout_seconds: float | None = 5.0,
    ):
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        deadline = None if timeout_seconds is None else monotonic() + timeout_seconds
        while True:
            try:
                fd = os.open(self.meta_dir / self.LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                self._write_lock_fd = fd
                break
            except FileExistsError:
                if deadline is not None and monotonic() >= deadline:
                    raise MetadataWriteLockTimeout(timeout_seconds=timeout_seconds)
                await asyncio.sleep(self.LOCK_RETRY_INTERVAL_SECONDS)
        try:
            yield
        finally:
            os.close(fd)
            self._write_lock_fd = None
            (self.meta_dir / self.LOCK_FILE).unlink(missing_ok=True)

```

- [x] **Step 6: Add validation and explicit schema defaults**

```python
SOURCE_MAPPING_EMPTY = {"items": []}
TOPIC_DETAIL_EMPTY = {"items": []}
PENDING_MUTATION_EMPTY = {"items": []}
RUN_LOG_EMPTY = {"items": []}

SOURCE_STATUS_VALUES = {"processed", "skipped", "failed", "tombstoned"}
PENDING_MUTATION_STATUS_VALUES = {"pending", "superseded", "rejected"}
DETAIL_TYPE_VALUES = {"example", "case", "exception", "quote", "tactic"}
RUN_SCOPE_VALUES = {"single_note", "repair", "migration"}
RUN_STATUS_VALUES = {"started", "success", "failed"}
MUTATION_TYPE_VALUES = {"create_leaf", "add_alias", "rename", "merge", "split", "move", "replace"}

def validate_snapshot(self, name: str, data: dict) -> None:
    if not isinstance(data, dict) or "items" not in data or not isinstance(data["items"], list):
        raise ValueError(f"{name} must be a dict with items list")
    if name == "source-topic-map.json":
        required = {
            "source_inbox_path",
            "source_content_fingerprint",
            "source_processing_status",
            "knowledge_note_id",
            "knowledge_note_path",
            "primary_topic_node_id",
            "secondary_topic_node_ids",
            "ancestor_topic_node_ids",
            "graph_version",
            "last_generated_at",
            "persisted_first_seen_inbox_path",
        }
        for item in data["items"]:
            if required - item.keys():
                raise ValueError("source-topic-map.json item missing required keys")
            validate_source_mapping_item(item)
    elif name == "pending-topic-mutations.json":
        required = {
            "proposal_identity",
            "proposed_mutation_type",
            "lifecycle_status",
            "affected_node_ids",
            "affected_unresolved_names",
            "target_parent_path",
            "target_name",
            "target_replacement_node_id",
            "target_paths",
            "confidence_score",
            "reason",
            "supporting_source_note_paths",
            "supporting_source_count",
            "created_at",
            "resolved_at",
        }
        for item in data["items"]:
            if required - item.keys():
                raise ValueError("pending-topic-mutations.json item missing required keys")
            validate_pending_mutation_item(item)
    elif name == "topic-detail-index.json":
        required = {
            "topic_node_id",
            "detail_fingerprint",
            "detail_type",
            "normalized_semantic_statement",
            "supporting_source_inbox_paths",
            "last_updated_at",
        }
        for item in data["items"]:
            if required - item.keys():
                raise ValueError("topic-detail-index.json item missing required keys")
            validate_topic_detail_item(item)
    elif name == "pipeline-run-log.json":
        required = {
            "run_id",
            "run_scope",
            "status",
            "files_intended",
            "files_written",
            "graph_changed",
            "mapping_changed",
            "started_at",
            "completed_at",
        }
        for item in data["items"]:
            if required - item.keys():
                raise ValueError("pipeline-run-log.json item missing required keys")
            validate_run_log_item(item)
```

- [x] **Step 7: Add run-log start/success/failure helpers and lock-contention logging**

```python
async def append_run_log_start(self, record: dict) -> str:
    self._assert_write_lock_held()
    required = {
        "run_id",
        "run_scope",
        "status",
        "files_intended",
        "files_written",
        "graph_changed",
        "mapping_changed",
        "started_at",
        "completed_at",
    }
    if not ("source_note_paths" in record or "batch_selector" in record):
        raise ValueError("run log requires source_note_paths or batch_selector")
    run_log = await self.load_run_log()
    run_log["items"].append(record)
    self.validate_snapshot(self.RUN_LOG_FILE, run_log)
    await self._atomic_write_json(self.RUN_LOG_FILE, run_log)
    return record["run_id"]


async def finalize_run_log(self, run_id: str, status: str, updates: dict) -> None:
    self._assert_write_lock_held()
    run_log = await self.load_run_log()
    for item in run_log["items"]:
        if item["run_id"] == run_id:
            item["status"] = status
            item.update(updates)
            break
    else:
        raise ValueError(f"unknown run_id: {run_id}")
    self.validate_snapshot(self.RUN_LOG_FILE, run_log)
    await self._atomic_write_json(self.RUN_LOG_FILE, run_log)


async def _record_lock_contention_after_release(
    self,
    run_scope: str,
    context: dict,
    timeout_seconds: float,
    lock_error: str,
) -> str:
    failure_record = {
        "run_id": f"lock-failure-{uuid4().hex[:12]}",
        "run_scope": run_scope,
        "status": "failed",
        "files_intended": context.get("files_intended", []),
        "files_written": [],
        "graph_changed": False,
        "mapping_changed": False,
        "started_at": datetime.now(UTC).isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "failure_reason": "lock_contention",
        "lock_wait_timeout_seconds": timeout_seconds,
        "lock_error": lock_error,
    }
    if "source_note_paths" in context:
        failure_record["source_note_paths"] = context["source_note_paths"]
    if "batch_selector" in context:
        failure_record["batch_selector"] = context["batch_selector"]
    async with self.write_lock(timeout_seconds=None):
        run_log = await self.load_run_log()
        run_log["items"].append(failure_record)
        self.validate_snapshot(self.RUN_LOG_FILE, run_log)
        await self._atomic_write_json(self.RUN_LOG_FILE, run_log)
    return failure_record["run_id"]


async def _read_json(self, filename: str, default: dict) -> dict:
    path = self.meta_dir / filename
    if not path.exists():
        raise ValueError(f"missing metadata file: {filename}; run bootstrap() or repair first")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"corrupted metadata file: {filename}") from exc
    self.validate_snapshot(filename, data)
    return data


async def _atomic_write_json(self, filename: str, data: dict) -> None:
    self.meta_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = self.meta_dir / f"{filename}.tmp"
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(self.meta_dir / filename)


async def _ensure_file(self, filename: str, default: dict) -> None:
    path = self.meta_dir / filename
    if not path.exists():
        await self._atomic_write_json(filename, deepcopy(default))


def _assert_write_lock_held(self) -> None:
    if not getattr(self, "_write_lock_fd", None):
        raise RuntimeError("metadata mutations require write_lock()")


def validate_source_mapping_item(item: dict) -> None:
    if not isinstance(item["source_inbox_path"], str) or not item["source_inbox_path"]:
        raise ValueError("invalid source_inbox_path")
    if item["source_processing_status"] not in SOURCE_STATUS_VALUES:
        raise ValueError("invalid source_processing_status")
    if not isinstance(item["secondary_topic_node_ids"], list) or not isinstance(item["ancestor_topic_node_ids"], list):
        raise ValueError("topic node id collections must be lists")
    if item["source_processing_status"] == "processed" and not item["knowledge_note_id"]:
        raise ValueError("processed items require knowledge_note_id")
    if item["source_processing_status"] == "processed" and not item["knowledge_note_path"]:
        raise ValueError("processed items require knowledge_note_path")
    if item["source_processing_status"] == "processed" and not item["primary_topic_node_id"]:
        raise ValueError("processed items require primary_topic_node_id")
    if item["source_processing_status"] == "tombstoned" and not item["knowledge_note_id"]:
        raise ValueError("tombstoned items require knowledge_note_id")
    if item["source_processing_status"] == "tombstoned" and not item["knowledge_note_path"]:
        raise ValueError("tombstoned items require knowledge_note_path")
    if item["source_processing_status"] == "tombstoned" and not item["primary_topic_node_id"]:
        raise ValueError("tombstoned items require primary_topic_node_id")


def validate_pending_mutation_item(item: dict) -> None:
    if item["lifecycle_status"] not in PENDING_MUTATION_STATUS_VALUES:
        raise ValueError("invalid lifecycle_status")
    if item["proposed_mutation_type"] not in MUTATION_TYPE_VALUES:
        raise ValueError("invalid mutation type")
    if not isinstance(item["affected_node_ids"], list) or not isinstance(item["affected_unresolved_names"], list):
        raise ValueError("affected node collections must be lists")
    if bool(item["affected_node_ids"]) == bool(item["affected_unresolved_names"]):
        raise ValueError("pending mutation requires exactly one identity mode")
    if item["proposed_mutation_type"] == "create_leaf" and not item["affected_unresolved_names"]:
        raise ValueError("create_leaf requires affected_unresolved_names")
    if item["proposed_mutation_type"] == "add_alias" and not item["affected_node_ids"]:
        raise ValueError("add_alias requires affected_node_ids")
    if not isinstance(item["target_paths"], list) or not isinstance(item["supporting_source_note_paths"], list):
        raise ValueError("target_paths and supporting_source_note_paths must be lists")
    if not isinstance(item["confidence_score"], (int, float)) or not 0.0 <= item["confidence_score"] <= 1.0:
        raise ValueError("invalid confidence_score")
    if item["supporting_source_count"] != len(set(item["supporting_source_note_paths"])):
        raise ValueError("supporting_source_count must match distinct inbox note paths")
    expected_identity = build_mutation_identity(
        mutation_type=item["proposed_mutation_type"],
        affected_node_ids=item["affected_node_ids"],
        affected_unresolved_names=item["affected_unresolved_names"],
        target_parent_path=item["target_parent_path"],
        target_replacement_node_id=item["target_replacement_node_id"],
    )
    if item["proposal_identity"] != expected_identity:
        raise ValueError("invalid proposal_identity")
    if item["lifecycle_status"] in {"superseded", "rejected"} and not item["resolved_at"]:
        raise ValueError("terminal pending mutation requires resolved_at")


def validate_topic_detail_item(item: dict) -> None:
    if item["detail_type"] not in DETAIL_TYPE_VALUES:
        raise ValueError("invalid detail_type")
    if not isinstance(item["supporting_source_inbox_paths"], list):
        raise ValueError("supporting_source_inbox_paths must be a list")


def validate_run_log_item(item: dict) -> None:
    if "source_note_paths" not in item and "batch_selector" not in item:
        raise ValueError("run log requires source_note_paths or batch_selector")
    if item["run_scope"] not in RUN_SCOPE_VALUES:
        raise ValueError("invalid run_scope")
    if item["status"] not in RUN_STATUS_VALUES:
        raise ValueError("invalid run status")
    if "batch_selector" in item and not isinstance(item["batch_selector"], dict):
        raise ValueError("batch_selector must be an object")
    if not isinstance(item["files_intended"], list) or not isinstance(item["files_written"], list):
        raise ValueError("files_intended/files_written must be lists")
    if item["status"] in {"success", "failed"} and not item["completed_at"]:
        raise ValueError("terminal run-log record requires completed_at")


def build_mutation_identity(
    mutation_type: str,
    affected_node_ids: list[str],
    affected_unresolved_names: list[str],
    target_parent_path: list[str] | None,
    target_replacement_node_id: str | None,
) -> str:
    affected_part = ",".join(sorted(affected_node_ids or affected_unresolved_names))
    if target_replacement_node_id:
        target_part = f"replacement:{target_replacement_node_id}"
    else:
        target_part = f"parent:{'/'.join(target_parent_path or [])}"
    return f"{mutation_type}|{affected_part}|{target_part}"
```

Implementation note: in Chunk 3 orchestration work, the same `MetadataState.write_lock()` must wrap the full transaction described in the spec: knowledge-note file writes, topic-page writes, topic-detail snapshot, pending-mutation snapshot, topic-graph snapshot, source-topic mapping snapshot, and final run-log update. Pre-lock contention failures are also serialized through the same shared lock after the blocking transaction releases it, so every canonical run-log mutation still follows one lock path.

- [x] **Step 8: Run tests to verify they pass**

Run: `python -m unittest discover -s test -p 'test_metadata_state.py' -v`
Expected: PASS with `_meta/source-topic-map.json`, `_meta/topic-detail-index.json`, `_meta/pending-topic-mutations.json`, and `_meta/pipeline-run-log.json` written via same-directory atomic replace (`*.tmp` then `Path.replace()`), unlocked mutations rejected, schema-validated, and pre-lock contention failures appended as terminal failed records in the canonical run-log.

- [x] **Step 9: Commit**

```bash
git add app/services/knowledge_pipeline/metadata_state.py test/test_metadata_state.py
git commit -m "feat: add hierarchical metadata state manager"
```

## Chunk 2: Topic Graph and Mutation Rules

### Task 3: Implement topic graph persistence and mutation rules

**Files:**
- Create: `app/services/knowledge_pipeline/topic_graph.py`
- Modify: `app/services/knowledge_pipeline/metadata_state.py`
- Test: `test/test_topic_graph.py`

- [x] **Step 1: Write the failing graph-node test**

```python
def test_topic_graph_creates_parent_child_nodes(self):
    graph = TopicGraph.empty()
    graph.create_node(
        name="投资",
        parent_path=[],
        aliases=[],
        replacement_target_id=None,
        lineage=[],
        summary_version="s0",
        detail_version="d0",
        status="active",
    )
    graph.apply_new_leaf(parent_path=["投资"], child_name="做T")
    self.assertEqual(graph.get_node_by_path(["投资", "做T"]).parent_id, graph.get_node_by_path(["投资"]).id)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_topic_graph.py' -v`
Expected: FAIL because `TopicGraph` does not exist.

- [x] **Step 3: Write the failing deferred-mutation test**

```python
def test_merge_mutation_is_deferred(self):
    graph = TopicGraph.empty()
    result = graph.evaluate_mutation(MutationProposal(type="merge", proposal_identity="merge|a,b|replacement:c", affected_node_ids=["a", "b"], affected_unresolved_names=[], target_parent_path=[], target_name="", target_replacement_node_id="c", target_paths=[], confidence=0.99, impacted_existing_nodes=2, replaced_canonical_paths=1, reason="overlap"))
    self.assertEqual(result.status, "pending")
```

- [x] **Step 4: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_topic_graph.py' -v`
Expected: FAIL because mutation policy is missing.

- [x] **Step 5: Add failing snapshot-persistence test**

```python
def test_topic_graph_round_trips_full_node_contract(self):
    graph = TopicGraph.empty()
    node = graph.create_node(
        name="做T",
        parent_path=["投资", "短线交易"],
        aliases=["日内回转"],
        replacement_target_id=None,
        lineage=["投资/日内交易/做T"],
        summary_version="sum-v1",
        detail_version="detail-v1",
        status="active",
    )
    snapshot = graph.to_snapshot()
    restored = TopicGraph.from_snapshot(snapshot)
    restored_node = restored.get_node(node.id)
    self.assertEqual(restored_node.path, ["投资", "短线交易", "做T"])
    self.assertEqual(restored_node.lineage, ["投资/日内交易/做T"])
    self.assertEqual(restored_node.summary_version, "sum-v1")
    self.assertEqual(restored_node.detail_version, "detail-v1")
```

- [x] **Step 6: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_topic_graph.py' -v`
Expected: FAIL because topic-graph snapshot persistence does not exist.

- [x] **Step 7: Add failing mutation-threshold test**

```python
def test_add_alias_auto_applies_only_within_thresholds(self):
    graph = TopicGraph.empty()
    proposal = MutationProposal(
        type="add_alias",
        confidence=0.90,
        affected_node_ids=["topic-1"],
        impacted_existing_nodes=1,
        replaced_canonical_paths=0,
    )
    self.assertEqual(graph.evaluate_mutation(proposal).status, "auto_apply")

    risky = MutationProposal(
        type="add_alias",
        confidence=0.90,
        affected_node_ids=["topic-1"],
        impacted_existing_nodes=6,
        replaced_canonical_paths=0,
    )
    self.assertEqual(graph.evaluate_mutation(risky).status, "pending")


def test_create_leaf_only_auto_applies_under_existing_canonical_parent(self):
    graph = TopicGraph.empty()
    graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
    graph.create_node(name="短线交易", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
    safe = MutationProposal(
        type="create_leaf",
        confidence=0.90,
        target_parent_path=["投资", "短线交易"],
        target_name="做T",
        affected_node_ids=[],
        impacted_existing_nodes=1,
        replaced_canonical_paths=0,
    )
    self.assertEqual(graph.evaluate_mutation(safe).status, "auto_apply")

    missing_parent = MutationProposal(
        type="create_leaf",
        confidence=0.95,
        target_parent_path=["投资", "不存在的父节点"],
        target_name="做T",
        affected_node_ids=[],
        impacted_existing_nodes=1,
        replaced_canonical_paths=0,
    )
    self.assertEqual(graph.evaluate_mutation(missing_parent).status, "pending")


def test_finalize_resolution_keeps_non_primary_pending_mutations(self):
    graph = TopicGraph.empty()
    graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
    graph.create_node(name="短线交易", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
    resolution = TopicResolution(
        requested_primary_path=["投资", "短线交易"],
        secondary_paths=[],
        mutation_proposals=[
            MutationProposal(
                type="rename",
                proposal_identity="rename|topic-1|短线做T",
                affected_node_ids=["topic-1"],
                target_paths=[["投资", "短线做T"]],
                confidence=0.92,
                impacted_existing_nodes=2,
                replaced_canonical_paths=1,
                reason="更准确命名",
            )
        ],
        source_identity={"source_inbox_path": "inbox/douyin/a.md"},
    )
    placement = graph.finalize_resolution(resolution)
    self.assertEqual(len(placement.deferred_mutation_records), 1)
```

- [x] **Step 8: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_topic_graph.py' -v`
Expected: FAIL because threshold-based auto-apply gates are not implemented.

- [x] **Step 9: Add failing rename-and-restructure semantics tests**

```python
def test_rename_keeps_node_id_and_backfills_alias_and_lineage(self):
    graph = TopicGraph.empty()
    node = graph.create_node(name="做T", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
    graph.rename_node(node.id, new_name="日内回转")
    renamed = graph.get_node(node.id)
    self.assertEqual(renamed.id, node.id)
    self.assertIn("做T", renamed.aliases)
    self.assertIn("投资/做T", renamed.lineage)
    self.assertEqual(renamed.path, ["投资", "日内回转"])


def test_move_node_rewrites_descendant_paths_and_backfills_lineage(self):
    graph = TopicGraph.empty()
    graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
    graph.create_node(name="短线交易", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
    parent = graph.create_node(name="做T", parent_path=["投资", "短线交易"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
    child = graph.create_node(name="仓位控制", parent_path=["投资", "短线交易", "做T"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
    graph.move_node(parent.id, target_parent_path=["投资"])
    self.assertEqual(graph.get_node(parent.id).path, ["投资", "做T"])
    self.assertIn("投资/短线交易/做T", graph.get_node(parent.id).lineage)
    self.assertEqual(graph.get_node(child.id).path, ["投资", "做T", "仓位控制"])
    self.assertNotIn(parent.id, graph.get_node_by_path(["投资", "短线交易"]).children_ids)
    self.assertIn(parent.id, graph.get_node_by_path(["投资"]).children_ids)


def test_merge_split_and_replace_backfill_lineage_and_replacement_target(self):
    graph = TopicGraph.empty()
    graph.create_node(name="投资", parent_path=[], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
    left = graph.create_node(name="短线交易", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
    right = graph.create_node(name="日内交易", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
    replacement = graph.create_node(name="日内回转", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
    source = graph.create_node(name="旧节点", parent_path=["投资"], aliases=[], replacement_target_id=None, lineage=[], summary_version="s1", detail_version="d1", status="active")
    merge_result = graph.merge_nodes([left.id, right.id], replacement_node_id=replacement.id)
    self.assertIn(replacement.id, merge_result.changed_node_ids)
    self.assertEqual(graph.get_node(left.id).replacement_target_id, replacement.id)
    split_result = graph.split_node(source.id, [["投资", "短线交易策略"], ["投资", "波段交易"]])
    self.assertIn(source.id, split_result.changed_node_ids)
    replace_result = graph.replace_node(left.id, replacement_node_id=replacement.id)
    self.assertEqual(replace_result.changed_node_ids, [left.id, replacement.id])
```

- [x] **Step 10: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_topic_graph.py' -v`
Expected: FAIL because rename and graph-restructure semantics are not implemented.

- [x] **Step 11: Add failing atomic-save test**

```python
async def test_topic_graph_save_is_atomic(self):
    graph = TopicGraph.empty()
    tmp_path = Path(self.temp_dir)
    state = MetadataState(meta_dir=tmp_path / "_meta")
    await state.bootstrap()
    async with state.write_lock():
        await graph.save(state)
    self.assertTrue((tmp_path / "_meta" / "topic-graph.json").exists())
    self.assertFalse((tmp_path / "_meta" / "topic-graph.json.tmp").exists())
```

- [x] **Step 12: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_topic_graph.py' -v`
Expected: FAIL because graph save is not atomic.

- [x] **Step 13: Add failing corrupted-snapshot test**

```python
async def test_topic_graph_load_rejects_corrupted_snapshot(self):
    tmp_path = Path(self.temp_dir)
    state = MetadataState(meta_dir=tmp_path / "_meta")
    await state.bootstrap()
    (tmp_path / "_meta" / "topic-graph.json").write_text('{"version":"topic-graph-v1","nodes":"bad"}', encoding="utf-8")
    with self.assertRaisesRegex(ValueError, "invalid topic graph snapshot"):
        await TopicGraph.empty().load(state)
```

- [x] **Step 14: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_topic_graph.py' -v`
Expected: FAIL because topic-graph snapshot validation does not exist.

- [x] **Step 15: Implement topic graph snapshot dataclasses**

```python
@dataclass
class TopicNode:
    id: str
    name: str
    parent_id: str | None
    children_ids: list[str]
    aliases: list[str]
    path: list[str]
    replacement_target_id: str | None
    lineage: list[str]
    summary_version: str
    detail_version: str
    status: str


@dataclass
class TopicGraphSnapshot:
    version: str
    nodes: list[dict]

```

- [x] **Step 16: Implement topic graph create/query/load/save helpers**

```python

class TopicGraph:
    @classmethod
    def empty(cls) -> "TopicGraph":
        return cls(nodes={}, version="topic-graph-v1")

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "TopicGraph":
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("nodes"), list):
            raise ValueError("invalid topic graph snapshot")
        nodes = {
            item["id"]: TopicNode(**item)
            for item in snapshot["nodes"]
        }
        return cls(nodes=nodes, version=snapshot["version"])

    def to_snapshot(self) -> dict:
        return {
            "version": self.version,
            "nodes": [asdict(node) for node in self.nodes.values()],
        }

    def create_node(self, name: str, parent_path: list[str], aliases: list[str], replacement_target_id: str | None, lineage: list[str], summary_version: str, detail_version: str, status: str) -> TopicNode:
        parent = self.get_node_by_path(parent_path) if parent_path else None
        node = TopicNode(
            id=self._new_node_id(),
            name=name,
            parent_id=parent.id if parent else None,
            children_ids=[],
            aliases=aliases,
            path=[*parent_path, name],
            replacement_target_id=replacement_target_id,
            lineage=lineage,
            summary_version=summary_version,
            detail_version=detail_version,
            status=status,
        )
        self.nodes[node.id] = node
        if parent:
            parent.children_ids.append(node.id)
        return node

    def apply_new_leaf(self, parent_path: list[str], child_name: str) -> GraphApplyResult:
        node = self.create_node(name=child_name, parent_path=parent_path, aliases=[], replacement_target_id=None, lineage=[], summary_version="", detail_version="", status="active")
        parent = self.get_node_by_path(parent_path)
        impacted = [node.id, *([parent.id] if parent else [])]
        return GraphApplyResult(changed_node_ids=[node.id], impacted_node_ids=impacted)

    def get_node(self, node_id: str) -> TopicNode:
        return self.nodes[node_id]

    def get_node_by_path(self, path: list[str]) -> TopicNode | None:
        for node in self.nodes.values():
            if node.path == path and node.status == "active":
                return node
        return None

    def get_path(self, node_id: str | None) -> list[str]:
        return [] if node_id is None else self.get_node(node_id).path

    def relative_suffix(self, node_id: str, ancestor_id: str) -> list[str]:
        node = self.get_node(node_id)
        ancestor = self.get_node(ancestor_id)
        return node.path[len(ancestor.path):]

    def deepest_existing_path(self, path: list[str]) -> list[str]:
        for size in range(len(path), -1, -1):
            candidate = path[:size]
            if not candidate or self.get_node_by_path(candidate):
                return candidate
        return []

    def get_ancestor_ids(self, path: list[str]) -> list[str]:
        ancestors: list[str] = []
        for size in range(1, len(path) + 1):
            node = self.get_node_by_path(path[:size])
            if node:
                ancestors.append(node.id)
        return ancestors

    def get_descendants(self, node_id: str) -> list[TopicNode]:
        descendants: list[TopicNode] = []
        queue = list(self.get_node(node_id).children_ids)
        while queue:
            current = self.get_node(queue.pop(0))
            descendants.append(current)
            queue.extend(current.children_ids)
        return descendants

    async def load(self, metadata_state: MetadataState) -> "TopicGraph":
        snapshot = await metadata_state.load_topic_graph()
        return TopicGraph.from_snapshot(snapshot)

    async def save(self, metadata_state: MetadataState) -> None:
        metadata_state._assert_write_lock_held()
        await metadata_state.save_topic_graph(self.to_snapshot())


async def load_topic_graph(self) -> dict:
    return await self._load_json("topic-graph.json", default={"version": "topic-graph-v1", "nodes": []})


async def save_topic_graph(self, snapshot: dict) -> None:
    self._assert_write_lock_held()
    await self._save_json("topic-graph.json", snapshot)


async def bootstrap(self) -> None:
    await self._ensure_managed_file("topic-graph.json", {"version": "topic-graph-v1", "nodes": []})
```

Ownership note: `topic_graph.py` owns graph schema, validation, and mutation semantics. `metadata_state.py` is only the atomic persistence/locking wrapper used so graph snapshot writes participate in the shared transaction model from the spec.

- [x] **Step 17: Implement mutation threshold evaluation and auto-apply helpers**

```python
def evaluate_mutation(self, proposal: MutationProposal) -> MutationDecision:
    if proposal.type == "create_leaf":
        parent = self.get_node_by_path(proposal.target_parent_path)
        if parent is None or parent.status != "active":
            return MutationDecision(status="pending", impacted_node_ids=proposal.affected_node_ids)
    if (
        proposal.type in {"create_leaf", "add_alias"}
        and proposal.confidence >= 0.85
        and proposal.impacted_existing_nodes <= 5
        and proposal.replaced_canonical_paths <= 1
    ):
        return MutationDecision(status="auto_apply", impacted_node_ids=proposal.affected_node_ids)
    return MutationDecision(status="pending", impacted_node_ids=proposal.affected_node_ids)


@dataclass
class MutationProposal:
    type: str
    proposal_identity: str
    affected_node_ids: list[str]
    affected_unresolved_names: list[str]
    target_parent_path: list[str]
    target_name: str
    target_replacement_node_id: str | None
    target_paths: list[list[str]]
    confidence: float
    impacted_existing_nodes: int
    replaced_canonical_paths: int
    reason: str


@dataclass
class MutationDecision:
    status: str
    impacted_node_ids: list[str]


@dataclass
class GraphApplyResult:
    changed_node_ids: list[str]
    impacted_node_ids: list[str]

```

- [x] **Step 18: Implement explicit rename and move helpers**

```python


def apply_explicit_mutation(self, proposal: MutationProposal) -> GraphApplyResult:
    if proposal.type == "rename":
        self.rename_node(proposal.affected_node_ids[0], proposal.target_name)
        impacted = [*proposal.affected_node_ids, *[item.id for item in self.get_descendants(proposal.affected_node_ids[0])]]
        return GraphApplyResult(changed_node_ids=proposal.affected_node_ids, impacted_node_ids=impacted)
    if proposal.type == "move":
        return self.move_node(proposal.affected_node_ids[0], proposal.target_parent_path)
    if proposal.type in {"merge", "split", "replace"}:
        raise ValueError("handled in later step")
    raise ValueError("proposal requires explicit handling")


def rename_node(self, node_id: str, new_name: str) -> None:
    node = self.get_node(node_id)
    old_path = "/".join(node.path)
    old_name = node.name
    node.name = new_name
    node.aliases.append(old_name)
    node.lineage.append(old_path)
    node.path = [*self.get_path(node.parent_id), new_name]
    for descendant in self.get_descendants(node_id):
        descendant.path = [*node.path, *self.relative_suffix(descendant.id, ancestor_id=node_id)]


def move_node(self, node_id: str, target_parent_path: list[str]) -> GraphApplyResult:
    node = self.get_node(node_id)
    old_path = "/".join(node.path)
    old_parent = self.get_node(node.parent_id) if node.parent_id else None
    new_parent = self.get_node_by_path(target_parent_path)
    node.parent_id = new_parent.id
    if old_parent:
        old_parent.children_ids = [child_id for child_id in old_parent.children_ids if child_id != node_id]
    new_parent.children_ids.append(node_id)
    node.lineage.append(old_path)
    node.path = [*target_parent_path, node.name]
    descendants = self.get_descendants(node_id)
    for descendant in descendants:
        descendant.path = [*node.path, *self.relative_suffix(descendant.id, ancestor_id=node_id)]
    impacted = [node_id, new_parent.id, *([old_parent.id] if old_parent else []), *[item.id for item in descendants]]
    return GraphApplyResult(changed_node_ids=[node_id, *[item.id for item in descendants]], impacted_node_ids=impacted)

```

- [x] **Step 19: Implement explicit merge, split, and replace helpers**

```python

def apply_explicit_mutation(self, proposal: MutationProposal) -> GraphApplyResult:
    if proposal.type == "merge":
        return self.merge_nodes(proposal.affected_node_ids, proposal.target_replacement_node_id)
    if proposal.type == "split":
        return self.split_node(proposal.affected_node_ids[0], proposal.target_paths)
    if proposal.type == "replace":
        return self.replace_node(proposal.affected_node_ids[0], proposal.target_replacement_node_id)
    raise ValueError("proposal requires explicit handling")


def merge_nodes(self, source_node_ids: list[str], replacement_node_id: str) -> GraphApplyResult:
    changed = set(source_node_ids + [replacement_node_id])
    impacted = set(changed)
    for node_id in source_node_ids:
        node = self.get_node(node_id)
        node.status = "merged"
        node.replacement_target_id = replacement_node_id
        node.lineage.append("/".join(node.path))
        impacted.update(desc.id for desc in self.get_descendants(node_id))
    return GraphApplyResult(changed_node_ids=sorted(changed), impacted_node_ids=sorted(impacted))


def split_node(self, source_node_id: str, target_paths: list[list[str]]) -> GraphApplyResult:
    source = self.get_node(source_node_id)
    source.status = "deprecated"
    source.lineage.append("/".join(source.path))
    created = [self.apply_new_leaf(parent_path=path[:-1], child_name=path[-1]).changed_node_ids[0] for path in target_paths]
    impacted = [source_node_id, *created, *[item.id for item in self.get_descendants(source_node_id)]]
    return GraphApplyResult(changed_node_ids=[source_node_id, *created], impacted_node_ids=impacted)


def replace_node(self, source_node_id: str, replacement_node_id: str) -> GraphApplyResult:
    source = self.get_node(source_node_id)
    source.status = "deprecated"
    source.replacement_target_id = replacement_node_id
    source.lineage.append("/".join(source.path))
    impacted = [source_node_id, replacement_node_id, *[item.id for item in self.get_descendants(source_node_id)]]
    return GraphApplyResult(changed_node_ids=[source_node_id, replacement_node_id], impacted_node_ids=impacted)

```

- [x] **Step 20: Implement auto-apply aliases/leaves plus deferred-placement finalization**

```python


def apply_auto_mutation(self, proposal: MutationProposal) -> GraphApplyResult:
    if proposal.type == "create_leaf":
        return self.apply_new_leaf(parent_path=proposal.target_parent_path, child_name=proposal.target_name)
    if proposal.type == "add_alias":
        node = self.get_node(proposal.affected_node_ids[0])
        node.aliases.append(proposal.target_name)
        return GraphApplyResult(changed_node_ids=[node.id], impacted_node_ids=[node.id])
    raise ValueError("proposal is not auto-applicable")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DeferredMutationRecord:
    proposal_identity: str
    proposed_mutation_type: str
    lifecycle_status: str
    affected_node_ids: list[str]
    affected_unresolved_names: list[str]
    target_parent_path: list[str]
    target_name: str
    target_replacement_node_id: str | None
    target_paths: list[list[str]]
    confidence_score: float
    reason: str
    supporting_source_note_paths: list[str]
    supporting_source_count: int
    created_at: str
    resolved_at: str | None


def build_deferred_mutation_record(self, proposal: MutationProposal, source_identity: dict[str, str]) -> DeferredMutationRecord:
    return DeferredMutationRecord(
        proposal_identity=proposal.proposal_identity,
        proposed_mutation_type=proposal.type,
        lifecycle_status="pending",
        affected_node_ids=proposal.affected_node_ids,
        affected_unresolved_names=proposal.affected_unresolved_names,
        target_parent_path=proposal.target_parent_path,
        target_name=proposal.target_name,
        target_replacement_node_id=proposal.target_replacement_node_id,
        target_paths=proposal.target_paths,
        confidence_score=proposal.confidence,
        reason=proposal.reason,
        supporting_source_note_paths=[source_identity["source_inbox_path"]],
        supporting_source_count=1,
        created_at=utc_now_iso(),
        resolved_at=None,
    )


@dataclass
class SecondaryPlacementResult:
    requested_path: list[str]
    canonical_node_id: str | None
    placement_path: list[str]
    placement_mode: str
    deferred_path: list[str] | None


@dataclass
class GraphPlacementResult:
    canonical_primary_path: list[str]
    canonical_primary_node_id: str | None
    placement_path: list[str]
    placement_mode: str
    deferred_primary_path: list[str] | None
    highest_confidence_replacement_path: list[str] | None
    secondary_placements: list[SecondaryPlacementResult]
    secondary_node_ids: list[str]
    ancestor_node_ids: list[str]
    secondary_ancestor_node_ids: list[str]
    deferred_mutation_records: list[DeferredMutationRecord]


def _find_primary_path_proposal(self, primary_path: list[str], proposals: list[MutationProposal]) -> MutationProposal | None:
    for proposal in proposals:
        if primary_path in proposal.target_paths:
            return proposal
    return None


def finalize_additional_path(self, requested_path: list[str], proposals: list[MutationProposal], source_identity: dict[str, str]) -> tuple[SecondaryPlacementResult, list[DeferredMutationRecord]]:
    proposal = self._find_primary_path_proposal(requested_path, proposals)
    if proposal is None:
        node = self.get_node_by_path(requested_path)
        return SecondaryPlacementResult(
            requested_path=requested_path,
            canonical_node_id=node.id,
            placement_path=requested_path,
            placement_mode="canonical",
            deferred_path=None,
        ), []
    decision = self.evaluate_mutation(proposal)
    if decision.status == "auto_apply":
        self.apply_auto_mutation(proposal)
        node = self.get_node_by_path(proposal.target_paths[0])
        return SecondaryPlacementResult(
            requested_path=requested_path,
            canonical_node_id=node.id,
            placement_path=proposal.target_paths[0],
            placement_mode="canonical",
            deferred_path=None,
        ), []
    existing_ancestor = self.deepest_existing_path(proposal.target_paths[0])
    return SecondaryPlacementResult(
        requested_path=requested_path,
        canonical_node_id=self.get_node_by_path(existing_ancestor).id,
        placement_path=existing_ancestor,
        placement_mode="deferred_to_existing_ancestor",
        deferred_path=proposal.target_paths[0],
    ), [self.build_deferred_mutation_record(proposal, source_identity)]


def finalize_resolution(self, resolution: TopicResolution) -> GraphPlacementResult:
    deferred_records: list[DeferredMutationRecord] = []
    primary_path = resolution.requested_primary_path
    primary_proposal = self._find_primary_path_proposal(primary_path, resolution.mutation_proposals)
    for proposal in resolution.mutation_proposals:
        decision = self.evaluate_mutation(proposal)
        if decision.status == "auto_apply":
            self.apply_auto_mutation(proposal)
            if proposal is primary_proposal:
                primary_path = proposal.target_paths[0]
            continue
        if proposal is not primary_proposal:
            deferred_records.append(self.build_deferred_mutation_record(proposal, resolution.source_identity))
    secondary_placements: list[SecondaryPlacementResult] = []
    for path in resolution.secondary_paths:
        placement, secondary_records = self.finalize_additional_path(path, resolution.mutation_proposals, resolution.source_identity)
        secondary_placements.append(placement)
        deferred_records.extend(secondary_records)
    if primary_proposal is None or primary_proposal not in resolution.mutation_proposals:
        return GraphPlacementResult(
            canonical_primary_path=primary_path,
            canonical_primary_node_id=self.get_node_by_path(primary_path).id,
            placement_path=primary_path,
            placement_mode="canonical",
            deferred_primary_path=None,
            highest_confidence_replacement_path=None,
            secondary_placements=secondary_placements,
            secondary_node_ids=[item.canonical_node_id for item in secondary_placements],
            ancestor_node_ids=self.get_ancestor_ids(primary_path),
            secondary_ancestor_node_ids=sorted({ancestor for item in secondary_placements for ancestor in self.get_ancestor_ids(item.placement_path)}),
            deferred_mutation_records=deferred_records,
        )
    if all(record.proposal_identity != primary_proposal.proposal_identity for record in deferred_records):
        return GraphPlacementResult(
            canonical_primary_path=primary_path,
            canonical_primary_node_id=self.get_node_by_path(primary_path).id,
            placement_path=primary_path,
            placement_mode="canonical",
            deferred_primary_path=None,
            highest_confidence_replacement_path=None,
            secondary_placements=secondary_placements,
            secondary_node_ids=[item.canonical_node_id for item in secondary_placements],
            ancestor_node_ids=self.get_ancestor_ids(primary_path),
            secondary_ancestor_node_ids=sorted({ancestor for item in secondary_placements for ancestor in self.get_ancestor_ids(item.placement_path)}),
            deferred_mutation_records=deferred_records,
        )
    existing_ancestor = self.deepest_existing_path(primary_proposal.target_paths[0])
    return GraphPlacementResult(
        canonical_primary_path=existing_ancestor,
        canonical_primary_node_id=self.get_node_by_path(existing_ancestor).id,
        placement_path=existing_ancestor,
        placement_mode="deferred_to_existing_ancestor",
        deferred_primary_path=primary_proposal.target_paths[0],
        highest_confidence_replacement_path=None,
        secondary_placements=secondary_placements,
        secondary_node_ids=[item.canonical_node_id for item in secondary_placements],
        ancestor_node_ids=self.get_ancestor_ids(existing_ancestor),
        secondary_ancestor_node_ids=sorted({ancestor for item in secondary_placements for ancestor in self.get_ancestor_ids(item.placement_path)}),
        deferred_mutation_records=deferred_records,
    )
```

Caller contract: `finalize_resolution()` emits raw deferred-mutation candidates only. Chunk 3 Task 4 Step 19 reconciles them against existing pending records via `proposal_identity`, distinct inbox paths, and lifecycle transitions before anything is persisted.

- [x] **Step 21: Run tests to verify they pass**

Run: `python -m unittest discover -s test -p 'test_topic_graph.py' -v`
Expected: PASS for leaf creation, full node snapshot persistence, rename semantics, mutation threshold gates, committed/deferred placement finalization, and lineage/version tracking in `_meta/topic-graph.json`.

- [x] **Step 22: Commit**

```bash
git add app/services/knowledge_pipeline/topic_graph.py app/services/knowledge_pipeline/metadata_state.py test/test_topic_graph.py
git commit -m "feat: add topic graph state and mutation rules"
```

## Chunk 3: Distillation and Path Resolution

### Task 4: Distill source notes and resolve hierarchical topic paths

**Files:**
- Create: `app/services/knowledge_pipeline/knowledge_distiller.py`
- Create: `app/services/knowledge_pipeline/topic_path_resolver.py`
- Modify: `app/services/knowledge_pipeline/metadata_state.py`
- Test: `test/test_knowledge_distiller.py`
- Test: `test/test_topic_path_resolver.py`

- [x] **Step 1: Write the failing distiller test**

```python
async def test_distiller_returns_structured_units(self):
    source_identity = {"source_inbox_path": "inbox/douyin/a.md", "published_date": "2026-06-03"}
    result = await KnowledgeDistiller(fake_processor).distill(doc, source_identity)
    self.assertEqual(result.methods[0], "四象限筛选")
    self.assertIn("风险控制", result.risks)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_knowledge_distiller.py' -v`
Expected: FAIL because the distiller module does not exist.

- [x] **Step 3: Write the failing path resolver test**

```python
async def test_resolver_returns_primary_and_secondary_paths(self):
    result = await TopicPathResolver(fake_processor).resolve(units, graph)
    self.assertEqual(result.requested_primary_path, ["投资", "股票交易", "做T"])
    self.assertIn(["投资", "风险控制"], result.secondary_paths)
```

- [x] **Step 4: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_topic_path_resolver.py' -v`
Expected: FAIL because no resolver contract exists.

- [x] **Step 5: Add failing deferred-placement test**

```python
async def test_graph_finalizes_deferred_primary_path_to_nearest_existing_canonical_ancestor(self):
    graph = TopicGraph.from_snapshot(existing_graph_snapshot)
    resolution = await TopicPathResolver(fake_processor).resolve(units, graph)
    placement = graph.finalize_resolution(resolution)
    self.assertEqual(placement.canonical_primary_path, ["投资", "短线交易"])
    self.assertEqual(placement.placement_path, ["投资", "短线交易"])
    self.assertEqual(placement.deferred_primary_path, ["投资", "短线交易", "做T"])
    self.assertEqual(placement.placement_mode, "deferred_to_existing_ancestor")
```

- [x] **Step 6: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_topic_path_resolver.py' -v`
Expected: FAIL because deferred-path fallback is not implemented.

- [x] **Step 7: Add failing malformed-output test**

```python
async def test_resolver_rejects_invalid_llm_payload(self):
    fake_processor.return_value = {
        "primary_path": "not-a-list",
        "secondary_paths": [],
        "mutation_proposals": [{"type": "create_leaf", "confidence": "high"}],
    }
    with self.assertRaisesRegex(ValueError, "invalid topic path payload"):
        await TopicPathResolver(fake_processor).resolve(units, graph)
```

- [x] **Step 8: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_topic_path_resolver.py' -v`
Expected: FAIL because malformed structured LLM output is not validated.

- [x] **Step 9: Add failing eligibility-status test**

```python
async def test_distiller_marks_weak_signal_as_skipped(self):
    doc = ParsedKnowledgeDocument(title="T", date_str="2026-06-03", source_url="https://x.com", summary="", body="太短", raw_frontmatter={}, key_points=[])
    source_identity = {"source_inbox_path": "inbox/douyin/a.md", "published_date": "2026-06-03"}
    result = await KnowledgeDistiller(fake_processor, min_body_chars=80).distill(doc, source_identity)
    self.assertEqual(result.status, "skipped")


async def test_distiller_uses_parser_summary_when_body_is_short(self):
    doc = ParsedKnowledgeDocument(title="T", date_str="2026-06-03", source_url="https://x.com", summary="这是一条有效摘要", body="短", raw_frontmatter={}, key_points=[])
    source_identity = {"source_inbox_path": "inbox/douyin/a.md", "published_date": "2026-06-03"}
    result = await KnowledgeDistiller(fake_processor, min_body_chars=80).distill(doc, source_identity)
    self.assertEqual(result.status, "processed")


async def test_distiller_marks_processor_failure_as_failed(self):
    fake_processor.side_effect = RuntimeError("model timeout")
    source_identity = {"source_inbox_path": "inbox/douyin/a.md", "published_date": "2026-06-03"}
    result = await KnowledgeDistiller(fake_processor, min_body_chars=80).distill(doc, source_identity)
    self.assertEqual(result.status, "failed")
```

- [x] **Step 10: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_knowledge_distiller.py' -v`
Expected: FAIL because distillation status handling does not exist.

- [x] **Step 11: Expand failing distiller assertion for full output**

```python
async def test_distiller_returns_structured_units(self):
    source_identity = {"source_inbox_path": "inbox/douyin/a.md", "published_date": "2026-06-03"}
    result = await KnowledgeDistiller(fake_processor, min_body_chars=80).distill(doc, source_identity)
    self.assertEqual(result.status, "processed")
    self.assertEqual(result.knowledge.summary, "副业筛选应先看时间/专业度匹配")
    self.assertEqual(result.knowledge.methods[0], "四象限筛选")
    self.assertIn("风险控制", result.knowledge.risks)
    self.assertIn("真实案例", result.knowledge.examples)
    self.assertEqual(result.knowledge.quotes[0]["text"], "不要一开始就追求三角全占")
```

- [x] **Step 12: Implement distillation result types and weak-signal skip**

```python
class DistillationResult:
    status: str  # processed | skipped | failed
    knowledge: DistilledKnowledge | None
    failure_reason: str | None


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

```

- [x] **Step 13: Implement processor failure handling and distillation payload validation**

```python


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


def _validate_payload(self, payload: dict) -> dict:
    required_list_fields = ["concepts", "methods", "decision_rules", "examples", "risks", "quotes"]
    if not isinstance(payload.get("summary"), str) or not payload["summary"].strip():
        raise ValueError("invalid distillation payload")
    for field in required_list_fields:
        if not isinstance(payload.get(field, []), list):
            raise ValueError("invalid distillation payload")
    return payload
```

Caller contract: when `DistillationResult.status` is `skipped` or `failed`, the orchestration path in later tasks must call `metadata_state.upsert_source_mapping(...)` with that status and null note/topic fields instead of invoking note rendering.

Source identity contract: orchestration obtains `source_identity` from `MetadataState.get_or_create_source_identity(...)` before calling `distill(...)`; the distiller remains pure and only consumes that input.

- [x] **Step 14: Implement resolver shell and topic-resolution contract**

```python
class TopicResolution:
    requested_primary_path: list[str]
    secondary_paths: list[list[str]]
    mutation_proposals: list[MutationProposal]
    source_identity: dict[str, str]

async def resolve(self, units: DistilledKnowledge, graph: TopicGraph) -> TopicResolution:
    payload = self._validate_payload(await self.processor(units=units, graph_snapshot=graph.to_snapshot()))
    mutation_proposals = self._normalize_proposals(
        payload.get("mutation_proposals", []),
        graph,
        payload["primary_path"],
        payload.get("_secondary_paths_requiring_canonical_check", []),
    )
    primary_candidate = payload["primary_path"]
    secondary_paths = payload.get("secondary_paths", [])
    return TopicResolution(
        requested_primary_path=primary_candidate,
        secondary_paths=secondary_paths,
        mutation_proposals=mutation_proposals,
        source_identity=units.source_identity,
    )

```

- [x] **Step 15: Implement resolver payload validation for primary and secondary paths**

```python


def _validate_payload(self, payload: dict) -> dict:
    primary_path = payload.get("primary_path")
    secondary_paths = payload.get("secondary_paths", [])
    if not isinstance(primary_path, list) or not all(isinstance(item, str) and item.strip() for item in primary_path):
        raise ValueError("invalid topic path payload")
    if not isinstance(secondary_paths, list) or any(not isinstance(path, list) for path in secondary_paths):
        raise ValueError("invalid topic path payload")
    normalized_secondary = []
    seen = set()
    for path in secondary_paths:
        if not all(isinstance(item, str) and item.strip() for item in path):
            raise ValueError("invalid topic path payload")
        key = tuple(path)
        if key != tuple(primary_path) and key not in seen:
            normalized_secondary.append(path)
            seen.add(key)
    proposed_target_paths = {
        tuple(target_path)
        for proposal in payload.get("mutation_proposals", [])
        for target_path in proposal.get("target_paths", [])
    }
    for path in normalized_secondary:
        if tuple(path) not in proposed_target_paths:
            payload.setdefault("_secondary_paths_requiring_canonical_check", []).append(path)
    payload["secondary_paths"] = normalized_secondary
    return payload

```

- [x] **Step 16: Implement proposal normalization and threshold-input estimation**

```python


def _normalize_proposals(self, proposals: list[dict], graph: TopicGraph, primary_path: list[str], secondary_paths_requiring_canonical_check: list[list[str]]) -> list[MutationProposal]:
    normalized: list[MutationProposal] = []
    for item in proposals:
        proposal_type = item["type"]
        if proposal_type not in {"create_leaf", "add_alias", "rename", "merge", "split", "move", "replace"}:
            raise ValueError("invalid topic path payload")
        if not isinstance(item.get("confidence"), (int, float)) or not (0.0 <= float(item["confidence"]) <= 1.0):
            raise ValueError("invalid topic path payload")
        if proposal_type == "create_leaf":
            if not item.get("target_parent_path") or not item.get("target_name") or item.get("affected_node_ids"):
                raise ValueError("invalid topic path payload")
            item["affected_unresolved_names"] = item.get("affected_unresolved_names") or [item["target_name"]]
            item["target_paths"] = item.get("target_paths") or [[*item["target_parent_path"], item["target_name"]]]
        else:
            if not item.get("affected_node_ids") or any(graph.get_node(node_id) is None for node_id in item["affected_node_ids"]):
                raise ValueError("invalid topic path payload")
            if proposal_type in {"merge", "replace"} and not item.get("target_replacement_node_id"):
                raise ValueError("invalid topic path payload")
            if proposal_type == "move" and not item.get("target_parent_path"):
                raise ValueError("invalid topic path payload")
            if proposal_type == "rename" and not item.get("target_name"):
                raise ValueError("invalid topic path payload")
            if proposal_type == "split" and not item.get("target_paths"):
                raise ValueError("invalid topic path payload")
        if proposal_type == "add_alias" and len(item["affected_node_ids"]) != 1:
            raise ValueError("invalid topic path payload")
        item["impacted_existing_nodes"] = item.get("impacted_existing_nodes", self._estimate_impacted_existing_nodes(item, graph))
        item["replaced_canonical_paths"] = item.get("replaced_canonical_paths", self._estimate_replaced_canonical_paths(item))
        item["proposal_identity"] = item.get("proposal_identity") or build_mutation_identity(
            mutation_type=item["type"],
            affected_node_ids=item.get("affected_node_ids", []),
            affected_unresolved_names=item.get("affected_unresolved_names", []),
            target_parent_path=item.get("target_parent_path"),
            target_replacement_node_id=item.get("target_replacement_node_id"),
        )
        normalized.append(MutationProposal(**item))
    if graph.get_node_by_path(primary_path) is None and not any(primary_path in proposal.target_paths for proposal in normalized):
        raise ValueError("invalid topic path payload")
    for path in secondary_paths_requiring_canonical_check:
        if graph.get_node_by_path(path) is None and not any(path in proposal.target_paths for proposal in normalized):
            raise ValueError("invalid topic path payload")
    return normalized


def _estimate_impacted_existing_nodes(self, item: dict, graph: TopicGraph) -> int:
    affected = set(item.get("affected_node_ids", []))
    for node_id in item.get("affected_node_ids", []):
        affected.update(desc.id for desc in graph.get_descendants(node_id))
    return max(1, len(affected))


def _estimate_replaced_canonical_paths(self, item: dict) -> int:
    if item["type"] in {"merge", "replace"}:
        return 1
    return 0
```

- [x] **Step 17: Add failing pending-mutation merge test**

```python
async def test_metadata_state_merges_equivalent_pending_mutations_by_identity(self):
    state = MetadataState(meta_dir=tmp / "_meta")
    await state.bootstrap()
    async with state.write_lock():
        await state.merge_pending_mutations([
            first_record_for("merge|n1,n2|replacement:n3", "inbox/a.md"),
            second_record_for("merge|n1,n2|replacement:n3", "inbox/b.md"),
        ])
    snapshot = await state.load_pending_mutations()
    self.assertEqual(snapshot["items"][0]["supporting_source_count"], 2)


async def test_finalize_resolution_records_are_persisted_into_pending_mutations(self):
    state = MetadataState(meta_dir=tmp / "_meta")
    await state.bootstrap()
    resolution = await TopicPathResolver(fake_processor).resolve(units, graph)
    placement = graph.finalize_resolution(resolution)
    async with state.write_lock():
        await state.merge_pending_mutations([asdict(item) for item in placement.deferred_mutation_records])
    snapshot = await state.load_pending_mutations()
    self.assertEqual(snapshot["items"][0]["proposal_identity"], placement.deferred_mutation_records[0].proposal_identity)
```

- [x] **Step 18: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_topic_path_resolver.py' -v && python -m unittest discover -s test -p 'test_metadata_state.py' -v`
Expected: FAIL because pending mutations are not yet merged by proposal identity or wired from finalized placement records.

- [x] **Step 19: Implement metadata-state source-identity and deferred-mutation helpers**

```python
async def merge_pending_mutations(self, records: list[dict]) -> None:
    self._assert_write_lock_held()
    snapshot = await self.load_pending_mutations()
    by_identity = {item["proposal_identity"]: item for item in snapshot["items"]}
    for record in records:
        current = by_identity.get(record["proposal_identity"])
        if current:
            current["supporting_source_note_paths"] = sorted(set(current["supporting_source_note_paths"]) | set(record["supporting_source_note_paths"]))
            current["supporting_source_count"] = len(current["supporting_source_note_paths"])
            current["confidence_score"] = max(current["confidence_score"], record["confidence_score"])
        else:
            by_identity[record["proposal_identity"]] = record
    await self.save_pending_mutations({"items": list(by_identity.values())})


async def reconcile_pending_mutations(self, records: list[dict]) -> None:
    self._assert_write_lock_held()
    snapshot = await self.load_pending_mutations()
    by_identity = {item["proposal_identity"]: item for item in snapshot["items"]}
    incoming_identities = {record["proposal_identity"] for record in records}
    for item in by_identity.values():
        if item["proposal_identity"] not in incoming_identities and item["lifecycle_status"] == "pending":
            item["lifecycle_status"] = "superseded"
    for record in records:
        current = by_identity.get(record["proposal_identity"])
        if current and current["lifecycle_status"] in {"rejected", "superseded"}:
            current["lifecycle_status"] = "pending"
            current["resolved_at"] = None
    await self.save_pending_mutations({"items": list(by_identity.values())})


async def get_or_create_source_identity(self, source_inbox_path: str, source_fingerprint: str, doc: ParsedKnowledgeDocument) -> dict[str, str]:
    existing = await self.find_source_mapping_by_path(source_inbox_path)
    if existing is None:
        existing = await self.find_source_mapping_by_fingerprint(source_fingerprint)
    persisted_first_seen = existing["persisted_first_seen_inbox_path"] if existing else source_inbox_path
    return {
        "source_inbox_path": source_inbox_path,
        "persisted_first_seen_inbox_path": persisted_first_seen,
        "source_url": doc.source_url or "",
        "published_date": doc.date_str or "",
        "title": doc.title,
    }


async def find_source_mapping_by_path(self, source_inbox_path: str) -> dict | None:
    snapshot = await self.load_source_mapping()
    for item in snapshot["items"]:
        if item["source_inbox_path"] == source_inbox_path:
            return item
    return None


async def find_source_mapping_by_fingerprint(self, source_fingerprint: str) -> dict | None:
    snapshot = await self.load_source_mapping()
    for item in snapshot["items"]:
        if item["source_content_fingerprint"] == source_fingerprint:
            return item
    return None


async def upsert_source_mapping(self, record: dict) -> None:
    self._assert_write_lock_held()
    snapshot = await self.load_source_mapping()
    items = {item["persisted_first_seen_inbox_path"]: item for item in snapshot["items"]}
    if record.get("knowledge_note_id"):
        items = {
            key: item
            for key, item in items.items()
            if item.get("knowledge_note_id") != record["knowledge_note_id"] or key == record["persisted_first_seen_inbox_path"]
        }
    items[record["persisted_first_seen_inbox_path"]] = record
    await self.save_source_mapping({"items": list(items.values())})
```

- [x] **Step 20: Run tests to verify they pass**

Run: `python -m unittest discover -s test -p 'test_knowledge_distiller.py' -v && python -m unittest discover -s test -p 'test_topic_path_resolver.py' -v && python -m unittest discover -s test -p 'test_metadata_state.py' -v`
Expected: PASS with distillation producing `processed/skipped/failed` outcomes, resolver returning proposal-oriented `TopicResolution`, `TopicGraph.finalize_resolution()` producing canonical/deferred placement contracts, and deferred proposals merged into `pending-topic-mutations.json` by `proposal_identity` from finalized placement records.

- [x] **Step 21: Commit**

```bash
git add app/services/knowledge_pipeline/knowledge_distiller.py app/services/knowledge_pipeline/topic_path_resolver.py app/services/knowledge_pipeline/metadata_state.py test/test_knowledge_distiller.py test/test_topic_path_resolver.py test/test_metadata_state.py
git commit -m "feat: add knowledge distillation and topic path resolution"
```

## Chunk 4: Knowledge Note Rendering and Storage

### Task 5: Render and place distilled knowledge notes

**Files:**
- Create: `app/services/knowledge_pipeline/knowledge_note_identity.py`
- Create: `app/services/knowledge_pipeline/knowledge_note_renderer.py`
- Create: `app/services/knowledge_pipeline/knowledge_note_store.py`
- Modify: `app/services/knowledge_pipeline/metadata_state.py`
- Test: `test/test_knowledge_note_renderer.py`
- Test: `test/test_knowledge_note_store.py`
- Test: `test/test_metadata_state.py`

- [x] **Step 1: Write the failing renderer test**

```python
def test_renderer_excludes_full_source_body(self):
    markdown = KnowledgeNoteRenderer().render(note_input)
    self.assertIn("## 核心结论", markdown)
    self.assertNotIn("原始正文整段", markdown)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_knowledge_note_renderer.py' -v`
Expected: FAIL because no renderer exists.

- [x] **Step 3: Write the failing note-store move test**

```python
async def test_store_moves_note_when_primary_path_changes(self):
    result, processed_mapping = await store.write_note(
        knowledge_note_id="n1",
        mapping_record=existing_mapping,
        source_mapping_seed=existing_mapping,
        placement=placement_result,
        note_metadata=KnowledgeNoteFileMetadata(title="做T", published_date="2026-06-03"),
        rendered_markdown="# x",
    )
    self.assertTrue(result.final_path.name.endswith(".md"))
    self.assertEqual(processed_mapping["source_processing_status"], "processed")
    self.assertIn(existing_mapping["knowledge_note_path"], processed_mapping["prior_knowledge_note_paths"])


def test_store_selects_primary_path_by_stability_priority(self):
    self.assertEqual(
        store.choose_storage_primary_path(mapping_record_with_active_primary, placement_result),
        ["投资", "短线交易"],
    )
    self.assertEqual(
        store.choose_storage_primary_path(mapping_record_with_missing_primary_and_surviving_ancestor, placement_result),
        ["投资"],
    )
    self.assertEqual(
        store.choose_storage_primary_path(mapping_record_with_merge_target, placement_result),
        ["投资", "日内回转"],
    )
    self.assertEqual(
        store.choose_storage_primary_path(mapping_record_without_survivor_or_merge_target, replacement_placement_result),
        replacement_placement_result.canonical_primary_path,
    )
```

- [x] **Step 4: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_knowledge_note_store.py' -v`
Expected: FAIL because no placement/move helper exists.

- [x] **Step 5: Add failing note-id/collision test**

```python
async def test_store_generates_stable_note_id_and_collision_safe_path(self):
    note_id = build_knowledge_note_id({
        "source_url": "https://example.com/a",
        "published_date": "2026-06-03",
        "persisted_first_seen_inbox_path": "inbox/douyin/a.md",
        "title": "做T",
    })
    result, processed_mapping = await store.write_note(
        knowledge_note_id=note_id,
        mapping_record=None,
        source_mapping_seed={"source_inbox_path": "inbox/douyin/a.md", "source_content_fingerprint": "fp-1", "persisted_first_seen_inbox_path": "inbox/douyin/a.md"},
        placement=placement_result,
        note_metadata=KnowledgeNoteFileMetadata(title="做T", published_date="2026-06-03"),
        rendered_markdown="# x",
    )
    self.assertTrue(result.note_id)
    self.assertFalse(result.final_path.name.endswith(f"-{result.note_id[:8]}.md"))
    self.assertIn(placement_result.secondary_ancestor_node_ids[0], processed_mapping["ancestor_topic_node_ids"])


async def test_store_rewrites_same_note_id_without_duplicate_suffix(self):
    metadata = KnowledgeNoteFileMetadata(title="做T", published_date="2026-06-03")
    seed = {"source_inbox_path": "inbox/douyin/a.md", "source_content_fingerprint": "fp-1", "persisted_first_seen_inbox_path": "inbox/douyin/a.md"}
    first, _ = await store.write_note(knowledge_note_id="note-1", mapping_record=None, source_mapping_seed=seed, placement=placement_result, note_metadata=metadata, rendered_markdown="# v1")
    second, _ = await store.write_note(knowledge_note_id="note-1", mapping_record={"knowledge_note_id": first.note_id, "knowledge_note_path": str(first.final_path), **seed}, source_mapping_seed=seed, placement=placement_result, note_metadata=metadata, rendered_markdown="# v2")
    self.assertEqual(first.final_path, second.final_path)
```

- [x] **Step 6: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_knowledge_note_store.py' -v`
Expected: FAIL because stable `knowledge_note_id` generation and collision-safe path handling do not exist.

- [x] **Step 7: Add failing safe-filename test**

```python
async def test_store_slugifies_each_topic_segment_and_normalizes_reserved_chars(self):
    result, _ = await store.write_note(
        knowledge_note_id="note-1",
        mapping_record=None,
        source_mapping_seed={"source_inbox_path": "inbox/douyin/a.md", "source_content_fingerprint": "fp-1", "persisted_first_seen_inbox_path": "inbox/douyin/a.md"},
        placement=placement_result_for(["投资/交易", "做T:日内"]),
        note_metadata=KnowledgeNoteFileMetadata(title="做T", published_date="2026-06-03"),
        rendered_markdown="# x",
    )
    self.assertIn("投资_交易", str(result.final_path))
    self.assertIn("做T_日内", str(result.final_path))
```

- [x] **Step 8: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_knowledge_note_store.py' -v`
Expected: FAIL because path encoding is not locked to existing safe-filename rules.

- [x] **Step 9: Add failing deferred-metadata test**

```python
def test_renderer_includes_secondary_paths_and_generation_version(self):
    markdown = KnowledgeNoteRenderer().render(note_input)
    self.assertIn("secondary_topic_paths:", markdown)
    self.assertIn("generation_version:", markdown)
    self.assertIn("deferred_primary_path:", markdown)
```

- [x] **Step 10: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_knowledge_note_renderer.py' -v`
Expected: FAIL because required frontmatter metadata is incomplete.

- [x] **Step 11: Implement deterministic note-id helper**

```python
def build_knowledge_note_id(source_identity: dict) -> str:
    if source_identity.get("source_url") and source_identity.get("published_date"):
        base = f'{source_identity["source_url"]}|{source_identity["published_date"]}'
    elif source_identity.get("persisted_first_seen_inbox_path") and source_identity.get("published_date"):
        base = f'{source_identity["persisted_first_seen_inbox_path"]}|{source_identity["published_date"]}'
    else:
        base = f'{source_identity.get("source_url") or source_identity.get("persisted_first_seen_inbox_path")}|{source_identity["title"]}'
    return sha1(base.encode("utf-8")).hexdigest()
```

- [x] **Step 12: Implement renderer frontmatter contract**

```python
def render(self, payload: KnowledgeNotePayload) -> str:
    return f"""---
knowledge_note_id: {payload.knowledge_note_id}
source_inbox_path: {payload.source_inbox_path}
source_url: {payload.source_url}
topic_paths: {payload.topic_paths}
primary_topic_path: {payload.primary_topic_path}
secondary_topic_paths: {payload.secondary_topic_paths}
placement_path: {payload.placement_path}
deferred_primary_path: {payload.deferred_primary_path}
generation_version: {payload.generation_version}
topic_node_ids: {payload.topic_node_ids}
generated_at: {payload.generated_at}
---

# {payload.title}

## 核心概念
{payload.concepts_markdown}

## 核心结论
{payload.summary}

## 方法 / 框架
{payload.methods_markdown}

## 判断标准
{payload.decision_rules_markdown}

## 风险与边界
{payload.risks_markdown}

## 关键摘录
{payload.quotes_markdown}

## 来源
- [[{payload.source_inbox_path}]]
- {payload.source_url or "无外部原始链接"}
"""
```

- [x] **Step 13: Implement primary-path selection and processed-mapping builder**

```python
@dataclass
class KnowledgeNoteFileMetadata:
    title: str
    published_date: str


def choose_storage_primary_path(self, mapping_record: dict | None, placement: GraphPlacementResult) -> list[str]:
    current_primary_node_id = mapping_record.get("primary_topic_node_id") if mapping_record else None
    if current_primary_node_id and self.graph.is_active_node(current_primary_node_id):
        return self.graph.get_node(current_primary_node_id).path
    if current_primary_node_id:
        surviving_ancestor = self.graph.deepest_surviving_ancestor_node(current_primary_node_id)
        if surviving_ancestor:
            return surviving_ancestor.path
        replacement_target = self.graph.replacement_target_path(current_primary_node_id)
        if replacement_target:
            return replacement_target
    if placement.highest_confidence_replacement_path:
        return placement.highest_confidence_replacement_path
    return placement.canonical_primary_path


def build_processed_mapping_record(
    self,
    mapping_record: dict | None,
    source_mapping_seed: dict,
    storage_primary_path: list[str],
    placement: GraphPlacementResult,
    write_result: NoteWriteResult,
) -> dict:
    primary_node = self.graph.get_node_by_path(storage_primary_path)
    prior_paths = list((mapping_record or {}).get("prior_knowledge_note_paths", []))
    if write_result.prior_path and str(write_result.prior_path) not in prior_paths:
        prior_paths.append(str(write_result.prior_path))
    return {
        "source_inbox_path": source_mapping_seed["source_inbox_path"],
        "source_content_fingerprint": source_mapping_seed["source_content_fingerprint"],
        "source_processing_status": "processed",
        "knowledge_note_id": write_result.note_id,
        "knowledge_note_path": str(write_result.final_path),
        "prior_knowledge_note_paths": prior_paths,
        "primary_topic_node_id": primary_node.id,
        "secondary_topic_node_ids": placement.secondary_node_ids,
        "ancestor_topic_node_ids": sorted(set(self.graph.get_ancestor_ids(storage_primary_path)) | set(placement.secondary_ancestor_node_ids)),
        "graph_version": self.graph.version,
        "last_generated_at": utc_now_iso(),
        "persisted_first_seen_inbox_path": source_mapping_seed["persisted_first_seen_inbox_path"],
    }

```

- [x] **Step 14: Implement note writing and path-history helpers**

```python


async def write_note(
    self,
    knowledge_note_id: str,
    mapping_record: dict | None,
    source_mapping_seed: dict,
    placement: GraphPlacementResult,
    note_metadata: KnowledgeNoteFileMetadata,
    rendered_markdown: str,
) -> tuple[NoteWriteResult, dict]:
    storage_primary_path = self.choose_storage_primary_path(mapping_record, placement)
    final_path = self._build_note_path(
        storage_primary_path,
        note_metadata.published_date,
        note_metadata.title,
        knowledge_note_id,
    )
    prior_path = Path(mapping_record["knowledge_note_path"]) if mapping_record and mapping_record["knowledge_note_path"] else None
    if prior_path and prior_path != final_path:
        await self._move_with_history(prior_path, final_path)
    await self._write_markdown(final_path, rendered_markdown)
    result = NoteWriteResult(
        note_id=knowledge_note_id,
        final_path=final_path,
        prior_path=prior_path,
        placement_path=storage_primary_path,
        canonical_primary_path=storage_primary_path,
        deferred_primary_path=placement.deferred_primary_path,
    )
    processed_mapping_record = self.build_processed_mapping_record(
        mapping_record=mapping_record,
        source_mapping_seed=source_mapping_seed,
        storage_primary_path=storage_primary_path,
        placement=placement,
        write_result=result,
    )
    return result, processed_mapping_record


def _build_note_path(self, placement_path: list[str], published_date: str, title: str, note_id: str) -> Path:
    candidate = self._render_base_path(placement_path, published_date, title)
    existing_note_path = self._lookup_existing_path_for_note_id(note_id)
    if existing_note_path and existing_note_path == candidate:
        return existing_note_path
    if not candidate.exists():
        return candidate
    return candidate.with_name(f"{candidate.stem}-{note_id[:8]}{candidate.suffix}")


def _render_base_path(self, placement_path: list[str], published_date: str, title: str) -> Path:
    safe_segments = [KnowledgeArchiver._slugify(segment) for segment in placement_path]
    safe_title = KnowledgeArchiver._slugify(title)
    date_prefix = published_date or "undated"
    return self.knowledge_root.joinpath(*safe_segments, f"{date_prefix}-{safe_title}.md")


async def _move_with_history(self, prior_path: Path, final_path: Path) -> None:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    prior_path.replace(final_path)


# Caller contract: the transaction/orchestration layer persists `processed_mapping_record`
# via `metadata_state.upsert_source_mapping(...)` after knowledge/topic file writes and
# pending-mutation/topic-graph updates, matching the spec's write-order model.
```

- [x] **Step 15: Run tests to verify they pass**

Run: `python -m unittest discover -s test -p 'test_knowledge_note_renderer.py' -v && python -m unittest discover -s test -p 'test_knowledge_note_store.py' -v && python -m unittest discover -s test -p 'test_metadata_state.py' -v`
Expected: PASS with note output excluding raw body, required sections/source links/frontmatter present, stable `knowledge_note_id` generation, collision-safe path suffixing, prior-path traceability, and processed source-topic-map records updated with note/topic mapping fields.

- [x] **Step 16: Commit**

```bash
git add app/services/knowledge_pipeline/knowledge_note_identity.py app/services/knowledge_pipeline/knowledge_note_renderer.py app/services/knowledge_pipeline/knowledge_note_store.py app/services/knowledge_pipeline/metadata_state.py test/test_knowledge_note_renderer.py test/test_knowledge_note_store.py test/test_metadata_state.py
git commit -m "feat: add distilled knowledge note rendering and storage"
```

## Chunk 5: Topic Rebuild, Orchestration, and Repair

### Task 6: Render topic pages and rebuild impacted nodes

**Files:**
- Create: `app/services/knowledge_pipeline/topic_page_renderer.py`
- Create: `app/services/knowledge_pipeline/topic_rebuilder.py`
- Test: `test/test_topic_page_renderer.py`
- Test: `test/test_topic_rebuilder.py`

- [x] **Step 1: Write the failing summary/detail renderer test**

```python
def test_topic_renderer_separates_summary_and_details(self):
    markdown = TopicPageRenderer().render(payload)
    self.assertIn("## 概览", markdown)
    self.assertIn("## 详情积累", markdown)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_topic_page_renderer.py' -v`
Expected: FAIL because no topic page renderer exists.

- [x] **Step 3: Write the failing rebuild decision test**

```python
async def test_rebuilder_rewrites_summary_only_when_facets_change(self):
    result = await TopicRebuilder(...).rebuild_nodes(["topic-1"])
    self.assertTrue(result.updated_summary)
```

- [x] **Step 4: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_topic_rebuilder.py' -v`
Expected: FAIL because no rebuild contract exists.

- [x] **Step 5: Implement pure renderer**

```python
def render(self, payload: TopicPagePayload) -> str:
    ...
```

- [x] **Step 6: Implement rebuilder**

```python
async def rebuild_nodes(self, node_ids: list[str]) -> RebuildResult:
    ...
```

- [x] **Step 7: Run tests to verify they pass**

Run: `python -m unittest discover -s test -p 'test_topic_page_renderer.py' -v && python -m unittest discover -s test -p 'test_topic_rebuilder.py' -v`
Expected: PASS for summary rewrite rules, detail dedupe, ancestor propagation, and subtree rebuild scope.

- [x] **Step 8: Commit**

```bash
git add app/services/knowledge_pipeline/topic_page_renderer.py app/services/knowledge_pipeline/topic_rebuilder.py test/test_topic_page_renderer.py test/test_topic_rebuilder.py
git commit -m "feat: add topic rebuilding and page rendering"
```

### Task 7: Rewire the orchestrator to the new graph-backed flow

**Files:**
- Modify: `app/services/knowledge_pipeline/orchestrator.py`
- Modify: `app/services/knowledge_pipeline/archiver.py`
- Modify: `app/services/knowledge_pipeline/topic_updater.py`
- Modify: `app/services/knowledge_pipeline/category_map.py`
- Modify: `test/test_knowledge_pipeline_orchestrator.py`
- Modify: `test/test_knowledge_archiver.py`
- Modify: `test/test_knowledge_parser.py`

- [x] **Step 1: Write the failing orchestrator integration test**

```python
async def test_orchestrator_generates_note_graph_and_topic_updates(self):
    result = await orchestrator.process_files([md_file])
    self.assertEqual(result.completed, 1)
    self.assertTrue((tmp / "_meta" / "topic-graph.json").exists())
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_knowledge_pipeline_orchestrator.py' -v`
Expected: FAIL because the orchestrator still uses flat category/archive/topic logic.

- [x] **Step 3: Implement orchestrator dependency wiring**

```python
distiller = KnowledgeDistiller(...)
resolver = TopicPathResolver(...)
note_store = KnowledgeNoteStore(...)
rebuilder = TopicRebuilder(...)
```

- [x] **Step 4: Replace legacy archive/topic calls**

```python
# remove direct raw-copy archive + flat append topic updates
```

- [x] **Step 5: Run focused tests to verify they pass**

Run: `python -m unittest discover -s test -p 'test_knowledge_pipeline_orchestrator.py' -v && python -m unittest discover -s test -p 'test_knowledge_archiver.py' -v`
Expected: PASS with orchestrator producing graph state, knowledge notes, and rebuilt topic pages.

- [x] **Step 6: Run broader knowledge pipeline tests**

Run: `python -m unittest discover -s test -p 'test_knowledge*.py' -v`
Expected: PASS except for any known unrelated baseline failures already present before this task.

- [x] **Step 7: Commit**

```bash
git add app/services/knowledge_pipeline/orchestrator.py app/services/knowledge_pipeline/archiver.py app/services/knowledge_pipeline/topic_updater.py app/services/knowledge_pipeline/category_map.py test/test_knowledge_pipeline_orchestrator.py test/test_knowledge_archiver.py test/test_knowledge_parser.py
git commit -m "feat: wire graph-backed knowledge pipeline"
```

### Task 8: Add diagnosis/repair workflow and migration checks

**Files:**
- Create: `scripts/diagnose_knowledge_library.py`
- Create: `test/test_diagnose_knowledge_library.py`
- Modify: `docs/knowledge-pipeline-verification.md`

- [x] **Step 1: Write the failing dry-run diagnosis test**

```python
def test_diagnose_reports_orphan_topic_and_stale_mapping(self):
    result = run_diagnose(["--dry-run"])
    self.assertIn("orphan topic", result.stdout)
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_diagnose_knowledge_library.py' -v`
Expected: FAIL because the diagnosis script does not exist.

- [x] **Step 3: Write the failing apply repair test**

```python
def test_apply_repairs_mapping_and_topic_pages(self):
    result = run_diagnose(["--apply"])
    self.assertEqual(result.exit_code, 0)
```

- [x] **Step 4: Run test to verify it fails**

Run: `python -m unittest discover -s test -p 'test_diagnose_knowledge_library.py' -v`
Expected: FAIL because no repair/apply mode exists.

- [x] **Step 5: Implement diagnosis script**

```python
def main():
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
```

- [x] **Step 6: Document operator workflow**

```markdown
python scripts/diagnose_knowledge_library.py --dry-run
python scripts/diagnose_knowledge_library.py --apply
```

- [x] **Step 7: Run diagnosis tests to verify they pass**

Run: `python -m unittest discover -s test -p 'test_diagnose_knowledge_library.py' -v`
Expected: PASS for orphan detection, stale mapping repair, pending mutation surfacing, and repair reporting.

- [x] **Step 8: Run the full project test suite**

Run: `python -m unittest discover -s test -p 'test_*.py' -v`
Expected: PASS except for any unrelated known baseline failures that predate this work; if baseline failures exist, record them before merge.

- [x] **Step 9: Commit**

```bash
git add scripts/diagnose_knowledge_library.py test/test_diagnose_knowledge_library.py docs/knowledge-pipeline-verification.md
git commit -m "feat: add knowledge library diagnosis and repair workflow"
```
Implementation of hierarchical knowledge pipeline chunks 1-5 completed.
