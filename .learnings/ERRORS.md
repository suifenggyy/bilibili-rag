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
