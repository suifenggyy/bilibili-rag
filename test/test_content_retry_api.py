"""Tests for content retry API endpoints."""
import unittest
from fastapi.testclient import TestClient


class ContentRetryAPITests(unittest.TestCase):
    def _make_client(self):
        from app.main import app
        # Use context manager to trigger lifespan (which calls init_db)
        return TestClient(app, raise_server_exceptions=False)

    def test_list_processing_records_returns_200(self):
        from app.main import app
        with TestClient(app) as client:
            resp = client.get("/api/processing/list?platform=bilibili&limit=5")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertIn("records", data)
            self.assertIsInstance(data["records"], list)

    def test_retry_correction_404_when_record_not_found(self):
        from app.main import app
        with TestClient(app) as client:
            resp = client.post(
                "/api/processing/bilibili/BVnotexist/retry",
                json={"stage": "correction"},
            )
            self.assertEqual(resp.status_code, 404)

    def test_retry_invalid_stage_returns_400_or_404(self):
        from app.main import app
        with TestClient(app) as client:
            resp = client.post(
                "/api/processing/bilibili/BVnotexist/retry",
                json={"stage": "invalid_stage"},
            )
            # Either 404 (record not found, checked first) or 400 (invalid stage)
            self.assertIn(resp.status_code, [400, 404])


if __name__ == "__main__":
    unittest.main()
