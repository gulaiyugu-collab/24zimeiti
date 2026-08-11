from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.tiktok_media import (
    CommandOutcome,
    ProxyConfig,
    TikTokMediaCollector,
    TikTokProxyUnavailableError,
    parse_caption_file,
    resolve_tiktok_proxy,
)


TIKTOK_URL = "https://www.tiktok.com/@example/video/7123456789012345678"


def write_metadata(path: Path, duration: float = 30) -> None:
    path.write_text(
        json.dumps(
            {
                "id": "7123456789012345678",
                "title": "测试视频",
                "description": "用于采集单元测试",
                "duration": duration,
                "uploader": "Example Creator",
                "uploader_id": "example",
                "webpage_url": TIKTOK_URL,
                "view_count": 1234,
                "like_count": 87,
                "comment_count": 9,
                "repost_count": 4,
                "cookies": "temporary-session-value",
                "http_headers": {"Cookie": "temporary-session-value"},
                "formats": [{"url": "https://signed.example/media"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class TikTokMediaTests(unittest.TestCase):
    def test_resolve_proxy_prefers_project_environment(self) -> None:
        proxy = resolve_tiktok_proxy(
            {"PROJECT024_TIKTOK_PROXY": "127.0.0.1:7890"},
            windows_proxy_reader=lambda: "127.0.0.1:9999",
        )
        self.assertIsNotNone(proxy)
        assert proxy is not None
        self.assertEqual("http://127.0.0.1:7890", proxy.url)
        self.assertEqual("project_environment", proxy.source)

    def test_parse_caption_returns_deduplicated_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "tiktok_source.zh-Hans.vtt"
            path.write_text(
                """WEBVTT

00:00:00.000 --> 00:00:01.500
第一句话

00:00:01.500 --> 00:00:02.000
第一句话

00:00:02.000 --> 00:00:04.000
<c>第二句话</c>
""",
                encoding="utf-8",
            )
            transcript = parse_caption_file(path)

        self.assertIsNotNone(transcript)
        assert transcript is not None
        self.assertEqual("zh-Hans", transcript["language"])
        self.assertEqual("第一句话 第一句话 第二句话", transcript["text"])
        self.assertEqual(3, len(transcript["segments"]))
        self.assertEqual(2.0, transcript["segments"][2]["start"])

    def test_collector_prefers_platform_caption_without_audio_extract(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], cwd: Path, timeout: int) -> CommandOutcome:
            calls.append(command)
            if "-show_entries" in command:
                return CommandOutcome(0, stdout='{"format":{"duration":"30.0"}}')
            write_metadata(cwd / "tiktok_source.info.json")
            (cwd / "tiktok_source.mp4").write_bytes(b"video")
            (cwd / "tiktok_source.en.vtt").write_text(
                "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello world\n",
                encoding="utf-8",
            )
            return CommandOutcome(0)

        with tempfile.TemporaryDirectory() as temporary:
            collector = TikTokMediaCollector(
                proxy_resolver=lambda: ProxyConfig(
                    "http://127.0.0.1:7890", "test"
                ),
                proxy_probe=lambda _: True,
                command_runner=runner,
                yt_dlp_executable=__file__,
                ffmpeg_executable=__file__,
                ffprobe_executable=__file__,
                prefer_public_api=False,
                probe_platform_captions=False,
            )
            result = collector.collect(TIKTOK_URL, Path(temporary))

        self.assertEqual(2, len(calls))
        self.assertIsNone(result.audio_path)
        self.assertEqual("Hello world", result.native_transcript["text"])
        self.assertEqual(
            "platform_caption", result.source["content"]["transcript_source"]
        )

    def test_collector_extracts_audio_when_caption_is_missing(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str], cwd: Path, timeout: int) -> CommandOutcome:
            calls.append(command)
            if len(calls) == 1:
                write_metadata(cwd / "tiktok_source.info.json")
                (cwd / "tiktok_source.mp4").write_bytes(b"video")
            elif "-show_entries" in command:
                return CommandOutcome(0, stdout='{"format":{"duration":"30.0"}}')
            else:
                Path(command[-1]).write_bytes(b"wave")
            return CommandOutcome(0)

        with tempfile.TemporaryDirectory() as temporary:
            collector = TikTokMediaCollector(
                proxy_resolver=lambda: ProxyConfig(
                    "http://127.0.0.1:7890", "test"
                ),
                proxy_probe=lambda _: True,
                command_runner=runner,
                yt_dlp_executable=__file__,
                ffmpeg_executable=__file__,
                ffprobe_executable=__file__,
                prefer_public_api=False,
                probe_platform_captions=False,
            )
            result = collector.collect(TIKTOK_URL, Path(temporary))

        self.assertEqual(3, len(calls))
        self.assertIsNone(result.native_transcript)
        self.assertIsNotNone(result.audio_path)
        self.assertEqual(
            "local_asr_required", result.source["content"]["transcript_source"]
        )

    def test_collector_removes_transient_fields_from_registered_metadata(self) -> None:
        def runner(command: list[str], cwd: Path, timeout: int) -> CommandOutcome:
            if "-show_entries" in command:
                return CommandOutcome(0, stdout='{"format":{"duration":"30.0"}}')
            if command[-1].endswith(".wav"):
                Path(command[-1]).write_bytes(b"wave")
                return CommandOutcome(0)
            write_metadata(cwd / "tiktok_source.info.json")
            (cwd / "tiktok_source.mp4").write_bytes(b"video")
            return CommandOutcome(0)

        with tempfile.TemporaryDirectory() as temporary:
            collector = TikTokMediaCollector(
                proxy_resolver=lambda: ProxyConfig(
                    "http://127.0.0.1:7890", "test"
                ),
                proxy_probe=lambda _: True,
                command_runner=runner,
                yt_dlp_executable=__file__,
                ffmpeg_executable=__file__,
                ffprobe_executable=__file__,
                prefer_public_api=False,
                probe_platform_captions=False,
            )
            result = collector.collect(TIKTOK_URL, Path(temporary))
            metadata = json.loads(result.metadata_path.read_text(encoding="utf-8"))

        self.assertEqual("7123456789012345678", metadata["id"])
        self.assertEqual(TIKTOK_URL, metadata["webpage_url"])
        self.assertNotIn("cookies", metadata)
        self.assertNotIn("http_headers", metadata)
        self.assertNotIn("formats", metadata)

    def test_collector_stops_when_proxy_is_unavailable(self) -> None:
        collector = TikTokMediaCollector(
            proxy_resolver=lambda: ProxyConfig(
                "http://127.0.0.1:7890", "test"
            ),
            proxy_probe=lambda _: False,
            yt_dlp_executable=__file__,
            ffmpeg_executable=__file__,
            ffprobe_executable=__file__,
            prefer_public_api=False,
            probe_platform_captions=False,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(TikTokProxyUnavailableError):
                collector.collect(TIKTOK_URL, Path(temporary))

    def test_collector_accepts_long_media_without_duration_limit(self) -> None:
        def runner(command: list[str], cwd: Path, timeout: int) -> CommandOutcome:
            if "-show_entries" in command:
                return CommandOutcome(0, stdout='{"format":{"duration":"1621.9"}}')
            if command[-1].endswith(".wav"):
                Path(command[-1]).write_bytes(b"wave")
                return CommandOutcome(0)
            write_metadata(cwd / "tiktok_source.info.json", duration=1621.9)
            (cwd / "tiktok_source.mp4").write_bytes(b"video")
            return CommandOutcome(0)

        with tempfile.TemporaryDirectory() as temporary:
            collector = TikTokMediaCollector(
                proxy_resolver=lambda: ProxyConfig(
                    "http://127.0.0.1:7890", "test"
                ),
                proxy_probe=lambda _: True,
                command_runner=runner,
                yt_dlp_executable=__file__,
                ffmpeg_executable=__file__,
                ffprobe_executable=__file__,
                prefer_public_api=False,
                probe_platform_captions=False,
            )
            result = collector.collect(TIKTOK_URL, Path(temporary))

        self.assertEqual(1621.9, result.source["content"]["duration_seconds"])
        self.assertEqual(
            1621.9,
            result.source["content"]["decoded_media_duration_seconds"],
        )


if __name__ == "__main__":
    unittest.main()
