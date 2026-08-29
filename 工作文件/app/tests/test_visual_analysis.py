from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.services.visual_analysis import (
    CommandOutcome,
    LocalOCRProvider,
    LocalOllamaVisionProvider,
    VisualAnalysisConfig,
    VisualAnalysisError,
    VisualAnalyzer,
)


SOURCE_SHA256 = "a" * 64


class FakeCommandRunner:
    def __init__(
        self,
        *,
        duration_seconds: float = 22.1,
        scene_output: str | None = None,
        probe_stdout: str | None = None,
        empty_frame: bool = False,
        timeout_stage: str | None = None,
    ) -> None:
        self.duration_seconds = duration_seconds
        self.scene_output = scene_output if scene_output is not None else ""
        self.probe_stdout = probe_stdout
        self.empty_frame = empty_frame
        self.timeout_stage = timeout_stage
        self.calls: list[tuple[list[str], Path, int]] = []

    def __call__(
        self, command: list[str], cwd: Path, timeout_seconds: int
    ) -> CommandOutcome:
        self.calls.append((list(command), cwd, timeout_seconds))
        if "-show_entries" in command:
            if self.timeout_stage == "probe":
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            payload = {
                "streams": [
                    {
                        "codec_name": "h264",
                        "width": 720,
                        "height": 1280,
                        "avg_frame_rate": "30/1",
                        "duration": str(self.duration_seconds),
                    }
                ],
                "format": {
                    "duration": str(self.duration_seconds),
                    "size": "4627697",
                },
            }
            stdout = (
                self.probe_stdout
                if self.probe_stdout is not None
                else json.dumps(payload)
            )
            return CommandOutcome(returncode=0, stdout=stdout, stderr="")

        if "null" in command:
            if self.timeout_stage == "scene":
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            return CommandOutcome(returncode=0, stdout="", stderr=self.scene_output)

        if "-frames:v" in command:
            if self.timeout_stage == "frame":
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            destination = Path(command[-1])
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"" if self.empty_frame else b"\xff\xd8mock-jpeg\xff\xd9")
            return CommandOutcome(returncode=0, stdout="", stderr="")

        raise AssertionError(f"Unexpected command: {command}")


def write_media(path: Path) -> None:
    path.write_bytes(b"caller-verified-source-media")


class CompletedOCRProvider:
    name = "fixture_ocr"
    version = "fixture-v1"
    config = {"text_score": 0.5}

    def __init__(self) -> None:
        self.calls = 0

    def analyze(
        self,
        frames: list[dict[str, object]],
        frame_root: Path,
        *,
        timeout_seconds: int,
    ) -> dict[str, object]:
        del frame_root, timeout_seconds
        self.calls += 1
        frame = frames[0]
        frame_id = str(frame["frame_id"])
        timestamp = float(frame["timestamp_seconds"])
        box = [[1.0, 2.0], [20.0, 2.0], [20.0, 12.0], [1.0, 12.0]]
        reference = {
            "frame_id": frame_id,
            "timestamp_seconds": timestamp,
            "box": box,
            "confidence": 0.91,
        }
        return {
            "status": "completed",
            "provider": self.name,
            "model_version": self.version,
            "message": "fixture completed",
            "frame_count": len(frames),
            "block_count": 1,
            "blocks": [
                {
                    "frame_id": frame_id,
                    "last_frame_id": frame_id,
                    "timestamp_seconds": timestamp,
                    "first_seen_seconds": timestamp,
                    "last_seen_seconds": timestamp,
                    "text": "画面文字",
                    "box": box,
                    "confidence": 0.91,
                    "provider": self.name,
                    "model_version": self.version,
                    "frame_refs": [reference],
                }
            ],
            "limitations": [],
        }


class CompletedVisionProvider:
    name = "fixture_vision"
    version = "fixture-v1"
    config = {"max_frames": 1}

    def __init__(self) -> None:
        self.calls = 0

    def analyze(
        self,
        frames: list[dict[str, object]],
        frame_root: Path,
        *,
        ocr: dict[str, object],
        timeout_seconds: int,
    ) -> dict[str, object]:
        del frame_root, ocr, timeout_seconds
        self.calls += 1
        frame = frames[0]
        frame_id = str(frame["frame_id"])
        timestamp = float(frame["timestamp_seconds"])
        observation = {
            "frame_id": frame_id,
            "timestamp_seconds": timestamp,
            "category": "scene",
            "description": "室内书架场景",
            "confidence": 0.91,
            "provider": self.name,
            "model_version": self.version,
            "evidence_type": "visual_model",
            "evidence_state": "observed",
        }
        return {
            "status": "completed",
            "provider": self.name,
            "model_version": self.version,
            "message": "fixture completed",
            "frame_count": 1,
            "successful_frame_count": 1,
            "observation_count": 1,
            "inference_count": 0,
            "observations": [observation],
            "possible_inferences": [],
            "frame_results": [
                {
                    "frame_id": frame_id,
                    "timestamp_seconds": timestamp,
                    "status": "completed",
                    "observation_count": 1,
                    "inference_count": 0,
                }
            ],
            "limitations": ["fixture"],
        }


class VisualAnalysisTests(unittest.TestCase):
    def test_scene_candidates_are_merged_and_report_contains_no_local_paths(self) -> None:
        scene_output = "\n".join(
            [
                "[metadata] frame:0 pts:5 pts_time:1.000",
                "[metadata] lavfi.scene_score=0.400000",
                "[metadata] frame:1 pts:7 pts_time:1.400",
                "[metadata] lavfi.scene_score=0.800000",
                "[metadata] frame:2 pts:20 pts_time:4.000",
                "[metadata] lavfi.scene_score=0.500000",
            ]
        )
        runner = FakeCommandRunner(scene_output=scene_output)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_path = root / "source.mp4"
            output_dir = root / "raw"
            write_media(media_path)
            analyzer = VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
            )

            result = analyzer.analyze(
                media_path,
                source_sha256=SOURCE_SHA256,
                output_dir=output_dir,
                artifact_url_builder=lambda name: f"/api/artifacts/{name}",
            )

            self.assertEqual("partial", result["status"])
            self.assertEqual("completed", result["scene_structure"]["status"])
            self.assertEqual(2, result["scene_structure"]["candidate_cut_count"])
            self.assertEqual(
                [1.4, 4.0],
                [item["timestamp_seconds"] for item in result["scene_structure"]["cuts"]],
            )
            self.assertEqual("unavailable", result["ocr"]["status"])
            self.assertLessEqual(len(result["frames"]), 12)
            self.assertTrue(result["frames"])
            self.assertTrue(
                all(item["artifact_url"].startswith("/api/artifacts/") for item in result["frames"])
            )
            self.assertEqual(
                "/api/artifacts/visual_analysis.json",
                result["report_artifact_url"],
            )
            serialized = json.dumps(result, ensure_ascii=False)
            self.assertNotIn(str(root), serialized)
            self.assertNotIn(str(media_path), serialized)

            stored = json.loads(
                (output_dir / "visual_analysis.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("report_artifact_url", stored)
            self.assertTrue(all("artifact_url" not in item for item in stored["frames"]))

    def test_no_scene_candidates_falls_back_to_uniform_frames(self) -> None:
        runner = FakeCommandRunner(duration_seconds=20.0, scene_output="")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_path = root / "source.mp4"
            write_media(media_path)
            result = VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
            ).analyze(media_path, SOURCE_SHA256, root / "raw")

        self.assertEqual(0, result["scene_structure"]["candidate_cut_count"])
        self.assertEqual(12, len(result["frames"]))
        self.assertTrue(
            all(item["reason"] in {"coverage_anchor", "uniform"} for item in result["frames"])
        )

    def test_analysis_is_limited_to_first_1200_seconds(self) -> None:
        runner = FakeCommandRunner(duration_seconds=1500.0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_path = root / "source.mp4"
            write_media(media_path)
            result = VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
            ).analyze(media_path, SOURCE_SHA256, root / "raw")

        self.assertEqual(1200.0, result["probe"]["coverage_seconds"])
        self.assertTrue(result["probe"]["truncated"])
        scene_command = next(call[0] for call in runner.calls if "null" in call[0])
        self.assertEqual("1200", scene_command[scene_command.index("-t") + 1])
        filter_value = scene_command[scene_command.index("-vf") + 1]
        self.assertIn("fps=5", filter_value)
        self.assertIn("gt(scene,0.30)", filter_value)

    def test_valid_report_is_reused_and_tampered_frame_invalidates_cache(self) -> None:
        runner = FakeCommandRunner(duration_seconds=10.0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_path = root / "source.mp4"
            output_dir = root / "raw"
            write_media(media_path)
            analyzer = VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
            )

            first = analyzer.analyze(media_path, SOURCE_SHA256, output_dir)
            calls_after_first = len(runner.calls)
            second = analyzer.analyze(media_path, SOURCE_SHA256, output_dir)
            self.assertFalse(first["cache_hit"])
            self.assertTrue(second["cache_hit"])
            self.assertEqual(calls_after_first, len(runner.calls))

            frame_path = output_dir / first["frames"][0]["artifact_name"]
            frame_path.write_bytes(b"tampered")
            third = analyzer.analyze(media_path, SOURCE_SHA256, output_dir)
            self.assertFalse(third["cache_hit"])
            self.assertGreater(len(runner.calls), calls_after_first)

    def test_config_change_invalidates_cache(self) -> None:
        runner = FakeCommandRunner(duration_seconds=10.0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_path = root / "source.mp4"
            output_dir = root / "raw"
            write_media(media_path)
            VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
            ).analyze(media_path, SOURCE_SHA256, output_dir)
            calls_after_first = len(runner.calls)

            result = VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
                config=VisualAnalysisConfig(scene_threshold=0.4),
            ).analyze(media_path, SOURCE_SHA256, output_dir)

        self.assertFalse(result["cache_hit"])
        self.assertGreater(len(runner.calls), calls_after_first)

    def test_ocr_provider_change_invalidates_unavailable_cache_and_completed_cache_is_strict(self) -> None:
        runner = FakeCommandRunner(duration_seconds=2.0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_path = root / "source.mp4"
            output_dir = root / "raw"
            write_media(media_path)
            VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
            ).analyze(media_path, SOURCE_SHA256, output_dir)
            calls_after_unavailable = len(runner.calls)

            provider = CompletedOCRProvider()
            analyzer = VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
                ocr_provider=provider,
            )
            first = analyzer.analyze(media_path, SOURCE_SHA256, output_dir)
            second = analyzer.analyze(media_path, SOURCE_SHA256, output_dir)

            self.assertFalse(first["cache_hit"])
            self.assertEqual("completed", first["ocr"]["status"])
            self.assertEqual("completed", first["capabilities"]["ocr"])
            self.assertGreater(len(runner.calls), calls_after_unavailable)
            self.assertTrue(second["cache_hit"])
            self.assertEqual(1, provider.calls)

            report_path = output_dir / "visual_analysis.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["ocr"]["blocks"][0]["confidence"] = 2.0
            report_path.write_text(
                json.dumps(report, ensure_ascii=False), encoding="utf-8"
            )
            third = analyzer.analyze(media_path, SOURCE_SHA256, output_dir)
            self.assertFalse(third["cache_hit"])
            self.assertEqual(2, provider.calls)

    def test_local_ocr_provider_deduplicates_adjacent_text_without_paths(self) -> None:
        captured: dict[str, object] = {}

        def process_runner(
            command: list[str], cwd: Path, timeout_seconds: int, request_json: str
        ) -> CommandOutcome:
            captured.update(
                command=command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                request=json.loads(request_json),
            )
            payload = {
                "schema_version": "project024-ocr-worker/v1",
                "status": "completed",
                "provider": "rapidocr_local",
                "model_version": "rapidocr-3.9.2-ppocrv6-small-v1",
                "frames": [
                    {
                        "frame_id": "visual_frame_00_000000500ms.jpg",
                        "timestamp_seconds": 0.5,
                        "blocks": [
                            {
                                "text": "字幕Ａ",
                                "box": [[1.0, 1.0], [9.0, 1.0], [9.0, 5.0], [1.0, 5.0]],
                                "confidence": 0.94,
                            }
                        ],
                    },
                    {
                        "frame_id": "visual_frame_01_000001500ms.jpg",
                        "timestamp_seconds": 1.5,
                        "blocks": [
                            {
                                "text": "字幕A",
                                "box": [[2.0, 2.0], [10.0, 2.0], [10.0, 6.0], [2.0, 6.0]],
                                "confidence": 0.88,
                            }
                        ],
                    },
                ],
            }
            return CommandOutcome(returncode=0, stdout=json.dumps(payload), stderr="")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            python_executable = root / "python.exe"
            worker_script = root / "ocr_worker.py"
            python_executable.write_bytes(b"python")
            worker_script.write_text("# fixture\n", encoding="utf-8")
            frames = []
            for index, timestamp in enumerate((0.5, 1.5)):
                artifact_name = f"visual_frame_{index:02d}_{int(timestamp * 1000):09d}ms.jpg"
                frame_path = root / artifact_name
                frame_path.write_bytes(f"frame-{index}".encode("ascii"))
                frames.append(
                    {
                        "frame_id": artifact_name,
                        "artifact_name": artifact_name,
                        "timestamp_seconds": timestamp,
                        "sha256": hashlib.sha256(frame_path.read_bytes()).hexdigest(),
                    }
                )
            provider = LocalOCRProvider(
                python_executable=python_executable,
                worker_script=worker_script,
                process_runner=process_runner,
            )
            result = provider.analyze(frames, root, timeout_seconds=12)

            self.assertEqual("completed", result["status"])
            self.assertEqual(1, result["block_count"])
            block = result["blocks"][0]
            self.assertEqual(0.5, block["first_seen_seconds"])
            self.assertEqual(1.5, block["last_seen_seconds"])
            self.assertEqual(0.88, block["confidence"])
            self.assertEqual(2, len(block["frame_refs"]))
            self.assertEqual(12, captured["timeout_seconds"])
            self.assertEqual(["-X", "utf8"], captured["command"][1:3])
            self.assertNotIn(str(root), json.dumps(result, ensure_ascii=False))

    def test_local_ollama_provider_uses_bounded_frames_and_separates_inference(self) -> None:
        captured: list[dict[str, object]] = []

        def request_runner(
            url: str, payload: dict[str, object], timeout_seconds: int
        ) -> dict[str, object]:
            captured.append(
                {"url": url, "payload": payload, "timeout_seconds": timeout_seconds}
            )
            content = {
                "person": {"description": "画面标题文字", "confidence": 0.99},
                "objects": {"description": "一台电脑屏幕", "confidence": 0.92},
                "action": {
                    "description": "人物似乎正在讲解",
                    "confidence": 0.88,
                },
                "scene": {"description": "室内书架场景", "confidence": 0.9},
                "composition": {
                    "description": "人物位于画面中央",
                    "confidence": 0.93,
                },
                "product_display": {
                    "description": "没有显示任何产品",
                    "confidence": 0.9,
                },
                "limitation": "ignored",
            }
            return {"message": {"content": json.dumps(content, ensure_ascii=False)}}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frames: list[dict[str, object]] = []
            for index in range(5):
                artifact_name = f"visual_frame_{index:02d}_{index * 1000:09d}ms.jpg"
                frame_path = root / artifact_name
                frame_path.write_bytes(f"frame-{index}".encode("ascii"))
                frames.append(
                    {
                        "frame_id": artifact_name,
                        "artifact_name": artifact_name,
                        "timestamp_seconds": float(index),
                        "sha256": hashlib.sha256(frame_path.read_bytes()).hexdigest(),
                    }
                )
            provider = LocalOllamaVisionProvider(
                max_frames=2,
                request_runner=request_runner,
            )
            result = provider.analyze(
                frames,
                root,
                ocr={
                    "blocks": [
                        {
                            "frame_id": frames[0]["frame_id"],
                            "text": "画面标题文字",
                            "frame_refs": [
                                {"frame_id": frames[0]["frame_id"]},
                                {"frame_id": frames[-1]["frame_id"]},
                            ],
                        }
                    ]
                },
                timeout_seconds=30,
            )

        self.assertEqual("completed", result["status"])
        self.assertEqual(2, result["frame_count"])
        self.assertEqual(2, len(captured))
        self.assertTrue(all(item["url"].endswith("/api/chat") for item in captured))
        self.assertFalse(
            any(item["description"] == "画面标题文字" for item in result["observations"])
        )
        self.assertFalse(
            any("没有显示" in item["description"] for item in result["observations"])
        )
        self.assertTrue(
            any(item["evidence_state"] == "inferred" for item in result["possible_inferences"])
        )
        self.assertNotIn(str(root), json.dumps(result, ensure_ascii=False))

    def test_vision_provider_change_invalidates_unavailable_cache(self) -> None:
        runner = FakeCommandRunner(duration_seconds=2.0)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_path = root / "source.mp4"
            output_dir = root / "raw"
            write_media(media_path)
            VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
            ).analyze(media_path, SOURCE_SHA256, output_dir)
            calls_after_unavailable = len(runner.calls)

            provider = CompletedVisionProvider()
            analyzer = VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
                vision_provider=provider,
            )
            first = analyzer.analyze(media_path, SOURCE_SHA256, output_dir)
            second = analyzer.analyze(media_path, SOURCE_SHA256, output_dir)

        self.assertFalse(first["cache_hit"])
        self.assertEqual("completed", first["vision"]["status"])
        self.assertEqual("completed", first["capabilities"]["vision"])
        self.assertGreater(len(runner.calls), calls_after_unavailable)
        self.assertTrue(second["cache_hit"])
        self.assertEqual(1, provider.calls)

    def test_local_ocr_provider_does_not_merge_distant_sampled_frames(self) -> None:
        provider = object.__new__(LocalOCRProvider)
        provider.name = "rapidocr_local"
        provider.version = "fixture-v1"
        frames = [
            {
                "frame_id": "frame_00.jpg",
                "timestamp_seconds": 1.0,
                "blocks": [{"text": "固定标题", "box": [[0, 0], [2, 0], [2, 1], [0, 1]], "confidence": 0.9}],
            },
            {
                "frame_id": "frame_01.jpg",
                "timestamp_seconds": 120.0,
                "blocks": [{"text": "固定标题", "box": [[0, 0], [2, 0], [2, 1], [0, 1]], "confidence": 0.9}],
            },
        ]
        result = provider._deduplicate(frames)
        self.assertEqual(2, len(result))
        self.assertEqual([1.0, 120.0], [item["first_seen_seconds"] for item in result])

    def test_empty_probe_output_is_rejected(self) -> None:
        runner = FakeCommandRunner(probe_stdout="")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_path = root / "source.mp4"
            write_media(media_path)
            analyzer = VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
            )
            with self.assertRaisesRegex(VisualAnalysisError, "ffprobe 未返回"):
                analyzer.analyze(media_path, SOURCE_SHA256, root / "raw")

    def test_non_object_probe_output_is_rejected(self) -> None:
        runner = FakeCommandRunner(probe_stdout="[]")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_path = root / "source.mp4"
            write_media(media_path)
            analyzer = VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
            )
            with self.assertRaisesRegex(VisualAnalysisError, "不是 JSON 对象"):
                analyzer.analyze(media_path, SOURCE_SHA256, root / "raw")

    def test_empty_frame_output_is_rejected_without_report(self) -> None:
        runner = FakeCommandRunner(empty_frame=True)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_path = root / "source.mp4"
            output_dir = root / "raw"
            write_media(media_path)
            analyzer = VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
            )
            with self.assertRaisesRegex(VisualAnalysisError, "空图片"):
                analyzer.analyze(media_path, SOURCE_SHA256, output_dir)
            self.assertFalse((output_dir / "visual_analysis.json").exists())

    def test_timeout_error_does_not_disclose_local_media_path(self) -> None:
        runner = FakeCommandRunner(timeout_stage="scene")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_path = root / "source.mp4"
            write_media(media_path)
            analyzer = VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
            )
            with self.assertRaises(VisualAnalysisError) as raised:
                analyzer.analyze(media_path, SOURCE_SHA256, root / "raw")
            self.assertIn("超时", str(raised.exception))
            self.assertNotIn(str(media_path), str(raised.exception))

    def test_total_deadline_stops_before_late_frame_commands(self) -> None:
        base_runner = FakeCommandRunner(duration_seconds=10.0)
        clock_state = {"now": 0.0}

        def clock() -> float:
            return clock_state["now"]

        def advancing_runner(
            command: list[str], cwd: Path, timeout_seconds: int
        ) -> CommandOutcome:
            outcome = base_runner(command, cwd, timeout_seconds)
            clock_state["now"] += 2.0
            return outcome

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_path = root / "source.mp4"
            output_dir = root / "raw"
            write_media(media_path)
            analyzer = VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=advancing_runner,
                config=VisualAnalysisConfig(total_timeout_seconds=3),
                clock=clock,
            )
            with self.assertRaisesRegex(
                VisualAnalysisError,
                "3 秒总时限.*代表帧抽取",
            ):
                analyzer.analyze(media_path, SOURCE_SHA256, output_dir)

            self.assertFalse((output_dir / "visual_analysis.json").exists())
            self.assertEqual([3, 1], [call[2] for call in base_runner.calls])
            self.assertFalse(
                any("-frames:v" in command for command, _, _ in base_runner.calls)
            )

    def test_invalid_source_hash_is_rejected_before_commands_run(self) -> None:
        runner = FakeCommandRunner()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_path = root / "source.mp4"
            write_media(media_path)
            analyzer = VisualAnalyzer(
                ffmpeg_executable="ffmpeg-mock",
                ffprobe_executable="ffprobe-mock",
                command_runner=runner,
            )
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                analyzer.analyze(media_path, "not-a-hash", root / "raw")
        self.assertEqual([], runner.calls)


FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe are not installed")
class VisualAnalysisFFmpegIntegrationTests(unittest.TestCase):
    def test_real_ffmpeg_analyzes_synthetic_video_and_reuses_cache(self) -> None:
        assert FFMPEG is not None
        assert FFPROBE is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media_path = root / "synthetic.mp4"
            completed = subprocess.run(
                [
                    FFMPEG,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=red:s=320x240:d=1:r=25",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=blue:s=320x240:d=1:r=25",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=green:s=320x240:d=1:r=25",
                    "-filter_complex",
                    "[0:v][1:v][2:v]concat=n=3:v=1:a=0,format=yuv420p[v]",
                    "-map",
                    "[v]",
                    "-c:v",
                    "mpeg4",
                    str(media_path),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if completed.returncode != 0:
                self.skipTest("local ffmpeg cannot generate the synthetic fixture")

            source_sha256 = hashlib.sha256(media_path.read_bytes()).hexdigest()
            output_dir = root / "raw"
            analyzer = VisualAnalyzer(
                ffmpeg_executable=FFMPEG,
                ffprobe_executable=FFPROBE,
            )
            first = analyzer.analyze(media_path, source_sha256, output_dir)
            second = analyzer.analyze(media_path, source_sha256, output_dir)

            self.assertEqual("partial", first["status"])
            self.assertTrue(first["frames"])
            self.assertTrue(
                all(
                    (output_dir / item["artifact_name"]).is_file()
                    for item in first["frames"]
                )
            )
            self.assertFalse(first["cache_hit"])
            self.assertTrue(second["cache_hit"])
            self.assertNotIn(str(root), json.dumps(first, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
