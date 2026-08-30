"""Supabase PostgREST backend for the cloud task contract."""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx

from .cloud_worker_protocol import CloudTaskConflictError, CloudTaskNotFoundError


class SupabaseTaskBackend:
    """Persist task state in Supabase using a server-only secret key."""

    def __init__(
        self,
        *,
        project_url: str | None = None,
        service_key: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        url = (project_url or os.getenv("SUPABASE_URL", "")).strip().rstrip("/")
        key = (service_key or os.getenv("SUPABASE_SECRET_KEY", "")).strip()
        if not url or not key:
            raise ValueError("SUPABASE_URL and SUPABASE_SECRET_KEY are required")
        self._client = client or httpx.Client(
            base_url=f"{url}/rest/v1",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    @staticmethod
    def _decode(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": row["id"],
            "owner_id": row["owner_id"],
            "idempotency_key": row["idempotency_key"],
            "status": row["status"],
            "payload": row.get("payload") or {},
            "result": row.get("result"),
            "error": row.get("error"),
            "worker_id": row.get("worker_id"),
            "lease_until": row.get("lease_until"),
            "retry_count": int(row.get("retry_count") or 0),
            "max_retries": int(row.get("max_retries") or 0),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
            "completed_at": row.get("completed_at"),
        }

    @staticmethod
    def _rows(response: httpx.Response) -> list[dict[str, Any]]:
        response.raise_for_status()
        value = response.json()
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            return [value]
        return []

    def _rpc(self, name: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        response = self._client.post(f"/rpc/{name}", json=payload)
        rows = self._rows(response)
        return self._decode(rows[0]) if rows else None

    def create(
        self,
        *,
        owner_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        max_retries: int = 1,
        now: float | None = None,
    ) -> dict[str, Any]:
        del now
        if not owner_id.strip() or not idempotency_key.strip():
            raise ValueError("owner_id and idempotency_key are required")
        existing_response = self._client.get(
            "/cloud_tasks",
            params={
                "owner_id": f"eq.{owner_id}",
                "idempotency_key": f"eq.{idempotency_key}",
                "select": "*",
                "limit": "1",
            },
        )
        existing = self._rows(existing_response)
        if existing:
            return self._decode(existing[0]) | {"deduplicated": True}

        row = {
            "id": f"ct_{uuid.uuid4().hex}",
            "owner_id": owner_id,
            "idempotency_key": idempotency_key,
            "status": "queued",
            "payload": payload,
            "max_retries": max_retries,
        }
        response = self._client.post(
            "/cloud_tasks",
            params={"select": "*"},
            headers={"Prefer": "return=representation"},
            json=row,
        )
        if response.status_code == 409:
            retry = self._client.get(
                "/cloud_tasks",
                params={
                    "owner_id": f"eq.{owner_id}",
                    "idempotency_key": f"eq.{idempotency_key}",
                    "select": "*",
                    "limit": "1",
                },
            )
            rows = self._rows(retry)
            if rows:
                return self._decode(rows[0]) | {"deduplicated": True}
        rows = self._rows(response)
        if not rows:
            raise RuntimeError("Supabase did not return the created cloud task")
        return self._decode(rows[0]) | {"deduplicated": False}

    def get(self, task_id: str) -> dict[str, Any]:
        response = self._client.get(
            "/cloud_tasks", params={"id": f"eq.{task_id}", "select": "*", "limit": "1"}
        )
        rows = self._rows(response)
        if not rows:
            raise CloudTaskNotFoundError(task_id)
        return self._decode(rows[0])

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 120,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        del now
        return self._rpc(
            "claim_cloud_task",
            {"p_worker_id": worker_id, "p_lease_seconds": lease_seconds},
        )

    def heartbeat(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 120,
        now: float | None = None,
    ) -> dict[str, Any]:
        del now
        result = self._rpc(
            "heartbeat_cloud_task",
            {
                "p_task_id": task_id,
                "p_worker_id": worker_id,
                "p_lease_seconds": lease_seconds,
            },
        )
        if result is None:
            raise CloudTaskConflictError("only the current worker can heartbeat a task")
        return result

    def complete(
        self,
        task_id: str,
        *,
        worker_id: str,
        result: dict[str, Any],
        now: float | None = None,
    ) -> dict[str, Any]:
        del now
        completed = self._rpc(
            "complete_cloud_task",
            {"p_task_id": task_id, "p_worker_id": worker_id, "p_result": result},
        )
        if completed is None:
            raise CloudTaskConflictError("task cannot be completed by this worker")
        return completed

    def fail(
        self,
        task_id: str,
        *,
        worker_id: str,
        error: dict[str, Any],
        retryable: bool = True,
        now: float | None = None,
    ) -> dict[str, Any]:
        del now
        failed = self._rpc(
            "fail_cloud_task",
            {
                "p_task_id": task_id,
                "p_worker_id": worker_id,
                "p_error": error,
                "p_retryable": retryable,
            },
        )
        if failed is None:
            raise CloudTaskConflictError("task cannot be failed by this worker")
        return failed

