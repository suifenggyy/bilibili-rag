## [LRN-20260516-001] correction

**Logged**: 2026-05-16T21:05:02+08:00
**Priority**: high
**Status**: pending
**Area**: config

### Summary
Python 依赖安装应使用 `uv pip install ...`，不要直接用 `pip install`

### Details
在本次任务里安装 Python 依赖时误用了 `pip install`。用户明确要求后续统一使用 `uv pip install ...`，并希望这条习惯被长期记住，避免后续再次使用错误的安装方式。

### Suggested Action
后续凡是需要安装 Python 依赖，一律优先使用 `uv pip install ...`；如果是按 requirements 安装，则使用 `uv pip install -r requirements.txt`。

### Metadata
- Source: user_feedback
- Related Files: requirements.txt
- Tags: uv, pip, dependency-install, workflow
- Recurrence-Count: 2
- Last-Seen: 2026-08-09

---

## [LRN-20260809-001] correction

**Logged**: 2026-08-09T00:55:00+08:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
Do not verify Xiaohongshu QR login with a cross-origin browser `fetch`.

### Details
The first implementation called the `edith.xiaohongshu.com` self-info endpoint
from a `www.xiaohongshu.com` page. Browser CORS prevented the check from
succeeding, so Enter and automatic completion never closed the login window.
The headed launch also exposed duplicate visible QR windows.

### Suggested Action
Use same-origin observable state such as the QR flow's `web_session` transition
and page-local initial user state. Launch QR mode with one persistent browser
context and reuse its initial page.

### Metadata
- Source: user_feedback
- Related Files: app/services/xhs_auth.py, test/test_xhs_auth.py
- Tags: xhs, playwright, cors, qr-login

### Resolution
- **Resolved**: 2026-08-09T01:02:00+08:00
- **Commit/PR**: uncommitted
- **Notes**: Reused one persistent-context page, removed cross-origin identity fetch, and waited for secondary security verification to disappear.

---
