from __future__ import annotations

import ipaddress
import json
import mimetypes
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.adapters import canonical_url, extract_aweme_id

from .tiktok_media import CollectedArtifact, CommandOutcome


class DouyinMediaError(RuntimeError):
    pass


class DouyinProviderUnavailableError(DouyinMediaError):
    pass


class DouyinBrowserUnavailableError(DouyinProviderUnavailableError):
    pass


class DouyinMediaDownloadError(DouyinMediaError):
    pass


class DouyinToolUnavailableError(DouyinMediaError):
    pass


@dataclass(frozen=True)
class DouyinMediaCollection:
    source: dict[str, Any]
    metadata_path: Path
    media_path: Path
    audio_path: Path
    caption_path: None
    native_transcript: None
    artifacts: tuple[CollectedArtifact, ...]
    timings: dict[str, float]


@dataclass(frozen=True)
class DouyinResolvedSubmission:
    canonical_url: str
    aweme_id: str | None
    link_verified: bool


CommandRunner = Callable[[list[str], Path, int], CommandOutcome]
UrlValidator = Callable[[str], bool]


def _default_command_runner(
    command: list[str], cwd: Path, timeout_seconds: int
) -> CommandOutcome:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        creationflags=creationflags,
        check=False,
    )
    return CommandOutcome(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _resolve_executable(configured: str | None, default_name: str) -> str:
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise DouyinToolUnavailableError(f"配置的 {default_name} 不存在。")
    resolved = shutil.which(default_name)
    if not resolved:
        raise DouyinToolUnavailableError(f"未找到 {default_name}。")
    return resolved


def _safe_command_error(outcome: CommandOutcome) -> str:
    message = " ".join((outcome.stderr or outcome.stdout).split())
    return message[-1200:] or f"exit_code={outcome.returncode}"


def _artifact_content_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _duration_seconds(value: Any) -> float:
    try:
        duration = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if duration > 1000:
        duration /= 1000
    return round(duration, 3)


def _is_public_http_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if parsed.username or parsed.password or (port and port not in {80, 443}):
        return False
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".local"):
        return False
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(item[4][0])
                for item in socket.getaddrinfo(
                    hostname,
                    port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            ]
        except (OSError, ValueError):
            return False
    return bool(addresses) and all(address.is_global for address in addresses)


def _is_douyin_url(value: str) -> bool:
    try:
        hostname = (urlsplit(value).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return (
        hostname == "douyin.com"
        or hostname.endswith(".douyin.com")
        or hostname == "iesdouyin.com"
        or hostname.endswith(".iesdouyin.com")
    )


def _clean_douyin_url(value: str) -> str:
    parsed = urlsplit(canonical_url(value))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def resolve_douyin_submission(
    raw_url: str,
    *,
    client_factory: Callable[..., httpx.Client] | None = None,
    timeout_seconds: int = 12,
    url_validator: UrlValidator = _is_public_http_url,
) -> DouyinResolvedSubmission:
    """Resolve a public Douyin share redirect without using browser state."""
    submitted_url = _clean_douyin_url(raw_url)
    aweme_id = extract_aweme_id(submitted_url)
    if aweme_id:
        return DouyinResolvedSubmission(
            canonical_url=f"https://www.douyin.com/video/{aweme_id}",
            aweme_id=aweme_id,
            link_verified=False,
        )
    if not _is_douyin_url(submitted_url) or not url_validator(submitted_url):
        return DouyinResolvedSubmission(submitted_url, None, False)

    factory = client_factory or httpx.Client
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36"
        )
    }
    current = submitted_url
    try:
        with factory(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            headers=headers,
        ) as client:
            for _ in range(4):
                response = client.get(current)
                location = response.headers.get("location") or ""
                if not response.is_redirect or not location:
                    break
                target = _clean_douyin_url(urljoin(current, location))
                if not _is_douyin_url(target) or not url_validator(target):
                    break
                current = target
                aweme_id = extract_aweme_id(current)
                if aweme_id:
                    return DouyinResolvedSubmission(
                        canonical_url=f"https://www.douyin.com/video/{aweme_id}",
                        aweme_id=aweme_id,
                        link_verified=True,
                    )
    except (httpx.HTTPError, OSError, ValueError):
        pass
    return DouyinResolvedSubmission(submitted_url, None, False)


class DouyinCommunityClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        client_factory: Callable[..., httpx.Client] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        url_validator: UrlValidator = _is_public_http_url,
    ) -> None:
        self.base_url = (
            base_url
            or os.getenv("PROJECT024_DOUYIN_PUBLIC_API_BASE")
            or "https://douyin.wtf"
        ).rstrip("/")
        self.client_factory = client_factory or httpx.Client
        self.sleep = sleep
        self.url_validator = url_validator

    @staticmethod
    def _request_headers() -> dict[str, str]:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            ),
            "Referer": "https://www.douyin.com/",
        }

    def fetch_and_download(
        self,
        *,
        submitted_url: str,
        destination: Path,
        timeout_seconds: int,
        max_bytes: int,
        max_metadata_bytes: int = 2 * 1024 * 1024,
    ) -> dict[str, Any]:
        if not self.url_validator(self.base_url):
            raise DouyinProviderUnavailableError("抖音公共 Provider 地址不安全。")
        with self.client_factory(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            headers=self._request_headers(),
        ) as client:
            data = self._fetch_metadata(
                client,
                submitted_url=submitted_url,
                max_bytes=max_metadata_bytes,
            )
            video = data.get("video") if isinstance(data.get("video"), dict) else {}
            duration = _duration_seconds(data.get("duration") or video.get("duration"))
            candidates = self._media_candidates(video)
            if not candidates:
                raise DouyinMediaDownloadError("抖音公共 Provider 没有返回视频媒体。")
            self._download_media(client, candidates, destination, max_bytes)

        author = data.get("author") if isinstance(data.get("author"), dict) else {}
        statistics = (
            data.get("statistics") if isinstance(data.get("statistics"), dict) else {}
        )
        aweme_id = str(data.get("aweme_id") or data.get("group_id") or "").strip()
        webpage_url = (
            f"https://www.douyin.com/video/{aweme_id}"
            if aweme_id.isdigit()
            else submitted_url
        )
        return {
            "id": aweme_id or None,
            "title": data.get("desc") or data.get("item_title"),
            "description": data.get("desc") or data.get("item_title"),
            "duration": duration,
            "uploader": author.get("nickname"),
            "uploader_id": author.get("unique_id"),
            "webpage_url": webpage_url,
            "language": "zh",
            "view_count": statistics.get("play_count"),
            "like_count": statistics.get("digg_count"),
            "comment_count": statistics.get("comment_count"),
            "share_count": statistics.get("share_count"),
            "save_count": statistics.get("collect_count"),
            "timestamp": data.get("create_time"),
            "_retrieval_provider": "douyin_wtf_community_api",
        }

    def download_public_media(
        self,
        *,
        media_url: str,
        destination: Path,
        timeout_seconds: int,
        max_bytes: int,
    ) -> None:
        if not self.url_validator(media_url):
            raise DouyinMediaDownloadError("隔离浏览器返回的媒体地址不安全。")
        with self.client_factory(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            headers=self._request_headers(),
        ) as client:
            self._download_media(client, [media_url], destination, max_bytes)

    def _fetch_metadata(
        self,
        client: httpx.Client,
        *,
        submitted_url: str,
        max_bytes: int,
    ) -> dict[str, Any]:
        endpoint = f"{self.base_url}/api/hybrid/video_data"
        for attempt in range(2):
            try:
                response = client.get(
                    endpoint,
                    params={"url": submitted_url, "minimal": "false"},
                    follow_redirects=True,
                )
            except httpx.HTTPError as exc:
                raise DouyinProviderUnavailableError(
                    f"抖音公共 Provider 请求失败：{type(exc).__name__}"
                ) from exc
            if len(response.content) > max_bytes:
                raise DouyinProviderUnavailableError("抖音公共 Provider 元数据过大。")
            try:
                payload = response.json()
            except ValueError as exc:
                raise DouyinProviderUnavailableError(
                    "抖音公共 Provider 没有返回 JSON。"
                ) from exc
            message = str(
                payload.get("message") or payload.get("detail") or payload.get("msg") or ""
            )
            limited = response.status_code in {400, 429} and "limit" in message.lower()
            if limited and attempt == 0:
                self.sleep(1.25)
                continue
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                raise DouyinProviderUnavailableError(
                    f"抖音公共 Provider 返回 HTTP {response.status_code}。"
                ) from exc
            data = payload.get("data") if isinstance(payload, dict) else None
            if payload.get("code") != 200 or not isinstance(data, dict):
                raise DouyinProviderUnavailableError(
                    "抖音公共 Provider 没有返回可用作品数据。"
                )
            return data
        raise DouyinProviderUnavailableError("抖音公共 Provider 当前限流。")

    @staticmethod
    def _media_candidates(video: dict[str, Any]) -> list[str]:
        candidates: list[str] = []
        for key in ("play_addr", "download_addr"):
            address = video.get(key)
            if not isinstance(address, dict):
                continue
            values = address.get("url_list")
            if not isinstance(values, list):
                continue
            for value in values:
                if isinstance(value, str) and value and value not in candidates:
                    candidates.append(value)
        return candidates

    def _download_media(
        self,
        client: httpx.Client,
        candidates: list[str],
        destination: Path,
        max_bytes: int,
    ) -> None:
        temporary = destination.with_suffix(destination.suffix + ".download")
        last_error = ""
        try:
            for candidate in candidates:
                current = candidate
                if not self.url_validator(current):
                    continue
                for _ in range(4):
                    try:
                        with client.stream(
                            "GET", current, follow_redirects=False
                        ) as response:
                            if response.is_redirect:
                                location = response.headers.get("location") or ""
                                current = urljoin(current, location)
                                if not self.url_validator(current):
                                    last_error = "媒体重定向地址不安全"
                                    break
                                continue
                            response.raise_for_status()
                            content_type = (
                                response.headers.get("content-type") or ""
                            ).split(";", 1)[0].lower()
                            if content_type and not (
                                content_type.startswith("video/")
                                or content_type == "application/octet-stream"
                            ):
                                last_error = "媒体响应类型无效"
                                break
                            content_length = int(
                                response.headers.get("content-length") or 0
                            )
                            if content_length > max_bytes:
                                raise DouyinMediaDownloadError(
                                    "抖音媒体文件超过本机下载安全上限 "
                                    f"{max_bytes // (1024 * 1024)} MiB。"
                                )
                            size_bytes = 0
                            with temporary.open("wb") as handle:
                                for chunk in response.iter_bytes(1024 * 1024):
                                    size_bytes += len(chunk)
                                    if size_bytes > max_bytes:
                                        raise DouyinMediaDownloadError(
                                            "抖音媒体文件超过本机下载安全上限 "
                                            f"{max_bytes // (1024 * 1024)} MiB。"
                                        )
                                    handle.write(chunk)
                            if size_bytes == 0:
                                last_error = "媒体下载结果为空"
                                break
                            os.replace(temporary, destination)
                            return
                    except DouyinMediaError:
                        raise
                    except (httpx.HTTPError, OSError, ValueError) as exc:
                        last_error = type(exc).__name__
                        break
            raise DouyinMediaDownloadError(
                f"抖音媒体下载失败：{last_error or '没有安全的公开媒体地址'}。"
            )
        finally:
            temporary.unlink(missing_ok=True)


def _resolve_browser_executable(configured: str | None = None) -> str:
    explicit = configured or os.getenv("PROJECT024_CHROMIUM_EXE")
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise DouyinBrowserUnavailableError("配置的隔离浏览器不存在。")

    candidates: list[Path] = []
    program_files = os.getenv("PROGRAMFILES")
    program_files_x86 = os.getenv("PROGRAMFILES(X86)")
    local_app_data = os.getenv("LOCALAPPDATA")
    if program_files:
        candidates.append(Path(program_files) / "Google/Chrome/Application/chrome.exe")
    if program_files_x86:
        candidates.append(
            Path(program_files_x86) / "Microsoft/Edge/Application/msedge.exe"
        )
    if local_app_data:
        candidates.extend(
            sorted(
                (Path(local_app_data) / "ms-playwright").glob(
                    "chromium-*/chrome-win64/chrome.exe"
                ),
                reverse=True,
            )
        )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    raise DouyinBrowserUnavailableError("未找到可用于公开采集的 Chrome 或 Edge。")


class DouyinBrowserClient:
    def __init__(
        self,
        *,
        command_runner: CommandRunner = _default_command_runner,
        node_executable: str | None = None,
        browser_executable: str | None = None,
        probe_script: Path | None = None,
        url_validator: UrlValidator = _is_public_http_url,
    ) -> None:
        self.command_runner = command_runner
        self.node_executable = node_executable
        self.browser_executable = browser_executable
        self.probe_script = probe_script or Path(__file__).with_name(
            "douyin_browser_probe.cjs"
        )
        self.url_validator = url_validator

    def inspect(self, submitted_url: str, *, timeout_seconds: int) -> dict[str, Any]:
        node = _resolve_executable(self.node_executable, "node")
        browser = _resolve_browser_executable(self.browser_executable)
        if not self.probe_script.is_file():
            raise DouyinBrowserUnavailableError("隔离浏览器探测脚本不存在。")
        outcome = self.command_runner(
            [
                node,
                str(self.probe_script),
                "--url",
                submitted_url,
                "--browser",
                browser,
                "--timeout-ms",
                str(timeout_seconds * 1000),
            ],
            self.probe_script.parents[2],
            timeout_seconds + 15,
        )
        try:
            payload = json.loads(outcome.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise DouyinBrowserUnavailableError(
                "隔离浏览器没有返回可用结果。"
            ) from exc
        if outcome.returncode != 0 or payload.get("status") != "ok":
            raise DouyinBrowserUnavailableError(
                str(payload.get("message") or "隔离浏览器未能取得公开媒体。")
            )
        media_url = str(payload.get("media_url") or "")
        if not self.url_validator(media_url):
            raise DouyinBrowserUnavailableError(
                "隔离浏览器返回的媒体地址未通过安全检查。"
            )
        audio_url = str(payload.get("audio_url") or "")
        if audio_url and not self.url_validator(audio_url):
            raise DouyinBrowserUnavailableError(
                "隔离浏览器返回的独立音轨地址未通过安全检查。"
            )
        return payload


class DouyinMediaCollector:
    def __init__(
        self,
        *,
        command_runner: CommandRunner = _default_command_runner,
        ffmpeg_executable: str | None = None,
        ffprobe_executable: str | None = None,
        public_client: DouyinCommunityClient | None = None,
        browser_client: DouyinBrowserClient | None = None,
        timeout_seconds: int = 240,
        max_media_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.command_runner = command_runner
        self.ffmpeg_executable = ffmpeg_executable
        self.ffprobe_executable = ffprobe_executable
        self.public_client = public_client or DouyinCommunityClient()
        self.browser_client = browser_client or DouyinBrowserClient()
        self.timeout_seconds = timeout_seconds
        self.max_media_bytes = max_media_bytes

    def _merge_separate_audio(
        self,
        *,
        ffmpeg: str,
        media_path: Path,
        separate_audio_path: Path,
        raw_dir: Path,
    ) -> None:
        muxed_path = raw_dir / "douyin_source.muxed.mp4"
        try:
            outcome = self.command_runner(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(media_path),
                    "-i",
                    str(separate_audio_path),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-shortest",
                    "-movflags",
                    "+faststart",
                    str(muxed_path),
                ],
                raw_dir,
                min(self.timeout_seconds, 180),
            )
            if (
                outcome.returncode != 0
                or not muxed_path.is_file()
                or muxed_path.stat().st_size == 0
            ):
                raise DouyinMediaDownloadError(
                    "抖音分离音视频合并失败：" + _safe_command_error(outcome)
                )
            if muxed_path.stat().st_size > self.max_media_bytes:
                raise DouyinMediaDownloadError(
                    "抖音合并媒体文件超过本机下载安全上限 "
                    f"{self.max_media_bytes // (1024 * 1024)} MiB。"
                )
            os.replace(muxed_path, media_path)
        finally:
            muxed_path.unlink(missing_ok=True)
            separate_audio_path.unlink(missing_ok=True)

    def collect(self, url: str, raw_dir: Path) -> DouyinMediaCollection:
        started = time.perf_counter()
        raw_dir = raw_dir.resolve()
        raw_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg = _resolve_executable(self.ffmpeg_executable, "ffmpeg")
        ffprobe = _resolve_executable(self.ffprobe_executable, "ffprobe")
        metadata_path = raw_dir / "douyin_source.info.json"
        media_path = raw_dir / "douyin_source.mp4"

        download_started = time.perf_counter()
        separate_audio_path: Path | None = None
        try:
            metadata = self.public_client.fetch_and_download(
                submitted_url=url,
                destination=media_path,
                timeout_seconds=min(self.timeout_seconds, 18),
                max_bytes=self.max_media_bytes,
            )
        except DouyinProviderUnavailableError as provider_error:
            try:
                browser_result = self.browser_client.inspect(
                    url, timeout_seconds=min(self.timeout_seconds, 55)
                )
                media_url = str(browser_result.pop("media_url"))
                audio_url = str(browser_result.pop("audio_url", "") or "")
                self.public_client.download_public_media(
                    media_url=media_url,
                    destination=media_path,
                    timeout_seconds=min(self.timeout_seconds, 120),
                    max_bytes=self.max_media_bytes,
                )
                if audio_url:
                    separate_audio_path = raw_dir / "douyin_separate_audio.m4a"
                    self.public_client.download_public_media(
                        media_url=audio_url,
                        destination=separate_audio_path,
                        timeout_seconds=min(self.timeout_seconds, 120),
                        max_bytes=self.max_media_bytes,
                    )
            except (DouyinProviderUnavailableError, DouyinMediaDownloadError) as exc:
                raise DouyinProviderUnavailableError(
                    "抖音链接已识别，但公共 Provider 和隔离浏览器均未取得媒体。"
                ) from exc
            metadata = {
                "id": browser_result.get("aweme_id"),
                "title": browser_result.get("title"),
                "description": browser_result.get("description"),
                "duration": _duration_seconds(browser_result.get("duration")),
                "uploader": browser_result.get("uploader"),
                "uploader_id": None,
                "webpage_url": browser_result.get("webpage_url") or url,
                "language": browser_result.get("language") or "zh",
                "view_count": None,
                "like_count": None,
                "comment_count": None,
                "share_count": None,
                "save_count": None,
                "_retrieval_provider": "douyin_public_ephemeral_browser",
                "_provider_fallback": type(provider_error).__name__,
            }
            if separate_audio_path is not None:
                self._merge_separate_audio(
                    ffmpeg=ffmpeg,
                    media_path=media_path,
                    separate_audio_path=separate_audio_path,
                    raw_dir=raw_dir,
                )
                metadata["_media_assembly"] = "separate_browser_audio_merged"
        download_seconds = time.perf_counter() - download_started
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        probe_outcome = self.command_runner(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type",
                "-of",
                "json",
                str(media_path),
            ],
            raw_dir,
            min(self.timeout_seconds, 60),
        )
        try:
            probe_payload = json.loads(probe_outcome.stdout)
            decoded_duration = float(probe_payload["format"]["duration"])
            stream_types = {
                str(stream.get("codec_type") or "")
                for stream in probe_payload.get("streams", [])
                if isinstance(stream, dict)
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            decoded_duration = 0.0
            stream_types = set()
        if (
            probe_outcome.returncode != 0
            or decoded_duration <= 0
            or "video" not in stream_types
        ):
            raise DouyinMediaDownloadError("下载文件未通过 ffprobe 完整媒体检查。")
        if "audio" not in stream_types:
            raise DouyinMediaDownloadError(
                "抖音作品只取得了画面，没有取得可用音轨；平台可能使用了分离音视频流。"
            )
        metadata["decoded_media_duration_seconds"] = round(decoded_duration, 3)
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        audio_path = raw_dir / "douyin_audio_16k.wav"
        audio_started = time.perf_counter()
        audio_outcome = self.command_runner(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(media_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(audio_path),
            ],
            raw_dir,
            min(self.timeout_seconds, 120),
        )
        audio_seconds = time.perf_counter() - audio_started
        if (
            audio_outcome.returncode != 0
            or not audio_path.is_file()
            or audio_path.stat().st_size == 0
        ):
            raise DouyinMediaDownloadError(
                "抖音音轨提取失败：" + _safe_command_error(audio_outcome)
            )

        source = self._source_from_metadata(url, metadata)
        artifacts = (
            CollectedArtifact(metadata_path, "public_metadata", "application/json"),
            CollectedArtifact(media_path, "source_media", _artifact_content_type(media_path)),
            CollectedArtifact(audio_path, "asr_input", "audio/wav"),
        )
        return DouyinMediaCollection(
            source=source,
            metadata_path=metadata_path,
            media_path=media_path,
            audio_path=audio_path,
            caption_path=None,
            native_transcript=None,
            artifacts=artifacts,
            timings={
                "download_seconds": round(download_seconds, 3),
                "audio_extract_seconds": round(audio_seconds, 3),
                "total_seconds": round(time.perf_counter() - started, 3),
            },
        )

    @staticmethod
    def _source_from_metadata(
        submitted_url: str, metadata: dict[str, Any]
    ) -> dict[str, Any]:
        webpage_url = str(metadata.get("webpage_url") or submitted_url)
        uploader = str(metadata.get("uploader") or "")
        handle = str(metadata.get("uploader_id") or "")
        if handle and not handle.startswith("@"):
            handle = f"@{handle}"
        retrieval_provider = str(metadata.get("_retrieval_provider") or "")
        browser_fallback = retrieval_provider == "douyin_public_ephemeral_browser"
        return {
            "platform": "douyin",
            "url": webpage_url,
            "submitted_url": submitted_url,
            "aweme_id": str(metadata.get("id") or "") or None,
            "author": {
                "name": uploader or handle or None,
                "handle": handle or None,
            },
            "content": {
                "title": metadata.get("title"),
                "description": metadata.get("description"),
                "duration_seconds": metadata.get("duration"),
                "decoded_media_duration_seconds": metadata.get(
                    "decoded_media_duration_seconds"
                ),
                "language": metadata.get("language"),
                "transcript_source": "local_asr_required",
            },
            "metrics": {
                "views": metadata.get("view_count"),
                "likes": metadata.get("like_count"),
                "comments": metadata.get("comment_count"),
                "favorites": metadata.get("save_count"),
                "shares": metadata.get("share_count"),
            },
            "acquisition_mode": (
                "public_browser_media_download"
                if browser_fallback
                else "public_media_download"
            ),
            "retrieval_status": "media_completed",
            "network": {
                "proxy_required": False,
                "proxy_source": (
                    "direct_ephemeral_browser"
                    if browser_fallback
                    else "direct_community_provider"
                ),
                "proxy_reachable": True,
            },
            "evidence": [
                {
                    "type": "public_url",
                    "label": "用户提交的抖音链接",
                    "value": submitted_url,
                    "confidence": "submitted",
                },
                {
                    "type": "retrieved_public_metadata",
                    "label": (
                        "隔离浏览器取得的抖音公开页面证据"
                        if browser_fallback
                        else "社区 Provider 返回的抖音公开元数据"
                    ),
                    "value": webpage_url,
                    "confidence": (
                        "retrieved_ephemeral_browser"
                        if browser_fallback
                        else "retrieved_community_provider"
                    ),
                },
                {
                    "type": "transcript_path",
                    "label": "字幕获取方式",
                    "value": "local_asr_required",
                    "confidence": "pending_runtime_step",
                },
            ],
            "missing": ["本地 ASR 转写"],
            "retrieval_provider": metadata.get("_retrieval_provider"),
        }
