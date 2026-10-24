"""Shared Xiaohongshu ingestion contracts."""

from dataclasses import dataclass, field, replace
from enum import Enum


class XhsDiscoveryKind(str, Enum):
    EXPLICIT = "explicit"
    SEARCH = "search"
    CREATOR = "creator"
    FAVORITES = "favorites"


_DISCOVERY_PRIORITY = {
    XhsDiscoveryKind.EXPLICIT: 3,
    XhsDiscoveryKind.SEARCH: 2,
    XhsDiscoveryKind.FAVORITES: 1,
    XhsDiscoveryKind.CREATOR: 1,
}


@dataclass(frozen=True)
class XhsNoteReference:
    note_id: str
    source_url: str = ""
    xsec_token: str = ""
    xsec_source: str = ""
    published_at: int | None = None
    discovery_kind: XhsDiscoveryKind = XhsDiscoveryKind.EXPLICIT


@dataclass(frozen=True)
class XhsNoteInfo:
    note_id: str
    title: str
    description: str = ""
    author_name: str = ""
    author_id: str = ""
    published_at: int | None = None
    note_type: str = ""
    tags: tuple[str, ...] = ()
    liked_count: str = ""
    collected_count: str = ""
    comment_count: str = ""
    image_urls: tuple[str, ...] = ()
    video_urls: tuple[str, ...] = ()
    source_url: str = ""
    xsec_token: str = ""
    xsec_source: str = ""


@dataclass
class XhsNoteContent:
    note: XhsNoteInfo
    image_paths: list[str] = field(default_factory=list)
    original_description: str = ""
    formatted_tags: str = ""
    transcript: str = ""
    content_source: str = "basic_info"
    summary_block: str = ""
    asr_raw_text: str = ""
    comments_section: str = ""
    warnings: list[str] = field(default_factory=list)


def _is_full_url(value: str) -> bool:
    return value.startswith(("https://", "http://"))


def merge_note_references(
    current: XhsNoteReference,
    incoming: XhsNoteReference,
) -> XhsNoteReference:
    """Merge duplicate references deterministically without losing metadata."""
    if current.note_id != incoming.note_id:
        raise ValueError("cannot merge references with different note IDs")

    preferred = max(
        (current, incoming),
        key=lambda item: (
            bool(item.xsec_token),
            _DISCOVERY_PRIORITY[item.discovery_kind],
        ),
    )
    other = incoming if preferred is current else current

    # A canonical URL is more useful than a bare ID even if its sibling supplied
    # the token selected by the discovery precedence above.
    source_url = next(
        (
            item.source_url
            for item in (preferred, other)
            if _is_full_url(item.source_url)
        ),
        preferred.source_url or other.source_url,
    )
    published_at = current.published_at if current.published_at is not None else incoming.published_at
    return replace(preferred, source_url=source_url, published_at=published_at)
