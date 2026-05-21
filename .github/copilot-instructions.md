# Copilot Instructions

## Project Overview

Bilibili RAG — turns Bilibili (B站) favorites into a queryable personal knowledge base via ASR transcription, ChromaDB vector search, and an LLM QA chain.

**Stack:**
- **Backend:** FastAPI + LangChain + ChromaDB + SQLite (async via `aiosqlite`)
- **Frontend:** Next.js 16 + React 19 + Tailwind CSS 4 + TypeScript
- **ASR:** DashScope cloud (paraformer) or Ollama local (Whisper)
- **LLM/Embeddings:** DashScope-compatible OpenAI API (Alibaba Cloud Qwen)

---

## Commands

### Backend
```bash
# Start (from project root)
uvicorn app.main:app --reload

# Or via Python entry point
python app/main.py
```

### Frontend
```bash
cd frontend
npm install       # first time
npm run dev       # dev server
npm run build     # production build
npm run lint      # ESLint
```

### Diagnostics (run from project root)
```bash
python test/diagnose_rag.py         # inspect vector store state
python test/debug_asr_single.py     # test ASR on a single video
python test/sync_cache_vectors.py   # re-sync DB cache to vector store
```

### Standalone export scripts (run from project root)
```bash
python scripts/export_favorites_to_md.py
python scripts/export_douyin_to_md.py
python scripts/export_instapaper_to_md.py
```

---

## Architecture

### Backend layout (`app/`)

| Layer | Files | Responsibility |
|---|---|---|
| Entry | `main.py` | FastAPI app, CORS, lifespan, router registration |
| Config | `config.py` | `Settings` (pydantic-settings), singleton `settings` |
| DB | `database.py` | Async SQLAlchemy engine, `get_db` (DI), `get_db_context` (context manager) |
| Models | `models.py` | **Both** SQLAlchemy ORM models and Pydantic API models in one file |
| Routers | `routers/` | Thin HTTP layer; business logic lives in services |
| Services | `services/` | Core logic: Bilibili API, ASR, content fetching, RAG |

### Data flow for building the knowledge base

1. **Auth** (`routers/auth.py`) — QR login caches B站 cookies in `UserSession` (SQLite)
2. **Favorites** (`routers/favorites.py`) — lists/selects folders; stored in `FavoriteFolder` + `FavoriteVideo`
3. **Knowledge build** (`routers/knowledge.py`) — background task that:
   - Calls `ContentFetcher.fetch_content()` per video (2-tier fallback: ASR → basic info)
   - Caches result in `VideoCache` (SQLite)
   - Calls `RAGService.add_video()` to chunk text and embed into ChromaDB
4. **Chat** (`routers/chat.py`) — LangChain RAG chain: ChromaDB retrieval → Qwen LLM → answer with source citations

### Content fetching fallback chain (`services/content_fetcher.py`)

```
Audio URL → DashScope ASR
         ↘ (on failure) local download + ffmpeg transcode → ASR
         ↘ (on failure) video title + description (basic info)
```

### ASR backends (`services/asr.py`, `services/asr_local.py`)

- **Cloud:** `ASRService` → DashScope `paraformer-v2` (async file upload → poll)
- **Local:** `ASRLocalService` → Ollama Whisper HTTP API
- Selected via `.env`; both share the same interface used by `ContentFetcher`

### Frontend (`frontend/`)

- All API calls are centralized in `lib/api.ts`
- Components map 1:1 to UI panels (chat, export, login modal, etc.)
- No state management library — React `useState`/`useEffect` only

---

## Key Conventions

### Configuration

- All config lives in `.env` (copy from `.env.example`)
- `DASHSCOPE_API_KEY` is aliased as `OPENAI_API_KEY` in `config.py` (`AliasChoices`) — use `settings.openai_api_key` everywhere
- Code defaults: LLM `gpt-4-turbo`, embedding `text-embedding-3-small`. The `.env.example` sets `qwen3-max` / `text-embedding-v4` (Alibaba Cloud) — copy it before starting

### Database sessions

- In router handlers (FastAPI DI): `db: AsyncSession = Depends(get_db)`
- In background tasks or scripts: `async with get_db_context() as db:`
- Never use `async_session_factory` directly

### RAG service singleton

`RAGService` is instantiated once per process via `get_rag_service()` in `knowledge.py`. Do not create new instances in hot paths — embeddings and ChromaDB client are expensive to initialize.

### Models file (`app/models.py`)

Contains two kinds of models:
- **SQLAlchemy ORM** (`Base` subclasses) — `VideoCache`, `UserSession`, `FavoriteFolder`, `FavoriteVideo`
- **Pydantic** (`BaseModel` subclasses) — request/response schemas for the API

### Logging

Use `loguru` (`from loguru import logger`) throughout the backend. Logs go to stdout and `logs/app.log` (auto-rotating, 7-day retention).

### Runtime-generated files (not in git)

`data/` directory, `.bili_session.json`, `.instapaper_session.json` are created automatically at runtime. The `data/` directory contains SQLite DB, ChromaDB vector store, ASR temp files, and export outputs.

### System dependency

`ffmpeg` must be on `PATH` for audio transcoding. Without it, ASR falls back to basic video info only.

### Scripts must run from project root

All files under `test/` and `scripts/` use relative paths and `.env` loading — always `cd` to project root first.
