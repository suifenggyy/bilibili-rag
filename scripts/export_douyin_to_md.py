"""
抖音收藏夹 → Markdown 导出工具

从抖音收藏夹获取视频，将音频通过 ASR 转写为文字，保存为 Markdown 文件。
不依赖 RAG 或向量数据库，独立运行。

【前提条件】
需要本地运行 Evil0ctal/Douyin_TikTok_Download_API 服务：
    git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API
    cd Douyin_TikTok_Download_API && pip install -r requirements.txt
    python main.py                   # 默认监听 http://localhost:2333

【获取抖音 Cookie】
抖音不提供扫码 API，需手动从浏览器复制 Cookie：
    1. Chrome/Edge 打开 https://www.douyin.com 并登录
    2. F12 → Application → Cookies → douyin.com
    3. 复制全部字段，拼接为 "key1=val1; key2=val2; ..." 格式
    4. 填入 DOUYIN_COOKIE 环境变量或通过 --cookie 参数传入

支持两种 ASR 后端：
  - dashscope：DashScope 云端（需配置 DASHSCOPE_API_KEY）
  - ollama：本地 Whisper（需安装 Ollama 并运行 `ollama pull whisper`）

用法:
    # 交互式选择要导出的视频数量
    python scripts/export_douyin_to_md.py

    # 指定 Cookie（不用配置环境变量）
    python scripts/export_douyin_to_md.py --cookie "ttwid=xxx; sessionid=xxx; ..."

    # 指定输出目录
    python scripts/export_douyin_to_md.py --output-dir ./douyin_output

    # 导出所有收藏视频
    python scripts/export_douyin_to_md.py --all

    # 仅导出最新的 N 个视频
    python scripts/export_douyin_to_md.py --limit 20

    # 使用 Ollama 本地 ASR
    python scripts/export_douyin_to_md.py --asr-backend ollama

    # 指定 Evil0ctal 服务地址
    python scripts/export_douyin_to_md.py --evil0ctal-url http://localhost:2333
"""

import argparse
import asyncio
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


def _get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _safe_filename(name: str, max_len: int = 80) -> str:
    """将字符串转为合法文件名"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:max_len] if len(name) > max_len else name


def _format_duration(ms: int) -> str:
    """将毫秒转为 mm:ss 或 hh:mm:ss 格式"""
    if not ms:
        return "未知"
    s = ms // 1000
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _build_markdown(vc, asr_text: str, source: str) -> str:
    """构建 Markdown 文件内容"""
    create_str = (
        datetime.fromtimestamp(vc.create_time).strftime("%Y-%m-%d")
        if vc.create_time
        else "未知"
    )
    duration_str = _format_duration(vc.duration)

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
        f"| 视频ID | [{vc.aweme_id}]({vc.share_url}) |",
        f"| 作者 | {vc.author} |",
        f"| 时长 | {duration_str} |",
        f"| 发布日期 | {create_str} |",
        f"| 内容来源 | {source_label} |",
    ]

    if vc.cover_url:
        lines += ["", f"![封面]({vc.cover_url})"]

    lines += ["", "---", "", "## 转写内容", ""]

    if asr_text and asr_text.strip():
        lines.append(asr_text.strip())
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
    """根据参数构建 ASR 服务，并做可用性检查"""
    backend = args.asr_backend

    if backend == "auto":
        if args.api_key:
            backend = "dashscope"
        else:
            backend = "ollama"
            print("⚠️  未配置 DASHSCOPE_API_KEY，自动切换到 Ollama 本地 ASR")

    if backend == "dashscope":
        if not args.api_key:
            print("⚠️  未配置 DASHSCOPE_API_KEY，ASR 转写不可用，将仅保存基本信息")
            return None
        from app.services.asr import ASRService
        print(f"🔊 ASR 后端：DashScope（{_get_env('ASR_MODEL', 'paraformer-v2')}）")
        return ASRService(api_key=args.api_key)

    # Ollama 后端
    from app.services.asr_local import OllamaASRService
    asr = OllamaASRService(
        base_url=args.ollama_url,
        model=args.ollama_model,
        language=args.ollama_language,
        timeout=int(_get_env("ASR_TIMEOUT", "600")),
    )
    print(f"🔊 ASR 后端：Ollama 本地（{asr.base_url}，模型：{asr.model}）")

    if not asr.check_ollama_available():
        print(
            f"❌ 无法连接到 Ollama 服务（{asr.base_url}），"
            "请确认 Ollama 已启动（运行 `ollama serve`）"
        )
        sys.exit(1)

    if not asr.check_model_available():
        print(
            f"⚠️  Ollama 中未找到模型 '{asr.model}'，"
            f"请先运行：ollama pull {asr.model}"
        )
        sys.exit(1)

    print(f"   ✅ Ollama 服务正常，模型 '{asr.model}' 已就绪")
    return asr


async def export_videos(
    douyin,
    fetcher,
    videos: list,
    output_dir: Path,
) -> tuple[int, int]:
    """
    批量处理并导出视频到 Markdown 文件。

    Returns:
        (成功数, 失败数)
    """
    from app.services.douyin import DouyinService

    success, failed = 0, 0
    total = len(videos)

    for i, raw_video in enumerate(videos, 1):
        video_info = DouyinService.parse_video_info(raw_video)
        aweme_id = video_info["aweme_id"]
        title = video_info["title"]

        safe_title = _safe_filename(title)
        md_path = output_dir / f"{safe_title}_{aweme_id}.md"

        if md_path.exists():
            print(f"  [{i:3d}/{total}] ⏭️  已存在，跳过：{title[:50]}")
            success += 1
            continue

        print(f"  [{i:3d}/{total}] 🔄 处理中：{title[:55]}", end="", flush=True)

        try:
            vc = await fetcher.fetch_content(video_info)
            md_content = _build_markdown(vc, vc.content, vc.content_source)
            md_path.write_text(md_content, encoding="utf-8")

            status = "ASR ✅" if vc.content_source == "asr" else "仅基本信息 ⚠️"
            print(f"  → {status}")
            success += 1

        except Exception as e:
            logger.error(f"处理视频失败 [{aweme_id}]: {e}")
            print(f"  → ❌ 失败: {e}")
            failed += 1

        await asyncio.sleep(0.3)

    return success, failed


async def interactive_select_limit(total: int) -> int:
    """交互式选择要导出的视频数量"""
    print(f"\n📦 共找到 {total} 个收藏视频")
    print("  [1] 仅导出最新 20 个")
    print("  [2] 仅导出最新 50 个")
    print("  [3] 导出全部")
    print("  [4] 自定义数量")
    print()

    while True:
        raw = input("请选择 [1/2/3/4]：").strip()
        if raw == "1":
            return min(20, total)
        if raw == "2":
            return min(50, total)
        if raw == "3":
            return total
        if raw == "4":
            try:
                n = int(input(f"请输入数量（1~{total}）：").strip())
                if 1 <= n <= total:
                    return n
                print(f"  ⚠️  请输入 1~{total} 之间的数字")
            except ValueError:
                print("  ⚠️  请输入有效数字")
        else:
            print("  ⚠️  请输入 1、2、3 或 4")


async def main():
    parser = argparse.ArgumentParser(
        description="抖音收藏夹音频转写 → Markdown 导出工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # 核心参数
    parser.add_argument(
        "--cookie",
        default=_get_env("DOUYIN_COOKIE"),
        help="抖音浏览器 Cookie 字符串（或设置 DOUYIN_COOKIE 环境变量）",
    )
    parser.add_argument(
        "--evil0ctal-url",
        default=_get_env("DOUYIN_EVIL0CTAL_URL", "http://localhost:2333"),
        help="Evil0ctal API 服务地址（默认: http://localhost:2333）",
    )
    parser.add_argument(
        "--output-dir",
        default=_get_env("DOUYIN_OUTPUT_DIR", "douyin_output"),
        help="输出目录（默认: ./douyin_output）",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="导出所有收藏视频（不弹出交互选择）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多导出最新 N 个视频（0=不限制）",
    )
    # ASR 后端
    parser.add_argument(
        "--asr-backend",
        default="auto",
        choices=["auto", "dashscope", "ollama"],
        help="ASR 转写后端（auto=有 Key 用 DashScope，否则用 Ollama）",
    )
    parser.add_argument(
        "--api-key",
        default=_get_env("DASHSCOPE_API_KEY"),
        help="DashScope API Key（--asr-backend dashscope 时使用）",
    )
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
    args = parser.parse_args()

    # ── 参数校验 ─────────────────────────────────────────────────────────
    if not args.cookie:
        print(
            "❌ 未提供抖音 Cookie！\n"
            "   请通过 --cookie 参数或 DOUYIN_COOKIE 环境变量提供。\n\n"
            "   获取方式：\n"
            "   1. Chrome 打开 https://www.douyin.com 并登录\n"
            "   2. F12 → Application → Cookies → douyin.com\n"
            '   3. 复制全部字段，拼接为 "key1=val1; key2=val2; ..." 格式'
        )
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 初始化服务 ────────────────────────────────────────────────────────
    from app.services.douyin import DouyinService
    from app.services.douyin_fetcher import DouyinContentFetcher

    douyin = DouyinService(cookie=args.cookie, evil0ctal_url=args.evil0ctal_url)

    print(f"\n🔗 检查 Evil0ctal API 服务（{args.evil0ctal_url}）...", end="", flush=True)
    available = await douyin.check_evil0ctal_available()
    if not available:
        print(
            f"\n❌ 无法连接到 Evil0ctal API 服务（{args.evil0ctal_url}）\n\n"
            "   请先按以下步骤部署：\n"
            "   git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API\n"
            "   cd Douyin_TikTok_Download_API && pip install -r requirements.txt\n"
            "   python main.py"
        )
        await douyin.close()
        sys.exit(1)
    print(" ✅")

    print("🍪 校验 Cookie 有效性...", end="", flush=True)
    cookie_ok = await douyin.check_cookie_valid()
    if not cookie_ok:
        print(
            "\n⚠️  Cookie 校验失败，可能已过期或格式不正确。\n"
            "   请重新从浏览器复制最新 Cookie。\n"
            "   （将继续尝试，实际结果可能有误）"
        )
    else:
        print(" ✅")

    asr = await _build_asr_service(args)
    fetcher = DouyinContentFetcher(asr_service=asr)

    try:
        # ── 获取收藏夹视频列表 ───────────────────────────────────────────
        print("\n📥 获取收藏夹视频列表（可能需要较长时间）...", flush=True)
        start_t = time.time()
        try:
            all_videos = await douyin.get_all_collection_videos()
        except Exception as e:
            print(f"❌ 获取失败：{e}")
            sys.exit(1)

        elapsed = time.time() - start_t
        total = len(all_videos)
        print(f"✅ 共找到 {total} 个收藏视频（耗时 {elapsed:.1f}s）")

        if total == 0:
            print("⚠️  收藏夹为空，无视频可导出")
            sys.exit(0)

        # ── 决定要处理的视频范围 ─────────────────────────────────────────
        if args.limit > 0:
            selected = all_videos[:args.limit]
            print(f"📌 限制导出最新 {args.limit} 个（共 {total} 个）")
        elif args.all:
            selected = all_videos
        else:
            limit = await interactive_select_limit(total)
            selected = all_videos[:limit]
            print(f"📌 将导出最新 {len(selected)} 个视频")

        # ── 开始导出 ─────────────────────────────────────────────────────
        print(f"\n🚀 开始导出 {len(selected)} 个视频 → {output_dir.resolve()}\n")
        s, f = await export_videos(douyin, fetcher, selected, output_dir)

        print(f"\n{'='*60}")
        print(f"✅ 导出完成！成功：{s} 个，失败：{f} 个")
        print(f"📂 文件保存在：{output_dir.resolve()}")
        if f > 0:
            print(f"⚠️  {f} 个视频处理失败，请查看上方错误信息")

    finally:
        await douyin.close()


if __name__ == "__main__":
    asyncio.run(main())
