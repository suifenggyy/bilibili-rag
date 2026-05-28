"""
B站收藏夹 → Markdown 导出工具

从 B站收藏夹获取视频，将音频通过 ASR 转写为文字，保存为 Markdown 文件。
不依赖 RAG 或向量数据库，独立运行。

登录方式：扫码登录（登录凭据会缓存到 .bili_session.json，下次自动复用）

支持两种 ASR 后端：
  - dashscope：DashScope 云端（需配置 DASHSCOPE_API_KEY）
  - ollama：本地 Whisper（需安装 Ollama 并运行 `ollama pull whisper`）

音频下载依赖：
  - yt-dlp：用于从 B站页面稳定提取音频
  - ffmpeg：用于音频抽取/转码

用法:
    python scripts/export_favorites_to_md.py                         # 交互式选择收藏夹
    python scripts/export_favorites_to_md.py --all                   # 导出所有收藏夹
    python scripts/export_favorites_to_md.py --folder-id 12345678    # 导出指定收藏夹
    python scripts/export_favorites_to_md.py --output-dir /path/to/collection   # 指定输出目录
    python scripts/export_favorites_to_md.py --relogin               # 强制重新扫码登录
    python scripts/export_favorites_to_md.py --asr-backend ollama    # 使用 Ollama 本地转写
    python scripts/export_favorites_to_md.py --asr-backend ollama --ollama-model whisper:large
"""

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# 将项目根目录加入路径，确保可以导入 app 模块
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
from loguru import logger
from app.services.content_summary import append_summary_section
from app.services.processing_status import ProcessingStatusService
from app.database import get_db_context

load_dotenv(ROOT_DIR / ".env")

# 本地 session 缓存文件（保存在项目根目录，不提交到 git）
SESSION_CACHE_FILE = ROOT_DIR / ".bili_session.json"


def _get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


DEFAULT_COLLECTION_OUTPUT_DIR = (
    "/Users/gongyongyue/FangcloudV2/personal_space.localized/同步空间/个人资料/Obsidian/jarvis/collection"
)


def _safe_filename(name: str, max_len: int = 80) -> str:
    """将字符串转为合法文件名"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:max_len] if len(name) > max_len else name


# ==================== Session 缓存 ====================

def load_cached_session() -> dict | None:
    """从本地缓存文件加载 session"""
    if not SESSION_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(SESSION_CACHE_FILE.read_text(encoding="utf-8"))
        # 检查是否过期（B站 cookie 有效期约 180 天，保守取 30 天）
        saved_at = data.get("saved_at", 0)
        if time.time() - saved_at > 30 * 24 * 3600:
            logger.debug("缓存 session 已超过 30 天，视为过期")
            return None
        return data
    except Exception:
        return None


def save_session(cookies: dict, user_info: dict) -> None:
    """将 session 保存到本地缓存文件"""
    data = {
        "cookies": cookies,
        "user_info": user_info,
        "saved_at": time.time(),
    }
    SESSION_CACHE_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.debug(f"Session 已缓存到 {SESSION_CACHE_FILE}")


def clear_session_cache() -> None:
    """清除本地 session 缓存"""
    if SESSION_CACHE_FILE.exists():
        SESSION_CACHE_FILE.unlink()


# ==================== 扫码登录 ====================

def _print_qrcode_ascii(qrcode_url: str) -> None:
    """在终端中打印二维码（ASCII 模式）"""
    try:
        import qrcode as qrcode_lib

        qr = qrcode_lib.QRCode(
            version=1,
            error_correction=qrcode_lib.constants.ERROR_CORRECT_L,
            box_size=1,
            border=1,
        )
        qr.add_data(qrcode_url)
        qr.make(fit=True)
        # 打印到终端
        qr.print_ascii(invert=True)
    except Exception as e:
        logger.debug(f"ASCII 二维码生成失败: {e}")
        print(f"  请用手机 B站 App 扫描此链接登录：\n  {qrcode_url}")


def _save_and_open_qrcode_image(img_base64: str) -> None:
    """将二维码图片保存到临时文件并尝试用系统默认程序打开"""
    import subprocess
    import tempfile

    try:
        img_data = base64.b64decode(img_base64.split(",", 1)[-1])
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.write(img_data)
        tmp.close()
        # macOS: open, Linux: xdg-open
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.Popen([opener, tmp.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"  （已在系统图片查看器中打开二维码图片：{tmp.name}）")
    except Exception as e:
        logger.debug(f"打开二维码图片失败: {e}")


async def qr_login(bili) -> dict:
    """
    扫码登录，返回 cookies 字典。

    流程：
    1. 生成二维码并在终端展示
    2. 轮询扫码状态，最多等待 3 分钟
    3. 扫码确认后返回 cookies
    """
    print("\n🔑 正在生成登录二维码...", end="", flush=True)
    qr_data = await bili.generate_qrcode()
    qrcode_key = qr_data["qrcode_key"]
    qrcode_url = qr_data["qrcode_url"]
    img_base64 = qr_data.get("qrcode_image_base64", "")
    print(" ✅")

    print("\n" + "=" * 56)
    print("  请用手机 B站 App 扫描下方二维码登录")
    print("=" * 56)
    _print_qrcode_ascii(qrcode_url)

    # 同时尝试在系统图片查看器中打开（方便在无法显示 ASCII 二维码时使用）
    if img_base64:
        _save_and_open_qrcode_image(img_base64)

    print("=" * 56)
    print("  等待扫码中，最多等待 3 分钟...")

    deadline = time.time() + 180  # 3 分钟超时
    last_status = ""

    while time.time() < deadline:
        await asyncio.sleep(2)
        try:
            result = await bili.poll_qrcode_status(qrcode_key)
        except Exception as e:
            logger.warning(f"轮询失败: {e}")
            continue

        status = result.get("status")
        if status != last_status:
            status_hints = {
                "waiting": "  ⏳ 等待扫码...",
                "scanned": "  📱 已扫码，请在手机上确认登录...",
                "expired": None,
            }
            hint = status_hints.get(status)
            if hint:
                print(hint, flush=True)
            last_status = status

        if status == "confirmed":
            cookies = result.get("cookies", {})
            print("  ✅ 扫码登录成功！\n")
            return cookies

        if status == "expired":
            raise RuntimeError("二维码已过期，请重新运行脚本")

    raise RuntimeError("等待扫码超时（3 分钟），请重新运行脚本")


# ==================== 登录入口（支持缓存复用）====================

async def ensure_logged_in(relogin: bool = False) -> tuple:
    """
    确保已登录，返回 (BilibiliService 实例, user_info 字典)。

    - 优先复用缓存的 session（最长 30 天）
    - 缓存失效或 relogin=True 时，发起扫码登录
    - 登录成功后自动缓存 session
    """
    from app.services.bilibili import BilibiliService

    if not relogin:
        cached = load_cached_session()
        if cached:
            cookies = cached.get("cookies", {})
            user_info = cached.get("user_info", {})
            bili = BilibiliService(
                sessdata=cookies.get("SESSDATA"),
                bili_jct=cookies.get("bili_jct"),
                dedeuserid=cookies.get("DedeUserID"),
            )
            # 验证 session 是否仍然有效
            try:
                live_info = await bili.get_user_info()
                uname = live_info.get("uname", user_info.get("uname", "未知用户"))
                print(f"🔐 已复用缓存登录：{uname}（如需切换账号请加 --relogin）")
                return bili, live_info
            except Exception:
                logger.debug("缓存 session 已失效，重新登录")
                await bili.close()
                clear_session_cache()

    # 扫码登录
    bili_tmp = BilibiliService()
    try:
        cookies = await qr_login(bili_tmp)
    finally:
        await bili_tmp.close()

    bili = BilibiliService(
        sessdata=cookies.get("SESSDATA"),
        bili_jct=cookies.get("bili_jct"),
        dedeuserid=cookies.get("DedeUserID"),
    )

    user_info = {}
    try:
        user_info = await bili.get_user_info()
        uname = user_info.get("uname", "未知用户")
        print(f"✅ 登录成功：{uname}")
    except Exception as e:
        logger.warning(f"获取用户信息失败: {e}")

    save_session(cookies, user_info)
    return bili, user_info


def _format_duration(seconds: int) -> str:
    if not seconds:
        return "未知"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _build_markdown(
    video: dict,
    asr_text: str,
    source: str,
    folder_title: str,
    summary_block: str = "",
) -> str:
    """构建 Markdown 文件内容（包含 YAML frontmatter）"""
    from app.services.knowledge_pipeline.frontmatter import (
        build_export_frontmatter,
        extract_plain_summary,
    )

    title = video.get("title") or "未知标题"
    bvid = video.get("bvid") or ""
    owner = (video.get("upper") or {}).get("name") or video.get("owner_name") or "未知UP主"
    duration = _format_duration(video.get("duration"))
    cover = video.get("cover") or video.get("pic") or ""
    intro = video.get("intro") or video.get("desc") or ""
    pub_time = video.get("pubtime") or video.get("ctime") or 0
    pub_str = datetime.fromtimestamp(pub_time).strftime("%Y-%m-%d") if pub_time else datetime.now().strftime("%Y-%m-%d")
    source_url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""

    # 构建 frontmatter
    plain_summary = extract_plain_summary(summary_block)
    frontmatter = build_export_frontmatter(
        title=title,
        date_str=pub_str,
        source=source_url,
        summary=plain_summary,
        platform="bilibili",
    )

    source_label = {
        "asr": "ASR 语音转写",
        "basic_info": "视频基本信息",
    }.get(source, source)

    lines = [
        f"# {title}",
        "",
        "## 视频信息",
        "",
        f"| 字段 | 内容 |",
        f"|------|------|",
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

    append_summary_section(lines, summary_block)
    lines += ["", "---", "", "## 转写内容", ""]

    if asr_text and asr_text.strip():
        lines.append(asr_text.strip())
    else:
        lines.append("_（未获取到有效内容）_")

    lines += ["", f"---", f"", f"_导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_"]

    return frontmatter + "\n".join(lines)


async def export_folder(
    bili,
    asr_service,
    fetcher,
    folder: dict,
    output_dir: Path,
) -> tuple[int, int]:
    """
    导出单个收藏夹

    Returns:
        (成功数, 失败数)
    """
    media_id = folder.get("id")
    folder_title = folder.get("title", f"收藏夹_{media_id}")
    media_count = folder.get("media_count", 0)

    print(f"\n📁 收藏夹：{folder_title}（共 {media_count} 个视频）")
    print(f"   输出目录：{output_dir}")

    # 获取所有视频
    try:
        videos = await bili.get_all_favorite_videos(media_id)
    except Exception as e:
        logger.error(f"获取收藏夹视频列表失败 [{folder_title}]: {e}")
        return 0, 0

    if not videos:
        print("   ⚠️  收藏夹为空，跳过")
        return 0, 0

    success, failed = 0, 0

    # 过滤失效视频
    valid_videos = []
    invalid_count = 0
    for v in videos:
        attr = v.get("attr", 0)
        title_raw = v.get("title", "")
        if attr == 9 or title_raw in ("已失效视频", "已删除视频"):
            invalid_count += 1
            continue
        if not (v.get("bvid") or v.get("bv_id")):
            invalid_count += 1
            continue
        valid_videos.append(v)

    if invalid_count:
        print(f"   ⚠️  过滤掉 {invalid_count} 个失效视频")

    _proc_svc = ProcessingStatusService()

    for i, video in enumerate(valid_videos, 1):
        bvid = video.get("bvid") or video.get("bv_id") or ""
        title = video.get("title") or bvid
        # cid 在收藏夹 API 返回的 ugc.first_cid 字段中
        cid = (video.get("ugc") or {}).get("first_cid") or video.get("cid") or None

        safe_title = _safe_filename(title)
        md_path = output_dir / f"{safe_title}_{bvid}.md"

        # 检查 DB 状态，已完成则跳过
        already_done = False
        try:
            async with get_db_context() as db:
                proc_rec = await _proc_svc.get_or_create(db, "bilibili", bvid, title)
                await db.commit()
                already_done = _proc_svc.is_completed(proc_rec)
        except Exception as _db_err:
            logger.debug(f"DB 状态检查失败（跳过）: {_db_err}")

        if already_done and md_path.exists():
            print(f"   [{i}/{len(valid_videos)}] ⏭️  已完成，跳过：{title[:40]}")
            success += 1
            continue

        # 文件存在但 DB 无记录时也跳过（兼容旧数据）
        if md_path.exists() and not already_done:
            print(f"   [{i}/{len(valid_videos)}] ⏭️  已存在，跳过：{title[:40]}")
            success += 1
            continue

        print(f"   [{i}/{len(valid_videos)}] 🔄 处理中：{title[:50]}", end="", flush=True)

        try:
            from app.models import ContentSource

            video_content = await fetcher.fetch_content(
                bvid,
                cid=cid,
                title=title,
                fail_on_audio_download_error=True,
            )
            source = video_content.source.value if hasattr(video_content.source, "value") else str(video_content.source)
            md_content = _build_markdown(
                video,
                video_content.content,
                source,
                folder_title,
                getattr(video_content, "summary_block", "") or "",
            )

            md_path.write_text(md_content, encoding="utf-8")
            source_label = "ASR ✅" if source == "asr" else "基本信息 ⚠️"
            print(f"  → {source_label}")
            success += 1

            # 更新处理状态
            try:
                async with get_db_context() as db:
                    rec = await _proc_svc.get_or_create(db, "bilibili", bvid, title)
                    asr_raw = getattr(video_content, "asr_raw_text", None)
                    if asr_raw:
                        await _proc_svc.mark_asr_done(db, rec, asr_raw)
                    if video_content.content:
                        await _proc_svc.mark_correction_done(db, rec, video_content.content)
                    summary = getattr(video_content, "summary_block", None)
                    if summary:
                        await _proc_svc.mark_summary_done(db, rec, summary)
                    await _proc_svc.mark_completed(db, rec)
                    await db.commit()
            except Exception as _db_err:
                logger.debug(f"DB 状态写入失败（不影响导出）: {_db_err}")

        except Exception as e:
            logger.error(f"处理视频失败 [{bvid}]: {e}")
            print(f"  → ❌ 失败: {e}")
            failed += 1
            try:
                async with get_db_context() as db:
                    rec = await _proc_svc.get_or_create(db, "bilibili", bvid, title)
                    await _proc_svc.mark_failed(db, rec, "asr", str(e))
                    await db.commit()
            except Exception as _db_err:
                logger.debug(f"DB 失败状态写入失败（不影响导出）: {_db_err}")

        # 控制请求速率
        await asyncio.sleep(0.5)

    return success, failed


async def interactive_select_folders(folders: list) -> list:
    """交互式选择要导出的收藏夹"""
    if not folders:
        print("⚠️  未找到任何收藏夹")
        return []

    print("\n📚 你的收藏夹列表：")
    print("-" * 60)
    for i, f in enumerate(folders, 1):
        print(f"  [{i:2d}] {f['title']:<30} ({f.get('media_count', 0)} 个视频)")
    print("-" * 60)
    print("  [0] 全部导出")
    print()

    while True:
        raw = input("请输入要导出的收藏夹编号（多个用逗号分隔，0=全部）：").strip()
        if not raw:
            continue

        if raw == "0":
            return folders

        try:
            indices = [int(x.strip()) for x in raw.split(",")]
            selected = []
            for idx in indices:
                if 1 <= idx <= len(folders):
                    selected.append(folders[idx - 1])
                else:
                    print(f"  ⚠️  无效编号: {idx}，请重新输入")
                    selected = []
                    break
            if selected:
                return selected
        except ValueError:
            print("  ⚠️  输入格式不正确，请输入数字（例如：1,3,5）")


async def _build_asr_service(args):
    """根据参数构建 ASR 服务实例，并在启动时做可用性检查"""
    backend = args.asr_backend

    # 自动选择：跟随 .env 中的 ASR_BACKEND
    if backend == "auto":
        backend = _get_env("ASR_BACKEND", "whisper").strip().lower() or "whisper"
        if backend == "auto":
            backend = "whisper"
        print(f"ℹ️  自动模式使用 .env 中的 ASR_BACKEND={backend}")

    if backend == "dashscope":
        if not args.api_key:
            print("⚠️  未配置 DASHSCOPE_API_KEY，ASR 转写不可用，将仅保存基本信息")
        from app.services.asr import ASRService
        print(f"🔊 ASR 后端：DashScope（{_get_env('ASR_MODEL', 'paraformer-v2')}）")
        return ASRService(api_key=args.api_key or None)

    if backend == "whisper":
        from app.services.asr_whisper import OpenAIWhisperASRService

        asr = OpenAIWhisperASRService(
            model=_get_env("WHISPER_MODEL", "turbo"),
            language=_get_env("WHISPER_LANGUAGE", "zh"),
            timeout=int(_get_env("ASR_TIMEOUT", "600")),
        )
        print(f"🔊 ASR 后端：openai-whisper 本地（模型：{asr.model_name}）")
        return asr

    # Ollama 后端
    from app.services.asr_local import OllamaASRService
    asr = OllamaASRService(
        base_url=args.ollama_url,
        model=args.ollama_model,
        language=args.ollama_language,
        timeout=int(_get_env("ASR_TIMEOUT", "600")),
    )
    print(f"🔊 ASR 后端：Ollama 本地（{asr.base_url}，模型：{asr.model}）")

    # 检查 Ollama 服务是否可达
    if not asr.check_ollama_available():
        print(
            f"❌ 无法连接到 Ollama 服务（{asr.base_url}），"
            "请确认 Ollama 已启动（运行 `ollama serve`）"
        )
        sys.exit(1)

    # 检查模型是否已安装
    if not asr.check_model_available():
        print(
            f"⚠️  Ollama 中未找到模型 '{asr.model}'，"
            f"请先运行：ollama pull {asr.model}"
        )
        sys.exit(1)

    print(f"   ✅ Ollama 服务正常，模型 '{asr.model}' 已就绪")
    return asr


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="B站收藏夹音频转写 → Markdown 导出工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--folder-id", type=int, help="指定收藏夹 ID（media_id）")
    parser.add_argument("--all", action="store_true", help="导出所有收藏夹")
    parser.add_argument(
        "--output-dir",
        default=_get_env("COLLECTION_OUTPUT_DIR", DEFAULT_COLLECTION_OUTPUT_DIR),
        help="输出目录（默认读取 COLLECTION_OUTPUT_DIR）",
    )
    parser.add_argument(
        "--relogin",
        action="store_true",
        help="强制重新扫码登录（忽略缓存）",
    )
    # ASR 后端选择
    parser.add_argument(
        "--asr-backend",
        default="ollama",
        choices=["auto", "dashscope", "ollama", "whisper"],
        help="ASR 转写后端（默认: ollama；auto=按 .env 中 ASR_BACKEND 解析）",
    )
    # DashScope 参数
    parser.add_argument(
        "--api-key",
        default=_get_env("DASHSCOPE_API_KEY"),
        help="DashScope API Key（--asr-backend dashscope 时使用）",
    )
    # Ollama 参数
    parser.add_argument(
        "--ollama-url",
        default=_get_env("OLLAMA_BASE_URL", "http://localhost:11434"),
        help="Ollama 服务地址（默认: http://localhost:11434）",
    )
    parser.add_argument(
        "--ollama-model",
        default=_get_env("OLLAMA_ASR_MODEL", "whisper"),
        help="Ollama Whisper 模型名称（默认: whisper）",
    )
    parser.add_argument(
        "--ollama-language",
        default=_get_env("OLLAMA_ASR_LANGUAGE", "zh"),
        help="转写语言提示（默认: zh，留空则自动检测）",
    )
    return parser


async def main():
    parser = build_parser()
    args = parser.parse_args()

    from app.services.content_storage import ContentStorageManager
    from app.services.content_fetcher import ContentFetcher
    from app.database import init_db
    await init_db()

    storage_manager = ContentStorageManager(export_root=args.output_dir)
    output_dir = storage_manager.get_export_dir("bilibili")

    # 登录（复用缓存或扫码）
    bili, user_info = await ensure_logged_in(relogin=args.relogin)

    # 初始化 ASR 服务
    asr = await _build_asr_service(args)
    fetcher = ContentFetcher(bili, asr, storage_manager=storage_manager)

    try:
        # 获取收藏夹列表
        print("📂 获取收藏夹列表...", end="", flush=True)
        try:
            folders = await bili.get_user_favorites()
            print(f" ✅  找到 {len(folders)} 个收藏夹")
        except Exception as e:
            print(f" ❌  获取收藏夹失败: {e}")
            sys.exit(1)

        # 决定要处理的收藏夹
        if args.folder_id:
            selected = [f for f in folders if f.get("id") == args.folder_id]
            if not selected:
                print(f"❌ 未找到 ID 为 {args.folder_id} 的收藏夹")
                sys.exit(1)
        elif args.all:
            selected = folders
        else:
            selected = await interactive_select_folders(folders)

        if not selected:
            print("未选择任何收藏夹，退出")
            sys.exit(0)

        # 开始导出
        print(f"\n🚀 开始导出 {len(selected)} 个收藏夹 → {output_dir.resolve()}")
        total_success, total_failed = 0, 0

        for folder in selected:
            s, f = await export_folder(bili, asr, fetcher, folder, output_dir)
            total_success += s
            total_failed += f

        print(f"\n{'='*60}")
        print(f"✅ 导出完成！成功：{total_success} 个，失败：{total_failed} 个")
        print(f"📂 文件保存在：{output_dir.resolve()}")

    finally:
        await bili.close()


if __name__ == "__main__":
    asyncio.run(main())
