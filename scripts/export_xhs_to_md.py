"""Export Xiaohongshu notes to Markdown files for the Obsidian inbox."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import shutil
import sys
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Protocol

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

load_dotenv(ROOT_DIR / ".env")

from loguru import logger

from app.config import settings
from app.services.content_storage import ContentStorageManager
from app.services.content_summary import ContentSummaryService, append_summary_section
from app.services.knowledge_pipeline.frontmatter import (
    build_export_frontmatter,
    extract_plain_summary,
)
from app.services.xhs import XhsService
from app.services.xhs_auth import XhsAuthOptions, XhsAuthService
from app.services.xhs_client import (
    XhsAuthenticationError as XhsClientAuthenticationError,
    XhsClient,
    XhsNoteUnavailableError,
    XhsRiskControlError,
)
from app.services.xhs_fetcher import XhsContentFetcher
from app.services.xhs_models import (
    XhsNoteContent,
    XhsNoteReference,
    merge_note_references,
)


class SourceKind(str, Enum):
    NOTE_URL = "note_url"
    CREATOR = "creator"
    KEYWORD = "keyword"
    FAVORITES = "favorites"


@dataclass(frozen=True)
class SourceRequest:
    kind: SourceKind
    value: str


@dataclass
class ExportCounts:
    successful: int = 0
    partial: int = 0
    failed: int = 0
    skipped: int = 0


class SourceRequestAction(argparse.Action):
    """Append a source request while retaining the order of mixed CLI flags."""

    def __init__(self, option_strings, dest, *, source_kind: SourceKind, **kwargs):
        self.source_kind = source_kind
        super().__init__(option_strings, dest, **kwargs)

    def __call__(self, parser, namespace, value, option_string=None):
        requests = list(getattr(namespace, self.dest, []) or [])
        requests.append(SourceRequest(self.source_kind, "" if value == [] else value))
        setattr(namespace, self.dest, requests)


class ExportArgumentParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        parsed = super().parse_args(args, namespace)
        if not parsed.source_requests:
            self.error(
                "at least one of --note-url, --creator, --keyword, or --favorites is required"
            )
        if parsed.limit < 0:
            self.error("--limit must be zero or greater")
        if parsed.concurrency < 1:
            self.error("--concurrency must be at least 1")
        if parsed.comment_limit < 0:
            self.error("--comment-limit must be zero or greater")
        if parsed.request_interval < 0:
            self.error("--request-interval must be zero or greater")
        return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = ExportArgumentParser(
        description="Export Xiaohongshu notes, creator posts, search results, or favorite boards to Markdown."
    )
    parser.set_defaults(source_requests=[])
    parser.add_argument(
        "--note-url",
        dest="source_requests",
        action=SourceRequestAction,
        source_kind=SourceKind.NOTE_URL,
        metavar="URL_OR_ID",
        help="Note URL or bare note ID; repeatable.",
    )
    parser.add_argument(
        "--creator",
        dest="source_requests",
        action=SourceRequestAction,
        source_kind=SourceKind.CREATOR,
        metavar="URL_OR_ID",
        help="Creator profile URL or user ID; repeatable.",
    )
    parser.add_argument(
        "--keyword",
        dest="source_requests",
        action=SourceRequestAction,
        source_kind=SourceKind.KEYWORD,
        metavar="KEYWORD",
        help="Search keyword; repeatable.",
    )
    parser.add_argument(
        "--favorites",
        "--all-favorite-boards",
        dest="source_requests",
        action=SourceRequestAction,
        source_kind=SourceKind.FAVORITES,
        nargs=0,
        help="Export every note saved by the authenticated account.",
    )
    parser.add_argument("--cookie", default="", help="Xiaohongshu Cookie header.")
    parser.add_argument("--login", action="store_true", help="Force interactive QR-code login.")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Global maximum number of unique notes (default: 10; 0 means unlimited).",
    )
    parser.add_argument(
        "--after-date",
        default=settings.xhs_after_date,
        help="Only include listed notes on or after YYYY-MM-DD.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=settings.asr_concurrency,
        help="Maximum concurrent note exports.",
    )
    parser.add_argument(
        "--comment-limit",
        type=int,
        default=settings.xhs_comment_limit,
        help="Maximum top-level comments per note.",
    )
    parser.add_argument(
        "--request-interval",
        type=float,
        default=settings.xhs_request_interval,
        help="Minimum interval in seconds between XHS HTTP requests.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Final export root (defaults to the configured Obsidian inbox).",
    )
    return parser


class ExportDependencyFactory(Protocol):
    def create_auth(self, args) -> XhsAuthService: ...
    def create_client(self, session, args) -> XhsClient: ...
    def create_service(self, client) -> XhsService: ...
    def create_summary_service(self) -> ContentSummaryService: ...
    def create_storage(self, args) -> ContentStorageManager: ...
    def create_fetcher(
        self,
        client,
        summary_service,
        storage,
        args,
    ) -> XhsContentFetcher: ...


class DefaultExportDependencyFactory:
    """Construct production dependencies in one place for test isolation."""

    def create_auth(self, args) -> XhsAuthService:
        return XhsAuthService(
            XhsAuthOptions(
                force_login=args.login,
                cookie=args.cookie,
                env_cookie=settings.xhs_cookie,
            ),
            session_file=settings.xhs_session_file,
        )

    def create_client(self, session, args) -> XhsClient:
        return XhsClient(session, request_interval=args.request_interval)

    def create_service(self, client) -> XhsService:
        return XhsService(client)

    def create_summary_service(self) -> ContentSummaryService:
        return ContentSummaryService()

    def create_storage(self, args) -> ContentStorageManager:
        return ContentStorageManager(export_root=args.output_dir)

    def create_fetcher(
        self,
        client,
        summary_service,
        storage,
        args,
    ) -> XhsContentFetcher:
        return XhsContentFetcher(
            client,
            summary_service=summary_service,
            storage_manager=storage,
            comment_limit=args.comment_limit,
        )


async def _close(resource) -> None:
    close = getattr(resource, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def _discover_references(args, service: XhsService, counts: ExportCounts):
    merged: OrderedDict[str, XhsNoteReference] = OrderedDict()
    limit = args.limit or None

    for request in args.source_requests:
        if limit is not None and len(merged) >= limit:
            break
        source_start_count = len(merged)
        logger.info("[XHS] Discovering {} source: {}", request.kind.value, request.value)
        if request.kind is SourceKind.NOTE_URL:
            references = await service.references_from_urls([request.value])
            iterator = _iterate_references(references)
        elif request.kind is SourceKind.CREATOR:
            iterator = service.iter_creator_references(request.value, after_date=args.after_date or None)
        elif request.kind is SourceKind.KEYWORD:
            iterator = service.iter_search_references(request.value, after_date=args.after_date or None)
        else:
            iterator = service.iter_favorite_references(
                after_date=args.after_date or None,
            )

        async for reference in iterator:
            existing = merged.get(reference.note_id)
            if existing is not None:
                merged[reference.note_id] = merge_note_references(existing, reference)
                counts.skipped += 1
            else:
                merged[reference.note_id] = reference
            if limit is not None and len(merged) >= limit:
                break
        logger.info(
            "[XHS] Discovery complete for {}: {} new unique notes ({} total).",
            request.value,
            len(merged) - source_start_count,
            len(merged),
        )
    return list(merged.values())


async def _iterate_references(references: list[XhsNoteReference]):
    for reference in references:
        yield reference


def _export_day(content: XhsNoteContent) -> date:
    timestamp = content.note.published_at
    return datetime.fromtimestamp(timestamp).date() if timestamp else date.today()


def build_markdown(content: XhsNoteContent, relative_images: list[str]) -> str:
    """Render one complete XHS export document with inbox-compatible frontmatter."""
    note = content.note
    published_date = _export_day(content).isoformat()
    frontmatter = build_export_frontmatter(
        title=note.title or note.note_id,
        date_str=published_date,
        source=note.source_url,
        summary=extract_plain_summary(content.summary_block),
        platform="xhs",
        tags=list(note.tags) or None,
    )
    source_label = {
        "asr": "视频 ASR 转写",
        "basic_info": "笔记原文",
    }.get(content.content_source, content.content_source)
    lines = [
        f"# {note.title or note.note_id}",
        "",
        "## 笔记信息",
        "",
        "| 字段 | 内容 |",
        "|------|------|",
        f"| 笔记 ID | [{note.note_id}]({note.source_url}) |",
        f"| 作者 | {note.author_name or '未知'} |",
        f"| 发布日期 | {published_date} |",
        f"| 类型 | {note.note_type or '未知'} |",
        f"| 点赞 / 收藏 / 评论 | {note.liked_count or '0'} / {note.collected_count or '0'} / {note.comment_count or '0'} |",
        f"| 内容来源 | {source_label} |",
        "",
        "## 原文",
        "",
        content.original_description or "_（未获取到笔记正文）_",
    ]
    if content.formatted_tags:
        lines += ["", "## 标签", "", content.formatted_tags]
    if relative_images:
        lines += ["", "## 图片", ""]
        lines.extend(
            f"![图片 {index}]({relative_path})"
            for index, relative_path in enumerate(relative_images, start=1)
        )
    append_summary_section(lines, content.summary_block)
    if content.transcript:
        lines += ["", "## 视频转写", "", content.transcript]
    if content.comments_section:
        lines += ["", content.comments_section.strip()]
    lines += [
        "",
        "---",
        "",
        f"_导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_",
    ]
    return frontmatter + "\n".join(lines) + "\n"


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}


def _write_export(content: XhsNoteContent, storage: ContentStorageManager) -> Path:
    note = content.note
    markdown_path = storage.build_markdown_path(
        "xhs",
        note.title or note.note_id,
        note.note_id,
        day=_export_day(content),
    )
    media_dir = markdown_path.with_name(f"{markdown_path.stem}.media")
    relative_images: list[str] = []
    copy_warnings: list[str] = []
    for image_path in content.image_paths:
        source = Path(image_path)
        if source.suffix.lower() not in _IMAGE_SUFFIXES or not source.is_file():
            continue
        destination = media_dir / source.name
        try:
            media_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            relative_images.append((Path(media_dir.name) / destination.name).as_posix())
        except OSError as exc:
            copy_warnings.append(f"Image copy failed for note {note.note_id}: {exc}")
    content.warnings.extend(copy_warnings)

    partial_path = markdown_path.with_suffix(markdown_path.suffix + ".part")
    try:
        partial_path.write_text(build_markdown(content, relative_images), encoding="utf-8")
        partial_path.replace(markdown_path)
    finally:
        partial_path.unlink(missing_ok=True)
    return markdown_path.resolve()


async def run_export(
    args,
    *,
    dependency_factory: ExportDependencyFactory | None = None,
) -> ExportCounts:
    """Run discovery, enrichment, and export with item-level failure isolation."""
    factory = dependency_factory or DefaultExportDependencyFactory()
    counts = ExportCounts()
    auth = client = None
    try:
        logger.info("[XHS] Opening authenticated browser session.")
        auth = factory.create_auth(args)
        session = await auth.open()
        logger.info("[XHS] Browser session is ready; initializing export services.")
        client = factory.create_client(session, args)
        service = factory.create_service(client)
        summary_service = factory.create_summary_service()
        storage = factory.create_storage(args)
        fetcher = factory.create_fetcher(
            client,
            summary_service,
            storage,
            args,
        )
        references = await _discover_references(args, service, counts)
        logger.info(
            "[XHS] Discovered {} notes; exporting with concurrency {}.",
            len(references),
            args.concurrency,
        )
        semaphore = asyncio.Semaphore(args.concurrency)
        total = len(references)

        async def export_one(index: int, reference: XhsNoteReference):
            async with semaphore:
                try:
                    position = index + 1
                    logger.info(
                        "[XHS] [{}/{}] Resolving note {}.",
                        position,
                        total,
                        reference.note_id,
                    )
                    note = await service.resolve_note(reference)
                    logger.info(
                        "[XHS] [{}/{}] Fetching content for {} ({}).",
                        position,
                        total,
                        note.note_id,
                        note.title or "untitled",
                    )
                    content = await fetcher.fetch_content(note)
                    logger.info(
                        "[XHS] [{}/{}] Writing Markdown for {}.",
                        position,
                        total,
                        note.note_id,
                    )
                    markdown_path = _write_export(content, storage)
                    logger.info(
                        "[XHS] [{}/{}] Document written: {}.",
                        position,
                        total,
                        markdown_path,
                    )
                    status = "partial" if content.warnings else "successful"
                    logger.info(
                        "[XHS] [{}/{}] Finished note {} ({}).",
                        position,
                        total,
                        note.note_id,
                        status,
                    )
                    return index, status, reference.note_id, ""
                except (XhsClientAuthenticationError, XhsRiskControlError):
                    raise
                except XhsNoteUnavailableError as exc:
                    logger.warning(
                        "[XHS] [{}/{}] Skipped unavailable note {}: {}",
                        index + 1,
                        total,
                        reference.note_id,
                        exc,
                    )
                    return index, "skipped", reference.note_id, ""
                except Exception as exc:
                    return index, "failed", reference.note_id, str(exc)

        results = await asyncio.gather(
            *(export_one(index, reference) for index, reference in enumerate(references))
        )
        for _, status, note_id, detail in sorted(results):
            setattr(counts, status, getattr(counts, status) + 1)
            if status == "failed":
                logger.error("[XHS] Export failed for note {}: {}", note_id, detail)
        return counts
    finally:
        if client is not None:
            await _close(client)
        if auth is not None:
            await _close(auth)


def main(argv: list[str] | None = None, *, dependency_factory: ExportDependencyFactory | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        counts = asyncio.run(run_export(args, dependency_factory=dependency_factory))
    except Exception as exc:
        logger.error("[XHS] Export stopped: {}", exc)
        print(f"Xiaohongshu export failed: {exc}", file=sys.stderr)
        return 1
    print(
        "Xiaohongshu export complete: "
        f"successful={counts.successful}, partial={counts.partial}, "
        f"failed={counts.failed}, skipped={counts.skipped}"
    )
    return 1 if counts.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
