"""
Obsidian writer backend tests.
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


class ObsidianWriterTests(unittest.IsolatedAsyncioTestCase):
    async def test_filesystem_backend_skips_rest_and_writes_file(self):
        from app.services.knowledge_pipeline.obsidian_client import ObsidianWriter

        with TemporaryDirectory() as tmpdir:
            vault_root = Path(tmpdir)
            target = vault_root / "knowledge" / "note.md"

            writer = ObsidianWriter(
                vault_root=vault_root,
                rest_url="http://127.0.0.1:27124",
                api_key="token",
                write_backend="filesystem",
            )

            with patch("httpx.AsyncClient.put", side_effect=AssertionError("REST should not be called")):
                await writer.write_text("knowledge/note.md", "hello")

            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "hello")


if __name__ == "__main__":
    unittest.main()
