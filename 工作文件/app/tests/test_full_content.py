from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.services.full_content import (
    FullContentError,
    build_timeline,
    paginated_response,
    read_verified_transcript,
)


class FullContentTests(unittest.TestCase):
    def test_verified_transcript_reads_full_file_and_rejects_tampering(self) -> None:
        payload = {
            "text": "第一段。第二段。",
            "source": "local_asr",
            "segments": [
                {"id": 1, "start": 0, "end": 1.2, "text": "第一段。"},
                {"id": 2, "start": 1.2, "end": 2.4, "text": "第二段。"},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "transcript.json"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            expected_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            result = read_verified_transcript(
                path,
                expected_sha256=expected_hash,
                expected_size_bytes=path.stat().st_size,
            )
            self.assertEqual(payload["text"], result["text"])
            self.assertEqual(8, result["character_count"])
            self.assertEqual(2, result["segment_count"])

            path.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(FullContentError, "大小.*不一致|校验失败"):
                read_verified_transcript(
                    path,
                    expected_sha256=expected_hash,
                    expected_size_bytes=len(json.dumps(payload).encode("utf-8")),
                )

    def test_pagination_is_bounded(self) -> None:
        items = [{"id": index} for index in range(205)]
        page = paginated_response("transcript", items, offset=100, limit=100)
        self.assertEqual(205, page["total_items"])
        self.assertEqual(100, len(page["items"]))
        self.assertTrue(page["has_more"])
        with self.assertRaisesRegex(FullContentError, "limit"):
            paginated_response("transcript", items, offset=0, limit=101)

    def test_timeline_aligns_ocr_without_inventing_visual_observations(self) -> None:
        transcript = {
            "segments": [
                {"id": "1", "start": 0.0, "end": 2.0, "text": "第一句"},
                {"id": "2", "start": 2.1, "end": 4.0, "text": "第二句"},
            ]
        }
        visual = {
            "frames": [
                {
                    "frame_id": "visual_frame_00_000001000ms.jpg",
                    "artifact_url": "/api/acquisition/jobs/job/visual-analysis/artifacts/visual_frame_00_000001000ms.jpg",
                }
            ],
            "ocr": {
                "status": "completed",
                "provider": "fixture",
                "model_version": "v1",
                "blocks": [
                    {
                        "frame_id": "visual_frame_00_000001000ms.jpg",
                        "last_frame_id": "visual_frame_00_000001000ms.jpg",
                        "timestamp_seconds": 1.0,
                        "first_seen_seconds": 1.0,
                        "last_seen_seconds": 2.5,
                        "text": "画面标题",
                        "box": [[0, 0], [10, 0], [10, 5], [0, 5]],
                        "confidence": 0.9,
                    }
                ],
            },
        }
        timeline = build_timeline(transcript, visual)
        self.assertEqual(["画面标题"], timeline[0]["on_screen_text"])
        self.assertEqual(["画面标题"], timeline[1]["on_screen_text"])
        self.assertEqual([], timeline[0]["visual_observations"])
        self.assertIn("frame:visual_frame_00_000001000ms.jpg", timeline[0]["evidence_refs"])

    def test_timeline_keeps_observations_and_inferences_separate_with_frame_url(self) -> None:
        frame_id = "visual_frame_00_000001000ms.jpg"
        artifact_url = (
            "/api/acquisition/jobs/job/visual-analysis/artifacts/"
            "visual_frame_00_000001000ms.jpg"
        )
        transcript = {
            "segments": [
                {"id": "1", "start": 0.0, "end": 2.0, "text": "第一句"}
            ]
        }
        common = {
            "frame_id": frame_id,
            "timestamp_seconds": 1.0,
            "confidence": 0.9,
            "provider": "fixture_vision",
            "model_version": "fixture-v1",
        }
        visual = {
            "frames": [{"frame_id": frame_id, "artifact_url": artifact_url}],
            "ocr": {"status": "unavailable"},
            "vision": {
                "status": "completed",
                "provider": "fixture_vision",
                "model_version": "fixture-v1",
                "observations": [
                    {
                        **common,
                        "category": "scene",
                        "description": "室内书架场景",
                    }
                ],
                "possible_inferences": [
                    {
                        **common,
                        "category": "possible_inference",
                        "description": "可能正在进行讲解",
                    }
                ],
            },
        }

        item = build_timeline(transcript, visual)[0]

        self.assertEqual(["室内书架场景"], item["visual_observations"])
        self.assertEqual(["可能正在进行讲解"], item["visual_inferences"])
        self.assertEqual(artifact_url, item["visual_evidence"][0]["artifact_url"])
        self.assertEqual("observed", item["visual_evidence"][0]["evidence_state"])
        self.assertIn(f"frame:{frame_id}", item["evidence_refs"])


if __name__ == "__main__":
    unittest.main()
