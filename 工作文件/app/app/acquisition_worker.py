from __future__ import annotations

import argparse
from pathlib import Path

from app.services.acquisition import AcquisitionJobStore, run_acquisition_job


def main() -> int:
    parser = argparse.ArgumentParser(description="项目024隔离采集 Worker")
    parser.add_argument("--root", required=True, help="采集任务根目录")
    parser.add_argument("--job-id", required=True, help="待执行任务 ID")
    args = parser.parse_args()

    store = AcquisitionJobStore(Path(args.root))
    run_acquisition_job(store, args.job_id)
    final_status = store.status(args.job_id)
    return 0 if final_status.get("status") in {"completed", "needs_input"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
