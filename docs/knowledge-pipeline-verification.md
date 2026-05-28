# 知识库流水线验证使用说明

> 文档对应实现分支：`main`（commits `9960517` → `e8aa8a1`）  
> 计划文档：`docs/superpowers/plans/2026-05-28-knowledge-base-pipeline-integration.md`

---

## 目录

1. [环境准备](#1-环境准备)
2. [核心组件说明](#2-核心组件说明)
3. [逐阶段验证](#3-逐阶段验证)
   - [阶段 1：Vault 配置（Task 1）](#阶段-1vault-配置task-1)
   - [阶段 2：Inbox Frontmatter（Task 2）](#阶段-2inbox-frontmattertask-2)
   - [阶段 3：历史存档导入（Task 3）](#阶段-3历史存档导入task-3)
   - [阶段 4：文档解析 & 类目图（Task 4）](#阶段-4文档解析--类目图task-4)
   - [阶段 5：分类器（Task 5）](#阶段-5分类器task-5)
   - [阶段 6：归档器 & Topic 更新器（Task 6）](#阶段-6归档器--topic-更新器task-6)
   - [阶段 7：Orchestrator & Watcher（Task 7）](#阶段-7orchestrator--watchertask-7)
   - [阶段 8：日报生成器（Task 8）](#阶段-8日报生成器task-8)
4. [端到端 Smoke Test](#4-端到端-smoke-test)
5. [常见问题](#5-常见问题)

---

## 1. 环境准备

### 1.1 复制并填写 .env

```bash
cp .env.example .env
```

在 `.env` 中填写以下必要字段：

```env
# Obsidian vault 根目录（绝对路径）
OBSIDIAN_VAULT_ROOT=/Users/yourname/obsidian/MyVault

# LLM 相关（分类器需要）
OPENAI_API_KEY=sk-xxxx          # 或 DashScope key
OPENAI_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen3-max

# 可选：Tavily（日报外部信号）
TAVILY_API_KEY=tvly-xxxx

# 可选：Obsidian Local REST API
OBSIDIAN_LOCAL_REST_URL=http://localhost:27123
OBSIDIAN_LOCAL_REST_API_KEY=your-rest-key
```

### 1.2 安装 Python 依赖

```bash
uv pip install -r requirements.txt
# 如需监听功能：
uv pip install watchdog
# 如需 Tavily：
uv pip install tavily-python
```

### 1.3 运行测试基线

```bash
python -m unittest discover -s test -p 'test_*.py' 2>&1 | tail -3
# 期望: OK (或只有1个预存在的非相关失败)
```

---

## 2. 核心组件说明

| 组件 | 文件 | 职责 |
|---|---|---|
| 配置 | `app/config.py` | `Settings` 提供 10 个 vault 相关字段 |
| 路径助手 | `app/services/content_storage.py` | `get_inbox_dir()`, `get_knowledge_dir()` 等 |
| Frontmatter | `app/services/knowledge_pipeline/frontmatter.py` | 为导出的 Markdown 注入 YAML 头 |
| 历史导入 | `app/services/knowledge_pipeline/legacy_import.py` | 从旧 `collection/` 导入到 inbox |
| 解析器 | `app/services/knowledge_pipeline/parser.py` | 解析 YAML frontmatter，提取结构化字段 |
| 类目图 | `app/services/knowledge_pipeline/category_map.py` | 维护 `_meta/category-map.json` |
| 分类器 | `app/services/knowledge_pipeline/classifier.py` | LLM 分类 → `ClassificationResult` |
| 归档器 | `app/services/knowledge_pipeline/archiver.py` | 写入 `knowledge/<category>/` |
| Obsidian 写入 | `app/services/knowledge_pipeline/obsidian_client.py` | REST API 优先，回退文件系统 |
| Topic 更新器 | `app/services/knowledge_pipeline/topic_updater.py` | 维护 `knowledge/_topics/<topic>.md` |
| 处理日志 | `app/services/knowledge_pipeline/processing_logger.py` | 写入 `_meta/logs/YYYY-MM-DD.log` |
| Orchestrator | `app/services/knowledge_pipeline/orchestrator.py` | 串联完整流水线 |
| Watcher | `app/services/knowledge_pipeline/watcher.py` | Watchdog 监听 inbox 目录 |
| 日报器 | `app/services/knowledge_pipeline/daily_reporter.py` | 生成 `daily/YYYY-MM-DD.md` |

---

## 3. 逐阶段验证

### 阶段 1：Vault 配置（Task 1）

```bash
python -m unittest discover -s test -p 'test_knowledge_pipeline_config.py' -v
```

**期望输出：**
```
test_settings_expose_obsidian_and_tavily_fields ... ok
test_content_storage_vault_path_helpers ... ok
...
Ran 4 tests in X.XXs OK
```

**手动验证：**
```python
from app.config import settings
print(settings.obsidian_vault_root)      # 你设置的路径
print(settings.obsidian_inbox_dir)       # "inbox"（默认）
print(settings.tavily_api_key)           # 你设置的 key
```

---

### 阶段 2：Inbox Frontmatter（Task 2）

```bash
python -m unittest discover -s test -p 'test_export_inbox_frontmatter.py' -v
```

**期望：** 8 tests OK

**手动验证（B站导出为例）：**
```bash
python scripts/export_favorites_to_md.py --limit 1
# 查看 inbox/ 中输出文件的头部
head -10 "$OBSIDIAN_VAULT_ROOT/inbox/*.md"
```

期望看到：
```yaml
---
title: 视频标题
date: 2026-05-28
source: https://www.bilibili.com/video/BVxxx
platform: bilibili
summary: ...
---
```

---

### 阶段 3：历史存档导入（Task 3）

```bash
python -m unittest discover -s test -p 'test_legacy_collection_import.py' -v
```

**期望：** 4 tests OK

**手动导入（如有旧存档）：**
```bash
# 查看可用的 sources
python scripts/import_collection_to_inbox.py --list-sources

# 导入测试（dry-run）
python scripts/import_collection_to_inbox.py --sources bilibili --limit 3 --dry-run

# 正式导入
python scripts/import_collection_to_inbox.py --sources bilibili,instapaper
```

**期望输出：**
```
[LegacyImport] 找到 12 个历史文件 (bilibili)
[LegacyImport] 已导入: 2026-05-01-xxx.md
...
完成: 导入 12 个，跳过 0 个重复
```

---

### 阶段 4：文档解析 & 类目图（Task 4）

```bash
python -m unittest discover -s test -p 'test_knowledge_parser.py' -v
```

**期望：** 9 tests OK

**手动验证（Python REPL）：**
```python
from app.services.knowledge_pipeline.parser import KnowledgeMarkdownParser
from pathlib import Path

parser = KnowledgeMarkdownParser()
# 用 inbox 中的任意文件
doc = parser.parse_file(Path("vault/inbox/your-article.md"))
print(doc.title)
print(doc.date_str)
print(doc.summary[:100])
```

---

### 阶段 5：分类器（Task 5）

```bash
python -m unittest discover -s test -p 'test_knowledge_classifier.py' -v
```

**期望：** 4 tests OK（含 mock LLM）

**手动验证（需要配置 LLM API）：**
```python
import asyncio
from app.services.knowledge_pipeline.classifier import KnowledgeClassifier

async def test():
    clf = KnowledgeClassifier()
    result = await clf.classify(
        title="GPT-4 多模态能力评测",
        summary="本文评测了 GPT-4 的图文理解能力……",
        existing_categories=["AI与技术", "编程开发", "产品设计"],
    )
    print(result.category)       # 期望: "AI与技术"
    print(result.topics)         # 期望: ["LLM", "多模态", ...]
    print(result.quality_score)  # 期望: 0.0~1.0

asyncio.run(test())
```

---

### 阶段 6：归档器 & Topic 更新器（Task 6）

```bash
python -m unittest discover -s test -p 'test_knowledge_archiver.py' -v
python -m unittest discover -s test -p 'test_topic_updater.py' -v
```

**期望：** 4 + 1 = 5 tests OK

**手动验证：**
```python
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from app.services.knowledge_pipeline.archiver import KnowledgeArchiver
from app.services.knowledge_pipeline.classifier import ClassificationResult

# 准备测试文件
with TemporaryDirectory() as tmpdir:
    tmp = Path(tmpdir)
    inbox_file = tmp / "inbox" / "2026-05-28-test.md"
    inbox_file.parent.mkdir()
    inbox_file.write_text("---\ntitle: 测试\ndate: 2026-05-28\nsource: https://x.com\nsummary: 测试摘要\n---\n\n# 测试\n\n正文")
    
    result = ClassificationResult(
        category="AI与技术", topics=["LLM"], quality_score=0.85, processing_log="测试日志"
    )
    archiver = KnowledgeArchiver(knowledge_dir=tmp / "knowledge")
    archive_path = archiver.archive(inbox_file, ...)
    print(f"归档到: {archive_path}")
```

---

### 阶段 7：Orchestrator & Watcher（Task 7）

```bash
python -m unittest discover -s test -p 'test_knowledge_pipeline_orchestrator.py' -v
```

**期望：** 3 tests OK

**一次性处理 inbox：**
```bash
# 查看 inbox 文件列表（不执行处理）
python scripts/run_knowledge_pipeline.py --skip-pipeline

# 处理最多 5 个文件
python scripts/run_knowledge_pipeline.py --limit 5

# 处理单个文件
python scripts/run_knowledge_pipeline.py --file vault/inbox/2026-05-28-article.md
```

**持续监听模式：**
```bash
# 需要先安装: uv pip install watchdog
python scripts/run_knowledge_pipeline.py --watch
# 向 inbox 目录复制 .md 文件后可看到自动处理输出
# Ctrl+C 停止
```

**处理完成后检查：**
```bash
# 查看归档结果
ls vault/knowledge/*/

# 查看 topic 索引页
ls vault/knowledge/_topics/

# 查看处理日志
cat vault/_meta/logs/$(date +%Y-%m-%d).log
```

---

### 阶段 8：日报生成器（Task 8）

```bash
python -m unittest discover -s test -p 'test_daily_reporter.py' -v
```

**期望：** 3 tests OK

**生成今日日报：**
```bash
python scripts/generate_daily_report.py
# 输出: ✅ 日报已保存: vault/daily/2026-05-28.md
```

**在终端预览（不写文件）：**
```bash
python scripts/generate_daily_report.py --print
```

**期望日报结构：**
```markdown
# 知识库日报 2026-05-28

> 生成时间：2026-05-28  |  今日新增：5 篇

## 重点关注

- **AI大模型**  score=8.2  今日+3  近3天+5  近14天+12  avg_quality=0.82
- **编程开发**  score=4.1  ...

## 今日新增

- [GPT-4 多模态能力评测](https://...) — AI与技术 ★0.9
- ...

## 近期趋势

- AI大模型（近3天 +5）
- ...

## 待关注信号

_（未配置 Tavily API Key，跳过外部信号）_
```

---

## 4. 端到端 Smoke Test

运行完整流水线验证（确保 `.env` 已配置 `OBSIDIAN_VAULT_ROOT`）：

```bash
# Step 1: 运行所有流水线相关测试（共 27 个）
python -m unittest discover -s test -p 'test_knowledge*.py' -v
python -m unittest discover -s test -p 'test_daily*.py' -v
python -m unittest discover -s test -p 'test_export_inbox*.py' -v
python -m unittest discover -s test -p 'test_legacy*.py' -v

# Step 2: 如有历史存档，导入前 2 条做测试
python scripts/import_collection_to_inbox.py --sources instapaper --limit 2

# Step 3: 处理 inbox 前 2 条
python scripts/run_knowledge_pipeline.py --limit 2

# Step 4: 生成今日日报
python scripts/generate_daily_report.py --print

# Step 5: 查看生成文件
find "${OBSIDIAN_VAULT_ROOT}" -newer .env -name "*.md" | head -20
```

---

## 5. 常见问题

### Q: `OBSIDIAN_VAULT_ROOT` 未设置时怎么办？

默认使用 `data/vault/` 目录（在项目根目录下自动创建）。可以用于本地测试，无需真实 Obsidian vault。

### Q: 分类器返回"未分类"

检查：
1. `OPENAI_API_KEY` 和 `OPENAI_API_BASE` 是否配置正确
2. 网络是否可访问 API endpoint
3. 查看日志：`tail -f logs/app.log`

### Q: Tavily 外部信号为空

仅当 `TAVILY_API_KEY` 配置且不为空时才查询外部信号。默认关闭，不影响日报生成。

### Q: watchdog 监听不到文件变化

某些 macOS 版本需要：
```bash
uv pip install watchdog[watchmedo]
```

### Q: Obsidian REST API 连接失败

`ObsidianWriter` 会自动回退到直接文件系统写入，不会中断流水线。需要安装 [Obsidian Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api) 插件并启动后才能使用 REST 模式。

---

## 提交历史

| commit | 内容 |
|---|---|
| `9960517` | feat: emit inbox frontmatter from export scripts |
| `d4f46e5` | feat: add legacy collection inbox importer |
| `553d367` | feat: add knowledge markdown parser |
| `04f14d6` | feat: add knowledge classifier |
| `3ad11c8` | feat: add archiver and topic updater |
| `6d37a83` | feat: add pipeline orchestrator and watcher |
| `a3ef49a` | feat: add knowledge daily reporter |
| `e8aa8a1` | docs: document knowledge pipeline workflow |
