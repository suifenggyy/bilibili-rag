# Xiaohongshu Content Ingestion Design

## Goal

Add Xiaohongshu content ingestion that follows the repository's existing
platform structure and writes knowledge-pipeline-ready Markdown into the
configured Obsidian inbox.

The first version supports:

- One or more explicit note URLs.
- All available notes from a creator profile.
- Notes returned by keyword search.
- Cookie authentication and interactive QR-code authentication.
- Note text, tags, selected engagement metadata, popular comments, and image
  downloads.
- Video download and ASR transcription for video notes.

FastAPI endpoints and frontend UI are outside this version's scope.

## Constraints

- Do not add a runtime dependency on the LittleCrawler project.
- Follow the existing service, fetcher, storage, ASR, summary, and standalone
  export-script patterns in this repository.
- Use the existing unified LLM and text-postprocessing factories.
- Preserve useful note content when optional comment, image, video, or ASR
  steps fail.
- Surface authentication, signing, CAPTCHA, and risk-control failures
  explicitly rather than returning empty success results.

## Architecture

### `app/services/xhs_auth.py`

Owns the Playwright browser lifecycle and authenticated session.

Responsibilities:

- Start a Chromium context on the Xiaohongshu web origin.
- In cookie mode, parse the configured cookie string and inject all valid
  Xiaohongshu cookies before loading the site.
- In QR mode, display or open the login page, wait for the user to complete
  login, and verify that an authenticated session exists.
- Persist reusable cookies to `.xhs_session.json` after successful QR login.
- Load a saved session when neither an explicit cookie nor a forced login is
  requested.
- Expose the authenticated `Page`, cookie dictionary, user agent, and cookie
  header needed by the API client.
- Close Playwright resources deterministically.

The session file is runtime data and must be added to `.gitignore`.

### `app/services/xhs_client.py`

Owns signed HTTP communication with Xiaohongshu.

Responsibilities:

- Generate request signatures by invoking the signing function available in
  the loaded Xiaohongshu page.
- Serialize signing calls with an async lock because one Playwright page is
  shared by concurrent note tasks.
- Send signed API requests with `httpx`.
- Detect HTTP and API-level authentication, CAPTCHA, risk-control, IP block,
  malformed-response, and note-unavailable errors.
- Apply bounded retries with backoff only to retryable transport and transient
  server failures.
- Provide focused methods for keyword search, note detail, creator notes,
  creator profile HTML, comment pages, note HTML fallback, and streamed media
  downloads.

The client must not contain export formatting, ASR orchestration, or
knowledge-pipeline logic.

### `app/services/xhs.py`

Owns Xiaohongshu domain operations and response normalization.

It provides three entry flows:

1. Parse explicit note URLs and fetch each note detail.
2. Parse a creator URL or creator ID, paginate creator notes, and fetch their
   details.
3. Paginate keyword search results and fetch their details.

Search and creator list responses provide note IDs and security tokens. The
service carries those tokens into detail and comment requests. If the detail
API returns no usable note, the service tries the note-page initial-state
fallback.

The service maps raw responses into a stable `XhsNoteInfo` dataclass with:

- `note_id`
- `title`
- `description`
- `author_name`
- `author_id`
- `published_at`
- `note_type`
- `tags`
- `liked_count`
- `collected_count`
- `comment_count`
- `image_urls`
- `video_urls`
- `source_url`
- `xsec_token`
- `xsec_source`

Pagination stops when the remote API reports no more results, the configured
limit is reached, or the available publication date is older than
`after_date`. Missing or unparseable dates do not cause notes to be dropped.

### `app/services/xhs_fetcher.py`

Owns media acquisition and content enrichment for one normalized note.

The returned `XhsNoteContent` dataclass contains the normalized note metadata,
downloaded image paths, original description, formatted tags, video
transcription, content source, summary block, raw ASR text, and formatted
comments section.

Processing order:

1. Reuse existing workspace files for the note when present.
2. Download images into the note workspace using deterministic names.
3. For video notes, reuse or download the video, extract 16 kHz mono WAV with
   ffmpeg, and invoke the configured ASR backend.
4. Run ASR text through the existing text postprocessor.
5. Fetch and format the configured number of popular comments.
6. Summarize the combined original description, transcription, and comments
   with `ContentSummaryService`.

Images are always downloaded when available. The first version does not run
OCR. If ASR is unavailable or fails, the note still exports with original
text, tags, images, and comments and records a basic-content source.

Workspace files use source name `xhs` through `ContentStorageManager`.

### `scripts/export_xhs_to_md.py`

Owns command-line parsing and batch orchestration.

Supported inputs:

- `--note-url URL`, repeatable.
- `--creator URL_OR_ID`.
- `--keyword KEYWORD`.

At least one input is required. Different input types may be supplied in one
run; duplicate note IDs are removed before processing.

Common options include:

- `--cookie`
- `--login`
- `--limit`
- `--after-date`
- `--concurrency`
- `--comment-limit`
- `--output-dir`
- Existing ASR backend options consistent with other export scripts.

The script builds authentication, client, service, fetcher, ASR, summary, and
storage dependencies; gathers candidate notes; processes them with bounded
concurrency; writes Markdown; reports per-note failures; and closes browser
and HTTP resources in a `finally` path.

## Authentication and Signing Flow

Authentication precedence is:

1. Explicit `--cookie`.
2. `XHS_COOKIE`.
3. Saved `.xhs_session.json`.
4. Interactive QR login when `--login` is supplied or no reusable session is
   available in an interactive terminal.

Non-interactive execution without a usable session fails with an actionable
message.

After loading the Xiaohongshu page, the auth service verifies both session
cookies and the presence of the page signing capability. The API client uses
the cookie value required by signing, browser local-storage values, request
path, query parameters or JSON body, and current timestamp to create the
required signed headers. Signature generation failure is a task-level error.

## Data Flow

1. The CLI resolves authentication and opens one authenticated browser page.
2. Each selected entry source yields note references.
3. References are deduplicated by note ID.
4. The domain service requests and normalizes note details.
5. The fetcher downloads media, transcribes video, fetches comments, and
   generates a summary.
6. The script renders Markdown and writes it with
   `ContentStorageManager.build_markdown_path`.
7. Output lands under `inbox/xhs/YYYY-MM-DD/` by default and can be consumed
   by the existing knowledge pipeline.

## Markdown Output

The document frontmatter uses the existing `build_export_frontmatter` helper:

- `title`: note title, falling back to a short description or note ID.
- `date`: publication date when available, otherwise the export date.
- `source`: canonical Xiaohongshu note URL.
- `summary`: plain text extracted from the generated summary block.
- `platform`: `xhs`.

The body contains:

1. Note title.
2. Note metadata table.
3. Original note text and tags.
4. Downloaded images referenced with relative Markdown paths.
5. Generated summary.
6. Video transcription when present.
7. Popular comments when present.
8. Export timestamp.

The Markdown file and its media directory use sanitized deterministic names
containing the note ID to avoid collisions.

## Configuration

Add settings and `.env.example` entries for:

- `XHS_COOKIE`
- `XHS_AFTER_DATE`
- `XHS_COMMENT_LIMIT`
- `XHS_REQUEST_INTERVAL`
- `XHS_SESSION_FILE` if a non-default session path is needed

Defaults must be conservative. Request concurrency and intervals remain CLI
overridable, but the signing page is always protected by the client lock.

The existing Playwright dependency is reused. Add no LittleCrawler package or
source-tree dependency.

## Error Handling

Task-level failures stop the run:

- No usable authentication in non-interactive mode.
- Login timeout or unsuccessful QR login.
- Missing page signing function.
- Repeated CAPTCHA or explicit risk-control response.
- Invalid CLI input with no supported source.

Item-level failures are logged with the note ID and do not stop unrelated
notes:

- Note deleted, private, or unavailable.
- Comment request failure.
- Individual image download failure.
- Video download, ffmpeg, ASR, postprocessing, or summary failure.
- Markdown write failure.

An unavailable detail API triggers the HTML fallback before the item is
declared failed. Optional enrichment failures never erase the original note
text already obtained.

Partial downloads use temporary filenames and are renamed only after a
successful size-checked download. Failed partial files are removed.

## Rate Limiting and Concurrency

- Candidate listing is sequential per source to preserve pagination state.
- Note enrichment uses a bounded semaphore.
- Signature generation is serialized.
- Requests include a configurable base interval with small random jitter.
- Retry count is finite and limited to retryable failures.
- CAPTCHA and authentication failures are not retried as ordinary transport
  failures.

## Tests

Tests use the repository's existing `unittest` conventions and mock all live
network, browser, ASR, and LLM calls.

Required coverage:

- Note URL and creator URL/ID parsing.
- Cookie parsing, precedence, saved-session loading, and QR-session saving.
- Search and creator pagination termination and limits.
- Deduplication across mixed input sources.
- Raw note-detail normalization for image and video notes.
- Detail API to HTML fallback.
- Signed-header generation calling the Playwright page under a lock.
- API error classification and bounded retry behavior.
- Comment pagination and configured limits.
- Image and video workspace cache reuse.
- Video-to-WAV and ASR success and fallback behavior.
- Markdown frontmatter, image references, transcription, tags, summary, and
  comments.
- Batch continuation after one item-level failure.

No automated test depends on a live Xiaohongshu account. A documented manual
smoke check uses one public note with `--limit 1`.

## Documentation

Update the repository documentation with:

- Cookie acquisition and QR-login instructions.
- Playwright browser installation prerequisite if the local browser is absent.
- Examples for note URL, creator, and keyword exports.
- Output structure and ASR behavior.
- Warnings that Xiaohongshu authentication and anti-bot controls may require
  session refresh or manual verification.

## Acceptance Criteria

- A user can authenticate with either a cookie or QR login.
- Explicit note URLs, creator pages, and keyword searches all produce
  deduplicated note candidates.
- Text/image notes export their original text, tags, downloaded images, and
  available popular comments.
- Video notes additionally attempt ASR using the configured existing backend.
- Optional enrichment failures preserve and export available core note data.
- Output Markdown uses `platform: xhs` and is written to the configured inbox
  in the same structural style as existing sources.
- Targeted unit tests pass without live external services.
