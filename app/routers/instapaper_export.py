"""
Instapaper 书签导出路由

将 Instapaper 收藏书签提取正文并导出为 Markdown 文件，
以后台异步任务方式运行，支持文件夹筛选。

端点：
    POST /instapaper-export/start          启动导出任务
    GET  /instapaper-export/status/{id}    查询任务进度
    GET  /instapaper-export/download/{id}  下载 ZIP 结果
"""

import asyncio
import io
import os
import re
import uuid
import zipfile
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import InstapaperSession
from app.services.content_storage import ContentStorageManager

router = APIRouter(prefix="/instapaper-export", tags=["Instapaper导出"])

instapaper_export_tasks: dict[str, dict] = {}

# 内置文件夹（Instapaper 固定）
BUILTIN_FOLDERS = [
    {"folder_id": "unread",  "title": "稍后阅读"},
    {"folder_id": "starred", "title": "星标收藏"},
    {"folder_id": "archive", "title": "已归档"},
]


# ==================== 请求 / 响应模型 ====================

class InstapaperExportRequest(BaseModel):
    consumer_key: str = Field(..., description="Instapaper API Consumer Key")
    consumer_secret: str = Field(..., description="Instapaper API Consumer Secret")
    email: str = Field(..., description="Instapaper 登录邮箱")
    password: str = Field(..., description="Instapaper 登录密码")
    folders: List[str] = Field(
        default=["starred"],
        description="要导出的文件夹 ID 列表（unread/starred/archive/自定义ID）",
    )
    limit: int = Field(default=0, ge=0, description="每个文件夹最多导出条数（0=全部）")


class InstapaperExportStatus(BaseModel):
    job_id: str
    status: str           # pending | running | completed | failed
    progress: int
    total_articles: int
    processed_articles: int
    current_article: str
    message: str
    file_count: int
    created_at: str
    completed_at: Optional[str] = None
    logs: list[str] = []

    model_config = {"extra": "ignore"}


class InstapaperCredentials(BaseModel):
    """保存/恢复 Instapaper 凭据"""
    consumer_key: str
    consumer_secret: str
    email: str
    password: str


# ==================== Session 存储端点 ====================

@router.post("/session/{session_id}", response_model=InstapaperCredentials, status_code=201)
async def save_instapaper_session(
    session_id: str,
    creds: InstapaperCredentials,
    db: AsyncSession = Depends(get_db),
):
    """保存 Instapaper 凭据到数据库（刷新页面后可恢复）。"""
    row = await db.scalar(
        select(InstapaperSession).where(InstapaperSession.session_id == session_id)
    )
    if row:
        row.consumer_key = creds.consumer_key
        row.consumer_secret = creds.consumer_secret
        row.email = creds.email
        row.password = creds.password
    else:
        row = InstapaperSession(
            session_id=session_id,
            consumer_key=creds.consumer_key,
            consumer_secret=creds.consumer_secret,
            email=creds.email,
            password=creds.password,
        )
        db.add(row)
    await db.commit()
    return creds


@router.get("/session/{session_id}", response_model=InstapaperCredentials)
async def get_instapaper_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """根据 session_id 恢复已保存的 Instapaper 凭据。"""
    row = await db.scalar(
        select(InstapaperSession).where(InstapaperSession.session_id == session_id)
    )
    if not row or not row.email:
        raise HTTPException(status_code=404, detail="凭据不存在")
    return InstapaperCredentials(
        consumer_key=row.consumer_key or "",
        consumer_secret=row.consumer_secret or "",
        email=row.email or "",
        password=row.password or "",
    )


@router.delete("/session/{session_id}", status_code=204)
async def delete_instapaper_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """删除保存的 Instapaper 凭据（登出）。"""
    row = await db.scalar(
        select(InstapaperSession).where(InstapaperSession.session_id == session_id)
    )
    if row:
        await db.delete(row)
        await db.commit()


# ==================== 工具函数 ====================

def _safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:max_len] if len(name) > max_len else name


# ==================== 后台导出任务 ====================

async def _run_instapaper_export(job_id: str, req: InstapaperExportRequest):
    task = instapaper_export_tasks[job_id]
    task.setdefault("output_files", [])

    from app.services.instapaper import InstapaperService
    from app.services.article_fetcher import ArticleFetcher

    svc = InstapaperService(req.consumer_key, req.consumer_secret)
    storage_manager = ContentStorageManager()
    fetcher = ArticleFetcher(storage_manager=storage_manager)

    def _log(msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        logger.info(f"[InstapaperExport] {msg}")
        task["logs"].append(entry)
        if len(task["logs"]) > 200:
            task["logs"] = task["logs"][-200:]

    try:
        task["status"] = "running"
        task["message"] = "正在登录 Instapaper..."
        _log("登录 Instapaper...")

        await svc.login(req.email, req.password)
        _log("登录成功")

        # 获取书签列表（所有选定文件夹）
        task["message"] = "正在获取书签列表..."
        _log("获取书签列表...")
        all_bookmarks: list[tuple[dict, str]] = []  # (bookmark, folder_title)

        # 获取自定义文件夹名称映射
        folder_title_map = {f["folder_id"]: f["title"] for f in BUILTIN_FOLDERS}
        try:
            custom_folders = await svc.get_folders()
            folder_title_map.update({f["folder_id"]: f["title"] for f in custom_folders})
        except Exception as e:
            logger.warning(f"[InstapaperExport] 获取自定义文件夹失败: {e}")

        for folder_id in req.folders:
            try:
                bookmarks = await svc.get_all_bookmarks(folder_id)
                if req.limit > 0:
                    bookmarks = bookmarks[:req.limit]
                folder_title = folder_title_map.get(folder_id, folder_id)
                for bm in bookmarks:
                    all_bookmarks.append((bm, folder_title))
                _log(f"文件夹「{folder_title}」: {len(bookmarks)} 篇")
                logger.info(
                    f"[InstapaperExport] 文件夹 '{folder_title}': {len(bookmarks)} 篇"
                )
            except Exception as e:
                logger.warning(f"[InstapaperExport] 获取文件夹 {folder_id} 失败: {e}")

        total = len(all_bookmarks)
        task["total_articles"] = total
        task["message"] = f"共 {total} 篇文章，开始提取正文..."
        _log(f"共 {total} 篇文章，开始提取正文...")

        if total == 0:
            task.update({
                "status": "completed",
                "progress": 100,
                "message": "没有可导出的书签",
                "completed_at": datetime.now().isoformat(),
            })
            return

        file_count = 0

        for idx, (bookmark, folder_title) in enumerate(all_bookmarks):
            bm_id = str(bookmark.get("bookmark_id", ""))
            title = bookmark.get("title") or bookmark.get("url", "未知标题")
            url = bookmark.get("url", "")

            task["current_article"] = title
            task["processed_articles"] = idx
            task["progress"] = int(idx / total * 95)

            md_path = storage_manager.build_markdown_path("instapaper", title, bm_id)

            if md_path.exists():
                file_count += 1
                task["output_files"].append(str(md_path))
                task["file_count"] = file_count
                _log(f"[{idx+1}/{total}] ⏭ 已存在，跳过：{title[:40]}")
                continue

            _log(f"[{idx+1}/{total}] 🌐 抓取正文：{title[:50]}")
            task["message"] = f"[{idx+1}/{total}] 抓取: {title[:30]}..."

            try:
                content = await fetcher.fetch_content(url, title)
                source_label = "✅ trafilatura" if content["source"] == "trafilatura" else "⚠️ 基本信息"
                md_text = ArticleFetcher.build_markdown(bookmark, content)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_text)
                file_count += 1
                task["output_files"].append(str(md_path))
                _log(f"[{idx+1}/{total}] ✅ 完成（{source_label}）：{title[:40]}")
                logger.info(
                    f"[InstapaperExport] [{idx+1}/{total}] ✅ "
                    f"{title[:40]} ({content['source']})"
                )
            except Exception as e:
                _log(f"[{idx+1}/{total}] ❌ 失败：{title[:40]} — {str(e)[:80]}")
                logger.error(f"[InstapaperExport] [{idx+1}/{total}] ❌ {bm_id}: {e}")

            task["file_count"] = file_count
            await asyncio.sleep(0.2)

        task.update({
            "status": "completed",
            "progress": 100,
            "processed_articles": total,
            "current_article": "",
            "file_count": file_count,
            "message": f"导出完成，共生成 {file_count} 个 Markdown 文件",
            "completed_at": datetime.now().isoformat(),
        })
        _log(f"🎉 任务完成，共生成 {file_count} 个 Markdown 文件")
        logger.info(f"[InstapaperExport] 任务完成: job_id={job_id}, files={file_count}")

    except Exception as e:
        logger.error(f"[InstapaperExport] 任务失败: {job_id}: {e}")
        _log(f"❌ 任务失败: {str(e)[:100]}")
        task.update({
            "status": "failed",
            "message": f"导出失败: {str(e)}",
            "completed_at": datetime.now().isoformat(),
        })
    finally:
        await svc.close()


# ==================== 路由 ====================

@router.post("/start")
async def start_instapaper_export(
    req: InstapaperExportRequest,
    background_tasks: BackgroundTasks,
):
    """
    启动 Instapaper 导出任务。

    验证凭据后立即返回 job_id，任务在后台异步执行。
    """
    if not req.consumer_key or not req.consumer_secret:
        raise HTTPException(status_code=400, detail="请提供 Instapaper API Consumer Key/Secret")
    if not req.email or not req.password:
        raise HTTPException(status_code=400, detail="请提供 Instapaper 登录邮箱和密码")
    if not req.folders:
        raise HTTPException(status_code=400, detail="请至少选择一个文件夹")

    # 提前验证登录凭据
    from app.services.instapaper import InstapaperService
    svc = InstapaperService(req.consumer_key, req.consumer_secret)
    try:
        await svc.login(req.email, req.password)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Instapaper 登录失败: {str(e)}")
    finally:
        await svc.close()

    job_id = str(uuid.uuid4())
    instapaper_export_tasks[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "total_articles": 0,
        "processed_articles": 0,
        "current_article": "",
        "message": "任务已创建，等待启动...",
        "file_count": 0,
        "output_files": [],
        "logs": [],
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
    }

    background_tasks.add_task(_run_instapaper_export, job_id, req)
    logger.info(
        f"[InstapaperExport] 任务已创建: job_id={job_id}, "
        f"folders={req.folders}, limit={req.limit}"
    )
    return {"job_id": job_id, "message": "Instapaper 导出任务已启动"}


@router.get("/status/{job_id}", response_model=InstapaperExportStatus)
async def get_instapaper_export_status(job_id: str):
    task = instapaper_export_tasks.get(job_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return InstapaperExportStatus(**task)


@router.get("/download/{job_id}")
async def download_instapaper_export(job_id: str):
    task = instapaper_export_tasks.get(job_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    output_files = [path for path in task.get("output_files", []) if os.path.exists(path)]
    if not output_files:
        raise HTTPException(status_code=404, detail="导出文件不存在")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        export_root = os.path.expanduser(settings.collection_output_dir)
        for abs_path in output_files:
            zf.write(abs_path, os.path.relpath(abs_path, export_root))
    zip_buffer.seek(0)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="instapaper_export_{ts}.zip"'},
    )


@router.get("/folders")
async def get_instapaper_folders(
    consumer_key: str,
    consumer_secret: str,
    email: str,
    password: str,
):
    """获取 Instapaper 文件夹列表（含自定义文件夹）"""
    from app.services.instapaper import InstapaperService
    svc = InstapaperService(consumer_key, consumer_secret)
    try:
        await svc.login(email, password)
        custom = await svc.get_folders()
        return {"folders": BUILTIN_FOLDERS + custom}
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))
    finally:
        await svc.close()
