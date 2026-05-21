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

---
