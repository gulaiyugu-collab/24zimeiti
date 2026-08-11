from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


DEMO_URL = "https://www.douyin.com/video/7666774161494183218"


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def test_health(self) -> None:
        response = self.client.get("/api/health")

        self.assertEqual(200, response.status_code)
        self.assertEqual("ok", response.json()["status"])
        self.assertEqual("自媒体通关搭档", response.json()["service"])
        self.assertIsInstance(response.json()["paid_content_enabled"], bool)

    def test_platform_statuses(self) -> None:
        response = self.client.get("/api/platforms")

        self.assertEqual(200, response.status_code)
        platforms = {item["id"]: item["status"] for item in response.json()["platforms"]}
        self.assertEqual("active", platforms["douyin"])
        self.assertEqual("planned", platforms["youtube"])
        self.assertEqual("planned", platforms["facebook"])
        self.assertEqual("planned", platforms["x"])

    def test_demo_endpoint_returns_completed_fixture(self) -> None:
        response = self.client.get("/api/demo")

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual(DEMO_URL, payload["sample_input"]["url"])
        self.assertEqual("completed", payload["result"]["status"])
        self.assertEqual("fixture", payload["result"]["source"]["acquisition_mode"])
        self.assertIn("content_package", payload["result"]["report"])
        self.assertIn("full_text", payload["result"]["report"]["content_package"]["script"])
        self.assertFalse(payload["result"]["report"]["risk_gate"]["publishable"])

    def test_registered_douyin_url_returns_completed(self) -> None:
        response = self.client.post("/api/analyze", json={"url": DEMO_URL})

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("completed", payload["status"])
        self.assertEqual("douyin", payload["platform"])
        self.assertEqual("7666774161494183218", payload["source"]["aweme_id"])

    def test_share_sentence_can_resolve_registered_case(self) -> None:
        response = self.client.post(
            "/api/analyze",
            json={"url": f"复制这段文字打开抖音 {DEMO_URL} 看视频"},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("completed", response.json()["status"])

    def test_unknown_douyin_without_transcript_needs_input(self) -> None:
        response = self.client.post(
            "/api/analyze",
            json={"url": "https://www.douyin.com/video/7999999999999999999"},
        )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("needs_input", payload["status"])
        self.assertIsNone(payload["report"])
        self.assertIn("视频字幕或口播稿", payload["missing"])

    def test_unknown_douyin_with_transcript_is_partial(self) -> None:
        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "", "PROJECT024_CONTENT_API_KEY": ""},
        ):
            response = self.client.post(
                "/api/analyze",
                json={
                    "url": "https://www.douyin.com/video/7999999999999999999",
                    "transcript": "这是用户提供的字幕，只能作为待分析材料。",
                },
            )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("partial", payload["status"])
        self.assertIsNone(payload["report"]["distillation"])
        self.assertIsNone(payload["report"]["content_package"])
        self.assertFalse(payload["report"]["risk_gate"]["publishable"])

    def test_planned_platform_is_unsupported(self) -> None:
        response = self.client.post(
            "/api/analyze",
            json={"url": "https://www.youtube.com/watch?v=example"},
        )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("unsupported", payload["status"])
        self.assertEqual("youtube", payload["platform"])
        self.assertIn("后续平台计划", payload["message"])

    def test_unknown_platform_is_unsupported(self) -> None:
        response = self.client.post(
            "/api/analyze",
            json={"url": "https://example.com/content/1"},
        )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("unsupported", payload["status"])
        self.assertEqual("unknown", payload["platform"])

    def test_missing_url_is_validation_error(self) -> None:
        response = self.client.post("/api/analyze", json={})

        self.assertEqual(422, response.status_code)


    def test_demo_fixture_preserves_verified_public_snapshot(self) -> None:
        response = self.client.get("/api/demo")

        self.assertEqual(200, response.status_code)
        report = response.json()["result"]["report"]
        source = report["source"]
        self.assertEqual("2026-07-31T00:57:18+08:00", source["snapshot_at"])
        self.assertEqual(135000, source["author"]["followers"])
        self.assertEqual(481, source["metrics"]["likes"])
        self.assertEqual(53, source["metrics"]["comments"])
        self.assertEqual(161, source["metrics"]["favorites"])
        self.assertEqual(91, source["metrics"]["shares"])
        self.assertEqual(5, len(source["content"]["chapters"]))
        self.assertEqual(6, source["public_comment_summary"]["sample_size"])
        self.assertIn("audience_insights", report)
        self.assertEqual(3, len(report["content_package"]["post_copy"]["title_options"]))
        self.assertEqual(5, len(report["content_package"]["comment_replies"]))
        self.assertEqual("needs_human_review", report["risk_gate"]["status"])

if __name__ == "__main__":
    unittest.main()
