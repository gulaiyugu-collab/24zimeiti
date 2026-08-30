"""独立云端 Worker 客户端。

该模块只负责控制面通信和本地任务执行，不暴露入站端口，也不读取浏览器
登录态。生产环境应通过受控的 Worker 凭据访问云端；当前 HTTP 合约仍可用
于本地演练和替换后端验收。
"""

from __future__ import annotations

import argparse
import os
import re
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

import httpx

from app.models import AcquisitionJobRequest
from app.services.acquisition import (
    AcquisitionJobManager,
    AcquisitionJobStore,
    InlineAcquisitionDispatcher,
)


def _safe_worker_id(value: str) -> str:
    """将 Windows/中文主机名转换为可放入 HTTP header 的 ASCII ID。"""
    raw = str(value or "").strip()
    safe = "".join(char if char.isascii() and (char.isalnum() or char in "-_.") else "-" for char in raw)
    safe = re.sub(r"-+", "-", safe).strip("-._")[:64]
    return safe or "worker-local"


class CloudWorkerClient(Protocol):
    def claim(self, *, lease_seconds: int) -> dict[str, Any] | None: ...

    def heartbeat(self, task_id: str, *, lease_seconds: int) -> dict[str, Any]: ...

    def complete(self, task_id: str, *, result: dict[str, Any]) -> dict[str, Any]: ...

    def fail(
        self,
        task_id: str,
        *,
        error: dict[str, Any],
        retryable: bool,
    ) -> dict[str, Any]: ...


class TaskExecutor(Protocol):
    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class WorkerSettings:
    poll_seconds: float = 5.0
    lease_seconds: int = 120
    heartbeat_seconds: float = 30.0

    def validate(self) -> None:
        if self.poll_seconds <= 0:
            raise ValueError("poll_seconds 必须大于 0")
        if self.lease_seconds < 3 or self.lease_seconds > 3600:
            raise ValueError("lease_seconds 必须在 3..3600")
        if self.heartbeat_seconds <= 0 or self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError("heartbeat_seconds 必须小于 lease_seconds")


class HttpCloudWorkerClient:
    """云端控制面 HTTP 客户端；不在日志中输出 Authorization 内容。"""

    def __init__(
        self,
        base_url: str,
        worker_id: str,
        worker_token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.worker_id = worker_id.strip()
        if not self.base_url or not self.worker_id:
            raise ValueError("base_url 和 worker_id 不能为空")
        headers = {"X-Worker-Id": self.worker_id}
        if worker_token:
            headers["Authorization"] = f"Bearer {worker_token}"
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=30.0,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("云端响应必须是 JSON 对象")
        return data

    def claim(self, *, lease_seconds: int) -> dict[str, Any] | None:
        data = self._json(self._client.post("/workers/claim", params={"lease_seconds": lease_seconds}))
        task = data.get("task")
        return task if isinstance(task, dict) else None

    def heartbeat(self, task_id: str, *, lease_seconds: int) -> dict[str, Any]:
        return self._json(
            self._client.post(f"/tasks/{task_id}/heartbeat", json={"lease_seconds": lease_seconds})
        )

    def complete(self, task_id: str, *, result: dict[str, Any]) -> dict[str, Any]:
        return self._json(self._client.post(f"/tasks/{task_id}/complete", json={"result": result}))

    def fail(self, task_id: str, *, error: dict[str, Any], retryable: bool) -> dict[str, Any]:
        return self._json(
            self._client.post(
                f"/tasks/{task_id}/fail", json={"error": error, "retryable": retryable}
            )
        )


class CloudWorkerRunner:
    def __init__(
        self,
        client: CloudWorkerClient,
        *,
        executor: TaskExecutor,
        settings: WorkerSettings | None = None,
    ) -> None:
        self.client = client
        self.executor = executor
        self.settings = settings or WorkerSettings()
        self.settings.validate()

    def run_once(self) -> str:
        task = self.client.claim(lease_seconds=self.settings.lease_seconds)
        if task is None:
            return "idle"
        task_id = str(task.get("task_id") or "").strip()
        payload = task.get("payload")
        print(f"云端 Worker 已领取任务：{task_id or '无任务号'}", flush=True)
        if not task_id or not isinstance(payload, dict):
            if task_id:
                self.client.fail(
                    task_id,
                    error={"type": "invalid_task_payload", "message": "payload 必须是对象"},
                    retryable=False,
                )
            return "invalid"

        stop_event = threading.Event()
        heartbeat_errors: list[Exception] = []

        def heartbeat_loop() -> None:
            while not stop_event.wait(self.settings.heartbeat_seconds):
                try:
                    self.client.heartbeat(task_id, lease_seconds=self.settings.lease_seconds)
                except Exception as exc:  # pragma: no cover - exercised by integration logs
                    heartbeat_errors.append(exc)
                    return

        thread = threading.Thread(target=heartbeat_loop, name=f"cloud-heartbeat-{task_id}", daemon=True)
        thread.start()
        try:
            result = self.executor(payload)
            if heartbeat_errors:
                raise RuntimeError("云端租约心跳失败，停止回传以避免重复执行") from heartbeat_errors[0]
            self.client.complete(task_id, result=result)
            print(f"云端 Worker 任务已完成：{task_id}", flush=True)
            return "completed"
        except Exception as exc:
            self.client.fail(
                task_id,
                error={"type": type(exc).__name__, "message": str(exc)[:500]},
                retryable=True,
            )
            print(f"云端 Worker 任务失败：{task_id}（{type(exc).__name__}: {str(exc)[:200]}）", flush=True)
            return "failed"
        finally:
            stop_event.set()
            thread.join(timeout=1.0)

    def run_forever(self, *, stop_event: threading.Event | None = None) -> None:
        stopper = stop_event or threading.Event()
        while not stopper.is_set():
            try:
                outcome = self.run_once()
            except Exception as exc:
                # Keep the local Worker alive across transient control-plane or
                # network failures, while making the failure visible locally.
                print(
                    f"云端 Worker 轮询失败，将重试：{type(exc).__name__}: {str(exc)[:200]}",
                    flush=True,
                )
                stopper.wait(min(max(self.settings.poll_seconds, 5.0), 30.0))
                continue
            if outcome == "idle":
                stopper.wait(self.settings.poll_seconds)


def make_local_acquisition_executor(local_root: str | os.PathLike[str]) -> TaskExecutor:
    """将云端任务 payload 映射到现有本地采集内核。"""

    store = AcquisitionJobStore(local_root)
    manager = AcquisitionJobManager(store=store, dispatcher=InlineAcquisitionDispatcher(store))

    def execute(payload: dict[str, Any]) -> dict[str, Any]:
        request = AcquisitionJobRequest.model_validate(
            {
                "url": payload.get("url"),
                "item_limit": payload.get("item_limit", 1),
                "force_refresh": payload.get("force_refresh", False),
            }
        )
        status = manager.submit(request)
        result: dict[str, Any] = {
            "local_job_id": status.get("job_id"),
            "status": status.get("status"),
            "platform": status.get("platform"),
            "cache_hit": bool(status.get("cache_hit", False)),
            "message": status.get("message"),
            "missing": status.get("missing", []),
        }
        # The cloud task stores a compact status plus the local evidence manifest.
        # Raw media remains on the user's computer; only the manifest is returned.
        job_id = str(status.get("job_id") or "").strip()
        if job_id and status.get("status") in {"completed", "needs_input"}:
            try:
                result["manifest"] = store.manifest(job_id)
            except Exception:
                result["manifest"] = None
        if status.get("status") == "completed":
            # Reuse the same analysis endpoint as the desktop app after local
            # acquisition. The import stays lazy so the Worker protocol remains
            # lightweight for tests and idle polling.
            import asyncio
            from app.main import analyze_acquisition_job
            from app.models import AcquisitionAnalysisRequest

            analysis_request = AcquisitionAnalysisRequest.model_validate(
                {
                    "analysis_mode": payload.get("analysis_mode", "quick"),
                    "analysis_strategy": payload.get("analysis_strategy", "multi_agent"),
                    "product_context": payload.get("product_context"),
                    "product": payload.get("product"),
                    "product_relevance_override": payload.get("product_relevance_override"),
                    "market": payload.get("market") or {},
                }
            )
            analysis = asyncio.run(analyze_acquisition_job(job_id, analysis_request))
            result["analysis"] = analysis.model_dump(mode="json")
        return result

    return execute


def main() -> int:
    parser = argparse.ArgumentParser(description="Project024 云端 Worker 客户端")
    parser.add_argument("--base-url", default=os.getenv("PROJECT024_CLOUD_CONTROL_BASE_URL"))
    parser.add_argument("--worker-id", default=os.getenv("PROJECT024_CLOUD_WORKER_ID") or socket.gethostname())
    parser.add_argument("--worker-token", default=os.getenv("PROJECT024_CLOUD_WORKER_TOKEN"))
    parser.add_argument("--local-root", default=os.getenv("PROJECT024_ACQUISITION_ROOT"))
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--lease-seconds", type=int, default=120)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.base_url:
        parser.error("必须提供 --base-url 或 PROJECT024_CLOUD_CONTROL_BASE_URL")
    if not args.local_root:
        parser.error("必须提供 --local-root 或 PROJECT024_ACQUISITION_ROOT")

    if not args.worker_token:
        parser.error("production Worker requires --worker-token or PROJECT024_CLOUD_WORKER_TOKEN")
    raw_worker_id = args.worker_id
    args.worker_id = _safe_worker_id(args.worker_id)
    if args.worker_id != raw_worker_id:
        print(f"Worker ID 已转换为 ASCII：{args.worker_id}", flush=True)
    print(
        f"云端 Worker 已启动：worker_id={args.worker_id}，控制面={args.base_url}",
        flush=True,
    )
    client = HttpCloudWorkerClient(args.base_url, args.worker_id, args.worker_token)
    try:
        runner = CloudWorkerRunner(
            client,
            executor=make_local_acquisition_executor(args.local_root),
            settings=WorkerSettings(poll_seconds=args.poll_seconds, lease_seconds=args.lease_seconds),
        )
        if args.once:
            return 0 if runner.run_once() in {"idle", "completed"} else 1
        runner.run_forever()
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
