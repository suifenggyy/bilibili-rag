import unittest
from types import SimpleNamespace


class MarkdownSummaryBlockTests(unittest.TestCase):
    def test_build_summary_block_strips_model_returned_yaml_fences(self):
        from app.services.content_summary import build_summary_block

        markdown = build_summary_block("```yaml\nsummary: |\n  重点\n```\n")

        self.assertEqual(markdown.count("```yaml"), 1)
        self.assertIn("summary: |\n  重点", markdown)

    def test_bilibili_markdown_places_summary_block_before_transcript(self):
        from scripts.export_favorites_to_md import _build_markdown

        markdown = _build_markdown(
            {
                "title": "视频标题",
                "bvid": "BV1test",
                "upper": {"name": "UP主"},
                "duration": 120,
            },
            "正文内容",
            "asr",
            "收藏夹",
            "<!-- AI_SUMMARY_START -->\n```yaml\ntags:\n  - bilibili\n```\n<!-- AI_SUMMARY_END -->",
        )

        self.assertLess(markdown.index("AI_SUMMARY_START"), markdown.index("## 转写内容"))

    def test_douyin_markdown_places_summary_block_before_transcript(self):
        from scripts.export_douyin_to_md import _build_markdown

        markdown = _build_markdown(
            SimpleNamespace(
                title="抖音标题",
                aweme_id="123",
                share_url="https://example.com",
                author="作者",
                duration=120000,
                create_time=0,
                cover_url="",
                content="正文内容",
                summary_block="<!-- AI_SUMMARY_START -->\n```yaml\ntags:\n  - douyin\n```\n<!-- AI_SUMMARY_END -->",
            ),
            "正文内容",
            "asr",
        )

        self.assertLess(markdown.index("AI_SUMMARY_START"), markdown.index("## 转写内容"))


if __name__ == "__main__":
    unittest.main()
