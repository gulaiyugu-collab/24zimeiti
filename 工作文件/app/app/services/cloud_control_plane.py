"""Production control plane for the mainland deployment and local Workers."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import FileResponse

from .cloud_worker_http import CompleteBody, FailBody, HeartbeatBody, TaskCreateBody
from .cloud_worker_protocol import CloudTaskBackend, CloudTaskConflictError, CloudTaskNotFoundError, CloudTaskStore
from .domestic_auth import DomesticAuthError, LocalAuthStore, LocalJWTAuthenticator
from .supabase_auth import AuthenticatedUser, SupabaseAuthError, SupabaseJWTAuthenticator
from .supabase_tasks import SupabaseTaskBackend


def _bearer(value: str | None) -> str | None:
    scheme, _, token = (value or "").partition(" ")
    return token.strip() if scheme.casefold() == "bearer" and token.strip() else None


def create_cloud_control_plane_app(
    *,
    backend: CloudTaskBackend | None = None,
    authenticator: Any | None = None,
    worker_token: str | None = None,
    domestic_mode: bool | None = None,
    domestic_database_path: str | os.PathLike[str] | None = None,
) -> FastAPI:
    app = FastAPI(title="Project024 Cloud Control Plane", version="1.0")
    static_dir = Path(__file__).resolve().parents[2] / "static"
    use_domestic = domestic_mode if domestic_mode is not None else os.getenv("PROJECT024_DOMESTIC_MODE", "1").strip() != "0"
    task_database_path = domestic_database_path or os.getenv("PROJECT024_CLOUD_TASK_DB", "var/cloud-control.sqlite3")
    local_auth_store = LocalAuthStore(task_database_path) if use_domestic and authenticator is None else None
    configured_backend = backend or (CloudTaskStore(task_database_path) if use_domestic else None)
    configured_authenticator = authenticator or (LocalJWTAuthenticator() if use_domestic else SupabaseJWTAuthenticator())
    configured_worker_token = worker_token

    def get_backend() -> CloudTaskBackend:
        nonlocal configured_backend
        if configured_backend is None:
            try:
                configured_backend = SupabaseTaskBackend()
            except ValueError as exc:
                raise HTTPException(status_code=503, detail="云端数据库尚未配置") from exc
        return configured_backend

    def issue_local_token(email: str, password: str, register: bool) -> dict[str, Any]:
        if not local_auth_store or not isinstance(configured_authenticator, LocalJWTAuthenticator):
            raise HTTPException(status_code=404, detail="国内登录模式未启用")
        try:
            user = local_auth_store.register(email, password) if register else local_auth_store.authenticate(email, password)
        except DomesticAuthError as exc:
            raise HTTPException(status_code=400 if register else 401, detail=str(exc)) from exc
        return {"access_token": configured_authenticator.issue(user), "token_type": "bearer", "user": user}

    def require_user(authorization: str | None = Header(default=None)) -> AuthenticatedUser:
        token = _bearer(authorization)
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="需要 Supabase 登录令牌",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            return configured_authenticator.verify(token)
        except (SupabaseAuthError, DomesticAuthError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="登录令牌无效或已过期",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    def require_worker(
        x_worker_id: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> str:
        worker_id = (x_worker_id or "").strip()
        token = _bearer(authorization)
        expected = (configured_worker_token or os.getenv("PROJECT024_CLOUD_WORKER_TOKEN", "")).strip()
        if not worker_id or not expected or not token or not secrets.compare_digest(token, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Worker 凭据无效",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return worker_id

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "project024-cloud-control-plane",
            "mode": "domestic" if use_domestic else "supabase-legacy",
            "supabase_configured": (not use_domestic) and bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SECRET_KEY")),
            "worker_auth_configured": bool(configured_worker_token or os.getenv("PROJECT024_CLOUD_WORKER_TOKEN")),
        }

    @app.get("/api/cloud/config")
    def cloud_config() -> dict[str, Any]:
        """Return browser-safe client configuration; never return server secrets."""
        if use_domestic:
            return {"mode": "domestic", "configured": True}
        publishable_key = (
            os.getenv("SUPABASE_PUBLISHABLE_KEY", "").strip()
            or os.getenv("SUPABASE_ANON_KEY", "").strip()
        )
        return {
            "supabase_url": os.getenv("SUPABASE_URL", "").strip().rstrip("/"),
            "supabase_publishable_key": publishable_key,
            "configured": bool(os.getenv("SUPABASE_URL") and publishable_key),
        }

    @app.post("/api/auth/register")
    def register(body: dict[str, str]) -> dict[str, Any]:
        return issue_local_token(str(body.get("email") or ""), str(body.get("password") or ""), True)

    @app.post("/api/auth/login")
    def login(body: dict[str, str]) -> dict[str, Any]:
        return issue_local_token(str(body.get("email") or ""), str(body.get("password") or ""), False)

    @app.post("/api/cloud/tasks", status_code=201)
    def create_task(body: TaskCreateBody, user: AuthenticatedUser = Depends(require_user)) -> dict[str, Any]:
        return get_backend().create(
            owner_id=user.user_id,
            idempotency_key=body.idempotency_key,
            payload=body.payload,
            max_retries=body.max_retries,
        )

    @app.get("/api/cloud/tasks/{task_id}")
    def get_task(task_id: str, user: AuthenticatedUser = Depends(require_user)) -> dict[str, Any]:
        try:
            task = get_backend().get(task_id)
        except CloudTaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        if task.get("owner_id") != user.user_id:
            raise HTTPException(status_code=404, detail="任务不存在")
        return task

    @app.post("/workers/claim")
    def claim_task(worker_id: str = Depends(require_worker), lease_seconds: int = 120) -> dict[str, Any]:
        if lease_seconds < 1 or lease_seconds > 3600:
            raise HTTPException(status_code=422, detail="lease_seconds 必须在 1..3600")
        return {"task": get_backend().claim_next(worker_id=worker_id, lease_seconds=lease_seconds)}

    @app.post("/tasks/{task_id}/heartbeat")
    def heartbeat(task_id: str, body: HeartbeatBody, worker_id: str = Depends(require_worker)) -> dict[str, Any]:
        try:
            return get_backend().heartbeat(task_id, worker_id=worker_id, lease_seconds=body.lease_seconds)
        except CloudTaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        except CloudTaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/tasks/{task_id}/complete")
    def complete(task_id: str, body: CompleteBody, worker_id: str = Depends(require_worker)) -> dict[str, Any]:
        try:
            return get_backend().complete(task_id, worker_id=worker_id, result=body.result)
        except CloudTaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        except CloudTaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/tasks/{task_id}/fail")
    def fail(task_id: str, body: FailBody, worker_id: str = Depends(require_worker)) -> dict[str, Any]:
        try:
            return get_backend().fail(
                task_id,
                worker_id=worker_id,
                error=body.error,
                retryable=body.retryable,
            )
        except CloudTaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="任务不存在") from exc
        except CloudTaskConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/cloud", include_in_schema=False)
    def cloud_page() -> FileResponse:
        page = static_dir / "cloud.html"
        if not page.is_file():
            raise HTTPException(status_code=404, detail="手机入口尚未打包")
        return FileResponse(page, headers={"Cache-Control": "no-store"})

    @app.get("/static/cloud.js", include_in_schema=False)
    def cloud_script() -> FileResponse:
        script = static_dir / "cloud.js"
        if not script.is_file():
            raise HTTPException(status_code=404, detail="手机入口脚本尚未打包")
        return FileResponse(
            script,
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    return app


app = create_cloud_control_plane_app()
