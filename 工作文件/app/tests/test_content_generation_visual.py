from __future__ import annotations

import json
import os
import unittest
from typing import Any
from unittest.mock import patch

import httpx

from app.services.content_generation import DeepSeekContentProvider


def _client_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, **kwargs)

    return factory


def _response(request: httpx.Request, generated: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"content": json.dumps(generated, ensure_ascii=False)}}
            ]
        },
        request=request,
    )


def _full_generated() -> dict[str, Any]:
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
        "summary": "这是一份只依据现有证据整理的研究稿。",
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
            "selection_reason": "这版没有越过现有证据边界。",
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


def _quick_generated() -> dict[str, Any]:
    return {
        "summary": "内容先提出问题，再给出核对步骤。",
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


def _user_payload(request_payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(request_payload["messages"][1]["content"])


class VisualEvidencePromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_generation_only_sends_allowlisted_visual_fields(self) -> None:
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return _response(request, _full_generated())

        provider = DeepSeekContentProvider(client_factory=_client_factory(handler))
        visual_evidence = {
            "job_id": "job-secret-123",
            "input_sha256": "a" * 64,
            "config_hash": "b" * 64,
            "report_artifact_url": "https://secret.example/frame-report",
            "probe": {
                "coverage_seconds": 61.23456,
                "truncated": False,
                "width": 1080,
                "height": 1920,
                "codec_name": "h264",
                "absolute_path": "G:\\private\\source.mp4",
            },
            "scene_structure": {
                "method": "ffmpeg_scene_score_v1",
                "candidate_cut_count": 12,
                "estimated_segment_count": 13,
                "cuts_per_minute": 11.98765,
                "pace": "moderate",
                "sampling_fps": 5,
                "threshold": 0.3,
                "cuts": [{"frame_url": "https://secret.example/frame.jpg"}],
            },
            "ocr": {
                "status": "unavailable",
                "provider": None,
                "reason_code": "engine_not_installed",
                "text_items": ["这段伪造文字不能在 unavailable 状态进入提示词"],
                "blocks": [{"text": "secret-block", "sha256": "c" * 64}],
            },
            "frames": [
                {
                    "artifact_url": "https://secret.example/frame.jpg",
                    "path": "G:\\private\\frame.jpg",
                    "sha256": "d" * 64,
                }
            ],
            "limits": {"max_frames": 12, "ffmpeg_path": "G:\\bin\\ffmpeg.exe"},
        }

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "mock-only-key"}, clear=False):
            await provider.generate(
                platform="douyin",
                transcript="只包含已核验字幕。",
                product_context=None,
                product=None,
                visual_evidence=visual_evidence,
            )

        self.assertEqual(1, len(captured))
        request_payload = captured[0]
        user_payload = _user_payload(request_payload)
        self.assertEqual(
            {
                "probe": {
                    "coverage_seconds": 61.235,
                    "truncated": False,
                    "width": 1080,
                    "height": 1920,
                },
                "scene_structure": {
                    "method": "ffmpeg_scene_score_v1",
                    "candidate_cut_count": 12,
                    "estimated_segment_count": 13,
                    "cuts_per_minute": 11.988,
                    "pace": "moderate",
                },
                "ocr": {
                    "status": "unavailable",
                    "provider": None,
                    "reason_code": "engine_not_installed",
                },
            },
            user_payload["visual_evidence"],
        )
        serialized_request = json.dumps(request_payload, ensure_ascii=False)
        for sensitive_key in (
            "job_id",
            "input_sha256",
            "config_hash",
            "report_artifact_url",
            "frames",
            "limits",
            "absolute_path",
            "sampling_fps",
            "threshold",
            "cuts",
            "blocks",
        ):
            self.assertNotIn(f'"{sensitive_key}"', serialized_request)
        for sensitive_value in (
            "job-secret-123",
            "a" * 64,
            "b" * 64,
            "G:\\private\\source.mp4",
            "G:\\private\\frame.jpg",
            "https://secret.example/frame.jpg",
            "secret-block",
            "这段伪造文字不能在 unavailable 状态进入提示词",
        ):
            self.assertNotIn(sensitive_value, serialized_request)

        system_prompt = request_payload["messages"][0]["content"]
        self.assertIn("视觉数据只能作为字幕和用户资料的补充证据", system_prompt)
        self.assertIn("镜头切点、分段数和节奏都是机器估算", system_prompt)
        self.assertIn("不得根据抽帧数量", system_prompt)
        self.assertIn("禁止声称看到了画面文字或物体", system_prompt)

    async def test_quick_generation_bounds_future_ocr_text_items(self) -> None:
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return _response(request, _quick_generated())

        provider = DeepSeekContentProvider(client_factory=_client_factory(handler))
        text_items: list[Any] = [f"画面文字 {index}" for index in range(22)]
        text_items[0] = "甲" * 250
        text_items[1] = {"text": "从结构中只提取文字", "frame_url": "secret-frame"}

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "mock-only-key"}, clear=False):
            await provider.generate_quick(
                platform="tiktok",
                transcript="只包含已核验字幕。",
                product_context=None,
                product=None,
                visual_evidence={
                    "ocr": {
                        "status": "completed",
                        "provider": "local-ocr",
                        "reason_code": "ok",
                        "text_items": text_items,
                    }
                },
            )

        request_payload = captured[0]
        visual_payload = _user_payload(request_payload)["visual_evidence"]
        self.assertEqual(20, len(visual_payload["ocr"]["text_items"]))
        self.assertEqual(200, len(visual_payload["ocr"]["text_items"][0]))
        self.assertEqual("从结构中只提取文字", visual_payload["ocr"]["text_items"][1])
        self.assertNotIn("secret-frame", json.dumps(request_payload, ensure_ascii=False))
        self.assertIn(
            "视觉数据只能作为字幕和用户资料的补充证据",
            request_payload["messages"][0]["content"],
        )

    async def test_omitted_and_none_visual_evidence_keep_old_calls_compatible(self) -> None:
        captured: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            captured.append(payload)
            generated = _full_generated() if payload["max_tokens"] == 4000 else _quick_generated()
            return _response(request, generated)

        provider = DeepSeekContentProvider(client_factory=_client_factory(handler))
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "mock-only-key"}, clear=False):
            await provider.generate(
                platform="douyin",
                transcript="旧调用不传视觉参数。",
                product_context=None,
                product=None,
            )
            await provider.generate_quick(
                platform="douyin",
                transcript="显式传入空视觉参数。",
                product_context=None,
                product=None,
                visual_evidence=None,
            )

        self.assertEqual(2, len(captured))
        self.assertIsNone(_user_payload(captured[0])["visual_evidence"])
        self.assertIsNone(_user_payload(captured[1])["visual_evidence"])


if __name__ == "__main__":
    unittest.main()
