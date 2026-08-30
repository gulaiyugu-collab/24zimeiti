from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.services.cloud_control_plane import create_cloud_control_plane_app


class DomesticControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Path(self.temp_dir.name) / "cloud.sqlite3"
        with patch.dict("os.environ", {"PROJECT024_AUTH_SECRET": "test-auth-secret-0123456789-abcdefghijklmnopqrstuvwxyz"}):
            self.app = create_cloud_control_plane_app(
                domestic_mode=True,
                worker_token="worker-secret-0123456789",
                domestic_database_path=self.db,
            )
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def test_domestic_register_login_and_task_flow(self) -> None:
        registered = self.client.post("/api/auth/register", json={"email": "friend@example.com", "password": "password-123"})
        self.assertEqual(200, registered.status_code)
        login = self.client.post("/api/auth/login", json={"email": "friend@example.com", "password": "password-123"})
        self.assertEqual(200, login.status_code)
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        created = self.client.post("/api/cloud/tasks", headers=headers, json={"idempotency_key": "mobile-1", "payload": {"url": "https://www.douyin.com/video/1"}})
        self.assertEqual(201, created.status_code)
        listed = self.client.get("/api/cloud/tasks?limit=1", headers=headers)
        self.assertEqual(200, listed.status_code)
        self.assertEqual(created.json()["task_id"], listed.json()["tasks"][0]["task_id"])
        worker_headers = {"X-Worker-Id": "worker-a", "Authorization": "Bearer worker-secret-0123456789"}
        claimed = self.client.post("/workers/claim", headers=worker_headers)
        self.assertEqual(created.json()["task_id"], claimed.json()["task"]["task_id"])
        completed = self.client.post(f"/tasks/{created.json()['task_id']}/complete", headers=worker_headers, json={"result": {"status": "done"}})
        self.assertEqual("completed", completed.json()["status"])


if __name__ == "__main__":
    unittest.main()
