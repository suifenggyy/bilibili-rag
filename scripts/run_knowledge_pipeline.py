#!/usr/bin/env python
"""
知识库流水线入口脚本。

用法示例：
  # 一次性处理当前 inbox 所有文件
  python scripts/run_knowledge_pipeline.py

  # 持续监听 inbox 目录（新文件自动处理）
  python scripts/run_knowledge_pipeline.py --watch

  # 只处理指定文件
  python scripts/run_knowledge_pipeline.py --file /path/to/article.md

  # 跳过流水线（仅导出，不处理）
  python scripts/run_knowledge_pipeline.py --skip-pipeline
"""
import argparse
import asyncio
import signal
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()


async def run_once(args):
    from app.services.knowledge_pipeline.orchestrator import (
        KnowledgePipelineOrchestrator,
    )

    orchestrator = KnowledgePipelineOrchestrator()
    if args.file:
        p = Path(args.file)
        if not p.exists():
            print(f"[Error] 文件不存在: {p}", file=sys.stderr)
            sys.exit(1)
        result = await orchestrator.process_files([p])
    else:
        result = await orchestrator.process_inbox(limit=args.limit)

    print(
        f"\n✅ 处理完成: {result.completed} 成功 / {result.failed} 失败",
        flush=True,
    )
    if result.failed > 0:
        print("失败文件：")
        for fr in result.file_results:
            if not fr.success:
                print(f"  - {fr.path.name}: {fr.error}")
    return result


def run_watch():
    from app.services.content_storage import ContentStorageManager
    from app.services.knowledge_pipeline.watcher import InboxWatcher

    storage = ContentStorageManager()
    inbox_dir = storage.get_inbox_dir()
    inbox_dir.mkdir(parents=True, exist_ok=True)

    watcher = InboxWatcher(inbox_dir=inbox_dir)
    watcher.start()

    stop_event = asyncio.Event()

    def _handle_signal(sig, frame):
        print("\n[Signal] 收到停止信号，正在退出…")
        watcher.stop()
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    print(f"👁  监听 inbox: {inbox_dir}（Ctrl+C 停止）")
    try:
        while not stop_event.is_set():
            import time
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()


def main():
    parser = argparse.ArgumentParser(
        description="知识库流水线：处理 inbox 中的 Markdown 文件",
    )
    parser.add_argument("--watch", action="store_true", help="持续监听 inbox 目录")
    parser.add_argument("--file", help="处理指定单个文件")
    parser.add_argument("--limit", type=int, default=None, help="最多处理 N 个文件")
    parser.add_argument(
        "--skip-pipeline",
        action="store_true",
        help="跳过流水线（仅打印 inbox 文件列表）",
    )
    args = parser.parse_args()

    if args.skip_pipeline:
        from app.services.content_storage import ContentStorageManager
        inbox = ContentStorageManager().get_inbox_dir()
        files = list(inbox.glob("*.md"))
        print(f"[skip-pipeline] inbox 文件列表 ({len(files)} 个):")
        for f in files:
            print(f"  {f.name}")
        return

    if args.watch:
        run_watch()
    else:
        asyncio.run(run_once(args))


if __name__ == "__main__":
    main()
