"""
小宇宙播客 → Markdown 导出工具

从小宇宙收藏夹/收件箱/订阅中获取单集，优先使用官方字幕，无字幕时通过 ASR 转写，
保存为 Markdown 文件。不依赖 RAG 或向量数据库，独立运行。

【认证方式】
方式一：通过 .env 配置 Token（推荐，无需重复登录）：
    XIAOYUZHOU_ACCESS_TOKEN=<your_access_token>
    XIAOYUZHOU_REFRESH_TOKEN=<your_refresh_token>

方式二：交互式短信登录：
    python scripts/export_xiaoyuzhou_to_md.py --login

Token 登录成功后会自动保存到 .xiaoyuzhou_session.json，下次无需重新登录。

【内容来源】
默认：收藏夹（已登录时）
    python scripts/export_xiaoyuzhou_to_md.py

收件箱（所有订阅的最新更新）：
    python scripts/export_xiaoyuzhou_to_md.py --inbox

RSS URL（无需登录）：
    python scripts/export_xiaoyuzhou_to_md.py --rss https://feeds.xiaoyuzhoufm.com/podcast/xxx

用法:
    # 导出收藏夹（默认）
    python scripts/export_xiaoyuzhou_to_md.py

    # 导出收件箱
    python scripts/export_xiaoyuzhou_to_md.py --inbox

    # 指定 RSS URL
    python scripts/export_xiaoyuzhou_to_md.py --rss https://feeds.xiaoyuzhoufm.com/podcast/xxx

    # 最多导出 N 集
    python scripts/export_xiaoyuzhou_to_md.py --limit 10

    # 导出所有集（不弹出交互选择）
    python scripts/export_xiaoyuzhou_to_md.py --all

    # 指定 ASR 后端
    python scripts/export_xiaoyuzhou_to_md.py --asr-backend dashscope

    # 并发度（默认读取 ASR_CONCURRENCY）
    python scripts/export_xiaoyuzhou_to_md.py --concurrency 3
"""

import argparse
import asyncio
import json
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


SESSION_FILE = ROOT_DIR / ".xiaoyuzhou_session.json"

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


def _build_markdown(ec, source: str) -> str:
    """构建 Markdown 文件内容"""
    source_label = {
        "asr": "ASR 语音转写",
        "basic_info": "播客基本信息（ASR 未成功）",
    }.get(source, source)

    lines = [
        f"# {ec.title}",
        "",
        "## 节目信息",
        "",
        "| 字段 | 内容 |",
        "|------|------|",
        f"| 单集ID | {ec.episode_id} |",
        f"| 播客 | {ec.podcast_title} |",
        f"| 时长 | {_format_duration(ec.duration)} |",
        f"| 内容来源 | {source_label} |",
    ]

    if ec.cover_url:
        lines += ["", f"![封面]({ec.cover_url})"]

    if ec.description:
        lines += ["", "## 节目简介", "", ec.description[:500]]

    from app.services.content_summary import append_summary_section
    append_summary_section(lines, getattr(ec, "summary_block", ""))

    lines += ["", "---", "", "## 转写内容", ""]
    if ec.content and ec.content.strip():
        lines.append(ec.content.strip())
    else:
        lines.append("_（未获取到有效内容）_")

    lines += [
        "",
        "---",
        "",
        f"_导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
    ]
    return "\n".join(lines)


def _load_session() -> dict:
    """从文件或环境变量加载 Token"""
    access = _get_env("XIAOYUZHOU_ACCESS_TOKEN")
    refresh = _get_env("XIAOYUZHOU_REFRESH_TOKEN")
    if access:
        return {"access_token": access, "refresh_token": refresh}

    if SESSION_FILE.exists():
        try:
            data = json.loads(SESSION_FILE.read_text())
            if data.get("access_token"):
                return data
        except Exception:
            pass
    return {}


def _save_session(access_token: str, refresh_token: str, phone: str = "") -> None:
    SESSION_FILE.write_text(
        json.dumps(
            {"access_token": access_token, "refresh_token": refresh_token, "phone": phone},
            ensure_ascii=False,
            indent=2,
        )
    )


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


async def interactive_login() -> Optional[dict]:
    """交互式短信登录，返回 {access_token, refresh_token}"""
    from app.services.xiaoyuzhou import XiaoyuzhouService
    xyz = XiaoyuzhouService()

    phone = input("📱 请输入手机号（+86）：").strip()
    if not phone:
        print("❌ 手机号不能为空")
        return None

    print("📨 发送验证码...", end="", flush=True)
    ok = await xyz.send_sms_code(phone)
    if not ok:
        print(" ❌ 发送失败")
        return None
    print(" ✅")

    code = input("🔢 请输入短信验证码：").strip()
    if not code:
        print("❌ 验证码不能为空")
        return None

    print("🔐 登录中...", end="", flush=True)
    result = await xyz.login_with_sms(phone, code)
    if not result or not result.get("access_token"):
        print(" ❌ 登录失败，请检查验证码")
        return None
    print(f" ✅ 登录成功，欢迎 {result.get('nickname', '用户')}！")

    _save_session(result["access_token"], result.get("refresh_token", ""), phone)
    print(f"💾 Token 已保存到 {SESSION_FILE}")
    return result


async def export_episodes(
    fetcher,
    episodes: list,  # list of (episode_dict, podcast_title)
    output_dir: Path,
    concurrency: int = 2,
) -> tuple[int, int]:
    """批量并发导出单集到 Markdown 文件"""
    from app.services.processing_status import ProcessingStatusService
    from app.database import get_db_context

    _proc_svc = ProcessingStatusService()
    sem = asyncio.Semaphore(concurrency)
    counter_lock = asyncio.Lock()
    success_count = 0
    failed_count = 0
    total = len(episodes)

    async def _process_one(i: int, ep: dict, podcast_title: str) -> None:
        nonlocal success_count, failed_count

        episode_id = ep.get("episode_id", "")
        title = ep.get("title", episode_id)
        full_title = f"[{podcast_title}] {title}" if podcast_title else title
        safe_title = _safe_filename(full_title)
        md_path = output_dir / f"{safe_title}_{episode_id}.md"

        # Check DB for completion
        try:
            async with get_db_context() as db:
                proc_rec = await _proc_svc.get_or_create(db, "xiaoyuzhou", episode_id, full_title)
                await db.commit()
                already_done = _proc_svc.is_completed(proc_rec)
        except Exception as _db_err:
            logger.debug(f"DB 状态检查失败: {_db_err}")
            already_done = False

        if already_done and md_path.exists():
            print(f"  [{i:3d}/{total}] ⏭️  已完成，跳过：{full_title[:50]}")
            async with counter_lock:
                success_count += 1
            return

        print(f"  [{i:3d}/{total}] 🔊 处理中：{full_title[:50]}", flush=True)

        try:
            async with sem:
                ec = await fetcher.fetch_content(ep, podcast_title=podcast_title)

            md_content = _build_markdown(ec, ec.content_source)
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(md_content, encoding="utf-8")

            status = "ASR ✅" if ec.content_source == "asr" else "仅基本信息 ⚠️"
            print(f"       → {status}：{full_title[:40]}")

            try:
                async with get_db_context() as db:
                    rec = await _proc_svc.get_or_create(db, "xiaoyuzhou", episode_id, full_title)
                    if getattr(ec, "asr_raw_text", None):
                        await _proc_svc.mark_asr_done(db, rec, ec.asr_raw_text)
                    if ec.content:
                        await _proc_svc.mark_correction_done(db, rec, ec.content)
                    if getattr(ec, "summary_block", None):
                        await _proc_svc.mark_summary_done(db, rec, ec.summary_block)
                    await _proc_svc.mark_completed(db, rec)
                    await db.commit()
            except Exception as _db_err:
                logger.debug(f"DB 状态写入失败（不影响导出）: {_db_err}")

            async with counter_lock:
                success_count += 1

        except Exception as e:
            logger.error(f"处理单集失败 [{episode_id}]: {e}")
            print(f"       ❌ 失败: {e}")
            try:
                async with get_db_context() as db:
                    rec = await _proc_svc.get_or_create(db, "xiaoyuzhou", episode_id, full_title)
                    await _proc_svc.mark_failed(db, rec, "asr", str(e))
                    await db.commit()
            except Exception:
                pass
            async with counter_lock:
                failed_count += 1

    await asyncio.gather(*[_process_one(i, ep, pt) for i, (ep, pt) in enumerate(episodes, 1)])
    return success_count, failed_count


async def main():
    parser = argparse.ArgumentParser(
        description="小宇宙播客 ASR 转写 → Markdown 导出工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--login", action="store_true", help="重新进行短信登录")
    parser.add_argument(
        "--access-token",
        default=_get_env("XIAOYUZHOU_ACCESS_TOKEN"),
        help="小宇宙 access token（优先级高于 session 文件）",
    )
    parser.add_argument(
        "--refresh-token",
        default=_get_env("XIAOYUZHOU_REFRESH_TOKEN"),
        help="小宇宙 refresh token",
    )
    parser.add_argument(
        "--rss",
        nargs="+",
        metavar="URL",
        help="手动指定一个或多个播客 RSS URL（不用登录）",
    )
    parser.add_argument(
        "--favorites",
        action="store_true",
        default=True,
        help="导出收藏夹内容（默认）",
    )
    parser.add_argument(
        "--inbox",
        action="store_true",
        help="导出收件箱（所有订阅的最新更新）",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"输出目录（默认: {DEFAULT_OUTPUT_DIR}）",
    )
    parser.add_argument("--all", action="store_true", help="导出所有集（不弹出交互选择）")
    parser.add_argument("--limit", type=int, default=0, help="每个播客最多导出最新 N 集（0=不限制）")
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

    # ── 初始化数据库 ─────────────────────────────────────────────────────
    from app.database import init_db
    await init_db()

    # ── 认证 ─────────────────────────────────────────────────────────────
    from app.services.xiaoyuzhou import XiaoyuzhouService

    if args.rss:
        # RSS 模式无需认证
        xyz = XiaoyuzhouService()
        subscriptions = [
            {"podcast_id": f"rss_{i}", "title": f"RSS播客{i+1}", "rss_url": url}
            for i, url in enumerate(args.rss)
        ]
        print(f"📡 RSS 模式，共 {len(subscriptions)} 个播客源")
    else:
        # Token 认证模式
        if args.login:
            result = await interactive_login()
            if not result:
                sys.exit(1)
            args.access_token = result["access_token"]
            args.refresh_token = result.get("refresh_token", "")

        # 尝试从 session 文件加载
        if not args.access_token:
            session = _load_session()
            if session.get("access_token"):
                args.access_token = session["access_token"]
                args.refresh_token = session.get("refresh_token", "")
                print("🔐 使用已保存的 Token 登录")

        if not args.access_token:
            print(
                "❌ 未提供小宇宙 Token！\n"
                "   请通过以下方式之一认证：\n"
                "   1. 在 .env 中设置 XIAOYUZHOU_ACCESS_TOKEN\n"
                "   2. 运行 python scripts/export_xiaoyuzhou_to_md.py --login\n"
                "   3. 传入 --access-token <token>\n"
                "   4. 使用 --rss <url> 直接指定 RSS URL（无需登录）"
            )
            sys.exit(1)

        xyz = XiaoyuzhouService(access_token=args.access_token, refresh_token=args.refresh_token)

        # 仅 inbox/RSS 模式需要获取订阅列表
        if args.inbox:
            print("📋 获取订阅播客列表...", end="", flush=True)
            try:
                subscriptions = await xyz.get_subscriptions(limit=200)
                print(f" ✅ 共 {len(subscriptions)} 个订阅")
            except Exception as e:
                print(f" ❌ 失败：{e}")
                sys.exit(1)
            if not subscriptions:
                print("⚠️  订阅列表为空，请通过 --rss 手动指定 RSS URL")
                sys.exit(0)

    # ── 收集单集 ─────────────────────────────────────────────────────────
    print("\n📥 获取单集列表...", flush=True)
    all_episodes: list[tuple[dict, str]] = []

    if xyz.access_token and not args.rss and not args.inbox:
        # ── 收藏夹模式（默认）──
        try:
            fetch_limit = args.limit if args.limit > 0 else 100
            fav_result = await xyz.get_favorites(limit=fetch_limit)
            fav_eps = fav_result.get("episodes", [])
            while fav_result.get("load_more_key") and (args.limit <= 0 or len(fav_eps) < args.limit):
                fav_result = await xyz.get_favorites(
                    limit=fetch_limit, load_more_key=fav_result["load_more_key"]
                )
                fav_eps.extend(fav_result.get("episodes", []))
            print(f"   ⭐ 收藏夹共 {len(fav_eps)} 集")
            for ep in fav_eps:
                podcast_title = ep.pop("podcast_title", "") or "未知播客"
                all_episodes.append((ep, podcast_title))
        except Exception as e:
            print(f"   ❌ 收藏夹获取失败: {e}")
            sys.exit(1)

    elif xyz.access_token and args.inbox and subscriptions and not any(s["podcast_id"].startswith("rss_") for s in subscriptions):
        # ── 已登录 → 优先用收件箱合流接口 ──
        try:
            fetch_limit = args.limit if args.limit > 0 else 100
            inbox_result = await xyz.get_inbox_list(limit=fetch_limit)
            inbox_eps = inbox_result.get("episodes", [])
            while inbox_result.get("load_more_key") and (args.limit <= 0 or len(inbox_eps) < args.limit):
                inbox_result = await xyz.get_inbox_list(
                    limit=fetch_limit, load_more_key=inbox_result["load_more_key"]
                )
                inbox_eps.extend(inbox_result.get("episodes", []))
            print(f"   收件箱共 {len(inbox_eps)} 集（来自所有订阅）")
            for ep in inbox_eps:
                podcast_title = ep.pop("podcast_title", "") or "未知播客"
                all_episodes.append((ep, podcast_title))
        except Exception as e:
            logger.warning(f"[Xiaoyuzhou] 收件箱获取失败，改用逐播客模式: {e}")

        if not all_episodes:
            print("   收件箱为空，改用逐播客获取...")
            for sub in subscriptions:
                podcast_id = sub.get("podcast_id", "")
                podcast_title = sub.get("title", podcast_id)
                try:
                    result = await xyz.get_episodes_by_api(podcast_id, limit=args.limit or 50)
                    episodes = result.get("episodes", [])
                    print(f"   「{podcast_title}」: {len(episodes)} 集")
                    for ep in episodes:
                        all_episodes.append((ep, podcast_title))
                except Exception as e:
                    print(f"   ⚠️  「{podcast_title}」获取失败: {e}")
    else:
        # ── RSS-only 模式 ──
        for sub in subscriptions:
            rss_url = sub.get("rss_url") or ""
            podcast_title = sub.get("title", sub.get("podcast_id", ""))
            if not rss_url:
                continue
            try:
                episodes = await xyz.get_episodes_from_rss(rss_url, limit=args.limit or 0)
                print(f"   「{podcast_title}」: {len(episodes)} 集")
                for ep in episodes:
                    all_episodes.append((ep, podcast_title))
            except Exception as e:
                print(f"   ⚠️  「{podcast_title}」获取失败: {e}")

    total = len(all_episodes)
    if total == 0:
        print("⚠️  未找到任何单集")
        sys.exit(0)

    # ── 展示前 20 条标题 ──────────────────────────────────────────────────
    preview_n = min(20, total)
    print(f"\n📋 前 {preview_n} 集标题预览：")
    for idx, (ep, pt) in enumerate(all_episodes[:preview_n], 1):
        title = ep.get("title", "（无标题）")
        pub = ep.get("pub_date", "")[:10]
        date_str = f"  {pub}" if pub else ""
        print(f"  {idx:>2}. [{pt}] {title}{date_str}")
    if total > preview_n:
        print(f"  ... 共 {total} 集")

    # ── 决定导出数量 ──────────────────────────────────────────────────────
    if args.limit > 0:
        all_episodes = all_episodes[:args.limit]
        print(f"\n📌 限制导出最新 {args.limit} 集（共 {total} 集）")
    elif args.all:
        print(f"\n📌 导出全部 {total} 集")
    else:
        print(f"\n📦 共找到 {total} 集")
        print("  [1] 最新 20 集\n  [2] 最新 50 集\n  [3] 全部\n  [4] 自定义")
        while True:
            raw = input("请选择 [1/2/3/4]：").strip()
            if raw == "1":
                all_episodes = all_episodes[:20]
                break
            if raw == "2":
                all_episodes = all_episodes[:50]
                break
            if raw == "3":
                break
            if raw == "4":
                try:
                    n = int(input(f"请输入数量（1~{total}）：").strip())
                    if 1 <= n <= total:
                        all_episodes = all_episodes[:n]
                        break
                    print(f"  ⚠️  请输入 1~{total} 之间的数字")
                except ValueError:
                    print("  ⚠️  请输入有效数字")
            else:
                print("  ⚠️  请输入 1、2、3 或 4")
        print(f"📌 将导出 {len(all_episodes)} 集")

    # ── 构建服务 ──────────────────────────────────────────────────────────
    from app.services.content_storage import ContentStorageManager
    from app.services.xiaoyuzhou_fetcher import XiaoyuzhouContentFetcher

    storage_manager = ContentStorageManager(export_root=args.output_dir)
    output_dir = storage_manager.get_export_dir("xiaoyuzhou")
    output_dir.mkdir(parents=True, exist_ok=True)

    asr = await _build_asr_service(args)
    fetcher = XiaoyuzhouContentFetcher(
        asr_service=asr,
        storage_manager=storage_manager,
        xyz_service=xyz if xyz.access_token else None,  # 有 token 时传入，优先用官方字幕
    )
    print(f"🔀 并发度：{args.concurrency}（可通过 --concurrency 或 ASR_CONCURRENCY 配置）")
    print(f"\n🚀 开始导出 {len(all_episodes)} 集 → {output_dir.resolve()}\n")

    start_t = time.time()
    s, f = await export_episodes(fetcher, all_episodes, output_dir, concurrency=args.concurrency)
    elapsed = time.time() - start_t

    print(f"\n✅ 导出完成：成功 {s}，失败 {f}，耗时 {elapsed:.1f}s")
    print(f"📁 输出目录：{output_dir.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
