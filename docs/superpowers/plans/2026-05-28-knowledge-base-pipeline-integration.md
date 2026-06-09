# Knowledge Base Pipeline Integration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有各抓取脚本的 Markdown 导出流程改为直接进入 Obsidian `vault/inbox/`，并在脚本执行完成后自动触发知识库处理流水线，完成分类归档、主题页更新、日志记录、历史补导和日报生成。

**Architecture:** 保留现有“各平台抓取 + ASR/正文提取”的前半段能力，把“落地 Markdown”与“知识库归档”拆成两个明确阶段。抓取脚本只负责产出带标准 frontmatter 的 inbox 文件并回传新文件列表；统一的 `knowledge_pipeline` 服务负责解析、分类、归档、Topic 更新、日志和日报，支持“脚本直接调用”与“watchdog 目录监听”两种触发方式。

**Tech Stack:** Python 3.11+, FastAPI existing services/config/models, loguru, unittest, watchdog, OpenAI-compatible LLM API, Tavily API, Obsidian Local REST API + direct filesystem fallback

---

## Confirmed Product Decisions

- 导出脚本的正式落点改为 `vault/inbox/`，原 `collection/` 目录不再作为日常写入目标。
- 导出脚本负责生成标准 YAML frontmatter，至少包含 `title` / `date` / `source` / `summary`。
- 知识库写入优先走 Obsidian Local REST API，失败后回退到直接写文件系统。
- 触发方式同时支持：
  - 导出脚本结束后直接调用 pipeline
  - 可选 `watchdog` 常驻监听 `inbox/`
- 历史 `collection/` 文件通过一次性导入工具补迁移。
- Tavily API Key 后续配置到 `.env`，字段名定为 `TAVILY_API_KEY`，并在 `app/config.py` 增加对应设置项。

## File Structure

### Existing files to modify

- `.env.example` — 增加 vault / inbox / Obsidian Local REST / Tavily 配置项，标记 `COLLECTION_OUTPUT_DIR` 为废弃迁移项
- `README.md` — 更新导出路径、运行方式、知识库流水线说明
- `app/config.py` — 增加 vault/pipeline/Tavily/Obsidian 配置字段
- `app/models.py` — 增加 inbox/归档处理状态模型
- `app/services/content_storage.py` — 新增 inbox 路径与知识库路径 helper，保留 legacy import 所需旧目录 helper
- `scripts/export_favorites_to_md.py` — 输出 frontmatter，改写入 inbox，成功后直接触发 pipeline
- `scripts/export_douyin_to_md.py` — 同上
- `scripts/export_instapaper_to_md.py` — 同上
- `scripts/export_xiaoyuzhou_to_md.py` — 同上
- `scripts/export_youtube_to_md.py` — 同上

### New service package

- `app/services/knowledge_pipeline/__init__.py` — 导出 orchestrator 与公共常量
- `app/services/knowledge_pipeline/frontmatter.py` — frontmatter 构建/解析、summary 提取、slug 生成
- `app/services/knowledge_pipeline/obsidian_client.py` — Local REST API 写入封装 + filesystem fallback
- `app/services/knowledge_pipeline/category_map.py` — `_meta/category-map.json` 的读写与分类复用逻辑
- `app/services/knowledge_pipeline/parser.py` — 解析 inbox Markdown，抽取 frontmatter + 正文
- `app/services/knowledge_pipeline/classifier.py` — LLM 分类、topics、quality_score、processing_log 输出
- `app/services/knowledge_pipeline/archiver.py` — 生成归档路径、补写处理后 frontmatter、移动到 `knowledge/<category>/`
- `app/services/knowledge_pipeline/topic_updater.py` — 创建/更新 `_topics/<topic>.md`
- `app/services/knowledge_pipeline/processing_logger.py` — 维护 `_meta/logs/YYYY-MM-DD.log`
- `app/services/knowledge_pipeline/orchestrator.py` — 单文件/批量文件处理总入口
- `app/services/knowledge_pipeline/watcher.py` — `watchdog` 监听 `inbox/`
- `app/services/knowledge_pipeline/daily_reporter.py` — 生成 `daily/YYYY-MM-DD.md`
- `app/services/knowledge_pipeline/legacy_import.py` — 将历史 `collection/` Markdown 迁移为 inbox 待处理文件

### New scripts

- `scripts/run_knowledge_pipeline.py` — 手动处理 inbox / 启动 watcher
- `scripts/import_collection_to_inbox.py` — 一次性导入历史 `collection/` 文件
- `scripts/generate_daily_report.py` — 手动/定时生成日报

### Tests to add

- `test/test_knowledge_pipeline_config.py`
- `test/test_export_inbox_frontmatter.py`
- `test/test_legacy_collection_import.py`
- `test/test_knowledge_parser.py`
- `test/test_knowledge_classifier.py`
- `test/test_knowledge_archiver.py`
- `test/test_topic_updater.py`
- `test/test_knowledge_pipeline_orchestrator.py`
- `test/test_daily_reporter.py`

---

## Chunk 1: Export Contract and Vault Path Migration

> Use @test-driven-development. Finish this chunk before moving on to pipeline internals.

### Task 1: Lock the new vault and pipeline configuration contract

**Files:**
- Modify: `.env.example`
- Modify: `app/config.py`
- Modify: `app/services/content_storage.py`
- Modify: `README.md`
- Test: `test/test_knowledge_pipeline_config.py`

- [ ] **Step 1: Write the failing config test**

```python
import unittest


class KnowledgePipelineConfigTests(unittest.TestCase):
    def test_settings_expose_obsidian_and_tavily_fields(self):
        from app.config import Settings

        settings = Settings(
            OBSIDIAN_VAULT_ROOT="/tmp/vault",
            OBSIDIAN_INBOX_DIR="inbox",
            OBSIDIAN_LOCAL_REST_URL="http://127.0.0.1:27124",
            OBSIDIAN_LOCAL_REST_API_KEY="token",
            TAVILY_API_KEY="tvly-test",
        )

        self.assertEqual(settings.obsidian_vault_root, "/tmp/vault")
        self.assertEqual(settings.obsidian_inbox_dir, "inbox")
        self.assertEqual(settings.obsidian_local_rest_url, "http://127.0.0.1:27124")
        self.assertEqual(settings.tavily_api_key, "tvly-test")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest test.test_knowledge_pipeline_config -v`
Expected: FAIL with missing `Settings` fields such as `obsidian_vault_root` / `tavily_api_key`

- [ ] **Step 3: Implement minimal configuration and storage helpers**

```python
class Settings(BaseSettings):
    obsidian_vault_root: str = Field(default="~/Obsidian/jarvis", env="OBSIDIAN_VAULT_ROOT")
    obsidian_inbox_dir: str = Field(default="inbox", env="OBSIDIAN_INBOX_DIR")
    obsidian_knowledge_dir: str = Field(default="knowledge", env="OBSIDIAN_KNOWLEDGE_DIR")
    obsidian_topics_dir: str = Field(default="knowledge/_topics", env="OBSIDIAN_TOPICS_DIR")
    obsidian_daily_dir: str = Field(default="daily", env="OBSIDIAN_DAILY_DIR")
    obsidian_meta_dir: str = Field(default="_meta", env="OBSIDIAN_META_DIR")
    obsidian_local_rest_url: str = Field(default="http://127.0.0.1:27124", env="OBSIDIAN_LOCAL_REST_URL")
    obsidian_local_rest_api_key: str = Field(default="", env="OBSIDIAN_LOCAL_REST_API_KEY")
    tavily_api_key: str = Field(default="", env="TAVILY_API_KEY")
```

```python
class ContentStorageManager:
    def get_vault_root(self) -> Path: ...
    def get_inbox_dir(self) -> Path: ...
    def get_failed_inbox_dir(self) -> Path: ...
    def get_knowledge_dir(self) -> Path: ...
    def get_topics_dir(self) -> Path: ...
    def get_daily_dir(self) -> Path: ...
    def get_meta_dir(self) -> Path: ...
    def get_legacy_collection_dir(self, source: str, day: Optional[date] = None) -> Path: ...
```

- [ ] **Step 4: Update `.env.example` and README contract**

Include:

```env
OBSIDIAN_VAULT_ROOT=/Users/gongyongyue/FangcloudV2/personal_space.localized/同步空间/个人资料/Obsidian/jarvis
OBSIDIAN_INBOX_DIR=inbox
OBSIDIAN_KNOWLEDGE_DIR=knowledge
OBSIDIAN_DAILY_DIR=daily
OBSIDIAN_META_DIR=_meta
OBSIDIAN_LOCAL_REST_URL=http://127.0.0.1:27124
OBSIDIAN_LOCAL_REST_API_KEY=
TAVILY_API_KEY=
COLLECTION_OUTPUT_DIR=... # deprecated, legacy import only
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest test.test_knowledge_pipeline_config -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add .env.example README.md app/config.py app/services/content_storage.py test/test_knowledge_pipeline_config.py
git commit -m "feat: add knowledge pipeline vault config"
```

### Task 2: Standardize inbox Markdown frontmatter across all export scripts

**Files:**
- Create: `app/services/knowledge_pipeline/frontmatter.py`
- Modify: `scripts/export_favorites_to_md.py`
- Modify: `scripts/export_douyin_to_md.py`
- Modify: `scripts/export_instapaper_to_md.py`
- Modify: `scripts/export_xiaoyuzhou_to_md.py`
- Modify: `scripts/export_youtube_to_md.py`
- Test: `test/test_export_inbox_frontmatter.py`

- [ ] **Step 1: Write the failing export/frontmatter tests**

```python
import unittest


class ExportInboxFrontmatterTests(unittest.TestCase):
    def test_build_frontmatter_contains_required_fields(self):
        from app.services.knowledge_pipeline.frontmatter import build_export_frontmatter

        frontmatter = build_export_frontmatter(
            title="文章标题",
            date_str="2026-05-28",
            source="https://example.com/1",
            summary="一句摘要",
        )

        self.assertTrue(frontmatter.startswith("---\n"))
        self.assertIn("title: 文章标题", frontmatter)
        self.assertIn("date: 2026-05-28", frontmatter)
        self.assertIn("source: https://example.com/1", frontmatter)
        self.assertIn("summary: 一句摘要", frontmatter)
```

```python
    def test_bilibili_export_markdown_places_frontmatter_before_body(self):
        from scripts.export_favorites_to_md import _build_markdown

        markdown = _build_markdown(video={...}, asr_text="正文", source="asr", folder_title="收藏夹", summary_block="")
        self.assertTrue(markdown.startswith("---\n"))
        self.assertIn("\n# ", markdown)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest test.test_export_inbox_frontmatter -v`
Expected: FAIL because export scripts still start with `# 标题` and no shared frontmatter helper exists

- [ ] **Step 3: Implement the shared frontmatter builder**

```python
def build_export_frontmatter(*, title: str, date_str: str, source: str, summary: str) -> str:
    safe_summary = normalize_single_line(summary)
    return "\n".join(
        [
            "---",
            f"title: {yaml_scalar(title)}",
            f"date: {date_str}",
            f"source: {yaml_scalar(source)}",
            f"summary: {yaml_scalar(safe_summary)}",
            "---",
            "",
        ]
    )
```

```python
def extract_plain_summary(summary_block: str) -> str:
    # 从现有 AI_SUMMARY YAML 中优先提取 summary 字段；没有则返回空字符串
```

- [ ] **Step 4: Modify each exporter to write inbox-compatible Markdown**

Required output shape:

```markdown
---
title: 视频标题
date: 2026-05-28
source: https://www.bilibili.com/video/BV...
summary: 一段摘要
---

# 视频标题

## 视频信息
...
```

Implementation rules:

- `title`: 原始标题
- `date`: 内容发布时间；没有发布时间时退化为导出日期
- `source`: 原始内容 URL
- `summary`: 从现有 `summary_block` 中抽取纯文本 summary；无 summary 时写空字符串
- 保留现有正文结构、AI总结区块、转写内容区块

- [ ] **Step 5: Keep script helper names stable**

Do not rename public CLI entrypoints. Only change:
- default write target from `collection/<source>/<date>/` to `vault/inbox/`
- return value so callers can collect `written_paths: list[Path]`

- [ ] **Step 6: Run focused tests**

Run: `python -m unittest test.test_export_inbox_frontmatter -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/services/knowledge_pipeline/frontmatter.py scripts/export_favorites_to_md.py scripts/export_douyin_to_md.py scripts/export_instapaper_to_md.py scripts/export_xiaoyuzhou_to_md.py scripts/export_youtube_to_md.py test/test_export_inbox_frontmatter.py
git commit -m "feat: emit inbox frontmatter from export scripts"
```

### Task 3: Add legacy collection import path and idempotent inbox record tracking

**Files:**
- Modify: `app/models.py`
- Create: `app/services/knowledge_pipeline/legacy_import.py`
- Create: `scripts/import_collection_to_inbox.py`
- Test: `test/test_legacy_collection_import.py`

- [ ] **Step 1: Write the failing legacy import tests**

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


class LegacyCollectionImportTests(unittest.TestCase):
    def test_importer_copies_existing_collection_markdown_into_inbox(self):
        ...
        self.assertEqual(imported_count, 2)
        self.assertTrue((inbox_dir / "2026-05-24-title.md").exists())
```

```python
    def test_importer_skips_duplicates_using_source_hash(self):
        ...
        self.assertEqual(skipped_count, 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest test.test_legacy_collection_import -v`
Expected: FAIL because no importer and no inbox tracking model exist

- [ ] **Step 3: Add a dedicated processing record model**

Add to `app/models.py`:

```python
class InboxEntry(Base):
    __tablename__ = "inbox_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_platform = Column(String(30), nullable=False)
    source_identifier = Column(String(300), nullable=False)
    inbox_path = Column(String(1000), nullable=False)
    archive_path = Column(String(1000), nullable=True)
    content_hash = Column(String(64), nullable=False)
    status = Column(String(30), default="pending")  # pending/running/completed/failed
    category = Column(String(200), nullable=True)
    topics_json = Column(JSON, nullable=True)
    quality_score = Column(String(20), nullable=True)
    error_message = Column(Text, nullable=True)
    processed_at = Column(DateTime, nullable=True)
```

- [ ] **Step 4: Implement the historical import CLI**

```python
class LegacyCollectionImporter:
    def import_all(self) -> ImportResult:
        # 扫描 legacy collection 子目录
        # 读取旧 Markdown
        # 补 frontmatter（如果旧文件没有）
        # 复制到 inbox
        # 记录 hash，避免重复导入
```

CLI contract:

```bash
python scripts/import_collection_to_inbox.py --sources bilibili douyin instapaper
python scripts/import_collection_to_inbox.py --all
```

- [ ] **Step 5: Run focused tests**

Run: `python -m unittest test.test_legacy_collection_import -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/models.py app/services/knowledge_pipeline/legacy_import.py scripts/import_collection_to_inbox.py test/test_legacy_collection_import.py
git commit -m "feat: add legacy collection inbox importer"
```

---

## Chunk 2: Knowledge Pipeline Runtime

> Use @test-driven-development. Keep classification, archiving, and topic writing in separate files with single responsibilities.

### Task 4: Build the parser and category-map persistence layer

**Files:**
- Create: `app/services/knowledge_pipeline/parser.py`
- Create: `app/services/knowledge_pipeline/category_map.py`
- Test: `test/test_knowledge_parser.py`

- [ ] **Step 1: Write the failing parser tests**

```python
import unittest


class KnowledgeParserTests(unittest.TestCase):
    def test_parser_reads_frontmatter_and_body(self):
        markdown = """---
title: 标题
date: 2026-05-28
source: https://example.com
summary: 摘要
---

# 标题

正文
"""
        from app.services.knowledge_pipeline.parser import KnowledgeMarkdownParser

        article = KnowledgeMarkdownParser().parse_text(markdown)
        self.assertEqual(article.title, "标题")
        self.assertEqual(article.summary, "摘要")
        self.assertEqual(article.body.strip(), "# 标题\n\n正文")
```

```python
    def test_category_map_prefers_existing_category_names(self):
        from app.services.knowledge_pipeline.category_map import CategoryMapRepository
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest test.test_knowledge_parser -v`
Expected: FAIL because parser and category-map repository do not exist

- [ ] **Step 3: Implement parser data contract**

```python
@dataclass
class ParsedKnowledgeDocument:
    title: str
    date_str: str
    source_url: str
    summary: str
    body: str
    raw_frontmatter: dict[str, Any]
```

Validation rules:

- frontmatter 缺失时抛出明确异常
- `title` / `date` / `source` 缺失时抛出明确异常
- `summary` 允许为空，但字段必须存在

- [ ] **Step 4: Implement category-map repository**

Persist file:

```json
{
  "categories": {
    "AI与技术": {
      "slug": "AI与技术",
      "topics": ["AI大模型", "Prompt工程"]
    }
  }
}
```

Repository responsibilities:

- load/create `_meta/category-map.json`
- expose `list_categories()`
- expose `merge_classification(category, topics)`
- normalize duplicate topic strings by exact-text dedupe

- [ ] **Step 5: Run focused tests**

Run: `python -m unittest test.test_knowledge_parser -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/knowledge_pipeline/parser.py app/services/knowledge_pipeline/category_map.py test/test_knowledge_parser.py
git commit -m "feat: add knowledge markdown parser"
```

### Task 5: Build the classifier and classification result contract

**Files:**
- Create: `app/services/knowledge_pipeline/classifier.py`
- Modify: `app/config.py`
- Test: `test/test_knowledge_classifier.py`

- [ ] **Step 1: Write the failing classifier tests**

```python
import unittest
from unittest.mock import AsyncMock


class KnowledgeClassifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_classifier_returns_category_topics_quality_and_reason(self):
        from app.services.knowledge_pipeline.classifier import KnowledgeClassifier

        fake_llm = AsyncMock()
        fake_llm.postprocess.return_value = """
category: AI与技术
topics:
  - AI大模型
quality_score: 0.85
processing_log: 摘要提及 GPT-4o 与 Prompt 优化
"""
        result = await KnowledgeClassifier(processor=fake_llm).classify(...)
        self.assertEqual(result.category, "AI与技术")
        self.assertEqual(result.topics, ["AI大模型"])
        self.assertEqual(result.quality_score, 0.85)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest test.test_knowledge_classifier -v`
Expected: FAIL because classifier contract does not exist

- [ ] **Step 3: Implement the classifier using existing text processor stack**

Reuse `create_text_postprocessor(prompt_template=...)`, do not create a second unrelated LLM client abstraction.

```python
CLASSIFICATION_PROMPT = """
你是知识库分类器。
输入：
1. 标题
2. 摘要
3. 已有分类列表

输出 YAML：
category: ...
topics:
  - ...
quality_score: 0.00
processing_log: ...
"""
```

Rules:

- 优先复用现有 category-map 中的 category
- `topics` 允许新增，但必须去重、去空白
- `quality_score` 限制在 `0.0 <= x <= 1.0`
- LLM 超时/失败时返回 fallback：`未分类`, `[]`, `0.0`, `LLM 分类失败...`

- [ ] **Step 4: Add config fields for prompts/timeouts**

```python
knowledge_classification_prompt: str = Field(...)
knowledge_classification_timeout: int = Field(default=120, env="KNOWLEDGE_CLASSIFICATION_TIMEOUT")
daily_report_prompt: str = Field(...)
```

- [ ] **Step 5: Run focused tests**

Run: `python -m unittest test.test_knowledge_classifier -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/config.py app/services/knowledge_pipeline/classifier.py test/test_knowledge_classifier.py
git commit -m "feat: add knowledge classifier"
```

### Task 6: Implement archiving, topic update, and processing log writing

**Files:**
- Create: `app/services/knowledge_pipeline/obsidian_client.py`
- Create: `app/services/knowledge_pipeline/archiver.py`
- Create: `app/services/knowledge_pipeline/topic_updater.py`
- Create: `app/services/knowledge_pipeline/processing_logger.py`
- Test: `test/test_knowledge_archiver.py`
- Test: `test/test_topic_updater.py`

- [ ] **Step 1: Write the failing archive/topic tests**

```python
class KnowledgeArchiverTests(unittest.TestCase):
    def test_archiver_writes_processed_frontmatter_to_category_folder(self):
        ...
        self.assertIn("category: AI与技术", written_markdown)
        self.assertIn("topics:\n  - AI大模型", written_markdown)
        self.assertTrue(target_path.endswith("knowledge/AI与技术/2026-05-28-文章标题.md"))
```

```python
class TopicUpdaterTests(unittest.IsolatedAsyncioTestCase):
    async def test_topic_updater_creates_topic_file_with_dataview_block(self):
        ...
        self.assertIn('LIST FROM "knowledge" WHERE contains(topics, "AI大模型")', content)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest test.test_knowledge_archiver test.test_topic_updater -v`
Expected: FAIL because archive/topic services do not exist

- [ ] **Step 3: Implement Obsidian client with fallback**

```python
class ObsidianWriter:
    async def write_text(self, vault_relative_path: str, text: str) -> None:
        try:
            await self._write_via_local_rest(vault_relative_path, text)
        except Exception:
            self._write_via_filesystem(vault_relative_path, text)
```

Do not silently swallow both paths failing; if fallback also fails, raise the second error with context.

- [ ] **Step 4: Implement archiver**

Processed frontmatter target:

```yaml
category: AI与技术
topics:
  - AI大模型
processed_at: 2026-05-28T23:00:00
quality_score: 0.85
processing_log: 分类依据：摘要提及 GPT-4o 与 Prompt 优化
```

Archive rules:

- filename = `YYYY-MM-DD-slug.md`
- category folder auto-create
- Local REST API path uses vault-relative path, not absolute local path
- success 时更新 `InboxEntry.archive_path` / `status=completed`
- failure 时文件移入 `inbox/failed/`

- [ ] **Step 5: Implement topic updater**

Topic file template:

```markdown
# AI大模型

## 核心观点
**[2026-05-28 更新]** 本文新增观点摘要

## 相关文章
```dataview
LIST FROM "knowledge" WHERE contains(topics, "AI大模型") SORT date DESC
```
```

Append-only rules:

- 核心观点区只追加，不覆盖
- topic 文件不存在时初始化完整模板
- 每个 topic 只追加一次本次文章的观点

- [ ] **Step 6: Implement daily log writer**

Log line examples:

```text
2026-05-28T14:23:03 [INFO] 分类: AI与技术 | topics: [AI大模型, 多模态]
2026-05-28T14:23:03 [INFO] quality_score: 0.87 | 耗时: 2.1s
```

- [ ] **Step 7: Run focused tests**

Run: `python -m unittest test.test_knowledge_archiver test.test_topic_updater -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add app/services/knowledge_pipeline/obsidian_client.py app/services/knowledge_pipeline/archiver.py app/services/knowledge_pipeline/topic_updater.py app/services/knowledge_pipeline/processing_logger.py test/test_knowledge_archiver.py test/test_topic_updater.py
git commit -m "feat: add archiver and topic updater"
```

### Task 7: Implement the orchestrator and dual trigger modes

**Files:**
- Create: `app/services/knowledge_pipeline/orchestrator.py`
- Create: `app/services/knowledge_pipeline/watcher.py`
- Create: `scripts/run_knowledge_pipeline.py`
- Modify: `scripts/export_favorites_to_md.py`
- Modify: `scripts/export_douyin_to_md.py`
- Modify: `scripts/export_instapaper_to_md.py`
- Modify: `scripts/export_xiaoyuzhou_to_md.py`
- Modify: `scripts/export_youtube_to_md.py`
- Test: `test/test_knowledge_pipeline_orchestrator.py`

- [ ] **Step 1: Write the failing orchestrator tests**

```python
class KnowledgePipelineOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_files_runs_parse_classify_archive_topic_log_flow(self):
        ...
        self.assertEqual(result.completed, 1)
        self.assertEqual(result.failed, 0)

    async def test_export_script_only_passes_newly_written_paths(self):
        ...
        orchestrator.process_files.assert_awaited_once_with([expected_path])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest test.test_knowledge_pipeline_orchestrator -v`
Expected: FAIL because no orchestrator/watcher exists

- [ ] **Step 3: Implement batch orchestration**

```python
class KnowledgePipelineOrchestrator:
    async def process_files(self, paths: list[Path]) -> PipelineResult: ...
    async def process_inbox(self, limit: int | None = None) -> PipelineResult: ...
    async def process_single_file(self, path: Path) -> FileProcessResult: ...
```

Execution order per file:

1. parse
2. classify
3. merge category-map
4. archive
5. update topics
6. write daily log
7. update `InboxEntry`

- [ ] **Step 4: Wire export scripts to call orchestrator after successful writes**

Pattern:

```python
written_paths = []
...
md_path.write_text(md_content, encoding="utf-8")
written_paths.append(md_path)
...
if written_paths:
    await KnowledgePipelineOrchestrator().process_files(written_paths)
```

Rules:

- only pass newly written files, not skipped/existing files
- if pipeline processing fails, exporter should print explicit failure summary and exit non-zero
- keep optional flag to disable auto-processing for debugging, e.g. `--skip-knowledge-pipeline`

- [ ] **Step 5: Add watchdog runner**

CLI contract:

```bash
python scripts/run_knowledge_pipeline.py --once
python scripts/run_knowledge_pipeline.py --watch
python scripts/run_knowledge_pipeline.py --limit 50
```

- [ ] **Step 6: Run focused tests**

Run: `python -m unittest test.test_knowledge_pipeline_orchestrator -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/services/knowledge_pipeline/orchestrator.py app/services/knowledge_pipeline/watcher.py scripts/run_knowledge_pipeline.py scripts/export_favorites_to_md.py scripts/export_douyin_to_md.py scripts/export_instapaper_to_md.py scripts/export_xiaoyuzhou_to_md.py scripts/export_youtube_to_md.py test/test_knowledge_pipeline_orchestrator.py
git commit -m "feat: trigger knowledge pipeline after exports"
```

---

## Chunk 3: Daily Report, Verification, and Rollout

> Use @test-driven-development and finish with @verification-before-completion.

### Task 8: Implement the daily reporter with Tavily enrichment

**Files:**
- Create: `app/services/knowledge_pipeline/daily_reporter.py`
- Create: `scripts/generate_daily_report.py`
- Modify: `.env.example`
- Modify: `README.md`
- Test: `test/test_daily_reporter.py`

- [ ] **Step 1: Write the failing daily reporter tests**

```python
class DailyReporterTests(unittest.IsolatedAsyncioTestCase):
    async def test_daily_reporter_builds_focus_today_trends_and_watchlist_sections(self):
        ...
        self.assertIn("# 知识库日报 2026-05-28", report)
        self.assertIn("## 重点关注", report)
        self.assertIn("## 今日新增", report)
        self.assertIn("## 近期趋势", report)
        self.assertIn("## 待关注信号", report)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest test.test_daily_reporter -v`
Expected: FAIL because daily reporter does not exist

- [ ] **Step 3: Implement active-topic scoring and Tavily adapter**

```python
class DailyReporter:
    async def generate(self, day: date) -> str: ...
    async def collect_internal_topic_signals(self, day: date) -> list[TopicSignal]: ...
    async def collect_external_topic_signals(self, topics: list[str]) -> list[ExternalTopicSignal]: ...
```

Scoring inputs:

- 今日新增文章数量（权重 1.0）
- 近 3 天新增（权重 0.7）
- 14 天内 topic 活跃度（权重 0.3）
- `quality_score` 均值
- Tavily 搜索结果条数/摘要热度

- [ ] **Step 4: Implement daily markdown writer**

Output path:

```text
daily/2026-05-28.md
```

Trigger modes:

- manual: `python scripts/generate_daily_report.py --date 2026-05-28`
- scheduled: `launchd` / `cron` later接入，代码侧只提供 CLI

- [ ] **Step 5: Run focused tests**

Run: `python -m unittest test.test_daily_reporter -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/services/knowledge_pipeline/daily_reporter.py scripts/generate_daily_report.py .env.example README.md test/test_daily_reporter.py
git commit -m "feat: add knowledge daily reporter"
```

### Task 9: Final documentation and end-to-end verification

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Verify: `test/test_knowledge_pipeline_config.py`
- Verify: `test/test_export_inbox_frontmatter.py`
- Verify: `test/test_legacy_collection_import.py`
- Verify: `test/test_knowledge_parser.py`
- Verify: `test/test_knowledge_classifier.py`
- Verify: `test/test_knowledge_archiver.py`
- Verify: `test/test_topic_updater.py`
- Verify: `test/test_knowledge_pipeline_orchestrator.py`
- Verify: `test/test_daily_reporter.py`

- [ ] **Step 1: Update README usage flows**

Document:

1. export scripts now write to `vault/inbox/`
2. exporters auto-trigger knowledge pipeline by default
3. legacy import command for old `collection/`
4. daily report command
5. required `.env` keys including `TAVILY_API_KEY` and `OBSIDIAN_LOCAL_REST_API_KEY`

- [ ] **Step 2: Run focused pipeline test set**

Run:

```bash
python -m unittest \
  test.test_knowledge_pipeline_config \
  test.test_export_inbox_frontmatter \
  test.test_legacy_collection_import \
  test.test_knowledge_parser \
  test.test_knowledge_classifier \
  test.test_knowledge_archiver \
  test.test_topic_updater \
  test.test_knowledge_pipeline_orchestrator \
  test.test_daily_reporter -v
```

Expected: PASS

- [ ] **Step 3: Run repository regression suite**

Run: `python -m unittest discover -s test -p 'test_*.py'`
Expected: PASS

- [ ] **Step 4: Do a manual smoke flow**

Run:

```bash
python scripts/import_collection_to_inbox.py --sources instapaper --limit 2
python scripts/run_knowledge_pipeline.py --once --limit 2
python scripts/generate_daily_report.py --date 2026-05-28
```

Expected:

- `inbox/` 有导入文件
- `knowledge/<category>/` 出现归档文件
- `knowledge/_topics/` 出现 topic 页
- `_meta/logs/2026-05-28.log` 有处理日志
- `daily/2026-05-28.md` 生成成功

- [ ] **Step 5: Commit**

```bash
git add README.md .env.example
git commit -m "docs: document knowledge pipeline workflow"
```

---

## Implementation Notes

- Keep `knowledge_pipeline` isolated from the current RAG vector-store flow; this work is Obsidian knowledge management, not Chroma ingestion.
- Reuse existing `create_text_postprocessor(...)` for structured YAML generation before adding a second model client.
- Prefer vault-relative paths at service boundaries; only `ContentStorageManager` should know absolute filesystem layout.
- Do not make watcher mode mandatory for exports; direct invocation is the default fast path.
- When the classifier falls back to `未分类`, archiver must still produce a valid file under `knowledge/未分类/`.
- Legacy import should be resumable and idempotent.

## Open Questions To Reconfirm Before Implementation Starts

- None blocking. The only config addition implied by the confirmed design is:

```env
TAVILY_API_KEY=<your key>
```

and it should be surfaced through `app/config.py` the same way other API keys are handled.

## Suggested Execution Order

1. Chunk 1 first — lock the data contract before touching pipeline internals
2. Chunk 2 second — parser/classifier/archive/topic/orchestrator
3. Chunk 3 last — daily report, docs, full verification

## Rollback Strategy

- If export-script integration causes instability, temporarily run:
  - exporters with `--skip-knowledge-pipeline`
  - `python scripts/run_knowledge_pipeline.py --once`
- Keep legacy import separate from daily exporter flow so historical migration can be retried without affecting new captures

Plan complete and saved to `docs/superpowers/plans/2026-05-28-knowledge-base-pipeline-integration.md`. Ready to execute?
