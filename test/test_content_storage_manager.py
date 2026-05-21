import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path


class ContentStorageManagerTests(unittest.TestCase):
    def test_work_dir_uses_source_date_and_title_layers(self):
        from app.services.content_storage import ContentStorageManager

        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as output_dir:
            manager = ContentStorageManager(
                workspace_root=workspace_dir,
                export_root=output_dir,
                max_total_size_bytes=1024,
                retention_days=3,
            )

            work_dir = manager.get_work_dir(
                source="bilibili",
                title="测试 标题/abc",
                day=date(2026, 5, 17),
            )

            self.assertTrue(work_dir.exists())
            self.assertEqual(
                work_dir,
                Path(workspace_dir) / "bilibili" / "2026-05-17" / "测试 标题_abc",
            )

    def test_markdown_path_uses_source_date_and_title_filename(self):
        from app.services.content_storage import ContentStorageManager

        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as output_dir:
            manager = ContentStorageManager(
                workspace_root=workspace_dir,
                export_root=output_dir,
                max_total_size_bytes=1024,
                retention_days=3,
            )

            md_path = manager.build_markdown_path(
                source="douyin",
                title="这是一个标题",
                identifier="123456",
                day=date(2026, 5, 17),
            )

            self.assertEqual(
                md_path,
                Path(output_dir) / "douyin" / "2026-05-17" / "这是一个标题_123456.md",
            )

    def test_cleanup_removes_only_files_older_than_retention_when_workspace_exceeds_limit(self):
        from app.services.content_storage import ContentStorageManager

        with tempfile.TemporaryDirectory() as workspace_dir, tempfile.TemporaryDirectory() as output_dir:
            manager = ContentStorageManager(
                workspace_root=workspace_dir,
                export_root=output_dir,
                max_total_size_bytes=20,
                retention_days=3,
            )

            old_file = manager.get_work_dir("bilibili", "旧内容", day=date(2026, 5, 10)) / "audio.m4s"
            recent_file = manager.get_work_dir("bilibili", "新内容", day=date(2026, 5, 17)) / "audio.m4s"
            old_file.write_bytes(b"x" * 12)
            recent_file.write_bytes(b"y" * 12)

            old_mtime = time.time() - 4 * 24 * 60 * 60
            os.utime(old_file, (old_mtime, old_mtime))

            manager.cleanup_workspace_if_needed(now=time.time())

            self.assertFalse(old_file.exists())
            self.assertTrue(recent_file.exists())


if __name__ == "__main__":
    unittest.main()
