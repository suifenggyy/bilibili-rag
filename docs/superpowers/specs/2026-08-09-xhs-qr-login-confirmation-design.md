# Xiaohongshu QR Login Confirmation Design

## Problem

The current Xiaohongshu QR flow waits for cookies and browser signing state
before recording the unauthenticated `web_session`. If the user scans quickly,
the first observed value can already be the authenticated session, so the code
treats it as the baseline and waits until timeout. The additional
`window.__INITIAL_STATE__` user lookup is also coupled to unstable page data.

LittleCrawler avoids this race by locating the QR login UI first, recording the
current `web_session` immediately, and then waiting for that value to change.

## Goals

- Follow LittleCrawler's QR login sequence while fitting the existing
  `XhsAuthService` lifecycle.
- Keep the headed browser open until the QR login changes `web_session`.
- Preserve the existing 120-second timeout and session-file permissions.
- Validate signing readiness after login before returning the browser session.
- Preserve explicit-cookie, environment-cookie, and saved-session behavior.

## Non-Goals

- Display a second QR image outside the already visible browser.
- Bypass CAPTCHA, account security verification, or risk controls.
- Change XHS request signing, content discovery, or export behavior.

## Design

### QR Preparation

After navigation, QR mode will wait for the Xiaohongshu QR image selector. If it
is not initially present, the service will click the page login button and wait
again. Failure to expose the QR code within a bounded preparation timeout will
raise an actionable authentication error.

As soon as the QR image is present, the service will read the browser cookies
and store the current `web_session` as the unauthenticated baseline. This happens
before polling signing readiness, preventing a fast scan from becoming the
baseline.

### Login Confirmation

The service will poll cookies until a non-empty `web_session` differs from the
baseline. This is the same confirmation signal used by LittleCrawler. Page
`__INITIAL_STATE__` user IDs and visual account markers will no longer determine
success.

If the page shows Xiaohongshu's security-verification prompt, the browser remains
open and the user can complete it. Pressing Enter may request an immediate poll,
but cannot force successful authentication.

### Signing Readiness and Persistence

After `web_session` changes, the service will continue polling until `a1`,
`web_session`, `window.mnsv2`, and localStorage `b1` are available. Only then
will it persist cookies and return `XhsBrowserSession`.

Non-QR modes retain the existing readiness check without requiring a session
change.

### Errors and Cleanup

The flow distinguishes:

- QR UI not found;
- QR scan or secondary verification not completed before timeout;
- login confirmed but signing state remained incomplete.

All failures use the existing `XhsAuthenticationError` path and close Playwright
resources. QR cookies are never persisted before both login confirmation and
signing readiness succeed.

## Testing

Update the `unittest.IsolatedAsyncioTestCase` fakes to model QR visibility,
login-button fallback, baseline cookies, session changes, and delayed signing
state. Cover:

- baseline capture occurs before readiness polling;
- a fast scan is detected rather than timing out;
- unchanged guest `web_session` does not authenticate;
- login-button fallback exposes the QR code;
- missing QR UI produces an actionable error;
- delayed signing readiness after session change succeeds;
- timeout and security-verification paths do not persist cookies;
- explicit-cookie, environment-cookie, and saved-session precedence is
  unchanged.

## Acceptance Criteria

- QR login succeeds when `web_session` changes from the value captured when the
  QR code first appears.
- A fast scan cannot cause the authenticated session to become the baseline.
- The browser remains open during QR scanning and secondary verification.
- Session cookies are saved only after signing readiness is complete.
- Existing non-QR authentication behavior remains compatible.
