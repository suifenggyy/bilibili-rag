# Knowledge Pipeline 完整流程（当前实现）

> 基于代码逐行梳理，2026-06-09。入口：`scripts/run_knowledge_pipeline.py` → `orchestrator.py`

---

## 总览

```
inbox/*.md
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│  Orchestrator.process_single_file()                      │
│                                                          │
│  1. Parse          解析 Markdown frontmatter + body      │
│  2. Bootstrap      初始化 _meta/ 目录和 JSON 状态文件     │
│  3. Distill        LLM 蒸馏 → 结构化知识单元              │
│  4. Resolve        LLM 解析 → 话题路径 + 变更提案         │
│  5. Finalize       图变更判定 + 放置决策                   │
│  6. Render+Write   渲染知识笔记 → 写入 vault              │
│  7. Rebuild        重建受影响的 topic 页                   │
│  8. Persist        持久化 graph + mapping 状态             │
│  9. Log            追加处理日志                            │
└─────────────────────────────────────────────────────────┘
    │
    ▼
vault/knowledge/  ← 知识笔记 .md
vault/knowledge/_topics/  ← 话题页面 .md
vault/_meta/  ← 状态文件 (topic-graph.json, source-topic-map.json, ...)
vault/_meta/logs/  ← 处理日志
```

---

## 步骤 1：Parse

**文件**：`parser.py` — `KnowledgeMarkdownParser`

**输入**：inbox 下的 Markdown 文件（必须含 YAML frontmatter）

**输出**：`ParsedKnowledgeDocument`

```
---
title: 一条视频教会你如何挑选适合自己的副业
date: 2026-06-03
source: https://www.douyin.com/video/7644112067632434470
summary: 副业筛选框架
key_points:
  - 不可能三角
  - 四象限
---

正文内容...
```

**解析规则**：
- 文件必须以 `---\n` 开头，找到闭合 `---` 分隔 frontmatter 和 body
- 必填字段：`title`、`date`、`source`（缺一则 `FrontmatterFieldMissingError`）
- `summary` 缺失时默认空字符串，不报错
- `key_points` 缺失时默认空列表
- body 为 frontmatter 闭合后的全部文本

**产出数据结构**：

```python
ParsedKnowledgeDocument(
    title="一条视频教会你如何挑选适合自己的副业",
    date_str="2026-06-03",
    source_url="https://www.douyin.com/video/...",
    summary="副业筛选框架",
    body="正文内容...",
    key_points=["不可能三角", "四象限"],
    raw_frontmatter={...},   # 原始 YAML dict
)
```

---

## 步骤 2：Bootstrap — 初始化状态

**文件**：`metadata_state.py` — `MetadataState`

**动作**：`await meta_state.bootstrap()`

确保 `_meta/` 目录下存在 5 个 JSON 文件，缺失则创建空默认值：

| 文件 | 默认值 | 用途 |
|------|--------|------|
| `topic-graph.json` | `{"version":"topic-graph-v1","nodes":[]}` | 话题图快照 |
| `source-topic-map.json` | `{"items":[]}` | inbox→知识笔记映射 |
| `topic-detail-index.json` | `{"items":[]}` | 话题细节指纹索引 |
| `pending-topic-mutations.json` | `{"items":[]}` | 待审核变更提案 |
| `pipeline-run-log.json` | `{"items":[]}` | 流水线运行日志 |

然后从磁盘加载当前状态：

```python
graph_snapshot = await meta_state.load_topic_graph()
graph = TopicGraph.from_snapshot(graph_snapshot)      # 反序列化为内存图

mapping_records_container = await meta_state.load_source_mapping()
mapping_records = mapping_records_container["items"]    # 现有映射列表
```

**注意**：每个文件处理都重新 bootstrap 一次，加载的是**上一次持久化后的状态**。这意味着同批处理中，第 2 个文件能看到第 1 个文件写入的 graph 变更。

---

## 步骤 3：Distill — LLM 蒸馏

**文件**：`knowledge_distiller.py` + `llm_processor.py`

### 3a. 弱信号判断（纯逻辑，不调 LLM）

```python
if len(body.strip()) < min_body_chars and not key_points and not summary.strip():
    → 返回 DistillationResult(status="skipped", failure_reason="weak_signal")
```

当 body 太短（默认 < 80 字符）且无 key_points 和 summary 时，直接跳过，不调 LLM。

### 3b. LLM 调用链

```
KnowledgeDistiller.processor(title, summary, key_points, body)
    │
    ▼
DistillerProcessor.__call__(title, summary, key_points, body)
    │  拼接 user_content = "标题：...\n摘要：...\n关键要点：...\n正文：\n..."
    │  获取 TextPostProcessor（lazy init，根据 TEXT_MODEL_BACKEND 选择）
    ▼
TextPostProcessor.postprocess(user_content, title=title)
    │  system prompt = settings.knowledge_note_distill_prompt
    │  实际 HTTP 调用：Ollama / ProxyTextPostProcessor (OpenAI 兼容) / localopenai
    ▼
LLM 返回 YAML/JSON 文本
    │
    ▼
_parse_yaml_output(raw)  →  解析为 dict
```

**LLM 后端选择**（由 `.env` 的 `TEXT_MODEL_BACKEND` 决定）：

| 值 | 实现 | 端点 |
|----|------|------|
| `ollama` | `OllamaTextPostProcessor` | `POST {base_url}/api/generate` |
| `proxy` | `ProxyTextPostProcessor` | `POST {base_url}/v1/chat/completions` |
| `localopenai` | `ProxyTextPostProcessor` | `POST {local_openai_base_url}/v1/chat/completions` |

**实际使用**：当前 `.env` 配置 `TEXT_MODEL_BACKEND=ollama` + `TEXT_MODEL_BASE_URL` 指向 DashScope 兼容端点，模型 `gpt-5-mini`，走的是 `OllamaTextPostProcessor`。

### 3c. Payload 校验

LLM 返回的 dict 必须包含：
- `summary`：非空字符串
- `concepts`、`methods`、`decision_rules`、`examples`、`risks`、`quotes`：均为 list

校验失败 → `DistillationResult(status="failed", failure_reason="invalid distillation payload")`

LLM 调用异常 → `DistillationResult(status="failed", failure_reason=str(exc))`

### 3d. 产出

```python
DistilledKnowledge(
    source_identity={"source_inbox_path": "...", "source_url": "...", "title": "...", "published_date": "..."},
    summary="面向上班族的副业决策框架...",
    concepts=["不可能三角", "时间×专业度四象限"],
    methods=["四象限筛选"],
    decision_rules=["稳定/上手快/收入高三选二"],
    examples=["信息差套利案例"],
    risks=["打粉/客资售卖风险"],
    quotes=[{"text": "...", "reason": "..."}],
    source_excerpt_fingerprints=["..."],  # quotes 的 text 字段列表
)
```

**如果 distill 结果不是 `processed`，pipeline 终止，返回失败。**

---

## 步骤 4：Resolve — LLM 解析话题路径

**文件**：`topic_path_resolver.py` + `llm_processor.py`

### 4a. LLM 调用

```
TopicPathResolver.processor(units=units, graph_snapshot=graph.to_snapshot())
    │
    ▼
TopicPathProcessor.__call__(units, graph_snapshot)
    │  拼接 user_content = "来源标题：...\n摘要：...\n...已有话题图：{snapshot}\n已有别名：[...]"
    │  获取 TextPostProcessor（lazy init，用 settings.knowledge_topic_path_prompt）
    ▼
TextPostProcessor.postprocess(user_content)
    │
    ▼
_parse_yaml_output(raw) → dict
```

### 4b. Payload 校验 (`_validate_payload`)

LLM 返回必须包含：
- `primary_path`：`["投资", "盘口指标"]` — 非空字符串列表
- `secondary_paths`：`[["投资", "短线交易", "做T"]]` — 路径列表的列表
- `mutation_proposals`：变更提案列表（可为空）

校验失败 → `ValueError("invalid topic path payload")`，pipeline 终止。

secondary_paths 中不在任何 proposal 的 target_paths 中的路径，标记为 `_secondary_paths_requiring_canonical_check`，后续在 placement 阶段处理。

### 4c. Mutation proposals 归一化 (`_normalize_proposals`)

对每个 proposal 做容错处理：

| mutation type | 处理逻辑 |
|--------------|---------|
| `create_leaf` | 清空 LLM 可能误填的 `affected_node_ids`；如果缺 `target_parent_path`/`target_name`，尝试从 `target_paths` 推导；否则跳过 |
| `add_alias` | 要求恰好 1 个 `affected_node_ids`（且存在于图中），否则跳过 |
| `rename`/`move`/`merge`/`split`/`replace` | 要求 `affected_node_ids` 存在于图中 + 各自必填字段，否则跳过 |
| 未知 type | 直接跳过 |

**关键兜底**：如果 `primary_path` 在图中不存在且没有任何 proposal 覆盖它，**自动生成**一个 `create_leaf` proposal（confidence=0.9）。

**产出**：

```python
TopicResolution(
    requested_primary_path=["投资", "盘口指标"],
    secondary_paths=[["投资", "短线交易", "做T"], ...],
    mutation_proposals=[MutationProposal(...), ...],
    source_identity={...},
)
```

---

## 步骤 5：Finalize — 图变更判定与放置

**文件**：`topic_graph.py` — `TopicGraph.finalize_resolution()`

这是纯内存操作，不涉及 I/O。核心逻辑：

### 5a. 逐个评估 mutation proposals

```
for proposal in resolution.mutation_proposals:
    decision = graph.evaluate_mutation(proposal)
```

**`evaluate_mutation` 规则**：

| 条件 | 决策 |
|------|------|
| `create_leaf` 或 `add_alias` + confidence ≥ 0.85 + impacted_existing_nodes ≤ 5 + replaced_canonical_paths ≤ 1 | → `auto_apply` |
| `create_leaf` + 父路径不存在但**无冲突**（同路径无 deprecated 节点） | → `auto_apply`（递归创建祖先） |
| `create_leaf` + 父路径有**冲突**（同路径有 deprecated 节点） | → `pending` |
| `add_alias` + affected_node 的 status ≠ active | → `pending` |
| 其他（merge/rename/move/split/replace 或低 confidence） | → `pending` |

### 5b. 执行 auto_apply

对 `auto_apply` 的 proposals，**立即修改内存图**：

- `create_leaf`：调用 `apply_new_leaf(parent_path, child_name)` — 如果 parent_path 上的祖先不存在，递归创建它们
- `add_alias`：往已有节点的 aliases 列表追加别名

### 5c. 延迟的 proposals → `DeferredMutationRecord`

非 primary 的 `pending` proposals 封装为 `DeferredMutationRecord`（后续会被写入 `pending-topic-mutations.json`）。

### 5d. Primary path 的放置决策

| 情况 | placement_mode | placement_path |
|------|---------------|----------------|
| primary_path 在图中已存在，或其 proposal 被 auto_apply | `canonical` | 请求的 primary_path |
| primary_path 的 proposal 被 pending，延迟 | `deferred_to_existing_ancestor` | 图中最近已存在的祖先路径 |

### 5e. Secondary paths 的放置

每个 secondary_path 同样评估：auto_apply → canonical；pending → deferred_to_existing_ancestor + 生成 DeferredMutationRecord。

### 5f. 产出

```python
GraphPlacementResult(
    canonical_primary_path=["投资", "盘口指标"],       # 最终放置路径
    canonical_primary_node_id="abc123",               # 主节点 ID
    placement_path=["投资", "盘口指标"],               # 笔记存储路径
    placement_mode="canonical",                       # canonical | deferred_to_existing_ancestor
    deferred_primary_path=None,                       # 如果 deferred，此为请求但未创建的路径
    highest_confidence_replacement_path=None,
    secondary_placements=[SecondaryPlacementResult(...)],
    secondary_node_ids=["def456"],
    ancestor_node_ids=["root", "abc123"],
    secondary_ancestor_node_ids=["root"],
    deferred_mutation_records=[DeferredMutationRecord(...)],  # 待持久化的延迟变更
)
```

---

## 步骤 6：Render + Write — 渲染与写入知识笔记

### 6a. 生成稳定 note_id

**文件**：`knowledge_note_identity.py`

```python
note_id = sha256(f"{source_url}|{published_date}|{persisted_first_seen_inbox_path}|{title}".encode()).hexdigest()
```

相同来源 + 日期 + 路径 + 标题 → 相同 ID，确保幂等。

### 6b. 渲染 Markdown

**文件**：`knowledge_note_renderer.py`

**frontmatter**：

```yaml
type: knowledge_note
knowledge_note_id: <sha256>
source_inbox_path: inbox/douyin/2026-06-03/xxx.md
source_url: https://...
primary_topic_path: ["投资", "盘口指标"]
secondary_topic_paths: [["投资", "短线交易", "做T"]]
placement_path: ["投资", "盘口指标"]
deferred_primary_path: null
generation_version: v1
topic_node_ids:
  primary: abc123
  secondary: [def456]
  ancestors: [root, abc123]
generated_at: 2026-06-09T09:00:00+00:00
status: distilled
```

**正文结构**：

```markdown
## 核心概念
- 概念1

## 核心结论
<summary>

## 方法 / 框架
- 方法1

## 判断标准
- 规则1

## 场景与案例
- 案例1

## 风险与边界
- 风险1

## 关键摘录
> 引用1

## 来源
- [[inbox/douyin/2026-06-03/xxx.md]]
- https://...
```

**不包含原始正文**（只含蒸馏后的结构化内容）。

### 6c. 写入文件

**文件**：`knowledge_note_store.py`

1. **选择存储路径** `choose_storage_primary_path`：
   - 如果已有映射记录且节点仍 active → 保持原路径
   - 否则使用 placement 的 canonical_primary_path

2. **构建文件路径** `_build_note_path`：
   - `knowledge/{slug1}/{slug2}/.../{date}-{slugified-title}.md`
   - slug 规则：小写 + 去除特殊字符 + 空格/连字符合并为单个 `-`
   - 如果路径已存在同名文件 → 添加 `-{note_id[:8]}` 后缀防碰撞

3. **移动旧文件**：如果映射中已有 `knowledge_note_path` 且与目标不同，移动到新路径

4. **写入**：`aiofiles.open(final_path, 'w')` 写入渲染后的 Markdown

5. **构建映射记录** `build_processed_mapping_record`：

| 场景 | source_processing_status | knowledge_note_id | knowledge_note_path |
|------|------------------------|-------------------|---------------------|
| primary_node 存在（正常） | `"processed"` | note_id | final_path |
| primary_node 为 None（延迟放置） | `"skipped"` | null | null |

**注意**：当 placement 是 deferred 时，笔记仍然被写入文件系统，但映射记录标记为 `"skipped"` 且 knowledge_note_id/path 为 null。这是一个已知的不一致——笔记文件存在但映射不指向它。

---

## 步骤 7：Rebuild — 重建 topic 页面

**文件**：`topic_rebuilder.py`

对 placement_path 对应的图节点，重建其 topic 页面。

### 7a. 查找关联笔记

从 `meta_state.load_source_mapping()` 中筛选 `primary_topic_node_id == node_id` 的记录。

### 7b. LLM 摘要

如果有关联笔记：
1. 从文件系统读取每篇笔记内容
2. 调用 `distiller.distill_topic_summary(topic_name, note_contents)`
   - 内部使用 `TopicSummaryProcessor` → LLM → 解析
   - 失败时 fallback 到简单列表（`- 笔记标题`）

### 7c. 渲染 topic 页面

**文件**：`topic_page_renderer.py`

```markdown
---
type: topic_page
topic_id: abc123
status: active
---

# 盘口指标
**路径**: 投资 > 盘口指标

## 概览
<LLM 生成的摘要 或 "暂无概览信息">

### 子主题
- [[委比]] -

## 详情积累
- [[knowledge/投资/盘口指标/2026-06-09-xxx.md|xxx]]
```

### 7d. 写入文件

```python
topic_path = knowledge_dir / "_topics" / f"{node.path[-1]}.md"
```

**注意**：直接同步写入，不经过 ObsidianWriter。topic 页面只有叶子节点名（如 `盘口指标.md`），不包含完整路径层级。

---

## 步骤 8：Persist — 持久化状态

**文件**：`metadata_state.py`

```python
async with meta_state.write_lock():
    await meta_state.save_source_mapping(mapping_records_container)
    await meta_state.save_topic_graph(graph.to_snapshot())
```

### 8a. 写锁

混合锁机制：
1. **asyncio.Lock**：进程内序列化，防止同一 event loop 中并发写
2. **文件锁** `_meta/.write.lock`：跨进程保护，`O_CREAT | O_EXCL` 原子创建

超时（默认 5 秒）→ `MetadataWriteLockTimeout`，记录 lock-contention 失败到 `pipeline-run-log.json`。

### 8b. save_source_mapping

1. **Legacy 迁移**：自动将 `status="processed"` 但 `primary_topic_node_id=null` 的旧记录降级为 `"skipped"`（兼容旧 pipeline 写入的数据）
2. **Schema 校验**：每条记录必须包含 `source_inbox_path`, `source_content_fingerprint`, `source_processing_status`, `knowledge_note_id`, `knowledge_note_path`, `primary_topic_node_id`, `secondary_topic_node_ids`, `ancestor_topic_node_ids`, `graph_version`, `last_generated_at`, `persisted_first_seen_inbox_path`
3. **业务规则校验**：
   - `processed` 状态必须有 `knowledge_note_id` + `knowledge_note_path` + `primary_topic_node_id`
   - `tombstoned` 同理
4. **原子写入**：先写 `.json.tmp`，再 `replace()` 到目标文件

### 8c. save_topic_graph

序列化当前内存图为：

```json
{
  "version": "topic-graph-v1",
  "nodes": [
    {
      "id": "abc123",
      "name": "盘口指标",
      "parent_id": "root",
      "children_ids": ["def456"],
      "aliases": [],
      "path": ["投资", "盘口指标"],
      "replacement_target_id": null,
      "lineage": [],
      "summary_version": "",
      "detail_version": "",
      "status": "active"
    },
    ...
  ]
}
```

### 8d. Deferred mutations 未持久化

**当前遗漏**：`placement.deferred_mutation_records` 中的延迟变更提案**没有被写入** `pending-topic-mutations.json`。orchestrator 只保存了 `source_mapping` 和 `topic_graph`，遗漏了 `merge_pending_mutations` 调用。

---

## 步骤 9：Log — 处理日志

**文件**：`processing_logger.py`

追加一行到 `_meta/logs/2026-06-09.log`：

```
2026-06-09T09:00:00 [INFO] 文章: xxx | 分类: 投资 | topics: [投资, 盘口指标]
2026-06-09T09:00:00 [INFO] quality_score: 0.90 | 耗时: 42.1s
```

写入方式：优先通过 Obsidian Local REST API，失败则直接写文件系统。

---

## 持久化状态文件汇总

处理完成后，`_meta/` 目录下的文件：

| 文件 | 写入时机 | 内容 |
|------|---------|------|
| `topic-graph.json` | 步骤 8 | 完整话题图快照（所有节点 + 关系 + 状态） |
| `source-topic-map.json` | 步骤 8 | 每个已处理 inbox 文件的映射（状态、笔记路径、话题节点 ID） |
| `topic-detail-index.json` | 未写入 | 话题细节指纹索引（当前 pipeline 未使用） |
| `pending-topic-mutations.json` | 未写入 | 延迟变更提案（**当前遗漏**） |
| `pipeline-run-log.json` | 未写入 | 流水线运行日志（**当前遗漏**） |
| `logs/2026-06-09.log` | 步骤 9 | 人可读的日志行 |

---

## 已知问题与偏差

1. **Deferred mutations 未持久化**：`graph.finalize_resolution()` 产出的 `deferred_mutation_records` 没有被写入 `pending-topic-mutations.json`。同一路径上的后续笔记处理无法利用之前的延迟提案。

2. **Topic 页面路径扁平**：topic 页面写为 `_topics/盘口指标.md`，而非 `_topics/投资/盘口指标.md`。同名不同路径的节点会互相覆盖。

3. **Rebuilder 用 monkeypatch 传数据**：`meta_state.get_source_mapping_records = lambda: mapping_records`，而非使用 `meta_state` 自身的加载方法。这绕过了锁和持久化读取。

4. **Deferred placement 的笔记映射不一致**：`build_processed_mapping_record` 将 `primary_node=None` 的记录标为 `"skipped"`，但笔记文件实际已写入文件系统。映射不指向已写入的文件。

5. **每文件独立 bootstrap**：每个文件处理时都重新 `bootstrap()` + 从磁盘加载状态，保证看到前一个文件的结果。但这意味着每文件都要获取/释放写锁，且 bootstrap 每次都检查 5 个文件是否存在。

6. **Orchestrator 仍保留旧 Classifier**：`_get_classifier()` 和构造函数的 `classifier` 参数仍存在，但实际 pipeline 不再使用它。`process_inbox()` 的 docstring 仍描述旧流程。

7. **Topic 页面同步写**：`topic_rebuilder` 产出后用 `open(..., "w")` 同步写，不走 ObsidianWriter 或 aiofiles。

8. **笔记存储路径的 slug 丢失中文语义**：`_slugify("盘口指标")` → `pan-kou-zhi-biao`（取决于 `\w` 的 Unicode 匹配行为），非中文字符可能被直接丢弃。

9. **`topic-detail-index.json` 和 `pipeline-run-log.json`**：bootstrap 时创建空文件，但 pipeline 从未写入内容。`append_run_log_start` / `finalize_run_log` 方法存在于 `MetadataState` 中但 orchestrator 未调用。

10. **LLM 调用无重试**：`DistillerProcessor` / `TopicPathProcessor` 对 LLM 调用无重试机制。单次超时或错误直接导致整个文件处理失败。
