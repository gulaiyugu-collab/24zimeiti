from __future__ import annotations

import json
import os
import unittest
from typing import Any
from unittest.mock import patch

import httpx

from app.services.content_generation import (
    ContentGenerationRouter,
    DeepSeekContentProvider,
)


def _quick_payload() -> dict[str, Any]:
    return {
        "summary": "候选 Agent 认为内容应先解释问题，再给出核对步骤。",
        "what_happens": ["提出问题", "说明步骤"],
        "why_it_works": ["结构清楚"],
        "transferable": ["先给核对方法"],
        "original_angle": "从观众自己的使用场景重新组织内容。",
        "evidence_boundary": {
            "facts": ["字幕出现两个步骤。"],
            "inferences": [],
            "pending": ["画面内容需要人工核验。"],
        },
    }


def _full_payload() -> dict[str, Any]:
    full_text = (
        "开头先说明一个具体问题，再用字幕中已经出现的步骤逐项解释。"
        "每一步只引用现有材料，不补写商品参数，也不把推断说成事实。"
        "接着给出观众可以自己核对的方法，并明确哪些信息还需要人工确认。"
        "最后邀请观众根据自己的使用场景做选择，避免承诺任何未经验证的结果。"
    ) * 2
    row = {
        "time": "0-15秒",
        "visual": "展示与已核验字幕对应的操作画面",
        "voiceover": "先说明问题，再解释核对方法。",
        "subtitle": "先核对，再判断",
        "product_proof": "无新增商品事实",
        "sound": "保留现场声音",
    }
    return {
        "summary": "这是由多个分析角色审阅后合成的研究稿。",
        "marketing_structure": {
            "hook": "用具体问题开场。",
            "product_demo": "只展示已有证据支持的步骤。",
            "value_proposition": "帮助观众自行核对。",
            "cta": "邀请观众补充自己的场景。",
        },
        "recommended_script": {
            "title": "先核对证据，再决定怎么拍",
            "duration_seconds": 70,
            "full_text": full_text,
            "selection_reason": "合并角色意见后仍没有越过证据边界。",
        },
        "shooting_table": [dict(row) for _ in range(4)],
        "publishing_package": {
            "titles": ["先核对证据，再决定怎么拍"],
            "post_copy": "这是一份待核验的内容研究稿。",
            "tags": ["内容研究"],
            "cta": "说说你的使用场景。",
            "comment_replies": [],
        },
        "evidence_boundary": {
            "facts": ["字幕提供了操作步骤。"],
            "inferences": [],
            "pending": ["画面语义仍需人工核验。"],
        },
    }


def _factory(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, **kwargs)

    return factory


class MultiAgentContentGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_generation_fanout_and_fanin(self) -> None:
        calls: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            calls.append(payload)
            generated = _quick_payload() if payload["max_tokens"] == 900 else _full_payload()
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(generated, ensure_ascii=False)}}]},
                request=request,
            )

        provider = DeepSeekContentProvider(client_factory=_factory(handler))
        router = ContentGenerationRouter(provider)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-key"}, clear=False):
            result = await router.generate(
                strategy="multi_agent",
                platform="douyin",
                transcript="只包含已核验字幕。",
                product_context=None,
                product=None,
            )

        assert result is not None
        orchestration = result.provider_metadata["orchestration"]
        self.assertEqual("multi_agent", orchestration["mode"])
        self.assertEqual(3, orchestration["fanout_count"])
        self.assertEqual(3, len(orchestration["completed_roles"]))
        self.assertEqual("completed", orchestration["fan_in_status"])
        self.assertEqual(4, orchestration["call_count"])
        self.assertEqual(4, len(calls))
        self.assertEqual(3, len(json.loads(calls[-1]["messages"][1]["content"])["agent_outputs"]))
        self.assertFalse(result.data["publishable"])

    async def test_role_failure_is_recorded_and_fan_in_continues(self) -> None:
        calls: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            calls.append(payload)
            if payload["max_tokens"] == 900 and "证据与风险" in payload["messages"][0]["content"]:
                return httpx.Response(503, json={"error": {"message": "temporary"}}, request=request)
            generated = _quick_payload() if payload["max_tokens"] == 900 else _full_payload()
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(generated, ensure_ascii=False)}}]},
                request=request,
            )

        provider = DeepSeekContentProvider(client_factory=_factory(handler))
        router = ContentGenerationRouter(provider)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-key"}, clear=False):
            result = await router.generate(
                strategy="multi_agent",
                platform="douyin",
                transcript="只包含已核验字幕。",
                product_context=None,
                product=None,
            )

        assert result is not None
        orchestration = result.provider_metadata["orchestration"]
        self.assertEqual(["evidence_auditor"], orchestration["failed_roles"])
        self.assertEqual("completed", orchestration["fan_in_status"])
        self.assertFalse(orchestration["fallback_used"])
        self.assertEqual(4, len(calls))

    async def test_single_model_strategy_keeps_one_call(self) -> None:
        calls: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(_full_payload(), ensure_ascii=False)}}]},
                request=request,
            )

        provider = DeepSeekContentProvider(client_factory=_factory(handler))
        router = ContentGenerationRouter(provider)
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "unit-test-key"}, clear=False):
            result = await router.generate(
                strategy="single_model",
                platform="douyin",
                transcript="只包含已核验字幕。",
                product_context=None,
                product=None,
            )

        assert result is not None
        self.assertEqual(1, len(calls))
        self.assertEqual("single_model", result.provider_metadata["orchestration"]["mode"])


if __name__ == "__main__":
    unittest.main()
