from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from app.models import AcquisitionJobRequest
from app.services.acquisition import AcquisitionJobManager, AcquisitionJobStore


TERMINAL_STATUSES = {"completed", "needs_input", "failed"}


def _compact_result(store: AcquisitionJobStore, status: dict[str, Any]) -> dict[str, Any]:
    job_id = str(status["job_id"])
    manifest_path = store.job_dir(job_id) / "evidence_manifest.json"
    return {
        "job_id": job_id,
        "status": status["status"],
        "platform": status["platform"],
        "cache_hit": bool(status.get("cache_hit", False)),
        "message": status["message"],
        "manifest_path": str(manifest_path) if manifest_path.is_file() else None,
        "missing": status.get("missing", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="提交项目024隔离采集任务，只输出最终精简状态。"
    )
    parser.add_argument("--url", required=True, help="公开内容或主页链接")
    parser.add_argument("--item-limit", type=int, default=1, choices=range(1, 51))
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--root", help="可选任务根目录")
    parser.add_argument("--wait-seconds", type=float, default=120.0)
    args = parser.parse_args()

    store = AcquisitionJobStore(Path(args.root)) if args.root else AcquisitionJobStore()
    manager = AcquisitionJobManager(store=store)
    payload = AcquisitionJobRequest(
        url=args.url,
        item_limit=args.item_limit,
        force_refresh=args.force_refresh,
    )
    status = manager.submit(payload)
    deadline = time.monotonic() + max(0.0, args.wait_seconds)
    while status["status"] not in TERMINAL_STATUSES and time.monotonic() < deadline:
        time.sleep(0.2)
        status = store.status(str(status["job_id"]))

    result = _compact_result(store, status)
    if status["status"] not in TERMINAL_STATUSES:
        result["message"] = "等待时间已到，Worker 仍在后台运行。"
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 1 if status["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
