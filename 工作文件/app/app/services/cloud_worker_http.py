"""云端 Worker HTTP 合约样机。

该路由工厂只用于契约测试和后续接线，不挂载到当前生产主应用。认证通过
``X-User-Id`` / ``X-Worker-Id`` 标头模拟，真实部署必须替换为 JWT 和 Worker
注册凭据校验；接口不接受媒体内容或任何密钥。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from .cloud_worker_protocol import CloudTaskBackend, CloudTaskConflictError, CloudTaskNotFoundError


class TaskCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)
    max_retries: int = Field(default=1, ge=0, le=3)


class HeartbeatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_seconds: int = Field(default=120, ge=1, le=3600)


class CompleteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result: dict[str, Any] = Field(default_factory=dict)


class FailBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = True


def _require(value: str | None, label: str) -> str:
    candidate = (value or "").strip()
    if not candidate:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"缺少 {label}")
    return candidate


def create_cloud_worker_app(backend: CloudTaskBackend) -> FastAPI:
    app = FastAPI(title="Project024 Cloud Worker Contract", version="0.1-test")

    @app.post("/tasks", status_code=201)
    def create_task(body: TaskCreateBody, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
        owner_id = _require(x_user_id, "X-User-Id")
        return backend.create(
            owner_id=owner_id,
            idempotency_key=body.idempotency_key,
            payload=body.payload,
            max_retries=body.max_retries,
        )

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str, x_user_id: str | None = Header(default=None)) -> dict[str, Any]:
        owner_id = _require(x_user_id, "X-User-Id")
        try:
            task = backend.get(task_id)
        except CloudTaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        if task["owner_id"] != owner_id:
            raise HTTPException(status_code=404, detail="任务不存在")
        return task

    @app.post("/workers/claim")
    def claim_task(
        x_worker_id: str | None = Header(default=None),
        lease_seconds: int = 120,
    ) -> dict[str, Any]:
        worker_id = _require(x_worker_id, "X-Worker-Id")
        if lease_seconds < 1 or lease_seconds > 3600:
            raise HTTPException(status_code=422, detail="lease_seconds 必须在 1..3600")
        return {"task": backend.claim_next(worker_id=worker_id, lease_seconds=lease_seconds)}

    @app.post("/tasks/{task_id}/heartbeat")
    def heartbeat(
        task_id: str,
        body: HeartbeatBody,
        x_worker_id: str | None = Header(default=None),
    ) -> dict[str, Any]:
        worker_id = _require(x_worker_id, "X-Worker-Id")
        try:
            return backend.heartbeat(task_id, worker_id=worker_id, lease_seconds=body.lease_seconds)
        except CloudTaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        except CloudTaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/tasks/{task_id}/complete")
    def complete(
        task_id: str,
        body: CompleteBody,
        x_worker_id: str | None = Header(default=None),
    ) -> dict[str, Any]:
        worker_id = _require(x_worker_id, "X-Worker-Id")
        try:
            return backend.complete(task_id, worker_id=worker_id, result=body.result)
        except CloudTaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        except CloudTaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/tasks/{task_id}/fail")
    def fail(
        task_id: str,
        body: FailBody,
        x_worker_id: str | None = Header(default=None),
    ) -> dict[str, Any]:
        worker_id = _require(x_worker_id, "X-Worker-Id")
        try:
            return backend.fail(task_id, worker_id=worker_id, error=body.error, retryable=body.retryable)
        except CloudTaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        except CloudTaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app
