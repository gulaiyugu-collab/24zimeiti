from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .douyin import canonical_url


_TIKTOK_VIDEO_ID_RE = re.compile(r"/video/(\d{10,})", re.IGNORECASE)


def extract_tiktok_video_id(raw_value: str) -> str | None:
    """Extract a public video id when the submitted URL already contains one."""
    match = _TIKTOK_VIDEO_ID_RE.search(canonical_url(raw_value))
    return match.group(1) if match else None


@lru_cache(maxsize=4)
def _load_cases(data_path: str) -> dict[str, dict[str, Any]]:
    path = Path(data_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to load TikTok demo cases from {path}") from exc

    cases = payload.get("cases")
    if not isinstance(cases, dict):
        raise RuntimeError("tiktok_cases.json must contain a 'cases' object")

    validated: dict[str, dict[str, Any]] = {}
    for video_id, case in cases.items():
        if not isinstance(video_id, str) or not isinstance(case, dict):
            raise RuntimeError("Each TikTok demo case must map a video id to an object")
        report = case.get("report")
        if not isinstance(report, dict):
            raise RuntimeError(f"TikTok demo case {video_id} is missing a report object")
        aliases = case.get("aliases", [])
        if not isinstance(aliases, list) or not all(
            isinstance(item, str) for item in aliases
        ):
            raise RuntimeError(f"TikTok demo case {video_id} aliases must be a string array")
        validated[video_id] = case
    return validated


@dataclass(frozen=True)
class TikTokSubmission:
    url: str
    video_id: str | None

    def as_source(self) -> dict[str, object]:
        evidence: list[dict[str, object]] = [
            {
                "type": "public_url",
                "label": "用户提交的 TikTok 链接",
                "value": self.url,
                "confidence": "submitted",
            }
        ]
        if self.video_id:
            evidence.append(
                {
                    "type": "url_identifier",
                    "label": "链接中可见的视频 ID",
                    "value": self.video_id,
                    "confidence": "parsed_from_url",
                }
            )

        return {
            "platform": "tiktok",
            "url": self.url,
            "video_id": self.video_id,
            "acquisition_mode": "user_supplied_url",
            "retrieval_status": "not_run",
            "evidence": evidence,
        }


class TikTokAdapter:
    """Evidence-safe TikTok boundary for v0.2.

    Short-link resolution, page retrieval and media downloading are intentionally
    outside this adapter. A submitted URL is never presented as retrieved content.
    """

    def __init__(self, data_path: Path | None = None) -> None:
        self.data_path = data_path or Path(__file__).resolve().parents[1] / "data" / "tiktok_cases.json"

    @property
    def cases(self) -> dict[str, dict[str, Any]]:
        return _load_cases(str(self.data_path.resolve()))

    def inspect_submission(self, raw_url: str) -> TikTokSubmission:
        url = canonical_url(raw_url)
        return TikTokSubmission(url=url, video_id=extract_tiktok_video_id(url))

    def get_registered_case(self, raw_url: str) -> dict[str, Any] | None:
        submitted_url = canonical_url(raw_url).rstrip("/")
        video_id = extract_tiktok_video_id(submitted_url)
        if video_id:
            case = self.cases.get(video_id)
            if case:
                return deepcopy(case)

        for case in self.cases.values():
            aliases = case.get("aliases", [])
            if any(canonical_url(alias).rstrip("/") == submitted_url for alias in aliases):
                return deepcopy(case)
        return None
