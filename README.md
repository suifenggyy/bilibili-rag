# 🚀 Bilibili RAG：把收藏夹变成可对话的知识库

把你在 B 站收藏的访谈 / 演讲 / 课程，变成可检索、可追溯来源的**个人知识库**。
适合：技术演讲整理、公开课复盘、播客内容归档、会议回顾等。

> 亮点：扫码登录 → 自动拉取收藏夹 → 语音转写（ASR）→ 向量检索 → 对话问答

---

## ✨ 功能一览

| 功能 | 说明 |
|------|------|
| 🔐 扫码登录 | B 站 QR 登录，session 自动缓存，无需反复扫码 |
| 📁 收藏夹管理 | 读取所有收藏夹，支持多选入库 |
| 🎙️ ASR 语音转写 | 支持 DashScope 云端、Ollama 本地 Whisper、openai-whisper 本地三种后端 |
| 🔍 语义检索 | 基于 ChromaDB 向量检索 |
| 💬 RAG 对话问答 | 基于收藏内容回答问题，附来源溯源 |
| 📝 B 站收藏夹导出 | 将收藏夹视频转写内容批量导出为 Markdown 文件 |
| 🎵 抖音收藏夹导出 | 获取抖音收藏视频，音频 ASR 转写后导出 Markdown |
| 📖 Instapaper 书签导出 | 获取书签列表，按 requests → Playwright → 基本信息 的链路提取正文，无需 Premium |

---

## 📂 项目结构

```
bilibili-rag/
├── app/                          # 后端（FastAPI）
│   ├── main.py                   # 应用入口，注册路由与中间件
│   ├── config.py                 # 全局配置（读取 .env）
│   ├── database.py               # SQLite 数据库初始化
│   ├── models.py                 # 数据模型（SQLAlchemy + Pydantic）
│   ├── routers/
│   │   ├── auth.py               # 登录接口（扫码 / 会话管理）
│   │   ├── favorites.py          # 收藏夹接口
│   │   ├── knowledge.py          # 知识库构建接口
│   │   ├── chat.py               # 对话问答接口
│   │   ├── export.py             # B站收藏夹导出接口
│   │   ├── douyin_export.py      # 抖音收藏夹导出接口
│   │   └── instapaper_export.py  # Instapaper 书签导出接口
│   └── services/
│       ├── bilibili.py           # B 站 API 封装（登录、收藏夹、视频、音频）
│       ├── wbi.py                # B 站 WBI 签名工具
│       ├── content_fetcher.py    # 视频内容获取（ASR + 降级策略）
│       ├── asr.py                # DashScope ASR 服务
│       ├── asr_local.py          # Ollama 本地 ASR 服务（Whisper）
│       ├── asr_whisper.py        # openai-whisper 本地 ASR 服务
│       ├── asr_factory.py        # ASR 后端选择工厂
│       ├── douyin.py             # 抖音 API 封装（Evil0ctal 中间层）
│       ├── douyin_fetcher.py     # 抖音音频下载 + ASR 转写
│       ├── instapaper.py         # Instapaper OAuth 1.0a xAuth 认证 + 书签 API
│       ├── article_fetcher.py    # 文章正文提取（requests + trafilatura，Playwright 回退）
│       └── rag.py                # 向量检索与 RAG 对话
│
├── frontend/                     # 前端（Next.js + React + Tailwind CSS）
│   ├── app/
│   │   ├── page.tsx              # 主页
│   │   └── layout.tsx            # 根布局
│   ├── components/
│   │   ├── LoginModal.tsx        # 扫码登录弹窗
│   │   ├── ChatPanel.tsx         # 对话面板
│   │   ├── SourcesPanel.tsx      # 来源溯源面板
│   │   ├── DemoFlowModal.tsx     # 演示流程弹窗
│   │   ├── OrganizePreviewModal.tsx  # 收藏夹整理预览
│   │   ├── ExportPanel.tsx       # B站收藏夹导出面板
│   │   ├── DouyinExportPanel.tsx # 抖音收藏夹导出面板
│   │   └── InstapaperExportPanel.tsx # Instapaper 书签导出面板
│   └── lib/api.ts                # API 请求封装
│
├── scripts/
│   ├── export_favorites_to_md.py # B站收藏夹 → Markdown 独立导出脚本
│   ├── export_douyin_to_md.py    # 抖音收藏夹 → Markdown 独立导出脚本
│   └── export_instapaper_to_md.py # Instapaper 书签 → Markdown 独立导出脚本
│
├── test/                         # 诊断脚本（需在项目根目录运行）
│   ├── debug_asr_single.py       # 测试单视频 ASR 转写
│   ├── diagnose_rag.py           # 测试向量检索准确性
│   └── sync_cache_vectors.py     # 同步数据库缓存到向量库
│
├── skills/
│   └── bilibili-rag-local/       # OpenClaw Skill 接入配置
│       └── SKILL.md
│
├── data/                         # 运行时数据（自动生成，不入 git）
│   ├── bilibili_rag.db           # SQLite 数据库
│   └── chroma_db/                # ChromaDB 向量库
│
├── .env.example                  # 环境变量模板
├── .bili_session.json            # B 站登录缓存（自动生成，不入 git）
├── .instapaper_session.json      # Instapaper token 缓存（自动生成，不入 git）
├── requirements.txt              # Python 依赖
└── README.md                     # 本文档
```

额外运行时目录：

- `~/.bilibili-rag/<source>/<YYYY-MM-DD>/<title>/`：抓取工作目录，保留视频/音频/`asr_raw.txt`/`asr_corrected.txt`
- `COLLECTION_OUTPUT_DIR/<source>/<YYYY-MM-DD>/`：最终 Markdown 输出目录

---

## ⚙️ 环境准备

### 1. 系统依赖

**ffmpeg**（必须，用于音频转码）

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Windows
# 下载 https://ffmpeg.org/download.html，将 bin/ 加入 PATH
```

验证安装：
```bash
ffmpeg -version
```

### 2. Python 环境

推荐使用 conda 或 venv 隔离环境：

```bash
conda create -n bilibili-rag python=3.11
conda activate bilibili-rag
pip install -r requirements.txt
```

### 3. Node.js 环境（仅前端需要）

推荐 Node.js 18+：

```bash
cd frontend
npm install
```

---

## 🔧 配置说明（.env）

复制模板后编辑：

```bash
cp .env.example .env
```

**完整配置项：**

```env
# ── DashScope（Aliyun 通义）────────────────────────────────
# ASR 云端转写 + LLM 对话 + Embedding 向量化
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx    # 必填（用于 DashScope 功能）
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# ── LLM & Embedding 模型 ──────────────────────────────────
LLM_MODEL=qwen3-max
EMBEDDING_MODEL=text-embedding-v4

# ── DashScope ASR ─────────────────────────────────────────
ASR_BACKEND=whisper                   # dashscope / ollama / whisper
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/api/v1
ASR_MODEL=paraformer-v2              # 云端转写模型（URL 模式）
ASR_MODEL_LOCAL=paraformer-realtime-v2  # 本地文件识别模型（兜底）
ASR_TIMEOUT=600                      # 转写超时秒数
ASR_INPUT_FORMAT=pcm                 # pcm 或 wav

# ── Ollama 本地 ASR（Whisper）────────────────────────────
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_ASR_MODEL=whisper             # whisper / whisper:large 等
OLLAMA_ASR_LANGUAGE=zh               # 语言提示，留空则自动检测

# ── 文本后处理 / 正文总结模型 ────────────────────────────
# ollama: /api/generate
# proxy:  OpenAI 兼容 /v1/chat/completions（例如本地代理 http://localhost:4141）
TEXT_MODEL_BACKEND=ollama
TEXT_MODEL_BASE_URL=http://localhost:11434
TEXT_MODEL_NAME=gemma4:e2b
TEXT_MODEL_TIMEOUT=300
TEXT_MODEL_CORRECTION_PROMPT=下面是一个语音转换生成的文本，修改其中识别错误的字，并完善格式，增加标点符号和段落;最终只输出纠错并格式化后的文本,不要添加额外的信息
TEXT_MODEL_SUMMARY_PROMPT=请严格依据下方原始正文文本开展专业化精炼汇总与核心信息萃取工作，全程恪守信息完整留存、内容客观还原、逻辑条理规整、表述专业规范四大核心原则，不得擅自删减核心要件、篡改原文语义逻辑、新增主观臆断内容或弱化关键实操要点。全面精准提炼文本核心主旨、核心业务内容与核心逻辑脉络，系统性完整留存原文所有不可缺失关键信息，全覆盖保留各类实操类、数据类、专属类核心内容，具体包含且不限于全流程操作规范、标准化执行细则、分步安装部署流程、核心实操代码片段、专属访问链接与网络地址、官方专有名称及专属代号、核心业务运行参数、关键时间节点与精准数值指标、环节衔接核心逻辑、注意警示事项与禁忌操作要求、配套备注补充说明等全部核心刚需内容。完成内容精炼总结后，结合文本核心主题、业务领域、核心用途、关键核心关键词，提炼生成精准贴合、分类清晰、适配归档检索与快速识别使用的标准化专属标签，整体输出内容结构规整、层级分明、专业严谨、一目了然，适配正式归档、工作查阅、业务对接、资料留存等专业办公使用场景。\n\n请严格按 YAML 输出，不要添加额外说明，格式如下：\nsummary: |\n  一段精炼总结\nkey_points:\n  - 要点1\n  - 要点2\ntags:\n  - 标签1\n  - 标签2

# ── openai-whisper 本地 ASR ──────────────────────────────
WHISPER_MODEL=turbo                  # tiny / base / small / medium / large / turbo
WHISPER_LANGUAGE=zh                  # 语言提示，留空则自动检测

# ── 抖音导出 ──────────────────────────────────────────────
# 浏览器手动复制：Chrome → douyin.com → F12 → Application → Cookies
DOUYIN_COOKIE=
DOUYIN_EVIL0CTAL_URL=http://localhost:2333

# ── Instapaper 导出 ────────────────────────────────────────
# 申请 API Key：https://www.instapaper.com/main/request_oauth_consumer_token
INSTAPAPER_CONSUMER_KEY=
INSTAPAPER_CONSUMER_SECRET=
INSTAPAPER_EMAIL=
INSTAPAPER_PASSWORD=

# ── 应用 ──────────────────────────────────────────────────
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=true

# ── 数据存储 ──────────────────────────────────────────────
DATABASE_URL=sqlite+aiosqlite:///./data/bilibili_rag.db
CHROMA_PERSIST_DIRECTORY=./data/chroma_db
CONTENT_WORKSPACE_ROOT=~/.bilibili-rag
CONTENT_WORKSPACE_MAX_SIZE_BYTES=1073741824
CONTENT_WORKSPACE_RETENTION_DAYS=3
COLLECTION_OUTPUT_DIR=/Users/gongyongyue/FangcloudV2/personal_space.localized/同步空间/个人资料/Obsidian/jarvis/collection
```

---

## 🌐 模式一：Web 全栈应用

图形界面，支持扫码登录、收藏夹管理、RAG 问答。

### 启动后端

```bash
# 在项目根目录
conda activate bilibili-rag
python -m uvicorn app.main:app --reload
```

| 地址 | 说明 |
|------|------|
| `http://localhost:8000` | API 根路径 |
| `http://localhost:8000/docs` | Swagger 交互文档 |
| `http://localhost:8000/health` | 健康检查 |

### 启动前端

```bash
cd frontend
npm run dev
```

前端页面：`http://localhost:3000`

### 使用流程

1. 打开 `http://localhost:3000`
2. 点击登录 → 用手机 B 站 App 扫码
3. 右侧面板有四个功能 Tab：

| Tab | 功能 |
|-----|------|
| **知识库问答** | 选择收藏夹 → 构建知识库 → RAG 对话问答 |
| **B站导出** | 选择收藏夹 + ASR 后端 → 导出 Markdown → 下载 ZIP |
| **抖音导出** | 粘贴 Cookie + Evil0ctal URL → 导出收藏视频转写 → 下载 ZIP |
| **Instapaper** | 填写 API Key + 邮箱密码 → 选择文件夹 → 提取正文 → 下载 ZIP |

### API 路由一览

| 路由 | 方法 | 说明 |
|------|------|------|
| `/auth/qrcode` | GET | 生成登录二维码 |
| `/auth/qrcode/poll/{key}` | GET | 轮询扫码状态 |
| `/auth/session/{id}` | GET | 获取会话信息 |
| `/favorites/list` | GET | 获取收藏夹列表 |
| `/favorites/{id}/videos` | GET | 获取收藏夹视频（分页）|
| `/favorites/{id}/all-videos` | GET | 获取收藏夹全部视频 |
| `/knowledge/build` | POST | 构建知识库（触发 ASR）|
| `/knowledge/status` | GET | 查看入库状态 |
| `/chat/ask` | POST | RAG 问答 |
| `/chat/search` | POST | 语义检索片段 |
| `/export/start` | POST | B站导出：启动任务 |
| `/export/status/{id}` | GET | B站导出：查询进度 |
| `/export/download/{id}` | GET | B站导出：下载 ZIP |
| `/export/jobs` | GET | B站导出：历史任务 |
| `/douyin-export/start` | POST | 抖音导出：启动任务 |
| `/douyin-export/status/{id}` | GET | 抖音导出：查询进度 |
| `/douyin-export/download/{id}` | GET | 抖音导出：下载 ZIP |
| `/instapaper-export/start` | POST | Instapaper 导出：启动任务 |
| `/instapaper-export/status/{id}` | GET | Instapaper 导出：查询进度 |
| `/instapaper-export/download/{id}` | GET | Instapaper 导出：下载 ZIP |
| `/instapaper-export/folders` | GET | Instapaper：获取文件夹列表 |

---

## 📝 模式二：独立导出脚本

无需启动 Web 服务，直接将收藏夹视频转写为本地 Markdown 文件。
**不依赖 RAG 或向量数据库。**

### 快速开始

```bash
# 在项目根目录运行
conda activate bilibili-rag
python scripts/export_favorites_to_md.py
```

首次运行会在终端展示 B 站 QR 码，扫码后自动缓存 session（30 天内无需重新登录）。

### 完整参数说明

```
python scripts/export_favorites_to_md.py [选项]

收藏夹选择：
  （无参数）              交互式选择收藏夹
  --all                  导出全部收藏夹
  --folder-id 12345678   导出指定收藏夹（media_id）

输出：
  --output-dir ./output  输出目录（默认: ./output）

登录：
  --relogin              强制重新扫码登录（切换账号时使用）

ASR 后端：
  --asr-backend auto       自动：按 .env 中 ASR_BACKEND 选择
  --asr-backend dashscope  使用 DashScope 云端转写
  --asr-backend ollama     使用 Ollama 本地转写
  --asr-backend whisper    使用 openai-whisper 本地转写

Ollama 参数（--asr-backend ollama 时有效）：
  --ollama-url http://localhost:11434   Ollama 服务地址
  --ollama-model whisper               模型名称
  --ollama-language zh                 语言提示（留空自动检测）
```

### 使用示例

```bash
# 交互式选择，自动选择 ASR 后端
python scripts/export_favorites_to_md.py

# 导出所有收藏夹，指定输出目录
python scripts/export_favorites_to_md.py --all --output-dir ~/bilibili-notes

# 使用 openai-whisper 本地转写（无需 API Key）
python scripts/export_favorites_to_md.py --asr-backend whisper

# 使用 Ollama 本地 Whisper 转写（需本地启动 Ollama）
python scripts/export_favorites_to_md.py --asr-backend ollama

# 使用高精度 whisper:large 模型
python scripts/export_favorites_to_md.py --asr-backend ollama --ollama-model whisper:large

# 导出指定收藏夹
python scripts/export_favorites_to_md.py --folder-id 12345678

# 切换账号时强制重新登录
python scripts/export_favorites_to_md.py --relogin
```

说明：ASR 成功后，系统会继续调用 `TEXT_MODEL_*` 配置指定的文本模型对转写文本进行纠错、补标点和分段；`TEXT_MODEL_BACKEND=ollama` 时走 Ollama `/api/generate`，`TEXT_MODEL_BACKEND=proxy` 时走 OpenAI 兼容 `/v1/chat/completions`（例如 `http://localhost:4141`）。正文总结也复用同一后端和模型连接配置，但使用独立的 `TEXT_MODEL_SUMMARY_PROMPT`。如果后处理失败，会自动回退到原始 ASR 文本。抓取阶段会保留 `asr_raw.txt` 和 `asr_corrected.txt` 两份文本，Markdown、知识库缓存和向量库使用纠错后的文本。

### 输出文件结构

```
$COLLECTION_OUTPUT_DIR/
└── bilibili/
    └── 2026-05-17/
        ├── 如何设计一个好的 API_BV1xx411c7mD.md
        └── 分布式系统入门_BV1yz4y1Z7kg.md
```

每个 Markdown 文件包含：视频元信息表格（BV号超链接、UP主、时长、发布日期）、封面图、视频简介、ASR 转写全文（转写失败时降级为标题 + 简介）。

---

## 🎵 模式三：抖音收藏夹导出脚本

`scripts/export_douyin_to_md.py` 将**抖音收藏夹**中的视频音频转写为 Markdown 文档，与 B站导出脚本平行独立运行。

### 前提条件

**1. 部署 Evil0ctal API 中间层**（处理抖音动态签名 A-Bogus）

```bash
git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API
cd Douyin_TikTok_Download_API && pip install -r requirements.txt
python main.py   # 默认监听 http://localhost:2333
```

**2. 获取抖音 Cookie**（抖音不提供扫码 API，需手动复制）

1. Chrome/Edge 打开 [https://www.douyin.com](https://www.douyin.com) 并登录
2. 按 F12 → Application → Cookies → `douyin.com`
3. 将所有字段拼接为 `"key1=val1; key2=val2; ..."` 格式
4. 填入 `.env` 的 `DOUYIN_COOKIE` 或通过 `--cookie` 参数传入

### 配置

```bash
# .env
DOUYIN_COOKIE=ttwid=xxx; sessionid=xxx; odin_tt=xxx; msToken=xxx; ...
DOUYIN_EVIL0CTAL_URL=http://localhost:2333   # Evil0ctal API 地址
COLLECTION_OUTPUT_DIR=/path/to/collection    # B站/抖音 Markdown 统一输出目录
```

### 用法

```bash
# 交互式选择导出数量（推荐初次使用）
python scripts/export_douyin_to_md.py

# 指定 Cookie（不配置环境变量时使用）
python scripts/export_douyin_to_md.py --cookie "ttwid=xxx; sessionid=xxx; ..."

# 导出全部收藏视频
python scripts/export_douyin_to_md.py --all

# 仅导出最新 20 个
python scripts/export_douyin_to_md.py --limit 20

# 指定输出目录
python scripts/export_douyin_to_md.py --output-dir ~/douyin-notes

# 使用 openai-whisper 本地 ASR（不消耗 API 额度）
python scripts/export_douyin_to_md.py --asr-backend whisper

# 使用 Ollama 本地 ASR（需本地启动 Ollama）
python scripts/export_douyin_to_md.py --asr-backend ollama

# 使用更高精度的 Whisper 模型
python scripts/export_douyin_to_md.py --asr-backend ollama --ollama-model whisper:large

# 指定 Evil0ctal 服务地址（非默认端口时使用）
python scripts/export_douyin_to_md.py --evil0ctal-url http://192.168.1.100:2333
```

### 输出文件结构

```
$COLLECTION_OUTPUT_DIR/
└── douyin/
    └── 2026-05-17/
        ├── 这个视频讲得太好了_7234567890123456789.md
        ├── 学习Python必看_7198765432109876543.md
        └── 美食探店日记_7111122223333444455.md
```

每个 Markdown 文件包含：视频信息表格（ID 超链接、作者、时长、发布日期）、封面图、ASR 转写全文。

### 与 B站导出对比

| 维度 | B站 | 抖音 |
|------|-----|------|
| 登录方式 | 扫码（官方 API） | 手动复制 Cookie |
| 中间层 | 无需 | 需部署 Evil0ctal API |
| 收藏组织 | 按收藏夹分目录 | 统一输出目录 |
| ASR 复用 | ✅ DashScope / Ollama / openai-whisper | ✅ 完全相同 |

---

## 🎙️ ASR 转写后端对比

| | DashScope（云端） | Ollama Whisper（本地） | openai-whisper（本地） |
|---|---|---|---|
| 模型 | paraformer-v2 | whisper / whisper:large | tiny / base / small / medium / large / turbo |
| 费用 | 按时长计费（有免费额度）| 完全免费 | 完全免费 |
| 隐私 | 音频上传至云端 | 音频留在本地 | 音频留在本地 |
| 速度 | 快（通常 1-3 分钟/小时音频）| 依赖本机 GPU/CPU | 依赖本机 GPU/CPU |
| 中文效果 | 优秀（专为中文优化）| 良好（多语言通用）| 良好（多语言通用） |
| 网络依赖 | 需要公网 | 仅需本地 Ollama 服务 | 无需本地服务 |
| 配置 | 需要 `DASHSCOPE_API_KEY` | 需安装 Ollama + 拉取模型 | 需安装 `openai-whisper` 和模型 |

### Ollama Whisper 环境准备

```bash
# 1. 安装 Ollama（https://ollama.com）
curl -fsSL https://ollama.com/install.sh | sh   # Linux/macOS

# 2. 拉取 Whisper 模型
ollama pull whisper           # 标准版（约 1.5GB）
ollama pull whisper:large     # 高精度版（约 3GB）

# 3. 启动服务（若未以后台服务运行）
ollama serve

# 4. 验证可用
curl http://localhost:11434/api/tags
```

### openai-whisper 环境准备

```bash
# 1. 安装 Python 依赖
pip install -r requirements.txt

# 2. 首次转写时会自动下载模型，也可以直接在 .env 中指定
WHISPER_MODEL=turbo
```

---

## 🧪 诊断脚本

> ⚠️ 以下脚本需在**项目根目录**运行（依赖相对路径与 .env 配置）。

```bash
conda activate bilibili-rag

# 测试单个视频的音频获取与 ASR 转写流程
python test/debug_asr_single.py

# 诊断向量检索召回准确性
python test/diagnose_rag.py

# 将数据库中的转写缓存同步到 ChromaDB 向量库
python test/sync_cache_vectors.py
```

---

## 🧩 OpenClaw Skill 接入

`skills/bilibili-rag-local/SKILL.md` 提供了将本项目接入 OpenClaw 的配置。
启动后端后，OpenClaw 可通过以下接口调用：

- `POST /chat/ask` — RAG 问答
- `POST /chat/search` — 语义检索片段
- `GET /knowledge/folders/status` — 查看入库状态

接入步骤：
1. 完成本地部署，确认 `http://127.0.0.1:8000/docs` 可访问
2. 将 `skills/bilibili-rag-local` 复制到 OpenClaw Skills 目录（如 `~/.openclaw/skills/`）
3. 重启 OpenClaw 并加载 Skill

---

## ❓ 常见问题

**Q：为什么有些视频 ASR 失败，只有基本信息？**
A：B 站音频直链存在鉴权 / 过期 / 区域限制。系统会自动降级：直链转写 → 本地下载 + ffmpeg 转码 → 再次识别 → 最终降级为视频基本信息（标题 + 简介）。确保 ffmpeg 已正确安装可提升成功率。

**Q：Whisper 转写速度很慢怎么办？**
A：Whisper 在 CPU 上较慢。建议：① 使用较小的模型（如 `turbo` / `whisper`，而非 `large`）；② 若使用 Ollama，安装支持 GPU 推理的版本；③ 处理视频较多时切换到 DashScope 云端（有免费额度）。

**Q：扫码登录后多久需要重新登录？**
A：脚本会缓存 session 到 `.bili_session.json`，有效期按 30 天保守计算（B 站实际约 180 天）。缓存失效或使用 `--relogin` 时触发重新扫码。

**Q：DashScope 费用如何控制？**
A：ASR 按时长计费，LLM 按 Token 计费，Embedding 按 Token 计费。建议先用 **10 分钟左右的短视频**测试整体流程。大多数模型有免费额度，日常个人使用通常足够。

**Q：`已失效视频` 是什么？**
A：B 站收藏夹中被 UP 主删除或下架的视频。导出脚本和 Web 服务均会自动过滤（`attr == 9`）。

---

## 🧩 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI 0.115 + Uvicorn |
| LLM / RAG | LangChain 0.3 + DashScope (qwen3-max) |
| 向量数据库 | ChromaDB 0.5 |
| 关系数据库 | SQLite + SQLAlchemy 2.0 (async) |
| ASR 云端 | DashScope paraformer-v2 |
| ASR 本地 | Ollama Whisper |
| 音频处理 | ffmpeg |
| 前端框架 | Next.js 16 + React 19 |
| 前端样式 | Tailwind CSS 4 |
| 前端语言 | TypeScript 5 |

---

## 🖼️ 演示

![首页截图](assets/screenshots/home.png)
![对话界面截图](assets/screenshots/chat.png)

[B站演示视频](https://b23.tv/bGXyhjU)

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=via007/bilibili-rag&type=Date)](https://star-history.com/#via007/bilibili-rag&Date)

---

## 📜 License

MIT — 仅供个人学习与技术研究，使用者需自行遵守 B 站相关协议与法律法规，禁止用于未授权的商业用途。

---

## 🗺️ TodoList

- [ ] 对话历史存储与会话管理
- [ ] 支持 B 站分 P 视频（多 cid）
- [ ] 适配更多 LLM 与向量模型
- [ ] 定时自动同步收藏夹新增视频
