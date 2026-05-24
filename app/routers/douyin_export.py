"""
抖音收藏夹导出路由

提供 Web API 接口，在后台异步执行抖音收藏夹 → Markdown 的导出任务。
与 B站导出路由不同，无需 B站会话，Cookie 直接由请求体传入。

端点：
    GET  /douyin-export/qrcode              生成 QR 码登录二维码
    GET  /douyin-export/qrcode/poll/{token} 轮询 QR 码登录状态
    POST /douyin-export/start               启动导出任务
    GET  /douyin-export/status/{id}         查询任务进度
    GET  /douyin-export/download/{id}       下载 ZIP 结果
"""

import asyncio
import io
import os
import re
import time
import uuid
import zipfile
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, get_db_context
from app.models import DouyinSession, DouyinCreator
from app.services.content_summary import append_summary_section
from app.services.content_storage import ContentStorageManager
from app.services.processing_status import ProcessingStatusService

router = APIRouter(prefix="/douyin-export", tags=["抖音导出"])

# 任务状态（内存存储，重启后清空）
douyin_export_tasks: dict[str, dict] = {}
_proc_svc = ProcessingStatusService()

# ==================== QR 登录模型 ====================

class DouyinQRCodeResponse(BaseModel):
    token: str
    qrcode_url: str
    qrcode_image_base64: str


class DouyinQRPollResponse(BaseModel):
    status: str          # waiting | scanned | confirmed | expired
    message: str
    cookie_str: Optional[str] = None
    session_id: Optional[str] = None   # set when confirmed and saved


class DouyinSessionResponse(BaseModel):
    session_id: str
    cookie_str: str


class DouyinCreatorRequest(BaseModel):
    """添加抖音创作者配置"""
    sec_uid: str               # sec_uid 或主页 URL
    nickname: Optional[str] = None
    after_date: Optional[str] = None   # YYYY-MM-DD


class DouyinCreatorResponse(BaseModel):
    id: int
    sec_uid: str
    nickname: Optional[str] = None
    after_date: Optional[str] = None


# ==================== QR 登录路由 ====================

@router.get("/qrcode", response_model=DouyinQRCodeResponse)
async def douyin_generate_qrcode():
    """生成抖音扫码登录二维码。"""
    from app.services.douyin_auth import DouyinAuthService
    async with DouyinAuthService() as svc:
        try:
            result = await svc.generate_qrcode()
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    return DouyinQRCodeResponse(**result)


@router.get("/qrcode/poll/{token}", response_model=DouyinQRPollResponse)
async def douyin_poll_qrcode(token: str, db: AsyncSession = Depends(get_db)):
    """轮询抖音二维码登录状态。confirmed 时保存 Cookie 到数据库并返回 session_id。"""
    from app.services.douyin_auth import DouyinAuthService
    async with DouyinAuthService() as svc:
        try:
            result = await svc.poll_qrcode_status(token)
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))

    session_id: Optional[str] = None
    if result.get("status") == "confirmed" and result.get("cookie_str"):
        session_id = str(uuid.uuid4())
        db_session = DouyinSession(
            session_id=session_id,
            cookie_str=result["cookie_str"],
        )
        db.add(db_session)
        await db.commit()
        logger.info("[DouyinExport] Cookie 已保存, session_id={}", session_id)

    return DouyinQRPollResponse(**result, session_id=session_id)


@router.get("/session/{session_id}", response_model=DouyinSessionResponse)
async def douyin_get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """根据 session_id 恢复抖音 Cookie（刷新页面后调用）。"""
    row = await db.scalar(
        select(DouyinSession).where(DouyinSession.session_id == session_id)
    )
    if not row or not row.cookie_str:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    return DouyinSessionResponse(session_id=session_id, cookie_str=row.cookie_str)


@router.delete("/session/{session_id}", status_code=204)
async def douyin_delete_session(session_id: str, db: AsyncSession = Depends(get_db)):
    """删除保存的抖音 Cookie（登出）。"""
    row = await db.scalar(
        select(DouyinSession).where(DouyinSession.session_id == session_id)
    )
    if row:
        await db.delete(row)
        await db.commit()
        logger.info("[DouyinExport] 会话已删除, session_id={}", session_id)


class DouyinExportRequest(BaseModel):
    """抖音导出请求"""
    cookie: str = Field(..., description="抖音浏览器 Cookie 字符串")
    evil0ctal_url: str = Field(
        default="",
        description="已废弃，忽略（直接调用子模块，无需外部服务）",
    )
    limit: int = Field(
        default=0,
        ge=0,
        description="最多导出视频数（0=全部）",
    )
    asr_backend: str = Field(
        default="auto",
        description="ASR 后端：auto | dashscope | ollama | whisper",
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
    logs: list[str] = []

    model_config = {"extra": "ignore"}


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
    append_summary_section(lines, getattr(vc, "summary_block", ""))
    lines += ["", "---", "", "## 转写内容", ""]
    if vc.content and vc.content.strip():
        lines.append(vc.content.strip())
    else:
        lines.append("_（未获取到有效内容）_")
    lines += ["", "---", f"", f"_导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"]
    return "\n".join(lines)


def _build_asr_service(req: DouyinExportRequest):
    """根据请求参数构建 ASR 服务实例"""
    from app.services.asr_factory import create_asr_service

    return create_asr_service(
        backend=req.asr_backend,
        ollama_base_url=req.ollama_url,
        ollama_model=req.ollama_model,
        ollama_language=req.ollama_language,
    )


# ==================== 后台导出任务 ====================

async def _run_douyin_export(job_id: str, req: DouyinExportRequest):
    """后台执行抖音导出任务"""
    task = douyin_export_tasks[job_id]

    from app.services.douyin import DouyinService
    from app.services.douyin_fetcher import DouyinContentFetcher

    douyin = DouyinService(cookie=req.cookie)
    asr = _build_asr_service(req)
    storage_manager = ContentStorageManager()
    fetcher = DouyinContentFetcher(
        asr_service=asr,
        storage_manager=storage_manager,
    )

    def _log(msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        logger.info(f"[DouyinExport] {msg}")
        task["logs"].append(entry)
        if len(task["logs"]) > 200:
            task["logs"] = task["logs"][-200:]

    try:
        task["status"] = "running"
        task["message"] = "正在获取收藏夹视频列表..."
        _log("开始获取抖音收藏夹视频列表...")

        # 获取所有收藏视频
        all_videos = await douyin.get_all_collection_videos()
        if req.limit > 0:
            all_videos = all_videos[:req.limit]

        total = len(all_videos)
        task["total_videos"] = total
        task["message"] = f"共 {total} 个视频，开始转写..."
        _log(f"获取到 {total} 个视频，开始处理...")

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

            # Skip if already exported
            async with get_db_context() as db:
                proc_rec = await _proc_svc.get_or_create(db, "douyin", aweme_id, title)
                await db.commit()
            if _proc_svc.is_completed(proc_rec):
                md_path = storage_manager.build_markdown_path("douyin", title, aweme_id)
                if md_path.exists():
                    file_count += 1
                    task["output_files"].append(str(md_path))
                    task["file_count"] = file_count
                    _log(f"[{idx+1}/{total}] ⏭ 已完成，跳过：{title[:40]}")
                    continue

            md_path = storage_manager.build_markdown_path("douyin", title, aweme_id)

            if md_path.exists():
                file_count += 1
                task["output_files"].append(str(md_path))
                task["file_count"] = file_count
                _log(f"[{idx+1}/{total}] ⏭ 文件已存在，跳过：{title[:40]}")
                continue

            _log(f"[{idx+1}/{total}] 🔄 开始处理：{title[:50]}")
            task["message"] = f"[{idx+1}/{total}] 🔊 转写中: {title[:30]}..."

            try:
                _log(f"[{idx+1}/{total}] 🎵 下载音频并进行 ASR 转写...")
                vc = await fetcher.fetch_content(video_info)
                source_label = "ASR ✅" if vc.content_source == "asr" else "基本信息 ⚠️"
                _log(f"[{idx+1}/{total}] ✅ 转写完成（{source_label}）：{title[:40]}")
                md_content = _build_markdown(vc, vc.content_source)
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                file_count += 1
                task["output_files"].append(str(md_path))
                # Track processing stages
                try:
                    async with get_db_context() as db:
                        r = await _proc_svc.get_or_create(db, "douyin", aweme_id, title)
                        if vc.asr_raw_text:
                            await _proc_svc.mark_asr_done(db, r, vc.asr_raw_text)
                            await _proc_svc.mark_correction_done(db, r, vc.content or "")
                        if vc.summary_block:
                            await _proc_svc.mark_summary_done(db, r, vc.summary_block)
                        await _proc_svc.mark_completed(db, r)
                        await db.commit()
                except Exception as _e:
                    logger.warning(f"[DouyinExport] 处理状态记录失败（非关键）[{aweme_id}]: {_e}")
            except Exception as e:
                _log(f"[{idx+1}/{total}] ❌ 失败：{title[:40]} — {str(e)[:80]}")
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
        _log(f"🎉 任务完成，共生成 {file_count} 个 Markdown 文件")
        logger.info(f"[DouyinExport] 任务完成: job_id={job_id}, files={file_count}")

    except Exception as e:
        logger.error(f"[DouyinExport] 任务失败: job_id={job_id}, error={e}")
        _log(f"❌ 任务失败: {str(e)[:100]}")
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
    douyin_check = DouyinService(cookie=req.cookie)
    available = await douyin_check.check_evil0ctal_available()
    await douyin_check.close()

    if not available:
        raise HTTPException(
            status_code=503,
            detail=(
                "Evil0ctal 子模块不可用，请确认已运行 git submodule update --init "
                "并安装依赖（pip install -r requirements.txt）。"
            ),
        )

    # 检查 Ollama 可用性（仅 ollama 后端）
    from app.services.asr_factory import resolve_asr_backend

    backend = resolve_asr_backend(req.asr_backend)
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
        "output_files": [],
        "logs": [],
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

    output_files = [path for path in task.get("output_files", []) if os.path.exists(path)]
    if not output_files:
        raise HTTPException(status_code=404, detail="导出文件不存在")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        export_root = os.path.expanduser(settings.collection_output_dir)
        for abs_path in output_files:
            arc_name = os.path.relpath(abs_path, export_root)
            zf.write(abs_path, arc_name)
    zip_buffer.seek(0)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="douyin_export_{ts}.zip"'},
    )


# ==================== 创作者管理 ====================

@router.post("/creators", response_model=DouyinCreatorResponse)
async def add_douyin_creator(
    request: DouyinCreatorRequest,
    session_id: str = Query(..., description="会话ID"),
    db: AsyncSession = Depends(get_db),
):
    """添加抖音创作者配置"""
    sec_uid = request.sec_uid.strip()
    if not sec_uid:
        raise HTTPException(status_code=400, detail="sec_uid 不能为空")

    result = await db.execute(
        select(DouyinCreator).where(
            DouyinCreator.session_id == session_id,
            DouyinCreator.sec_uid == sec_uid,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.nickname = request.nickname or existing.nickname
        existing.after_date = request.after_date
        await db.commit()
        await db.refresh(existing)
        return DouyinCreatorResponse(id=existing.id, sec_uid=existing.sec_uid, nickname=existing.nickname, after_date=existing.after_date)

    creator = DouyinCreator(
        session_id=session_id,
        sec_uid=sec_uid,
        nickname=request.nickname,
        after_date=request.after_date,
    )
    db.add(creator)
    await db.commit()
    await db.refresh(creator)
    return DouyinCreatorResponse(id=creator.id, sec_uid=creator.sec_uid, nickname=creator.nickname, after_date=creator.after_date)


@router.get("/creators", response_model=List[DouyinCreatorResponse])
async def list_douyin_creators(
    session_id: str = Query(..., description="会话ID"),
    db: AsyncSession = Depends(get_db),
):
    """获取已配置的抖音创作者列表"""
    result = await db.execute(
        select(DouyinCreator).where(DouyinCreator.session_id == session_id)
    )
    creators = result.scalars().all()
    return [DouyinCreatorResponse(id=c.id, sec_uid=c.sec_uid, nickname=c.nickname, after_date=c.after_date) for c in creators]


@router.delete("/creators/{creator_id}", status_code=204)
async def delete_douyin_creator(
    creator_id: int,
    session_id: str = Query(..., description="会话ID"),
    db: AsyncSession = Depends(get_db),
):
    """删除抖音创作者配置"""
    result = await db.execute(
        select(DouyinCreator).where(
            DouyinCreator.id == creator_id,
            DouyinCreator.session_id == session_id,
        )
    )
    creator = result.scalar_one_or_none()
    if not creator:
        raise HTTPException(status_code=404, detail="未找到该创作者配置")
    await db.delete(creator)
    await db.commit()


@router.post("/creators/sync")
async def sync_douyin_creator_videos(
    background_tasks: BackgroundTasks,
    session_id: str = Query(..., description="会话ID"),
    db: AsyncSession = Depends(get_db),
):
    """同步所有已配置抖音创作者的作品（后台任务）"""
    # 获取 Cookie
    result = await db.execute(
        select(DouyinCreator).where(DouyinCreator.session_id == session_id)
    )
    creators = result.scalars().all()
    if not creators:
        raise HTTPException(status_code=400, detail="尚未配置任何抖音创作者")

    # 获取 Cookie
    from sqlalchemy import select as sa_select
    sess_result = await db.execute(
        sa_select(DouyinSession).where(DouyinSession.session_id == session_id)
    )
    sess = sess_result.scalar_one_or_none()
    cookie_str = sess.cookie_str if sess else ""

    creator_list = [
        {"id": c.id, "sec_uid": c.sec_uid, "nickname": c.nickname, "after_date": c.after_date}
        for c in creators
    ]

    job_id = str(uuid.uuid4())
    douyin_export_tasks[job_id] = {
        "status": "pending",
        "progress": 0,
        "current_step": "初始化中...",
        "total": 0,
        "done": 0,
        "message": "",
    }

    background_tasks.add_task(
        _sync_douyin_creators_task,
        job_id,
        session_id,
        cookie_str,
        creator_list,
    )
    return {"task_id": job_id, "message": "抖音创作者同步任务已启动"}


async def _sync_douyin_creators_task(
    job_id: str,
    session_id: str,
    cookie_str: str,
    creator_list: list,
):
    """后台任务：获取抖音创作者视频并导出为 Markdown"""
    from app.services.douyin import DouyinService
    storage = ContentStorageManager()

    try:
        douyin_export_tasks[job_id]["status"] = "running"
        total_saved = 0

        douyin_svc = DouyinService(cookie=cookie_str)
        try:
            for creator in creator_list:
                sec_uid = creator["sec_uid"]
                nickname = creator.get("nickname") or sec_uid[:20]
                after_date = creator.get("after_date")

                douyin_export_tasks[job_id]["current_step"] = f"获取 {nickname} 的作品列表..."
                logger.info(f"[Douyin Creator Sync] 同步 {nickname}, after_date={after_date}")

                try:
                    videos = await douyin_svc.get_all_creator_videos(sec_uid, after_date=after_date)
                except Exception as e:
                    logger.error(f"[Douyin Creator Sync] 获取 {sec_uid} 失败: {e}")
                    continue

                douyin_export_tasks[job_id]["total"] = douyin_export_tasks[job_id].get("total", 0) + len(videos)
                logger.info(f"[Douyin Creator Sync] {nickname} 共 {len(videos)} 个视频")

                for video in videos:
                    info = DouyinService.parse_video_info(video)
                    try:
                        content_lines = [
                            f"# {info['title']}",
                            f"\n作者：{info['author']}",
                            f"\n链接：{info['share_url']}",
                            f"\n发布时间：{datetime.fromtimestamp(info['create_time']).strftime('%Y-%m-%d %H:%M') if info['create_time'] else '未知'}",
                            "\n\n（暂无转写文本）",
                        ]
                        content = "\n".join(content_lines)
                        output_path = await storage.save_content(
                            platform="douyin_creator",
                            content_id=info["aweme_id"],
                            title=info["title"],
                            content=content,
                        )
                        if output_path:
                            total_saved += 1
                    except Exception as e:
                        logger.error(f"[Douyin Creator Sync] 保存视频 {info.get('aweme_id')} 失败: {e}")

                    douyin_export_tasks[job_id]["done"] = douyin_export_tasks[job_id].get("done", 0) + 1
                    total = max(douyin_export_tasks[job_id].get("total", 1), 1)
                    done = douyin_export_tasks[job_id].get("done", 0)
                    douyin_export_tasks[job_id]["progress"] = min(99, int(done / total * 100))
        finally:
            await douyin_svc.close()

        douyin_export_tasks[job_id]["status"] = "completed"
        douyin_export_tasks[job_id]["progress"] = 100
        douyin_export_tasks[job_id]["current_step"] = "完成"
        douyin_export_tasks[job_id]["message"] = f"已导出 {total_saved} 个视频"
        logger.info(f"[Douyin Creator Sync] 完成，共保存 {total_saved} 个视频")

    except Exception as e:
        logger.error(f"[Douyin Creator Sync] 任务失败: {e}")
        douyin_export_tasks[job_id]["status"] = "failed"
        douyin_export_tasks[job_id]["message"] = str(e)
