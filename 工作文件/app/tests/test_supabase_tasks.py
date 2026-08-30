from __future__ import annotations

import json
import unittest

import httpx

from app.services.supabase_tasks import SupabaseTaskBackend


class SupabaseTaskBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows: dict[str, dict] = {}
        self.calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            path = request.url.path
            if path == "/rest/v1/cloud_tasks" and request.method == "GET":
                params = dict(request.url.params)
                owner = str(params.get("owner_id", "")).removeprefix("eq.")
                idem = str(params.get("idempotency_key", "")).removeprefix("eq.")
                task_id = str(params.get("id", "")).removeprefix("eq.")
                values = list(self.rows.values())
                if owner:
                    values = [row for row in values if row["owner_id"] == owner]
                if idem:
                    values = [row for row in values if row["idempotency_key"] == idem]
                if task_id:
                    values = [row for row in values if row["id"] == task_id]
                return httpx.Response(200, json=values[:1])
            if path == "/rest/v1/cloud_tasks" and request.method == "POST":
                row = json.loads(request.content)
                if any(
                    existing["owner_id"] == row["owner_id"]
                    and existing["idempotency_key"] == row["idempotency_key"]
                    for existing in self.rows.values()
                ):
                    return httpx.Response(409, json={"message": "duplicate"})
                row.setdefault("result", None)
                row.setdefault("error", None)
                row.setdefault("worker_id", None)
                row.setdefault("lease_until", None)
                row.setdefault("retry_count", 0)
                row.setdefault("created_at", "2026-01-01T00:00:00Z")
                row.setdefault("updated_at", row["created_at"])
                row.setdefault("completed_at", None)
                self.rows[row["id"]] = row
                return httpx.Response(201, json=[row])
            if path.startswith("/rest/v1/rpc/") and request.method == "POST":
                name = path.rsplit("/", 1)[-1]
                payload = json.loads(request.content)
                if name == "claim_cloud_task":
                    queued = next(
                        (row for row in self.rows.values() if row["status"] in {"queued", "retryable"}),
                        None,
                    )
                    if not queued:
                        return httpx.Response(200, json=[])
                    queued["status"] = "processing"
                    queued["worker_id"] = payload["p_worker_id"]
                    return httpx.Response(200, json=[queued])
                task = self.rows.get(payload.get("p_task_id"))
                if not task or task.get("worker_id") != payload.get("p_worker_id"):
                    return httpx.Response(200, json=[])
                if name == "heartbeat_cloud_task":
                    return httpx.Response(200, json=[task])
                if name == "complete_cloud_task":
                    task["status"] = "completed"
                    task["result"] = payload["p_result"]
                    task["worker_id"] = None
                    return httpx.Response(200, json=[task])
                if name == "fail_cloud_task":
                    task["status"] = "failed"
                    task["error"] = payload["p_error"]
                    task["worker_id"] = None
                    return httpx.Response(200, json=[task])
            return httpx.Response(404, json={"message": "not found"})

        self.transport = httpx.MockTransport(handler)
        self.backend = SupabaseTaskBackend(
            project_url="https://example.supabase.co",
            service_key="service-key",
            client=httpx.Client(transport=self.transport, base_url="https://example.supabase.co/rest/v1"),
        )

    def tearDown(self) -> None:
        self.backend.close()

    def test_create_get_claim_complete_and_deduplicate(self) -> None:
        created = self.backend.create(
            owner_id="11111111-1111-1111-1111-111111111111",
            idempotency_key="same",
            payload={"url": "https://example.com/video"},
        )
        duplicate = self.backend.create(
            owner_id="11111111-1111-1111-1111-111111111111",
            idempotency_key="same",
            payload={"url": "https://example.com/video"},
        )
        self.assertFalse(created["deduplicated"])
        self.assertTrue(duplicate["deduplicated"])
        claimed = self.backend.claim_next(worker_id="worker-a")
        self.assertEqual(created["task_id"], claimed["task_id"])
        self.backend.heartbeat(created["task_id"], worker_id="worker-a")
        completed = self.backend.complete(
            created["task_id"], worker_id="worker-a", result={"status": "done"}
        )
        self.assertEqual("completed", completed["status"])
        self.assertEqual("done", self.backend.get(created["task_id"])["result"]["status"])


if __name__ == "__main__":
    unittest.main()
