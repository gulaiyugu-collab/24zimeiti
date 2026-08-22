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
from app.services.product_relevance import (
    build_product_requirements,
    infer_product_relevance,
)


UNKNOWN_URL = "https://www.douyin.com/video/7999999999999999999"


def async_client_factory(handler):
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    return factory


class ProductRelevanceRuleTests(unittest.TestCase):
    def test_product_content_is_detected(self) -> None:
        result = infer_product_relevance(
            transcript="这款保温杯的材质、容量和售价都在商品页，今天演示实际使用效果。"
        )

        self.assertEqual("has_product", result["status"])
        self.assertTrue(result["has_product"])
        self.assertTrue(result["evidence"])

    def test_non_product_content_skips_product_fields(self) -> None:
        result = infer_product_relevance(
            transcript="今天分享三个提高专注力的方法，先减少干扰，再拆小任务，最后记录每天的复盘。"
        )

        self.assertEqual("no_product", result["status"])
        self.assertFalse(result["has_product"])

    def test_ambiguous_content_requires_confirmation(self) -> None:
        result = infer_product_relevance(
            transcript="这个东西最近很好用，很多人都在问。"
        )

        self.assertEqual("needs_confirmation", result["status"])
        self.assertIsNone(result["has_product"])

    def test_english_keyword_matching_does_not_match_inside_words(self) -> None:
        result = infer_product_relevance(
            transcript="Kitchen focus habits are a practical education topic for daily life."
        )

        self.assertEqual("no_product", result["status"])

    def test_confirmed_unknown_keeps_tri_state_value(self) -> None:
        result = infer_product_relevance(
            transcript="内容很短。",
            override="needs_confirmation",
        )

        self.assertEqual("needs_confirmation", result["status"])
        self.assertIsNone(result["has_product"])

    def test_product_follow_up_only_lists_fields_still_missing(self) -> None:
        product = {
            "name": "测试杯",
            "category": "杯具",
            "selling_points": ["保温"],
            "specifications": {"capacity": "500ml"},
        }
        relevance = infer_product_relevance(product=product)
        requirements = build_product_requirements(
            product=product,
            relevance=relevance,
        )

        self.assertNotIn("商品名称或 SKU", requirements["missing_fields"])
        self.assertNotIn("商品品类", requirements["missing_fields"])
        self.assertIn("商品证明材料", requirements["missing_fields"])
        self.assertNotIn("商品名称或 SKU", " ".join(requirements["follow_up"]))


class ProductRelevanceApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client.close()

    def test_non_product_analysis_does_not_report_product_gaps(self) -> None:
        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "", "PROJECT024_CONTENT_API_KEY": ""},
        ):
            response = self.client.post(
                "/api/analyze",
                json={
                    "url": UNKNOWN_URL,
                    "transcript": "今天分享三个提高专注力的方法，先减少干扰，再拆小任务，最后记录每天的复盘。",
                },
            )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        report = payload["report"]
        self.assertEqual("no_product", report["product_relevance"]["status"])
        self.assertEqual("not_applicable", report["product_requirements"]["status"])
        self.assertEqual([], report["product_requirements"]["missing_fields"])
        self.assertFalse(any("商品" in item for item in payload["missing"]))
        self.assertIn(
            "画面 OCR 与镜头结构分析",
            report["requirements"]["optional_enhancements"],
        )

    def test_product_gaps_only_enter_rewrite_or_publish_scope(self) -> None:
        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "", "PROJECT024_CONTENT_API_KEY": ""},
        ):
            response = self.client.post(
                "/api/analyze",
                json={
                    "url": UNKNOWN_URL,
                    "transcript": "这款商品今天演示三种配件，价格和规格请查看商品页。",
                },
            )

        report = response.json()["report"]
        self.assertEqual("has_product", report["product_relevance"]["status"])
        self.assertIn(
            "商品核心卖点",
            report["requirements"]["product_for_rewrite_or_publish"],
        )
        self.assertNotIn("商品核心卖点", response.json()["missing"])
        self.assertFalse(
            report["requirements"]["interpretation_blocked_by_product"]
        )

    def test_user_override_can_confirm_non_product(self) -> None:
        with patch.dict(
            os.environ,
            {"DEEPSEEK_API_KEY": "", "PROJECT024_CONTENT_API_KEY": ""},
        ):
            response = self.client.post(
                "/api/analyze",
                json={
                    "url": UNKNOWN_URL,
                    "transcript": "这款商品看起来很适合日常使用。",
                    "product_relevance_override": "no_product",
                },
            )

        report = response.json()["report"]
        self.assertEqual("no_product", report["product_relevance"]["status"])
        self.assertEqual("user_confirmation", report["product_relevance"]["source"])
        self.assertEqual([], report["product_requirements"]["missing_fields"])

    def test_completed_model_analysis_clears_distillation_gap(self) -> None:
        secret = "unit-test-product-relevance-secret"
        generated = {
            "summary": "用一个问题引出三个可执行的专注方法。",
            "product_relevance": {
                "status": "no_product",
                "confidence": "high",
                "evidence": ["字幕只讨论专注方法，没有具体商品或购买行为。"],
                "reason": "这是知识方法类内容。",
                "follow_up": ["继续按方法型内容制作，无需补商品资料。"],
            },
            "marketing_structure": {
                "hook": "先提出专注困难。",
                "product_demo": "不涉及商品，改为方法演示。",
                "value_proposition": "给出三个可执行动作。",
                "cta": "邀请观众记录实践结果。",
            },
            "recommended_script": {
                "title": "三个动作找回专注",
                "duration_seconds": 60,
                "full_text": "先关掉无关提醒，只保留眼前这一件事。把任务拆成十分钟能完成的小步骤，每做完一步就打一个勾。遇到走神时，不要责备自己，先写下刚才被什么打断，再回到当前步骤。每天结束前，用一分钟记录今天最容易分心的时段和最有效的处理办法。连续实践一周后，你会得到一份属于自己的专注规律。方法是否适合你，要以真实记录为准，不需要照搬别人的节奏。" * 2,
                "selection_reason": "完整展示方法、实践和复盘闭环。",
            },
            "shooting_table": [
                {
                    "time": f"{index * 5}-{index * 5 + 5} 秒",
                    "visual": "记录一个专注动作。",
                    "voiceover": "按步骤执行并记录。",
                    "subtitle": "一步一记录",
                    "product_proof": "不适用",
                    "sound": "轻环境声",
                }
                for index in range(4)
            ],
            "publishing_package": {
                "titles": ["三个动作找回专注"],
                "post_copy": "记录一周，找到自己的专注规律。",
                "tags": ["专注力", "学习方法"],
                "cta": "你最容易在哪个时段分心？",
                "comment_replies": [],
            },
            "evidence_boundary": {
                "facts": ["字幕提出三个专注方法。"],
                "inferences": ["持续记录可能帮助复盘。"],
                "pending": [],
            },
        }

        def handler(request: httpx.Request) -> httpx.Response:
            request_payload = json.loads(request.content)
            self.assertIn(
                "product_relevance",
                request_payload["messages"][1]["content"],
            )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(generated, ensure_ascii=False)
                            }
                        }
                    ]
                },
                request=request,
            )

        router = ContentGenerationRouter(
            provider=DeepSeekContentProvider(
                client_factory=async_client_factory(handler)
            )
        )
        with (
            patch.dict(os.environ, {"DEEPSEEK_API_KEY": secret}, clear=False),
            patch.object(main_module, "content_router", router),
        ):
            response = self.client.post(
                "/api/analyze",
                json={
                    "url": UNKNOWN_URL,
                    "analysis_mode": "full",
                    "transcript": "今天分享三个提高专注力的方法，先减少干扰，再拆小任务，最后记录每天的复盘。",
                },
            )

        self.assertEqual(200, response.status_code)
        payload = response.json()
        report = payload["report"]
        self.assertEqual(
            "completed_research_draft",
            payload["diagnostics"]["generation"]["status"],
        )
        self.assertNotIn("generation", report)
        self.assertEqual("no_product", report["product_relevance"]["status"])
        self.assertNotIn("经模型或人工完成的内容蒸馏", payload["missing"])
        self.assertEqual([], report["requirements"]["blocking_for_interpretation"])
        self.assertIn("content_demonstration", report["distillation"])
        self.assertNotIn("product_demo", report["distillation"])
        self.assertNotIn("product_proof", report["shooting_table"]["columns"])
        self.assertTrue(
            all("product_proof" not in row for row in report["shooting_table"]["rows"])
        )


if __name__ == "__main__":
    unittest.main()
