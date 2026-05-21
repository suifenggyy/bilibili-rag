# Frontend redesign spec

## Problem

The current frontend still centers the old workspace flow around favorites selection and chat, which no longer matches the intended usage path. The new UI should shift the primary experience to a document-centric workspace, expose grouped configuration in a dedicated page, and provide a staged "build knowledge base" page as a frontend-only workflow shell for later backend integration.

## Constraints

- The original request started as frontend-only, but this spec now assumes backend additions are allowed where required to support a complete document experience across all three sources.
- Reuse existing frontend styles and API clients where practical.
- Config editing is frontend-only for now and persists in `localStorage`.
- The knowledge-base build flow is UI-only in this iteration and does not call real build endpoints.

## Goals

1. Replace the current homepage/workspace layout with a workbench-style shell.
2. Make the default logged-in view show already captured Markdown documents across Bilibili, Douyin, and Instapaper.
3. Allow opening a Markdown document inside the app in a reader/detail view.
4. Add a dedicated configuration page with grouped fields derived from `app/config.py`.
5. Add a dedicated knowledge-base build page that presents the future workflow as a multi-step UI without real execution.

## Non-goals

- No server-side config persistence.
- No changes to ASR, export, RAG, or auth logic.
- No real build execution or progress polling in the new build page.

## Information architecture

The logged-in app becomes a route-based application with three primary pages:

1. **`/documents`** — primary landing page after login.
2. **`/settings`** — grouped editable config form backed by frontend storage.
3. **`/knowledge-builder`** — staged flow page with placeholder execution states.

Top-level navigation remains visible after login so the user can move between these pages without modal workflows or nested tab overload.

An additional document reader route supports in-app reading:

- **`/documents/[source]/[documentId]`** — Markdown reader/detail view

## Routing and auth behavior

- Unauthenticated users remain on the marketing/login entry page.
- Authenticated users entering `/` are redirected to `/documents`.
- Protected routes are:
  - `/documents`
  - `/documents/[source]/[documentId]`
  - `/settings`
  - `/knowledge-builder`
- If the existing session becomes invalid on any protected route, the frontend follows the current behavior: clear local auth state and send the user back to the login entry page.
- Deep links to a document reader route should succeed if session data is still valid; otherwise they redirect to login first.

## Page design

### 1. Documents page

This page follows the selected "workbench" direction rather than a pure document library.

#### Layout

- **Top summary area**
  - total documents
  - per-source counts
  - latest update time
  - config status summary
- **Left rail**
  - source filter: all / Bilibili / Douyin / Instapaper
  - keyword search
  - quick sort options such as latest updated
- **Main content**
  - document list cards/table
  - empty state when no document metadata is available
  - selection opens an in-app reader

#### Reader behavior

- Opening a document does **not** leave the app.
- The document view is shown as an in-app detail/read mode.
- The view should preserve an easy way back to the list.
- Markdown is rendered using existing frontend markdown rendering patterns.

#### Document source contract

The page must support real Markdown access for all three sources, so the implementation should normalize document data behind a unified frontend model and allow backend additions where current APIs are insufficient.

Required normalized fields:

- `id`
- `source` (`bilibili` / `douyin` / `instapaper`)
- `title`
- `summary` or short description
- `updatedAt`
- `folderLabel` or source-specific grouping label
- `downloadUrl` or `readUrl`
- `hasMarkdownBody`

Stable identifier contract:

- `source` is one of `bilibili`, `douyin`, `instapaper`
- `documentId` is a source-scoped stable string
- the pair `{source, documentId}` uniquely identifies a readable Markdown document
- list and read APIs must use the same identifier pair so route generation stays stable

Source expectations:

- **Bilibili**: can start from existing export job history, but should expose a document-oriented list/read contract instead of making the frontend infer documents from job rows.
- **Douyin**: needs backend support for listing completed exported documents and reading/downloading Markdown content.
- **Instapaper**: needs backend support for listing completed exported documents and reading/downloading Markdown content.

The reader route must receive enough metadata to load and render the actual Markdown body rather than a placeholder shell.

#### Document API contract

The later implementation plan should assume a narrow document API surface:

- **List documents**
  - returns normalized metadata rows
  - supports source filtering and optional keyword search
- **Read document**
  - returns normalized metadata plus Markdown body
- **Error behavior**
  - `404` when `{source, documentId}` does not exist
  - `401` when session/auth is invalid
  - readable frontend empty/error states for unavailable content

### 2. Configuration page

This page is a dedicated editable settings surface derived from the existing backend config structure, but stored only on the frontend for now.

#### Groups

- LLM / Embedding
- DashScope ASR
- Ollama
- Local Whisper
- Douyin
- Instapaper
- App
- Storage

#### Exact field coverage

The settings page should expose these config fields grouped by purpose:

- **LLM / Embedding**
  - `openai_api_key`
  - `openai_base_url`
  - `llm_model`
  - `embedding_model`
- **DashScope ASR**
  - `asr_backend`
  - `dashscope_base_url`
  - `asr_model`
  - `asr_timeout`
  - `asr_model_local`
  - `asr_input_format`
- **Ollama**
  - `ollama_base_url`
  - `ollama_asr_model`
  - `ollama_asr_language`
  - `ollama_text_base_url`
  - `ollama_text_model`
  - `ollama_text_prompt`
  - `ollama_text_timeout`
- **Local Whisper**
  - `whisper_model`
  - `whisper_language`
- **Douyin**
  - `douyin_cookie`
  - `douyin_evil0ctal_url`
  - `douyin_output_dir`
- **Instapaper**
  - `instapaper_consumer_key`
  - `instapaper_consumer_secret`
  - `instapaper_email`
  - `instapaper_password`
- **App**
  - `app_host`
  - `app_port`
  - `debug`
- **Storage**
  - `content_workspace_root`
  - `content_workspace_max_size_bytes`
  - `content_workspace_retention_days`
  - `collection_output_dir`
  - `database_url`
  - `chroma_persist_directory`

#### Behavior

- Fields are initialized from sensible frontend defaults matching current config definitions.
- Saved values persist to `localStorage`.
- Reads across the frontend should prefer the saved frontend config when relevant.
- Saving shows explicit success feedback.
- Reset-to-default behavior is allowed if straightforward, but must remain purely frontend-side.
- Existing export-related forms should initialize from saved frontend config where matching fields exist.
- Frontend-saved config should affect:
  - the new Settings page
  - the new Build Knowledge Base page
  - existing export forms where there is a matching field already exposed in the UI
- Frontend-saved config should **not** silently alter unrelated request behavior beyond values the existing forms already submit.

#### Security/UX note

Sensitive fields such as keys and passwords remain editable in the UI but are stored only in the browser, not sent to the backend in this phase. The page should make that scope clear through copy or field grouping.

### 3. Build Knowledge Base page

This page introduces the future workflow without connecting to live build endpoints yet.

#### Steps

1. **Select scope**
   - choose source types
   - choose folders or document groups
   - show estimated item count
2. **Confirm configuration**
   - show the current frontend-saved config snapshot that will be used later
   - allow quick jump to Settings when required fields are missing
3. **Review plan**
   - display a local-only summary card with selected sources, item count, and config highlights
4. **Simulated execution/result**
   - show staged UI states: idle, validating, ready, simulated-running, simulated-complete

#### Behavior

- Stepper UI should clearly indicate current, completed, and upcoming steps.
- User can move through the flow locally.
- Final action presents a non-executing placeholder state rather than calling the backend.
- Copy should make it clear that this phase is UI-only.
- Local states/transitions must be explicit so the later real integration can map to them directly:
  - `idle`
  - `editingScope`
  - `reviewingConfig`
  - `reviewingPlan`
  - `simulating`
  - `simulatedComplete`

#### Why this matters

The goal is to implement the interaction model and layout now so the later backend hookup can reuse the same page and state structure.

## Component boundaries

The redesign should reduce the amount of page-level state concentrated in `frontend/app/page.tsx`.

Recommended units:

- **App shell/home container**
  - owns login-derived shell state and shared navigation chrome
- **Documents workbench**
  - owns document filters, list fetching, normalized list rendering, and empty/error states for `/documents`
- **Document reader**
  - owns data loading and markdown rendering for `/documents/[source]/[documentId]`
- **Configuration page**
  - owns grouped form rendering and persistence actions
- **Build flow page**
  - owns step navigation and placeholder summary/result states
- **Frontend config store/helper**
  - centralizes defaults, read/write/reset behavior for `localStorage`

Each unit should have a single clear purpose and a narrow interface so later backend integration does not require reshaping the whole app.

## State model

Three major state domains should remain distinct:

1. **Login/session state** — existing auth/session handling
2. **Document browsing state** — filters, selection, reader mode
3. **Frontend config state** — grouped config values and persistence

The build flow page may own its own transient UI step state, but it should read config values through the shared frontend config helper rather than duplicating them.

## Backend additions required for the document experience

To satisfy the requirement that all three sources can directly open real Markdown, backend support is needed where APIs do not already exist.

Required support:

- list exported Markdown documents for Bilibili, Douyin, and Instapaper through a document-oriented response shape
- read or fetch a single Markdown document body for each source
- expose stable identifiers and metadata needed by the document list and reader routes

These additions should be narrowly scoped to document listing/reading and should not alter the actual export logic.

## Reuse strategy

- Keep the existing auth/login flow.
- Reuse current styling language, spacing, buttons, cards, and input styles from the existing frontend.
- Reuse existing markdown rendering approach from the chat experience where appropriate.
- Reuse/export-related API client pieces only where they already fit the frontend-only boundaries.

## Error handling and empty states

- Missing document data should show explicit empty or unavailable states, never a broken reader.
- Config load failures from malformed `localStorage` should fall back to defaults.
- Save/reset actions should provide clear user feedback.
- The build flow page should never imply that a real build has started.

## Testing/validation targets

The implementation should be considered complete when:

1. Logged-in users land on `/documents`.
2. The top navigation switches between `/documents`, `/settings`, and `/knowledge-builder`.
3. The Documents page renders a unified list view across Bilibili, Douyin, and Instapaper and supports in-app detail mode with real Markdown content.
4. The Configuration page renders grouped fields and persists edits to `localStorage`.
5. Reloading the page restores saved frontend config.
6. The Build Knowledge Base page supports full local step navigation without calling backend build logic.
7. Existing frontend build/lint commands still pass.

## Implementation notes for later planning

- Favor incremental file extraction from the current `page.tsx` rather than rewriting everything in one large component.
- Introduce normalized types for unified document list items early so page code stays simple.
- Keep the UI copy explicit about what is real today versus what is staged for later integration.
- In the UI-only build flow, the estimated item count should come from the selected document records already loaded for the Documents experience rather than from new backend build APIs.
