"""
Bilibili RAG 知识库系统

导出路由 - 将收藏夹视频转写内容导出为 Markdown 文件
"""
import asyncio
import io
import os
import re
import time
import uuid
import zipfile
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel

from app.routers.auth import get_session
from app.services.bilibili import BilibiliService
from app.services.content_fetcher import ContentFetcher

router = APIRouter(prefix="/export", tags=["导出"])

# 导出任务状态（内存存储，重启后清空）
export_tasks: dict[str, dict] = {}

# 导出文件存储目录
EXPORT_DIR = os.path.join("data", "exports")


# ==================== 请求 / 响应模型 ====================

class ExportRequest(BaseModel):
    """导出请求"""
    folder_ids: List[int]
    asr_backend: str = "auto"          # auto | dashscope | ollama
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "whisper"
    ollama_language: str = "zh"


class ExportStatus(BaseModel):
    """导出任务状态"""
    job_id: str
    status: str                        # pending | running | completed | failed
    progress: int                      # 0-100
    total_videos: int
    processed_videos: int
    current_video: str
    message: str
    created_at: str
    completed_at: Optional[str] = None
    file_count: int = 0


# ==================== 工具函数 ====================

def _safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:max_len] if len(name) > max_len else name


def _format_duration(seconds: int) -> str:
    if not seconds:
        return "未知"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _build_markdown(video: dict, content: str, source: str, folder_title: str) -> str:
    title = video.get("title") or "未知标题"
    bvid = video.get("bvid") or ""
    owner = (video.get("upper") or {}).get("name") or video.get("owner_name") or "未知UP主"
    duration = _format_duration(video.get("duration"))
    cover = video.get("cover") or ""
    intro = video.get("intro") or video.get("desc") or ""
    pub_time = video.get("pubtime") or video.get("ctime") or 0
    pub_str = datetime.fromtimestamp(pub_time).strftime("%Y-%m-%d") if pub_time else "未知"

    source_label = {"asr": "ASR 语音转写", "basic_info": "视频基本信息"}.get(source, source)

    lines = [
        f"# {title}", "",
        "## 视频信息", "",
        "| 字段 | 内容 |",
        "|------|------|",
        f"| BV号 | [{bvid}](https://www.bilibili.com/video/{bvid}) |",
        f"| UP主 | {owner} |",
        f"| 时长 | {duration} |",
        f"| 发布日期 | {pub_str} |",
        f"| 来源收藏夹 | {folder_title} |",
        f"| 内容来源 | {source_label} |",
    ]
    if cover:
        lines += ["", f"![封面]({cover})"]
    if intro:
        lines += ["", "## 视频简介", "", intro]
    lines += ["", "---", "", "## 转写内容", ""]
    lines.append(content.strip() if content and content.strip() else "_（未获取到有效内容）_")
    lines += ["", "---", f"", f"_导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"]
    return "\n".join(lines)


def _build_asr_service(req: ExportRequest):
    """根据请求参数构建 ASR 服务实例"""
    from app.config import settings

    backend = req.asr_backend
    if backend == "auto":
        backend = "dashscope" if settings.openai_api_key else "ollama"

    if backend == "dashscope":
        from app.services.asr import ASRService
        return ASRService()

    from app.services.asr_local import OllamaASRService
    return OllamaASRService(
        base_url=req.ollama_url,
        model=req.ollama_model,
        language=req.ollama_language,
    )


# ==================== 后台导出任务 ====================

async def _run_export(job_id: str, req: ExportRequest, cookies: dict):
    """后台执行导出任务"""
    task = export_tasks[job_id]
    job_dir = os.path.join(EXPORT_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    bili = BilibiliService(
        sessdata=cookies.get("SESSDATA"),
        bili_jct=cookies.get("bili_jct"),
        dedeuserid=cookies.get("DedeUserID"),
    )
    asr = _build_asr_service(req)
    fetcher = ContentFetcher(bili, asr)

    try:
        task["status"] = "running"
        task["message"] = "正在获取收藏夹视频列表..."

        all_videos: list[tuple[dict, dict]] = []  # (video, folder_info)

        for folder_id in req.folder_ids:
            try:
                # 获取收藏夹基本信息（用于文件夹命名）
                page1 = await bili.get_favorite_content(folder_id, pn=1, ps=1)
                folder_title = (page1.get("info") or {}).get("title", f"收藏夹_{folder_id}")
                videos = await bili.get_all_favorite_videos(folder_id)
                for v in videos:
                    attr = v.get("attr", 0)
                    t = v.get("title", "")
                    if attr == 9 or t in ("已失效视频", "已删除视频"):
                        continue
                    if not (v.get("bvid") or v.get("bv_id")):
                        continue
                    all_videos.append((v, {"title": folder_title, "id": folder_id}))
            except Exception as e:
                logger.warning(f"[Export] 获取收藏夹 {folder_id} 失败: {e}")

        total = len(all_videos)
        task["total_videos"] = total
        task["message"] = f"共 {total} 个视频，开始转写..."

        if total == 0:
            task["status"] = "completed"
            task["progress"] = 100
            task["message"] = "没有可导出的视频"
            task["completed_at"] = datetime.now().isoformat()
            return

        file_count = 0

        for idx, (video, folder_info) in enumerate(all_videos):
            bvid = video.get("bvid") or video.get("bv_id") or ""
            title = video.get("title") or bvid
            cid = (video.get("ugc") or {}).get("first_cid") or video.get("cid") or None
            folder_title = folder_info.get("title", "收藏夹")

            task["current_video"] = title
            task["processed_videos"] = idx
            task["progress"] = int(idx / total * 95)

            # 目标文件路径
            folder_dir = os.path.join(job_dir, _safe_filename(folder_title))
            os.makedirs(folder_dir, exist_ok=True)
            safe_title = _safe_filename(title)
            md_path = os.path.join(folder_dir, f"{safe_title}_{bvid}.md")

            if os.path.exists(md_path):
                file_count += 1
                continue

            try:
                video_content = await fetcher.fetch_content(bvid, cid=cid, title=title)
                source = (
                    video_content.source.value
                    if hasattr(video_content.source, "value")
                    else str(video_content.source)
                )
                md_content = _build_markdown(video, video_content.content, source, folder_title)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                file_count += 1
                logger.info(f"[Export] [{idx+1}/{total}] ✅ {title[:40]} ({source})")
            except Exception as e:
                logger.error(f"[Export] [{idx+1}/{total}] ❌ {bvid}: {e}")

            task["file_count"] = file_count
            await asyncio.sleep(0.3)

        task["status"] = "completed"
        task["progress"] = 100
        task["processed_videos"] = total
        task["current_video"] = ""
        task["file_count"] = file_count
        task["message"] = f"导出完成，共生成 {file_count} 个 Markdown 文件"
        task["completed_at"] = datetime.now().isoformat()
        logger.info(f"[Export] 任务完成: job_id={job_id}, files={file_count}")

    except Exception as e:
        logger.error(f"[Export] 任务失败: job_id={job_id}, error={e}")
        task["status"] = "failed"
        task["message"] = f"导出失败: {str(e)}"
        task["completed_at"] = datetime.now().isoformat()
    finally:
        await bili.close()


# ==================== 路由 ====================

@router.post("/start")
async def start_export(
    req: ExportRequest,
    background_tasks: BackgroundTasks,
    session_id: str = Query(..., description="会话ID"),
):
    """
    启动导出任务（后台异步执行）

    返回 job_id，通过 GET /export/status/{job_id} 轮询进度。
    """
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")

    if not req.folder_ids:
        raise HTTPException(status_code=400, detail="请至少选择一个收藏夹")

    # 检查 Ollama 可用性（仅在使用 ollama 时）
    backend = req.asr_backend
    if backend == "auto":
        from app.config import settings
        backend = "dashscope" if settings.openai_api_key else "ollama"
    if backend == "ollama":
        from app.services.asr_local import OllamaASRService
        asr_check = OllamaASRService(base_url=req.ollama_url, model=req.ollama_model)
        if not asr_check.check_ollama_available():
            raise HTTPException(
                status_code=503,
                detail=f"无法连接到 Ollama 服务（{req.ollama_url}），请确认 Ollama 已启动"
            )
        if not asr_check.check_model_available():
            raise HTTPException(
                status_code=503,
                detail=f"Ollama 中未找到模型 '{req.ollama_model}'，请先运行：ollama pull {req.ollama_model}"
            )

    job_id = str(uuid.uuid4())
    cookies = session.get("cookies", {})

    export_tasks[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "progress": 0,
        "total_videos": 0,
        "processed_videos": 0,
        "current_video": "",
        "message": "任务已创建，等待启动...",
        "file_count": 0,
        "created_at": datetime.now().isoformat(),
        "completed_at": None,
    }

    background_tasks.add_task(_run_export, job_id, req, cookies)
    logger.info(f"[Export] 任务已创建: job_id={job_id}, folders={req.folder_ids}, backend={req.asr_backend}")

    return {"job_id": job_id, "message": "导出任务已启动"}


@router.get("/status/{job_id}", response_model=ExportStatus)
async def get_export_status(job_id: str):
    """轮询导出任务进度"""
    task = export_tasks.get(job_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return ExportStatus(**task)


@router.get("/download/{job_id}")
async def download_export(job_id: str):
    """
    下载导出结果（ZIP 压缩包）

    任务完成后才可下载。
    """
    task = export_tasks.get(job_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    job_dir = os.path.join(EXPORT_DIR, job_id)
    if not os.path.exists(job_dir):
        raise HTTPException(status_code=404, detail="导出文件不存在")

    # 打包为 ZIP 并流式返回
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(job_dir):
            for fname in files:
                if fname.endswith(".md"):
                    abs_path = os.path.join(root, fname)
                    arc_name = os.path.relpath(abs_path, job_dir)
                    zf.write(abs_path, arc_name)
    zip_buffer.seek(0)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"bilibili_export_{ts}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/jobs")
async def list_export_jobs(session_id: str = Query(...)):
    """列出当前会话的所有导出任务（按创建时间倒序）"""
    session = await get_session(session_id)
    if not session:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")

    jobs = sorted(export_tasks.values(), key=lambda x: x["created_at"], reverse=True)
    return {"jobs": jobs[:20]}  # 最近 20 条
