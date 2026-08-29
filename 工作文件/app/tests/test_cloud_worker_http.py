from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx

from app.services.cloud_worker_http import create_cloud_worker_app
from app.services.cloud_worker_protocol import CloudTaskStore


class CloudWorkerHttpContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        backend = CloudTaskStore(Path(self.temp_dir.name) / "tasks.sqlite3")
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_cloud_worker_app(backend)),
            base_url="http://contract.test",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        self.temp_dir.cleanup()

    async def test_auth_and_owner_isolation(self) -> None:
        missing = await self.client.post("/tasks", json={"idempotency_key": "a"})
        self.assertEqual(missing.status_code, 401)
        created = await self.client.post(
            "/tasks",
            headers={"X-User-Id": "user-a"},
            json={"idempotency_key": "a", "payload": {"kind": "fixture"}},
        )
        self.assertEqual(created.status_code, 201)
        task_id = created.json()["task_id"]
        denied = await self.client.get(f"/tasks/{task_id}", headers={"X-User-Id": "user-b"})
        self.assertEqual(denied.status_code, 404)

    async def test_create_idempotency_and_single_claim(self) -> None:
        headers = {"X-User-Id": "user-a"}
        first = await self.client.post("/tasks", headers=headers, json={"idempotency_key": "same"})
        second = await self.client.post("/tasks", headers=headers, json={"idempotency_key": "same"})
        self.assertEqual(first.json()["task_id"], second.json()["task_id"])
        self.assertTrue(second.json()["deduplicated"])
        claimed = await self.client.post("/workers/claim", headers={"X-Worker-Id": "worker-a"})
        self.assertEqual(claimed.status_code, 200)
        self.assertEqual(claimed.json()["task"]["task_id"], first.json()["task_id"])
        other = await self.client.post("/workers/claim", headers={"X-Worker-Id": "worker-b"})
        self.assertIsNone(other.json()["task"])

    async def test_same_idempotency_key_isolated_between_users(self) -> None:
        first = await self.client.post(
            "/tasks", headers={"X-User-Id": "user-a"}, json={"idempotency_key": "shared"}
        )
        second = await self.client.post(
            "/tasks", headers={"X-User-Id": "user-b"}, json={"idempotency_key": "shared"}
        )
        self.assertEqual(201, first.status_code)
        self.assertEqual(201, second.status_code)
        self.assertNotEqual(first.json()["task_id"], second.json()["task_id"])
        self.assertEqual("user-b", second.json()["owner_id"])

    async def test_heartbeat_complete_and_duplicate_complete(self) -> None:
        created = await self.client.post(
            "/tasks", headers={"X-User-Id": "user-a"}, json={"idempotency_key": "flow"}
        )
        task_id = created.json()["task_id"]
        await self.client.post("/workers/claim", headers={"X-Worker-Id": "worker-a"})
        heartbeat = await self.client.post(
            f"/tasks/{task_id}/heartbeat",
            headers={"X-Worker-Id": "worker-a"},
            json={"lease_seconds": 30},
        )
        self.assertEqual(heartbeat.status_code, 200)
        completed = await self.client.post(
            f"/tasks/{task_id}/complete",
            headers={"X-Worker-Id": "worker-a"},
            json={"result": {"status": "fixture_completed"}},
        )
        duplicate = await self.client.post(
            f"/tasks/{task_id}/complete",
            headers={"X-Worker-Id": "worker-b"},
            json={"result": {"status": "wrong"}},
        )
        self.assertEqual(completed.status_code, 200)
        self.assertFalse(completed.json()["idempotent"])
        self.assertTrue(duplicate.json()["idempotent"])
        self.assertEqual(duplicate.json()["result"]["status"], "fixture_completed")

    async def test_wrong_worker_and_failure_retry_are_explicit(self) -> None:
        created = await self.client.post(
            "/tasks",
            headers={"X-User-Id": "user-a"},
            json={"idempotency_key": "fail", "max_retries": 0},
        )
        task_id = created.json()["task_id"]
        await self.client.post("/workers/claim", headers={"X-Worker-Id": "worker-a"})
        denied = await self.client.post(
            f"/tasks/{task_id}/heartbeat",
            headers={"X-Worker-Id": "worker-b"},
            json={},
        )
        self.assertEqual(denied.status_code, 409)
        failed = await self.client.post(
            f"/tasks/{task_id}/fail",
            headers={"X-Worker-Id": "worker-a"},
            json={"error": {"type": "fixture"}, "retryable": True},
        )
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()["status"], "failed")


if __name__ == "__main__":
    unittest.main()
