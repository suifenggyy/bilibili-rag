import unittest
from typing import Dict, List, Any
from app.services.knowledge_pipeline.topic_page_renderer import TopicPageRenderer

class TopicPageRendererTests(unittest.TestCase):
    def test_topic_renderer_separates_summary_and_details(self):
        payload = {
            "topic_id": "topic-1",
            "topic_path": ["技术", "编程", "Python"],
            "summary_content": "Python是一种高级编程语言，设计哲学强调代码的可读性。",
            "subtopics": [
                {"id": "topic-1-1", "name": "并发", "path": ["技术", "编程", "Python", "并发"], "description": "Python中的并发处理"}
            ],
            "knowledge_notes": [
                {"id": "note-1", "path": "/path/to/note-1.md", "title": "asyncio入门"}
            ],
            "status": "active"
        }
        markdown = TopicPageRenderer().render(payload)
        
        self.assertIn("## 概览", markdown)
        self.assertIn("## 详情积累", markdown)
        self.assertIn("Python是一种高级编程语言", markdown)
        self.assertIn("asyncio入门", markdown)
        self.assertIn("并发", markdown)

    def test_topic_renderer_handles_empty_fields(self):
        payload = {
            "topic_id": "topic-2",
            "topic_path": ["空主题"],
            "summary_content": "",
            "subtopics": [],
            "knowledge_notes": [],
            "status": "active"
        }
        markdown = TopicPageRenderer().render(payload)
        
        self.assertIn("## 概览", markdown)
        self.assertIn("暂无概览信息", markdown)
        self.assertIn("## 详情积累", markdown)
        self.assertIn("暂无相关知识笔记", markdown)
