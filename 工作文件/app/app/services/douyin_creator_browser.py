from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import re
from pathlib import Path
from typing import Any


class DouyinCreatorBrowserError(Exception):
    """本机浏览器会话或创作者中心导出失败。"""


def _node_executable() -> str:
    configured = os.environ.get("PROJECT024_NODE_EXE", "").strip()
    if configured and Path(configured).is_file():
        return configured
    resolved = shutil.which("node")
    if resolved:
        return resolved
    raise DouyinCreatorBrowserError("未找到 Node.js，无法启动本机浏览器适配器。")


def _worker_path() -> Path:
    worker = Path(__file__).with_name("douyin_creator_export.cjs")
    if not worker.is_file():
        raise DouyinCreatorBrowserError("缺少本机浏览器适配器文件。")
    return worker


def _run_worker(args: list[str], timeout_seconds: int) -> dict[str, Any]:
    command = [_node_executable(), str(_worker_path()), *args]
    try:
        completed = subprocess.run(
            command,
            cwd=str(_worker_path().parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DouyinCreatorBrowserError("浏览器导出超时；请确认创作者中心已登录并允许下载。") from exc
    except OSError as exc:
        raise DouyinCreatorBrowserError("本机浏览器适配器无法启动。") from exc
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    payload: dict[str, Any] = {}
    if lines:
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError:
            payload = {}
    if completed.returncode != 0 or payload.get("status") != "ok":
        raise DouyinCreatorBrowserError(str(payload.get("message") or "浏览器导出未完成。"))
    return payload


def list_browsers() -> list[dict[str, Any]]:
    payload = _run_worker(["--list"], timeout_seconds=15)
    browsers = payload.get("browsers")
    return browsers if isinstance(browsers, list) else []


def export_creator_data(
    *,
    browser_id: str | None = None,
    profile_mode: str = "existing",
    timeout_seconds: int = 180,
) -> tuple[dict[str, Any], bytes, str]:
    if profile_mode not in {"existing", "temporary"}:
        raise DouyinCreatorBrowserError("不支持的浏览器配置模式。")
    project_temp = Path(__file__).resolve().parents[2] / ".temp"
    project_temp.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="creator-export-", dir=project_temp) as temp_dir:
        output = Path(temp_dir) / "creator-export"
        args = [
            "--output",
            str(output),
            "--profile-mode",
            profile_mode,
            "--timeout-ms",
            str(max(30_000, min(timeout_seconds * 1000, 300_000))),
        ]
        if browser_id:
            args.extend(["--browser", browser_id])
        payload = _run_worker(args, timeout_seconds=max(45, timeout_seconds + 15))
        target = Path(str(payload.get("output") or ""))
        if target.suffix.lower() not in {".csv", ".xlsx"} or not target.is_file():
            raise DouyinCreatorBrowserError("导出文件不存在或格式不受支持。")
        data = target.read_bytes()
        if not data:
            raise DouyinCreatorBrowserError("创作者中心导出文件为空。")
        return payload, data, target.name


def _downloads_dir() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "Downloads"


def latest_creator_download(*, since_epoch_ms: int = 0) -> tuple[bytes, str, int] | None:
    """读取用户刚下载的 CSV/XLSX；不删除文件、不读取浏览器数据库。"""
    root = _downloads_dir()
    if not root.is_dir():
        return None
    candidates: list[tuple[int, Path]] = []
    for path in root.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".csv", ".xlsx"}:
            continue
        if not re.search(r"作品|粉丝|数据|导出|creator|douyin", path.stem, re.IGNORECASE):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        modified_ms = int(stat.st_mtime * 1000)
        if modified_ms < int(since_epoch_ms) or stat.st_size <= 0:
            continue
        candidates.append((modified_ms, path))
    if not candidates:
        return None
    modified_ms, path = max(candidates, key=lambda item: item[0])
    try:
        return path.read_bytes(), path.name, modified_ms
    except OSError as exc:
        raise DouyinCreatorBrowserError("最近下载文件暂时无法读取，请等待浏览器完成下载后重试。") from exc
