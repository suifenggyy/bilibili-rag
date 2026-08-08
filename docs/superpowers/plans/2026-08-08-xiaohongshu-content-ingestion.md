# Xiaohongshu Content Ingestion Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add standalone Xiaohongshu note, creator, and keyword ingestion with Cookie/QR authentication, image downloads, popular comments, video ASR, and knowledge-pipeline-ready Markdown output.

**Architecture:** Keep browser authentication/signing, signed API transport, domain discovery/normalization, per-note media enrichment, and CLI/export rendering in separate units. Reuse `ContentStorageManager`, `create_asr_service`, the existing text postprocessor and summary service, and frontmatter helpers; do not depend on LittleCrawler at runtime.

**Tech Stack:** Python 3.11, Playwright async API, httpx, tenacity, asyncio, dataclasses, unittest, existing ffmpeg/ASR/LLM services.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/services/xhs_models.py` | Stable dataclasses and enums shared by XHS modules. |
| `app/services/xhs_auth.py` | Cookie/QR/saved-session selection and Playwright lifecycle. |
| `app/services/xhs_client.py` | Serialized page signing, signed API requests, error classification, HTML/media requests. |
| `app/services/xhs.py` | URL parsing, candidate discovery, reference merging, pagination, detail normalization. |
| `app/services/xhs_fetcher.py` | Image/video workspace caching, ffmpeg, ASR, postprocessing, comments, summary. |
| `scripts/export_xhs_to_md.py` | CLI validation, dependency assembly, global limit, orchestration, final media copy, Markdown rendering, exit code. |
| `test/test_xhs_models.py` | DTO and deterministic merge tests. |
| `test/test_xhs_auth.py` | Auth precedence, cookie parsing, QR persistence, cleanup tests. |
| `test/test_xhs_client.py` | Signing lock, request headers, error classification, retries. |
| `test/test_xhs_service.py` | URL parsing, discovery, pagination, fallback, normalization. |
| `test/test_xhs_fetcher.py` | Cache, media, ASR, warning degradation, comments and summary. |
| `test/test_export_xhs_to_md.py` | Mixed-source orchestration, global limit, rendering, file layout and exit status. |
| `app/config.py` | XHS settings. |
| `.env.example` | XHS configuration examples. |
| `.gitignore` | Ignore `.xhs_session.json`. |
| `README.md` | Usage, authentication, output and anti-bot notes. |

## Chunk 1: Contracts, Authentication, and Signed Transport

### Task 1: Define XHS contracts and merge behavior

**Files:**
- Create: `app/services/xhs_models.py`
- Create: `test/test_xhs_models.py`

- [ ] **Step 1: Write failing dataclass and merge tests**

```python
# test/test_xhs_models.py
import unittest

from app.services.xhs_models import (
    XhsDiscoveryKind,
    XhsNoteReference,
    merge_note_references,
)


class XhsReferenceMergeTests(unittest.TestCase):
    def test_explicit_url_token_wins_over_search_token(self):
        search = XhsNoteReference(
            note_id="abc",
            source_url="https://www.xiaohongshu.com/explore/abc",
            xsec_token="search-token",
            xsec_source="pc_search",
            discovery_kind=XhsDiscoveryKind.SEARCH,
        )
        explicit = XhsNoteReference(
            note_id="abc",
            source_url="https://www.xiaohongshu.com/explore/abc?xsec_token=url-token",
            xsec_token="url-token",
            xsec_source="pc_feed",
            discovery_kind=XhsDiscoveryKind.EXPLICIT,
        )

        merged = merge_note_references(search, explicit)

        self.assertEqual("url-token", merged.xsec_token)
        self.assertEqual(XhsDiscoveryKind.EXPLICIT, merged.discovery_kind)

    def test_first_timestamp_is_preserved_when_richer_reference_has_none(self):
        creator = XhsNoteReference(
            note_id="abc",
            source_url="",
            published_at=1700000000,
            discovery_kind=XhsDiscoveryKind.CREATOR,
        )
        explicit = XhsNoteReference(
            note_id="abc",
            source_url="https://www.xiaohongshu.com/explore/abc",
            discovery_kind=XhsDiscoveryKind.EXPLICIT,
        )

        self.assertEqual(
            1700000000,
            merge_note_references(creator, explicit).published_at,
        )
```

- [ ] **Step 2: Run the tests and verify the missing module failure**

Run: `python -m unittest test.test_xhs_models -v`

Expected: `ModuleNotFoundError: No module named 'app.services.xhs_models'`.

- [ ] **Step 3: Implement focused shared contracts**

Create:

```python
# app/services/xhs_models.py
from dataclasses import dataclass, field, replace
from enum import Enum


class XhsDiscoveryKind(str, Enum):
    EXPLICIT = "explicit"
    SEARCH = "search"
    CREATOR = "creator"


_DISCOVERY_PRIORITY = {
    XhsDiscoveryKind.EXPLICIT: 3,
    XhsDiscoveryKind.SEARCH: 2,
    XhsDiscoveryKind.CREATOR: 1,
}


@dataclass(frozen=True)
class XhsNoteReference:
    note_id: str
    source_url: str = ""
    xsec_token: str = ""
    xsec_source: str = ""
    published_at: int | None = None
    discovery_kind: XhsDiscoveryKind = XhsDiscoveryKind.EXPLICIT


@dataclass(frozen=True)
class XhsNoteInfo:
    note_id: str
    title: str
    description: str = ""
    author_name: str = ""
    author_id: str = ""
    published_at: int | None = None
    note_type: str = ""
    tags: tuple[str, ...] = ()
    liked_count: str = ""
    collected_count: str = ""
    comment_count: str = ""
    image_urls: tuple[str, ...] = ()
    video_urls: tuple[str, ...] = ()
    source_url: str = ""
    xsec_token: str = ""
    xsec_source: str = ""


@dataclass
class XhsNoteContent:
    note: XhsNoteInfo
    image_paths: list[str] = field(default_factory=list)
    original_description: str = ""
    formatted_tags: str = ""
    transcript: str = ""
    content_source: str = "basic_info"
    summary_block: str = ""
    asr_raw_text: str = ""
    comments_section: str = ""
    warnings: list[str] = field(default_factory=list)


def merge_note_references(
    current: XhsNoteReference,
    incoming: XhsNoteReference,
) -> XhsNoteReference:
    if current.note_id != incoming.note_id:
        raise ValueError("cannot merge references with different note IDs")
    preferred = max(
        (current, incoming),
        key=lambda item: (
            bool(item.xsec_token),
            _DISCOVERY_PRIORITY[item.discovery_kind],
            bool(item.source_url),
        ),
    )
    source_url = preferred.source_url or current.source_url or incoming.source_url
    published_at = current.published_at or incoming.published_at
    return replace(preferred, source_url=source_url, published_at=published_at)
```

- [ ] **Step 4: Add tests for different IDs and empty-token URL preference**

Add assertions that different IDs raise `ValueError`, and that a canonical
full URL is retained when the token-preferred reference has only a bare URL.
Also verify search-token precedence over creator-token precedence.
Adjust `merge_note_references` minimally until all deterministic rules in the
spec pass.

- [ ] **Step 5: Run the contract tests**

Run: `python -m unittest test.test_xhs_models -v`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/xhs_models.py test/test_xhs_models.py
git commit -m "feat: add Xiaohongshu ingestion contracts"
```

### Task 2: Implement Cookie, saved-session, and QR authentication

**Files:**
- Create: `app/services/xhs_auth.py`
- Create: `test/test_xhs_auth.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing precedence and cookie parsing tests**

Test these public interfaces:

```python
from app.services.xhs_auth import (
    XhsAuthOptions,
    XhsAuthService,
    parse_cookie_string,
)


class XhsAuthTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_cookie_string_ignores_malformed_segments(self):
        self.assertEqual(
            {"a1": "one", "web_session": "two=three"},
            parse_cookie_string("a1=one; bad; web_session=two=three"),
        )

    async def test_force_login_ignores_explicit_cookie_and_saved_session(self):
        service = XhsAuthService(
            XhsAuthOptions(force_login=True, cookie="a1=explicit"),
            session_file=self.session_path,
            playwright_factory=self.playwright_factory,
        )
        session = await service.open()
        self.assertEqual("qr-cookie", session.cookie_dict["a1"])
        self.assertTrue(self.fake_page.qr_login_waited)
```

Also test: explicit cookie beats environment/saved session; environment beats
saved session; saved session is loaded; non-interactive mode without a session
raises `XhsAuthenticationError`; successful QR login writes JSON; `close()` is
idempotent; an invalid explicit/environment/saved cookie that does not produce
an authenticated page raises `XhsAuthenticationError`; and no-session
interactive mode automatically enters QR flow even without `force_login`.

- [ ] **Step 2: Run tests and verify missing imports**

Run: `python -m unittest test.test_xhs_auth -v`

Expected: import failure for `app.services.xhs_auth`.

- [ ] **Step 3: Implement the authentication API**

Use these public shapes:

```python
@dataclass(frozen=True)
class XhsAuthOptions:
    force_login: bool = False
    cookie: str = ""
    env_cookie: str = ""
    interactive: bool = True
    login_timeout: float = 120.0
    headless: bool = True


@dataclass(frozen=True)
class XhsBrowserSession:
    page: Page
    cookie_dict: dict[str, str]
    cookie_header: str
    user_agent: str


class XhsAuthenticationError(RuntimeError):
    pass


class XhsAuthService:
    async def open(self) -> XhsBrowserSession: ...
    async def close(self) -> None: ...
    async def __aenter__(self) -> XhsBrowserSession: ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...
```

Implementation sequence:

1. Resolve mode with `force_login` first.
2. Start Playwright and create a context.
3. Inject cookie-mode cookies for `.xiaohongshu.com`.
4. Navigate to `https://www.xiaohongshu.com/`.
5. For every cookie or saved-session mode, verify authenticated state from
   page/session evidence; do not treat page load alone as success.
6. For QR mode, wait until `web_session` changes or authenticated cookies
   appear; timeout raises `XhsAuthenticationError`.
7. Verify `window.mnsv2` is callable.
8. Persist QR cookies with `0600` file permissions.
9. Return `XhsBrowserSession`.

Inject a `playwright_factory` and `is_interactive` callable so tests do not
launch a browser or depend on `sys.stdin.isatty()`.

- [ ] **Step 4: Ignore the session file**

Add:

```gitignore
# Xiaohongshu login session cache (contains sensitive cookies)
.xhs_session.json
```

- [ ] **Step 5: Run authentication tests**

Run: `python -m unittest test.test_xhs_auth -v`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/xhs_auth.py test/test_xhs_auth.py .gitignore
git commit -m "feat: add Xiaohongshu browser authentication"
```

### Task 3: Implement serialized signing and the API client

**Files:**
- Create: `app/services/xhs_client.py`
- Create: `test/test_xhs_client.py`

- [ ] **Step 1: Write failing signing and error tests**

Cover:

- Two concurrent `build_signed_headers()` calls never overlap the fake page's
  `evaluate()` section.
- GET signing uses path plus encoded query data.
- POST signing uses compact JSON with `ensure_ascii=False`.
- `X-S-Common` generation consumes the `a1` cookie and the page's `b1`
  localStorage value; changing either changes the generated header.
- Returned headers include `X-S`, `X-T`, `X-S-Common`, and `X-B3-Traceid`.
- HTTP 461/471 raises `XhsRiskControlError` without retry.
- API authentication errors raise `XhsAuthenticationError`; configured IP
  block codes raise `XhsRiskControlError`; malformed JSON raises
  `XhsDataFetchError`; note-abnormal codes raise
  `XhsNoteUnavailableError`.
- transient `httpx.TransportError` is retried at most three times.
- streamed media rejects non-200/206 responses and oversized files.

Use `unittest.mock.AsyncMock`; do not use a live page or network.

- [ ] **Step 2: Run tests and verify they fail**

Run: `python -m unittest test.test_xhs_client -v`

Expected: missing `app.services.xhs_client`.

- [ ] **Step 3: Implement focused exceptions and signing**

```python
class XhsClientError(RuntimeError): ...
class XhsRiskControlError(XhsClientError): ...
class XhsAuthenticationError(XhsClientError): ...
class XhsDataFetchError(XhsClientError): ...
class XhsNoteUnavailableError(XhsDataFetchError): ...


class XhsClient:
    def __init__(
        self,
        session: XhsBrowserSession,
        *,
        http_client: httpx.AsyncClient | None = None,
        request_interval: float = 1.0,
        max_media_bytes: int = 500 * 1024 * 1024,
    ): ...

    async def build_signed_headers(
        self,
        uri: str,
        *,
        params: dict | None = None,
        payload: dict | None = None,
    ) -> dict[str, str]: ...
```

Keep the signature helpers private in this file. Derive `X-S` by invoking the
page's existing `window.mnsv2`, then construct the companion fields required
by the web API. Read `a1` from `session.cookie_dict` and `b1` from
`window.localStorage`; both are mandatory inputs to `X-S-Common`.
Reimplement only the minimal encoding required by these headers; do not import
LittleCrawler modules.

- [ ] **Step 4: Add endpoint methods**

Implement:

```python
async def search_notes(self, keyword, page, page_size, search_id) -> dict
async def get_note_detail(self, reference: XhsNoteReference) -> dict
async def get_note_html(self, reference, *, authenticated: bool) -> str
async def get_creator_profile_html(
    self,
    user_id: str,
    xsec_token: str = "",
    xsec_source: str = "",
) -> str
async def get_creator_notes(self, user_id, cursor, xsec_token, xsec_source) -> dict
async def get_comments(self, note_id, xsec_token, cursor="") -> dict
async def resolve_shared_url(self, url: str) -> str
async def download_media(self, url: str, destination: Path) -> Path
async def close(self) -> None
```

Use one reusable `httpx.AsyncClient`, preserve the browser user agent and
cookie header, write media to `destination.with_suffix(destination.suffix +
".part")`, validate size, then atomically replace the destination.
Pin error classification in one private response parser shared by GET and
POST so CAPTCHA, IP-block, malformed-response, authentication, and
note-unavailable semantics cannot diverge across endpoints.

- [ ] **Step 5: Run client tests**

Run: `python -m unittest test.test_xhs_client -v`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/xhs_client.py test/test_xhs_client.py
git commit -m "feat: add signed Xiaohongshu API client"
```

## Chunk 2: Discovery, Normalization, and Content Enrichment

### Task 4: Implement mixed-source discovery and note normalization

**Files:**
- Create: `app/services/xhs.py`
- Create: `test/test_xhs_service.py`

- [ ] **Step 1: Write failing URL parser tests**

Cover full `/explore/<id>` URLs, redirected shared URLs, bare note IDs, full
creator URLs, bare creator IDs, query extraction, and invalid input. The note
parser must preserve `xsec_token` and `xsec_source`.

- [ ] **Step 2: Write failing pagination and normalization tests**

Use a fake `XhsClient` to verify:

- search pagination stops on `has_more=False`;
- creator pagination carries the returned cursor;
- `after_date` stops only when a usable list timestamp is older;
- missing timestamps are retained;
- discovery is a lazy async iterator, so breaking iteration prevents the next
  remote page request;
- detail mapping extracts text, tags, engagement values, images and video URLs;
- detail API empty response falls back to HTML;
- a tokenless explicit URL first tries unauthenticated HTML, then authenticated
  HTML, and never invents a token.

- [ ] **Step 3: Run tests and verify missing module failure**

Run: `python -m unittest test.test_xhs_service -v`

Expected: import failure for `app.services.xhs`.

- [ ] **Step 4: Implement parsers and discovery interfaces**

```python
class XhsService:
    def __init__(self, client: XhsClient): ...

    async def references_from_urls(
        self,
        urls: list[str],
    ) -> list[XhsNoteReference]: ...

    async def iter_search_references(
        self,
        keyword: str,
        *,
        after_date: str | None = None,
    ) -> AsyncIterator[XhsNoteReference]: ...

    async def iter_creator_references(
        self,
        creator: str,
        *,
        after_date: str | None = None,
    ) -> AsyncIterator[XhsNoteReference]: ...

    async def resolve_note(
        self,
        reference: XhsNoteReference,
    ) -> XhsNoteInfo: ...
```

Keep global deduplication and global `--limit` out of this service; the CLI
owns those batch policies by breaking the lazy iterator after enough unique
references have been collected.

- [ ] **Step 5: Implement response normalization**

Create small private helpers:

```python
def _normalize_note(raw: dict, reference: XhsNoteReference) -> XhsNoteInfo: ...
def _extract_video_urls(raw: dict) -> tuple[str, ...]: ...
def _extract_tags(raw: dict) -> tuple[str, ...]: ...
def _extract_note_from_html(note_id: str, html: str) -> dict | None: ...
```

Use defensive type checks for remote data, but raise
`XhsNoteUnavailableError` when neither API nor HTML contains a usable note.

- [ ] **Step 6: Run service tests**

Run: `python -m unittest test.test_xhs_service -v`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add app/services/xhs.py test/test_xhs_service.py
git commit -m "feat: add Xiaohongshu discovery service"
```

### Task 5: Implement image download, comments, video ASR, and summary

**Files:**
- Create: `app/services/xhs_fetcher.py`
- Create: `test/test_xhs_fetcher.py`

- [ ] **Step 1: Write failing cache and degradation tests**

Test:

- Existing `image_01.jpg` is reused without download.
- Existing `asr_raw.txt` skips video download and ASR.
- Existing `video.mp4` skips download but runs ffmpeg/ASR.
- Image failure appends a warning and preserves the note.
- Video download/ffmpeg/ASR failure returns `basic_info`.
- Successful ASR stores raw and corrected text and returns source `asr`.
- Comment failure is a warning; top-level comment limit is enforced.
- Comment pagination advances the returned cursor, stops at `has_more=False`,
  and never fetches beyond `comment_limit`.
- Summary input contains description, transcript, and comments.

- [ ] **Step 2: Run tests and verify missing module failure**

Run: `python -m unittest test.test_xhs_fetcher -v`

Expected: import failure for `app.services.xhs_fetcher`.

- [ ] **Step 3: Implement the fetcher constructor and main flow**

```python
class XhsContentFetcher:
    def __init__(
        self,
        client: XhsClient,
        asr_service,
        *,
        text_postprocessor: TextPostProcessor | None = None,
        summary_service: ContentSummaryService | None = None,
        storage_manager: ContentStorageManager | None = None,
        comment_limit: int = 20,
    ): ...

    async def fetch_content(self, note: XhsNoteInfo) -> XhsNoteContent: ...
```

Use source `"xhs"` and a storage title containing both title and note ID.
Never treat optional media/comment/LLM failures as a core item failure.

- [ ] **Step 4: Implement media and ASR helpers**

Implement:

```python
async def _download_images(self, note, content) -> None
async def _transcribe_video(self, note, content) -> None
async def _fetch_comments(self, note, content) -> None
async def _postprocess_asr_text(self, note_id, text, title) -> str
async def _summarize_content(self, note_id, text) -> str
def _to_wav(self, source: str, destination: str) -> bool
```

Use the existing `ContentStorageManager` cache methods and the same ffmpeg
shape as other fetchers: mono, 16 kHz, `pcm_s16le`, bounded timeout.

Format comments as:

```markdown
## 热门评论

- **nickname**：comment text
```

Do not fetch nested replies.
`_fetch_comments` loops over `client.get_comments()` pages, appends only
top-level comments, stops at `comment_limit`, `has_more=False`, an empty
cursor, or a repeated cursor, and records one warning if a later page fails.

- [ ] **Step 5: Run fetcher tests**

Run: `python -m unittest test.test_xhs_fetcher -v`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/services/xhs_fetcher.py test/test_xhs_fetcher.py
git commit -m "feat: add Xiaohongshu content enrichment"
```

## Chunk 3: Export Orchestration, Configuration, and Documentation

### Task 6: Build CLI orchestration and Markdown output

**Files:**
- Create: `scripts/export_xhs_to_md.py`
- Create: `test/test_export_xhs_to_md.py`

- [ ] **Step 1: Record the dirty-worktree baseline**

Run:

```bash
git --no-pager status --short
git --no-pager diff --check
```

Expected: record all pre-existing user changes before touching shared files.
Never use `git add -A`; every later commit stages only the exact XHS paths
listed in that task. If a shared file is already modified at baseline (as
`app/config.py` and `.env.example` are in the current worktree), do not stage
that file in an automated commit; leave the combined file modification
uncommitted and report it for user review.

- [ ] **Step 2: Write failing parser and global-limit tests**

Test:

- no source input causes parser validation failure;
- an interleaved command such as
  `--keyword first --note-url second --creator third --keyword fourth`
  produces four ordered source requests in exactly that order;
- duplicate references merge deterministically;
- global `--limit` stops discovery after N unique note IDs;
- a partial export counts as partial but returns exit code `0`;
- a core detail or Markdown failure counts as failed and returns exit code `1`;
- task-level auth failure returns exit code `1`.

- [ ] **Step 3: Write failing Markdown/file-layout tests**

Create a temporary `ContentStorageManager` and assert:

```text
<output>/xhs/2026-08-08/Example_abc.md
<output>/xhs/2026-08-08/Example_abc.media/image_01.jpg
```

Assert frontmatter contains `platform: xhs`, source URL, date and plain summary;
the body contains original text, tags, summary, transcript, top-level comments,
and relative image links. Assert video/WAV cache files are not copied.

- [ ] **Step 4: Run tests and verify missing script failure**

Run: `python -m unittest test.test_export_xhs_to_md -v`

Expected: import failure for `scripts.export_xhs_to_md`.

- [ ] **Step 5: Implement parser and batch result contracts**

Create:

```python
class SourceKind(str, Enum):
    NOTE_URL = "note_url"
    CREATOR = "creator"
    KEYWORD = "keyword"


@dataclass(frozen=True)
class SourceRequest:
    kind: SourceKind
    value: str


@dataclass
class ExportCounts:
    successful: int = 0
    partial: int = 0
    failed: int = 0
    skipped: int = 0


def build_parser() -> argparse.ArgumentParser: ...
def build_markdown(content: XhsNoteContent, relative_images: list[str]) -> str: ...
async def run_export(
    args,
    *,
    dependency_factory: "ExportDependencyFactory | None" = None,
) -> ExportCounts: ...
def main() -> int: ...
```

Define skipped as a duplicate note reference removed before detail resolution.
Implement one custom `argparse.Action` used by all three source flags; it
appends `SourceRequest(kind, value)` to one `args.source_requests` list at parse
time, preserving cross-flag order.

- [ ] **Step 6: Add an explicit dependency-injection seam**

Define a small factory protocol/dataclass whose methods create auth, client,
service, fetcher, ASR, summary and storage dependencies. Production uses
`DefaultExportDependencyFactory`; tests pass a fake factory that returns only
in-memory fakes and temporary storage.

```python
class ExportDependencyFactory(Protocol):
    def create_auth(self, args) -> XhsAuthService: ...
    def create_client(self, session, args) -> XhsClient: ...
    def create_service(self, client) -> XhsService: ...
    def create_asr(self, args): ...
    def create_text_postprocessor(self): ...
    def create_summary_service(self) -> ContentSummaryService: ...
    def create_storage(self, args) -> ContentStorageManager: ...
    def create_fetcher(
        self,
        client,
        asr_service,
        text_postprocessor,
        summary_service,
        storage,
        args,
    ) -> XhsContentFetcher: ...
```

Automated tests must call `run_export(..., dependency_factory=fake_factory)`
and assert no Playwright, network, ASR model, or LLM constructor is reached.

- [ ] **Step 7: Implement ordered discovery and dedup**

For each `SourceRequest` in `args.source_requests` order:

1. Yield references.
2. Merge duplicates with `merge_note_references`.
3. Increment `skipped` for duplicate references.
4. Stop all remaining discovery when the unique map reaches global `--limit`.

Then resolve and enrich unique notes with an `asyncio.Semaphore`. Keep result
reporting deterministic by storing each task result under its original unique
order index.

- [ ] **Step 8: Implement final media copy and Markdown write**

For each resolved note:

1. Build the Markdown path with `ContentStorageManager.build_markdown_path`.
2. Create `<stem>.media/`.
3. Copy successful image workspace files with `shutil.copy2`.
4. Build relative POSIX paths.
5. Write UTF-8 Markdown atomically through a `.part` file and `Path.replace`.

Classify a note as partial when `content.warnings` is non-empty; otherwise
successful. A detail-resolution or final-write exception is failed.

- [ ] **Step 9: Assemble existing services**

Use:

```python
asr_service = create_asr_service(args.asr_backend, ...)
text_postprocessor = create_text_postprocessor()
summary_service = ContentSummaryService()
storage_manager = ContentStorageManager(export_root=args.output_dir)
```

Put this assembly only in `DefaultExportDependencyFactory`. Wrap auth and
client resources in `async with` / `finally`. Do not instantiate LLM clients
directly.

- [ ] **Step 10: Run export tests**

Run: `python -m unittest test.test_export_xhs_to_md -v`

Expected: all tests pass.

- [ ] **Step 11: Inspect and commit only XHS export paths**

```bash
git --no-pager status --short
git --no-pager diff --check -- scripts/export_xhs_to_md.py test/test_export_xhs_to_md.py
git add scripts/export_xhs_to_md.py test/test_export_xhs_to_md.py
git commit -m "feat: add Xiaohongshu export command"
```

### Task 7: Add configuration and usage documentation

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `test/test_xhs_auth.py`

- [ ] **Step 1: Write failing settings-default assertions**

Add tests that settings expose:

```python
settings.xhs_cookie
settings.xhs_after_date
settings.xhs_comment_limit
settings.xhs_request_interval
settings.xhs_session_file
```

Expected defaults:

```text
xhs_cookie=""
xhs_after_date=""
xhs_comment_limit=20
xhs_request_interval=1.0
xhs_session_file=".xhs_session.json"
```

- [ ] **Step 2: Run the targeted test and verify failure**

Run: `python -m unittest test.test_xhs_auth -v`

Expected: missing XHS settings attributes.

- [ ] **Step 3: Add settings**

Add adjacent to other platform export settings:

```python
xhs_cookie: str = Field(default="", env="XHS_COOKIE")
xhs_after_date: str = Field(default="", env="XHS_AFTER_DATE")
xhs_comment_limit: int = Field(default=20, env="XHS_COMMENT_LIMIT")
xhs_request_interval: float = Field(default=1.0, env="XHS_REQUEST_INTERVAL")
xhs_session_file: str = Field(default=".xhs_session.json", env="XHS_SESSION_FILE")
```

- [ ] **Step 4: Document environment variables**

Add a Xiaohongshu section to `.env.example` explaining Cookie acquisition,
saved QR session behavior, date filtering, comment count, and conservative
request interval. Never include a real cookie.

- [ ] **Step 5: Document CLI usage**

Update `README.md` with:

```bash
python scripts/export_xhs_to_md.py --note-url "https://www.xiaohongshu.com/explore/..."
python scripts/export_xhs_to_md.py --creator "https://www.xiaohongshu.com/user/profile/..."
python scripts/export_xhs_to_md.py --keyword "知识管理" --limit 20
python scripts/export_xhs_to_md.py --login --note-url "..."
```

Describe Playwright browser installation (`playwright install chromium` if
needed), output layout, image copying, video ASR, Cookie/QR precedence, and
CAPTCHA/risk-control recovery.

- [ ] **Step 6: Run settings/auth tests**

Run: `python -m unittest test.test_xhs_auth -v`

Expected: all tests pass.

- [ ] **Step 7: Inspect and commit only configuration/documentation paths**

```bash
git --no-pager status --short
git --no-pager diff --check -- app/config.py .env.example README.md test/test_xhs_auth.py
# Stage only files that were clean in the recorded baseline.
git add README.md test/test_xhs_auth.py
git commit -m "docs: configure Xiaohongshu ingestion"
```

Leave XHS hunks in baseline-dirty shared files unstaged rather than risking a
commit containing unrelated user work.

### Task 8: Run targeted and full regression validation

**Files:**
- Modify only if a failure is directly caused by the new XHS implementation.

- [ ] **Step 1: Run all XHS tests together**

Run:

```bash
python -m unittest \
  test.test_xhs_models \
  test.test_xhs_auth \
  test.test_xhs_client \
  test.test_xhs_service \
  test.test_xhs_fetcher \
  test.test_export_xhs_to_md -v
```

Expected: all XHS tests pass.

- [ ] **Step 2: Run related shared-service regressions**

Run:

```bash
python -m unittest \
  test.test_asr_backend_factory \
  test.test_content_storage_manager \
  test.test_export_inbox_frontmatter -v
```

Expected: all tests pass.

- [ ] **Step 3: Run the full backend suite**

Run: `python -m unittest discover -s test -p 'test_*.py'`

Expected: all tests pass. If an existing unrelated test fails, record it
without changing unrelated code.

- [ ] **Step 4: Verify CLI help without network access**

Run: `python scripts/export_xhs_to_md.py --help`

Expected: exit code `0`, all three source inputs and common options are shown.

- [ ] **Step 5: Perform manual smoke checks**

With a user-provided valid account/session:

```bash
python scripts/export_xhs_to_md.py \
  --cookie "$XHS_COOKIE" \
  --note-url "<accessible-note-url>" \
  --limit 1

python scripts/export_xhs_to_md.py \
  --login \
  --note-url "<accessible-note-url>" \
  --limit 1
```

Expected for each: one Markdown file, valid local image links, original note
text, comments when available, and video transcription when the note contains
video and ASR is configured. If Xiaohongshu presents CAPTCHA/risk control, the
command exits explicitly and instructs the user to refresh the session or
complete verification.

- [ ] **Step 6: Inspect repository diff**

Run:

```bash
git --no-pager status --short
git --no-pager diff --check
```

Expected: only intended XHS files plus pre-existing user changes; no whitespace
errors, session JSON, downloaded media, or credentials are tracked.

- [ ] **Step 7: Commit any validation-only fixes**

Only when required:

```bash
git add path/to/exact/xhs_file.py path/to/exact/xhs_test.py
git commit -m "fix: finalize Xiaohongshu ingestion"
```

Replace the example paths with the exact changed XHS paths. Exclude every file
that was already dirty in the recorded baseline.
