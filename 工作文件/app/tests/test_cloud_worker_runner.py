from __future__ import annotations

import threading
import tempfile
import unittest
from pathlib import Path

import httpx

from app.services.cloud_worker_runner import CloudWorkerRunner, HttpCloudWorkerClient, WorkerSettings, _safe_worker_id


class FakeClient:
    def __init__(self, task: dict | None) -> None:
        self.task = task
        self.events: list[tuple[str, str]] = []

    def claim(self, *, lease_seconds: int) -> dict | None:
        self.events.append(("claim", str(lease_seconds)))
        task, self.task = self.task, None
        return task

    def heartbeat(self, task_id: str, *, lease_seconds: int) -> dict:
        self.events.append(("heartbeat", task_id))
        return {"task_id": task_id}

    def complete(self, task_id: str, *, result: dict) -> dict:
        self.events.append(("complete", task_id))
        self.result = result
        return {"status": "completed"}

    def fail(self, task_id: str, *, error: dict, retryable: bool) -> dict:
        self.events.append(("fail", task_id))
        self.error = error
        self.retryable = retryable
        return {"status": "failed"}


class CloudWorkerRunnerTests(unittest.TestCase):
    def test_local_executor_accepts_string_root(self) -> None:
        from app.services.cloud_worker_runner import make_local_acquisition_executor

        with tempfile.TemporaryDirectory() as temp_dir:
            executor = make_local_acquisition_executor(temp_dir)
            self.assertTrue(callable(executor))
            self.assertTrue((Path(temp_dir) / "jobs").is_dir())

    def test_worker_id_is_safe_for_http_headers(self) -> None:
        self.assertEqual("worker-local", _safe_worker_id("家用电脑"))
        self.assertEqual("worker-01", _safe_worker_id("worker-01"))
        self.assertEqual("worker-local", _safe_worker_id("中文"))

    def test_idle_does_not_execute(self) -> None:
        client = FakeClient(None)
        runner = CloudWorkerRunner(client, executor=lambda payload: self.fail("must not execute"))
        self.assertEqual("idle", runner.run_once())
        self.assertEqual([("claim", "120")], client.events)

    def test_success_claim_execute_complete(self) -> None:
        client = FakeClient({"task_id": "ct-1", "payload": {"url": "https://example.com"}})
        runner = CloudWorkerRunner(client, executor=lambda payload: {"ok": payload["url"]})
        self.assertEqual("completed", runner.run_once())
        self.assertEqual([("claim", "120"), ("complete", "ct-1")], client.events)
        self.assertEqual({"ok": "https://example.com"}, client.result)

    def test_executor_failure_reports_retryable_failure(self) -> None:
        client = FakeClient({"task_id": "ct-2", "payload": {}})

        def execute(payload: dict) -> dict:
            raise ValueError("bad payload")

        runner = CloudWorkerRunner(client, executor=execute)
        self.assertEqual("failed", runner.run_once())
        self.assertEqual([("claim", "120"), ("fail", "ct-2")], client.events)
        self.assertEqual("ValueError", client.error["type"])
        self.assertTrue(client.retryable)

    def test_invalid_payload_is_non_retryable(self) -> None:
        client = FakeClient({"task_id": "ct-3", "payload": "not-an-object"})
        runner = CloudWorkerRunner(client, executor=lambda payload: {})
        self.assertEqual("invalid", runner.run_once())
        self.assertEqual([("claim", "120"), ("fail", "ct-3")], client.events)
        self.assertFalse(client.retryable)

    def test_settings_reject_heartbeat_longer_than_lease(self) -> None:
        with self.assertRaises(ValueError):
            CloudWorkerRunner(
                FakeClient(None),
                executor=lambda payload: {},
                settings=WorkerSettings(lease_seconds=10, heartbeat_seconds=10),
            )

    def test_run_forever_can_be_stopped(self) -> None:
        stop = threading.Event()
        client = FakeClient({"task_id": "ct-4", "payload": {}})

        def execute(payload: dict) -> dict:
            stop.set()
            return {"ok": True}

        runner = CloudWorkerRunner(
            client,
            executor=execute,
            settings=WorkerSettings(poll_seconds=0.01),
        )
        runner.run_forever(stop_event=stop)
        self.assertEqual([("claim", "120"), ("complete", "ct-4")], client.events)

    def test_run_forever_retries_transient_claim_failure(self) -> None:
        stop = threading.Event()

        class RetryClient(FakeClient):
            def __init__(self) -> None:
                super().__init__(None)
                self.attempts = 0

            def claim(self, *, lease_seconds: int) -> dict | None:
                self.attempts += 1
                if self.attempts == 1:
                    raise ConnectionError("temporary network failure")
                stop.set()
                return None

        client = RetryClient()
        runner = CloudWorkerRunner(
            client,
            executor=lambda payload: {},
            settings=WorkerSettings(poll_seconds=0.01),
        )
        runner.run_forever(stop_event=stop)
        self.assertEqual(2, client.attempts)

    def test_http_client_round_trip_uses_worker_header(self) -> None:
        seen: list[tuple[str, str | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.url.path, request.headers.get("x-worker-id")))
            if request.url.path == "/workers/claim":
                return httpx.Response(
                    200,
                    json={"task": {"task_id": "ct-http", "payload": {"kind": "fixture"}}},
                )
            if request.url.path == "/tasks/ct-http/complete":
                return httpx.Response(200, json={"status": "completed"})
            return httpx.Response(404, json={"detail": "unexpected"})

        client = HttpCloudWorkerClient(
            "https://cloud.example",
            "worker-http",
            transport=httpx.MockTransport(handler),
        )
        try:
            runner = CloudWorkerRunner(client, executor=lambda payload: {"ok": payload["kind"]})
            self.assertEqual("completed", runner.run_once())
        finally:
            client.close()
        self.assertEqual(
            [("/workers/claim", "worker-http"), ("/tasks/ct-http/complete", "worker-http")],
            seen,
        )


if __name__ == "__main__":
    unittest.main()
