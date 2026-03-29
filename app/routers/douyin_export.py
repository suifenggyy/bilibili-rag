"""
抖音收藏夹导出路由

提供 Web API 接口，在后台异步执行抖音收藏夹 → Markdown 的导出任务。
与 B站导出路由不同，无需 B站会话，Cookie 直接由请求体传入。

端点：
    POST /douyin-export/start          启动导出任务
    GET  /douyin-export/status/{id}    查询任务进度
    GET  /douyin-export/download/{id}  下载 ZIP 结果
"""

import asyncio
import io
import os
import re
import time
import uuid
import zipfile
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

router = APIRouter(prefix="/douyin-export", tags=["抖音导出"])

# 任务状态（内存存储，重启后清空）
douyin_export_tasks: dict[str, dict] = {}

DOUYIN_EXPORT_DIR = os.path.join("data", "douyin_exports")


# ==================== 请求 / 响应模型 ====================

class DouyinExportRequest(BaseModel):
    """抖音导出请求"""
    cookie: str = Field(..., description="抖音浏览器 Cookie 字符串")
    evil0ctal_url: str = Field(
        default="http://localhost:2333",
        description="Evil0ctal API 服务地址",
    )
    limit: int = Field(
        default=0,
        ge=0,
        description="最多导出视频数（0=全部）",
    )
    asr_backend: str = Field(
        default="auto",
        description="ASR 后端：auto | dashscope | ollama",
    )
    ollama_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="whisper")
    ollama_language: str = Field(default="zh")


class DouyinExportStatus(BaseModel):
    """导出任务状态"""
    job_id: str
    status: str           # pending | running | completed | failed
    progress: int         # 0-100
    total_videos: int
    processed_videos: int
    current_video: str
    message: str
    file_count: int
    created_at: str
    completed_at: Optional[str] = None


# ==================== 工具函数 ====================

def _safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:max_len] if len(name) > max_len else name


def _format_duration(ms: int) -> str:
    if not ms:
        return "未知"
    s = ms // 1000
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _build_markdown(vc, source: str) -> str:
    """从 DouyinVideoContent 构建 Markdown 文本"""
    create_str = (
        datetime.fromtimestamp(vc.create_time).strftime("%Y-%m-%d")
        if vc.create_time else "未知"
    )
    duration_str = _format_duration(vc.duration)
    source_label = {
        "asr": "ASR 语音转写",
        "basic_info": "视频基本信息（ASR 未成功）",
    }.get(source, source)

    lines = [
        f"# {vc.title}", "",
        "## 视频信息", "",
        "| 字段 | 内容 |",
        "|------|------|",
        f"| 视频ID | [{vc.aweme_id}]({vc.share_url}) |",
        f"| 作者 | {vc.author} |",
        f"| 时长 | {duration_str} |",
        f"| 发布日期 | {create_str} |",
        f"| 内容来源 | {source_label} |",
    ]
    if vc.cover_url:
        lines += ["", f"![封面]({vc.cover_url})"]
    lines += ["", "---", "", "## 转写内容", ""]
    if vc.content and vc.content.strip():
        lines.append(vc.content.strip())
    else:
        lines.append("_（未获取到有效内容）_")
    lines += ["", "---", f"", f"_导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"]
    return "\n".join(lines)


def _build_asr_service(req: DouyinExportRequest):
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

async def _run_douyin_export(job_id: str, req: DouyinExportRequest):
    """后台执行抖音导出任务"""
    task = douyin_export_tasks[job_id]
    job_dir = os.path.join(DOUYIN_EXPORT_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)

    from app.services.douyin import DouyinService
    from app.services.douyin_fetcher import DouyinContentFetcher

    douyin = DouyinService(cookie=req.cookie, evil0ctal_url=req.evil0ctal_url)
    asr = _build_asr_service(req)
    fetcher = DouyinContentFetcher(
        asr_service=asr,
        tmp_dir=os.path.join("data", "douyin_tmp"),
    )

    try:
        task["status"] = "running"
        task["message"] = "正在获取收藏夹视频列表..."

        # 获取所有收藏视频
        all_videos = await douyin.get_all_collection_videos()
        if req.limit > 0:
            all_videos = all_videos[:req.limit]

        total = len(all_videos)
        task["total_videos"] = total
        task["message"] = f"共 {total} 个视频，开始转写..."

        if total == 0:
            task.update({
                "status": "completed",
                "progress": 100,
                "message": "收藏夹为空，无视频可导出",
                "completed_at": datetime.now().isoformat(),
            })
            return

        file_count = 0

        for idx, raw_video in enumerate(all_videos):
            video_info = DouyinService.parse_video_info(raw_video)
            aweme_id = video_info["aweme_id"]
            title = video_info["title"]

            task["current_video"] = title
            task["processed_videos"] = idx
            task["progress"] = int(idx / total * 95)

            safe_title = _safe_filename(title)
            md_path = os.path.join(job_dir, f"{safe_title}_{aweme_id}.md")

            if os.path.exists(md_path):
                file_count += 1
                task["file_count"] = file_count
                continue

            try:
                vc = await fetcher.fetch_content(video_info)
                md_content = _build_markdown(vc, vc.content_source)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                file_count += 1
                logger.info(
                    f"[DouyinExport] [{idx+1}/{total}] ✅ "
                    f"{title[:40]} ({vc.content_source})"
                )
            except Exception as e:
                logger.error(f"[DouyinExport] [{idx+1}/{total}] ❌ {aweme_id}: {e}")

            task["file_count"] = file_count
            await asyncio.sleep(0.3)

        task.update({
            "status": "completed",
            "progress": 100,
            "processed_videos": total,
            "current_video": "",
            "file_count": file_count,
            "message": f"导出完成，共生成 {file_count} 个 Markdown 文件",
            "completed_at": datetime.now().isoformat(),
        })
        logger.info(f"[DouyinExport] 任务完成: job_id={job_id}, files={file_count}")

    except Exception as e:
        logger.error(f"[DouyinExport] 任务失败: job_id={job_id}, error={e}")
        task.update({
            "status": "failed",
            "message": f"导出失败: {str(e)}",
            "completed_at": datetime.now().isoformat(),
        })
    finally:
        await douyin.close()


# ==================== 路由 ====================

@router.post("/start")
async def start_douyin_export(req: DouyinExportRequest, background_tasks: BackgroundTasks):
    """
    启动抖音收藏夹导出任务（后台异步执行）

    无需 B站会话，通过请求体传入抖音 Cookie。
    返回 job_id，通过 GET /douyin-export/status/{job_id} 轮询进度。
    """
    if not req.cookie.strip():
        raise HTTPException(status_code=400, detail="Cookie 不能为空")

    # 验证 Evil0ctal API 可达性
    from app.services.douyin import DouyinService
    douyin_check = DouyinService(cookie=req.cookie, evil0ctal_url=req.evil0ctal_url)
    available = await douyin_check.check_evil0ctal_available()
    await douyin_check.close()

    if not available:
        raise HTTPException(
            status_code=503,
            detail=(
                f"无法连接到 Evil0ctal API 服务（{req.evil0ctal_url}）。"
                "请先按文档部署：git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API"
            ),
        )

    # 检查 Ollama 可用性（仅 ollama 后端）
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
                detail=f"无法连接到 Ollama 服务（{req.ollama_url}），请确认 Ollama 已启动",
            )
        if not asr_check.check_model_available():
            raise HTTPException(
                status_code=503,
                detail=f"Ollama 中未找到模型 '{req.ollama_model}'，请先运行：ollama pull {req.ollama_model}",
            )

    job_id = str(uuid.uuid4())
    douyin_export_tasks[job_id] = {
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

    background_tasks.add_task(_run_douyin_export, job_id, req)
    logger.info(
        f"[DouyinExport] 任务已创建: job_id={job_id}, "
        f"limit={req.limit}, backend={req.asr_backend}"
    )

    return {"job_id": job_id, "message": "抖音导出任务已启动"}


@router.get("/status/{job_id}", response_model=DouyinExportStatus)
async def get_douyin_export_status(job_id: str):
    """轮询导出任务进度"""
    task = douyin_export_tasks.get(job_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return DouyinExportStatus(**task)


@router.get("/download/{job_id}")
async def download_douyin_export(job_id: str):
    """下载导出结果（ZIP 压缩包），任务完成后才可下载"""
    task = douyin_export_tasks.get(job_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail="任务尚未完成")

    job_dir = os.path.join(DOUYIN_EXPORT_DIR, job_id)
    if not os.path.exists(job_dir):
        raise HTTPException(status_code=404, detail="导出文件不存在")

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
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="douyin_export_{ts}.zip"'},
    )
