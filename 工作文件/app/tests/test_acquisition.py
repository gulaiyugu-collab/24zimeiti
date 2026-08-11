from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services import (
    AcquisitionJobManager,
    AcquisitionJobStore,
    InlineAcquisitionDispatcher,
)
from app.services.acquisition import run_acquisition_job
from app.services.asr import TranscriptionResult
from app.services.douyin_media import (
    DouyinMediaCollection,
    DouyinProviderUnavailableError,
    DouyinResolvedSubmission,
)
from app.services.tiktok_media import (
    CollectedArtifact,
    TikTokMediaCollection,
    TikTokProxyUnavailableError,
)


DEMO_URL = "https://www.douyin.com/video/7666774161494183218"
UNKNOWN_URL = "https://www.douyin.com/video/7999999999999999999"
UNKNOWN_TIKTOK_URL = (
    "https://www.tiktok.com/@research/video/7999999999999999999"
)


class FakeTikTokCollector:
    def __init__(self, *, native_transcript=None, failure=None) -> None:
        self.native_transcript = native_transcript
        self.failure = failure

    def collect(self, url: str, raw_dir: Path) -> TikTokMediaCollection:
        if self.failure:
            raise self.failure
        metadata_path = raw_dir / "tiktok_source.info.json"
        media_path = raw_dir / "tiktok_source.mp4"
        audio_path = raw_dir / "tiktok_audio_16k.wav"
        metadata_path.write_text('{"id":"7999999999999999999"}', encoding="utf-8")
        media_path.write_bytes(b"video")
        audio_path.write_bytes(b"wave")
        return TikTokMediaCollection(
            source={
                "platform": "tiktok",
                "url": url,
                "video_id": "7999999999999999999",
                "author": {"name": "测试作者", "handle": "@research"},
                "content": {"title": "测试视频", "duration_seconds": 30},
                "metrics": {"views": 123, "likes": 12, "comments": 3},
                "acquisition_mode": "public_media_download",
                "retrieval_status": "completed",
                "evidence": [],
                "missing": [],
            },
            metadata_path=metadata_path,
            media_path=media_path,
            audio_path=None if self.native_transcript else audio_path,
            caption_path=None,
            native_transcript=self.native_transcript,
            artifacts=(
                CollectedArtifact(
                    metadata_path, "public_metadata", "application/json"
                ),
                CollectedArtifact(media_path, "source_media", "video/mp4"),
                CollectedArtifact(audio_path, "asr_input", "audio/wav"),
            ),
            timings={"download_seconds": 0.2, "total_seconds": 0.3},
        )


class FakeDouyinCollector:
    def __init__(self, *, failure=None) -> None:
        self.failure = failure
        self.urls: list[str] = []

    def collect(self, url: str, raw_dir: Path) -> DouyinMediaCollection:
        self.urls.append(url)
        if self.failure:
            raise self.failure
        metadata_path = raw_dir / "douyin_source.info.json"
        media_path = raw_dir / "douyin_source.mp4"
        audio_path = raw_dir / "douyin_audio_16k.wav"
        metadata_path.write_text('{"id":"7999999999999999999"}', encoding="utf-8")
        media_path.write_bytes(b"video")
        audio_path.write_bytes(b"wave")
        return DouyinMediaCollection(
            source={
                "platform": "douyin",
                "url": url,
                "aweme_id": "7999999999999999999",
                "author": {"name": "测试作者", "handle": "@research"},
                "content": {"title": "测试抖音视频", "duration_seconds": 30},
                "metrics": {"views": 123, "likes": 12, "comments": 3},
                "acquisition_mode": "public_media_download",
                "retrieval_status": "media_completed",
                "evidence": [],
                "missing": ["本地 ASR 转写"],
            },
            metadata_path=metadata_path,
            media_path=media_path,
            audio_path=audio_path,
            caption_path=None,
            native_transcript=None,
            artifacts=(
                CollectedArtifact(metadata_path, "public_metadata", "application/json"),
                CollectedArtifact(media_path, "source_media", "video/mp4"),
                CollectedArtifact(audio_path, "asr_input", "audio/wav"),
            ),
            timings={"download_seconds": 0.2, "total_seconds": 0.3},
        )


class FakeASRRouter:
    async def transcribe_path(self, mode, media_path, content_type, language):
        return TranscriptionResult(
            transcript="自动转写得到的测试字幕。",
            provider="local",
            model="mock-local",
            language="zh",
            segments=[
                {
                    "start": 0.0,
                    "end": 2.5,
                    "text": "自动转写得到的测试字幕。",
                }
            ],
            segments_status="provided",
            confidence=None,
            confidence_status="not_provided",
            provider_metadata={"device": "cpu"},
        )


class AcquisitionJobApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def _manager(
        self,
        root: Path,
        *,
        douyin_collector: FakeDouyinCollector | None = None,
        douyin_url_resolver=None,
    ) -> AcquisitionJobManager:
        store = AcquisitionJobStore(root)
        worker = None
        if douyin_collector is not None:
            worker = lambda worker_store, job_id: run_acquisition_job(
                worker_store,
                job_id,
                douyin_media_collector=douyin_collector,
                asr_router=FakeASRRouter(),
            )
        return AcquisitionJobManager(
            store=store,
            dispatcher=InlineAcquisitionDispatcher(store, worker=worker),
            douyin_url_resolver=douyin_url_resolver,
        )

    def test_registered_fixture_job_returns_compact_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = self._manager(Path(temporary))
            with patch.object(main_module, "acquisition_jobs", manager):
                submitted = self.client.post(
                    "/api/acquisition/jobs",
                    json={"url": DEMO_URL},
                )

                self.assertEqual(202, submitted.status_code)
                status = submitted.json()
                self.assertEqual("completed", status["status"])
                self.assertFalse(status["cache_hit"])
                self.assertNotIn("source", status)

                manifest = self.client.get(status["manifest_url"])
                self.assertEqual(200, manifest.status_code)
                payload = manifest.json()
                self.assertTrue(payload["analysis_ready"])
                self.assertEqual("registered_fixture", payload["acquisition_mode"])
                self.assertEqual(1, payload["evidence_summary"]["item_count"])
                self.assertFalse(payload["context_policy"]["raw_artifacts_included"])
                self.assertNotIn("public_comment_summary", payload)

    def test_completed_job_is_reused_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = self._manager(Path(temporary))
            with patch.object(main_module, "acquisition_jobs", manager):
                first = self.client.post(
                    "/api/acquisition/jobs",
                    json={"url": DEMO_URL},
                ).json()
                second = self.client.post(
                    "/api/acquisition/jobs",
                    json={"url": DEMO_URL},
                ).json()

                self.assertEqual(first["job_id"], second["job_id"])
                self.assertTrue(second["cache_hit"])

    def test_force_refresh_creates_a_new_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = self._manager(Path(temporary))
            with patch.object(main_module, "acquisition_jobs", manager):
                first = self.client.post(
                    "/api/acquisition/jobs",
                    json={"url": DEMO_URL},
                ).json()
                refreshed = self.client.post(
                    "/api/acquisition/jobs",
                    json={"url": DEMO_URL, "force_refresh": True},
                ).json()

                self.assertNotEqual(first["job_id"], refreshed["job_id"])
                self.assertFalse(refreshed["cache_hit"])

    def test_same_douyin_item_different_share_links_reuses_stable_cache(self) -> None:
        def resolver(raw_url: str) -> DouyinResolvedSubmission:
            return DouyinResolvedSubmission(
                "https://www.douyin.com/video/7999999999999999999",
                "7999999999999999999",
                True,
            )

        with tempfile.TemporaryDirectory() as temporary:
            manager = self._manager(
                Path(temporary),
                douyin_collector=FakeDouyinCollector(),
                douyin_url_resolver=resolver,
            )
            with patch.object(main_module, "acquisition_jobs", manager):
                first = self.client.post(
                    "/api/acquisition/jobs", json={"url": "https://v.douyin.com/a/"}
                ).json()
                second = self.client.post(
                    "/api/acquisition/jobs", json={"url": "https://v.douyin.com/b/"}
                ).json()

        self.assertEqual(first["job_id"], second["job_id"])
        self.assertTrue(second["cache_hit"])

    def test_expired_share_link_recovers_id_from_prior_failed_metadata(self) -> None:
        short_url = "https://v.douyin.com/expired-example/"

        def unresolved(raw_url: str) -> DouyinResolvedSubmission:
            return DouyinResolvedSubmission(raw_url, None, False)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = AcquisitionJobStore(root)
            prior = store.create_job(
                {
                    "url": short_url,
                    "platform": "douyin",
                    "item_limit": 1,
                    "cache_key": "prior-failed-job",
                }
            )
            prior_raw = store.job_dir(str(prior["job_id"])) / "raw"
            (prior_raw / "douyin_source.info.json").write_text(
                '{"id":"7999999999999999999"}', encoding="utf-8"
            )
            store.patch_status(str(prior["job_id"]), status="failed")
            store.create_job(
                {
                    "url": short_url,
                    "platform": "douyin",
                    "item_limit": 1,
                    "cache_key": "newer-job-without-metadata",
                }
            )

            collector = FakeDouyinCollector()
            manager = self._manager(
                root,
                douyin_collector=collector,
                douyin_url_resolver=unresolved,
            )
            with patch.object(main_module, "acquisition_jobs", manager):
                recovered = self.client.post(
                    "/api/acquisition/jobs",
                    json={"url": short_url, "force_refresh": True},
                ).json()

            request = manager.store.request(str(recovered["job_id"]))

        self.assertEqual("completed", recovered["status"])
        self.assertEqual(
            "https://www.douyin.com/video/7999999999999999999",
            collector.urls[-1],
        )
        self.assertEqual("7999999999999999999", request["stable_id"])
        self.assertEqual(short_url, request["submitted_url"])
        self.assertTrue(request["link_verified"])

    def test_needs_input_job_is_not_cached(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collector = FakeDouyinCollector(
                failure=DouyinProviderUnavailableError("provider unavailable")
            )
            manager = self._manager(Path(temporary), douyin_collector=collector)
            with patch.object(main_module, "acquisition_jobs", manager):
                first = self.client.post(
                    "/api/acquisition/jobs",
                    json={"url": UNKNOWN_URL},
                ).json()
                second = self.client.post(
                    "/api/acquisition/jobs",
                    json={"url": UNKNOWN_URL},
                ).json()

                self.assertEqual("needs_input", first["status"])
                self.assertEqual("needs_input", second["status"])
                self.assertNotEqual(first["job_id"], second["job_id"])
                self.assertFalse(first["cache_hit"])
                self.assertFalse(second["cache_hit"])

    def test_unresolved_douyin_job_stops_without_fabrication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            collector = FakeDouyinCollector(
                failure=DouyinProviderUnavailableError("provider unavailable")
            )
            manager = self._manager(Path(temporary), douyin_collector=collector)
            with patch.object(main_module, "acquisition_jobs", manager):
                submitted = self.client.post(
                    "/api/acquisition/jobs",
                    json={"url": UNKNOWN_URL, "item_limit": 10},
                ).json()

                self.assertEqual("needs_input", submitted["status"])
                self.assertIsNone(submitted["manifest_url"])
                self.assertEqual([], submitted["artifacts"])
                self.assertIn("公开的单条作品链接", submitted["message"])
                self.assertIn(
                    "可解析的公开抖音单条作品链接或可用公共 Provider",
                    submitted["missing"],
                )

    def test_raw_artifact_requires_allowlisted_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = self._manager(Path(temporary))
            with patch.object(main_module, "acquisition_jobs", manager):
                submitted = self.client.post(
                    "/api/acquisition/jobs",
                    json={"url": DEMO_URL},
                ).json()
                job_id = submitted["job_id"]

                source = self.client.get(
                    f"/api/acquisition/jobs/{job_id}/artifacts/source.json"
                )
                missing = self.client.get(
                    f"/api/acquisition/jobs/{job_id}/artifacts/not-listed.json"
                )

                self.assertEqual(200, source.status_code)
                self.assertEqual("fixture", source.json()["acquisition_mode"])
                self.assertEqual(404, missing.status_code)

    def test_unsupported_platform_is_rejected_before_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = self._manager(Path(temporary))
            with patch.object(main_module, "acquisition_jobs", manager):
                response = self.client.post(
                    "/api/acquisition/jobs",
                    json={"url": "https://www.youtube.com/watch?v=example"},
                )

                self.assertEqual(400, response.status_code)
                self.assertEqual([], list((Path(temporary) / "jobs").iterdir()))

    def test_live_tiktok_media_is_transcribed_and_written_to_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = AcquisitionJobStore(Path(temporary))
            queued = store.create_job(
                {
                    "url": UNKNOWN_TIKTOK_URL,
                    "platform": "tiktok",
                    "item_limit": 1,
                    "cache_key": "test-live-tiktok",
                }
            )
            run_acquisition_job(
                store,
                queued["job_id"],
                tiktok_media_collector=FakeTikTokCollector(),
                asr_router=FakeASRRouter(),
            )

            status = store.status(queued["job_id"])
            manifest = store.manifest(queued["job_id"])

        self.assertEqual("completed", status["status"])
        self.assertTrue(manifest["analysis_ready"])
        self.assertEqual("local_asr", manifest["acquisition_mode"])
        transcript = manifest["items"][0]["content"]["transcript"]
        self.assertEqual("自动转写得到的测试字幕。", transcript["text"])
        self.assertEqual(1, transcript["segment_count"])
        self.assertEqual(
            {"tiktok_source.info.json", "tiktok_source.mp4", "tiktok_audio_16k.wav", "transcript.json", "source.json"},
            {item["name"] for item in status["artifacts"]},
        )

    def test_live_douyin_media_is_transcribed_and_written_to_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = AcquisitionJobStore(Path(temporary))
            queued = store.create_job(
                {
                    "url": UNKNOWN_URL,
                    "platform": "douyin",
                    "item_limit": 1,
                    "cache_key": "test-live-douyin",
                }
            )
            run_acquisition_job(
                store,
                queued["job_id"],
                douyin_media_collector=FakeDouyinCollector(),
                asr_router=FakeASRRouter(),
            )

            status = store.status(queued["job_id"])
            manifest = store.manifest(queued["job_id"])

        self.assertEqual("completed", status["status"])
        self.assertEqual("douyin", manifest["platform"])
        self.assertEqual("7999999999999999999", manifest["stable_id"])
        self.assertTrue(manifest["analysis_ready"])
        self.assertEqual("local_asr", manifest["acquisition_mode"])
        transcript = manifest["items"][0]["content"]["transcript"]
        self.assertEqual("自动转写得到的测试字幕。", transcript["text"])
        self.assertEqual(
            {"douyin_source.info.json", "douyin_source.mp4", "douyin_audio_16k.wav", "transcript.json", "source.json"},
            {item["name"] for item in status["artifacts"]},
        )

    def test_live_tiktok_proxy_failure_requires_vpn_not_manual_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = AcquisitionJobStore(Path(temporary))
            queued = store.create_job(
                {
                    "url": UNKNOWN_TIKTOK_URL,
                    "platform": "tiktok",
                    "item_limit": 1,
                    "cache_key": "test-proxy-failure",
                }
            )
            run_acquisition_job(
                store,
                queued["job_id"],
                tiktok_media_collector=FakeTikTokCollector(
                    failure=TikTokProxyUnavailableError("请先开启电脑 VPN。")
                ),
                asr_router=FakeASRRouter(),
            )
            status = store.status(queued["job_id"])

        self.assertEqual("needs_input", status["status"])
        self.assertIn("VPN", status["message"])
        self.assertNotIn("字幕", " ".join(status["missing"]))

    def test_completed_acquisition_enters_analysis_without_client_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = AcquisitionJobStore(Path(temporary))
            queued = store.create_job(
                {
                    "url": UNKNOWN_TIKTOK_URL,
                    "platform": "tiktok",
                    "item_limit": 1,
                    "cache_key": "test-analysis-ready",
                }
            )
            run_acquisition_job(
                store,
                queued["job_id"],
                tiktok_media_collector=FakeTikTokCollector(),
                asr_router=FakeASRRouter(),
            )
            manager = AcquisitionJobManager(store=store)
            with (
                patch.object(main_module, "acquisition_jobs", manager),
                patch.dict(
                    os.environ,
                    {"DEEPSEEK_API_KEY": "", "PROJECT024_CONTENT_API_KEY": ""},
                ),
            ):
                response = self.client.post(
                    f"/api/acquisition/jobs/{queued['job_id']}/analyze",
                    json={"analysis_mode": "quick"},
                )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        acquisition = payload["source"]["acquisition"]
        self.assertEqual("partial", payload["status"])
        self.assertEqual(queued["job_id"], acquisition["job_id"])
        self.assertEqual("local_asr", acquisition["transcript"]["source"])
        self.assertEqual(64, len(acquisition["transcript"]["sha256"]))
        self.assertEqual("runtime_public_snapshot", acquisition["evidence_strength"])
        self.assertTrue(acquisition["completed_at"].endswith("Z"))
        self.assertEqual(64, len(acquisition["source_artifact"]["sha256"]))
        self.assertEqual(123, payload["source"]["metrics"]["views"])
        self.assertEqual("local_asr", payload["report"]["evidence_and_risk"]["transcript_status"])
        self.assertEqual("completed", payload["report"]["asr"]["status"])
        self.assertNotIn("实时公开指标", payload["missing"])

    def test_registered_fixture_uses_reviewed_analysis_without_runtime_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = self._manager(Path(temporary))
            with (
                patch.object(main_module, "acquisition_jobs", manager),
                patch.dict(
                    os.environ,
                    {"DEEPSEEK_API_KEY": "", "PROJECT024_CONTENT_API_KEY": ""},
                ),
            ):
                submitted = self.client.post(
                    "/api/acquisition/jobs",
                    json={"url": DEMO_URL},
                ).json()
                response = self.client.post(
                    f"/api/acquisition/jobs/{submitted['job_id']}/analyze",
                    json={"analysis_mode": "quick"},
                )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        acquisition = payload["source"]["acquisition"]
        self.assertEqual("completed", payload["status"])
        self.assertEqual("registered_fixture", acquisition["acquisition_mode"])
        self.assertEqual("reviewed_fixture", acquisition["evidence_strength"])
        self.assertEqual(64, len(acquisition["source_artifact"]["sha256"]))
        self.assertIsNone(acquisition["transcript"])

    def test_non_completed_acquisition_cannot_enter_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = AcquisitionJobStore(Path(temporary))
            manager = AcquisitionJobManager(store=store)
            with patch.object(main_module, "acquisition_jobs", manager):
                for lifecycle_status in ("queued", "processing", "needs_input", "failed"):
                    queued = store.create_job(
                        {
                            "url": UNKNOWN_TIKTOK_URL,
                            "platform": "tiktok",
                            "item_limit": 1,
                            "cache_key": f"test-{lifecycle_status}",
                        }
                    )
                    if lifecycle_status != "queued":
                        store.patch_status(
                            queued["job_id"],
                            status=lifecycle_status,
                            message="测试终态",
                        )
                    with self.subTest(status=lifecycle_status):
                        response = self.client.post(
                            f"/api/acquisition/jobs/{queued['job_id']}/analyze",
                            json={},
                        )
                        self.assertEqual(409, response.status_code)
                        self.assertEqual(
                            lifecycle_status,
                            response.json()["detail"]["status"],
                        )

    def test_empty_acquisition_transcript_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = AcquisitionJobStore(Path(temporary))
            queued = store.create_job(
                {
                    "url": UNKNOWN_TIKTOK_URL,
                    "platform": "tiktok",
                    "item_limit": 1,
                    "cache_key": "test-empty-transcript",
                }
            )
            run_acquisition_job(
                store,
                queued["job_id"],
                tiktok_media_collector=FakeTikTokCollector(),
                asr_router=FakeASRRouter(),
            )
            manifest = store.manifest(queued["job_id"])
            manifest["items"][0]["content"]["transcript"]["text"] = "   "
            store.write_manifest(queued["job_id"], manifest)
            manager = AcquisitionJobManager(store=store)
            with patch.object(main_module, "acquisition_jobs", manager):
                response = self.client.post(
                    f"/api/acquisition/jobs/{queued['job_id']}/analyze",
                    json={},
                )

        self.assertEqual(422, response.status_code)
        self.assertIn("非空字幕", response.json()["detail"])

    def test_client_cannot_override_acquisition_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = AcquisitionJobStore(Path(temporary))
            queued = store.create_job(
                {
                    "url": UNKNOWN_TIKTOK_URL,
                    "platform": "tiktok",
                    "item_limit": 1,
                    "cache_key": "test-client-transcript",
                }
            )
            manager = AcquisitionJobManager(store=store)
            with patch.object(main_module, "acquisition_jobs", manager):
                response = self.client.post(
                    f"/api/acquisition/jobs/{queued['job_id']}/analyze",
                    json={"transcript": "客户端伪造字幕"},
                )

        self.assertEqual(422, response.status_code)


if __name__ == "__main__":
    unittest.main()
