from .douyin import (
    DouyinAdapter,
    canonical_url,
    detect_platform,
    extract_aweme_id,
)
from .tiktok import TikTokAdapter, extract_tiktok_video_id

__all__ = [
    "DouyinAdapter",
    "canonical_url",
    "detect_platform",
    "extract_aweme_id",
    "TikTokAdapter",
    "extract_tiktok_video_id",
]
