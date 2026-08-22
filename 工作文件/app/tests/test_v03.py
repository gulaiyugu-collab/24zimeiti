from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services import ContentGenerationRouter, DeepSeekContentProvider


DEMO_URL = "https://www.douyin.com/video/7666774161494183218"
UNKNOWN_URL = "https://www.douyin.com/video/7999999999999999999"


def async_client_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    return factory


class V03QuickResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def test_registered_fixture_exposes_plain_language_quick_result(self) -> None:
        response = self.client.post(
            "/api/analyze", json={"url": DEMO_URL, "analysis_mode": "quick"}
        )

        self.assertEqual(200, response.status_code)
        quick = response.json()["report"]["quick_result"]
        self.assertTrue(quick["summary"])
        self.assertGreaterEqual(len(quick["what_happens"]), 1)
        self.assertGreaterEqual(len(quick["transferable"]), 1)

    def test_quick_model_returns_small_result_without_full_package(self) -> None:
        secret = "unit-test-v03-secret"
        quick = {
            "summary": "先用一个具体场景引起注意，再给出可执行的判断方法。",
            "what_happens": ["具体场景", "解释原因", "给出行动"],
            "why_it_works": ["开头有信息缺口", "步骤很清楚"],
            "transferable": ["先讲方法，再谈产品"],
            "original_angle": "从用户自己的标签和使用场景出发。",
            "evidence_boundary": {
                "facts": ["字幕中出现了三个步骤"],
                "inferences": ["观众可能需要清单"],
                "pending": ["商品资料仍需核对"],
            },
        }

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            self.assertEqual(900, payload["max_tokens"])
            self.assertIn("快速内容解读器", payload["messages"][0]["content"])
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps(quick, ensure_ascii=False)}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 30},
                },
                headers={"x-request-id": "v03-mock"},
                request=request,
            )

        router = ContentGenerationRouter(
            provider=DeepSeekContentProvider(client_factory=async_client_factory(handler))
        )
        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": secret}, clear=False),
            patch.object(main_module, "content_router", router),
        ):
            response = self.client.post(
                "/api/analyze",
                json={
                    "url": UNKNOWN_URL,
                    "analysis_mode": "quick",
                    "transcript": "这是用于快速解读的字幕材料。",
                },
            )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("partial", payload["status"])
        self.assertEqual(
            "completed_quick", payload["diagnostics"]["generation"]["status"]
        )
        self.assertNotIn("generation", payload["report"])
        self.assertEqual(quick["summary"], payload["report"]["quick_result"]["summary"])
        self.assertIsNone(payload["report"]["recommended_script"]["full_text"])
        self.assertNotIn(secret, response.text)


if __name__ == "__main__":
    unittest.main()
