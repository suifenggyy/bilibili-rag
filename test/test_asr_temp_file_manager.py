import os
import tempfile
import time
import unittest
from pathlib import Path


class ASRTempFileManagerTests(unittest.TestCase):
    def test_build_path_uses_readable_title_in_filename(self):
        from app.services.asr_temp_file_manager import ASRTempFileManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ASRTempFileManager(base_dir=tmpdir)
            path = manager.build_path("测试 标题/abc", ".m4s", prefix="audio")

        self.assertIn("测试_标题_abc", Path(path).name)
        self.assertTrue(path.endswith(".m4s"))

    def test_write_result_creates_text_file_named_after_title(self):
        from app.services.asr_temp_file_manager import ASRTempFileManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ASRTempFileManager(base_dir=tmpdir)
            result_path = manager.write_result("我的视频标题", "纠错后的文本")

            self.assertTrue(Path(result_path).exists())
            self.assertEqual(Path(result_path).read_text(encoding="utf-8"), "纠错后的文本")
            self.assertIn("我的视频标题", Path(result_path).name)

    def test_cleanup_removes_only_files_older_than_three_days_when_size_exceeds_limit(self):
        from app.services.asr_temp_file_manager import ASRTempFileManager

        with tempfile.TemporaryDirectory() as tmpdir:
            manager = ASRTempFileManager(base_dir=tmpdir, max_total_size_bytes=20)
            old_file = Path(tmpdir) / "old.txt"
            recent_file = Path(tmpdir) / "recent.txt"
            old_file.write_bytes(b"x" * 12)
            recent_file.write_bytes(b"y" * 12)

            old_mtime = time.time() - 4 * 24 * 60 * 60
            os.utime(old_file, (old_mtime, old_mtime))

            manager.cleanup_if_needed()

            self.assertFalse(old_file.exists())
            self.assertTrue(recent_file.exists())


if __name__ == "__main__":
    unittest.main()
