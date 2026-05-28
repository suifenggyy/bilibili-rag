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
            tavily_api_key="tvly-test",
        )

        self.assertEqual(s.obsidian_vault_root, "/tmp/vault")
        self.assertEqual(s.obsidian_inbox_dir, "inbox")
        self.assertEqual(s.obsidian_local_rest_url, "http://127.0.0.1:27124")
        self.assertEqual(s.tavily_api_key, "tvly-test")

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
