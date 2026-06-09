# Knowledge Pipeline — LLM 调用详解

> 逐个 LLM 调用点的完整数据流：传入数据、具体 prompt、HTTP 请求体、结果解析。
> 基于代码逐行梳理，2026-06-09。

---

## LLM 调用基础设施

### 后端选择

由 `.env` 的 `TEXT_MODEL_BACKEND` 决定：

| 值 | 实现 | HTTP 端点 | 请求格式 |
|----|------|----------|---------|
| `ollama`（默认） | `OllamaTextPostProcessor` | `POST {base_url}/api/generate` | `{"model": "...", "prompt": "...", "stream": false}` |
| `proxy` | `ProxyTextPostProcessor` | `POST {base_url}/v1/chat/completions` | OpenAI chat messages 格式 |
| `localopenai` | `ProxyTextPostProcessor` | `POST {local_openai_base_url}/v1/chat/completions` | OpenAI chat messages 格式 |

**当前实际配置**（`.env.example` 默认值）：`TEXT_MODEL_BACKEND=ollama`，`TEXT_MODEL_BASE_URL=http://localhost:11434`，`TEXT_MODEL_NAME=gemma4:e2b`。

但生产环境 `TEXT_MODEL_BASE_URL` 实际指向 DashScope 兼容端点，模型为 `gpt-5-mini`。虽然走的是 `OllamaTextPostProcessor`（用 `/api/generate` 端点），DashScope 的兼容层也支持该格式。

### 统一接口

所有 LLM 后端实现同一接口：

```python
class TextPostProcessor(Protocol):
    async def postprocess(self, text: str, title: Optional[str] = None) -> str
```

输入：字符串；输出：字符串。知识 pipeline 的结构化需求（dict in / dict out）由 `llm_processor.py` 的适配器桥接。

### 适配器模式

```
KnowledgeDistiller / TopicPathResolver
  期望: async processor(**kwargs) -> dict
           │
           ▼
DistillerProcessor / TopicPathProcessor  (llm_processor.py)
  1. 把 kwargs 拼成 user_content 字符串
  2. 调用 TextPostProcessor.postprocess(user_content, title=title)
  3. 对返回的字符串做 YAML/JSON 解析 → dict
           │
           ▼
OllamaTextPostProcessor / ProxyTextPostProcessor
  1. 把 prompt_template 作为 system prompt（或拼入 prompt）
  2. 把 user_content 作为 user message（或拼入 prompt）
  3. HTTP 调用 → 返回原始文本字符串
```

---

## LLM 调用点 1：知识蒸馏

**触发**：步骤 3，对每个 inbox 文件调用一次

**调用链**：

```
orchestrator._run_pipeline()
  → KnowledgeDistiller(processor=DistillerProcessor()).distill(doc, source_identity)
    → self.processor(title=..., summary=..., key_points=..., body=...)
      → DistillerProcessor.__call__(title, summary, key_points, body)
```

### 传入数据

| 参数 | 来源 | 示例值 |
|------|------|--------|
| `title` | `doc.title`（frontmatter 的 `title` 字段） | `"一条视频教会你如何挑选适合自己的副业 #职场 #副业 #副业避坑 #搞钱思维"` |
| `summary` | `doc.summary`（frontmatter 的 `summary` 字段） | `"副业筛选框架"` |
| `key_points` | `doc.key_points`（frontmatter 的 `key_points` 字段） | `["不可能三角：稳定/上手快/收入高三选二", "时间×专业度四象限"]` |
| `body` | `doc.body`（frontmatter 闭合 `---` 后的全部文本） | `"正文内容..."`（可能数千字） |

### Prompt 构造

**System prompt**：`settings.knowledge_note_distill_prompt`（来自 `prompt_defaults.py`）

```
你会收到 source summary、key_points、body。
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
要求：优先综合 key_points 与正文；quotes 最多 3 条；缺项返回空列表。
```

**User content**（由 `DistillerProcessor.__call__` 拼接）：

```
标题：一条视频教会你如何挑选适合自己的副业 #职场 #副业 #副业避坑 #搞钱思维
摘要：副业筛选框架
关键要点：['不可能三角：稳定/上手快/收入高三选二', '时间×专业度四象限']
正文：
<doc.body 的全部内容>
```

### 实际 HTTP 请求体

**Ollama 后端**（当前实际使用）：

```json
POST {TEXT_MODEL_BASE_URL}/api/generate
{
  "model": "gpt-5-mini",
  "prompt": "<system_prompt>\n\n标题：一条视频教会你如何挑选适合自己的副业...\n摘要：副业筛选框架\n关键要点：['不可能三角...']\n正文：\n<全部body>",
  "stream": false
}
```

**Proxy 后端**（如果 `TEXT_MODEL_BACKEND=proxy`）：

```json
POST {TEXT_MODEL_BASE_URL}/v1/chat/completions
{
  "model": "gpt-5-mini",
  "messages": [
    {"role": "system", "content": "<system_prompt>"},
    {"role": "user", "content": "标题：一条视频教会你如何挑选适合自己的副业...\n摘要：副业筛选框架\n关键要点：['不可能三角...']\n正文：\n<全部body>"}
  ]
}
```

### LLM 返回值

原始字符串，例如：

```yaml
summary: "面向上班族的副业决策框架：用不可能三角判断项目属性，结合时间×专业度四象限评估匹配度"
concepts:
  - "不可能三角（稳定/上手快/收入高）"
  - "时间×专业度四象限"
methods:
  - "四象限筛选法"
decision_rules:
  - "稳定/上手快/收入高三者只能选二"
examples:
  - "信息差套利（尾房/头等舱票）"
risks:
  - "打粉/客资售卖灰色地带"
quotes:
  - text: "不要一开始就追求三角全占"
    reason: "核心判断原则的简洁表述"
```

### 结果解析

`DistillerProcessor.__call__` 调用 `_parse_yaml_output(raw)`：

1. 去除 markdown 代码围栏（如果 LLM 输出了 ` ```yaml ... ``` `）
2. `yaml.safe_load(text)` → dict
3. 失败则 `json.loads(text)` → dict（JSON 兜底）
4. 仍然失败 → `ValueError("LLM output is not valid YAML/JSON")`

然后 `KnowledgeDistiller._validate_payload(payload)` 校验：

- `payload["summary"]` 必须是非空字符串
- `payload["concepts"]`/`methods`/`decision_rules`/`examples`/`risks`/`quotes` 必须是 list
- 校验失败 → `DistillationResult(status="failed")`

### 校验通过后的结构化输出

```python
DistilledKnowledge(
    source_identity={"source_inbox_path": "inbox/douyin/...", "source_url": "https://...", ...},
    summary="面向上班族的副业决策框架...",
    concepts=["不可能三角（稳定/上手快/收入高）", "时间×专业度四象限"],
    methods=["四象限筛选法"],
    decision_rules=["稳定/上手快/收入高三者只能选二"],
    examples=["信息差套利（尾房/头等舱票）"],
    risks=["打粉/客资售卖灰色地带"],
    quotes=[{"text": "不要一开始就追求三角全占", "reason": "核心判断原则的简洁表述"}],
    source_excerpt_fingerprints=["不要一开始就追求三角全占"],  # quotes 中每条的 text
)
```

### 异常处理

| 异常类型 | 来源 | 结果 |
|---------|------|------|
| LLM HTTP 错误（超时/网络/500） | `postprocess()` 内部 `httpx` 抛出 | `distill()` 捕获 → `DistillationResult(status="failed", failure_reason=str(exc))` |
| LLM 返回非 YAML/JSON | `_parse_yaml_output()` | `distill()` 捕获 → `DistillationResult(status="failed", failure_reason=str(exc))` |
| YAML 解析成功但缺 summary 字段 | `_validate_payload()` | `distill()` 捕获 → `DistillationResult(status="failed", failure_reason="invalid distillation payload")` |
| body 太短 + 无 key_points + 无 summary | 弱信号判断（调 LLM 之前） | `DistillationResult(status="skipped", failure_reason="weak_signal")` |

---

## LLM 调用点 2：话题路径解析

**触发**：步骤 4，对每个蒸馏成功的文件调用一次

**调用链**：

```
orchestrator._run_pipeline()
  → TopicPathResolver(processor=TopicPathProcessor()).resolve(units, graph)
    → self.processor(units=units, graph_snapshot=graph.to_snapshot())
      → TopicPathProcessor.__call__(units, graph_snapshot)
```

### 传入数据

| 参数 | 类型 | 来源 | 示例值 |
|------|------|------|--------|
| `units` | `DistilledKnowledge` | 步骤 3 的产出 | 完整的结构化知识单元 |
| `graph_snapshot` | `dict` | `graph.to_snapshot()` | 当前话题图快照 |

### Prompt 构造

**System prompt**：`settings.knowledge_topic_path_prompt`（来自 `prompt_defaults.py`）

```
你会收到 source metadata、summary、key_points、body、existing topic graph、existing aliases。
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
规则：primary_path 必填；secondary_paths 不得重复 primary_path；
create_leaf 必须用 `affected_unresolved_names` 表示待创建名称且 `affected_node_ids` 为空；
add_alias 必须用 `affected_node_ids` 指向已有 canonical node，并用 `target_name` 表示待添加 alias 文本；
不要输出解释性文字。
```

**User content**（由 `TopicPathProcessor.__call__` 拼接）：

```
来源标题：一条视频教会你如何挑选适合自己的副业 #职场 #副业 #副业避坑 #搞钱思维
来源URL：https://www.douyin.com/video/7644112067632434470
摘要：面向上班族的副业决策框架...
核心概念：['不可能三角（稳定/上手快/收入高）', '时间×专业度四象限']
方法：['四象限筛选法']
决策规则：['稳定/上手快/收入高三者只能选二']
示例：['信息差套利（尾房/头等舱票）']
风险：['打粉/客资售卖灰色地带']

已有话题图：{"version": "topic-graph-v1", "nodes": [...]}

已有别名：[['不可能三角', '稳定/上手快/收入高'], ['做T', '日内回转']]
```

**注意**：`已有话题图` 直接把 `graph.to_snapshot()` 整个 dict 转为字符串嵌入。当图很大时，这个字段会非常长。

### 实际 HTTP 请求体

**Ollama 后端**：

```json
POST {TEXT_MODEL_BASE_URL}/api/generate
{
  "model": "gpt-5-mini",
  "prompt": "<system_prompt>\n\n来源标题：一条视频教会你如何挑选适合自己的副业...\n来源URL：https://...\n摘要：面向上班族的副业决策框架...\n核心概念：[...]\n方法：[...]\n决策规则：[...]\n示例：[...]\n风险：[...]\n\n已有话题图：{\"version\": \"topic-graph-v1\", \"nodes\": [...]}\n\n已有别名：[[...], [...]]",
  "stream": false
}
```

### LLM 返回值

原始字符串，例如：

```yaml
primary_path: ["副业", "筛选与决策框架"]
secondary_paths:
  - ["副业", "筛选与决策框架", "不可能三角（稳定 / 上手快 / 收入高）"]
  - ["副业", "扩展路径", "获客路线"]
mutation_proposals:
  - type: create_leaf
    target_parent_path: ["副业"]
    target_name: "筛选与决策框架"
    target_paths: [["副业", "筛选与决策框架"]]
    confidence: 0.9
    reason: "核心决策框架应作为独立话题"
  - type: create_leaf
    target_parent_path: ["副业", "筛选与决策框架"]
    target_name: "不可能三角"
    target_paths: [["副业", "筛选与决策框架", "不可能三角"]]
    confidence: 0.9
    reason: "框架核心概念需建立子话题"
```

### 结果解析

#### 第一阶段：YAML/JSON 解析

同蒸馏步骤，`_parse_yaml_output(raw)` → `dict`

#### 第二阶段：Payload 校验 (`_validate_payload`)

| 字段 | 校验规则 | 失败结果 |
|------|---------|---------|
| `primary_path` | 必须是 `list[str]`，每个元素非空 | `ValueError("invalid topic path payload")` |
| `secondary_paths` | 必须是 `list[list[str]]`，内层每个元素非空 | `ValueError("invalid topic path payload")` |
| `secondary_paths` 中与 primary_path 重复的项 | 去重 | 静默移除 |
| `secondary_paths` 中不在任何 proposal 的 `target_paths` 中的路径 | 标记 | 加入 `_secondary_paths_requiring_canonical_check` 列表，不报错 |

#### 第三阶段：Mutation proposals 归一化 (`_normalize_proposals`)

**这是容错最重的环节**，对 LLM 输出做大量修补：

| LLM 输出问题 | 修补方式 |
|-------------|--------|
| mutation type 不在 7 种之内 | 跳过该 proposal，打 warning 日志 |
| confidence 为字符串 `"0.9"` | `float()` 转换；转换失败默认 0.5 |
| confidence 超出 [0,1] 范围 | 默认 0.5 |
| `create_leaf` 但 `affected_node_ids` 非空 | 强制清空为 `[]`（LLM 常犯错） |
| `create_leaf` 但缺 `target_parent_path` 或 `target_name` | 尝试从 `target_paths[0]` 推导；推导失败则跳过 |
| 非 create_leaf 类型但 `affected_node_ids` 为空 | 跳过 |
| 非 create_leaf 类型但 `affected_node_ids` 中有不在图中的 ID | 过滤掉无效 ID；全部无效则跳过 |
| `merge`/`replace` 缺 `target_replacement_node_id` | 跳过 |
| `move` 缺 `target_parent_path` | 跳过 |
| `rename` 缺 `target_name` | 跳过 |
| `split` 缺 `target_paths` | 跳过 |
| `add_alias` 的 `affected_node_ids` 不是恰好 1 个 | 跳过 |

**兜底规则**：如果 `primary_path` 在图中不存在，且没有任何 proposal 的 `target_paths` 覆盖它，**自动生成**一个 `create_leaf` proposal：

```python
MutationProposal(
    type="create_leaf",
    target_parent_path=primary_path[:-1],   # 如 ["副业"]
    target_name=primary_path[-1],            # 如 "筛选与决策框架"
    target_paths=[primary_path],             # 如 [["副业", "筛选与决策框架"]]
    confidence=0.9,
    impacted_existing_nodes=1,
    replaced_canonical_paths=0,
    reason="auto-created for primary path",
)
```

#### 最终产出

```python
TopicResolution(
    requested_primary_path=["副业", "筛选与决策框架"],
    secondary_paths=[["副业", "筛选与决策框架", "不可能三角"], ...],
    mutation_proposals=[MutationProposal(...), ...],
    source_identity={"source_inbox_path": "...", ...},
)
```

---

## LLM 调用点 3：Topic 摘要蒸馏

**触发**：步骤 7，对每个受影响的 topic 节点调用一次（仅当该节点有关联笔记时）

**调用链**：

```
orchestrator._run_pipeline()
  → TopicRebuilder.rebuild_nodes(node_ids)
    → self.distiller.distill_topic_summary(topic_name, note_contents)
      → KnowledgeDistiller.distill_topic_summary(topic_name, note_contents)
        → TopicSummaryProcessor()(topic_name=..., note_contents=...)
          → TopicSummaryProcessor.__call__(topic_name=..., note_contents=...)
```

### 传入数据

| 参数 | 来源 | 示例值 |
|------|------|--------|
| `topic_name` | `node.path[-1]`（图节点的最后一段名称） | `"盘口指标"` |
| `note_contents` | 从文件系统读取该节点下所有知识笔记的内容 | `["<笔记1全文>", "<笔记2全文>", ...]` |

### Prompt 构造

**System prompt**：`settings.knowledge_topic_summary_prompt`（来自 `prompt_defaults.py`）

```
你会收到某个 topic 的现有总结与所有聚合后的知识要点。
输出 markdown，总结区必须严格包含：
## 概览
## 核心框架
## 关键结论
## 上下位关系
可选补充：
## 适用边界
要求：写 topic 本身的稳定知识，不要写"某篇文章为什么属于这个 topic"。
```

**User content**（由 `TopicSummaryProcessor.__call__` 构造）：

```python
# 实际是把 kwargs 转为 str() 传入
str({"topic_name": "盘口指标", "note_contents": ["<笔记1全文>", ...]})
```

即：

```
{'topic_name': '盘口指标', 'note_contents': ['<笔记1全文>', '<笔记2全文>']}
```

**⚠️ 当前问题**：`TopicSummaryProcessor.__call__(**kwargs)` 把整个 kwargs dict 直接 `str()` 传入。这意味着笔记全文被 `str()` 包裹后作为一整个字符串传入，格式可读性差，且大量文本被压缩在引号内，LLM 难以区分结构。

### 实际 HTTP 请求体

**Ollama 后端**：

```json
POST {TEXT_MODEL_BASE_URL}/api/generate
{
  "model": "gpt-5-mini",
  "prompt": "<system_prompt>\n\n{'topic_name': '盘口指标', 'note_contents': ['<笔记1全文>', '<笔记2全文>']}",
  "stream": false
}
```

### LLM 返回值

可能是 Markdown 或 YAML。`distill_topic_summary` 的处理逻辑：

```python
result = await summary_processor(topic_name=..., note_contents=...)
if isinstance(result, dict):
    return result.get("summary", str(result))
return str(result)
```

- 如果 `_parse_yaml_output` 成功解析为 dict → 取 `summary` 字段，或整个 dict 转字符串
- 如果解析失败（LLM 返回了 Markdown） → 异常被捕获 → fallback 到简单笔记标题列表

### 异常处理

```python
try:
    result = await summary_processor(...)
    ...
except Exception as exc:
    # fallback
    return "\n".join(note_contents[:3]) if note_contents else ""
```

LLM 失败时不阻断 pipeline，而是返回简陋的 fallback 内容。

---

## LLM 调用点 4-6：未使用（已有 Processor 但 Orchestrator 未接入）

| 调用点 | Processor | System Prompt | 用途 | 当前状态 |
|--------|----------|---------------|------|---------|
| Topic 摘要决策 | `TopicSummaryDecisionProcessor` | `DEFAULT_TOPIC_SUMMARY_DECISION_PROMPT` | 判断是否需要重写 topic 总结区 | 未使用。Rebuilder 直接尝试重写，不先判断 |
| Topic 详情提取 | `TopicDetailProcessor` | `DEFAULT_TOPIC_DETAIL_PROMPT` | 提取新增细节到 topic-detail-index | 未使用。pipeline 未维护 topic-detail-index |
| 知识库修复 | `KnowledgeRepairProcessor`（不存在，但 prompt 已定义） | `DEFAULT_KNOWLEDGE_REPAIR_PROMPT` | 诊断/修复 graph 状态 | 未使用。`diagnose_knowledge_library.py` 不调 LLM，纯逻辑检测 |

---

## LLM 调用频次总结

处理 **1 个 inbox 文件**时的 LLM 调用次数：

| 调用点 | 次数 | 条件 |
|--------|------|------|
| 知识蒸馏 | 1 | body ≥ 80 字符或有 key_points/summary |
| 话题路径解析 | 1 | 蒸馏成功 |
| Topic 摘要蒸馏 | 0-N | N = 受影响的 topic 节点数（每个有关联笔记的节点调一次） |
| **总计** | **2 + N** | 弱信号跳过时为 0 次 |

**实际案例**：处理 3 个 inbox 文件，每个文件影响了 1 个 topic 节点 → 每文件 3 次 LLM 调用，共 9 次。耗时约 42-61 秒/文件（主要等待 LLM 响应）。

---

## `_parse_yaml_output` 解析策略

所有 LLM 调用的返回值都经过同一个解析函数：

```python
def _parse_yaml_output(text: str) -> dict:
    text = text.strip()
    # 1. 去除 markdown 代码围栏
    if text.startswith("```"):
        text = re.sub(r"^```(?:yaml|yml|json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        text = text.strip()
    # 2. 尝试 YAML 解析
    try:
        result = yaml.safe_load(text)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    # 3. 尝试 JSON 解析
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except Exception:
        pass
    # 4. 全部失败
    raise ValueError(f"LLM output is not valid YAML/JSON: {text[:200]}")
```

| 步骤 | 说明 |
|------|------|
| 去围栏 | LLM 经常在 YAML 前后加 ` ```yaml ` / ` ``` ` |
| YAML 优先 | `yaml.safe_load` 容错性好，能解析大多数格式 |
| JSON 兜底 | 部分 LLM 偶尔输出纯 JSON 而非 YAML |
| 失败 | 抛 `ValueError`，上层捕获后转 `failed` 状态 |

**已知局限**：
- LLM 在 YAML 中输出 `key_points: 不可能三角`（没有列表标记 ` - `）→ `yaml.safe_load` 解析为字符串而非 list → 下游校验失败
- LLM 在 mutation_proposals 中输出额外的解释性文字（prompt 说"不要输出解释性文字"但 LLM 不一定遵守）→ `yaml.safe_load` 可能解析为非 dict 的混合结构
