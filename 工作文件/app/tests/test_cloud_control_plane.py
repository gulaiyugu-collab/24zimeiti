from __future__ import annotations

import time
import unittest

import jwt
from fastapi.testclient import TestClient

from app.services.cloud_control_plane import create_cloud_control_plane_app
from app.services.cloud_worker_protocol import CloudTaskStore
from app.services.supabase_auth import SupabaseJWTAuthenticator


SECRET = "test-secret-0123456789-abcdefghijkl"
USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"


def token(user_id: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": user_id,
            "aud": "authenticated",
            "iss": "https://example.supabase.co/auth/v1",
            "iat": now,
            "exp": now + 300,
        },
        SECRET,
        algorithm="HS256",
    )


class CloudControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.temp_dir = tempfile.TemporaryDirectory()
        backend = CloudTaskStore(f"{self.temp_dir.name}/tasks.sqlite3")
        app = create_cloud_control_plane_app(
            backend=backend,
            authenticator=SupabaseJWTAuthenticator(
                project_url="https://example.supabase.co", jwt_secret=SECRET
            ),
            worker_token="worker-secret-0123456789",
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def test_user_auth_and_owner_isolation(self) -> None:
        no_auth = self.client.post("/api/cloud/tasks", json={"idempotency_key": "a"})
        self.assertEqual(401, no_auth.status_code)
        auth_a = {"Authorization": f"Bearer {token(USER_A)}"}
        created = self.client.post(
            "/api/cloud/tasks",
            headers=auth_a,
            json={"idempotency_key": "a", "payload": {"url": "https://example.com"}},
        )
        self.assertEqual(201, created.status_code)
        task_id = created.json()["task_id"]
        auth_b = {"Authorization": f"Bearer {token(USER_B)}"}
        denied = self.client.get(f"/api/cloud/tasks/{task_id}", headers=auth_b)
        self.assertEqual(404, denied.status_code)

    def test_worker_requires_bearer_token_and_completes_task(self) -> None:
        auth_a = {"Authorization": f"Bearer {token(USER_A)}"}
        created = self.client.post(
            "/api/cloud/tasks",
            headers=auth_a,
            json={"idempotency_key": "flow"},
        )
        task_id = created.json()["task_id"]
        missing = self.client.post(
            "/workers/claim", headers={"X-Worker-Id": "worker-a"}
        )
        self.assertEqual(401, missing.status_code)
        worker_headers = {
            "X-Worker-Id": "worker-a",
            "Authorization": "Bearer worker-secret-0123456789",
        }
        claimed = self.client.post("/workers/claim", headers=worker_headers)
        self.assertEqual(task_id, claimed.json()["task"]["task_id"])
        completed = self.client.post(
            f"/tasks/{task_id}/complete",
            headers=worker_headers,
            json={"result": {"status": "done"}},
        )
        self.assertEqual("completed", completed.json()["status"])
        visible = self.client.get(f"/api/cloud/tasks/{task_id}", headers=auth_a)
        self.assertEqual("done", visible.json()["result"]["status"])

    def test_browser_entry_and_public_config_do_not_expose_secret(self) -> None:
        config = self.client.get("/api/cloud/config")
        self.assertEqual(200, config.status_code)
        self.assertNotIn("secret", config.text.lower())
        page = self.client.get("/cloud")
        self.assertEqual(200, page.status_code)
        self.assertIn("自媒体通关助手", page.text)
        self.assertEqual("no-store", page.headers.get("cache-control"))
        script = self.client.get("/static/cloud.js")
        self.assertEqual(200, script.status_code)
        self.assertIn("signupButton", script.text)
        self.assertIn("project024-cloud-task-id", script.text)
        self.assertIn("savedTaskId", script.text)
        self.assertIn("/api/cloud/tasks?limit=1", script.text)
        self.assertIn("formatTaskResult", script.text)
        self.assertIn("loadHistory", script.text)
        self.assertIn("history-item", page.text)
        self.assertIn("application/javascript", script.headers.get("content-type", ""))
        self.assertEqual("no-store", script.headers.get("cache-control"))
        self.assertEqual(200, self.client.get("/static/agent-panel.js").status_code)
        self.assertEqual(200, self.client.get("/static/styles.css").status_code)
        self.assertEqual(200, self.client.get("/api/agent/status").status_code)


if __name__ == "__main__":
    unittest.main()
