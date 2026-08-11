from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services import (
    ASRRouter,
    ContentGenerationRouter,
    DeepSeekContentProvider,
)
from app.services.asr import ExternalAPIProvider
from app.services.asr import (
    ASRProviderError,
    ProviderAvailability,
    TranscriptionResult,
)


TIKTOK_VIDEO_ID = "7648937896535264533"
TIKTOK_URL = f"https://www.tiktok.com/@miyahome7/video/{TIKTOK_VIDEO_ID}"
TIKTOK_SHORT_URL = "https://vt.tiktok.com/ZS4BJ6sVM/"
UNKNOWN_TIKTOK_URL = "https://www.tiktok.com/@research/video/7999999999999999999"


def async_client_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    return factory


class V02ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def test_registered_tiktok_canonical_url_returns_reviewed_fixture(self) -> None:
        response = self.client.post("/api/analyze", json={"url": TIKTOK_URL})

        self.assertEqual(200, response.status_code)
        payload = response.json()
        report = payload["report"]
        source = report["source"]
        self.assertEqual("completed", payload["status"])
        self.assertEqual("tiktok", payload["platform"])
        self.assertEqual(TIKTOK_VIDEO_ID, source["video_id"])
        self.assertEqual("Miya Home", source["author"]["name"])
        self.assertEqual("@miyahome7", source["author"]["handle"])
        self.assertEqual(170679, source["metrics"]["views"])
        self.assertEqual(
            [170679, 170700],
            [item["value"] for item in source["metrics"]["view_snapshots"]],
        )
        self.assertEqual(21, source["metrics"]["view_snapshot_difference"])
        self.assertFalse(report["delivery"]["publishable"])
        self.assertEqual("research_draft", report["delivery"]["status"])
        self.assertIn(
            "商品是否真正支持遥控",
            report["evidence_boundary"]["pending"][0],
        )

    def test_registered_tiktok_short_url_alias_returns_same_fixture(self) -> None:
        response = self.client.post("/api/analyze", json={"url": TIKTOK_SHORT_URL})

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("completed", payload["status"])
        self.assertEqual(TIKTOK_VIDEO_ID, payload["source"]["video_id"])
        self.assertEqual(TIKTOK_URL, payload["source"]["url"])

    def test_research_draft_is_never_publishable(self) -> None:
        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "", "PROJECT024_CONTENT_API_KEY": ""},
        ):
            response = self.client.post(
                "/api/analyze",
                json={
                    "url": UNKNOWN_TIKTOK_URL,
                    "transcript": "用户提交的商品视频字幕，仅作为待核验研究材料。",
                },
            )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        report = payload["report"]
        self.assertEqual("partial", payload["status"])
        self.assertEqual("research_draft", report["delivery"]["status"])
        self.assertFalse(report["delivery"]["publishable"])
        self.assertFalse(report["recommended_script"]["publishable"])
        self.assertFalse(report["publishing_package"]["publishable"])
        self.assertFalse(report["risk_gate"]["publishable"])
        self.assertEqual("not_configured", report["generation"]["status"])

    def test_market_selection_is_saved_but_localization_stays_disabled(self) -> None:
        response = self.client.post(
            "/api/analyze",
            json={
                "url": TIKTOK_URL,
                "market": {
                    "region": "Southeast Asia",
                    "country": "Malaysia",
                    "language": "en",
                },
            },
        )

        self.assertEqual(200, response.status_code)
        localization = response.json()["report"]["localization"]
        self.assertEqual("future_disabled", localization["status"])
        self.assertFalse(localization["enabled"])
        self.assertFalse(localization["applied"])
        self.assertEqual(
            {
                "region": "Southeast Asia",
                "country": "Malaysia",
                "language": "en",
            },
            localization["requested"],
        )

    def test_transcribe_rejects_unsupported_media_type(self) -> None:
        response = self.client.post(
            "/api/transcribe",
            files={"file": ("notes.txt", b"not-media", "text/plain")},
        )

        self.assertEqual(415, response.status_code)
        payload = response.json()
        self.assertEqual("failed", payload["status"])
        self.assertIn("不支持的文件扩展名", payload["message"])
        self.assertFalse(payload["source"]["external_api_call_attempted"])

    def test_transcribe_rejects_oversized_upload_request(self) -> None:
        with (
            patch.object(main_module, "MAX_UPLOAD_BYTES", 8),
            patch.object(main_module, "MAX_MULTIPART_OVERHEAD_BYTES", 0),
        ):
            response = self.client.post(
                "/api/transcribe",
                files={"file": ("sample.mp3", b"123456789", "audio/mpeg")},
            )

        self.assertEqual(413, response.status_code)
        self.assertEqual("failed", response.json()["status"])
        self.assertIn("超过允许大小", response.json()["message"])

    def test_mock_external_asr_returns_structured_transcription(self) -> None:
        secret = "unit-test-asr-secret"

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(f"Bearer {secret}", request.headers["authorization"])
            return httpx.Response(
                200,
                json={
                    "text": "一台微型挖掘机正在演示三种附件。",
                    "language": "zh",
                    "duration": 3.25,
                    "segments": [
                        {
                            "id": 0,
                            "start": 0.0,
                            "end": 3.25,
                            "text": "一台微型挖掘机正在演示三种附件。",
                        }
                    ],
                },
                request=request,
            )

        provider = ExternalAPIProvider(client_factory=async_client_factory(handler))
        router = ASRRouter(external=provider)
        with (
            patch.dict(
                os.environ,
                {
                    "PROJECT024_ASR_API_KEY": secret,
                    "PROJECT024_ASR_MODEL": "mock-whisper",
                },
            ),
            patch.object(main_module, "asr_router", router),
        ):
            response = self.client.post(
                "/api/transcribe?provider=external&language=zh",
                files={"file": ("sample.mp3", b"mock-media", "audio/mpeg")},
            )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("completed", payload["status"])
        self.assertEqual("external_api", payload["provider"])
        self.assertEqual("mock-whisper", payload["model"])
        self.assertEqual("zh", payload["language"])
        self.assertEqual("provided", payload["segments_status"])
        self.assertEqual(1, len(payload["segments"]))
        self.assertNotIn(secret, response.text)

    def test_auto_asr_reports_final_provider_after_runtime_fallback(self) -> None:
        class FailingExternal:
            name = "external_api"

            def availability(self):
                return ProviderAvailability(self.name, True, "mock ready", "mock-external")

            async def transcribe(self, media, filename, content_type, language):
                raise ASRProviderError("mock external failure")

        class SuccessfulLocal:
            name = "local"

            def availability(self):
                return ProviderAvailability(self.name, True, "mock ready", "mock-local")

            async def transcribe(self, media, filename, content_type, language):
                return TranscriptionResult(
                    transcript="本地回退转写结果",
                    provider=self.name,
                    model="mock-local",
                    language="zh",
                    segments=[],
                    segments_status="provided",
                    confidence=None,
                    confidence_status="not_provided",
                    provider_metadata={},
                )

        router = ASRRouter(external=FailingExternal(), local=SuccessfulLocal())
        with patch.object(main_module, "asr_router", router):
            response = self.client.post(
                "/api/transcribe?provider=auto",
                files={"file": ("sample.mp3", b"mock-media", "audio/mpeg")},
            )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("local", payload["provider"])
        self.assertEqual("external_api", payload["source"]["provider_initially_selected"])
        self.assertEqual("local", payload["source"]["provider_selected"])
        self.assertTrue(payload["source"]["provider_fallback_used"])
        self.assertEqual(
            ["external_api"],
            payload["source"]["provider_metadata"]["fallback"]["from"],
        )

    def test_mock_deepseek_json_generation_remains_research_only(self) -> None:
        secret = "unit-test-deepseek-secret"
        generated = {
            "summary": "用三个动作展示附件差异。",
            "marketing_structure": {
                "hook": "先展示三种附件。",
                "product_demo": "分别展示破、挖、抓。",
                "value_proposition": "只陈述可见动作。",
                "cta": "询问观众偏好。",
            },
            "recommended_script": {
                "title": "三种附件，三个任务",
                "duration_seconds": 30,
                "full_text": "先看三种附件，再分别完成三个任务。商品规格仍待核验。" * 12,
                "selection_reason": "动作证明清楚且不补写商品参数。",
            },
            "shooting_table": [
                {
                    "time": "0-3 秒",
                    "visual": "三种附件总览",
                    "voiceover": "先看今天的三个任务。",
                    "subtitle": "3 ATTACHMENTS",
                    "product_proof": "[待确认：销售套装]",
                    "sound": "真实动作声",
                }
            ] * 4,
            "publishing_package": {
                "titles": ["三种附件，三个任务"],
                "post_copy": "商品事实核验后再发布。",
                "tags": ["MiniExcavator"],
                "cta": "你想先看哪一个？",
                "comment_replies": [],
            },
            "evidence_boundary": {
                "facts": ["字幕提到三种动作。"],
                "inferences": [],
                "pending": ["商品规格与销售套装。"],
            },
        }

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(f"Bearer {secret}", request.headers["authorization"])
            request_payload = json.loads(request.content.decode("utf-8"))
            self.assertEqual("mock-deepseek", request_payload["model"])
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"content": json.dumps(generated, ensure_ascii=False)}}
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 20},
                },
                headers={"x-request-id": "mock-request-id"},
                request=request,
            )

        provider = DeepSeekContentProvider(client_factory=async_client_factory(handler))
        router = ContentGenerationRouter(provider=provider)
        with (
            patch.dict(
                os.environ,
                {
                    "DEEPSEEK_API_KEY": secret,
                    "DEEPSEEK_MODEL": "mock-deepseek",
                },
            ),
            patch.object(main_module, "content_router", router),
        ):
            response = self.client.post(
                "/api/analyze",
                json={
                    "url": UNKNOWN_TIKTOK_URL,
                    "transcript": "可见内容依次展示破碎、挖取和抓取动作。",
                    "product": {
                        "name": "测试商品",
                        "sku": "TEST-001",
                        "category": "工程车模型",
                        "selling_points": ["三种附件演示"],
                        "specifications": {"color": "yellow"},
                        "approved_claims": ["展示三种附件动作"],
                        "evidence_urls": ["https://example.com/evidence"],
                    },
                },
            )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        report = payload["report"]
        self.assertEqual("partial", payload["status"])
        self.assertEqual("completed_research_draft", report["generation"]["status"])
        self.assertEqual("三种附件，三个任务", report["recommended_script"]["title"])
        self.assertFalse(report["delivery"]["publishable"])
        self.assertFalse(report["recommended_script"]["publishable"])
        self.assertFalse(report["publishing_package"]["publishable"])
        self.assertFalse(report["risk_gate"]["publishable"])
        self.assertNotIn(secret, response.text)

    def test_deepseek_failure_falls_back_without_leaking_secret(self) -> None:
        secret = "unit-test-deepseek-failure-secret"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                json={"error": {"message": f"invalid credential {secret}"}},
                request=request,
            )

        provider = DeepSeekContentProvider(client_factory=async_client_factory(handler))
        router = ContentGenerationRouter(provider=provider)
        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": secret}),
            patch.object(main_module, "content_router", router),
        ):
            response = self.client.post(
                "/api/analyze",
                json={
                    "url": UNKNOWN_TIKTOK_URL,
                    "transcript": "用户提交的字幕仍应保留为研究材料。",
                },
            )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        report = payload["report"]
        self.assertEqual("partial", payload["status"])
        self.assertEqual(
            "failed_research_draft_fallback",
            report["generation"]["status"],
        )
        self.assertIn("[redacted]", report["generation"]["message"])
        self.assertFalse(report["delivery"]["publishable"])
        self.assertFalse(report["risk_gate"]["publishable"])
        self.assertNotIn(secret, response.text)


if __name__ == "__main__":
    unittest.main()
