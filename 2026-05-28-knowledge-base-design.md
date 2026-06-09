# 个人知识库自动化系统设计文档

**日期：** 2026-05-28  
**状态：** 已审核

---

## 一、项目目标

构建一套全自动的个人知识库系统，将定期抓取的 `.md` 格式内容自动整理入 Obsidian vault，实现：

- 自动分类与主题归并
- 动态维护两层结构（独立文章 + 主题汇总页）
- 每日生成综合性知识动态日报
- 兼顾人工阅读体验与大模型输入需求

---

## 二、整体架构

```
抓取任务 → inbox/ → Python流水线 → Obsidian Vault
                         │
                    LLM API (分类/提炼/日报)
                    Tavily API (外部热度)
                    Local REST API (写入Obsidian)
```

**核心组件：**

| 组件 | 职责 |
|------|------|
| Watcher | 监控 inbox/ 目录，触发处理流程 |
| Parser | 解析 frontmatter 与正文 |
| Classifier | LLM 判断分类和 topic |
| Archiver | 归档文件，补写 frontmatter |
| TopicUpdater | 创建/更新主题汇总页 |
| Logger | 写入处理日志 |
| DailyReporter | 每日生成综合日报 |

**Obsidian 插件配套：**

| 插件 | 用途 |
|------|------|
| Dataview | 主题汇总页动态列出相关文章 |
| Local REST API | Python 脚本通过 API 写入，避免直接操作文件系统 |
| Breadcrumbs | 维护分类层次导航 |
| Folder Notes | 每个分类目录的索引页 |

---

## 三、Vault 目录结构

```
vault/
├── inbox/                          # 抓取任务投放原始文件的唯一入口
│   └── failed/                     # 处理失败的文件，保留原文待人工重试
├── knowledge/
│   ├── _topics/                    # 主题汇总页（每个 topic 一个文件）
│   │   ├── AI大模型.md
│   │   └── ...
│   ├── AI与技术/                   # LLM 自动判断的分类目录
│   │   ├── _index.md               # Folder Note，Dataview 查询本目录所有文章
│   │   └── 2026-05-28-文章标题.md
│   ├── 商业与投资/
│   │   ├── _index.md
│   │   └── ...
│   └── 未分类/                     # LLM 调用失败时的兜底目录
│       └── _index.md
├── daily/
│   └── 2026-05-28.md               # 每日综合知识动态日报
└── _meta/
    ├── category-map.json           # 分类名称→目录路径映射表，防止重复造分类
    └── logs/
        └── 2026-05-28.log          # 每日处理日志
```

---

## 四、文章 Frontmatter 规范

抓取任务产出的原始 frontmatter（已有字段）：

```yaml
title: 文章标题
date: 2026-05-28
source: https://example.com
summary: 大模型生成的摘要
```

流水线处理后补写的字段：

```yaml
category: AI与技术          # 主分类，唯一，决定存放目录
topics:                     # 所属 topic，可多个
  - AI大模型
  - Prompt工程
processed_at: 2026-05-28T23:00:00
quality_score: 0.85         # LLM 对内容质量的评分（0~1），用于日报排序
processing_log: "分类依据：摘要提及GPT-4o与Prompt优化；topics来自category-map现有条目"
```

---

## 五、处理流水线

### 5.1 单篇文章处理（实时触发）

```
inbox/ 新文件到达
        │
        ▼
1. Parser      读取 frontmatter + 正文，提取 title / summary
        │
        ▼
2. Classifier  LLM 输入：title + summary + 现有分类列表（来自 category-map.json）
               LLM 输出：category（尽量复用已有分类）+ topics[]（可新增）
               新 category/topic 写入 category-map.json
        │
        ▼
3. Archiver    补写 frontmatter，文件重命名为 YYYY-MM-DD-slug.md
               通过 Local REST API 写入 knowledge/<category>/
        │
        ▼
4. TopicUpdater 对每个 topic：
               - topic 页不存在则创建（含 Dataview 查询块）
               - LLM 从本文提炼新观点，追加到"核心观点"区块（带时间戳）
        │
        ▼
5. Logger      写入 _meta/logs/YYYY-MM-DD.log
               记录：文件名、分类结果、topics、耗时、是否出错
```

### 5.2 错误处理

- 单篇处理失败不中断流程，记录错误后跳过
- 失败文件移入 `inbox/failed/`，保留原文
- LLM 调用超时重试 3 次，仍失败则归入 `未分类/`
- Local REST API 写入失败则回退为直接写文件系统

---

## 六、主题汇总页结构

每个 `_topics/<TopicName>.md` 的格式：

```markdown
# AI大模型

## 核心观点
<!-- 脚本追加区，旧观点保留，每次更新加时间戳 -->
**[2026-05-28 更新]** GPT-4o 在多模态任务上...
**[2026-05-20 更新]** 开源模型在推理成本上...

## 相关文章
```dataview
LIST FROM "knowledge" WHERE contains(topics, "AI大模型") SORT date DESC
```
```

**更新规则：**
- 核心观点区只追加，不覆盖，完整保留演进脉络
- 每次新文章归入该 topic 时触发一次观点追加
- Dataview 块自动维护文章列表，无需脚本干预

---

## 七、日报生成机制

### 7.1 触发时间

每日 23:00 定时执行。

### 7.2 内容采集与权重

| 时间范围 | 权重 |
|----------|------|
| 今日新增 | 1.0 |
| 近3天新增 | 0.7 |
| 更早但 topic 仍活跃 | 0.3 |

> **"仍活跃"定义：** 该 topic 在过去 14 天内累计有 3 篇以上文章，或 Tavily 外部热度评分高于阈值（0.5）。

### 7.3 Topic 热度评分

对每个活跃 topic 综合打分：

- **内部信号：** 近期文章数量 + 文章 quality_score 均值
- **外部信号：** 调用 Tavily API 搜索该 topic，获取近期网络热度和相关新闻数量
- 综合排序后交给 LLM 判断最终关注优先级

### 7.4 日报结构

```markdown
# 知识库日报 2026-05-28

## 重点关注
<!-- LLM综合内外部信号判断，不限时间，持续值得关注的内容 -->

### AI Agent 工程化落地
- 外部热度：高（近3天相关新闻47篇）
- 知识库积累：8篇文章，最新：今日
- 核心动态：（LLM提炼2-3条关键进展）

### 开源模型成本竞争
- 外部热度：中
- 知识库积累：3篇文章，最新：2天前
- 核心动态：（LLM提炼）

## 今日新增
<!-- 今天入库的所有文章，含标题、分类、一句话摘要 -->

## 近期趋势
<!-- 过去7天哪些topic在持续升温，文章数量变化 -->

## 待关注信号
<!-- Tavily发现外部热度高但知识库文章少的topic，提示补充采集 -->
```

"待关注信号"形成闭环：系统主动发现知识盲区，反向引导抓取任务补充采集。

---

## 八、日志格式

`_meta/logs/YYYY-MM-DD.log` 每行一条记录：

```
2026-05-28T14:23:01 [INFO]  处理文件: gpt4o-multimodal.md
2026-05-28T14:23:03 [INFO]  分类: AI与技术 | topics: [AI大模型, 多模态]
2026-05-28T14:23:03 [INFO]  quality_score: 0.87 | 耗时: 2.1s
2026-05-28T14:23:04 [INFO]  TopicUpdater: AI大模型 观点已追加
2026-05-28T14:30:11 [ERROR] 处理文件: broken-article.md | 原因: frontmatter解析失败
2026-05-28T14:30:11 [INFO]  已移入 inbox/failed/
```

---

## 九、技术栈

| 层次 | 技术选型 |
|------|----------|
| 文件监控 | Python `watchdog` |
| LLM 调用 | OpenAI API（或兼容接口） |
| 外部热度 | Tavily Search API |
| Obsidian 写入 | Local REST API 插件 |
| 定时任务 | macOS `launchd` 或 `cron` |
| 语言 | Python 3.11+ |

---

## 十、扩展说明

- **category-map.json** 是防止分类爆炸的关键：LLM 分类时优先从已有列表选择，只有在明显不适合时才新增分类
- **quality_score** 字段为后续升级向量检索预留接口，当前用于日报排序
- **inbox/failed/** 下的文件支持人工修正后重新放回 inbox/ 触发重处理
- 未来可在"待关注信号"基础上，直接向抓取任务写入新的采集指令，实现全自动闭环
