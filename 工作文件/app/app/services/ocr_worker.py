from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "project024-ocr-worker/v1"
PROVIDER_NAME = "rapidocr_local"
MODEL_VERSION = "rapidocr-3.9.2-ppocrv6-small-v1"
MAX_FRAMES = 100


def _float(value: Any) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite number")
    return parsed


def _box(value: Any) -> list[list[float]]:
    raw = value.tolist() if hasattr(value, "tolist") else value
    if not isinstance(raw, list) or len(raw) != 4:
        raise ValueError("invalid OCR box")
    result: list[list[float]] = []
    for point in raw:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("invalid OCR point")
        result.append([round(_float(point[0]), 2), round(_float(point[1]), 2)])
    return result


def _request() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("invalid request schema")
    if payload.get("provider") != PROVIDER_NAME or payload.get("model_version") != MODEL_VERSION:
        raise ValueError("invalid provider identity")
    frames = payload.get("frames")
    if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
        raise ValueError("invalid frame count")
    text_score = _float(payload.get("text_score"))
    if not 0 <= text_score <= 1:
        raise ValueError("invalid text score")
    return payload


def main() -> int:
    try:
        payload = _request()
        from rapidocr import RapidOCR

        engine = RapidOCR(params={"Global.log_level": "error"})
        output_frames: list[dict[str, Any]] = []
        for frame in payload["frames"]:
            if not isinstance(frame, dict):
                raise ValueError("invalid frame")
            frame_id = str(frame.get("frame_id") or "")
            timestamp = round(_float(frame.get("timestamp_seconds")), 3)
            frame_path = Path(str(frame.get("path") or ""))
            if not frame_id or not frame_path.is_file():
                raise ValueError("invalid frame input")
            result = engine(frame_path, text_score=float(payload["text_score"]))
            texts = tuple(result.txts if result.txts is not None else ())
            scores = tuple(result.scores if result.scores is not None else ())
            boxes = tuple(result.boxes if result.boxes is not None else ())
            if not (len(texts) == len(scores) == len(boxes)):
                raise RuntimeError("inconsistent OCR output")
            blocks = [
                {
                    "text": str(text).strip(),
                    "box": _box(box),
                    "confidence": round(_float(score), 5),
                }
                for text, score, box in zip(texts, scores, boxes)
                if str(text).strip()
            ]
            output_frames.append(
                {
                    "frame_id": frame_id,
                    "timestamp_seconds": timestamp,
                    "blocks": blocks,
                }
            )
        json.dump(
            {
                "schema_version": SCHEMA_VERSION,
                "status": "completed",
                "provider": PROVIDER_NAME,
                "model_version": MODEL_VERSION,
                "frames": output_frames,
            },
            sys.stdout,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return 0
    except Exception as exc:
        print(f"OCR worker failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
