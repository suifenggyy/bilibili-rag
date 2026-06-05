"""
知识库流水线配置测试
"""
import unittest


class KnowledgePipelineConfigTests(unittest.TestCase):
    def test_settings_expose_obsidian_and_tavily_fields(self):
        from app.config import Settings

        s = Settings(
            obsidian_vault_root="/tmp/vault",
            obsidian_inbox_dir="inbox",
            obsidian_local_rest_url="http://127.0.0.1:27124",
            obsidian_local_rest_api_key="token",
            obsidian_write_backend="obsidian_api",
            tavily_api_key="tvly-test",
        )

        self.assertEqual(s.obsidian_vault_root, "/tmp/vault")
        self.assertEqual(s.obsidian_inbox_dir, "inbox")
        self.assertEqual(s.obsidian_local_rest_url, "http://127.0.0.1:27124")
        self.assertEqual(s.obsidian_write_backend, "obsidian_api")
        self.assertEqual(s.tavily_api_key, "tvly-test")

    def test_settings_default_obsidian_write_backend_uses_plugin(self):
        from app.config import Settings

        s = Settings()
        self.assertEqual(s.obsidian_write_backend, "obsidian_api")

    def test_content_storage_manager_exposes_vault_helpers(self):
        from app.services.content_storage import ContentStorageManager
        from pathlib import Path

        mgr = ContentStorageManager(vault_root="/tmp/vault")
        self.assertEqual(mgr.get_vault_root(), Path("/tmp/vault"))
        self.assertEqual(mgr.get_inbox_dir(), Path("/tmp/vault/inbox"))
        self.assertEqual(mgr.get_knowledge_dir(), Path("/tmp/vault/knowledge"))
        self.assertEqual(mgr.get_topics_dir(), Path("/tmp/vault/knowledge/_topics"))
        self.assertEqual(mgr.get_daily_dir(), Path("/tmp/vault/daily"))
        self.assertEqual(mgr.get_meta_dir(), Path("/tmp/vault/_meta"))

    def test_content_storage_manager_inbox_dir_respects_settings(self):
        from app.services.content_storage import ContentStorageManager
        from pathlib import Path

        mgr = ContentStorageManager(vault_root="/tmp/vault", inbox_dir="input")
        self.assertEqual(mgr.get_inbox_dir(), Path("/tmp/vault/input"))

    def test_content_storage_manager_legacy_collection_dir(self):
        from app.services.content_storage import ContentStorageManager
        from pathlib import Path
        from datetime import date

        mgr = ContentStorageManager(export_root="/tmp/collection")
        result = mgr.get_legacy_collection_dir("bilibili", date(2026, 5, 28))
        self.assertEqual(result, Path("/tmp/collection/bilibili/2026-05-28"))


if __name__ == "__main__":
    unittest.main()

class KnowledgePipelineConfigTests(unittest.TestCase):
    def test_settings_expose_hierarchical_prompt_fields(self):
        from app.config import Settings

        s = Settings(_env_file=None)
        self.assertIn("primary_path", s.knowledge_topic_path_prompt)
        self.assertIn("summary", s.knowledge_note_distill_prompt)
        self.assertIn("rewrite_summary", s.knowledge_topic_summary_decision_prompt)
        self.assertIn("## 概览", s.knowledge_topic_summary_prompt)
        self.assertIn("detail_items", s.knowledge_topic_detail_prompt)
        self.assertIn("repair_actions", s.knowledge_repair_prompt)

    def test_settings_allow_env_override_for_hierarchical_prompts(self):
        import os
        from unittest.mock import patch
        from app.config import Settings

        with patch.dict(os.environ, {
            "KNOWLEDGE_TOPIC_PATH_PROMPT": "override-path",
            "KNOWLEDGE_MIN_BODY_CHARS": "42",
        }, clear=False):
            s = Settings(_env_file=None)
        self.assertEqual(s.knowledge_topic_path_prompt, "override-path")
        self.assertEqual(s.knowledge_min_body_chars, 42)
