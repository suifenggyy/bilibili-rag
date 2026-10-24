## [ERR-20260516-001] frontend-build

**Logged**: 2026-05-16T21:09:05+08:00
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
前端构建时 `next` 命令不存在，说明前端依赖未安装到当前工作区

### Error
```text
> frontend@0.1.0 build
> next build

sh: next: command not found
```

### Context
- Command/operation attempted: `cd frontend && npm run build`
- Input or parameters used: project default frontend build command
- Environment details if relevant: repository root `/Users/gongyongyue/project/bilibili-rag`

### Suggested Fix
先安装前端依赖，再重新执行 `npm run build`。

### Metadata
- Reproducible: yes
- Related Files: frontend/package.json

### Resolution
- **Resolved**: 2026-05-16T21:19:24+08:00
- **Commit/PR**: uncommitted
- **Notes**: 安装了 frontend 依赖后重新执行 `npm run build`，构建通过。

---

## [ERR-20260809-001] backend-tests

**Logged**: 2026-08-09T01:12:00+08:00
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
系统 PATH 中没有 `python` 命令，项目测试应使用 `.venv/bin/python`。

### Error
```text
/bin/bash: python: command not found
```

### Context
- Command/operation attempted: `python -m unittest test.test_xhs_auth -v`
- Environment details: macOS repository root with a local `.venv`

### Suggested Fix
优先使用项目虚拟环境中的 `.venv/bin/python` 执行后端测试。

### Metadata
- Reproducible: yes
- Related Files: test/test_xhs_auth.py

### Resolution
- **Resolved**: 2026-08-09T01:12:00+08:00
- **Commit/PR**: uncommitted
- **Notes**: Located `.venv/bin/python` and reran the focused test with it.

---

## [ERR-20260809-002] xhs-keyword-search

**Logged**: 2026-08-09T01:44:21+08:00
**Priority**: medium
**Status**: resolved
**Area**: backend

### Summary
Xiaohongshu keyword search intermittently returned a successful response without
an `items` list.

### Error
```text
Xiaohongshu search response omitted the items list.
```

### Context
- Command/operation attempted: `uv run scripts/export_xhs_to_md.py --keyword 租房`
- The same authenticated session later returned a normal `data.items` list.

### Suggested Fix
Retry one successful-but-incomplete keyword-search response before reporting it
as a malformed response.

### Metadata
- Reproducible: unknown
- Related Files: app/services/xhs.py, test/test_xhs_service.py

### Resolution
- **Resolved**: 2026-08-09T01:51:00+08:00
- **Commit/PR**: uncommitted
- **Notes**: Matched LittleCrawler's pagination semantics: a successful final
  response may omit `items` when `has_more` is false. The live `租房` discovery
  then completed with 988 notes. An incomplete response that declares another
  page still retries once and then fails explicitly.

---
