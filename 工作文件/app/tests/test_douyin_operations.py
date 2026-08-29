from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main_module
from app.main import app
from app.services.douyin_operations import (
    DouyinTopicCreate,
    DouyinTopicNotFoundError,
    DouyinTopicStore,
    DouyinTopicUpdate,
)


def _topic_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": "抖音选题：前三秒先给结果",
        "source_url": "https://www.douyin.com/video/7670900617237286186",
        "analysis_ref": "用户可见分析引用",
        "content_summary": "先给结果，再展示三个可核验步骤。",
        "hypothesis": "待验证：具体结果前置可能提高前 3 秒留存。",
        "status": "draft",
    }
    payload.update(overrides)
    return payload


class DouyinTopicStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"PROJECT024_DOUYIN_OPERATIONS_ROOT": self._tmp.name},
        )
        self._env.start()
        self.store = DouyinTopicStore()

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_create_deduplicates_exact_analysis_snapshot(self) -> None:
        payload = DouyinTopicCreate(**_topic_payload())
        first = self.store.create(payload)
        second = self.store.create(payload)

        self.assertEqual(first["id"], second["id"])
        self.assertFalse(first["deduplicated"])
        self.assertTrue(second["deduplicated"])
        self.assertEqual(1, len(self.store.list_all()))
        self.assertTrue(str(first["id"]).startswith("dy_"))
        self.assertEqual("douyin", first["platform"])

    def test_non_douyin_source_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            DouyinTopicCreate(
                **_topic_payload(source_url="https://www.tiktok.com/@demo/video/1")
            )

    def test_update_status_persists_and_filters(self) -> None:
        created = self.store.create(DouyinTopicCreate(**_topic_payload()))
        updated = self.store.update(
            str(created["id"]), DouyinTopicUpdate(status="ready")
        )

        self.assertEqual("ready", updated["status"])
        self.assertEqual(2, updated["version"])
        self.assertEqual([created["id"]], [item["id"] for item in self.store.list_all(status="ready")])
        self.assertEqual([], self.store.list_all(status="draft"))

    def test_update_script_summary_without_changing_status(self) -> None:
        created = self.store.create(DouyinTopicCreate(**_topic_payload()))
        updated = self.store.update(
            str(created["id"]),
            DouyinTopicUpdate(content_summary="Agent 修改后的完整脚本摘要。"),
        )
        self.assertEqual("draft", updated["status"])
        self.assertEqual("Agent 修改后的完整脚本摘要。", updated["content_summary"])

    def test_missing_topic_raises_not_found(self) -> None:
        with self.assertRaises(DouyinTopicNotFoundError):
            self.store.update("dy_missing", DouyinTopicUpdate(status="ready"))


class DouyinTopicApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"PROJECT024_DOUYIN_OPERATIONS_ROOT": self._tmp.name},
        )
        self._env.start()
        self.store = DouyinTopicStore()
        self._store_patch = patch.object(main_module, "douyin_topics", self.store)
        self._store_patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self._store_patch.stop()
        self._env.stop()
        self._tmp.cleanup()

    def test_create_list_get_and_update_topic(self) -> None:
        created = self.client.post("/api/douyin/topics", json=_topic_payload())
        self.assertEqual(201, created.status_code, created.text)
        topic_id = created.json()["id"]

        listed = self.client.get("/api/douyin/topics")
        self.assertEqual(200, listed.status_code, listed.text)
        self.assertEqual([topic_id], [item["id"] for item in listed.json()["topics"]])

        fetched = self.client.get(f"/api/douyin/topics/{topic_id}")
        self.assertEqual(200, fetched.status_code, fetched.text)
        self.assertEqual("draft", fetched.json()["status"])

        updated = self.client.patch(
            f"/api/douyin/topics/{topic_id}", json={"status": "ready"}
        )
        self.assertEqual(200, updated.status_code, updated.text)
        self.assertEqual("ready", updated.json()["status"])

    def test_api_rejects_non_douyin_and_unknown_status(self) -> None:
        wrong_source = self.client.post(
            "/api/douyin/topics",
            json=_topic_payload(source_url="https://www.tiktok.com/@demo/video/1"),
        )
        self.assertEqual(422, wrong_source.status_code, wrong_source.text)

        wrong_filter = self.client.get("/api/douyin/topics?status=published")
        self.assertEqual(422, wrong_filter.status_code, wrong_filter.text)

        missing = self.client.patch(
            "/api/douyin/topics/dy_missing", json={"status": "ready"}
        )
        self.assertEqual(404, missing.status_code, missing.text)
