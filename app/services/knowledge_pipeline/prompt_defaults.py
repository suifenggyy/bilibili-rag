DEFAULT_TOPIC_PATH_PROMPT = """你会收到 source metadata、summary、key_points、body、existing topic graph、existing aliases。
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
规则：primary_path 必填；secondary_paths 不得重复 primary_path；create_leaf 必须用 `affected_unresolved_names` 表示待创建名称且 `affected_node_ids` 为空；add_alias 必须用 `affected_node_ids` 指向已有 canonical node，并用 `target_name` 表示待添加 alias 文本；不要输出解释性文字。"""

DEFAULT_KNOWLEDGE_NOTE_DISTILL_PROMPT = """你会收到 source summary、key_points、body。
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
要求：优先综合 key_points 与正文；quotes 最多 3 条；缺项返回空列表。"""

DEFAULT_TOPIC_SUMMARY_DECISION_PROMPT = """你会收到当前 topic summary、候选新增知识、最近一次 detail 增量。
判断是否需要重写 topic 总结区。
输出 YAML，且只能包含：
rewrite_summary: true|false
changed_facets:
  - "定义|框架|判断标准|风险边界|上下位关系"
reason: "一句话说明"
规则：只有 topic 的核心认知发生变化时才允许 rewrite_summary=true。"""

DEFAULT_TOPIC_SUMMARY_PROMPT = """你会收到某个 topic 的现有总结与所有聚合后的知识要点。
输出 markdown，总结区必须严格包含：
## 概览
## 核心框架
## 关键结论
## 上下位关系
可选补充：
## 适用边界
要求：写 topic 本身的稳定知识，不要写"某篇文章为什么属于这个 topic"。"""

DEFAULT_TOPIC_DETAIL_PROMPT = """你会收到 topic 当前 detail 指纹索引、候选新事实、支持来源。
输出 YAML，且只能包含：
detail_items:
  - statement: "新增且不重复的细节"
    detail_type: example|case|exception|quote|tactic
    supporting_source_note_paths:
      - "inbox/douyin/2026-06-03/example.md"
规则：只输出真正新增的细节；如果全部重复则返回 detail_items: []。"""

DEFAULT_KNOWLEDGE_REPAIR_PROMPT = """你会收到 graph snapshot、mapping snapshot、topic detail index、pending mutations、detected issues。
输出 YAML，且只能包含：
repair_actions:
  - action: rebuild_mapping|rebuild_topic_page|relink_note|remove_stale_topic_file|repair_parent_child|apply_pending_mutation|reject_pending_mutation
    target: "knowledge/投资/短线交易/做T/2026-06-03-example.md"
    pending_mutation_identity: "merge|n1,n2|replacement:n3"
    reason: "映射存在但 topic 页缺失"
manual_review_items:
  - issue: "merge proposal between 做T and 日内回转 remains ambiguous"
    reason: "缺少足够独立来源支撑自动决策"
规则：不要自动决定 merge/split/move 等语义性结构变更；只有输入明确要求 apply/reject 某个 pending mutation 时，才能输出 apply_pending_mutation / reject_pending_mutation，否则进入 manual_review_items。若无动作，返回 repair_actions: [] 和 manual_review_items: []。"""
