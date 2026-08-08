# Xiaohongshu Signing Readiness Design

## Problem

The Xiaohongshu exporter can stop immediately after interactive QR login with:

> Xiaohongshu signing state is incomplete (missing a1 cookie or b1 localStorage)

`XhsAuthService._wait_for_qr_login()` currently returns as soon as the `web_session`
and `a1` cookies exist. `open()` then checks `window.mnsv2` and localStorage `b1`
once. The cookies may become available before the page finishes initializing its
browser signing state, creating a race that closes the interactive browser before
the session is usable.

## Goals

- Wait automatically for the complete browser signing state.
- Apply the same readiness definition to QR, explicit-cookie, environment-cookie,
  and saved-session modes.
- Preserve the existing login timeout and browser visibility behavior.
- Report the exact missing readiness fields when the timeout expires.
- Persist QR-login cookies only after the session is fully ready.

## Non-Goals

- Reimplement Xiaohongshu's signing algorithm outside the browser.
- Bypass verification or risk-control pages.
- Change cookie precedence, export behavior, or request signing.
- Persist localStorage values in the session file.

## Design

### Readiness Model

Authentication is ready only when all of these values are available:

1. `web_session` cookie
2. `a1` cookie
3. `window.mnsv2` signing function
4. localStorage `b1`

`XhsAuthService` will collect these checks into one internal readiness snapshot so
polling, timeout diagnostics, and the returned session use identical state. A
successful snapshot includes the cookie dictionary and raw cookie records needed
to create and persist the session; `open()` will not reread cookies after success.

### Polling Flow

After navigating to the Xiaohongshu origin, `open()` will poll readiness at the
existing 250 ms interval until all four requirements are present or a monotonic
deadline based on `login_timeout` expires. The final check occurs at the deadline
before reporting a timeout.

In QR mode, the browser remains headed and the existing waiting log is retained.
Cookie, environment, and saved-session modes use the same poll, may wait for the
full `login_timeout`, and preserve their configured headless behavior. This
accommodates asynchronous page initialization without relying on a fixed delay or
an extra reload.

The existing one-shot authenticated-state, signing-function, and signing-state
checks will be replaced by the unified wait. Once ready, `open()` uses the
successful snapshot to persist QR cookies and return `XhsBrowserSession`.

### Error Handling

On timeout, `XhsAuthenticationError` will identify only the fields missing from the
final snapshot. Fields appear in this deterministic order:
`web_session cookie`, `a1 cookie`, `window.mnsv2`, `b1 localStorage`. The message
format is:

> Xiaohongshu browser session was not ready before timeout; missing: FIELD[, FIELD...]. Refresh the session or log in again.

The message will continue to advise refreshing the session or logging in again.
Unexpected Playwright errors keep the existing wrapped initialization error.
Cleanup remains unchanged, so browser resources close after a failed timeout.

### Session Persistence

QR cookies are written only after complete readiness succeeds. The session file
format and `0600` permissions remain unchanged. `b1` is not persisted because it
belongs to the active browser origin's localStorage and is initialized by the
site.

## Testing

Extend `test/test_xhs_auth.py` with fakes that can expose cookies, `window.mnsv2`,
and `b1` on different polling iterations.

Coverage will verify:

- QR login succeeds when cookies appear before `b1`.
- Readiness succeeds when `window.mnsv2` is delayed.
- At least one cookie, environment, or saved-session case succeeds after delayed
  signing-state initialization, proving non-QR modes use the same poll.
- A successful poll returns the cookie snapshot used by `open()` without an
  additional cookie read, verified by read count or post-success mutation.
- A fully missing timeout reports all four fields in the defined order.
- Partial-readiness timeouts report only the fields missing from the final
  snapshot, in the defined order.
- QR cookies are not persisted before full readiness.
- Explicit-cookie, environment-cookie, and saved-session precedence is unchanged.
- Browser cleanup and context-manager behavior remain unchanged.

## Acceptance Criteria

- A QR login does not fail merely because `b1` or `window.mnsv2` initializes after
  the authentication cookies.
- The browser remains open and polling until readiness or `login_timeout`.
- Timeout output states exactly which readiness requirements were missing.
- Existing XHS auth tests and the new delayed-readiness tests pass.
