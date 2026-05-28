"""
导出脚本 inbox frontmatter 测试
"""
import unittest


class ExportInboxFrontmatterTests(unittest.TestCase):
    def test_build_frontmatter_contains_required_fields(self):
        from app.services.knowledge_pipeline.frontmatter import build_export_frontmatter

        frontmatter = build_export_frontmatter(
            title="文章标题",
            date_str="2026-05-28",
            source="https://example.com/1",
            summary="一句摘要",
        )

        self.assertTrue(frontmatter.startswith("---\n"))
        self.assertIn("title: 文章标题", frontmatter)
        self.assertIn("date: 2026-05-28", frontmatter)
        self.assertIn("source: https://example.com/1", frontmatter)
        self.assertIn("summary: 一句摘要", frontmatter)

    def test_build_frontmatter_ends_with_separator_and_blank(self):
        from app.services.knowledge_pipeline.frontmatter import build_export_frontmatter

        frontmatter = build_export_frontmatter(
            title="T",
            date_str="2026-01-01",
            source="https://x.com",
            summary="",
        )
        self.assertTrue(frontmatter.endswith("---\n\n"))

    def test_build_frontmatter_escapes_title_with_colon(self):
        from app.services.knowledge_pipeline.frontmatter import build_export_frontmatter

        frontmatter = build_export_frontmatter(
            title="标题: 副标题",
            date_str="2026-05-28",
            source="https://example.com",
            summary="",
        )
        # Title containing colon must be quoted
        self.assertIn('title: "标题: 副标题"', frontmatter)

    def test_build_frontmatter_normalizes_multiline_summary(self):
        from app.services.knowledge_pipeline.frontmatter import build_export_frontmatter

        frontmatter = build_export_frontmatter(
            title="T",
            date_str="2026-05-28",
            source="https://example.com",
            summary="第一行\n第二行",
        )
        # summary must be single line in frontmatter
        self.assertNotIn("\n第二行", frontmatter)

    def test_extract_plain_summary_from_yaml_block(self):
        from app.services.knowledge_pipeline.frontmatter import extract_plain_summary

        summary_block = """<!-- AI_SUMMARY_START -->
```yaml
summary: |
  这是一段摘要
  第二行
key_points:
  - 要点1
tags:
  - 标签1
```
<!-- AI_SUMMARY_END -->"""
        result = extract_plain_summary(summary_block)
        self.assertIn("这是一段摘要", result)

    def test_extract_plain_summary_returns_empty_when_no_block(self):
        from app.services.knowledge_pipeline.frontmatter import extract_plain_summary

        result = extract_plain_summary("")
        self.assertEqual(result, "")

    def test_bilibili_export_markdown_places_frontmatter_before_body(self):
        from scripts.export_favorites_to_md import _build_markdown

        video = {
            "title": "测试视频",
            "bvid": "BV1xx411c7mn",
            "upper": {"name": "测试UP主"},
            "duration": 120,
            "cover": "",
            "intro": "简介",
            "pubtime": 1748390400,
        }
        markdown = _build_markdown(
            video=video,
            asr_text="正文内容",
            source="asr",
            folder_title="收藏夹",
            summary_block="",
        )
        self.assertTrue(markdown.startswith("---\n"))
        self.assertIn("\n# ", markdown)

    def test_bilibili_export_markdown_contains_source_url(self):
        from scripts.export_favorites_to_md import _build_markdown

        video = {
            "title": "测试视频",
            "bvid": "BV1xx411c7mn",
            "upper": {"name": "UP主"},
            "duration": 60,
            "cover": "",
            "pubtime": 1748390400,
        }
        markdown = _build_markdown(
            video=video,
            asr_text="",
            source="basic_info",
            folder_title="收藏夹",
            summary_block="",
        )
        self.assertIn("source:", markdown)
        self.assertIn("bilibili.com", markdown)


if __name__ == "__main__":
    unittest.main()
