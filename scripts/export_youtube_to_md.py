"""
YouTube → Markdown 导出工具

从 YouTube 频道、播放列表、点赞视频或单视频抓取内容，通过 ASR 转写为文字，保存为 Markdown 文件。
不依赖 RAG 或向量数据库，独立运行。

【前提条件】
需要安装 yt-dlp：
    pip install yt-dlp

对于私有内容（点赞视频、稍后观看、会员专属等），需要提供 cookies 文件：
    浏览器安装 "Get cookies.txt LOCALLY" 插件 → 访问 youtube.com → 导出 cookies.txt

用法:
    # 导出点赞视频（收藏夹）
    python scripts/export_youtube_to_md.py --liked --cookie-file /path/to/cookies.txt

    # 导出稍后观看
    python scripts/export_youtube_to_md.py --watch-later --cookie-file /path/to/cookies.txt

    # 导出频道最新视频
    python scripts/export_youtube_to_md.py --url https://www.youtube.com/@ChannelName

    # 导出播放列表
    python scripts/export_youtube_to_md.py --url "https://www.youtube.com/playlist?list=PLxxx"

    # 导出单个视频
    python scripts/export_youtube_to_md.py --url https://www.youtube.com/watch?v=xxx

    # 多个来源
    python scripts/export_youtube_to_md.py --url URL1 URL2 URL3

    # 只导出指定日期后的视频
    python scripts/export_youtube_to_md.py --url URL --after-date 2024-01-01

    # 每个来源最多导出 N 个视频
    python scripts/export_youtube_to_md.py --url URL --limit 10

    # 使用 Cookie 文件（Netscape 格式）
    python scripts/export_youtube_to_md.py --url URL --cookie-file /path/to/cookies.txt

    # 指定 ASR 后端
    python scripts/export_youtube_to_md.py --url URL --asr-backend dashscope

    # 并发度（默认读取 ASR_CONCURRENCY）
    python scripts/export_youtube_to_md.py --url URL --concurrency 3
"""

import argparse
import asyncio
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
from loguru import logger

load_dotenv(ROOT_DIR / ".env")


def _get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


DEFAULT_OUTPUT_DIR = _get_env(
    "COLLECTION_OUTPUT_DIR",
    str(ROOT_DIR / "data" / "collection"),
)


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


def _build_markdown(vc, source: str) -> str:
    """构建 Markdown 文件内容"""
    source_label = {
        "asr": "ASR 语音转写",
        "basic_info": "视频基本信息（ASR 未成功）",
    }.get(source, source)

    lines = [
        f"# {vc.title}",
        "",
        "## 视频信息",
        "",
        "| 字段 | 内容 |",
        "|------|------|",
        f"| 视频ID | [{vc.video_id}]({vc.url}) |",
        f"| 频道 | {vc.channel} |",
        f"| 时长 | {_format_duration(vc.duration)} |",
        f"| 内容来源 | {source_label} |",
    ]

    if vc.cover_url:
        lines += ["", f"![封面]({vc.cover_url})"]

    if vc.description:
        lines += ["", "## 视频描述", "", vc.description[:500]]

    from app.services.content_summary import append_summary_section
    append_summary_section(lines, getattr(vc, "summary_block", ""))

    lines += ["", "---", "", "## 转写内容", ""]
    if vc.content and vc.content.strip():
        lines.append(vc.content.strip())
    else:
        lines.append("_（未获取到有效内容）_")

    lines += [
        "",
        "---",
        "",
        f"_导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
    ]
    return "\n".join(lines)


async def _build_asr_service(args):
    """根据参数构建 ASR 服务"""
    backend = args.asr_backend
    if backend == "auto":
        backend = _get_env("ASR_BACKEND", "whisper").strip().lower() or "whisper"
        if backend == "auto":
            backend = "whisper"
        print(f"ℹ️  自动模式使用 .env 中的 ASR_BACKEND={backend}")

    if backend == "dashscope":
        if not args.api_key:
            print("⚠️  未配置 DASHSCOPE_API_KEY，ASR 转写不可用，将仅保存基本信息")
            return None
        from app.services.asr import ASRService
        print(f"🔊 ASR 后端：DashScope（{_get_env('ASR_MODEL', 'paraformer-v2')}）")
        return ASRService(api_key=args.api_key)

    if backend == "whisper":
        from app.services.asr_whisper import OpenAIWhisperASRService
        asr = OpenAIWhisperASRService(
            model=_get_env("WHISPER_MODEL", "turbo"),
            language=_get_env("WHISPER_LANGUAGE", "zh"),
            timeout=int(_get_env("ASR_TIMEOUT", "600")),
        )
        print(f"🔊 ASR 后端：openai-whisper 本地（模型：{asr.model_name}）")
        return asr

    from app.services.asr_local import OllamaASRService
    asr = OllamaASRService(
        base_url=args.ollama_url,
        model=args.ollama_model,
        language=args.ollama_language,
        timeout=int(_get_env("ASR_TIMEOUT", "600")),
    )
    print(f"🔊 ASR 后端：Ollama 本地（{asr.base_url}，模型：{asr.model}）")
    if not asr.check_ollama_available():
        print(f"❌ 无法连接到 Ollama 服务（{asr.base_url}），请确认 Ollama 已启动")
        sys.exit(1)
    if not asr.check_model_available():
        print(f"⚠️  Ollama 中未找到模型 '{asr.model}'，请先运行：ollama pull {asr.model}")
        sys.exit(1)
    print(f"   ✅ Ollama 服务正常，模型 '{asr.model}' 已就绪")
    return asr


async def export_videos(
    fetcher,
    videos: list[dict],
    output_dir: Path,
    concurrency: int = 2,
) -> tuple[int, int]:
    """批量并发导出视频到 Markdown 文件"""
    from app.services.processing_status import ProcessingStatusService
    from app.database import get_db_context

    _proc_svc = ProcessingStatusService()
    sem = asyncio.Semaphore(concurrency)
    counter_lock = asyncio.Lock()
    success_count = 0
    failed_count = 0
    total = len(videos)

    async def _process_one(i: int, video_info: dict) -> None:
        nonlocal success_count, failed_count

        video_id = video_info.get("video_id", "")
        title = video_info.get("title", video_id)
        safe_title = _safe_filename(title)
        md_path = output_dir / f"{safe_title}_{video_id}.md"

        # Check DB for completion
        try:
            async with get_db_context() as db:
                proc_rec = await _proc_svc.get_or_create(db, "youtube", video_id, title)
                await db.commit()
                already_done = _proc_svc.is_completed(proc_rec)
        except Exception as _db_err:
            logger.debug(f"DB 状态检查失败: {_db_err}")
            already_done = False

        if already_done and md_path.exists():
            print(f"  [{i:3d}/{total}] ⏭️  已完成，跳过：{title[:50]}")
            async with counter_lock:
                success_count += 1
            return

        print(f"  [{i:3d}/{total}] 🔊 处理中：{title[:50]}", flush=True)

        try:
            async with sem:
                vc = await fetcher.fetch_content(video_info)

            md_content = _build_markdown(vc, vc.content_source)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(md_content, encoding="utf-8")

            status = "ASR ✅" if vc.content_source == "asr" else "仅基本信息 ⚠️"
            print(f"       → {status}：{title[:40]}")

            try:
                async with get_db_context() as db:
                    rec = await _proc_svc.get_or_create(db, "youtube", video_id, title)
                    if getattr(vc, "asr_raw_text", None):
                        await _proc_svc.mark_asr_done(db, rec, vc.asr_raw_text)
                    if vc.content:
                        await _proc_svc.mark_correction_done(db, rec, vc.content)
                    if getattr(vc, "summary_block", None):
                        await _proc_svc.mark_summary_done(db, rec, vc.summary_block)
                    await _proc_svc.mark_completed(db, rec)
                    await db.commit()
            except Exception as _db_err:
                logger.debug(f"DB 状态写入失败（不影响导出）: {_db_err}")

            async with counter_lock:
                success_count += 1

        except Exception as e:
            logger.error(f"处理视频失败 [{video_id}]: {e}")
            print(f"       ❌ 失败: {e}")
            try:
                async with get_db_context() as db:
                    rec = await _proc_svc.get_or_create(db, "youtube", video_id, title)
                    await _proc_svc.mark_failed(db, rec, "asr", str(e))
                    await db.commit()
            except Exception:
                pass
            async with counter_lock:
                failed_count += 1

    await asyncio.gather(*[_process_one(i, v) for i, v in enumerate(videos, 1)])
    return success_count, failed_count


async def main():
    parser = argparse.ArgumentParser(
        description="YouTube ASR 转写 → Markdown 导出工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--url",
        nargs="+",
        metavar="URL",
        help="YouTube 频道/播放列表/视频 URL（可指定多个）",
    )
    parser.add_argument(
        "--liked",
        action="store_true",
        help="导出点赞视频（需要 --cookie-file）",
    )
    parser.add_argument(
        "--watch-later",
        action="store_true",
        help="导出稍后观看（需要 --cookie-file）",
    )
    parser.add_argument(
        "--after-date",
        default=_get_env("YOUTUBE_AFTER_DATE"),
        metavar="YYYY-MM-DD",
        help="只导出该日期之后发布的视频",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(_get_env("YOUTUBE_LIMIT", "0")),
        help="每个来源最多导出 N 个视频（0=不限制）",
    )
    parser.add_argument("--all", action="store_true", help="导出所有视频（不弹出交互选择）")
    parser.add_argument(
        "--cookie-file",
        default=_get_env("YOUTUBE_COOKIE_FILE"),
        help="yt-dlp 使用的 Netscape 格式 Cookie 文件路径",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出目录（默认: {DEFAULT_OUTPUT_DIR}）",
    )
    parser.add_argument(
        "--asr-backend",
        default=_get_env("ASR_BACKEND", "whisper"),
        choices=["auto", "dashscope", "ollama", "whisper"],
        help="ASR 转写后端",
    )
    parser.add_argument("--api-key", default=_get_env("DASHSCOPE_API_KEY"), help="DashScope API Key")
    parser.add_argument(
        "--ollama-url", default=_get_env("OLLAMA_BASE_URL", "http://localhost:11434"), help="Ollama 服务地址"
    )
    parser.add_argument("--ollama-model", default=_get_env("OLLAMA_ASR_MODEL", "whisper"), help="Ollama 模型名")
    parser.add_argument("--ollama-language", default=_get_env("OLLAMA_ASR_LANGUAGE", "zh"), help="转写语言")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=int(_get_env("ASR_CONCURRENCY", "2")),
        help="并发处理数（默认读取 ASR_CONCURRENCY，默认值 2）",
    )
    args = parser.parse_args()

    # ── 解析 URL 来源 ─────────────────────────────────────────────────────
    if args.liked:
        args.url = (args.url or []) + ["https://www.youtube.com/playlist?list=LL"]
    if args.watch_later:
        args.url = (args.url or []) + ["https://www.youtube.com/playlist?list=WL"]

    if not args.url:
        # 尝试从环境变量读取
        env_urls = _get_env("YOUTUBE_SOURCES")
        if env_urls:
            args.url = [u.strip() for u in env_urls.split(",") if u.strip()]
        else:
            # 默认：导出点赞视频
            args.url = ["https://www.youtube.com/playlist?list=LL"]

    # 点赞/稍后观看需要 cookie
    private_lists = {"LL", "WL"}
    needs_cookie = any(
        any(pl in u for pl in [f"list={p}" for p in private_lists])
        for u in args.url
    )
    cookie_text = _get_env("YOUTUBE_COOKIES")
    if needs_cookie and not args.cookie_file and not cookie_text:
        print(
            "❌ 点赞视频/稍后观看是私有播放列表，需要提供 Cookie\n"
            "   方式一：在 .env 中配置 YOUTUBE_COOKIES=<Netscape 格式 Cookie 文本>\n"
            "   方式二：浏览器安装 Get cookies.txt 插件 → 导出 cookies.txt\n"
            "          python scripts/export_youtube_to_md.py --liked --cookie-file /path/to/cookies.txt"
        )
        sys.exit(1)

    # ── 初始化数据库 ─────────────────────────────────────────────────────
    from app.database import init_db
    await init_db()

    # ── 初始化 YouTube 服务 ───────────────────────────────────────────────
    from app.services.youtube import YouTubeService

    cookie_file = args.cookie_file or None
    if cookie_file and not Path(cookie_file).exists():
        print(f"⚠️  Cookie 文件不存在：{cookie_file}，将不使用 Cookie")
        cookie_file = None

    # YOUTUBE_COOKIES 文本优先于文件路径
    yt_service = YouTubeService(cookie_file=cookie_file, cookie_text=cookie_text or None)

    # ── 收集视频列表 ──────────────────────────────────────────────────────
    print(f"\n📥 收集视频列表（共 {len(args.url)} 个来源）...", flush=True)
    all_videos: list[dict] = []
    for url in args.url:
        print(f"   🔗 {url[:80]}", flush=True)
        try:
            # Single video
            if (
                "watch?v=" in url
                or "youtu.be/" in url
                and "list=" not in url
            ):
                info = await yt_service.extract_video_info(url)
                if info:
                    all_videos.append(info)
                    print(f"      → 1 个视频")
                else:
                    print(f"      ⚠️  无法获取视频信息")
            else:
                videos = await yt_service.extract_playlist_videos(url, after_date=args.after_date or None)
                if args.limit > 0:
                    videos = videos[:args.limit]
                all_videos.extend(videos)
                print(f"      → {len(videos)} 个视频")
        except Exception as e:
            print(f"      ❌ 获取失败: {e}")

    total = len(all_videos)
    if total == 0:
        print("⚠️  未找到任何视频")
        sys.exit(0)

    # ── 展示前 20 条标题预览 ──────────────────────────────────────────────
    preview_n = min(20, total)
    print(f"\n📋 前 {preview_n} 个视频标题预览：")
    for idx, v in enumerate(all_videos[:preview_n], 1):
        title = v.get("title", "（无标题）")
        channel = v.get("channel") or v.get("uploader") or ""
        date = (v.get("upload_date") or "")[:10]
        channel_str = f"  [{channel}]" if channel else ""
        date_str = f"  {date}" if date else ""
        print(f"  {idx:>2}.{channel_str} {title}{date_str}")
    if total > preview_n:
        print(f"  ... 共 {total} 个")

    # ── 决定导出数量 ──────────────────────────────────────────────────────
    if args.limit > 0 and total > args.limit:
        all_videos = all_videos[:args.limit]
        print(f"\n📌 限制导出最新 {args.limit} 个（共 {total} 个）")
    elif args.all:
        print(f"\n📌 导出全部 {total} 个视频")
    else:
        print(f"\n📦 共找到 {total} 个视频")
        print("  [1] 最新 20 个\n  [2] 最新 50 个\n  [3] 全部\n  [4] 自定义")
        while True:
            raw = input("请选择 [1/2/3/4]：").strip()
            if raw == "1":
                all_videos = all_videos[:20]
                break
            if raw == "2":
                all_videos = all_videos[:50]
                break
            if raw == "3":
                break
            if raw == "4":
                try:
                    n = int(input(f"请输入数量（1~{total}）：").strip())
                    if 1 <= n <= total:
                        all_videos = all_videos[:n]
                        break
                    print(f"  ⚠️  请输入 1~{total} 之间的数字")
                except ValueError:
                    print("  ⚠️  请输入有效数字")
            else:
                print("  ⚠️  请输入 1、2、3 或 4")
        print(f"📌 将导出 {len(all_videos)} 个视频")

    # ── 构建服务 ──────────────────────────────────────────────────────────
    from app.services.content_storage import ContentStorageManager
    from app.services.youtube_fetcher import YouTubeContentFetcher

    storage_manager = ContentStorageManager(export_root=args.output_dir)
    output_dir = storage_manager.get_export_dir("youtube")
    output_dir.mkdir(parents=True, exist_ok=True)

    asr = await _build_asr_service(args)
    fetcher = YouTubeContentFetcher(asr_service=asr, youtube_service=yt_service, storage_manager=storage_manager)
    print(f"🔀 并发度：{args.concurrency}（可通过 --concurrency 或 ASR_CONCURRENCY 配置）")
    print(f"\n🚀 开始导出 {len(all_videos)} 个视频 → {output_dir.resolve()}\n")

    start_t = time.time()
    s, f = await export_videos(fetcher, all_videos, output_dir, concurrency=args.concurrency)
    elapsed = time.time() - start_t

    print(f"\n✅ 导出完成：成功 {s}，失败 {f}，耗时 {elapsed:.1f}s")
    print(f"📁 输出目录：{output_dir.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
