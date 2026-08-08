# Xiaohongshu Search Empty Results Repair Design

## Problem

Keyword export currently initializes `search_id` to an empty string. The
Xiaohongshu v1 search endpoint accepts that request but returns only
`{"has_more": false}`, so the exporter treats an invalid search response as a
successful search with zero notes.

A live request with the same authenticated session returned 22 items after
supplying a valid Xiaohongshu base36 search ID. The newer browser endpoint is
not a suitable direct replacement because it requires an additional dynamic
`x-rap-param`; direct signed HTTP requests without that value return HTTP 406.

## Goals

- Generate a valid search ID before the first keyword-search request.
- Reuse the same search ID for every page of one keyword search.
- Distinguish a valid empty result (`items: []`) from a malformed response that
  omits `items`.
- Preserve existing authentication, detail fetching, pagination, and export
  behavior.

## Non-Goals

- Migrate search to `so.xiaohongshu.com/api/sns/web/v2/search/notes`.
- Automate the search page UI or intercept browser search responses.
- Change creator, explicit-note, detail, comment, media, or ASR flows.

## Design

### Search Session ID

`app/services/xhs.py` will create one Xiaohongshu-compatible search ID when
`iter_search_references()` starts. The ID uses the established format: the
current millisecond timestamp shifted left by 64 bits, combined with a random
integer, then encoded in uppercase base36.

The iterator passes that non-empty value to the first request and reuses it for
all subsequent pages. If the API returns a replacement `search_id`, the
iterator continues to accept and use it, preserving current pagination
compatibility.

### Response Validation

Keyword discovery will require the response to contain an `items` field whose
value is a list. A present empty list is a legitimate zero-result search. A
missing or non-list `items` field raises `XhsDataFetchError` with an actionable
message instead of producing a successful zero-count export.

This validation is search-specific. The generic response helpers used by
creator and detail flows remain unchanged.

### Error Propagation

The existing exporter already treats discovery exceptions as task-level
failures, prints `Xiaohongshu export failed: ...`, and exits nonzero. No new
fallback or broad exception handling is added.

## Testing

Update the existing `unittest` coverage to verify:

- the first search request receives a non-empty search ID;
- every page in one search reuses the same search ID unless the API supplies a
  replacement;
- a response containing `items: []` and `has_more: false` completes normally;
- a response that omits `items` raises `XhsDataFetchError`;
- existing lazy pagination and date-cutoff behavior remains unchanged.

## Acceptance Criteria

- `uv run scripts/export_xhs_to_md.py --keyword 租房` discovers notes with a
  valid authenticated session instead of reporting a false successful zero.
- A malformed search response cannot produce exit status 0 with all counters
  at zero.
- Legitimate empty keyword searches remain successful zero-result exports.
- Existing focused Xiaohongshu unit tests pass.
