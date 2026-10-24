"""Best-effort media and comment enrichment for normalized Xiaohongshu notes."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from app.services.content_storage import ContentStorageManager
from app.services.content_summary import ContentSummaryService
from app.services.xhs_client import XhsClient
from app.services.xhs_models import XhsNoteContent, XhsNoteInfo


class XhsContentFetcher:
    """Download optional note media and summarize its available text content."""

    def __init__(
        self,
        client: XhsClient,
        *,
        summary_service: ContentSummaryService | None = None,
        storage_manager: ContentStorageManager | None = None,
        comment_limit: int = 20,
    ):
        self.client = client
        self.summary_service = summary_service or ContentSummaryService()
        self.storage_manager = storage_manager or ContentStorageManager()
        self.comment_limit = max(0, comment_limit)

    async def fetch_content(self, note: XhsNoteInfo) -> XhsNoteContent:
        """Return all available content; enrichment failures are warnings only."""
        logger.info("[XHS] Note {}: starting content enrichment.", note.note_id)
        content = XhsNoteContent(
            note=note,
            original_description=note.description,
            formatted_tags=" ".join(f"#{tag}" for tag in note.tags),
        )
        await self._download_images(note, content)
        await self._fetch_comments(note, content)

        summary_input = "\n\n".join(
            part
            for part in (content.original_description, content.comments_section)
            if part
        )
        content.summary_block = await self._summarize_content(
            note.note_id, summary_input, content.warnings
        )
        logger.info(
            "[XHS] Note {}: content enrichment complete ({} warnings).",
            note.note_id,
            len(content.warnings),
        )
        return content

    def _storage_title(self, note: XhsNoteInfo) -> str:
        return f"{note.title or note.note_id}_{note.note_id}"

    async def _download_images(self, note: XhsNoteInfo, content: XhsNoteContent) -> None:
        title = self._storage_title(note)
        if note.image_urls:
            logger.info("[XHS] Note {}: processing {} images.", note.note_id, len(note.image_urls))
        for index, url in enumerate(note.image_urls, start=1):
            filename = f"image_{index:02d}{_media_suffix(url, '.jpg')}"
            cached = self.storage_manager.find_work_file_path("xhs", title, filename)
            if cached and cached.is_file() and cached.stat().st_size > 0:
                logger.info("[XHS] Note {}: using cached image {}/{}.", note.note_id, index, len(note.image_urls))
                content.image_paths.append(str(cached))
                continue
            destination = self.storage_manager.build_work_file_path("xhs", title, filename)
            try:
                logger.info("[XHS] Note {}: downloading image {}/{}.", note.note_id, index, len(note.image_urls))
                saved = await self.client.download_media(url, destination)
                content.image_paths.append(str(saved))
            except Exception as exc:
                self._warn(
                    content,
                    f"Image download failed for note {note.note_id} ({index}): {exc}",
                )

    async def _fetch_comments(self, note: XhsNoteInfo, content: XhsNoteContent) -> None:
        if not self.comment_limit:
            return
        logger.info("[XHS] Note {}: fetching up to {} comments.", note.note_id, self.comment_limit)
        comments: list[tuple[str, str]] = []
        cursor = ""
        seen_cursors: set[str] = set()
        while len(comments) < self.comment_limit:
            try:
                response = await self.client.get_comments(note.note_id, note.xsec_token, cursor)
            except Exception as exc:
                logger.info("[XHS] Comments fetch skipped for note {}: {}", note.note_id, exc)
                break
            for raw_comment in _comment_items(response):
                if not _is_top_level_comment(raw_comment):
                    continue
                nickname = _comment_author(raw_comment) or "匿名用户"
                text = _comment_text(raw_comment)
                if text:
                    comments.append((nickname, text))
                if len(comments) >= self.comment_limit:
                    break
            if len(comments) >= self.comment_limit or not _has_more(response):
                break
            next_cursor = _string(
                response.get("cursor") or response.get("next_cursor") or response.get("nextCursor")
            )
            if not next_cursor or next_cursor in seen_cursors:
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor

        if comments:
            lines = ["## 热门评论", ""]
            lines.extend(f"- **{nickname}**：{text}" for nickname, text in comments)
            content.comments_section = "\n".join(lines)
        logger.info("[XHS] Note {}: fetched {} comments.", note.note_id, len(comments))

    async def _summarize_content(
        self,
        note_id: str,
        text: str,
        warnings: list[str] | None = None,
    ) -> str:
        if not text or not self.summary_service:
            return ""
        try:
            logger.info("[XHS] Note {}: generating summary.", note_id)
            summary = (await self.summary_service.summarize(text) or "").strip()
            logger.info("[XHS] Note {}: summary generation complete.", note_id)
            return summary
        except Exception as exc:
            message = f"Summary generation failed for note {note_id}: {exc}"
            logger.warning("[XHS] {}", message)
            if warnings is not None:
                warnings.append(message)
            return ""

    @staticmethod
    def _warn(content: XhsNoteContent, message: str) -> None:
        logger.warning("[XHS] {}", message)
        content.warnings.append(message)


def _media_suffix(url: str, fallback: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"} else fallback


def _comment_items(response: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("comments", "comment_list", "data"):
        value = response.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _is_top_level_comment(comment: dict[str, Any]) -> bool:
    parent = comment.get("parent_comment") or comment.get("parent_id") or comment.get("parent_comment_id")
    return parent in (None, "", 0, "0")


def _comment_author(comment: dict[str, Any]) -> str:
    author = comment.get("user_info") or comment.get("user") or comment.get("author") or {}
    return _string(author.get("nickname") or author.get("name")) if isinstance(author, dict) else ""


def _comment_text(comment: dict[str, Any]) -> str:
    return " ".join(_string(comment.get("content") or comment.get("text")).split())


def _has_more(response: dict[str, Any]) -> bool:
    value = response.get("has_more", response.get("hasMore", False))
    return value is True or value == 1 or value == "1"


def _string(value: Any) -> str:
    return str(value).strip() if value is not None else ""
