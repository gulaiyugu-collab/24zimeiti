from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from app.services.douyin_media import (
    DouyinCommunityClient,
    DouyinMediaCollector,
    DouyinProviderUnavailableError,
    _is_public_http_url,
    resolve_douyin_submission,
)
from app.services.tiktok_media import CommandOutcome


DOUYIN_URL = "https://v.douyin.com/example/"


def media_probe_payload(duration: float) -> str:
    return json.dumps(
        {
            "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
            "format": {"duration": str(duration)},
        }
    )


class FakeCommunityClient:
    def __init__(self, duration: float = 35.016) -> None:
        self.duration = duration

    def fetch_and_download(self, *, destination: Path, **kwargs):
        destination.write_bytes(b"video")
        return {
            "id": "7670900617237286186",
            "title": "测试抖音视频",
            "description": "测试抖音视频",
            "duration": self.duration,
            "uploader": "测试作者",
            "uploader_id": "test_author",
            "webpage_url": DOUYIN_URL,
            "language": "zh",
            "view_count": 100,
            "like_count": 20,
            "comment_count": 3,
            "share_count": 2,
            "save_count": 10,
            "_retrieval_provider": "test_community_provider",
        }


class DouyinMediaTests(unittest.TestCase):
    def test_share_redirect_resolves_to_stable_aweme_url(self) -> None:
        class Response:
            is_redirect = True
            headers = {
                "location": "https://www.iesdouyin.com/share/video/7999999999999999999/"
            }

        class Client:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def get(self, url):
                self.requested_url = url
                return Response()

        result = resolve_douyin_submission(
            "https://v.douyin.com/share-example/",
            client_factory=lambda **kwargs: Client(),
            url_validator=lambda _: True,
        )
        self.assertEqual("7999999999999999999", result.aweme_id)
        self.assertTrue(result.link_verified)
        self.assertEqual(
            "https://www.douyin.com/video/7999999999999999999",
            result.canonical_url,
        )

    def test_community_client_downloads_media_and_whitelists_metadata(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/hybrid/video_data":
                return httpx.Response(
                    200,
                    json={
                        "code": 200,
                        "data": {
                            "aweme_id": "7670900617237286186",
                            "desc": "测试抖音视频",
                            "duration": 35016,
                            "share_url": (
                                "https://www.iesdouyin.com/share/video/"
                                "7670900617237286186/?did=tracking&share_sign=signed"
                            ),
                            "author": {
                                "nickname": "测试作者",
                                "unique_id": "test_author",
                            },
                            "statistics": {
                                "play_count": 100,
                                "digg_count": 20,
                                "comment_count": 3,
                                "share_count": 2,
                                "collect_count": 10,
                            },
                            "video": {
                                "play_addr": {
                                    "url_list": ["https://media.example/video.mp4"]
                                }
                            },
                            "cookies": "must-not-be-retained",
                            "http_headers": {"authorization": "must-not-be-retained"},
                        },
                    },
                )
            if request.url.host == "media.example":
                return httpx.Response(
                    200,
                    content=b"video-bytes",
                    headers={"content-type": "video/mp4", "content-length": "11"},
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)

        def client_factory(**kwargs):
            return httpx.Client(transport=transport, **kwargs)

        client = DouyinCommunityClient(
            base_url="https://api.example",
            client_factory=client_factory,
            sleep=lambda _: None,
            url_validator=lambda _: True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "douyin_source.mp4"
            metadata = client.fetch_and_download(
                submitted_url=DOUYIN_URL,
                destination=destination,
                timeout_seconds=30,
                max_bytes=1024,
            )
            media = destination.read_bytes()

        self.assertEqual(b"video-bytes", media)
        self.assertEqual("7670900617237286186", metadata["id"])
        self.assertEqual(35.016, metadata["duration"])
        self.assertEqual(10, metadata["save_count"])
        self.assertEqual(
            "https://www.douyin.com/video/7670900617237286186",
            metadata["webpage_url"],
        )
        serialized = json.dumps(metadata)
        self.assertNotIn("media.example", serialized)
        self.assertNotIn("cookies", serialized)
        self.assertNotIn("http_headers", serialized)
        self.assertNotIn("share_sign", serialized)
        self.assertNotIn("tracking", serialized)

    def test_collector_extracts_audio_and_builds_douyin_source(self) -> None:
        def runner(command: list[str], cwd: Path, timeout: int) -> CommandOutcome:
            if "-show_entries" in command:
                return CommandOutcome(0, stdout=media_probe_payload(35.016))
            Path(command[-1]).write_bytes(b"wave")
            return CommandOutcome(0)

        collector = DouyinMediaCollector(
            command_runner=runner,
            ffmpeg_executable=__file__,
            ffprobe_executable=__file__,
            public_client=FakeCommunityClient(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            collection = collector.collect(DOUYIN_URL, Path(temporary))
            metadata = json.loads(collection.metadata_path.read_text(encoding="utf-8"))

        self.assertEqual("douyin", collection.source["platform"])
        self.assertEqual("7670900617237286186", collection.source["aweme_id"])
        self.assertEqual("local_asr_required", collection.source["content"]["transcript_source"])
        self.assertEqual(35.016, metadata["decoded_media_duration_seconds"])
        self.assertEqual(
            {"douyin_source.info.json", "douyin_source.mp4", "douyin_audio_16k.wav"},
            {artifact.path.name for artifact in collection.artifacts},
        )

    def test_private_media_urls_are_rejected(self) -> None:
        self.assertFalse(_is_public_http_url("http://127.0.0.1/video.mp4"))
        self.assertFalse(_is_public_http_url("http://localhost/video.mp4"))

    def test_collector_accepts_long_media_without_duration_limit(self) -> None:
        def runner(command: list[str], cwd: Path, timeout: int) -> CommandOutcome:
            if "-show_entries" in command:
                return CommandOutcome(0, stdout=media_probe_payload(1621.9))
            Path(command[-1]).write_bytes(b"wave")
            return CommandOutcome(0)

        collector = DouyinMediaCollector(
            command_runner=runner,
            ffmpeg_executable=__file__,
            ffprobe_executable=__file__,
            public_client=FakeCommunityClient(duration=1621.9),
        )
        with tempfile.TemporaryDirectory() as temporary:
            collection = collector.collect(DOUYIN_URL, Path(temporary))

        self.assertEqual(1621.9, collection.source["content"]["duration_seconds"])
        self.assertEqual(
            1621.9,
            collection.source["content"]["decoded_media_duration_seconds"],
        )

    def test_collector_falls_back_to_isolated_browser_without_retaining_media_url(self) -> None:
        class FailingPublicClient:
            def fetch_and_download(self, **kwargs):
                raise DouyinProviderUnavailableError("community provider unavailable")

            def download_public_media(self, *, destination, **kwargs):
                self.media_url = kwargs.get("media_url")
                destination.write_bytes(b"browser-video")

        class FakeBrowserClient:
            def inspect(self, submitted_url, *, timeout_seconds):
                return {
                    "status": "ok",
                    "aweme_id": "7999999999999999999",
                    "title": "浏览器回退视频",
                    "description": "浏览器回退视频",
                    "duration": 35.0,
                    "webpage_url": "https://www.douyin.com/video/7999999999999999999",
                    "media_url": "https://media.example/signed-video.mp4?token=ephemeral",
                    "language": "zh",
                    "uploader": None,
                }

        def runner(command: list[str], cwd: Path, timeout: int) -> CommandOutcome:
            if "-show_entries" in command:
                return CommandOutcome(0, stdout=media_probe_payload(35.0))
            Path(command[-1]).write_bytes(b"wave")
            return CommandOutcome(0)

        public_client = FailingPublicClient()
        collector = DouyinMediaCollector(
            command_runner=runner,
            ffmpeg_executable=__file__,
            ffprobe_executable=__file__,
            public_client=public_client,
            browser_client=FakeBrowserClient(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            collection = collector.collect(DOUYIN_URL, Path(temporary))
            metadata = json.loads(collection.metadata_path.read_text(encoding="utf-8"))
            media_bytes = collection.media_path.read_bytes()

        self.assertEqual(b"browser-video", media_bytes)
        self.assertEqual(
            "douyin_public_ephemeral_browser",
            metadata["_retrieval_provider"],
        )
        self.assertNotIn("media_url", metadata)
        self.assertEqual(
            "public_browser_media_download",
            collection.source["acquisition_mode"],
        )

    def test_collector_merges_separate_browser_audio_without_retaining_urls(self) -> None:
        class FailingPublicClient:
            def __init__(self) -> None:
                self.downloaded_urls: list[str] = []

            def fetch_and_download(self, **kwargs):
                raise DouyinProviderUnavailableError("community provider unavailable")

            def download_public_media(self, *, media_url, destination, **kwargs):
                self.downloaded_urls.append(media_url)
                payload = b"separate-audio" if destination.suffix == ".m4a" else b"video-only"
                destination.write_bytes(payload)

        class FakeBrowserClient:
            def inspect(self, submitted_url, *, timeout_seconds):
                return {
                    "status": "ok",
                    "aweme_id": "7999999999999999999",
                    "title": "分离音视频测试",
                    "description": "分离音视频测试",
                    "duration": 255.5,
                    "webpage_url": "https://www.douyin.com/video/7999999999999999999",
                    "media_url": "https://media.example/video-only.mp4?token=ephemeral",
                    "audio_url": "https://media.example/audio-only.mp4?token=ephemeral",
                    "language": "zh",
                    "uploader": None,
                }

        commands: list[list[str]] = []

        def runner(command: list[str], cwd: Path, timeout: int) -> CommandOutcome:
            commands.append(command)
            if "-show_entries" in command:
                return CommandOutcome(0, stdout=media_probe_payload(255.5))
            output = Path(command[-1])
            output.write_bytes(b"muxed-media" if output.suffix == ".mp4" else b"wave")
            return CommandOutcome(0)

        public_client = FailingPublicClient()
        collector = DouyinMediaCollector(
            command_runner=runner,
            ffmpeg_executable=__file__,
            ffprobe_executable=__file__,
            public_client=public_client,
            browser_client=FakeBrowserClient(),
        )
        with tempfile.TemporaryDirectory() as temporary:
            raw_dir = Path(temporary)
            collection = collector.collect(DOUYIN_URL, raw_dir)
            metadata = json.loads(collection.metadata_path.read_text(encoding="utf-8"))
            media_bytes = collection.media_path.read_bytes()
            separate_audio_exists = (raw_dir / "douyin_separate_audio.m4a").exists()

        self.assertEqual(b"muxed-media", media_bytes)
        self.assertFalse(separate_audio_exists)
        self.assertEqual(
            "separate_browser_audio_merged",
            metadata["_media_assembly"],
        )
        self.assertNotIn("media_url", json.dumps(metadata))
        self.assertNotIn("audio_url", json.dumps(metadata))
        self.assertEqual(2, len(public_client.downloaded_urls))
        self.assertTrue(any("1:a:0" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
