"""
历史 collection/ 文件一次性导入工具。

将旧版 collection/<source>/<date>/ 中的 Markdown 文件迁移到 vault/inbox/，
自动补写缺失的 frontmatter，并用内容哈希保证幂等性。

用法:
    python scripts/import_collection_to_inbox.py --sources bilibili douyin instapaper
    python scripts/import_collection_to_inbox.py --all
    python scripts/import_collection_to_inbox.py --all --dry-run
"""
import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv
load_dotenv(ROOT_DIR / ".env")

from loguru import logger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将历史 collection/ Markdown 迁移到 vault/inbox/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--sources",
        nargs="+",
        metavar="SOURCE",
        help="指定要导入的平台名称（如 bilibili douyin instapaper）",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="导入 collection/ 下所有平台",
    )
    parser.add_argument(
        "--collection-dir",
        required=True,
        help="旧版 collection 根目录（必须指定，例如 ~/Obsidian/jarvis/collection）",
    )
    parser.add_argument(
        "--inbox-dir",
        default=None,
        help="inbox 目标目录（默认读取 OBSIDIAN_VAULT_ROOT/OBSIDIAN_INBOX_DIR）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只统计数量，不实际复制文件",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    from app.services.knowledge_pipeline.legacy_import import LegacyCollectionImporter
    from app.services.content_storage import ContentStorageManager

    storage = ContentStorageManager(
        export_root=args.collection_dir,
        vault_root=None,
    )
    inbox_dir = Path(args.inbox_dir) if args.inbox_dir else storage.get_inbox_dir()

    importer = LegacyCollectionImporter(
        collection_root=storage.export_root,
        inbox_dir=inbox_dir,
    )

    if args.dry_run:
        print("🔍 Dry-run 模式：仅统计，不写入文件")

    if args.all:
        collection_root = storage.export_root
        if not collection_root.exists():
            print(f"❌ collection 目录不存在: {collection_root}")
            sys.exit(1)
        sources = [p.name for p in collection_root.iterdir() if p.is_dir()]
        print(f"📂 发现 {len(sources)} 个平台目录: {', '.join(sources)}")
    else:
        sources = args.sources

    if not sources:
        print("⚠️  没有找到可导入的 source，退出")
        sys.exit(0)

    print(f"📥 开始导入: {', '.join(sources)}")
    print(f"   collection: {importer.collection_root}")
    print(f"   inbox:      {importer.inbox_dir}")

    if args.dry_run:
        # Count only
        total_files = 0
        for source in sources:
            src_dir = importer.collection_root / source
            if src_dir.exists():
                count = sum(1 for _ in src_dir.rglob("*.md"))
                total_files += count
                print(f"   {source}: {count} 个文件")
        print(f"\n   共 {total_files} 个文件（dry-run，未实际导入）")
        return

    result = importer.import_sources(sources)

    print(f"\n✅ 导入完成：")
    print(f"   已导入：{result.imported_count}")
    print(f"   已跳过：{result.skipped_count}（重复）")
    if result.failed_count:
        print(f"   失败：  {result.failed_count}")


if __name__ == "__main__":
    main()
