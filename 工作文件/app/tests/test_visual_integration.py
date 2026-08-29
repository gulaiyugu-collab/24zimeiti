from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app
from app.services.acquisition import AcquisitionJobManager, AcquisitionJobStore


class _NoPaidContentRouter:
    def __init__(self) -> None:
        self.quick_visual_evidence: dict[str, object] | None = None

    def plan(self) -> dict[str, object]:
        return {"configured": False, "paid_api_called": False}

    async def generate_quick(self, **kwargs: object) -> None:
        value = kwargs.get("visual_evidence")
        self.quick_visual_evidence = value if isinstance(value, dict) else None
        return None

    async def generate(self, **kwargs: object) -> None:
        return None


class _FakeVisualAnalyzer:
    ocr_completed = False
    vision_completed = False

    def analyze(
        self,
        media_path: Path,
        source_sha256: str,
        output_dir: Path,
        *,
        artifact_url_builder: object,
    ) -> dict[str, object]:
        self.media_path = media_path
        self.source_sha256 = source_sha256
        output_dir.mkdir(parents=True, exist_ok=True)
        frame_name = "visual_frame_00_000000500ms.jpg"
        (output_dir / frame_name).write_bytes(b"\xff\xd8visual-frame\xff\xd9")
        (output_dir / "visual_analysis.json").write_text("{}\n", encoding="utf-8")
        url_builder = artifact_url_builder
        return {
            "schema_version": "1.0",
            "status": "partial",
            "input_sha256": source_sha256,
            "cache_hit": False,
            "probe": {
                "duration_seconds": 6.0,
                "coverage_seconds": 6.0,
                "truncated": False,
                "width": 720,
                "height": 1280,
                "fps": 30.0,
            },
            "scene_structure": {
                "status": "completed",
                "method": "ffmpeg_scene_score_v1",
                "candidate_cut_count": 2,
                "estimated_segment_count": 3,
                "estimated_average_segment_seconds": 2.0,
                "cuts_per_minute": 20.0,
                "pace": "fast",
                "pace_is_heuristic": True,
                "cuts_truncated": False,
                "cuts": [
                    {"timestamp_seconds": 1.0, "score": 0.8},
                    {"timestamp_seconds": 3.0, "score": 0.7},
                ],
            },
            "frames": [
                {
                    "frame_id": frame_name,
                    "artifact_name": frame_name,
                    "artifact_url": url_builder(frame_name),
                    "timestamp_seconds": 0.5,
                    "reason": "coverage_anchor",
                    "sha256": "0" * 64,
                }
            ],
            "ocr": (
                {
                    "status": "completed",
                    "provider": "fixture_ocr",
                    "model_version": "fixture-v1",
                    "message": "已完成画面文字识别。",
                    "frame_count": 1,
                    "block_count": 1,
                    "blocks": [
                        {
                            "frame_id": frame_name,
                            "last_frame_id": frame_name,
                            "timestamp_seconds": 0.5,
                            "first_seen_seconds": 0.5,
                            "last_seen_seconds": 0.5,
                            "text": "真实画面文字",
                            "box": [[1.0, 1.0], [20.0, 1.0], [20.0, 10.0], [1.0, 10.0]],
                            "confidence": 0.92,
                            "provider": "fixture_ocr",
                            "model_version": "fixture-v1",
                            "frame_refs": [
                                {
                                    "frame_id": frame_name,
                                    "timestamp_seconds": 0.5,
                                    "box": [[1.0, 1.0], [20.0, 1.0], [20.0, 10.0], [1.0, 10.0]],
                                    "confidence": 0.92,
                                }
                            ],
                        }
                    ],
                    "limitations": ["fixture"],
                }
                if self.ocr_completed
                else {
                    "status": "unavailable",
                    "provider": None,
                    "reason_code": "engine_not_installed",
                    "message": "本机未安装本地 OCR 引擎，未生成画面文字。",
                }
            ),
            "vision": (
                {
                    "status": "completed",
                    "provider": "fixture_vision",
                    "model_version": "fixture-v1",
                    "message": "已完成 fixture 视觉分析。",
                    "frame_count": 1,
                    "successful_frame_count": 1,
                    "observation_count": 1,
                    "inference_count": 1,
                    "observations": [
                        {
                            "frame_id": frame_name,
                            "timestamp_seconds": 0.5,
                            "category": "scene",
                            "description": "室内书架场景",
                            "confidence": 0.91,
                            "provider": "fixture_vision",
                            "model_version": "fixture-v1",
                            "evidence_type": "visual_model",
                            "evidence_state": "observed",
                        }
                    ],
                    "possible_inferences": [
                        {
                            "frame_id": frame_name,
                            "timestamp_seconds": 0.5,
                            "category": "possible_inference",
                            "description": "可能正在进行讲解",
                            "confidence": 0.5,
                            "provider": "fixture_vision",
                            "model_version": "fixture-v1",
                            "evidence_type": "visual_model",
                            "evidence_state": "inferred",
                        }
                    ],
                    "limitations": ["fixture"],
                }
                if self.vision_completed
                else {
                    "status": "unavailable",
                    "provider": None,
                    "reason_code": "provider_not_configured",
                    "message": "本机尚未配置多模态画面语义模型。",
                    "observations": [],
                    "possible_inferences": [],
                    "limitations": [],
                }
            ),
        }


class VisualIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = AcquisitionJobStore(Path(self._tmp.name))
        self.manager = AcquisitionJobManager(store=self.store)
        self.content_router = _NoPaidContentRouter()
        self.visual_analyzer = _FakeVisualAnalyzer()
        self._patches = [
            patch.object(main_module, "acquisition_jobs", self.manager),
            patch.object(main_module, "content_router", self.content_router),
            patch.object(main_module, "visual_analyzer", self.visual_analyzer),
            patch.dict(
                os.environ,
                {
                    "DEEPSEEK_API_KEY": "",
                    "PROJECT024_CONTENT_API_KEY": "",
                    "PROJECT024_ALLOW_PAID_API": "0",
                },
            ),
        ]
        for item in self._patches:
            item.start()
        self.client = TestClient(app)
        self.job_id = self._make_completed_job()

    def tearDown(self) -> None:
        self.client.close()
        for item in reversed(self._patches):
            item.stop()
        self._tmp.cleanup()

    def _make_completed_job(self) -> str:
        status = self.store.create_job(
            {
                "url": "https://www.douyin.com/video/7999999999999999999",
                "submitted_url": "https://www.douyin.com/video/7999999999999999999",
                "platform": "douyin",
                "item_limit": 1,
                "cache_key": "visual-integration-test",
                "stable_id": "7999999999999999999",
                "link_verified": True,
            }
        )
        job_id = str(status["job_id"])
        raw_dir = self.store.job_dir(job_id) / "raw"
        media_path = raw_dir / "douyin_source.mp4"
        media_path.write_bytes(b"verified-video-payload")
        media = self.store.register_raw_file(
            job_id,
            media_path,
            role="source_media",
            content_type="video/mp4",
        )
        transcript_payload = {
            "text": "这是一段用于验证抽帧和候选镜头结构接线的字幕。",
            "source": "local_asr",
            "provider": "faster-whisper",
            "segment_count": 1,
            "segments": [
                {
                    "id": 1,
                    "start": 0.0,
                    "end": 6.0,
                    "text": "这是一段用于验证抽帧和候选镜头结构接线的字幕。",
                }
            ],
        }
        transcript = self.store.write_raw_artifact(
            job_id, "transcript.json", transcript_payload
        )
        source = self.store.write_raw_artifact(
            job_id,
            "source.json",
            {"url": "https://www.douyin.com/video/7999999999999999999"},
        )
        artifacts = [media, transcript, source]
        manifest = {
            "schema_version": "1.0",
            "job_id": job_id,
            "status": "completed",
            "platform": "douyin",
            "canonical_url": "https://www.douyin.com/video/7999999999999999999",
            "stable_id": "7999999999999999999",
            "acquisition_mode": "public_media_and_local_asr",
            "analysis_ready": True,
            "evidence_summary": {
                "missing": ["画面 OCR 与镜头结构分析", "公开评论采集"]
            },
            "items": [
                {
                    "platform": "douyin",
                    "author": {},
                    "metrics": {},
                    "evidence": [],
                    "content": {"transcript": transcript_payload},
                }
            ],
            "raw_artifacts": artifacts,
            "completed_at": "2026-08-22T10:00:00Z",
        }
        self.store.write_manifest(job_id, manifest)
        self.store.patch_status(
            job_id,
            status="completed",
            message="测试任务已完成。",
            manifest_url=f"/api/acquisition/jobs/{job_id}/manifest",
            artifacts=artifacts,
            missing=["画面 OCR 与镜头结构分析", "公开评论采集"],
        )
        return job_id

    def test_visual_api_uses_verified_media_and_serves_inline_frame(self) -> None:
        response = self.client.post(
            f"/api/acquisition/jobs/{self.job_id}/visual-analysis"
        )
        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertEqual("partial", body["status"])
        self.assertEqual("completed", body["scene_structure"]["status"])
        self.assertEqual("unavailable", body["ocr"]["status"])
        self.assertEqual(1, body["frame_count"])
        self.assertNotIn("input_sha256", body)
        self.assertNotIn(str(Path(self._tmp.name)), json.dumps(body, ensure_ascii=False))

        expected_hash = hashlib.sha256(b"verified-video-payload").hexdigest()
        self.assertEqual(expected_hash, self.visual_analyzer.source_sha256)
        frame = self.client.get(body["frames"][0]["artifact_url"])
        self.assertEqual(200, frame.status_code, frame.text)
        self.assertEqual("image/jpeg", frame.headers["content-type"])
        self.assertTrue(frame.content.startswith(b"\xff\xd8"))

    def test_acquisition_analysis_attaches_visual_evidence_without_overclaim(self) -> None:
        response = self.client.post(
            f"/api/acquisition/jobs/{self.job_id}/analyze",
            json={"analysis_mode": "quick"},
        )
        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        visual = body["report"]["visual_analysis"]
        self.assertEqual("completed", visual["scene_structure"]["status"])
        self.assertEqual("unavailable", visual["ocr"]["status"])
        self.assertTrue(visual["scene_structure"]["pace_is_heuristic"])
        self.assertNotIn("input_sha256", visual)
        evidence_types = {
            item.get("type")
            for item in body["report"]["source"].get("evidence", [])
            if isinstance(item, dict)
        }
        self.assertIn("frame_and_shot_structure", evidence_types)
        optional = body["report"]["requirements"]["optional_enhancements"]
        self.assertIn("画面文字 OCR（本机未安装 OCR 引擎）", optional)
        self.assertNotIn("画面 OCR 与镜头结构分析", optional)
        self.assertIsNotNone(self.content_router.quick_visual_evidence)
        self.assertEqual(
            "completed",
            self.content_router.quick_visual_evidence["scene_structure"]["status"],
        )

    def test_completed_ocr_is_public_and_removes_uninstalled_requirement(self) -> None:
        self.visual_analyzer.ocr_completed = True
        response = self.client.post(
            f"/api/acquisition/jobs/{self.job_id}/analyze",
            json={"analysis_mode": "quick"},
        )

        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        visual = body["report"]["visual_analysis"]
        self.assertEqual("completed", visual["ocr"]["status"])
        self.assertEqual("真实画面文字", visual["ocr"]["blocks"][0]["text"])
        optional = body["report"]["requirements"]["optional_enhancements"]
        self.assertFalse(any("ocr" in str(item).lower() for item in optional))
        evidence_types = {
            item.get("type")
            for item in body["report"]["source"].get("evidence", [])
            if isinstance(item, dict)
        }
        self.assertIn("on_screen_text_ocr", evidence_types)
        self.assertNotIn(str(Path(self._tmp.name)), json.dumps(body, ensure_ascii=False))

    def test_completed_vision_is_public_and_timeline_links_to_frame(self) -> None:
        self.visual_analyzer.vision_completed = True
        visual_response = self.client.post(
            f"/api/acquisition/jobs/{self.job_id}/visual-analysis"
        )

        self.assertEqual(200, visual_response.status_code, visual_response.text)
        visual = visual_response.json()["vision"]
        self.assertEqual("completed", visual["status"])
        self.assertEqual("observed", visual["observations"][0]["evidence_state"])
        self.assertTrue(
            visual["observations"][0]["artifact_url"].startswith(
                f"/api/acquisition/jobs/{self.job_id}/"
            )
        )

        timeline_response = self.client.get(
            f"/api/acquisition/jobs/{self.job_id}/full-content/timeline?offset=0&limit=10"
        )
        self.assertEqual(200, timeline_response.status_code, timeline_response.text)
        item = timeline_response.json()["items"][0]
        self.assertEqual(["室内书架场景"], item["visual_observations"])
        self.assertEqual(["可能正在进行讲解"], item["visual_inferences"])
        self.assertTrue(
            item["visual_evidence"][0]["artifact_url"].startswith(
                f"/api/acquisition/jobs/{self.job_id}/"
            )
        )
        self.assertNotIn(
            str(Path(self._tmp.name)),
            json.dumps(timeline_response.json(), ensure_ascii=False),
        )

    def test_visual_artifact_route_rejects_unlisted_names(self) -> None:
        response = self.client.get(
            f"/api/acquisition/jobs/{self.job_id}/visual-analysis/artifacts/../status.json"
        )
        self.assertEqual(404, response.status_code)

    def test_full_content_apis_use_registered_transcript_and_paginate(self) -> None:
        transcript = self.client.get(
            f"/api/acquisition/jobs/{self.job_id}/full-content/transcript?offset=0&limit=1"
        )
        self.assertEqual(200, transcript.status_code, transcript.text)
        transcript_body = transcript.json()
        self.assertEqual(1, transcript_body["segment_count"])
        self.assertEqual(1, len(transcript_body["items"]))
        self.assertEqual(
            "这是一段用于验证抽帧和候选镜头结构接线的字幕。",
            transcript_body["items"][0]["text"],
        )

        full_text = self.client.get(
            f"/api/acquisition/jobs/{self.job_id}/full-content/transcript-text"
        )
        self.assertEqual(200, full_text.status_code, full_text.text)
        self.assertEqual(
            transcript_body["character_count"], full_text.json()["character_count"]
        )

        self.visual_analyzer.ocr_completed = True
        ocr = self.client.get(
            f"/api/acquisition/jobs/{self.job_id}/full-content/ocr?offset=0&limit=1"
        )
        self.assertEqual(200, ocr.status_code, ocr.text)
        self.assertEqual("真实画面文字", ocr.json()["items"][0]["text"])

        timeline = self.client.get(
            f"/api/acquisition/jobs/{self.job_id}/full-content/timeline?offset=0&limit=1"
        )
        self.assertEqual(200, timeline.status_code, timeline.text)
        item = timeline.json()["items"][0]
        self.assertEqual([], item["visual_observations"])
        self.assertIn("真实画面文字", item["on_screen_text"])
        self.assertNotIn(
            str(Path(self._tmp.name)),
            json.dumps(timeline.json(), ensure_ascii=False),
        )

    def test_visual_api_skips_media_declared_over_512_mib(self) -> None:
        manifest = self.store.manifest(self.job_id)
        source_media = next(
            item
            for item in manifest["raw_artifacts"]
            if item.get("role") == "source_media"
        )
        source_media["size_bytes"] = 512 * 1024 * 1024 + 1
        self.store.write_manifest(self.job_id, manifest)

        response = self.client.post(
            f"/api/acquisition/jobs/{self.job_id}/visual-analysis"
        )

        self.assertEqual(200, response.status_code, response.text)
        body = response.json()
        self.assertEqual("unavailable", body["status"])
        self.assertEqual(
            "source_media_too_large",
            body["scene_structure"]["reason_code"],
        )
        self.assertFalse(hasattr(self.visual_analyzer, "media_path"))


if __name__ == "__main__":
    unittest.main()
