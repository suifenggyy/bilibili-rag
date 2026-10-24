import unittest

from app.services.xhs_models import (
    XhsDiscoveryKind,
    XhsNoteReference,
    merge_note_references,
)


class XhsReferenceMergeTests(unittest.TestCase):
    def test_explicit_url_token_wins_over_search_token(self):
        search = XhsNoteReference(
            note_id="abc",
            source_url="https://www.xiaohongshu.com/explore/abc",
            xsec_token="search-token",
            xsec_source="pc_search",
            discovery_kind=XhsDiscoveryKind.SEARCH,
        )
        explicit = XhsNoteReference(
            note_id="abc",
            source_url="https://www.xiaohongshu.com/explore/abc?xsec_token=url-token",
            xsec_token="url-token",
            xsec_source="pc_feed",
            discovery_kind=XhsDiscoveryKind.EXPLICIT,
        )

        merged = merge_note_references(search, explicit)

        self.assertEqual("url-token", merged.xsec_token)
        self.assertEqual(XhsDiscoveryKind.EXPLICIT, merged.discovery_kind)

    def test_first_timestamp_is_preserved_when_richer_reference_has_none(self):
        creator = XhsNoteReference(
            note_id="abc",
            published_at=1700000000,
            discovery_kind=XhsDiscoveryKind.CREATOR,
        )
        explicit = XhsNoteReference(
            note_id="abc",
            source_url="https://www.xiaohongshu.com/explore/abc",
            discovery_kind=XhsDiscoveryKind.EXPLICIT,
        )

        self.assertEqual(
            1700000000,
            merge_note_references(creator, explicit).published_at,
        )

    def test_different_ids_cannot_be_merged(self):
        with self.assertRaises(ValueError):
            merge_note_references(
                XhsNoteReference(note_id="one"),
                XhsNoteReference(note_id="two"),
            )

    def test_canonical_url_is_preserved_when_token_reference_is_bare(self):
        canonical = XhsNoteReference(
            note_id="abc",
            source_url="https://www.xiaohongshu.com/explore/abc",
            discovery_kind=XhsDiscoveryKind.CREATOR,
        )
        token_reference = XhsNoteReference(
            note_id="abc",
            source_url="abc",
            xsec_token="token",
            xsec_source="pc_search",
            discovery_kind=XhsDiscoveryKind.SEARCH,
        )

        merged = merge_note_references(canonical, token_reference)

        self.assertEqual("token", merged.xsec_token)
        self.assertEqual(canonical.source_url, merged.source_url)

    def test_search_token_precedes_creator_token(self):
        creator = XhsNoteReference(
            note_id="abc",
            xsec_token="creator",
            xsec_source="pc_feed",
            discovery_kind=XhsDiscoveryKind.CREATOR,
        )
        search = XhsNoteReference(
            note_id="abc",
            xsec_token="search",
            xsec_source="pc_search",
            discovery_kind=XhsDiscoveryKind.SEARCH,
        )

        self.assertEqual("search", merge_note_references(creator, search).xsec_token)


if __name__ == "__main__":
    unittest.main()
