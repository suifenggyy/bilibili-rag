# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Summary

Bilibili RAG (BiliMind) — turns Bilibili favorites into a conversational personal knowledge base via ASR transcription, ChromaDB vector search, and LLM QA with source citations. Also supports Douyin, Instapaper, YouTube, and Xiaoyuzhou (小宇宙) content ingestion.

## Commands

### Backend
```bash
python -m uvicorn app.main:app --reload   # dev server
python app/main.py                         # alternative entry
pip install -r requirements.txt            # install deps (Python 3.11, ffmpeg required)
```

### Frontend
```bash
cd frontend && npm install   # first time
cd frontend && npm run dev   # dev server (localhost:3000)
cd frontend && npm run lint  # ESLint
cd frontend && npm run build # production build
```

### Tests
```bash
python -m unittest discover -s test -p 'test_*.py'                          # all tests
python -m unittest test.test_knowledge_pipeline_orchestrator -v              # single module
python -m unittest test.test_knowledge_pipeline_orchestrator.TestClassName.test_method -v  # single test
```

No pytest — tests use `unittest` with `unittest.IsolatedAsyncioTestCase` for async tests. No `pyproject.toml` or test config file.

### Diagnostics (run from project root)
```bash
python test/diagnose_rag.py           # inspect vector store state
python test/debug_asr_single.py       # test ASR on a single video (has CLI args)
python test/sync_cache_vectors.py     # re-sync DB cache → vector store
```

### Knowledge pipeline scripts
```bash
python scripts/run_knowledge_pipeline.py              # process Obsidian inbox
python scripts/run_knowledge_pipeline.py --watch      # watch mode
python scripts/run_knowledge_pipeline.py --file X.md  # single file
python scripts/import_collection_to_inbox.py --sources bilibili,instapaper
python scripts/generate_daily_report.py
python scripts/diagnose_knowledge_library.py          # diagnose/repair knowledge graph
```

All scripts under `test/` and `scripts/` must run from project root (they use relative paths and `.env`).

## Architecture

### Backend (`app/`)

| Layer | File(s) | Responsibility |
|---|---|---|
| Entry | `main.py` | FastAPI app, CORS, lifespan (creates dirs + init_db), router registration |
| Config | `config.py` | `Settings` (pydantic-settings singleton), loads `.env` |
| DB | `database.py` | Async SQLAlchemy engine, session helpers |
| Models | `models.py` | **Both** SQLAlchemy ORM models and Pydantic API schemas in one file |
| Routers | `routers/*.py` | Thin HTTP layer; business logic lives in services |
| Services | `services/*.py` | Core logic per platform/feature |
| Pipeline | `services/knowledge_pipeline/` | Obsidian knowledge pipeline (separate from RAG) |

### Data flow: building the knowledge base

1. **Auth** → QR login caches Bilibili cookies in `UserSession` (SQLite)
2. **Favorites** → list/select folders → stored in `FavoriteFolder` + `FavoriteVideo`
3. **Knowledge build** → background task: `ContentFetcher.fetch_content()` per video (ASR → fallback to basic info) → cache in `VideoCache` → `RAGService.add_video()` chunks & embeds into ChromaDB
4. **Chat** → LangChain RAG chain: ChromaDB retrieval → Qwen LLM → answer with source citations

### Multi-platform ingestion pattern

Each platform follows the same pattern: **platform service** (API metadata) → **fetcher** (download/extract content) → **ASR** (if audio) → **text postprocessor** → **summary** → **storage manager** (Markdown/cache). Platforms: Bilibili, Douyin, Instapaper, YouTube, Xiaoyuzhou.

### ASR factory (`services/asr_factory.py`)

Three backends selected via `.env` `ASR_BACKEND` or `--asr-backend` flag: `dashscope` (cloud paraformer), `ollama` (local Whisper HTTP), `whisper` (local openai-whisper). All share the same interface used by `ContentFetcher`.

### Text postprocessing factory (`services/text_postprocessor_factory.py`)

Three backends: `ollama`, `proxy` (OpenAI-compatible), `localopenai`. Selected via `TEXT_MODEL_BACKEND`. Internally delegates to the unified LLM factory's `ChatCompletionPostProcessor`.

### LLM factory (`services/llm/`)

Unified LLM abstraction layer. All LLM access should go through the factory — never instantiate `ChatOpenAI`, `OpenAI`, or `httpx` clients directly for LLM calls.

| Function | Returns | Use case |
|---|---|---|
| `get_llm_service(role)` | `OpenAICompatibleLLMService` | Async `complete()` / `stream()` |
| `get_langchain_chat(role)` | `ChatOpenAI` | LangChain chains (`prompt \| llm \| parser`) |
| `get_openai_client(role)` | `OpenAI` | Raw SDK sync/stream calls |
| `get_embeddings(role)` | `Embeddings` | DashScope or OpenAI embeddings |

**Role-based config**: Each role (e.g., `rag_qa`, `chat`, `chat_routing`, `knowledge_distill`) is independently configurable via env vars `LLM_<ROLE>_{BASE_URL,API_KEY,MODEL,TEMPERATURE,TIMEOUT,MAX_TOKENS}`. Unset roles fall back to global settings (`OPENAI_BASE_URL`+`LLM_MODEL` or `TEXT_MODEL_BASE_URL`+`TEXT_MODEL_NAME`). Zero `.env` changes needed for existing setups.

**Backward compat**: `create_text_postprocessor()` still works — it returns a `ChatCompletionPostProcessor` that implements the `TextPostProcessor` Protocol.

### RAG service (`services/rag.py`)

Singleton — embeddings and ChromaDB client are expensive to initialize. Access via `get_rag_service()` in `knowledge.py`. Uses `RecursiveCharacterTextSplitter` (chunk_size=1000, overlap=200, Chinese+English separators). LLM and embeddings obtained through the factory (`get_langchain_chat("rag_qa")`, `get_embeddings("rag_embedding")`).

### Knowledge pipeline (`services/knowledge_pipeline/`)

A second knowledge system beyond RAG — writes structured knowledge notes to an Obsidian vault. Pipeline: parse inbox Markdown → classify/distill → resolve topic paths via graph → render knowledge notes → update topic pages → log processing. Key components: `orchestrator.py`, `parser.py`, `classifier.py`, `knowledge_distiller.py`, `topic_graph.py`, `topic_path_resolver.py`, `knowledge_note_renderer.py`, `knowledge_note_store.py`, `topic_page_renderer.py`, `topic_rebuilder.py`.

### Frontend (`frontend/`)

Next.js 16 App Router + React 19 + Tailwind CSS 4. All API calls centralized in `lib/api.ts`. Components map 1:1 to UI panels. No state management library — `useState`/`useEffect` only. Session stored in `localStorage` under `bili_session`/`bili_user`.

## Key Conventions

- **Config**: All config in `.env` (copy from `.env.example`). `DASHSCOPE_API_KEY` is aliased as `OPENAI_API_KEY` via `AliasChoices` — always use `settings.openai_api_key`.
- **DB sessions**: `Depends(get_db)` in routers; `async with get_db_context() as db:` in background tasks/scripts. Never use `async_session_factory` directly.
- **RAG singleton**: Use `get_rag_service()` — do not create `RAGService` instances in hot paths.
- **LLM factory**: Use `get_llm_service(role)` / `get_langchain_chat(role)` / `get_embeddings(role)` from `app.services.llm` — do not instantiate `ChatOpenAI`, `OpenAI`, or call `httpx` directly for LLM calls.
- **Logging**: Use `loguru` (`from loguru import logger`). Logs to stdout + `logs/app.log` (7-day rotation).
- **System dependency**: `ffmpeg` must be on PATH for audio transcoding.
- **Models file**: `app/models.py` contains both SQLAlchemy ORM and Pydantic schemas — don't split them.
- **Runtime data**: `data/`, `.bili_session.json`, `.instapaper_session.json` are auto-created and gitignored.
