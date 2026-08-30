"""云端任务与出站 Worker 的最小协议样机。

该模块只验证控制面语义，不连接真实云端、媒体或模型：任务幂等、原子领取、
租约心跳、完成/失败回传，以及 Worker 离线后的恢复。正式接入时可将同一接口
替换为托管 PostgreSQL 或其他持久化实现。
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Literal, Protocol


CloudTaskStatus = Literal["queued", "processing", "retryable", "completed", "failed"]
_STATUSES = ("queued", "processing", "retryable", "completed", "failed")


class CloudTaskNotFoundError(LookupError):
    pass


class CloudTaskConflictError(RuntimeError):
    pass


class CloudTaskBackend(Protocol):
    """云端存储适配器契约；正式环境可替换 SQLite 为托管 PostgreSQL。"""

    def create(self, *, owner_id: str, idempotency_key: str, payload: dict[str, Any], max_retries: int = 1, now: float | None = None) -> dict[str, Any]: ...

    def get(self, task_id: str) -> dict[str, Any]: ...

    def list_for_owner(self, *, owner_id: str, limit: int = 20) -> list[dict[str, Any]]: ...

    def claim_next(self, *, worker_id: str, lease_seconds: int = 120, now: float | None = None) -> dict[str, Any] | None: ...

    def heartbeat(self, task_id: str, *, worker_id: str, lease_seconds: int = 120, now: float | None = None) -> dict[str, Any]: ...

    def complete(self, task_id: str, *, worker_id: str, result: dict[str, Any], now: float | None = None) -> dict[str, Any]: ...

    def fail(self, task_id: str, *, worker_id: str, error: dict[str, Any], retryable: bool = True, now: float | None = None) -> dict[str, Any]: ...


def _now() -> float:
    return time.time()


class CloudTaskStore:
    """SQLite 控制面样机；每个操作使用显式事务模拟云端原子更新。"""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialised = False
        self._ensure_initialised()

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=30.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _ensure_initialised(self) -> None:
        if self._initialised:
            return
        with closing(self._open()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cloud_tasks (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('queued', 'processing', 'retryable', 'completed', 'failed')),
                    payload_json TEXT NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    worker_id TEXT,
                    lease_until REAL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    max_retries INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL
                )
                """
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_cloud_tasks_owner_idempotency "
                "ON cloud_tasks (owner_id, idempotency_key)"
            )
        self._initialised = True

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        record: dict[str, Any] = {
            "task_id": str(row["id"]),
            "owner_id": str(row["owner_id"]),
            "idempotency_key": str(row["idempotency_key"]),
            "status": str(row["status"]),
            "worker_id": row["worker_id"],
            "lease_until": row["lease_until"],
            "retry_count": int(row["retry_count"]),
            "max_retries": int(row["max_retries"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "completed_at": row["completed_at"],
        }
        for field in ("payload_json", "result_json", "error_json"):
            raw = row[field]
            if raw is not None:
                try:
                    record[field.removesuffix("_json")] = json.loads(str(raw))
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(f"任务字段无法解析：{field}") from exc
        return record

    def get(self, task_id: str) -> dict[str, Any]:
        with closing(self._open()) as connection:
            row = connection.execute(
                "SELECT * FROM cloud_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise CloudTaskNotFoundError(task_id)
        return self._decode(row)

    def list_for_owner(self, *, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
        if not owner_id.strip():
            raise ValueError("owner_id 不能为空")
        if limit < 1 or limit > 50:
            raise ValueError("limit 必须在 1..50")
        with closing(self._open()) as connection:
            rows = connection.execute(
                "SELECT * FROM cloud_tasks WHERE owner_id = ? ORDER BY created_at DESC LIMIT ?",
                (owner_id, limit),
            ).fetchall()
        return [self._decode(row) for row in rows]

    def create(
        self,
        *,
        owner_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
        max_retries: int = 1,
        now: float | None = None,
    ) -> dict[str, Any]:
        if not owner_id.strip() or not idempotency_key.strip():
            raise ValueError("owner_id 和 idempotency_key 不能为空")
        if max_retries < 0 or max_retries > 3:
            raise ValueError("max_retries 必须在 0..3")
        serialised = json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
        timestamp = _now() if now is None else float(now)
        task_id = f"ct_{uuid.uuid4().hex}"
        with closing(self._open()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM cloud_tasks WHERE owner_id = ? AND idempotency_key = ?",
                (owner_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                connection.execute("COMMIT")
                return self._decode(existing) | {"deduplicated": True}
            connection.execute(
                """
                INSERT INTO cloud_tasks (
                    id, owner_id, idempotency_key, status, payload_json,
                    retry_count, max_retries, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', ?, 0, ?, ?, ?)
                """,
                (task_id, owner_id, idempotency_key, serialised, max_retries, timestamp, timestamp),
            )
            connection.execute("COMMIT")
        return self.get(task_id) | {"deduplicated": False}

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 120,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        if not worker_id.strip():
            raise ValueError("worker_id 不能为空")
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("lease_seconds 必须在 1..3600")
        timestamp = _now() if now is None else float(now)
        with closing(self._open()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM cloud_tasks
                WHERE status IN ('queued', 'retryable')
                   OR (status = 'processing' AND lease_until IS NOT NULL AND lease_until <= ?)
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (timestamp,),
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            lease_until = timestamp + lease_seconds
            connection.execute(
                """
                UPDATE cloud_tasks
                SET status = 'processing', worker_id = ?, lease_until = ?, updated_at = ?
                WHERE id = ?
                """,
                (worker_id, lease_until, timestamp, row["id"]),
            )
            connection.execute("COMMIT")
        return self.get(str(row["id"]))

    def heartbeat(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 120,
        now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = _now() if now is None else float(now)
        with closing(self._open()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM cloud_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise CloudTaskNotFoundError(task_id)
            if row["status"] == "completed":
                connection.execute("COMMIT")
                return self._decode(row)
            if row["status"] != "processing" or row["worker_id"] != worker_id:
                connection.execute("ROLLBACK")
                raise CloudTaskConflictError("只有当前领取 Worker 可以发送心跳")
            connection.execute(
                "UPDATE cloud_tasks SET lease_until = ?, updated_at = ? WHERE id = ?",
                (timestamp + lease_seconds, timestamp, task_id),
            )
            connection.execute("COMMIT")
        return self.get(task_id)

    def complete(
        self,
        task_id: str,
        *,
        worker_id: str,
        result: dict[str, Any],
        now: float | None = None,
    ) -> dict[str, Any]:
        serialised = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
        timestamp = _now() if now is None else float(now)
        with closing(self._open()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM cloud_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise CloudTaskNotFoundError(task_id)
            if row["status"] == "completed":
                connection.execute("COMMIT")
                return self._decode(row) | {"idempotent": True}
            if row["status"] != "processing" or row["worker_id"] != worker_id:
                connection.execute("ROLLBACK")
                raise CloudTaskConflictError("只有当前领取 Worker 可以完成任务")
            connection.execute(
                """
                UPDATE cloud_tasks
                SET status = 'completed', result_json = ?, worker_id = NULL,
                    lease_until = NULL, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (serialised, timestamp, timestamp, task_id),
            )
            connection.execute("COMMIT")
        return self.get(task_id) | {"idempotent": False}

    def fail(
        self,
        task_id: str,
        *,
        worker_id: str,
        error: dict[str, Any],
        retryable: bool = True,
        now: float | None = None,
    ) -> dict[str, Any]:
        serialised = json.dumps(error, ensure_ascii=False, sort_keys=True, allow_nan=False)
        timestamp = _now() if now is None else float(now)
        with closing(self._open()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM cloud_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                connection.execute("ROLLBACK")
                raise CloudTaskNotFoundError(task_id)
            if row["status"] == "completed":
                connection.execute("COMMIT")
                return self._decode(row) | {"idempotent": True}
            if row["status"] != "processing" or row["worker_id"] != worker_id:
                connection.execute("ROLLBACK")
                raise CloudTaskConflictError("只有当前领取 Worker 可以报告失败")
            retry_count = int(row["retry_count"]) + 1
            next_status = "retryable" if retryable and retry_count <= int(row["max_retries"]) else "failed"
            connection.execute(
                """
                UPDATE cloud_tasks
                SET status = ?, error_json = ?, retry_count = ?, worker_id = NULL,
                    lease_until = NULL, updated_at = ?
                WHERE id = ?
                """,
                (next_status, serialised, retry_count, timestamp, task_id),
            )
            connection.execute("COMMIT")
        return self.get(task_id) | {"idempotent": False}

    def recover_expired(self, *, now: float | None = None) -> int:
        """回收失联 Worker 的租约；超过重试上限的任务进入 failed。"""
        timestamp = _now() if now is None else float(now)
        with closing(self._open()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT id, retry_count, max_retries FROM cloud_tasks "
                "WHERE status = 'processing' AND lease_until IS NOT NULL AND lease_until <= ?",
                (timestamp,),
            ).fetchall()
            for row in rows:
                retry_count = int(row["retry_count"]) + 1
                status = "retryable" if retry_count <= int(row["max_retries"]) else "failed"
                connection.execute(
                    """
                    UPDATE cloud_tasks
                    SET status = ?, retry_count = ?, worker_id = NULL, lease_until = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, retry_count, timestamp, row["id"]),
                )
            connection.execute("COMMIT")
        return len(rows)
