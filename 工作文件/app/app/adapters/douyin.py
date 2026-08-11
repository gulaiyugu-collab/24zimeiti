from __future__ import annotations

import json
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_HTTP_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_AWEME_ID_RE = re.compile(r"(?:/video/|[?&]modal_id=)(\d{10,})", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?)]}>\"'，。；：！？）】》"


def canonical_url(raw_value: str) -> str:
    """Extract a URL from a pasted share sentence without contacting the network."""
    match = _HTTP_URL_RE.search(raw_value.strip())
    if match:
        return match.group(0).rstrip(_TRAILING_PUNCTUATION)
    return raw_value.strip().rstrip(_TRAILING_PUNCTUATION)


def _hostname(raw_value: str) -> str:
    value = canonical_url(raw_value)
    parsed = urlparse(value if "://" in value else f"https://{value}")
    return (parsed.hostname or "").lower().rstrip(".")


def detect_platform(raw_value: str) -> str:
    host = _hostname(raw_value)
    if host == "douyin.com" or host.endswith(".douyin.com") or host.endswith(".iesdouyin.com"):
        return "douyin"
    if host == "tiktok.com" or host.endswith(".tiktok.com"):
        return "tiktok"
    if host == "youtube.com" or host.endswith(".youtube.com") or host == "youtu.be":
        return "youtube"
    if host == "facebook.com" or host.endswith(".facebook.com") or host == "fb.watch":
        return "facebook"
    if host == "x.com" or host.endswith(".x.com") or host == "twitter.com" or host.endswith(".twitter.com"):
        return "x"
    return "unknown"


def extract_aweme_id(raw_value: str) -> str | None:
    match = _AWEME_ID_RE.search(canonical_url(raw_value))
    return match.group(1) if match else None


@lru_cache(maxsize=4)
def _load_cases(data_path: str) -> dict[str, dict[str, Any]]:
    path = Path(data_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Unable to load demo cases from {path}") from exc

    cases = payload.get("cases")
    if not isinstance(cases, dict):
        raise RuntimeError("demo_cases.json must contain a 'cases' object")

    validated: dict[str, dict[str, Any]] = {}
    for aweme_id, case in cases.items():
        if not isinstance(aweme_id, str) or not isinstance(case, dict):
            raise RuntimeError("Each demo case must map an aweme id to an object")
        report = case.get("report")
        if not isinstance(report, dict):
            raise RuntimeError(f"Demo case {aweme_id} is missing a report object")
        validated[aweme_id] = case
    return validated


class DouyinAdapter:
    """Fixture-backed adapter for the first demonstrable product slice."""

    def __init__(self, data_path: Path | None = None) -> None:
        self.data_path = data_path or Path(__file__).resolve().parents[1] / "data" / "demo_cases.json"

    @property
    def cases(self) -> dict[str, dict[str, Any]]:
        return _load_cases(str(self.data_path.resolve()))

    def get_registered_case(self, raw_url: str) -> dict[str, Any] | None:
        aweme_id = extract_aweme_id(raw_url)
        if not aweme_id:
            return None
        case = self.cases.get(aweme_id)
        return deepcopy(case) if case else None

    def get_default_case(self) -> tuple[str, dict[str, Any]]:
        if not self.cases:
            raise RuntimeError("No demo cases are registered")
        aweme_id = next(iter(self.cases))
        return aweme_id, deepcopy(self.cases[aweme_id])
