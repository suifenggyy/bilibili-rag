"""
Instapaper 书签 → Markdown 导出工具

从 Instapaper 获取收藏书签，优先用 requests + trafilatura 提取正文，
失败时回退到 Playwright 渲染后再提取，保存为 Markdown 文件。
免费账户可用，无需 Instapaper Premium 订阅。

【前提条件】
1. 申请 Instapaper API Key：
   https://www.instapaper.com/main/request_oauth_consumer_token
   填写 Application Name 和说明，通常 1-3 天审核通过

2. 安装依赖：
   pip install trafilatura playwright
   playwright install chromium

【文件夹说明】
  unread   稍后阅读（默认收件箱）
  starred  星标收藏
  archive  已归档
  <数字ID> 自定义文件夹（通过 --list-folders 查看）

用法:
    python scripts/export_instapaper_to_md.py \\
        --email user@example.com --password xxx \\
        --consumer-key KEY --consumer-secret SECRET

    # 指定文件夹
    python scripts/export_instapaper_to_md.py --folders starred archive

    # 查看所有文件夹
    python scripts/export_instapaper_to_md.py --list-folders

    # 限制每个文件夹导出数量
    python scripts/export_instapaper_to_md.py --limit 50

    # 指定输出目录
    python scripts/export_instapaper_to_md.py --output-dir ~/instapaper-notes

    # 凭据来自环境变量（配置 .env 后无需命令行参数）
    python scripts/export_instapaper_to_md.py
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

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
from loguru import logger
from app.services.processing_status import ProcessingStatusService
from app.database import get_db_context

load_dotenv(ROOT_DIR / ".env")

SESSION_CACHE_FILE = ROOT_DIR / ".instapaper_session.json"

# 内置文件夹
BUILTIN_FOLDERS = {
    "unread": "稍后阅读",
    "starred": "星标收藏",
    "archive": "已归档",
}


def _get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


DEFAULT_COLLECTION_OUTPUT_DIR = (
    "/Users/gongyongyue/FangcloudV2/personal_space.localized/同步空间/个人资料/Obsidian/jarvis/collection"
)


def _safe_filename(name: str, max_len: int = 80) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:max_len] if len(name) > max_len else name


# ==================== Session 缓存 ====================

def load_cached_session() -> dict | None:
    if not SESSION_CACHE_FILE.exists():
        return None
    try:
        data = json.loads(SESSION_CACHE_FILE.read_text(encoding="utf-8"))
        # Instapaper token 长期有效，保守取 90 天
        if time.time() - data.get("saved_at", 0) > 90 * 24 * 3600:
            return None
        return data
    except Exception:
        return None


def save_session(access_token: str, access_secret: str) -> None:
    data = {
        "access_token": access_token,
        "access_secret": access_secret,
        "saved_at": time.time(),
    }
    SESSION_CACHE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.debug(f"Session 已缓存到 {SESSION_CACHE_FILE}")


def clear_session_cache() -> None:
    if SESSION_CACHE_FILE.exists():
        SESSION_CACHE_FILE.unlink()


# ==================== 登录入口 ====================

async def ensure_logged_in(
    consumer_key: str,
    consumer_secret: str,
    email: str,
    password: str,
    relogin: bool = False,
):
    """
    确保已登录，返回 InstapaperService 实例。
    优先复用缓存 token，失效时重新 xAuth 登录。
    """
    from app.services.instapaper import InstapaperService

    svc = InstapaperService(consumer_key, consumer_secret)

    if not relogin:
        cached = load_cached_session()
        if cached:
            svc.set_tokens(cached["access_token"], cached["access_secret"])
            try:
                user = await svc.verify_credentials()
                uname = user.get("username") or user.get("email") or "未知用户"
                print(f"🔐 已复用缓存登录：{uname}（加 --relogin 重新登录）")
                return svc
            except Exception:
                logger.debug("缓存 token 失效，重新登录")
                clear_session_cache()

    print("🔑 正在登录 Instapaper...", end="", flush=True)
    try:
        tokens = await svc.login(email, password)
        print(" ✅")
        save_session(tokens["access_token"], tokens["access_secret"])
        try:
            user = await svc.verify_credentials()
            uname = user.get("username") or user.get("email") or email
            print(f"✅ 登录成功：{uname}")
        except Exception:
            pass
    except Exception as e:
        print(f"\n❌ 登录失败：{e}")
        sys.exit(1)

    return svc


# ==================== 导出核心 ====================

async def _export_single_bookmark(
    svc,
    fetcher,
    bookmark: dict,
    folder_title: str,
    output_dir: Path,
    idx: int = 0,
    total: int = 0,
) -> tuple[int, int]:
    """处理单条书签，返回 (success, failed)。"""
    from app.services.article_fetcher import ArticleFetcher

    bm_id = str(bookmark.get("bookmark_id", ""))
    title = bookmark.get("title") or bookmark.get("url", "未知标题")
    url = bookmark.get("url", "")
    safe_title = _safe_filename(title)
    md_path = output_dir / f"{safe_title}_{bm_id}.md"
    _proc_svc = ProcessingStatusService()
    pos = f"[{idx:3d}/{total}] " if idx and total else ""

    already_done = False
    try:
        async with get_db_context() as db:
            proc_rec = await _proc_svc.get_or_create(db, "instapaper", bm_id, title)
            await db.commit()
            already_done = _proc_svc.is_completed(proc_rec)
    except Exception as _db_err:
        logger.debug(f"DB 状态检查失败（跳过）: {_db_err}")

    if already_done and md_path.exists():
        print(f"   {pos}⏭️  已完成，跳过：{title[:50]}")
        return 1, 0

    if md_path.exists() and not already_done:
        print(f"   {pos}⏭️  已存在，跳过：{title[:50]}")
        return 1, 0

    print(f"   {pos}🔄 {title[:55]}", end="", flush=True)

    try:
        content = await fetcher.fetch_content(url, title)
        md_text = ArticleFetcher.build_markdown(bookmark, content)
        md_path.write_text(md_text, encoding="utf-8")

        source_label = "✅ trafilatura" if content["source"] == "trafilatura" else "⚠️ 仅基本信息"
        print(f"  → {source_label}")

        try:
            async with get_db_context() as db:
                rec = await _proc_svc.get_or_create(db, "instapaper", bm_id, title)
                corrected = content.get("content") or content.get("text") or ""
                if corrected:
                    await _proc_svc.mark_correction_done(db, rec, corrected)
                await _proc_svc.mark_completed(db, rec)
                await db.commit()
        except Exception as _db_err:
            logger.debug(f"DB 状态写入失败（不影响导出）: {_db_err}")

        await asyncio.sleep(0.2)
        return 1, 0

    except Exception as e:
        logger.error(f"处理失败 [{bm_id}]: {e}")
        print(f"  → ❌ {e}")
        try:
            async with get_db_context() as db:
                rec = await _proc_svc.get_or_create(db, "instapaper", bm_id, title)
                await _proc_svc.mark_failed(db, rec, "correction", str(e))
                await db.commit()
        except Exception as _db_err:
            logger.debug(f"DB 失败状态写入失败（不影响导出）: {_db_err}")
        await asyncio.sleep(0.2)
        return 0, 1


async def export_folder(
    svc,
    fetcher,
    folder_id: str,
    folder_title: str,
    output_dir: Path,
    limit: int = 0,
) -> tuple[int, int]:
    """导出单个文件夹，返回 (成功数, 失败数)"""
    from app.services.article_fetcher import ArticleFetcher

    print(f"\n📁 文件夹：{folder_title}")
    print(f"   获取书签中...", end="", flush=True)

    try:
        bookmarks = await svc.get_all_bookmarks(folder_id)
    except Exception as e:
        print(f"\n   ❌ 获取失败: {e}")
        return 0, 0

    if limit > 0:
        bookmarks = bookmarks[:limit]

    total = len(bookmarks)
    print(f" ✅  {total} 篇")

    if total == 0:
        print("   ⚠️  文件夹为空，跳过")
        return 0, 0

    print(f"   输出目录：{output_dir}")

    success, failed = 0, 0
    _proc_svc = ProcessingStatusService()

    for i, bookmark in enumerate(bookmarks, 1):
        bm_id = str(bookmark.get("bookmark_id", ""))
        title = bookmark.get("title") or bookmark.get("url", "未知标题")
        url = bookmark.get("url", "")

        safe_title = _safe_filename(title)
        md_path = output_dir / f"{safe_title}_{bm_id}.md"

        # 检查 DB 状态
        already_done = False
        try:
            async with get_db_context() as db:
                proc_rec = await _proc_svc.get_or_create(db, "instapaper", bm_id, title)
                await db.commit()
                already_done = _proc_svc.is_completed(proc_rec)
        except Exception as _db_err:
            logger.debug(f"DB 状态检查失败（跳过）: {_db_err}")

        if already_done and md_path.exists():
            print(f"   [{i:3d}/{total}] ⏭️  已完成，跳过：{title[:50]}")
            success += 1
            continue

        if md_path.exists() and not already_done:
            print(f"   [{i:3d}/{total}] ⏭️  已存在，跳过：{title[:50]}")
            success += 1
            continue

        print(f"   [{i:3d}/{total}] 🔄 {title[:55]}", end="", flush=True)

        try:
            content = await fetcher.fetch_content(url, title)
            md_text = ArticleFetcher.build_markdown(bookmark, content)
            md_path.write_text(md_text, encoding="utf-8")

            source_label = "✅ trafilatura" if content["source"] == "trafilatura" else "⚠️ 仅基本信息"
            print(f"  → {source_label}")
            success += 1

            try:
                async with get_db_context() as db:
                    rec = await _proc_svc.get_or_create(db, "instapaper", bm_id, title)
                    corrected = content.get("content") or content.get("text") or ""
                    if corrected:
                        await _proc_svc.mark_correction_done(db, rec, corrected)
                    await _proc_svc.mark_completed(db, rec)
                    await db.commit()
            except Exception as _db_err:
                logger.debug(f"DB 状态写入失败（不影响导出）: {_db_err}")

        except Exception as e:
            logger.error(f"处理失败 [{bm_id}]: {e}")
            print(f"  → ❌ {e}")
            failed += 1
            try:
                async with get_db_context() as db:
                    rec = await _proc_svc.get_or_create(db, "instapaper", bm_id, title)
                    await _proc_svc.mark_failed(db, rec, "correction", str(e))
                    await db.commit()
            except Exception as _db_err:
                logger.debug(f"DB 失败状态写入失败（不影响导出）: {_db_err}")

        await asyncio.sleep(0.2)

    return success, failed


async def list_folders(svc) -> None:
    """列出所有可用文件夹"""
    print("\n📚 可用文件夹：")
    print("-" * 50)
    for fid, title in BUILTIN_FOLDERS.items():
        print(f"  {fid:<12}  {title}")
    try:
        custom = await svc.get_folders()
        for f in custom:
            print(f"  {f['folder_id']:<12}  {f['title']}")
    except Exception as e:
        print(f"  ⚠️  获取自定义文件夹失败: {e}")
    print("-" * 50)


async def main():
    parser = argparse.ArgumentParser(
        description="Instapaper 书签正文提取 → Markdown 导出工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # 认证参数
    parser.add_argument(
        "--consumer-key",
        default=_get_env("INSTAPAPER_CONSUMER_KEY"),
        help="Instapaper API Consumer Key（或设置 INSTAPAPER_CONSUMER_KEY）",
    )
    parser.add_argument(
        "--consumer-secret",
        default=_get_env("INSTAPAPER_CONSUMER_SECRET"),
        help="Instapaper API Consumer Secret",
    )
    parser.add_argument(
        "--email",
        default=_get_env("INSTAPAPER_EMAIL"),
        help="Instapaper 登录邮箱（或设置 INSTAPAPER_EMAIL）",
    )
    parser.add_argument(
        "--password",
        default=_get_env("INSTAPAPER_PASSWORD"),
        help="Instapaper 登录密码（或设置 INSTAPAPER_PASSWORD）",
    )
    parser.add_argument(
        "--relogin",
        action="store_true",
        help="强制重新登录（忽略 token 缓存）",
    )
    # 文件夹
    parser.add_argument(
        "--folders",
        nargs="+",
        default=["unread"],
        help="要导出的文件夹 ID（默认: unread）。可多选：--folders unread starred archive",
    )
    parser.add_argument(
        "--list-folders",
        action="store_true",
        help="列出所有可用文件夹后退出",
    )
    # 导出控制
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="每个文件夹最多导出条数（0=不限制，触发交互选择）",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="导出所有书签（不弹出交互选择）",
    )
    parser.add_argument(
        "--output-dir",
        default=_get_env("COLLECTION_OUTPUT_DIR", DEFAULT_COLLECTION_OUTPUT_DIR),
        help="输出目录（默认读取 COLLECTION_OUTPUT_DIR）",
    )
    args = parser.parse_args()

    # ── 参数校验 ─────────────────────────────────────────────────────
    if not args.consumer_key or not args.consumer_secret:
        print(
            "❌ 未提供 Instapaper API Key！\n\n"
            "   申请地址：https://www.instapaper.com/main/request_oauth_consumer_token\n"
            "   申请后配置到 .env 文件：\n"
            "     INSTAPAPER_CONSUMER_KEY=your_key\n"
            "     INSTAPAPER_CONSUMER_SECRET=your_secret"
        )
        sys.exit(1)

    if not args.email or not args.password:
        print(
            "❌ 未提供登录凭据！\n"
            "   请通过 --email / --password 参数或 .env 配置。"
        )
        sys.exit(1)

    from app.services.content_storage import ContentStorageManager
    from app.services.article_fetcher import ArticleFetcher
    from app.database import init_db
    await init_db()

    storage_manager = ContentStorageManager(export_root=args.output_dir)
    output_dir = storage_manager.get_export_dir("instapaper")

    # ── 登录 ──────────────────────────────────────────────────────────
    svc = await ensure_logged_in(
        args.consumer_key, args.consumer_secret,
        args.email, args.password,
        relogin=args.relogin,
    )

    # ── 列出文件夹模式 ────────────────────────────────────────────────
    if args.list_folders:
        await list_folders(svc)
        await svc.close()
        sys.exit(0)

    # ── 初始化提取器 ──────────────────────────────────────────────────
    fetcher = ArticleFetcher(storage_manager=storage_manager)
    if not fetcher._trafilatura_available:
        print("⚠️  trafilatura 未安装，正文将无法提取，仅保存标题和 URL")
        print("   安装命令：pip install trafilatura")

    # ── 获取文件夹名称映射 ────────────────────────────────────────────
    folder_name_map = dict(BUILTIN_FOLDERS)
    try:
        custom_folders = await svc.get_folders()
        folder_name_map.update({f["folder_id"]: f["title"] for f in custom_folders})
    except Exception:
        pass

    # ── 收集所有书签（预览阶段，先抓 front 20） ───────────────────────
    print(f"\n📥 获取书签列表（共 {len(args.folders)} 个文件夹）...", flush=True)
    all_bookmarks: list[tuple[dict, str]] = []  # (bookmark, folder_title)

    try:
        for folder_id in args.folders:
            folder_title = folder_name_map.get(folder_id, folder_id)
            print(f"   📁 {folder_title}...", end="", flush=True)
            try:
                bms = await svc.get_all_bookmarks(folder_id)
                print(f" ✅ {len(bms)} 篇")
                for bm in bms:
                    all_bookmarks.append((bm, folder_title))
            except Exception as e:
                print(f" ❌ 失败: {e}")
    finally:
        pass  # svc closed later

    total = len(all_bookmarks)
    if total == 0:
        print("⚠️  未找到任何书签")
        await svc.close()
        sys.exit(0)

    # ── 展示前 20 条标题预览 ──────────────────────────────────────────
    preview_n = min(20, total)
    print(f"\n📋 前 {preview_n} 条标题预览：")
    for idx, (bm, ft) in enumerate(all_bookmarks[:preview_n], 1):
        title = bm.get("title") or bm.get("url", "（无标题）")
        print(f"  {idx:>2}. [{ft}] {title}")
    if total > preview_n:
        print(f"  ... 共 {total} 篇")

    # ── 决定导出数量 ──────────────────────────────────────────────────
    if args.limit > 0:
        all_bookmarks = all_bookmarks[:args.limit]
        print(f"\n📌 限制导出最新 {args.limit} 篇（共 {total} 篇）")
    elif args.all:
        print(f"\n📌 导出全部 {total} 篇")
    else:
        print(f"\n📦 共找到 {total} 篇书签")
        print("  [1] 最新 20 篇\n  [2] 最新 50 篇\n  [3] 全部\n  [4] 自定义")
        while True:
            raw = input("请选择 [1/2/3/4]：").strip()
            if raw == "1":
                all_bookmarks = all_bookmarks[:20]
                break
            if raw == "2":
                all_bookmarks = all_bookmarks[:50]
                break
            if raw == "3":
                break
            if raw == "4":
                try:
                    n = int(input(f"请输入数量（1~{total}）：").strip())
                    if 1 <= n <= total:
                        all_bookmarks = all_bookmarks[:n]
                        break
                    print(f"  ⚠️  请输入 1~{total} 之间的数字")
                except ValueError:
                    print("  ⚠️  请输入有效数字")
            else:
                print("  ⚠️  请输入 1、2、3 或 4")

    export_total = len(all_bookmarks)
    print(f"\n🚀 开始导出 {export_total} 篇 → {output_dir.resolve()}")

    # ── 开始导出 ──────────────────────────────────────────────────────
    total_success, total_failed = 0, 0

    try:
        for i, (bm, folder_title) in enumerate(all_bookmarks, 1):
            s, f = await _export_single_bookmark(
                svc, fetcher, bm, folder_title, output_dir, idx=i, total=export_total
            )
            total_success += s
            total_failed += f

        print(f"\n{'='*60}")
        print(f"✅ 导出完成！成功：{total_success} 篇，失败：{total_failed} 篇")
        print(f"📂 文件保存在：{output_dir.resolve()}")
    finally:
        await svc.close()


if __name__ == "__main__":
    asyncio.run(main())
