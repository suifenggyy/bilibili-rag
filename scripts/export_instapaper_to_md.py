"""
Instapaper 书签 → Markdown 导出工具

从 Instapaper 获取收藏书签，提取文章正文（trafilatura），保存为 Markdown 文件。
免费账户可用，无需 Instapaper Premium 订阅。

【前提条件】
1. 申请 Instapaper API Key：
   https://www.instapaper.com/main/request_oauth_consumer_token
   填写 Application Name 和说明，通常 1-3 天审核通过

2. 安装依赖：
   pip install trafilatura pyinstapaper

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

    folder_dir = output_dir / _safe_filename(folder_title)
    folder_dir.mkdir(parents=True, exist_ok=True)
    print(f"   输出目录：{folder_dir}")

    success, failed = 0, 0

    for i, bookmark in enumerate(bookmarks, 1):
        bm_id = str(bookmark.get("bookmark_id", ""))
        title = bookmark.get("title") or bookmark.get("url", "未知标题")
        url = bookmark.get("url", "")

        safe_title = _safe_filename(title)
        md_path = folder_dir / f"{safe_title}_{bm_id}.md"

        if md_path.exists():
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
        except Exception as e:
            logger.error(f"处理失败 [{bm_id}]: {e}")
            print(f"  → ❌ {e}")
            failed += 1

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
        default=["starred"],
        help="要导出的文件夹 ID（默认: starred）。可多选：--folders unread starred archive",
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
        help="每个文件夹最多导出条数（0=全部）",
    )
    parser.add_argument(
        "--output-dir",
        default=_get_env("INSTAPAPER_OUTPUT_DIR", "instapaper_output"),
        help="输出目录（默认: ./instapaper_output）",
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

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    from app.services.article_fetcher import ArticleFetcher

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
    fetcher = ArticleFetcher()
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

    # ── 开始导出 ──────────────────────────────────────────────────────
    print(f"\n🚀 开始导出 {len(args.folders)} 个文件夹 → {output_dir.resolve()}")
    total_success, total_failed = 0, 0

    try:
        for folder_id in args.folders:
            folder_title = folder_name_map.get(folder_id, folder_id)
            s, f = await export_folder(
                svc, fetcher, folder_id, folder_title, output_dir, args.limit
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
