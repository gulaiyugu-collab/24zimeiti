from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Protocol

from app.adapters import (
    DouyinAdapter,
    TikTokAdapter,
    canonical_url,
    detect_platform,
    extract_aweme_id,
)
from app.adapters.tiktok import extract_tiktok_video_id
from app.models import AcquisitionJobRequest

from .asr import ASRProviderError, ASRRouter
from .douyin_media import (
    DouyinMediaCollector,
    DouyinMediaError,
    DouyinProviderUnavailableError,
    DouyinResolvedSubmission,
    resolve_douyin_submission,
)
from .tiktok_media import (
    TikTokMediaCollector,
    TikTokMediaError,
    TikTokProxyUnavailableError,
)


ACQUISITION_SCHEMA_VERSION = "1.0"
_JOB_ID_RE = re.compile(r"^acq_[0-9]{8}T[0-9]{6}Z_[a-f0-9]{12}$")
_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class AcquisitionJobNotFoundError(LookupError):
    pass


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AcquisitionJobNotFoundError(str(path)) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected a JSON object in {path}")
    return payload


def default_acquisition_root() -> Path:
    configured = os.environ.get("PROJECT024_ACQUISITION_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "var" / "acquisition"


class AcquisitionJobStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or default_acquisition_root()).expanduser().resolve()
        self.jobs_root = self.root / "jobs"
        self.cache_root = self.root / "cache"
        self.stable_cache_root = self.root / "stable-cache"
        self.resolved_links_root = self.root / "resolved-links"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.stable_cache_root.mkdir(parents=True, exist_ok=True)
        self.resolved_links_root.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        if not _JOB_ID_RE.fullmatch(job_id):
            raise AcquisitionJobNotFoundError(job_id)
        return self.jobs_root / job_id

    def create_job(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        job_id = f"acq_{datetime.now(UTC):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:12]}"
        job_dir = self.job_dir(job_id)
        job_dir.mkdir(parents=False, exist_ok=False)
        (job_dir / "raw").mkdir()
        created_at = utc_now()
        request_record = {
            **request_payload,
            "job_id": job_id,
            "schema_version": ACQUISITION_SCHEMA_VERSION,
            "created_at": created_at,
        }
        status = {
            "job_id": job_id,
            "status": "queued",
            "platform": request_payload["platform"],
            "message": "采集任务已入队，等待独立 Worker 处理。",
            "progress": {"stage": "queued", "completed": 0, "total": 1},
            "cache_hit": False,
            "created_at": created_at,
            "updated_at": created_at,
            "manifest_url": None,
            "missing": [],
            "artifacts": [],
        }
        _atomic_write_json(job_dir / "request.json", request_record)
        _atomic_write_json(job_dir / "status.json", status)
        return status

    def request(self, job_id: str) -> dict[str, Any]:
        return _read_json(self.job_dir(job_id) / "request.json")

    def status(self, job_id: str) -> dict[str, Any]:
        return _read_json(self.job_dir(job_id) / "status.json")

    def patch_status(self, job_id: str, **changes: Any) -> dict[str, Any]:
        status = self.status(job_id)
        status.update(changes)
        status["updated_at"] = utc_now()
        _atomic_write_json(self.job_dir(job_id) / "status.json", status)
        return status

    def manifest(self, job_id: str) -> dict[str, Any]:
        return _read_json(self.job_dir(job_id) / "evidence_manifest.json")

    def write_manifest(self, job_id: str, manifest: dict[str, Any]) -> None:
        _atomic_write_json(self.job_dir(job_id) / "evidence_manifest.json", manifest)

    def write_raw_artifact(
        self,
        job_id: str,
        artifact_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not _ARTIFACT_NAME_RE.fullmatch(artifact_name):
            raise ValueError("Invalid artifact name")
        path = self.job_dir(job_id) / "raw" / artifact_name
        _atomic_write_json(path, payload)
        return self.register_raw_file(
            job_id,
            path,
            role="raw_evidence",
            content_type="application/json",
        )

    def register_raw_file(
        self,
        job_id: str,
        path: Path,
        *,
        role: str,
        content_type: str,
    ) -> dict[str, Any]:
        raw_dir = (self.job_dir(job_id) / "raw").resolve()
        resolved = path.resolve()
        if resolved.parent != raw_dir or not resolved.is_file():
            raise ValueError("Artifact must be a file directly inside the job raw directory")
        artifact_name = resolved.name
        if not _ARTIFACT_NAME_RE.fullmatch(artifact_name):
            raise ValueError("Invalid artifact name")
        digest = hashlib.sha256()
        size_bytes = 0
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size_bytes += len(chunk)
                digest.update(chunk)
        return {
            "name": artifact_name,
            "role": role,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "sha256": digest.hexdigest(),
            "url": f"/api/acquisition/jobs/{job_id}/artifacts/{artifact_name}",
            "included_in_default_context": False,
        }

    def artifact_path(self, job_id: str, artifact_name: str) -> Path:
        if not _ARTIFACT_NAME_RE.fullmatch(artifact_name):
            raise AcquisitionJobNotFoundError(artifact_name)
        status = self.status(job_id)
        allowed_names = {
            item.get("name")
            for item in status.get("artifacts", [])
            if isinstance(item, dict)
        }
        if artifact_name not in allowed_names:
            raise AcquisitionJobNotFoundError(artifact_name)
        raw_dir = (self.job_dir(job_id) / "raw").resolve()
        path = (raw_dir / artifact_name).resolve()
        if path.parent != raw_dir or not path.is_file():
            raise AcquisitionJobNotFoundError(artifact_name)
        return path

    def artifact_record(self, job_id: str, artifact_name: str) -> dict[str, Any]:
        status = self.status(job_id)
        record = next(
            (
                item
                for item in status.get("artifacts", [])
                if isinstance(item, dict) and item.get("name") == artifact_name
            ),
            None,
        )
        if not isinstance(record, dict):
            raise AcquisitionJobNotFoundError(artifact_name)
        return record

    def cached_status(self, cache_key: str) -> dict[str, Any] | None:
        try:
            record = _read_json(self.cache_root / f"{cache_key}.json")
            status = self.status(str(record["job_id"]))
            self.manifest(str(record["job_id"]))
        except (AcquisitionJobNotFoundError, KeyError, RuntimeError):
            return None
        if status.get("status") != "completed":
            return None
        return {**status, "cache_hit": True}

    def store_cache_entry(self, cache_key: str, job_id: str) -> None:
        _atomic_write_json(
            self.cache_root / f"{cache_key}.json",
            {"cache_key": cache_key, "job_id": job_id, "updated_at": utc_now()},
        )

    @staticmethod
    def _stable_cache_key(platform: str, stable_id: str) -> str:
        material = f"{platform}:{stable_id}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def store_stable_cache_entry(
        self, platform: str, stable_id: str, job_id: str
    ) -> None:
        cache_key = self._stable_cache_key(platform, stable_id)
        _atomic_write_json(
            self.stable_cache_root / f"{cache_key}.json",
            {
                "platform": platform,
                "stable_id": stable_id,
                "job_id": job_id,
                "updated_at": utc_now(),
            },
        )

    def cached_status_for_stable_id(
        self, platform: str, stable_id: str
    ) -> dict[str, Any] | None:
        cache_key = self._stable_cache_key(platform, stable_id)
        try:
            record = _read_json(self.stable_cache_root / f"{cache_key}.json")
            status = self.status(str(record["job_id"]))
            manifest = self.manifest(str(record["job_id"]))
            if (
                status.get("status") == "completed"
                and manifest.get("platform") == platform
                and str(manifest.get("stable_id") or "") == stable_id
            ):
                return {**status, "cache_hit": True}
        except (AcquisitionJobNotFoundError, KeyError, RuntimeError):
            pass

        for job_dir in sorted(self.jobs_root.glob("acq_*"), reverse=True):
            try:
                status = _read_json(job_dir / "status.json")
                if status.get("status") != "completed":
                    continue
                manifest = _read_json(job_dir / "evidence_manifest.json")
            except (AcquisitionJobNotFoundError, RuntimeError):
                continue
            if (
                manifest.get("platform") == platform
                and str(manifest.get("stable_id") or "") == stable_id
            ):
                job_id = str(status["job_id"])
                self.store_stable_cache_entry(platform, stable_id, job_id)
                return {**status, "cache_hit": True}
        return None

    @staticmethod
    def _resolved_link_key(platform: str, submitted_url: str) -> str:
        material = f"{platform}:{submitted_url}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def store_resolved_link(
        self, platform: str, submitted_url: str, stable_id: str
    ) -> None:
        if not stable_id.isdigit():
            return
        key = self._resolved_link_key(platform, submitted_url)
        _atomic_write_json(
            self.resolved_links_root / f"{key}.json",
            {
                "platform": platform,
                "stable_id": stable_id,
                "updated_at": utc_now(),
            },
        )

    def resolved_stable_id_for_link(
        self, platform: str, submitted_url: str
    ) -> str | None:
        key = self._resolved_link_key(platform, submitted_url)
        try:
            record = _read_json(self.resolved_links_root / f"{key}.json")
            stable_id = str(record.get("stable_id") or "")
            if record.get("platform") == platform and stable_id.isdigit():
                return stable_id
        except (AcquisitionJobNotFoundError, RuntimeError):
            pass

        if platform != "douyin":
            return None
        for job_dir in sorted(self.jobs_root.glob("acq_*"), reverse=True):
            try:
                request = _read_json(job_dir / "request.json")
                prior_url = str(request.get("submitted_url") or request.get("url") or "")
                if request.get("platform") != platform or prior_url != submitted_url:
                    continue
                metadata = _read_json(job_dir / "raw" / "douyin_source.info.json")
                stable_id = str(metadata.get("id") or "")
            except (AcquisitionJobNotFoundError, RuntimeError):
                continue
            if stable_id.isdigit():
                self.store_resolved_link(platform, submitted_url, stable_id)
                return stable_id
        return None


class AcquisitionDispatcher(Protocol):
    def dispatch(self, job_id: str) -> int | None: ...


class SubprocessAcquisitionDispatcher:
    def __init__(self, store: AcquisitionJobStore) -> None:
        self.store = store

    def dispatch(self, job_id: str) -> int:
        job_dir = self.store.job_dir(job_id)
        command = [
            sys.executable,
            "-m",
            "app.acquisition_worker",
            "--root",
            str(self.store.root),
            "--job-id",
            job_id,
        ]
        env = os.environ.copy()
        env["PROJECT024_ACQUISITION_ROOT"] = str(self.store.root)
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        popen_options: dict[str, Any] = {
            "cwd": str(Path(__file__).resolve().parents[2]),
            "env": env,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            popen_options["start_new_session"] = True
        with (
            (job_dir / "worker.stdout.log").open("ab") as stdout,
            (job_dir / "worker.stderr.log").open("ab") as stderr,
        ):
            process = subprocess.Popen(
                command,
                stdout=stdout,
                stderr=stderr,
                **popen_options,
            )
        threading.Thread(target=process.wait, daemon=True).start()
        return process.pid


class InlineAcquisitionDispatcher:
    """Synchronous dispatcher used by deterministic tests."""

    def __init__(
        self,
        store: AcquisitionJobStore,
        *,
        worker: Callable[[AcquisitionJobStore, str], None] | None = None,
    ) -> None:
        self.store = store
        self.worker = worker

    def dispatch(self, job_id: str) -> None:
        worker = self.worker or run_acquisition_job
        worker(self.store, job_id)
        return None


class AcquisitionJobManager:
    def __init__(
        self,
        store: AcquisitionJobStore | None = None,
        dispatcher: AcquisitionDispatcher | None = None,
        douyin_url_resolver: Callable[[str], DouyinResolvedSubmission] | None = None,
    ) -> None:
        self.store = store or AcquisitionJobStore()
        self.dispatcher = dispatcher or SubprocessAcquisitionDispatcher(self.store)
        self.douyin_url_resolver = douyin_url_resolver or resolve_douyin_submission

    def submit(self, payload: AcquisitionJobRequest) -> dict[str, Any]:
        submitted_url = canonical_url(payload.url)
        url = submitted_url
        platform = detect_platform(url)
        stable_id: str | None = None
        link_verified = False
        if platform == "douyin":
            try:
                resolved = self.douyin_url_resolver(url)
            except Exception:
                resolved = DouyinResolvedSubmission(url, None, False)
            url = resolved.canonical_url
            stable_id = resolved.aweme_id
            link_verified = resolved.link_verified
            if stable_id:
                self.store.store_resolved_link(platform, submitted_url, stable_id)
            else:
                stable_id = self.store.resolved_stable_id_for_link(
                    platform, submitted_url
                )
                if stable_id:
                    url = f"https://www.douyin.com/video/{stable_id}"
                    link_verified = True
        normalized = {
            "url": url,
            "platform": platform,
            "item_limit": payload.item_limit,
        }
        cache_material = json.dumps(
            {"schema_version": ACQUISITION_SCHEMA_VERSION, **normalized},
            sort_keys=True,
            ensure_ascii=True,
        ).encode("utf-8")
        cache_key = hashlib.sha256(cache_material).hexdigest()
        if not payload.force_refresh:
            cached = self.store.cached_status(cache_key)
            if cached is None and stable_id:
                cached = self.store.cached_status_for_stable_id(platform, stable_id)
            if cached:
                self.store.store_cache_entry(cache_key, str(cached["job_id"]))
                return cached

        status = self.store.create_job(
            {
                **normalized,
                "submitted_url": submitted_url,
                "cache_key": cache_key,
                "stable_id": stable_id,
                "link_verified": link_verified,
            }
        )
        job_id = str(status["job_id"])
        try:
            worker_pid = self.dispatcher.dispatch(job_id)
        except Exception as exc:
            return self.store.patch_status(
                job_id,
                status="failed",
                message="独立 Worker 启动失败。",
                error_type=type(exc).__name__,
                progress={"stage": "dispatch_failed", "completed": 0, "total": 1},
            )
        if worker_pid is not None:
            current = self.store.status(job_id)
            if current.get("status") == "queued":
                return self.store.patch_status(job_id, worker_pid=worker_pid)
        return self.store.status(job_id)


def _source_for_submission(
    platform: str,
    raw_url: str,
    tiktok_adapter: TikTokAdapter,
) -> dict[str, Any]:
    if platform == "tiktok":
        return tiktok_adapter.inspect_submission(raw_url).as_source()
    url = canonical_url(raw_url)
    return {
        "platform": platform,
        "url": url,
        "aweme_id": extract_aweme_id(url),
        "acquisition_mode": "user_supplied_url",
        "retrieval_status": "not_run",
        "evidence": [
            {
                "type": "public_url",
                "label": "用户提交链接",
                "value": url,
                "confidence": "submitted",
            }
        ],
    }


def _stable_id(platform: str, source: dict[str, Any], url: str) -> str | None:
    if platform == "douyin":
        return str(source.get("aweme_id") or extract_aweme_id(url) or "") or None
    if platform == "tiktok":
        return str(source.get("video_id") or extract_tiktok_video_id(url) or "") or None
    return None


def _compact_text(value: Any, limit: int = 600) -> Any:
    if not isinstance(value, str):
        return value
    if len(value) <= limit:
        return value
    return value[:limit] + "..."


def _compact_chapters(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    chapters: list[dict[str, Any]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        chapters.append(
            {
                key: _compact_text(item[key], 300)
                for key in ("start", "end", "time", "title", "text")
                if key in item
            }
        )
    return chapters


def _compact_evidence(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    evidence: list[dict[str, Any]] = []
    for item in value[:20]:
        if not isinstance(item, dict):
            continue
        evidence.append(
            {
                key: _compact_text(item[key], 500)
                for key in ("type", "label", "value", "confidence", "timestamp")
                if key in item
            }
        )
    return evidence


def _compact_item(platform: str, source: dict[str, Any], url: str) -> dict[str, Any]:
    author = source.get("author") if isinstance(source.get("author"), dict) else {}
    content = source.get("content") if isinstance(source.get("content"), dict) else {}
    metrics = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
    evidence = _compact_evidence(source.get("evidence"))
    compact_content: dict[str, Any] = {}
    for key in ("title", "description", "duration_seconds"):
        if key in content:
            compact_content[key] = _compact_text(content[key])
    if "chapters" in content:
        compact_content["chapters"] = _compact_chapters(content["chapters"])
    transcript = source.get("transcript")
    if isinstance(transcript, dict):
        raw_segments = transcript.get("segments")
        compact_segments: list[dict[str, Any]] = []
        if isinstance(raw_segments, list):
            for segment in raw_segments[:80]:
                if not isinstance(segment, dict):
                    continue
                compact_segments.append(
                    {
                        key: _compact_text(segment[key], 500)
                        for key in ("start", "end", "text")
                        if key in segment
                    }
                )
        compact_content["transcript"] = {
            "source": transcript.get("source"),
            "provider": transcript.get("provider"),
            "model": transcript.get("model"),
            "language": transcript.get("language"),
            "text": _compact_text(transcript.get("text"), 12_000),
            "character_count": len(str(transcript.get("text") or "")),
            "segments": compact_segments,
            "segment_count": len(raw_segments) if isinstance(raw_segments, list) else 0,
        }
    return {
        "item_id": _stable_id(platform, source, url),
        "url": source.get("url") or url,
        "author": {
            key: author[key]
            for key in ("name", "handle", "followers")
            if key in author
        },
        "content": compact_content,
        "metrics": {
            key: metrics[key]
            for key in ("views", "likes", "comments", "favorites", "shares")
            if key in metrics
        },
        "evidence": evidence,
        "missing": [
            _compact_text(item, 300) for item in list(source.get("missing", []))[:50]
        ],
    }


def _registered_case(
    platform: str,
    url: str,
    douyin_adapter: DouyinAdapter,
    tiktok_adapter: TikTokAdapter,
) -> dict[str, Any] | None:
    if platform == "douyin":
        return douyin_adapter.get_registered_case(url)
    if platform == "tiktok":
        return tiktok_adapter.get_registered_case(url)
    return None


def run_acquisition_job(
    store: AcquisitionJobStore,
    job_id: str,
    *,
    douyin_media_collector: DouyinMediaCollector | None = None,
    tiktok_media_collector: TikTokMediaCollector | None = None,
    asr_router: ASRRouter | None = None,
) -> None:
    request: dict[str, Any] = {}
    try:
        request = store.request(job_id)
        store.patch_status(
            job_id,
            status="processing",
            message="独立 Worker 正在整理来源证据。",
            progress={"stage": "inspect_source", "completed": 0, "total": 1},
        )
        platform = str(request["platform"])
        url = str(request["url"])
        douyin_adapter = DouyinAdapter()
        tiktok_adapter = TikTokAdapter()
        case = _registered_case(platform, url, douyin_adapter, tiktok_adapter)
        artifacts: list[dict[str, Any]] = []

        if case:
            report = case.get("report", {})
            source = report.get("source", {}) if isinstance(report, dict) else {}
            if not isinstance(source, dict):
                raise RuntimeError("Registered case source must be an object")
            lifecycle_status = "completed"
            message = "登记样本证据已由独立 Worker 整理完成。"
            missing = list(source.get("missing", []))
            analysis_ready = True
            acquisition_mode = "registered_fixture"
        elif platform in {"douyin", "tiktok"}:
            if platform == "douyin":
                platform_name = "抖音"
                status_message = (
                    "正在获取抖音公开媒体；公共接口不可用时会自动切换隔离浏览器。"
                )
                collector = douyin_media_collector or DouyinMediaCollector()
            else:
                platform_name = "TikTok"
                status_message = "正在通过电脑代理获取 TikTok 公开媒体。"
                collector = tiktok_media_collector or TikTokMediaCollector()
            store.patch_status(
                job_id,
                message=status_message,
                progress={"stage": "download_media", "completed": 1, "total": 4},
            )
            collection = collector.collect(url, store.job_dir(job_id) / "raw")
            source = collection.source
            for collected in collection.artifacts:
                artifacts.append(
                    store.register_raw_file(
                        job_id,
                        collected.path,
                        role=collected.role,
                        content_type=collected.content_type,
                    )
                )

            transcript = collection.native_transcript
            if transcript is None:
                if collection.audio_path is None:
                    raise ASRProviderError("未取得可供本地 ASR 使用的音轨。")
                store.patch_status(
                    job_id,
                    message="媒体已取得，正在本机读取字幕。",
                    progress={"stage": "transcribe_media", "completed": 2, "total": 4},
                )
                router = asr_router or ASRRouter()
                result = asyncio.run(
                    router.transcribe_path(
                        mode="local",
                        media_path=collection.audio_path,
                        content_type="audio/wav",
                        language=None,
                    )
                )
                if result is None:
                    raise ASRProviderError("本地 ASR 未返回可用字幕。")
                transcript = result.as_dict()

            transcript_text = str(transcript.get("text") or "").strip()
            if not transcript_text:
                raise ASRProviderError("字幕提取或转写结果为空。")
            transcript["character_count"] = len(transcript_text)
            source["transcript"] = transcript
            source["retrieval_status"] = "completed"
            content = source.get("content")
            if isinstance(content, dict):
                content["transcript_source"] = transcript.get("source")
                content["transcript_language"] = transcript.get("language")
                content["transcript_character_count"] = len(transcript_text)
            source.setdefault("evidence", []).append(
                {
                    "type": "timed_transcript",
                    "label": "自动取得的带时间码字幕",
                    "value": transcript.get("source"),
                    "confidence": "runtime_generated",
                }
            )
            missing = ["画面 OCR 与镜头结构分析", "公开评论采集"]
            source["missing"] = missing
            source["acquisition_timings"] = collection.timings
            artifacts.append(
                store.write_raw_artifact(job_id, "transcript.json", transcript)
            )
            lifecycle_status = "completed"
            message = f"{platform_name} 公开媒体和带时间码字幕已自动取得。"
            analysis_ready = True
            acquisition_mode = str(
                source.get("content", {}).get("transcript_source")
                or "public_media_and_local_asr"
            )
        else:
            source = _source_for_submission(platform, url, tiktok_adapter)
            missing = [
                "实时公开页面采集器",
                "视频字幕或可转写媒体",
            ]
            if int(request.get("item_limit", 1)) > 1:
                missing.append("主页作品列表与多条作品采集器")
            source["missing"] = missing
            lifecycle_status = "needs_input"
            message = (
                "已隔离保存链接证据；实时平台采集器尚未接入，"
                "未生成视频内容或伪造完整分析。"
            )
            analysis_ready = False
            acquisition_mode = "submission_inspection"

        source_artifact = store.write_raw_artifact(job_id, "source.json", source)
        artifacts.append(source_artifact)
        has_item = bool(case) or (
            platform in {"douyin", "tiktok"} and lifecycle_status == "completed"
        )
        stable_id = _stable_id(platform, source, url)
        manifest = {
            "schema_version": ACQUISITION_SCHEMA_VERSION,
            "job_id": job_id,
            "status": lifecycle_status,
            "platform": platform,
            "canonical_url": url,
            "stable_id": stable_id,
            "acquisition_mode": acquisition_mode,
            "item_limit_requested": int(request.get("item_limit", 1)),
            "analysis_ready": analysis_ready,
            "evidence_summary": {
                "item_count": 1 if has_item else 0,
                "evidence_count": len(source.get("evidence", [])),
                "missing_count": len(missing),
                "missing": missing,
            },
            "items": [_compact_item(platform, source, url)] if has_item else [],
            "raw_artifacts": artifacts,
            "context_policy": {
                "default_input": "evidence_manifest_only",
                "raw_artifacts_included": False,
                "retrieval": "按 artifact URL 定向读取，不把 Worker 日志放入分析上下文。",
            },
            "completed_at": utc_now(),
        }
        store.write_manifest(job_id, manifest)
        status = store.patch_status(
            job_id,
            status=lifecycle_status,
            message=message,
            progress={"stage": lifecycle_status, "completed": 1, "total": 1},
            manifest_url=f"/api/acquisition/jobs/{job_id}/manifest",
            missing=missing,
            artifacts=artifacts,
        )
        if status["status"] == "completed":
            store.store_cache_entry(str(request["cache_key"]), job_id)
            if stable_id:
                store.store_stable_cache_entry(platform, stable_id, job_id)
    except TikTokProxyUnavailableError as exc:
        traceback.print_exc()
        try:
            store.patch_status(
                job_id,
                status="needs_input",
                message=str(exc),
                error_type=type(exc).__name__,
                progress={"stage": "network_or_limit_required", "completed": 0, "total": 4},
                missing=["电脑 VPN/系统代理可用状态"],
            )
        except Exception:
            traceback.print_exc()
    except DouyinProviderUnavailableError as exc:
        traceback.print_exc()
        try:
            link_verified = bool(request.get("link_verified"))
            if link_verified:
                message = (
                    "链接已识别为公开抖音作品，但当前公共采集通道暂时不可用。"
                    "请稍后重试，或检查电脑网络后再试。"
                )
                missing = ["抖音公开采集通道"]
            else:
                message = (
                    "暂时无法确认这是公开的单条作品链接；它可能已失效、受限，"
                    "也可能是公共采集通道暂时不可用。"
                )
                missing = [
                    "可解析的公开抖音单条作品链接或可用公共 Provider",
                    "隔离浏览器公开采集通道",
                ]
            store.patch_status(
                job_id,
                status="needs_input",
                message=message,
                error_type=type(exc).__name__,
                progress={"stage": "public_acquisition_unavailable", "completed": 0, "total": 4},
                missing=missing,
            )
        except Exception:
            traceback.print_exc()
    except (DouyinMediaError, TikTokMediaError, ASRProviderError) as exc:
        traceback.print_exc()
        try:
            store.patch_status(
                job_id,
                status="failed",
                message=str(exc),
                error_type=type(exc).__name__,
                progress={"stage": "media_or_transcription_failed", "completed": 0, "total": 4},
            )
        except Exception:
            traceback.print_exc()
    except Exception as exc:
        traceback.print_exc()
        try:
            store.patch_status(
                job_id,
                status="failed",
                message="独立 Worker 执行失败，原始错误保存在 Worker 日志中。",
                error_type=type(exc).__name__,
                progress={"stage": "failed", "completed": 0, "total": 1},
            )
        except Exception:
            traceback.print_exc()
