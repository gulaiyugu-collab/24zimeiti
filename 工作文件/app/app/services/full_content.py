from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Sequence


MAX_TRANSCRIPT_BYTES = 4 * 1024 * 1024
MAX_TRANSCRIPT_SEGMENTS = 20_000
MAX_PAGE_SIZE = 100


class FullContentError(ValueError):
    """Raised when a registered full-content artifact cannot be trusted."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def read_verified_transcript(
    path: Path,
    *,
    expected_sha256: str,
    expected_size_bytes: int,
) -> dict[str, Any]:
    path = Path(path)
    try:
        size_bytes = path.stat().st_size
    except OSError as exc:
        raise FullContentError("完整口播文件当前不可读取。") from exc
    if size_bytes <= 0:
        raise FullContentError("完整口播文件为空。")
    if size_bytes > MAX_TRANSCRIPT_BYTES:
        raise FullContentError("完整口播文件超过 4 MiB 安全上限。")
    if expected_size_bytes <= 0 or size_bytes != expected_size_bytes:
        raise FullContentError("完整口播文件大小与登记记录不一致。")
    normalized_sha256 = str(expected_sha256).strip().lower()
    if len(normalized_sha256) != 64 or _sha256_file(path) != normalized_sha256:
        raise FullContentError("完整口播文件校验失败。")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FullContentError("完整口播文件不是有效的 UTF-8 JSON。") from exc
    if not isinstance(payload, dict):
        raise FullContentError("完整口播文件结构无效。")
    text = payload.get("text")
    segments = payload.get("segments")
    if not isinstance(text, str) or not text.strip():
        raise FullContentError("完整口播文件缺少非空全文。")
    if not isinstance(segments, list) or not segments:
        raise FullContentError("完整口播文件缺少带时间码分段。")
    if len(segments) > MAX_TRANSCRIPT_SEGMENTS:
        raise FullContentError("完整口播分段超过 20000 段安全上限。")
    cleaned_segments: list[dict[str, Any]] = []
    for index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            raise FullContentError("完整口播包含无效分段。")
        segment_text = segment.get("text")
        start = _number(segment.get("start"))
        end = _number(segment.get("end"))
        if not isinstance(segment_text, str) or not segment_text.strip() or end < start:
            raise FullContentError("完整口播包含无效时间码或空分段。")
        cleaned_segments.append(
            {
                "id": str(segment.get("id") or index),
                "start": round(max(start, 0.0), 3),
                "end": round(max(end, start), 3),
                "text": segment_text.strip(),
            }
        )
    return {
        "text": text,
        "character_count": len(text),
        "segment_count": len(cleaned_segments),
        "source": str(payload.get("source") or "worker_transcript")[:80],
        "provider": str(payload.get("provider") or "")[:120] or None,
        "model": str(payload.get("model") or "")[:120] or None,
        "language": str(payload.get("language") or "")[:40] or None,
        "segments": cleaned_segments,
    }


def paginated_response(
    section: str,
    items: Sequence[dict[str, Any]],
    *,
    offset: int,
    limit: int,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if offset < 0:
        raise FullContentError("offset 不能小于 0。")
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise FullContentError(f"limit 必须在 1 和 {MAX_PAGE_SIZE} 之间。")
    total = len(items)
    page = list(items[offset : offset + limit])
    return {
        "schema_version": "project024-full-content/v1",
        "section": section,
        "status": "completed",
        "offset": offset,
        "limit": limit,
        "total_items": total,
        "has_more": offset + len(page) < total,
        "items": page,
        **(metadata or {}),
    }


def ocr_items(visual_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    ocr = visual_analysis.get("ocr")
    if not isinstance(ocr, dict) or ocr.get("status") != "completed":
        return []
    frame_urls = {
        str(frame.get("frame_id")): str(frame.get("artifact_url"))
        for frame in visual_analysis.get("frames", [])
        if isinstance(frame, dict)
        and isinstance(frame.get("frame_id"), str)
        and str(frame.get("artifact_url") or "").startswith("/api/acquisition/jobs/")
    }
    items: list[dict[str, Any]] = []
    for block in ocr.get("blocks", []):
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            continue
        frame_id = str(block.get("frame_id") or "")
        confidence = _number(block.get("confidence"))
        items.append(
            {
                "frame_id": frame_id,
                "last_frame_id": str(block.get("last_frame_id") or frame_id),
                "timestamp_seconds": round(_number(block.get("timestamp_seconds")), 3),
                "first_seen_seconds": round(_number(block.get("first_seen_seconds")), 3),
                "last_seen_seconds": round(_number(block.get("last_seen_seconds")), 3),
                "text": block["text"].strip(),
                "box": block.get("box") if isinstance(block.get("box"), list) else [],
                "confidence": round(min(max(confidence, 0.0), 1.0), 5),
                "provider": str(block.get("provider") or ocr.get("provider") or "")[:80],
                "model_version": str(
                    block.get("model_version") or ocr.get("model_version") or ""
                )[:120],
                "artifact_url": frame_urls.get(frame_id),
            }
        )
    return items


def vision_items(visual_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    vision = visual_analysis.get("vision")
    if not isinstance(vision, dict) or vision.get("status") != "completed":
        return []
    frame_urls = {
        str(frame.get("frame_id")): str(frame.get("artifact_url"))
        for frame in visual_analysis.get("frames", [])
        if isinstance(frame, dict)
        and isinstance(frame.get("frame_id"), str)
        and str(frame.get("artifact_url") or "").startswith("/api/acquisition/jobs/")
    }
    items: list[dict[str, Any]] = []
    for key, evidence_state in (
        ("observations", "observed"),
        ("possible_inferences", "inferred"),
    ):
        raw_items = vision.get(key)
        for item in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(item, dict):
                continue
            frame_id = str(item.get("frame_id") or "")
            description = str(item.get("description") or "").strip()
            artifact_url = frame_urls.get(frame_id)
            confidence = _number(item.get("confidence"))
            if not description or artifact_url is None:
                continue
            items.append(
                {
                    "frame_id": frame_id,
                    "timestamp_seconds": round(
                        _number(item.get("timestamp_seconds")), 3
                    ),
                    "category": str(item.get("category") or "observation")[:40],
                    "description": description[:240],
                    "confidence": round(min(max(confidence, 0.0), 1.0), 4),
                    "evidence_state": evidence_state,
                    "provider": str(
                        item.get("provider") or vision.get("provider") or ""
                    )[:80],
                    "model_version": str(
                        item.get("model_version")
                        or vision.get("model_version")
                        or ""
                    )[:120],
                    "artifact_url": artifact_url,
                }
            )
    return items


def build_timeline(
    transcript: dict[str, Any], visual_analysis: dict[str, Any]
) -> list[dict[str, Any]]:
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        return []
    blocks = sorted(
        ocr_items(visual_analysis),
        key=lambda item: (item["first_seen_seconds"], item["last_seen_seconds"]),
    )
    visuals = sorted(
        vision_items(visual_analysis),
        key=lambda item: (item["timestamp_seconds"], item["frame_id"]),
    )
    timeline: list[dict[str, Any]] = []
    assigned: set[int] = set()
    assigned_visuals: set[int] = set()
    cursor = 0
    active: list[tuple[int, dict[str, Any]]] = []
    for segment in segments:
        start = _number(segment.get("start"))
        end = _number(segment.get("end"))
        active = [item for item in active if item[1]["last_seen_seconds"] >= start]
        while cursor < len(blocks) and blocks[cursor]["first_seen_seconds"] <= end:
            active.append((cursor, blocks[cursor]))
            cursor += 1
        overlapping = [
            (index, block)
            for index, block in active
            if block["last_seen_seconds"] >= start
            and block["first_seen_seconds"] <= end
        ]
        for index, _ in overlapping:
            assigned.add(index)
        overlapping_visuals = [
            (index, item)
            for index, item in enumerate(visuals)
            if start <= item["timestamp_seconds"] <= end
        ]
        for index, _ in overlapping_visuals:
            assigned_visuals.add(index)
        on_screen_text = list(dict.fromkeys(block["text"] for _, block in overlapping))
        visual_observations = list(
            dict.fromkeys(
                item["description"]
                for _, item in overlapping_visuals
                if item["evidence_state"] == "observed"
            )
        )
        visual_inferences = list(
            dict.fromkeys(
                item["description"]
                for _, item in overlapping_visuals
                if item["evidence_state"] == "inferred"
            )
        )
        evidence_refs = [f"transcript:segment_{segment['id']}"]
        evidence_refs.extend(
            f"frame:{block['frame_id']}" for _, block in overlapping
        )
        evidence_refs.extend(
            f"frame:{item['frame_id']}" for _, item in overlapping_visuals
        )
        timeline.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "spoken_text": str(segment.get("text") or ""),
                "on_screen_text": on_screen_text,
                "visual_observations": visual_observations,
                "visual_inferences": visual_inferences,
                "visual_evidence": [item for _, item in overlapping_visuals],
                "evidence_refs": list(dict.fromkeys(evidence_refs)),
                "confidence": (
                    "mixed_sources"
                    if overlapping or overlapping_visuals
                    else "transcript_only"
                ),
            }
        )
    for index, block in enumerate(blocks):
        if index in assigned:
            continue
        timeline.append(
            {
                "start": block["first_seen_seconds"],
                "end": block["last_seen_seconds"],
                "spoken_text": "",
                "on_screen_text": [block["text"]],
                "visual_observations": [],
                "visual_inferences": [],
                "visual_evidence": [],
                "evidence_refs": [f"frame:{block['frame_id']}"],
                "confidence": "ocr_only",
            }
        )
    grouped_visuals: dict[tuple[str, float], list[dict[str, Any]]] = {}
    for index, item in enumerate(visuals):
        if index not in assigned_visuals:
            grouped_visuals.setdefault(
                (item["frame_id"], item["timestamp_seconds"]), []
            ).append(item)
    for (frame_id, timestamp), items in grouped_visuals.items():
        timeline.append(
            {
                "start": timestamp,
                "end": timestamp,
                "spoken_text": "",
                "on_screen_text": [],
                "visual_observations": list(
                    dict.fromkeys(
                        item["description"]
                        for item in items
                        if item["evidence_state"] == "observed"
                    )
                ),
                "visual_inferences": list(
                    dict.fromkeys(
                        item["description"]
                        for item in items
                        if item["evidence_state"] == "inferred"
                    )
                ),
                "visual_evidence": items,
                "evidence_refs": [f"frame:{frame_id}"],
                "confidence": "vision_only",
            }
        )
    return sorted(timeline, key=lambda item: (item["start"], item["end"]))


__all__ = [
    "FullContentError",
    "MAX_PAGE_SIZE",
    "MAX_TRANSCRIPT_BYTES",
    "build_timeline",
    "ocr_items",
    "paginated_response",
    "read_verified_transcript",
    "vision_items",
]
