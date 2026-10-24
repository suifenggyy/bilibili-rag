"""Xiaohongshu discovery and note-detail normalization."""

from __future__ import annotations

import asyncio
import html
import json
import random
import re
import time
from collections.abc import AsyncIterator, Iterable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from loguru import logger

from app.services.xhs_client import (
    XhsAuthenticationError,
    XhsClient,
    XhsClientError,
    XhsDataFetchError,
    XhsNoteUnavailableError,
    XhsRiskControlError,
)
from app.services.xhs_models import (
    XhsDiscoveryKind,
    XhsNoteInfo,
    XhsNoteReference,
)


_NOTE_PATH = re.compile(r"/explore/([A-Za-z0-9_-]+)")
_CREATOR_PATH = re.compile(r"/user/profile/([A-Za-z0-9_-]+)")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{3,}$")
_WEB_ORIGIN = "https://www.xiaohongshu.com"
_BASE36_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_SEARCH_RESPONSE_ATTEMPTS = 2
_SEARCH_NON_NOTE_MODEL_TYPES = frozenset({"rec_query", "hot_query"})
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


class XhsService:
    """Discover lightweight note references and resolve normalized note details."""

    def __init__(self, client: XhsClient):
        self.client = client
        self._favorite_user_id: str | None = None

    async def references_from_urls(self, urls: list[str]) -> list[XhsNoteReference]:
        references: list[XhsNoteReference] = []
        for value in urls:
            candidate = (value or "").strip()
            if not candidate:
                raise ValueError("Xiaohongshu note URL or note ID must not be empty.")
            if "://" in candidate:
                parsed = urlparse(candidate)
                if not _NOTE_PATH.search(parsed.path):
                    try:
                        candidate = await self.client.resolve_shared_url(candidate)
                    except Exception as exc:
                        raise ValueError(
                            f"Unsupported Xiaohongshu note URL: {value}"
                        ) from exc
                references.append(self._parse_note_reference(candidate))
            else:
                references.append(self._parse_note_reference(candidate))
        return references

    async def iter_search_references(
        self,
        keyword: str,
        *,
        after_date: str | None = None,
    ) -> AsyncIterator[XhsNoteReference]:
        cleaned_keyword = (keyword or "").strip()
        if not cleaned_keyword:
            raise ValueError("Xiaohongshu search keyword must not be empty.")
        cutoff = _parse_after_date(after_date)
        page = 1
        search_id = _get_search_id()

        while True:
            logger.info("[XHS] Search '{}' requesting page {}.", cleaned_keyword, page)
            response, items = await self._search_page(
                cleaned_keyword,
                page,
                search_id,
            )
            logger.info(
                "[XHS] Search '{}' received {} items on page {} (has_more={}).",
                cleaned_keyword,
                len(items),
                page,
                _has_more(response),
            )
            for item in items:
                reference = _reference_from_list_item(item, XhsDiscoveryKind.SEARCH)
                if reference is None:
                    continue
                if _is_older_than(reference.published_at, cutoff):
                    return
                yield reference

            if not _has_more(response):
                return
            next_search_id = _string(response.get("search_id") or response.get("searchId"))
            if next_search_id:
                search_id = next_search_id
            page += 1

    async def _search_page(
        self,
        keyword: str,
        page: int,
        search_id: str,
    ) -> tuple[dict[str, Any], list[Any]]:
        """Return an empty final page when XHS omits items after pagination ends."""
        for attempt in range(_SEARCH_RESPONSE_ATTEMPTS):
            response = await self.client.search_notes(keyword, page, 20, search_id)
            items = response.get("items")
            if isinstance(items, list):
                return response, items
            if not _has_more(response):
                return response, []
            if attempt + 1 < _SEARCH_RESPONSE_ATTEMPTS:
                await asyncio.sleep(0.5)
        raise XhsDataFetchError(
            "Xiaohongshu search response omitted the items list after retrying. "
            "The response indicated that another page exists; refresh the session "
            "or update the search request format."
        )

    async def iter_creator_references(
        self,
        creator: str,
        *,
        after_date: str | None = None,
    ) -> AsyncIterator[XhsNoteReference]:
        user_id, token, source = self._parse_creator(creator)
        profile_html = await self.client.get_creator_profile_html(user_id, token, source)
        html_token, html_source = _extract_security_from_html(profile_html)
        token = token or html_token
        source = source or html_source
        if not token:
            raise XhsClientError(
                "Creator profile did not expose an xsec_token; cannot request the creator feed."
            )

        cutoff = _parse_after_date(after_date)
        cursor = ""
        seen_cursors: set[str] = set()
        while True:
            response = await self.client.get_creator_notes(user_id, cursor, token, source)
            for item in _response_items(response):
                reference = _reference_from_list_item(item, XhsDiscoveryKind.CREATOR)
                if reference is None:
                    continue
                reference = XhsNoteReference(
                    note_id=reference.note_id,
                    source_url=reference.source_url,
                    xsec_token=reference.xsec_token or token,
                    xsec_source=reference.xsec_source or source,
                    published_at=reference.published_at,
                    discovery_kind=reference.discovery_kind,
                )
                if _is_older_than(reference.published_at, cutoff):
                    return
                yield reference

            if not _has_more(response):
                return
            next_cursor = _string(
                response.get("cursor") or response.get("next_cursor") or response.get("nextCursor")
            )
            if not next_cursor or next_cursor in seen_cursors:
                return
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    async def iter_favorite_references(
        self,
        *,
        after_date: str | None = None,
    ) -> AsyncIterator[XhsNoteReference]:
        """Yield notes saved by the authenticated account."""
        user_id = await self._get_favorite_user_id()
        cutoff = _parse_after_date(after_date)
        cursor = ""
        seen_cursors: set[str] = set()
        while True:
            response = await self.client.get_user_collect_notes(user_id, cursor)
            for item in _response_items(response):
                reference = _reference_from_list_item(item, XhsDiscoveryKind.FAVORITES)
                if reference is None:
                    continue
                if _is_older_than(reference.published_at, cutoff):
                    return
                yield reference
            if not _has_more(response):
                return
            next_cursor = _string(
                response.get("cursor") or response.get("next_cursor") or response.get("nextCursor")
            )
            if not next_cursor or next_cursor in seen_cursors:
                return
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    async def _get_favorite_user_id(self) -> str:
        if self._favorite_user_id:
            return self._favorite_user_id
        current_user = await self.client.get_current_user()
        user_id = _string(
            current_user.get("user_id")
            or current_user.get("userid")
            or current_user.get("id")
            or _first_mapping(current_user, ("user", "user_info", "profile")).get("user_id")
        )
        if not user_id:
            raise XhsDataFetchError(
                "Xiaohongshu did not return the logged-in user ID required to list favorite boards."
            )
        self._favorite_user_id = user_id
        return user_id

    async def resolve_note(self, reference: XhsNoteReference) -> XhsNoteInfo:
        """Resolve one note, preferring tokenless explicit HTML before signed APIs."""
        effective_reference = reference
        if reference.discovery_kind is XhsDiscoveryKind.EXPLICIT and not reference.xsec_token:
            raw, effective_reference = await self._html_note(effective_reference, authenticated=False)
            if raw:
                return _normalize_note(raw, effective_reference)
            raw, effective_reference = await self._html_note(effective_reference, authenticated=True)
            if raw:
                return _normalize_note(raw, effective_reference)
            if not effective_reference.xsec_token:
                raise XhsNoteUnavailableError(
                    f"Could not resolve note {reference.note_id}: page did not provide a note or security token."
                )

        try:
            detail = await self.client.get_note_detail(effective_reference)
            raw = _find_note_mapping(detail, effective_reference.note_id)
            if raw:
                return _normalize_note(raw, effective_reference)
        except (XhsAuthenticationError, XhsRiskControlError):
            raise
        except XhsClientError:
            # Detail endpoint unavailability is item-scoped; the web page is the
            # documented fallback before declaring this note unavailable.
            pass

        raw, effective_reference = await self._html_note(effective_reference, authenticated=True)
        if raw:
            return _normalize_note(raw, effective_reference)
        raise XhsNoteUnavailableError(
            f"Xiaohongshu note {reference.note_id} was unavailable from both detail API and HTML."
        )

    @staticmethod
    def _parse_note_reference(value: str) -> XhsNoteReference:
        if _IDENTIFIER.fullmatch(value):
            note_id = value
            query: dict[str, list[str]] = {}
        else:
            parsed = urlparse(value)
            match = _NOTE_PATH.search(parsed.path)
            if not match:
                raise ValueError(f"Unsupported Xiaohongshu note URL: {value}")
            note_id = match.group(1)
            query = parse_qs(parsed.query)
        return XhsNoteReference(
            note_id=note_id,
            source_url=f"{_WEB_ORIGIN}/explore/{note_id}",
            xsec_token=_first_query_value(query, "xsec_token"),
            xsec_source=_first_query_value(query, "xsec_source"),
            discovery_kind=XhsDiscoveryKind.EXPLICIT,
        )

    @staticmethod
    def _parse_creator(value: str) -> tuple[str, str, str]:
        cleaned = (value or "").strip()
        if _IDENTIFIER.fullmatch(cleaned):
            return cleaned, "", ""
        parsed = urlparse(cleaned)
        match = _CREATOR_PATH.search(parsed.path)
        if not match:
            raise ValueError(f"Unsupported Xiaohongshu creator URL or ID: {value}")
        query = parse_qs(parsed.query)
        return (
            match.group(1),
            _first_query_value(query, "xsec_token"),
            _first_query_value(query, "xsec_source"),
        )

    async def _html_note(
        self,
        reference: XhsNoteReference,
        *,
        authenticated: bool,
    ) -> tuple[dict[str, Any] | None, XhsNoteReference]:
        try:
            document = await self.client.get_note_html(reference, authenticated=authenticated)
        except (XhsAuthenticationError, XhsRiskControlError):
            raise
        except XhsClientError:
            return None, reference
        raw = _extract_note_from_html(reference.note_id, document)
        token, source = _extract_security_from_html(document)
        if token and not reference.xsec_token:
            reference = XhsNoteReference(
                note_id=reference.note_id,
                source_url=reference.source_url,
                xsec_token=token,
                xsec_source=source,
                published_at=reference.published_at,
                discovery_kind=reference.discovery_kind,
            )
        return raw, reference


def _first_query_value(query: dict[str, list[str]], name: str) -> str:
    values = query.get(name) or []
    return _string(values[0]) if values else ""


def _get_search_id() -> str:
    value = (int(time.time() * 1000) << 64) + int(random.uniform(0, 2_147_483_646))
    encoded = ""
    while value:
        value, remainder = divmod(value, len(_BASE36_ALPHABET))
        encoded = _BASE36_ALPHABET[remainder] + encoded
    return encoded or "0"


def _parse_after_date(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    except ValueError as exc:
        raise ValueError("after_date must use YYYY-MM-DD format.") from exc


def _is_older_than(timestamp: int | None, cutoff: int | None) -> bool:
    return timestamp is not None and cutoff is not None and timestamp < cutoff


def _has_more(response: dict[str, Any]) -> bool:
    value = response.get("has_more", response.get("hasMore", False))
    return value is True or value == 1 or value == "1"


def _response_items(response: dict[str, Any]) -> list[Any]:
    for key in ("items", "notes", "note_list", "data"):
        value = response.get(key)
        if isinstance(value, list):
            return value
    return []


def _reference_from_list_item(
    item: Any,
    discovery_kind: XhsDiscoveryKind,
) -> XhsNoteReference | None:
    if not isinstance(item, dict):
        return None
    if (
        discovery_kind is XhsDiscoveryKind.SEARCH
        and _string(item.get("model_type")).lower() in _SEARCH_NON_NOTE_MODEL_TYPES
    ):
        return None
    card = _note_card(item)
    note_id = _string(
        item.get("id")
        or item.get("note_id")
        or card.get("note_id")
        or card.get("id")
    )
    if not note_id:
        return None
    token = _string(item.get("xsec_token") or card.get("xsec_token"))
    source = _string(item.get("xsec_source") or card.get("xsec_source"))
    return XhsNoteReference(
        note_id=note_id,
        source_url=f"{_WEB_ORIGIN}/explore/{note_id}",
        xsec_token=token,
        xsec_source=source,
        published_at=_timestamp(
            item.get("publish_time")
            or item.get("time")
            or item.get("published_at")
            or card.get("publish_time")
            or card.get("time")
            or card.get("published_at")
        ),
        discovery_kind=discovery_kind,
    )


def _note_card(value: dict[str, Any]) -> dict[str, Any]:
    for key in ("note_card", "note", "note_info"):
        nested = value.get(key)
        if isinstance(nested, dict):
            return nested
    return value


def _timestamp(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        numeric = int(value)
        return numeric // 1000 if numeric > 10_000_000_000 else numeric
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return _timestamp(int(stripped))
        try:
            return int(datetime.fromisoformat(stripped.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


def _extract_note_from_html(note_id: str, document: str) -> dict[str, Any] | None:
    for payload in _json_objects_from_html(document):
        note = _find_note_mapping(_snake_case_keys(payload), note_id)
        if note:
            return note
    return None


def _extract_security_from_html(document: str) -> tuple[str, str]:
    for payload in _json_objects_from_html(document):
        token = _find_value(payload, "xsec_token")
        if token:
            return _string(token), _string(_find_value(payload, "xsec_source"))
    token_match = re.search(r'["\']xsec_token["\']\s*[:=]\s*["\']([^"\']+)', document)
    source_match = re.search(r'["\']xsec_source["\']\s*[:=]\s*["\']([^"\']+)', document)
    return (token_match.group(1) if token_match else "", source_match.group(1) if source_match else "")


def _json_objects_from_html(document: str) -> Iterable[Any]:
    decoder = json.JSONDecoder()
    for script in re.findall(r"<script\b[^>]*>(.*?)</script>", document or "", flags=re.I | re.S):
        initial_state = re.search(
            r"window\.__INITIAL_STATE__\s*=\s*(\{.*\})\s*$",
            script,
            flags=re.S,
        )
        if initial_state:
            try:
                yield json.loads(
                    re.sub(
                        r"(?<=[\[:,])\s*undefined(?=\s*[,}\]])",
                        "null",
                        initial_state.group(1),
                    )
                )
            except json.JSONDecodeError:
                pass
        for index, character in enumerate(script):
            if character not in "[{":
                continue
            try:
                payload, _ = decoder.raw_decode(script[index:])
            except json.JSONDecodeError:
                continue
            yield payload
            break


def _snake_case_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            _CAMEL_CASE_BOUNDARY.sub("_", key).lower(): _snake_case_keys(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_snake_case_keys(child) for child in value]
    return value


def _find_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_value(child, key)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_value(child, key)
            if found not in (None, ""):
                return found
    return None


def _find_note_mapping(value: Any, note_id: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        card = _note_card(value)
        candidate_id = _string(
            card.get("note_id") or card.get("id") or value.get("note_id") or value.get("id")
        )
        if candidate_id == note_id and any(
            key in card for key in ("title", "desc", "description", "image_list", "video", "user")
        ):
            return card
        for child in value.values():
            found = _find_note_mapping(child, note_id)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_note_mapping(child, note_id)
            if found:
                return found
    return None


def _normalize_note(raw: dict[str, Any], reference: XhsNoteReference) -> XhsNoteInfo:
    card = _note_card(raw)
    note_id = _string(card.get("note_id") or card.get("id")) or reference.note_id
    description = _normalize_text(card.get("desc") or card.get("description") or card.get("content"))
    title = _normalize_text(card.get("title")) or description.split("\n", 1)[0][:80] or note_id
    author = _first_mapping(card, ("user", "author", "user_info"))
    interact = _first_mapping(card, ("interact_info", "interact", "engagement"))
    return XhsNoteInfo(
        note_id=note_id,
        title=title,
        description=description,
        author_name=_normalize_text(
            author.get("nickname") or author.get("name") or author.get("user_name")
        ),
        author_id=_string(author.get("user_id") or author.get("userid") or author.get("id")),
        published_at=_timestamp(
            card.get("publish_time") or card.get("time") or card.get("published_at")
        ) or reference.published_at,
        note_type=_string(card.get("type") or card.get("note_type")),
        tags=_extract_tags(card),
        liked_count=_engagement_value(card, interact, "liked_count", "like_count"),
        collected_count=_engagement_value(card, interact, "collected_count", "collect_count"),
        comment_count=_engagement_value(card, interact, "comment_count"),
        image_urls=_extract_image_urls(card),
        video_urls=_extract_video_urls(card),
        source_url=f"{_WEB_ORIGIN}/explore/{note_id}",
        xsec_token=reference.xsec_token or _string(card.get("xsec_token")),
        xsec_source=reference.xsec_source or _string(card.get("xsec_source")),
    )


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = html.unescape(re.sub(r"<br\s*/?>", "\n", value, flags=re.I))
    return "\n".join(re.sub(r"[ \t\f\v]+", " ", line).strip() for line in value.splitlines() if line.strip())


def _string(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple)):
        return ""
    return str(value).strip()


def _first_mapping(value: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    for key in keys:
        nested = value.get(key)
        if isinstance(nested, dict):
            return nested
    return {}


def _engagement_value(card: dict[str, Any], interact: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = interact.get(key, card.get(key))
        if value is not None:
            return _string(value)
    return ""


def _extract_tags(raw: dict[str, Any]) -> tuple[str, ...]:
    tags: list[str] = []
    for key in ("tag_list", "tags", "topics", "topic_list"):
        values = raw.get(key)
        if isinstance(values, dict):
            values = [values]
        if not isinstance(values, list):
            continue
        for value in values:
            tag = _normalize_text(
                value if isinstance(value, str) else value.get("name") or value.get("title") or value.get("topic_name")
                if isinstance(value, dict) else ""
            ).lstrip("#＃")
            if tag and tag not in tags:
                tags.append(tag)
    return tuple(tags)


def _extract_image_urls(raw: dict[str, Any]) -> tuple[str, ...]:
    urls: list[str] = []
    images = raw.get("image_list") or raw.get("images") or raw.get("image_info_list") or []
    for image in images if isinstance(images, list) else []:
        for url in _urls_from_image(image):
            if url not in urls:
                urls.append(url)
    return tuple(urls)


def _urls_from_image(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        if value.startswith(("https://", "http://")):
            yield value
    elif isinstance(value, dict):
        for key in ("url_default", "url_pre", "url", "url_original"):
            if isinstance(value.get(key), str):
                yield value[key]
                return
        for child in value.values():
            yield from _urls_from_image(child)
    elif isinstance(value, list):
        for child in value:
            yield from _urls_from_image(child)


def _extract_video_urls(raw: dict[str, Any]) -> tuple[str, ...]:
    urls: list[str] = []

    def visit(value: Any, in_video: bool = False) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                video_context = in_video or any(
                    marker in key.lower() for marker in ("video", "stream", "master", "play", "media")
                )
                if video_context and isinstance(child, str) and (
                    key.lower().endswith("url") or "url" in key.lower()
                ) and child.startswith(("https://", "http://")):
                    if child not in urls:
                        urls.append(child)
                else:
                    visit(child, video_context)
        elif isinstance(value, list):
            for child in value:
                visit(child, in_video)

    for key in ("video", "video_info", "media", "stream"):
        if key in raw:
            visit(raw[key], True)
    return tuple(urls)
