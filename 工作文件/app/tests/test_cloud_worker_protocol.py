from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.cloud_worker_protocol import (
    CloudTaskConflictError,
    CloudTaskStore,
)


class CloudWorkerProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = CloudTaskStore(Path(self.temp_dir.name) / "tasks.sqlite3")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create(self, *, key: str = "idem-1", max_retries: int = 1) -> dict:
        return self.store.create(
            owner_id="user-a",
            idempotency_key=key,
            payload={"url": "https://www.douyin.com/video/demo"},
            max_retries=max_retries,
            now=100.0,
        )

    def test_create_is_idempotent(self) -> None:
        first = self._create()
        second = self._create()
        self.assertEqual(first["task_id"], second["task_id"])
        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])

    def test_idempotency_key_is_scoped_to_owner(self) -> None:
        first = self.store.create(
            owner_id="user-a", idempotency_key="shared", payload={"owner": "a"}
        )
        second = self.store.create(
            owner_id="user-b", idempotency_key="shared", payload={"owner": "b"}
        )
        self.assertNotEqual(first["task_id"], second["task_id"])
        self.assertFalse(second["deduplicated"])
        self.assertEqual("user-b", second["owner_id"])

    def test_only_one_worker_claims_a_task(self) -> None:
        task = self._create()
        claimed = self.store.claim_next(worker_id="worker-a", now=101.0)
        self.assertEqual(task["task_id"], claimed["task_id"])
        self.assertIsNone(self.store.claim_next(worker_id="worker-b", now=101.0))

    def test_heartbeat_requires_current_worker(self) -> None:
        task = self._create()
        self.store.claim_next(worker_id="worker-a", now=101.0)
        with self.assertRaises(CloudTaskConflictError):
            self.store.heartbeat(task["task_id"], worker_id="worker-b", now=102.0)
        updated = self.store.heartbeat(task["task_id"], worker_id="worker-a", now=102.0)
        self.assertEqual(updated["lease_until"], 222.0)

    def test_completed_callback_is_idempotent(self) -> None:
        task = self._create()
        self.store.claim_next(worker_id="worker-a", now=101.0)
        first = self.store.complete(task["task_id"], worker_id="worker-a", result={"ok": True}, now=102.0)
        second = self.store.complete(task["task_id"], worker_id="worker-b", result={"ok": False}, now=103.0)
        self.assertEqual(first["status"], "completed")
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["result"], {"ok": True})

    def test_failure_retries_then_becomes_terminal(self) -> None:
        task = self._create(max_retries=1)
        self.store.claim_next(worker_id="worker-a", now=101.0)
        retryable = self.store.fail(
            task["task_id"], worker_id="worker-a", error={"type": "network"}, now=102.0
        )
        self.assertEqual(retryable["status"], "retryable")
        self.store.claim_next(worker_id="worker-b", now=103.0)
        failed = self.store.fail(
            task["task_id"], worker_id="worker-b", error={"type": "network"}, now=104.0
        )
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["retry_count"], 2)

    def test_expired_worker_lease_is_recovered(self) -> None:
        task = self._create()
        self.store.claim_next(worker_id="worker-a", lease_seconds=10, now=101.0)
        self.assertEqual(self.store.recover_expired(now=112.0), 1)
        recovered = self.store.get(task["task_id"])
        self.assertEqual(recovered["status"], "retryable")
        claimed = self.store.claim_next(worker_id="worker-b", now=113.0)
        self.assertEqual(claimed["task_id"], task["task_id"])


if __name__ == "__main__":
    unittest.main()
