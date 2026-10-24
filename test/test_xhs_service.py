import json
import unittest
from unittest.mock import AsyncMock

from app.services.xhs_client import XhsDataFetchError
from app.services.xhs_models import XhsDiscoveryKind, XhsNoteReference
from app.services.xhs import XhsService


class FakeClient:
    def __init__(self):
        self.search_pages = []
        self.creator_pages = []
        self.search_calls = []
        self.creator_calls = []
        self.favorite_calls = []
        self.favorite_pages = []
        self.current_user = {"user_id": "current-user"}
        self.detail = {}
        self.html = {}
        self.shared_urls = {}

    async def resolve_shared_url(self, url):
        return self.shared_urls[url]

    async def search_notes(self, keyword, page, page_size, search_id):
        self.search_calls.append((keyword, page, page_size, search_id))
        return self.search_pages.pop(0)

    async def get_creator_profile_html(self, user_id, xsec_token="", xsec_source=""):
        return self.html[("profile", user_id)]

    async def get_creator_notes(self, user_id, cursor, xsec_token, xsec_source):
        self.creator_calls.append((user_id, cursor, xsec_token, xsec_source))
        return self.creator_pages.pop(0)

    async def get_current_user(self):
        self.favorite_calls.append(("current_user",))
        return self.current_user

    async def get_user_collect_notes(self, user_id, cursor):
        self.favorite_calls.append(("collect", user_id, cursor))
        return self.favorite_pages.pop(0)

    async def get_note_detail(self, reference):
        return self.detail.get(reference.note_id, {})

    async def get_note_html(self, reference, *, authenticated):
        return self.html[(reference.note_id, authenticated)]


def note_card(note_id, *, timestamp=1_700_000_000, token="token"):
    return {
        "id": note_id,
        "xsec_token": token,
        "xsec_source": "pc_search",
        "note_card": {
            "note_id": note_id,
            "title": " A title ",
            "desc": " Line one \r\n Line two ",
            "time": timestamp,
            "user": {"nickname": "Author", "user_id": "user"},
            "tag_list": [{"name": "tag-one"}, {"name": "tag-two"}],
            "interact_info": {
                "liked_count": "12",
                "collected_count": "3",
                "comment_count": "4",
            },
            "image_list": [{"url_default": "https://img.example/one.jpg"}],
            "type": "normal",
        },
    }


class XhsServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = FakeClient()
        self.service = XhsService(self.client)

    async def test_parses_note_urls_shared_urls_and_bare_ids(self):
        self.client.shared_urls["https://xhslink.com/share"] = (
            "https://www.xiaohongshu.com/explore/shared?xsec_token=shared-token&xsec_source=pc_feed"
        )

        references = await self.service.references_from_urls(
            [
                "https://www.xiaohongshu.com/explore/full?xsec_token=url-token&xsec_source=pc_feed",
                "https://xhslink.com/share",
                "bare-note-id",
            ]
        )

        self.assertEqual(["full", "shared", "bare-note-id"], [item.note_id for item in references])
        self.assertEqual("url-token", references[0].xsec_token)
        self.assertEqual("pc_feed", references[0].xsec_source)
        self.assertEqual("shared-token", references[1].xsec_token)
        self.assertEqual(XhsDiscoveryKind.EXPLICIT, references[2].discovery_kind)

    async def test_rejects_invalid_explicit_reference(self):
        with self.assertRaises(ValueError):
            await self.service.references_from_urls(["https://www.example.com/not-a-note"])

    async def test_search_is_lazy_and_stops_on_old_usable_timestamp(self):
        self.client.search_pages = [
            {
                "items": [note_card("fresh"), note_card("unknown", timestamp=None)],
                "has_more": True,
                "search_id": "next",
            },
            {"items": [note_card("old", timestamp=1_600_000_000)], "has_more": True},
        ]
        found = []
        async for reference in self.service.iter_search_references(
            "test", after_date="2023-01-01"
        ):
            found.append(reference)

        self.assertEqual(["fresh", "unknown"], [item.note_id for item in found])
        self.assertEqual(2, len(self.client.search_calls))
        first_search_id = self.client.search_calls[0][3]
        self.assertTrue(first_search_id)
        self.assertRegex(first_search_id, r"^[0-9A-Z]+$")
        self.assertEqual("next", self.client.search_calls[1][3])

    async def test_search_reuses_generated_id_when_response_does_not_replace_it(self):
        self.client.search_pages = [
            {"items": [note_card("first")], "has_more": True},
            {"items": [note_card("second")], "has_more": False},
        ]

        found = [
            reference
            async for reference in self.service.iter_search_references("test")
        ]

        self.assertEqual(["first", "second"], [item.note_id for item in found])
        self.assertEqual(
            self.client.search_calls[0][3],
            self.client.search_calls[1][3],
        )

    async def test_empty_search_items_is_a_valid_zero_result(self):
        self.client.search_pages = [{"items": [], "has_more": False}]

        found = [
            reference
            async for reference in self.service.iter_search_references("test")
        ]

        self.assertEqual([], found)

    async def test_search_skips_non_note_recommendation_rows(self):
        recommendation = {
            "id": "4bf174cb-e835-4b1c-8f2b-2f449c9fe24b#1786331328360",
            "model_type": "rec_query",
        }
        hot_query = {"id": "hot-query-id", "model_type": "hot_query"}
        self.client.search_pages = [
            {"items": [recommendation, hot_query, note_card("note")], "has_more": False}
        ]

        found = [
            reference
            async for reference in self.service.iter_search_references("test")
        ]

        self.assertEqual(["note"], [item.note_id for item in found])

    async def test_search_stops_when_final_page_omits_items(self):
        self.client.search_pages = [
            {"items": [note_card("first")], "has_more": True},
            {"has_more": False},
        ]

        found = [
            reference
            async for reference in self.service.iter_search_references("test")
        ]

        self.assertEqual(["first"], [item.note_id for item in found])
        self.assertEqual(2, len(self.client.search_calls))

    async def test_search_response_without_items_and_with_more_pages_is_rejected(self):
        self.client.search_pages = [{"has_more": True}, {"has_more": True}]

        with self.assertRaisesRegex(XhsDataFetchError, "omitted the items list after retrying"):
            async for _ in self.service.iter_search_references("test"):
                pass
        self.assertEqual(2, len(self.client.search_calls))

    async def test_search_retries_an_incomplete_response_once(self):
        self.client.search_pages = [
            {"has_more": True},
            {"items": [note_card("recovered")], "has_more": False},
        ]

        found = [
            reference
            async for reference in self.service.iter_search_references("test")
        ]

        self.assertEqual(["recovered"], [item.note_id for item in found])
        self.assertEqual(2, len(self.client.search_calls))
        self.assertEqual(
            self.client.search_calls[0][3],
            self.client.search_calls[1][3],
        )

    async def test_breaking_lazy_search_does_not_request_another_page(self):
        self.client.search_pages = [
            {"items": [note_card("first")], "has_more": True, "search_id": "next"},
            {"items": [note_card("second")], "has_more": False},
        ]
        iterator = self.service.iter_search_references("test")
        first = await anext(iterator)
        await iterator.aclose()

        self.assertEqual("first", first.note_id)
        self.assertEqual(1, len(self.client.search_calls))

    async def test_creator_url_and_pagination_carry_cursor(self):
        self.client.html[("profile", "creator-id")] = (
            '<script>{"xsec_token":"profile-token","xsec_source":"pc_user"}</script>'
        )
        self.client.creator_pages = [
            {"notes": [note_card("one")], "has_more": True, "cursor": "cursor-two"},
            {"notes": [note_card("two")], "has_more": False, "cursor": ""},
        ]
        found = [
            reference
            async for reference in self.service.iter_creator_references(
                "https://www.xiaohongshu.com/user/profile/creator-id"
            )
        ]

        self.assertEqual(["one", "two"], [item.note_id for item in found])
        self.assertEqual(
            [
                ("creator-id", "", "profile-token", "pc_user"),
                ("creator-id", "cursor-two", "profile-token", "pc_user"),
            ],
            self.client.creator_calls,
        )

    async def test_favorite_notes_paginate_with_cursor(self):
        self.client.favorite_pages = [
            {"items": [note_card("one")], "has_more": True, "cursor": "next-note"},
            {"items": [note_card("two")], "has_more": False},
        ]

        references = [
            reference
            async for reference in self.service.iter_favorite_references()
        ]

        self.assertEqual(["one", "two"], [reference.note_id for reference in references])
        self.assertEqual(
            [
                ("current_user",),
                ("collect", "current-user", ""),
                ("collect", "current-user", "next-note"),
            ],
            self.client.favorite_calls,
        )

    async def test_detail_normalization_handles_images_and_video(self):
        reference = XhsNoteReference(note_id="detail", xsec_token="token", xsec_source="pc_feed")
        raw = note_card("detail")["note_card"]
        raw["type"] = "video"
        raw["video"] = {"media": {"stream": {"h264": [{"master_url": "https://video.example/movie.mp4"}]}}}
        self.client.detail["detail"] = {"items": [{"note_card": raw}]}

        note = await self.service.resolve_note(reference)

        self.assertEqual("A title", note.title)
        self.assertEqual("Line one\nLine two", note.description)
        self.assertEqual(("tag-one", "tag-two"), note.tags)
        self.assertEqual(("https://img.example/one.jpg",), note.image_urls)
        self.assertEqual(("https://video.example/movie.mp4",), note.video_urls)
        self.assertEqual("12", note.liked_count)

    async def test_empty_detail_falls_back_to_html(self):
        reference = XhsNoteReference(note_id="html-note", xsec_token="token", xsec_source="pc_feed")
        self.client.detail["html-note"] = {}
        self.client.html[("html-note", True)] = (
            '<script id="__INITIAL_STATE__" type="application/json">'
            + json.dumps({"note": note_card("html-note")["note_card"]})
            + "</script>"
        )

        note = await self.service.resolve_note(reference)

        self.assertEqual("html-note", note.note_id)
        self.assertEqual("A title", note.title)

    async def test_html_fallback_parses_camel_case_initial_state(self):
        reference = XhsNoteReference(note_id="state-note", xsec_token="token", xsec_source="pc_search")
        self.client.detail["state-note"] = {}
        state_note = {
            "noteId": "state-note",
            "title": "State title",
            "desc": "State description",
            "type": "normal",
            "user": {"nickname": "State author"},
            "imageList": [],
        }
        state = json.dumps(
            {
                "note": {
                    "noteDetailMap": {
                        "state-note": {
                            "note": state_note,
                            "unused": "undefined",
                        }
                    }
                }
            }
        ).replace('"undefined"', "undefined")
        self.client.html[("state-note", True)] = (
            "<script>window.__INITIAL_STATE__=" + state + "</script>"
        )

        note = await self.service.resolve_note(reference)

        self.assertEqual("state-note", note.note_id)
        self.assertEqual("State title", note.title)
        self.assertEqual("State description", note.description)
        self.assertEqual("State author", note.author_name)

    async def test_tokenless_explicit_note_tries_public_then_authenticated_html(self):
        reference = XhsNoteReference(
            note_id="tokenless",
            source_url="https://www.xiaohongshu.com/explore/tokenless",
        )
        self.client.html[("tokenless", False)] = "<html>login</html>"
        self.client.html[("tokenless", True)] = (
            "<script>" + json.dumps({"note": note_card("tokenless")["note_card"]}) + "</script>"
        )
        self.client.get_note_detail = AsyncMock()

        note = await self.service.resolve_note(reference)

        self.assertEqual("tokenless", note.note_id)
        self.client.get_note_detail.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
