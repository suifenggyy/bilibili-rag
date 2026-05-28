#!/usr/bin/env python
"""
知识库日报生成脚本。

用法：
  # 生成今日日报
  python scripts/generate_daily_report.py

  # 生成指定日期日报
  python scripts/generate_daily_report.py --date 2026-05-28

  # 生成后输出到终端（不写文件）
  python scripts/generate_daily_report.py --print
"""
import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()


async def _run(args):
    from app.config import settings
    from app.services.content_storage import ContentStorageManager
    from app.services.knowledge_pipeline.daily_reporter import DailyReporter

    storage = ContentStorageManager()
    knowledge_dir = storage.get_knowledge_dir()
    daily_dir = storage.get_daily_dir()

    day = date.fromisoformat(args.date) if args.date else date.today()

    reporter = DailyReporter(
        knowledge_dir=knowledge_dir,
        daily_dir=daily_dir,
        tavily_api_key=getattr(settings, "tavily_api_key", ""),
    )

    if args.print:
        content = await reporter.generate(day=day)
        print(content)
    else:
        out = await reporter.generate_and_save(day=day)
        print(f"✅ 日报已保存: {out}")


def main():
    parser = argparse.ArgumentParser(description="生成知识库日报")
    parser.add_argument("--date", help="日期 YYYY-MM-DD，默认今日")
    parser.add_argument("--print", action="store_true", help="输出到终端而不写文件")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
