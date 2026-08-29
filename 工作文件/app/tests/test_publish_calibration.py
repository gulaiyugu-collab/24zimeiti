from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main_module
from app.main import app
from app.services.publish_calibration import (
    DEFAULT_WINDOW_HOURS,
    METRIC_KEYS,
    SCORE_DIMENSIONS,
    PublishBackfillInput,
    PublishCalibrationConflictError,
    PublishCalibrationStore,
    PublishCalibrationValidationError,
    PublishExperimentCreate,
    PublishExperimentNotFoundError,
    PublishRecordInput,
)


def _scores() -> list[dict[str, object]]:
    return [
        {"dimension": dimension, "score": (index % 5) + 1, "note": "盲评"}
        for index, dimension in enumerate(SCORE_DIMENSIONS)
    ]


def _create_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "title": "测试发布实验",
        "source_url": "https://www.douyin.com/video/7670900617237286186",
        "analysis_ref": "analysis-job-001",
        "source_topic_id": "dy_20260824T120000Z_1234abcd",
        "content_summary": "前三秒提出问题，随后展示产品证据。",
        "platform": "抖音",
        "hypothesis": "待验证：同口径下，这个内容版本可能达到预测区间。",
        "scores": _scores(),
        "predictions": [
            {"key": "impressions", "low": 10000, "high": 20000},
            {"key": "views", "low": 5000, "high": 10000},
            {"key": "retention", "low": 20, "high": 40},
        ],
    }
    base.update(overrides)
    return base


def _publish_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "publish_url": "https://v.douyin.com/example",
        "platform": "抖音",
        "published_at": "2026-08-22T12:30:00+08:00",
    }
    base.update(overrides)
    return base


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env_patch = patch.dict(
            os.environ,
            {"PROJECT024_PUBLISH_CALIBRATION_ROOT": self._tmp.name},
        )
        self._env_patch.start()
        self.store = PublishCalibrationStore()

    def tearDown(self) -> None:
        self._env_patch.stop()
        self._tmp.cleanup()

    def _create(self, **overrides: object) -> dict[str, object]:
        return self.store.create(PublishExperimentCreate(**_create_payload(**overrides)))

    def _publish(self, experiment_id: str) -> dict[str, object]:
        return self.store.publish(
            experiment_id,
            PublishRecordInput(**_publish_payload()),
        )

    def _review_with_impressions(
        self,
        actual: int,
        *,
        title: str,
    ) -> dict[str, object]:
        record = self._create(
            title=title,
            predictions=[{"key": "impressions", "low": 100, "high": 200}],
        )
        experiment_id = str(record["id"])
        self._publish(experiment_id)
        self.store.backfill(
            experiment_id,
            PublishBackfillInput(metrics={"impressions": actual}),
        )
        return self.store.review(experiment_id)

    def test_create_keeps_old_full_baseline_and_accepts_optional_advanced_fields(self) -> None:
        payload = PublishExperimentCreate(**_create_payload())
        first = self.store.create(payload)
        second = self.store.create(payload)

        self.assertEqual("predicted", first["status"])
        self.assertEqual(7, len(first["scores"]))
        self.assertEqual("dy_20260824T120000Z_1234abcd", first["source_topic_id"])
        self.assertEqual(3, len(first["predictions"]))
        self.assertEqual(DEFAULT_WINDOW_HOURS, first["prediction_window_hours"])
        self.assertEqual(first["content_snapshot_sha256"], second["content_snapshot_sha256"])
        self.assertEqual(64, len(str(first["content_snapshot_sha256"])))
        self.assertNotEqual(first["id"], second["id"])

        with closing(sqlite3.connect(self.store.database_path)) as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual("wal", str(journal_mode).lower())
        self.assertEqual(["created"], [event["event_type"] for event in self.store.list_events(str(first["id"]))])

        minimal = self.store.create(
            PublishExperimentCreate(
                title="只登记核心字段",
                platform="抖音",
                scores=[{"dimension": SCORE_DIMENSIONS[0], "score": 4}],
            )
        )
        self.assertEqual(1, len(minimal["scores"]))
        self.assertEqual([], minimal["predictions"])
        self.assertEqual(DEFAULT_WINDOW_HOURS, minimal["prediction_window_hours"])

    def test_full_loop_persists_metadata_events_and_immutable_prediction(self) -> None:
        created = self._create()
        experiment_id = str(created["id"])
        original_predictions = deepcopy(created["predictions"])
        original_snapshot = created["content_snapshot_sha256"]

        published = self.store.publish(
            experiment_id,
            PublishRecordInput(
                publish_url="https://v.douyin.com/x",
                platform="抖音企业号",
                publish_date="2026-08-22",
            ),
        )
        self.assertEqual("published", published["status"])
        self.assertEqual("抖音企业号", published["platform"])
        self.assertEqual("2026-08-22", published["publish_date"])
        self.assertTrue(str(published["published_at"]).startswith("2026-08-22T00:00:00"))

        measured = self.store.backfill(
            experiment_id,
            PublishBackfillInput(
                metrics={"impressions": 15000, "retention": 32.5},
                data_source="抖音创作者中心截图人工抄录",
                note="统计口径截至发布后 72 小时",
            ),
        )
        self.assertEqual("measured", measured["status"])
        self.assertEqual(DEFAULT_WINDOW_HOURS, measured["window_hours"])
        self.assertEqual(DEFAULT_WINDOW_HOURS, measured["prediction_window_hours"])
        self.assertTrue(measured["observed_at"])
        self.assertEqual("抖音创作者中心截图人工抄录", measured["data_source"])
        self.assertEqual("统计口径截至发布后 72 小时", measured["backfill_note"])
        self.assertEqual(
            {"impressions": "count", "retention": "percent"},
            measured["metric_units"],
        )

        reviewed = self.store.review(experiment_id)
        self.assertEqual("reviewed", reviewed["status"])
        self.assertEqual(original_predictions, reviewed["predictions"])
        self.assertEqual(original_snapshot, reviewed["content_snapshot_sha256"])
        self.assertEqual(1, reviewed["calibration_summary"]["sample_size"])
        self.assertTrue(reviewed["calibration_summary"]["evidence_insufficient"])

        events = self.store.list_events(experiment_id)
        self.assertEqual(
            ["created", "published", "measured", "reviewed"],
            [event["event_type"] for event in events],
        )
        self.assertEqual([1, 2, 3, 4], [event["record_version"] for event in events])

        reopened = PublishCalibrationStore()
        self.assertEqual(reviewed, reopened.get(experiment_id))
        self.assertEqual(4, len(reopened.list_events(experiment_id)))

    def test_strict_state_machine_rejects_out_of_order_and_repeat_operations(self) -> None:
        record = self._create()
        experiment_id = str(record["id"])
        backfill = PublishBackfillInput(metrics={"impressions": 12345})

        with self.assertRaises(PublishCalibrationConflictError) as context:
            self.store.backfill(experiment_id, backfill)
        self.assertEqual("predicted", context.exception.current_status)
        self.assertEqual("published", context.exception.expected_status)

        with self.assertRaises(PublishCalibrationConflictError):
            self.store.review(experiment_id)

        self._publish(experiment_id)
        with self.assertRaises(PublishCalibrationConflictError):
            self._publish(experiment_id)

        self.store.backfill(experiment_id, backfill)
        with self.assertRaises(PublishCalibrationConflictError):
            self.store.backfill(experiment_id, backfill)

        self.store.review(experiment_id)
        with self.assertRaises(PublishCalibrationConflictError):
            self.store.review(experiment_id)

        self.assertEqual(4, len(self.store.list_events(experiment_id)))

    def test_no_baseline_can_complete_loop_without_entering_calibration_statistics(self) -> None:
        created = self._create(scores=[], predictions=[])
        experiment_id = str(created["id"])
        self._publish(experiment_id)
        measured = self.store.backfill(
            experiment_id,
            PublishBackfillInput(metrics={"views": 321, "likes": 12}),
        )
        self.assertEqual({"views": 321.0, "likes": 12.0}, measured["actual_metrics"])

        reviewed = self.store.review(experiment_id)
        self.assertEqual("reviewed", reviewed["status"])
        self.assertEqual([], reviewed["deviations"])
        self.assertEqual([], reviewed["next_suggestions"])
        self.assertFalse(reviewed["learning_candidate"])
        self.assertIn("不计入命中率或偏差统计", reviewed["learning_note"])
        self.assertEqual(0, reviewed["calibration_summary"]["sample_size"])
        self.assertEqual(0, reviewed["calibration_summary"]["metric_observations"])
        self.assertEqual(
            ["created", "published", "measured", "reviewed"],
            [event["event_type"] for event in self.store.list_events(experiment_id)],
        )

    def test_create_validation_rejects_duplicate_or_invalid_advanced_inputs(self) -> None:
        invalid_payloads: dict[str, dict[str, object]] = {
            "duplicate_score": _create_payload(scores=[*_scores()[:-1], _scores()[0]]),
            "score_out_of_range": _create_payload(
                scores=[{**item, "score": 9} if index == 0 else item for index, item in enumerate(_scores())]
            ),
            "duplicate_prediction": _create_payload(
                predictions=[
                    {"key": "views", "low": 10, "high": 20},
                    {"key": "views", "low": 30, "high": 40},
                ]
            ),
            "unknown_prediction": _create_payload(
                predictions=[{"key": "exposure", "low": 10, "high": 20}]
            ),
            "nan_prediction": _create_payload(
                predictions=[{"key": "views", "low": float("nan"), "high": 20}]
            ),
            "infinite_prediction": _create_payload(
                predictions=[{"key": "views", "low": 10, "high": float("inf")}]
            ),
            "negative_prediction": _create_payload(
                predictions=[{"key": "views", "low": -1, "high": 20}]
            ),
            "fractional_count_prediction": _create_payload(
                predictions=[{"key": "views", "low": 1.5, "high": 20}]
            ),
            "retention_over_100": _create_payload(
                predictions=[{"key": "retention", "low": 20, "high": 101}]
            ),
            "boolean_prediction_window": _create_payload(window_hours=True),
            "zero_prediction_window": _create_payload(window_hours=0),
            "too_long_prediction_window": _create_payload(window_hours=24 * 365 + 1),
        }
        for name, payload in invalid_payloads.items():
            with self.subTest(name=name), self.assertRaises(ValidationError):
                PublishExperimentCreate(**payload)

    def test_backfill_validation_rejects_empty_unknown_nan_and_negative_values(self) -> None:
        invalid_inputs: dict[str, dict[str, object]] = {
            "empty": {"metrics": {}},
            "unknown": {"metrics": {"exposure": 1}},
            "nan": {"metrics": {"views": float("nan")}},
            "infinite": {"metrics": {"views": float("inf")}},
            "negative": {"metrics": {"views": -1}},
            "fractional_count": {"metrics": {"views": 1.5}},
            "retention_over_100": {"metrics": {"retention": 100.1}},
        }
        for name, payload in invalid_inputs.items():
            with self.subTest(name=name), self.assertRaises(ValidationError):
                PublishBackfillInput(**payload)

    def test_backfill_accepts_unpredicted_metrics_but_review_compares_only_baseline(self) -> None:
        record = self._create(
            predictions=[{"key": "impressions", "low": 100, "high": 200}]
        )
        experiment_id = str(record["id"])
        self._publish(experiment_id)
        measured = self.store.backfill(
            experiment_id,
            PublishBackfillInput(metrics={"impressions": 150, "views": 999}),
        )
        self.assertEqual({"impressions": 150.0, "views": 999.0}, measured["actual_metrics"])

        reviewed = self.store.review(experiment_id)
        self.assertEqual(["impressions"], [item["key"] for item in reviewed["deviations"]])
        self.assertEqual(1, reviewed["calibration_summary"]["metric_observations"])

    def test_backfill_requires_same_prediction_window_and_valid_time_order(self) -> None:
        record = self._create(
            window_hours=24,
            predictions=[{"key": "impressions", "low": 100, "high": 200}],
        )
        experiment_id = str(record["id"])
        self.store.publish(
            experiment_id,
            PublishRecordInput(
                publish_url="https://v.douyin.com/window-test",
                platform="抖音",
                published_at="2026-08-22T12:30:00+08:00",
            ),
        )

        with self.assertRaises(PublishCalibrationValidationError) as mismatch:
            self.store.backfill(
                experiment_id,
                PublishBackfillInput(
                    metrics={"impressions": 150},
                    window_hours=72,
                    observed_at="2026-08-25T12:30:00+08:00",
                ),
            )
        self.assertIn("与登记时观察窗 24 小时不一致", str(mismatch.exception))

        with self.assertRaises(PublishCalibrationValidationError) as early:
            self.store.backfill(
                experiment_id,
                PublishBackfillInput(
                    metrics={"impressions": 150},
                    window_hours=24,
                    observed_at="2026-08-22T12:29:59+08:00",
                ),
            )
        self.assertIn("不能早于", str(early.exception))
        self.assertEqual("published", self.store.get(experiment_id)["status"])
        self.assertEqual(2, len(self.store.list_events(experiment_id)))

        measured = self.store.backfill(
            experiment_id,
            PublishBackfillInput(
                metrics={"impressions": 150},
                window_hours=24,
                observed_at="2026-08-23T12:30:00+08:00",
                data_source="创作者中心 T+24h",
            ),
        )
        self.assertEqual(24, measured["prediction_window_hours"])
        self.assertEqual(24, measured["window_hours"])
        self.assertEqual("measured", measured["status"])

    def test_review_bias_semantics_cover_low_inside_and_high(self) -> None:
        low = self._review_with_impressions(50, title="偏低")
        inside = self._review_with_impressions(150, title="命中")
        high = self._review_with_impressions(250, title="偏高")

        low_deviation = low["deviations"][0]
        inside_deviation = inside["deviations"][0]
        high_deviation = high["deviations"][0]
        self.assertEqual("prediction_optimistic", low_deviation["bias"])
        self.assertIn("预测偏乐观", low_deviation["note"])
        self.assertEqual("within_interval", inside_deviation["bias"])
        self.assertEqual("prediction_conservative", high_deviation["bias"])
        self.assertIn("预测偏保守", high_deviation["note"])

        self.assertIn("不能据此", low["next_suggestions"][0]["rationale"])
        self.assertIn("只", low["next_suggestions"][0]["direction"])
        self.assertNotIn("说明钩子", low["next_suggestions"][0]["rationale"])

    def test_cross_experiment_summary_uses_reviewed_event_history(self) -> None:
        first = self._review_with_impressions(150, title="命中一")
        second = self._review_with_impressions(250, title="未命中")
        third = self._review_with_impressions(180, title="命中二")

        self.assertTrue(first["calibration_summary"]["evidence_insufficient"])
        self.assertTrue(second["calibration_summary"]["evidence_insufficient"])
        self.assertFalse(third["calibration_summary"]["evidence_insufficient"])

        summary = self.store.calibration_summary()
        self.assertEqual(3, summary["sample_size"])
        self.assertEqual(3, summary["metric_observations"])
        self.assertEqual(2, summary["interval_hits"])
        self.assertEqual(0.6667, summary["hit_rate"])
        self.assertFalse(summary["evidence_insufficient"])
        self.assertIn("不代表因果关系", summary["message"])

    def test_legacy_jsonl_is_migrated_once_without_modifying_source(self) -> None:
        legacy_record = {
            "id": "pub_legacy_001",
            "status": "reviewed",
            "created_at": "2026-08-20T00:00:00+08:00",
            "updated_at": "2026-08-21T00:00:00+08:00",
            "title": "旧账本记录",
            "deviations": [{"inside_interval": True, "key": "exposure"}],
        }
        ledger = Path(self._tmp.name) / "ledger.jsonl"
        original = (json.dumps(legacy_record, ensure_ascii=False) + "\n").encode("utf-8")
        ledger.write_bytes(original)
        original_sha256 = hashlib.sha256(original).hexdigest()

        migrated = PublishCalibrationStore()
        self.assertEqual([legacy_record], migrated.list_all())
        self.assertNotIn("prediction_window_hours", migrated.list_all()[0])
        self.assertEqual("legacy_reviewed", migrated.list_events()[0]["event_type"])
        self.assertEqual(1, migrated.calibration_summary()["sample_size"])
        self.assertEqual(original_sha256, hashlib.sha256(ledger.read_bytes()).hexdigest())

        reopened = PublishCalibrationStore()
        self.assertEqual(1, len(reopened.list_all()))
        self.assertEqual(1, len(reopened.list_events()))
        self.assertTrue(ledger.exists())
        self.assertEqual(original, ledger.read_bytes())

    def test_concurrent_creates_and_updates_do_not_lose_data(self) -> None:
        def create_one(index: int) -> str:
            # 每个线程使用独立 Store/连接，覆盖真实多请求或多进程入口的初始化竞争。
            record = PublishCalibrationStore().create(
                PublishExperimentCreate(**_create_payload(title=f"并发实验 {index}"))
            )
            return str(record["id"])

        with ThreadPoolExecutor(max_workers=8) as pool:
            experiment_ids = list(pool.map(create_one, range(20)))

        self.assertEqual(20, len(set(experiment_ids)))
        self.assertEqual(20, len(self.store.list_all()))
        self.assertEqual(20, len(self.store.list_events()))

        target = experiment_ids[0]

        def publish_once() -> str:
            return str(
                self.store.publish(
                    target,
                    PublishRecordInput(**_publish_payload()),
                )["status"]
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(publish_once) for _ in range(2)]
        outcomes: list[str] = []
        errors: list[Exception] = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except Exception as exc:  # noqa: BLE001 - deliberate concurrency assertion
                errors.append(exc)

        self.assertEqual(["published"], outcomes)
        self.assertEqual(1, len(errors))
        self.assertIsInstance(errors[0], PublishCalibrationConflictError)
        self.assertEqual(2, len(self.store.list_events(target)))

    def test_event_table_rejects_update_and_delete(self) -> None:
        record = self._create()
        experiment_id = str(record["id"])
        self.assertEqual(1, len(self.store.list_events(experiment_id)))

        with closing(sqlite3.connect(self.store.database_path)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE experiment_events SET event_type = 'tampered' WHERE experiment_id = ?",
                    (experiment_id,),
                )
        with closing(sqlite3.connect(self.store.database_path)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "DELETE FROM experiment_events WHERE experiment_id = ?",
                    (experiment_id,),
                )
        self.assertEqual("created", self.store.list_events(experiment_id)[0]["event_type"])

    def test_get_missing_raises(self) -> None:
        with self.assertRaises(PublishExperimentNotFoundError):
            self.store.get("pub_missing")


class PublishApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env_patch = patch.dict(
            os.environ,
            {"PROJECT024_PUBLISH_CALIBRATION_ROOT": self._tmp.name},
        )
        self._env_patch.start()
        self._store = PublishCalibrationStore()
        self._store_patch = patch.object(main_module, "publish_calibration", self._store)
        self._store_patch.start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        self._store_patch.stop()
        self._env_patch.stop()
        self._tmp.cleanup()

    def test_full_loop_via_existing_api_contract(self) -> None:
        create = self.client.post("/api/publish/experiments", json=_create_payload())
        self.assertEqual(201, create.status_code, create.text)
        created = create.json()
        experiment_id = created["id"]
        self.assertEqual("predicted", created["status"])
        self.assertEqual(DEFAULT_WINDOW_HOURS, created["prediction_window_hours"])

        created_events = self.client.get(
            f"/api/publish/experiments/{experiment_id}/events"
        )
        self.assertEqual(200, created_events.status_code, created_events.text)
        self.assertEqual(["created"], [item["event_type"] for item in created_events.json()["events"]])

        publish = self.client.post(
            f"/api/publish/experiments/{experiment_id}/publish",
            json={
                "publish_url": "https://v.douyin.com/x",
                "publish_date": "2026-08-22",
            },
        )
        self.assertEqual(200, publish.status_code, publish.text)
        self.assertEqual("published", publish.json()["status"])
        self.assertTrue(publish.json()["published_at"])

        backfill = self.client.post(
            f"/api/publish/experiments/{experiment_id}/backfill",
            json={
                "metrics": {"impressions": 15000, "retention": 30.5},
                "data_source": "创作者中心",
                "note": "T+72h",
            },
        )
        self.assertEqual(200, backfill.status_code, backfill.text)
        self.assertEqual(DEFAULT_WINDOW_HOURS, backfill.json()["window_hours"])

        review = self.client.post(
            f"/api/publish/experiments/{experiment_id}/review",
            json={"note": "人工复盘备注"},
        )
        self.assertEqual(200, review.status_code, review.text)
        body = review.json()
        self.assertEqual("reviewed", body["status"])
        self.assertTrue(body["deviations"])
        self.assertTrue(body["next_suggestions"])
        self.assertTrue(body["learning_candidate"])
        self.assertEqual("人工复盘备注", body["learning_note"])

        listed = self.client.get("/api/publish/experiments")
        self.assertEqual(200, listed.status_code)
        self.assertEqual(1, len(listed.json()["experiments"]))

        events = self.client.get(
            f"/api/publish/experiments/{experiment_id}/events"
        )
        self.assertEqual(200, events.status_code, events.text)
        self.assertEqual(
            ["created", "published", "measured", "reviewed"],
            [item["event_type"] for item in events.json()["events"]],
        )

        summary = self.client.get("/api/publish/calibration-summary")
        self.assertEqual(200, summary.status_code, summary.text)
        self.assertEqual(1, summary.json()["sample_size"])
        self.assertTrue(summary.json()["evidence_insufficient"])

    def test_api_input_validation_and_missing_record(self) -> None:
        bad = _create_payload(scores=[*_scores()[:-1], _scores()[0]])
        response = self.client.post("/api/publish/experiments", json=bad)
        self.assertEqual(422, response.status_code)

        unknown_metric = self.client.post(
            "/api/publish/experiments/pub_missing/backfill",
            json={"metrics": {"exposure": 1}},
        )
        self.assertEqual(422, unknown_metric.status_code)

        missing = self.client.post(
            "/api/publish/experiments/pub_missing/publish",
            json=_publish_payload(),
        )
        self.assertEqual(404, missing.status_code)

        missing_events = self.client.get(
            "/api/publish/experiments/pub_missing/events"
        )
        self.assertEqual(404, missing_events.status_code)

    def test_api_minimal_registration_can_backfill_real_metrics_and_review(self) -> None:
        create = self.client.post(
            "/api/publish/experiments",
            json={"title": "轻量登记", "platform": "抖音"},
        )
        self.assertEqual(201, create.status_code, create.text)
        created = create.json()
        self.assertEqual([], created["scores"])
        self.assertEqual([], created["predictions"])
        experiment_id = created["id"]

        publish = self.client.post(
            f"/api/publish/experiments/{experiment_id}/publish",
            json=_publish_payload(),
        )
        self.assertEqual(200, publish.status_code, publish.text)
        backfill = self.client.post(
            f"/api/publish/experiments/{experiment_id}/backfill",
            json={"metrics": {"views": 456, "comments": 8}},
        )
        self.assertEqual(200, backfill.status_code, backfill.text)
        review = self.client.post(
            f"/api/publish/experiments/{experiment_id}/review",
            json={},
        )
        self.assertEqual(200, review.status_code, review.text)
        self.assertEqual([], review.json()["deviations"])
        self.assertFalse(review.json()["learning_candidate"])

        summary = self.client.get("/api/publish/calibration-summary")
        self.assertEqual(0, summary.json()["sample_size"])
        self.assertEqual(0, summary.json()["metric_observations"])

    def test_api_maps_state_and_context_validation(self) -> None:
        created = self.client.post(
            "/api/publish/experiments", json=_create_payload()
        ).json()
        experiment_id = created["id"]

        too_early = self.client.post(
            f"/api/publish/experiments/{experiment_id}/backfill",
            json={"metrics": {"impressions": 100}},
        )
        self.assertEqual(409, too_early.status_code, too_early.text)

        published = self.client.post(
            f"/api/publish/experiments/{experiment_id}/publish",
            json=_publish_payload(),
        )
        self.assertEqual(200, published.status_code, published.text)

        unpredicted = self.client.post(
            f"/api/publish/experiments/{experiment_id}/backfill",
            json={"metrics": {"likes": 100}},
        )
        self.assertEqual(200, unpredicted.status_code, unpredicted.text)
        self.assertEqual({"likes": 100.0}, unpredicted.json()["actual_metrics"])

        review = self.client.post(
            f"/api/publish/experiments/{experiment_id}/review",
            json={},
        )
        self.assertEqual(200, review.status_code, review.text)
        self.assertEqual([], review.json()["deviations"])


if __name__ == "__main__":
    unittest.main()
