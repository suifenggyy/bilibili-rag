import unittest
from app.services.knowledge_pipeline.parser import KnowledgeMarkdownParser

class KnowledgeParserV2Tests(unittest.TestCase):
    def test_parser_extracts_summary_and_key_points(self):
        md = """---
title: T
date: 2026-06-03
source: https://x.com
summary: one line
key_points:
  - p1
  - p2
---

正文"""
        doc = KnowledgeMarkdownParser().parse_text(md)
        self.assertEqual(doc.summary, "one line")
        self.assertEqual(doc.key_points, ["p1", "p2"])

class KnowledgeParserCompatibilityTests(unittest.TestCase):
    def test_parser_keeps_existing_field_contract(self):
        md = """---
title: T
date: 2026-06-03
source: https://x.com
summary: one line
key_points:
  - p1
---

正文"""
        doc = KnowledgeMarkdownParser().parse_text(md)
        self.assertEqual(doc.date_str, "2026-06-03")
        self.assertEqual(doc.source_url, "https://x.com")
        self.assertEqual(doc.body.strip(), "正文")
        self.assertEqual(doc.raw_frontmatter["key_points"], ["p1"])
