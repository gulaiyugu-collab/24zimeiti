from __future__ import annotations

import json
import os
import unittest
from typing import Any
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.operations_agent import OperationsAgent, OperationsAgentRequest


def _factory(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, **kwargs)

    return factory


class OperationsAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_confirmed_call_returns_full_updated_draft(self) -> None:
        calls: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(json.loads(request.content))
            generated = {
                "reply": "已把结果提前，并保留证据边界。",
                "updated_text": "先给结果，再解释两个已经核验的步骤。[待确认：画面细节]",
                "next_actions": ["核对第一句话", "只测试开头变量"],
            }
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps(generated, ensure_ascii=False)}}],
                    "usage": {"total_tokens": 321},
                },
                request=request,
            )

        agent = OperationsAgent(client_factory=_factory(handler))
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-key"}, clear=False):
            result = await agent.chat(
                OperationsAgentRequest(
                    message="把开头改快一点",
                    mode="script",
                    page="analysis",
                    draft="现在先铺垫背景。",
                    context={"facts": ["已有两个步骤"]},
                    confirm_paid=True,
                )
            )
        self.assertEqual(1, len(calls))
        self.assertTrue(json.loads(calls[0]["messages"][1]["content"])["request_requires_edit"])
        self.assertIn("先给结果", result["updated_text"])
        self.assertEqual("apply", result["decision"]["action"])
        self.assertTrue(result["decision"]["changed"])
        self.assertTrue(result["decision"]["request_requires_edit"])
        self.assertEqual(len("现在先铺垫背景。"), result["decision"]["before_chars"])
        self.assertTrue(result["provider_metadata"]["paid_api_called"])
        self.assertEqual(321, result["provider_metadata"]["usage"]["total_tokens"])

    async def test_question_can_keep_draft_without_false_execution(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            generated = {
                "reply": "当前稿件已经覆盖核心信息，暂不改写。",
                "updated_text": "原始策略",
                "next_actions": ["继续观察评论反馈"],
            }
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(generated, ensure_ascii=False)}}]},
                request=request,
            )

        agent = OperationsAgent(client_factory=_factory(handler))
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-key"}, clear=False):
            result = await agent.chat(
                OperationsAgentRequest(
                    message="还有其他建议吗？",
                    mode="strategy",
                    page="douyin",
                    draft="原始策略",
                    context={},
                    confirm_paid=True,
                )
            )
        self.assertEqual("keep", result["decision"]["action"])
        self.assertFalse(result["decision"]["changed"])


class OperationsAgentApiTests(unittest.TestCase):
    def test_unconfirmed_call_is_rejected_before_provider(self) -> None:
        agent = OperationsAgent()
        with patch.object(main_module, "operations_agent", agent), patch.dict(
            os.environ, {"DEEPSEEK_API_KEY": "unit-test-key"}, clear=False
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/agent/chat",
                    json={
                        "message": "修改脚本",
                        "mode": "script",
                        "page": "analysis",
                        "draft": "原稿",
                        "context": {},
                        "history": [],
                        "confirm_paid": False,
                    },
                )
        self.assertEqual(409, response.status_code, response.text)


if __name__ == "__main__":
    unittest.main()
