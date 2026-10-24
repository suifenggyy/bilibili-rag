import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.knowledge_pipeline.source_progress_logger import (
    SourceProgressLogger,
)


def _write(path: Path, body: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _log_text(source_dir: Path) -> str:
    return (source_dir / "_pipeline_progress.log").read_text(encoding="utf-8")


class SourceProgressLoggerTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_then_complete(self):
        with TemporaryDirectory() as tmpdir:
            inbox = Path(tmpdir) / "inbox"
            date_dir = inbox / "bilibili" / "2026-06-12"
            a = _write(date_dir / "A.md", "# A")
            b = _write(date_dir / "B.md", "# B")
            logger = SourceProgressLogger(inbox_root=inbox)

            await logger.record_processed(a)
            text = _log_text(inbox / "bilibili")
            self.assertIn("2026-06-12\n", text)
            self.assertIn("  - A.md\n", text)
            self.assertNotIn("  - B.md\n", text)

            await logger.record_processed(b)
            text = _log_text(inbox / "bilibili")
            # 完成后只剩裸日期行，不再列出文件。
            lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
            self.assertEqual(lines, ["2026-06-12"])

    async def test_skips_dateless_path(self):
        with TemporaryDirectory() as tmpdir:
            inbox = Path(tmpdir) / "inbox"
            inbox.mkdir()
            # 扁平文件：日期在文件名里、但路径中没有 YYYY-MM-DD 段。
            flat = _write(inbox / "2026-05-28-test.md", "# test")
            logger = SourceProgressLogger(inbox_root=inbox)

            await logger.record_processed(flat)  # 不应抛异常
            self.assertFalse((inbox / "_pipeline_progress.log").exists())

    async def test_skips_path_outside_inbox(self):
        with TemporaryDirectory() as tmpdir:
            inbox = Path(tmpdir) / "inbox"
            other = _write(Path(tmpdir) / "elsewhere" / "2026-06-12" / "X.md", "# x")
            logger = SourceProgressLogger(inbox_root=inbox)

            await logger.record_processed(other)
            self.assertFalse((inbox / "_pipeline_progress.log").exists())

    async def test_preserves_other_dates(self):
        with TemporaryDirectory() as tmpdir:
            inbox = Path(tmpdir) / "inbox"
            d1 = inbox / "douyin" / "2026-06-10"
            d2 = inbox / "douyin" / "2026-06-12"
            # 每个日期放两个文件，只处理一个 → 保持未完成态。
            a = _write(d1 / "A.md", "# A")
            _write(d1 / "B.md", "# B")
            c = _write(d2 / "C.md", "# C")
            _write(d2 / "D.md", "# D")
            logger = SourceProgressLogger(inbox_root=inbox)

            await logger.record_processed(a)
            await logger.record_processed(c)
            text = _log_text(inbox / "douyin")
            # 两个日期都保留，且按升序排列。
            self.assertLess(text.index("2026-06-10"), text.index("2026-06-12"))
            self.assertIn("  - A.md\n", text)
            self.assertIn("  - C.md\n", text)
            # 未处理的文件不应出现。
            self.assertNotIn("  - B.md\n", text)
            self.assertNotIn("  - D.md\n", text)

    async def test_incomplete_self_heals_when_new_file_appears(self):
        # 未完成态保留真实 processed 集合：新增文件后应被正确识别为待处理。
        with TemporaryDirectory() as tmpdir:
            inbox = Path(tmpdir) / "inbox"
            date_dir = inbox / "bilibili" / "2026-06-12"
            a = _write(date_dir / "A.md", "# A")
            _write(date_dir / "B.md", "# B")
            logger = SourceProgressLogger(inbox_root=inbox)

            await logger.record_processed(a)
            # 新增第三个文件 C（尚未处理），再记录一次 A：应仍为未完成，仅列出 A。
            _write(date_dir / "C.md", "# C")
            await logger.record_processed(a)
            text = _log_text(inbox / "bilibili")
            self.assertIn("  - A.md\n", text)
            self.assertNotIn("  - B.md\n", text)
            self.assertNotIn("  - C.md\n", text)
            lines = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
            self.assertIn("2026-06-12", lines)

    async def test_complete_stays_complete_after_new_file_processed(self):
        with TemporaryDirectory() as tmpdir:
            inbox = Path(tmpdir) / "inbox"
            date_dir = inbox / "bilibili" / "2026-06-12"
            a = _write(date_dir / "A.md", "# A")
            b = _write(date_dir / "B.md", "# B")
            logger = SourceProgressLogger(inbox_root=inbox)

            await logger.record_processed(a)
            await logger.record_processed(b)
            self.assertEqual(
                [ln for ln in _log_text(inbox / "bilibili").splitlines()
                 if ln and not ln.startswith("#")],
                ["2026-06-12"],
            )
            # 新增 C 并处理：应保持完成态（裸日期）。
            c = _write(date_dir / "C.md", "# C")
            await logger.record_processed(c)
            self.assertEqual(
                [ln for ln in _log_text(inbox / "bilibili").splitlines()
                 if ln and not ln.startswith("#")],
                ["2026-06-12"],
            )

    async def test_atomic_write_leaves_no_tmp_file(self):
        with TemporaryDirectory() as tmpdir:
            inbox = Path(tmpdir) / "inbox"
            date_dir = inbox / "bilibili" / "2026-06-12"
            a = _write(date_dir / "A.md", "# A")
            logger = SourceProgressLogger(inbox_root=inbox)

            await logger.record_processed(a)
            self.assertTrue((inbox / "bilibili" / "_pipeline_progress.log").exists())
            self.assertFalse(
                (inbox / "bilibili" / "_pipeline_progress.log.tmp").exists()
            )


if __name__ == "__main__":
    unittest.main()
