from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import httpx


_TIMING_RE = re.compile(
    r"(?P<start>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})\s+-->\s+"
    r"(?P<end>(?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})"
)
_TAG_RE = re.compile(r"<[^>]+>")
_MEDIA_SUFFIXES = {".mp4", ".webm", ".mkv", ".mov", ".m4v"}
_CAPTION_SUFFIXES = {".vtt", ".srt", ".json"}
_PUBLIC_METADATA_FIELDS = (
    "id",
    "title",
    "description",
    "duration",
    "uploader",
    "uploader_id",
    "channel",
    "channel_id",
    "webpage_url",
    "language",
    "view_count",
    "like_count",
    "comment_count",
    "repost_count",
    "share_count",
    "save_count",
    "collect_count",
    "favorite_count",
    "timestamp",
    "_retrieval_provider",
)


class TikTokMediaError(RuntimeError):
    pass


class TikTokProxyUnavailableError(TikTokMediaError):
    pass


class TikTokToolUnavailableError(TikTokMediaError):
    pass


class TikTokMediaDownloadError(TikTokMediaError):
    pass


@dataclass(frozen=True)
class ProxyConfig:
    url: str
    source: str


@dataclass(frozen=True)
class CommandOutcome:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class CollectedArtifact:
    path: Path
    role: str
    content_type: str


@dataclass(frozen=True)
class TikTokMediaCollection:
    source: dict[str, Any]
    metadata_path: Path
    media_path: Path
    audio_path: Path | None
    caption_path: Path | None
    native_transcript: dict[str, Any] | None
    artifacts: tuple[CollectedArtifact, ...]
    timings: dict[str, float]


@dataclass(frozen=True)
class CaptionProbeResult:
    path: Path
    transcript: dict[str, Any]
    elapsed_seconds: float


CommandRunner = Callable[[list[str], Path, int], CommandOutcome]
ProxyResolver = Callable[[], ProxyConfig | None]
ProxyProbe = Callable[[ProxyConfig], bool]


class TikWMClient:
    endpoint = "https://www.tikwm.com/api/"

    def fetch_and_download(
        self,
        *,
        submitted_url: str,
        proxy: ProxyConfig,
        destination: Path,
        timeout_seconds: int,
        max_bytes: int,
    ) -> dict[str, Any]:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            )
        }
        try:
            with httpx.Client(
                proxy=proxy.url or None,
                timeout=timeout_seconds,
                follow_redirects=True,
                trust_env=False,
                headers=headers,
            ) as client:
                response = client.post(
                    self.endpoint,
                    data={"url": submitted_url, "hd": "1"},
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if (
                    not isinstance(data, dict)
                    or payload.get("code") != 0
                ):
                    raise TikTokMediaDownloadError("TikWM 没有返回可用公开视频。")
                media_url = str(data.get("hdplay") or data.get("play") or "")
                if not media_url:
                    raise TikTokMediaDownloadError("TikWM 没有返回可下载媒体地址。")
                temporary = destination.with_suffix(destination.suffix + ".download")
                size_bytes = 0
                try:
                    with client.stream("GET", media_url) as media_response:
                        media_response.raise_for_status()
                        content_length = int(
                            media_response.headers.get("content-length") or 0
                        )
                        if content_length > max_bytes:
                            raise TikTokMediaDownloadError(
                                "TikTok 媒体文件超过本机下载安全上限 "
                                f"{max_bytes // (1024 * 1024)} MiB。"
                            )
                        with temporary.open("wb") as handle:
                            for chunk in media_response.iter_bytes(1024 * 1024):
                                size_bytes += len(chunk)
                                if size_bytes > max_bytes:
                                    raise TikTokMediaDownloadError(
                                        "TikTok 媒体文件超过本机下载安全上限 "
                                        f"{max_bytes // (1024 * 1024)} MiB。"
                                    )
                                handle.write(chunk)
                    if size_bytes == 0:
                        raise TikTokMediaDownloadError("TikTok 媒体下载结果为空。")
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
        except TikTokMediaError:
            raise
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            raise TikTokMediaDownloadError(
                f"TikWM 公开媒体请求失败：{type(exc).__name__}"
            ) from exc

        author = data.get("author") if isinstance(data.get("author"), dict) else {}
        return {
            "id": data.get("id"),
            "title": data.get("title"),
            "description": data.get("content_desc") or data.get("title"),
            "duration": data.get("duration"),
            "uploader": author.get("nickname"),
            "uploader_id": author.get("unique_id"),
            "webpage_url": submitted_url,
            "view_count": data.get("play_count"),
            "like_count": data.get("digg_count"),
            "comment_count": data.get("comment_count"),
            "repost_count": data.get("share_count"),
            "save_count": data.get("collect_count"),
            "download_count": data.get("download_count"),
            "timestamp": data.get("create_time"),
            "_retrieval_provider": "tikwm_public_api",
        }


def _normalize_proxy(raw_value: str, source: str) -> ProxyConfig | None:
    value = raw_value.strip()
    if not value:
        return None
    if ";" in value or re.search(r"(?:^|;)\s*(?:http|https|socks)=", value):
        entries: dict[str, str] = {}
        for part in value.split(";"):
            key, separator, entry = part.partition("=")
            if separator and entry.strip():
                entries[key.strip().lower()] = entry.strip()
        value = entries.get("https") or entries.get("http") or entries.get("socks") or ""
    if not value:
        return None
    if "://" not in value:
        value = f"http://{value}"
    parsed = urlsplit(value)
    if not parsed.hostname:
        return None
    return ProxyConfig(url=value, source=source)


def _read_windows_proxy() -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0]) == 1
            value = str(winreg.QueryValueEx(key, "ProxyServer")[0])
    except (OSError, ValueError):
        return ""
    return value if enabled else ""


def resolve_tiktok_proxy(
    environ: Mapping[str, str] | None = None,
    windows_proxy_reader: Callable[[], str] | None = None,
) -> ProxyConfig | None:
    values = environ if environ is not None else os.environ
    allow_direct = str(values.get("PROJECT024_TIKTOK_DIRECT", "")).lower()
    if allow_direct in {"1", "true", "yes"}:
        return ProxyConfig(url="", source="direct_tunnel")
    explicit = _normalize_proxy(
        str(values.get("PROJECT024_TIKTOK_PROXY", "")),
        "project_environment",
    )
    if explicit:
        return explicit
    reader = windows_proxy_reader or _read_windows_proxy
    return _normalize_proxy(reader(), "windows_system_proxy")


def probe_proxy(
    config: ProxyConfig,
    timeout_seconds: float = 1.5,
    connector: Callable[..., Any] = socket.create_connection,
) -> bool:
    if not config.url:
        return True
    parsed = urlsplit(config.url)
    host = parsed.hostname
    if not host:
        return False
    try:
        parsed_port = parsed.port
    except ValueError:
        return False
    if parsed_port:
        port = parsed_port
    elif parsed.scheme.startswith("socks"):
        port = 1080
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 80
    try:
        connection = connector((host, port), timeout_seconds)
        with closing(connection):
            return True
    except OSError:
        return False


def _default_command_runner(
    command: list[str],
    cwd: Path,
    timeout_seconds: int,
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
        raise TikTokToolUnavailableError(f"配置的 {default_name} 不存在。")
    resolved = shutil.which(default_name)
    if not resolved:
        raise TikTokToolUnavailableError(f"未找到 {default_name}。")
    return resolved


def _safe_command_error(outcome: CommandOutcome, proxy_url: str) -> str:
    message = " ".join((outcome.stderr or outcome.stdout).split())
    if proxy_url:
        message = message.replace(proxy_url, "[proxy]")
    return message[-1200:] or f"exit_code={outcome.returncode}"


def _timestamp_seconds(value: str) -> float:
    normalized = value.replace(",", ".")
    parts = normalized.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        hours = "0"
        minutes, seconds = parts
    return round(int(hours) * 3600 + int(minutes) * 60 + float(seconds), 3)


def _clean_caption_text(value: str) -> str:
    text = html.unescape(_TAG_RE.sub("", value)).replace("\u200b", "")
    return " ".join(text.split())


def parse_caption_file(path: Path) -> dict[str, Any] | None:
    if path.suffix.lower() == ".json":
        return _parse_json3_caption(path)
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    blocks = re.split(r"\r?\n\s*\r?\n", raw)
    segments: list[dict[str, Any]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next(
            (index for index, line in enumerate(lines) if _TIMING_RE.search(line)),
            None,
        )
        if timing_index is None:
            continue
        match = _TIMING_RE.search(lines[timing_index])
        if not match:
            continue
        text = _clean_caption_text(" ".join(lines[timing_index + 1 :]))
        if not text:
            continue
        segments.append(
            {
                "start": _timestamp_seconds(match.group("start")),
                "end": _timestamp_seconds(match.group("end")),
                "text": text,
            }
        )
    if not segments:
        return None
    return {
        "status": "completed",
        "source": "platform_caption",
        "language": _caption_language(path),
        "text": " ".join(segment["text"] for segment in segments),
        "segments": segments,
        "segments_status": "provided",
        "confidence": None,
        "confidence_status": "not_provided_by_platform",
    }


def _parse_json3_caption(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return None
    segments: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        pieces = event.get("segs")
        if not isinstance(pieces, list):
            continue
        text = _clean_caption_text(
            "".join(
                str(piece.get("utf8") or "")
                for piece in pieces
                if isinstance(piece, dict)
            )
        )
        if not text:
            continue
        start = float(event.get("tStartMs") or 0.0) / 1000
        duration = float(event.get("dDurationMs") or 0.0) / 1000
        segments.append(
            {
                "start": round(start, 3),
                "end": round(start + duration, 3),
                "text": text,
            }
        )
    if not segments:
        return None
    return {
        "status": "completed",
        "source": "platform_caption",
        "language": _caption_language(path),
        "text": " ".join(segment["text"] for segment in segments),
        "segments": segments,
        "segments_status": "provided",
        "confidence": None,
        "confidence_status": "not_provided_by_platform",
    }


def _caption_language(path: Path) -> str | None:
    parts = path.name.split(".")
    if len(parts) < 3:
        return None
    return parts[-2] or None


def _caption_priority(path: Path) -> tuple[int, str]:
    language = (_caption_language(path) or "").lower()
    priorities = ("zh-hans", "zh-cn", "zh", "en", "ms")
    rank = next(
        (index for index, prefix in enumerate(priorities) if language.startswith(prefix)),
        len(priorities),
    )
    return rank, path.name


def _artifact_content_type(path: Path) -> str:
    if path.suffix.lower() == ".vtt":
        return "text/vtt"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


def _public_metadata_record(
    metadata: dict[str, Any],
    submitted_url: str,
) -> dict[str, Any]:
    record = {
        key: metadata[key]
        for key in _PUBLIC_METADATA_FIELDS
        if key in metadata
        and (
            metadata[key] is None
            or isinstance(metadata[key], (str, int, float, bool))
        )
    }
    record["webpage_url"] = str(record.get("webpage_url") or submitted_url)
    return record


def _caption_candidate_priority(
    source_rank: int,
    language: str,
    extension: str,
) -> tuple[int, int, int, str]:
    normalized = language.lower()
    priorities = ("zh-hans", "zh-cn", "zh", "en", "ms")
    language_rank = next(
        (
            index
            for index, prefix in enumerate(priorities)
            if normalized.startswith(prefix)
        ),
        len(priorities),
    )
    extension_rank = {"vtt": 0, "json3": 1, "json": 1}.get(extension, 2)
    return source_rank, language_rank, extension_rank, language


def _download_caption_candidate(
    candidate: dict[str, Any],
    proxy: ProxyConfig,
    destination: Path,
    timeout_seconds: int,
) -> None:
    url = str(candidate.get("url") or "")
    if not url:
        raise TikTokMediaDownloadError("平台字幕没有可下载地址。")
    headers = candidate.get("http_headers")
    request_headers = headers if isinstance(headers, dict) else None
    with httpx.Client(
        proxy=proxy.url or None,
        timeout=timeout_seconds,
        follow_redirects=True,
        trust_env=False,
        headers=request_headers,
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        if len(response.content) > 5 * 1024 * 1024:
            raise TikTokMediaDownloadError("平台字幕文件超过大小上限。")
        destination.write_bytes(response.content)


class TikTokCaptionProbe:
    def __init__(
        self,
        *,
        command_runner: CommandRunner = _default_command_runner,
        caption_downloader: Callable[
            [dict[str, Any], ProxyConfig, Path, int], None
        ] = _download_caption_candidate,
    ) -> None:
        self.command_runner = command_runner
        self.caption_downloader = caption_downloader

    def probe(
        self,
        *,
        yt_dlp: str,
        url: str,
        proxy: ProxyConfig,
        raw_dir: Path,
        timeout_seconds: int,
    ) -> CaptionProbeResult | None:
        started = time.perf_counter()
        command = [yt_dlp]
        if proxy.url:
            command.extend(["--proxy", proxy.url])
        command.extend(
            [
                "--no-playlist",
                "--socket-timeout",
                "20",
                "--retries",
                "1",
                "--dump-single-json",
                "--skip-download",
                url,
            ]
        )
        outcome = self.command_runner(command, raw_dir, min(timeout_seconds, 60))
        if outcome.returncode != 0:
            return None
        try:
            metadata = json.loads(outcome.stdout)
        except ValueError:
            return None
        candidates: list[tuple[tuple[int, int, int, str], str, dict[str, Any]]] = []
        for source_rank, key in enumerate(("subtitles", "automatic_captions")):
            caption_map = metadata.get(key)
            if not isinstance(caption_map, dict):
                continue
            for language, entries in caption_map.items():
                if not isinstance(entries, list):
                    continue
                for candidate in entries:
                    if not isinstance(candidate, dict):
                        continue
                    extension = str(candidate.get("ext") or "").lower()
                    if extension not in {"vtt", "json3", "json"}:
                        continue
                    candidates.append(
                        (
                            _caption_candidate_priority(
                                source_rank, str(language), extension
                            ),
                            str(language),
                            candidate,
                        )
                    )
        for _, language, candidate in sorted(candidates, key=lambda item: item[0]):
            extension = str(candidate.get("ext") or "vtt").lower()
            suffix = ".json" if extension in {"json", "json3"} else ".vtt"
            safe_language = re.sub(r"[^A-Za-z0-9_-]", "_", language)[:40] or "unknown"
            path = raw_dir / f"tiktok_caption.{safe_language}{suffix}"
            try:
                self.caption_downloader(
                    candidate,
                    proxy,
                    path,
                    min(timeout_seconds, 30),
                )
                transcript = parse_caption_file(path)
            except (OSError, httpx.HTTPError, TikTokMediaError):
                path.unlink(missing_ok=True)
                continue
            if transcript:
                return CaptionProbeResult(
                    path=path,
                    transcript=transcript,
                    elapsed_seconds=round(time.perf_counter() - started, 3),
                )
            path.unlink(missing_ok=True)
        return None


class TikTokMediaCollector:
    def __init__(
        self,
        *,
        proxy_resolver: ProxyResolver = resolve_tiktok_proxy,
        proxy_probe: ProxyProbe = probe_proxy,
        command_runner: CommandRunner = _default_command_runner,
        yt_dlp_executable: str | None = None,
        ffmpeg_executable: str | None = None,
        ffprobe_executable: str | None = None,
        tikwm_client: TikWMClient | None = None,
        caption_probe: TikTokCaptionProbe | None = None,
        prefer_public_api: bool = True,
        probe_platform_captions: bool = True,
        timeout_seconds: int = 240,
        max_media_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.proxy_resolver = proxy_resolver
        self.proxy_probe = proxy_probe
        self.command_runner = command_runner
        self.yt_dlp_executable = yt_dlp_executable
        self.ffmpeg_executable = ffmpeg_executable
        self.ffprobe_executable = ffprobe_executable
        self.tikwm_client = tikwm_client or TikWMClient()
        self.caption_probe = caption_probe or TikTokCaptionProbe(
            command_runner=command_runner
        )
        self.prefer_public_api = prefer_public_api
        self.probe_platform_captions = probe_platform_captions
        self.timeout_seconds = timeout_seconds
        self.max_media_bytes = max_media_bytes

    def collect(self, url: str, raw_dir: Path) -> TikTokMediaCollection:
        started = time.perf_counter()
        raw_dir = raw_dir.resolve()
        raw_dir.mkdir(parents=True, exist_ok=True)
        proxy = self.proxy_resolver()
        if not proxy:
            raise TikTokProxyUnavailableError(
                "未检测到 TikTok 可用代理，请先在电脑上开启 VPN 后重试。"
            )
        if not self.proxy_probe(proxy):
            raise TikTokProxyUnavailableError(
                "TikTok 代理端口不可用，请确认电脑 VPN 已开启后重试。"
            )

        ffmpeg = _resolve_executable(self.ffmpeg_executable, "ffmpeg")
        ffprobe = _resolve_executable(self.ffprobe_executable, "ffprobe")
        try:
            yt_dlp = _resolve_executable(self.yt_dlp_executable, "yt-dlp")
        except TikTokToolUnavailableError:
            if not self.prefer_public_api:
                raise
            yt_dlp = None
        download_started = time.perf_counter()
        metadata_path = raw_dir / "tiktok_source.info.json"
        media_path = raw_dir / "tiktok_source.mp4"
        api_error: TikTokMediaError | None = None
        metadata: dict[str, Any] | None = None
        caption_result: CaptionProbeResult | None = None
        with ThreadPoolExecutor(max_workers=2) as executor:
            media_future = (
                executor.submit(
                    self.tikwm_client.fetch_and_download,
                    submitted_url=url,
                    proxy=proxy,
                    destination=media_path,
                    timeout_seconds=min(self.timeout_seconds, 60),
                    max_bytes=self.max_media_bytes,
                )
                if self.prefer_public_api
                else None
            )
            caption_future = (
                executor.submit(
                    self.caption_probe.probe,
                    yt_dlp=yt_dlp,
                    url=url,
                    proxy=proxy,
                    raw_dir=raw_dir,
                    timeout_seconds=self.timeout_seconds,
                )
                if self.probe_platform_captions and yt_dlp
                else None
            )
            if media_future:
                try:
                    metadata = media_future.result()
                    metadata_path.write_text(
                        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                except TikTokMediaError as exc:
                    api_error = exc
            if caption_future:
                caption_result = caption_future.result()

        if metadata is None:
            if not yt_dlp:
                raise TikTokMediaDownloadError(
                    "公开媒体接口不可用，且未找到 yt-dlp 备用工具。"
                )
            output_template = raw_dir / "tiktok_source.%(ext)s"
            download_command = [
                yt_dlp,
                "--no-playlist",
                "--no-part",
                "--no-progress",
                "--socket-timeout",
                "20",
                "--retries",
                "2",
                "--fragment-retries",
                "2",
                "--write-info-json",
                "--write-subs",
                "--write-auto-subs",
                "--sub-format",
                "vtt",
                "--sub-langs",
                "all,-live_chat",
                "--format",
                "best[ext=mp4]/best",
                "--ffmpeg-location",
                ffmpeg,
                "--output",
                str(output_template),
                url,
            ]
            if proxy.url:
                download_command[1:1] = ["--proxy", proxy.url]
            outcome = self.command_runner(
                download_command,
                raw_dir,
                self.timeout_seconds,
            )
            if outcome.returncode != 0:
                prefix = (
                    f"公开接口失败（{type(api_error).__name__}），且 "
                    if api_error
                    else ""
                )
                raise TikTokMediaDownloadError(
                    prefix
                    + "TikTok 备用下载失败："
                    + _safe_command_error(outcome, proxy.url)
                )
        download_seconds = time.perf_counter() - download_started

        metadata_paths = sorted(raw_dir.glob("tiktok_source.info.json"))
        media_paths = sorted(
            path
            for path in raw_dir.glob("tiktok_source.*")
            if path.suffix.lower() in _MEDIA_SUFFIXES and path.stat().st_size > 0
        )
        if not metadata_paths or not media_paths:
            raise TikTokMediaDownloadError("下载命令完成，但未得到完整媒体和元数据文件。")
        metadata_path = metadata_paths[0]
        media_path = media_paths[0]
        decoded_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(decoded_metadata, dict):
            metadata_path.unlink(missing_ok=True)
            raise TikTokMediaDownloadError("TikTok 元数据格式无效。")
        metadata = _public_metadata_record(decoded_metadata, url)
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        probe_command = [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(media_path),
        ]
        probe_outcome = self.command_runner(
            probe_command,
            raw_dir,
            min(self.timeout_seconds, 60),
        )
        try:
            probe_payload = json.loads(probe_outcome.stdout)
            decoded_duration = float(probe_payload["format"]["duration"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            decoded_duration = 0.0
        if probe_outcome.returncode != 0 or decoded_duration <= 0:
            raise TikTokMediaDownloadError("下载文件未通过 ffprobe 完整媒体检查。")
        metadata["decoded_media_duration_seconds"] = round(decoded_duration, 3)
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        captions = sorted(
            (
                path
                for path in raw_dir.glob("tiktok_source.*")
                if path.suffix.lower() in _CAPTION_SUFFIXES
                and path.stat().st_size > 0
            ),
            key=_caption_priority,
        )
        caption_path = caption_result.path if caption_result else None
        native_transcript = caption_result.transcript if caption_result else None
        if not native_transcript:
            for candidate_path in captions:
                if candidate_path.name == "tiktok_source.info.json":
                    continue
                parsed = parse_caption_file(candidate_path)
                if parsed:
                    caption_path = candidate_path
                    native_transcript = parsed
                    break
        audio_path: Path | None = None
        audio_seconds = 0.0
        if not native_transcript:
            audio_path = raw_dir / "tiktok_audio_16k.wav"
            audio_command = [
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
            ]
            audio_started = time.perf_counter()
            audio_outcome = self.command_runner(
                audio_command,
                raw_dir,
                min(self.timeout_seconds, 120),
            )
            audio_seconds = time.perf_counter() - audio_started
            if (
                audio_outcome.returncode != 0
                or not audio_path.is_file()
                or audio_path.stat().st_size == 0
            ):
                raise TikTokMediaDownloadError(
                    "TikTok 音轨提取失败："
                    + _safe_command_error(audio_outcome, proxy.url)
                )

        source = self._source_from_metadata(
            submitted_url=url,
            metadata=metadata,
            proxy_source=proxy.source,
            native_transcript=native_transcript,
        )
        artifacts = [
            CollectedArtifact(metadata_path, "public_metadata", "application/json"),
            CollectedArtifact(media_path, "source_media", _artifact_content_type(media_path)),
        ]
        if caption_path:
            artifacts.append(
                CollectedArtifact(
                    caption_path,
                    "platform_caption",
                    _artifact_content_type(caption_path),
                )
            )
        if audio_path:
            artifacts.append(CollectedArtifact(audio_path, "asr_input", "audio/wav"))

        return TikTokMediaCollection(
            source=source,
            metadata_path=metadata_path,
            media_path=media_path,
            audio_path=audio_path,
            caption_path=caption_path,
            native_transcript=native_transcript,
            artifacts=tuple(artifacts),
            timings={
                "download_seconds": round(download_seconds, 3),
                "audio_extract_seconds": round(audio_seconds, 3),
                "total_seconds": round(time.perf_counter() - started, 3),
                "caption_probe_seconds": (
                    caption_result.elapsed_seconds if caption_result else 0.0
                ),
            },
        )

    @staticmethod
    def _source_from_metadata(
        *,
        submitted_url: str,
        metadata: dict[str, Any],
        proxy_source: str,
        native_transcript: dict[str, Any] | None,
    ) -> dict[str, Any]:
        webpage_url = str(metadata.get("webpage_url") or submitted_url)
        uploader = str(metadata.get("uploader") or metadata.get("channel") or "")
        handle = str(metadata.get("uploader_id") or metadata.get("channel_id") or "")
        if handle and not handle.startswith("@"):
            handle = f"@{handle}"
        transcript_source = (
            "platform_caption" if native_transcript else "local_asr_required"
        )
        return {
            "platform": "tiktok",
            "url": webpage_url,
            "submitted_url": submitted_url,
            "video_id": str(metadata.get("id") or "") or None,
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
                "transcript_source": transcript_source,
            },
            "metrics": {
                "views": metadata.get("view_count"),
                "likes": metadata.get("like_count"),
                "comments": metadata.get("comment_count"),
                "favorites": metadata.get("save_count")
                or metadata.get("collect_count")
                or metadata.get("favorite_count"),
                "shares": metadata.get("repost_count") or metadata.get("share_count"),
            },
            "acquisition_mode": "public_media_download",
            "retrieval_status": (
                "completed" if native_transcript else "media_completed"
            ),
            "network": {
                "proxy_required": True,
                "proxy_source": proxy_source,
                "proxy_reachable": True,
            },
            "evidence": [
                {
                    "type": "public_url",
                    "label": "用户提交的 TikTok 链接",
                    "value": submitted_url,
                    "confidence": "submitted",
                },
                {
                    "type": "retrieved_public_metadata",
                    "label": "TikTok 公开元数据",
                    "value": webpage_url,
                    "confidence": "retrieved",
                },
                {
                    "type": "transcript_path",
                    "label": "字幕获取方式",
                    "value": transcript_source,
                    "confidence": (
                        "retrieved" if native_transcript else "pending_runtime_step"
                    ),
                },
            ],
            "missing": [] if native_transcript else ["本地 ASR 转写"],
            "retrieval_provider": metadata.get("_retrieval_provider") or "yt_dlp",
        }
