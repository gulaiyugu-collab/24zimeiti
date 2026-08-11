from __future__ import annotations

import asyncio
import importlib.util
import math
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

import httpx

from app.models import ASRMode


ProviderName = Literal["external_api", "local"]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SAFE_TEMP_SUFFIX = re.compile(r"^\.[a-z0-9]{1,10}$")
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[^\s,;]+"),
    re.compile(r"(?i)\b(?:sk|rk|pk)-[a-z0-9_-]{8,}\b"),
    re.compile(
        r"(?i)((?:api[_-]?key|access[_-]?token|token|secret)\s*[:=]\s*[\"']?)"
        r"[^\"',;\s}]+"
    ),
    re.compile(
        r"(?i)([?&](?:api[_-]?key|access[_-]?token|token|secret)=)[^&\s]+"
    ),
)
_NVIDIA_DLL_HANDLES: list[Any] = []


class ASRProviderError(RuntimeError):
    """A provider was selected but could not complete transcription."""


@dataclass(frozen=True)
class ProviderAvailability:
    provider: ProviderName
    configured: bool
    reason: str
    model: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "configured": self.configured,
            "reason": self.reason,
            "model": self.model,
        }


@dataclass(frozen=True)
class TranscriptionResult:
    transcript: str
    provider: ProviderName
    model: str
    language: str | None
    segments: list[dict[str, Any]] | None
    segments_status: str
    confidence: float | None
    confidence_status: str
    provider_metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "completed",
            "source": "local_asr" if self.provider == "local" else self.provider,
            "text": self.transcript,
            "provider": self.provider,
            "model": self.model,
            "language": self.language,
            "segments": self.segments,
            "segments_status": self.segments_status,
            "confidence": self.confidence,
            "confidence_status": self.confidence_status,
            "provider_metadata": self.provider_metadata,
        }


class ASRProvider(Protocol):
    name: ProviderName

    def availability(self) -> ProviderAvailability: ...

    async def transcribe(
        self,
        media: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> TranscriptionResult: ...


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    if not math.isfinite(value):
        return default
    return min(max(value, minimum), maximum)


def _env_text(name: str, default: str) -> str:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def _sanitize_error_text(value: object, *secrets: str | None) -> str:
    message = str(value or "")
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[redacted]")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            message = pattern.sub(r"\1[redacted]", message)
        else:
            message = pattern.sub("[redacted]", message)
    return " ".join(message.split())[:500]


def _external_key() -> str | None:
    value = os.getenv("PROJECT024_ASR_API_KEY") or os.getenv("OPENAI_API_KEY")
    value = value.strip() if value else ""
    return value or None


def _external_model() -> str:
    return _env_text("PROJECT024_ASR_MODEL", "whisper-1")


def _external_endpoint() -> str:
    endpoint = os.getenv("PROJECT024_ASR_ENDPOINT")
    if endpoint and endpoint.strip():
        return endpoint.strip().rstrip("/")
    base_url = _env_text(
        "PROJECT024_ASR_BASE_URL", "https://api.openai.com/v1"
    ).rstrip("/")
    if base_url.endswith("/audio/transcriptions"):
        return base_url
    return f"{base_url}/audio/transcriptions"


def _safe_provider_error(response: httpx.Response, secret: str) -> str:
    message = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "")
            elif error:
                message = str(error)
    except (ValueError, TypeError):
        message = ""

    message = _sanitize_error_text(message, secret)
    suffix = f"：{message}" if message else ""
    return f"外部 ASR 返回 HTTP {response.status_code}{suffix}"


class ExternalAPIProvider:
    """OpenAI-compatible /audio/transcriptions provider."""

    name: ProviderName = "external_api"

    def __init__(
        self,
        client_factory: Callable[..., httpx.AsyncClient] | None = None,
    ) -> None:
        self.client_factory = client_factory or httpx.AsyncClient

    def availability(self) -> ProviderAvailability:
        configured = bool(_external_key())
        reason = (
            "外部 ASR 已通过服务端环境变量配置。"
            if configured
            else "未检测到 PROJECT024_ASR_API_KEY 或 OPENAI_API_KEY。"
        )
        return ProviderAvailability(self.name, configured, reason, _external_model())

    async def transcribe(
        self,
        media: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> TranscriptionResult:
        secret = _external_key()
        if not secret:
            raise ASRProviderError("外部 ASR 未配置服务端密钥。")

        model = _external_model()
        fields: dict[str, str] = {
            "model": model,
            "response_format": _env_text(
                "PROJECT024_ASR_RESPONSE_FORMAT", "verbose_json"
            ),
        }
        if language:
            fields["language"] = language

        timeout = _env_float("PROJECT024_ASR_TIMEOUT_SECONDS", 120.0, 5.0, 600.0)
        try:
            async with self.client_factory(timeout=timeout) as client:
                response = await client.post(
                    _external_endpoint(),
                    headers={"Authorization": f"Bearer {secret}"},
                    data=fields,
                    files={"file": (filename, media, content_type)},
                )
        except httpx.TimeoutException as exc:
            raise ASRProviderError("外部 ASR 请求超时。") from exc
        except httpx.HTTPError as exc:
            raise ASRProviderError(f"外部 ASR 网络请求失败：{type(exc).__name__}") from exc
        except Exception as exc:
            raise ASRProviderError(f"外部 ASR 请求失败：{type(exc).__name__}") from exc

        if response.status_code >= 400:
            raise ASRProviderError(_safe_provider_error(response, secret))

        payload: dict[str, Any]
        try:
            decoded = response.json()
            if isinstance(decoded, dict):
                payload = decoded
            elif isinstance(decoded, str):
                payload = {"text": decoded}
            else:
                payload = {}
        except ValueError:
            content_type_header = response.headers.get("content-type", "").lower()
            if "text/plain" not in content_type_header:
                raise ASRProviderError("外部 ASR 返回了无法解析的非 JSON 响应。")
            payload = {"text": response.text}

        transcript = str(payload.get("text") or "").strip()
        if not transcript:
            raise ASRProviderError("外部 ASR 响应没有可用 transcript。")

        raw_segments = payload.get("segments")
        segments = raw_segments if isinstance(raw_segments, list) else None
        return TranscriptionResult(
            transcript=transcript,
            provider=self.name,
            model=model,
            language=str(payload.get("language") or language or "") or None,
            segments=segments,
            segments_status="provided" if segments is not None else "not_provided_by_provider",
            confidence=None,
            confidence_status="not_provided_by_provider",
            provider_metadata={
                "duration_seconds": payload.get("duration"),
                "response_format": fields["response_format"],
            },
        )


def _local_model_name() -> str:
    return _env_text("PROJECT024_LOCAL_ASR_MODEL", "large-v3-turbo")


def _local_cache_dir() -> Path:
    configured = os.getenv("PROJECT024_LOCAL_ASR_DOWNLOAD_ROOT")
    if configured:
        path = Path(configured.strip()).expanduser()
        return path if path.is_absolute() else _PROJECT_ROOT / path
    return _PROJECT_ROOT / ".cache" / "faster-whisper"


@lru_cache(maxsize=1)
def _configure_nvidia_dll_search_path() -> tuple[str, ...]:
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return ()

    site_packages = Path(sys.prefix) / "Lib" / "site-packages"
    directories = tuple(
        site_packages / "nvidia" / package / "bin"
        for package in ("cublas", "cudnn", "cuda_nvrtc")
    )
    loaded: list[str] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        _NVIDIA_DLL_HANDLES.append(os.add_dll_directory(str(directory)))
        loaded.append(str(directory))
    current_path = os.environ.get("PATH", "")
    current_entries = {
        entry.rstrip("\\/").casefold()
        for entry in current_path.split(os.pathsep)
        if entry
    }
    prepend = [
        directory
        for directory in loaded
        if directory.rstrip("\\/").casefold() not in current_entries
    ]
    if prepend:
        os.environ["PATH"] = os.pathsep.join([*prepend, current_path])
    return tuple(loaded)


@lru_cache(maxsize=4)
def _load_local_model(
    model_name: str,
    device: str,
    compute_type: str,
    download_root: str,
) -> Any:
    _configure_nvidia_dll_search_path()
    from faster_whisper import WhisperModel

    return WhisperModel(
        model_name,
        device=device,
        compute_type=compute_type,
        download_root=download_root,
    )


class LocalProvider:
    """Local faster-whisper provider with recorded CUDA-to-CPU fallback."""

    name: ProviderName = "local"

    def availability(self) -> ProviderAvailability:
        configured = importlib.util.find_spec("faster_whisper") is not None
        reason = (
            "已安装 faster-whisper。"
            if configured
            else "未安装可选依赖 faster-whisper。"
        )
        return ProviderAvailability(self.name, configured, reason, _local_model_name())

    async def transcribe(
        self,
        media: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> TranscriptionResult:
        if not self.availability().configured:
            raise ASRProviderError("本地 ASR 不可用：未安装 faster-whisper。")
        return await asyncio.to_thread(
            self._transcribe_sync,
            media,
            filename,
            language,
        )

    async def transcribe_path(
        self,
        media_path: Path,
        language: str | None,
    ) -> TranscriptionResult:
        if not self.availability().configured:
            raise ASRProviderError("本地 ASR 不可用：未安装 faster-whisper。")
        return await asyncio.to_thread(
            self._transcribe_path_sync,
            media_path.resolve(),
            language,
        )

    def _transcribe_sync(
        self,
        media: bytes,
        filename: str,
        language: str | None,
    ) -> TranscriptionResult:
        temp_dir = _PROJECT_ROOT / ".temp" / "asr"
        temp_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix.lower() or ".media"
        if not _SAFE_TEMP_SUFFIX.fullmatch(suffix):
            suffix = ".media"
        with tempfile.TemporaryDirectory(prefix="upload-", dir=temp_dir) as upload_dir:
            temp_path = Path(upload_dir) / f"media{suffix}"
            temp_path.write_bytes(media)
            return self._transcribe_path_sync(temp_path, language)

    def _transcribe_path_sync(
        self,
        media_path: Path,
        language: str | None,
    ) -> TranscriptionResult:
        if not media_path.is_file() or media_path.stat().st_size == 0:
            raise ASRProviderError("本地 ASR 输入媒体不存在或为空。")
        cache_dir = _local_cache_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        model_name = _local_model_name()
        requested_device = _env_text("PROJECT024_LOCAL_ASR_DEVICE", "cuda").lower()
        requested_compute = os.getenv("PROJECT024_LOCAL_ASR_COMPUTE_TYPE", "").strip()
        allow_cpu_fallback = _env_text(
            "PROJECT024_LOCAL_ASR_ALLOW_CPU_FALLBACK", "1"
        ).lower() not in {"0", "false", "no"}
        if requested_device in {"auto", "cuda"}:
            candidates = [
                ("cuda", requested_compute or "int8_float16"),
            ]
            if allow_cpu_fallback:
                candidates.append(("cpu", "int8"))
        else:
            candidates = [(requested_device, requested_compute or "int8")]

        failures: list[dict[str, str]] = []
        started = time.perf_counter()
        for device, compute_type in candidates:
            try:
                model_started = time.perf_counter()
                model = _load_local_model(
                    model_name,
                    device,
                    compute_type,
                    str(cache_dir),
                )
                model_load_seconds = time.perf_counter() - model_started
                transcribe_started = time.perf_counter()
                iterator, info = model.transcribe(
                    str(media_path),
                    language=language,
                    vad_filter=True,
                    beam_size=5,
                    condition_on_previous_text=True,
                )
                normalized_segments: list[dict[str, Any]] = []
                transcript_parts: list[str] = []
                for segment in iterator:
                    text = str(segment.text or "").strip()
                    if text:
                        transcript_parts.append(text)
                    normalized_segments.append(
                        {
                            "id": getattr(segment, "id", None),
                            "start": getattr(segment, "start", None),
                            "end": getattr(segment, "end", None),
                            "text": text,
                            "avg_logprob": getattr(segment, "avg_logprob", None),
                            "no_speech_prob": getattr(segment, "no_speech_prob", None),
                        }
                    )
                transcribe_seconds = time.perf_counter() - transcribe_started
                transcript = " ".join(transcript_parts).strip()
                if not transcript:
                    raise ASRProviderError("本地 ASR 没有识别出可用文字。")
                detected_language = (
                    str(getattr(info, "language", "") or "") or language
                )
                metadata: dict[str, Any] = {
                    "language_probability": getattr(
                        info, "language_probability", None
                    ),
                    "duration_seconds": getattr(info, "duration", None),
                    "device_requested": requested_device,
                    "device": device,
                    "compute_type": compute_type,
                    "model_load_seconds": round(model_load_seconds, 3),
                    "transcribe_seconds": round(transcribe_seconds, 3),
                    "total_seconds": round(time.perf_counter() - started, 3),
                    "cache_dir": str(cache_dir),
                }
                if failures:
                    metadata["runtime_fallback"] = {
                        "from": [item["device"] for item in failures],
                        "reason": failures[-1]["error"],
                    }
                return TranscriptionResult(
                    transcript=transcript,
                    provider=self.name,
                    model=model_name,
                    language=detected_language,
                    segments=normalized_segments,
                    segments_status="provided",
                    confidence=None,
                    confidence_status=(
                        "not_provided_as_overall_transcript_confidence"
                    ),
                    provider_metadata=metadata,
                )
            except ASRProviderError:
                raise
            except Exception as exc:
                failures.append(
                    {
                        "device": device,
                        "error": _sanitize_error_text(exc),
                        "error_type": type(exc).__name__,
                    }
                )
                if device != candidates[-1][0]:
                    continue
                raise ASRProviderError(
                    "本地 ASR 执行失败："
                    + "；".join(
                        f"{item['device']}={item['error_type']}: {item['error']}"
                        for item in failures
                    )
                ) from exc


class ASRRouter:
    """External-first provider selection with a local optional fallback."""

    def __init__(
        self,
        external: ASRProvider | None = None,
        local: ASRProvider | None = None,
    ) -> None:
        self.external = external or ExternalAPIProvider()
        self.local = local or LocalProvider()

    def availabilities(self) -> list[ProviderAvailability]:
        return [self.external.availability(), self.local.availability()]

    def select(self, mode: ASRMode) -> ASRProvider | None:
        providers: dict[ProviderName, ASRProvider] = {
            "external_api": self.external,
            "local": self.local,
        }
        if mode == "disabled":
            return None
        if mode == "external":
            order: list[ProviderName] = ["external_api"]
        elif mode == "local":
            order = ["local"]
        else:
            order = ["external_api", "local"]
        return next(
            (
                providers[name]
                for name in order
                if providers[name].availability().configured
            ),
            None,
        )

    async def transcribe(
        self,
        mode: ASRMode,
        media: bytes,
        filename: str,
        content_type: str,
        language: str | None,
    ) -> TranscriptionResult | None:
        if mode != "auto":
            provider = self.select(mode)
            if provider is None:
                return None
            return await provider.transcribe(media, filename, content_type, language)

        failures: list[tuple[ProviderName, str]] = []
        for provider in (self.external, self.local):
            if not provider.availability().configured:
                continue
            try:
                result = await provider.transcribe(
                    media, filename, content_type, language
                )
            except ASRProviderError as exc:
                failures.append(
                    (provider.name, _sanitize_error_text(exc, _external_key()))
                )
                continue

            if failures:
                metadata = dict(result.provider_metadata)
                metadata["fallback"] = {
                    "from": [name for name, _ in failures],
                    "reason": failures[-1][1],
                }
                return TranscriptionResult(
                    transcript=result.transcript,
                    provider=result.provider,
                    model=result.model,
                    language=result.language,
                    segments=result.segments,
                    segments_status=result.segments_status,
                    confidence=result.confidence,
                    confidence_status=result.confidence_status,
                    provider_metadata=metadata,
                )
            return result

        if failures:
            details = "；".join(f"{name}: {message}" for name, message in failures)
            raise ASRProviderError(f"自动 ASR 的可用 provider 均执行失败：{details}")
        return None

    async def transcribe_path(
        self,
        mode: ASRMode,
        media_path: Path,
        content_type: str,
        language: str | None,
    ) -> TranscriptionResult | None:
        provider = self.select(mode)
        if provider is None:
            return None
        path_method = getattr(provider, "transcribe_path", None)
        if callable(path_method):
            return await path_method(media_path, language)
        return await provider.transcribe(
            media_path.read_bytes(),
            media_path.name,
            content_type,
            language,
        )

    def plan(self, mode: ASRMode, transcript_supplied: bool) -> dict[str, object]:
        providers = self.availabilities()
        provider_map = {item.provider: item for item in providers}

        if transcript_supplied:
            return {
                "status": "not_needed",
                "mode": mode,
                "selected_provider": "user_supplied_transcript",
                "external_api_preferred": True,
                "paid_api_called": False,
                "media_required": False,
                "providers": [item.as_dict() for item in providers],
                "message": "已收到用户提供的文字，本次未运行 ASR。",
            }

        if mode == "disabled":
            return {
                "status": "disabled",
                "mode": mode,
                "selected_provider": None,
                "external_api_preferred": True,
                "paid_api_called": False,
                "media_required": True,
                "providers": [item.as_dict() for item in providers],
                "message": "本次请求已关闭 ASR。",
            }

        if mode == "local":
            order: list[ProviderName] = ["local"]
        elif mode == "external":
            order = ["external_api"]
        else:
            order = ["external_api", "local"]

        selected = next(
            (name for name in order if provider_map[name].configured),
            None,
        )
        if selected:
            status = "needs_media"
            message = (
                f"已选择 {selected}；只有显式调用 /api/transcribe 上传媒体后才会执行。"
            )
        elif mode == "external":
            status = "not_configured"
            message = "外部 ASR 未配置服务端密钥。"
        elif mode == "local":
            status = "not_configured"
            message = "本地 ASR 不可用：未安装 faster-whisper。"
        else:
            status = "not_configured"
            message = "外部 ASR 未配置密钥，且本地 faster-whisper 不可用。"

        return {
            "status": status,
            "mode": mode,
            "selected_provider": selected,
            "provider_order": order,
            "external_api_preferred": True,
            "paid_api_called": False,
            "media_required": True,
            "providers": [item.as_dict() for item in providers],
            "message": message,
        }
