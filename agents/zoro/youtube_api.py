"""YouTube Data API v3 — 搜尋熱門影片並分析標題模式。"""

from __future__ import annotations

import os
import re
from collections import Counter

from shared.log import get_logger

logger = get_logger("nakama.zoro.youtube")


def _parse_duration(iso: str) -> int:
    """Parse ISO 8601 duration (e.g. 'PT1H2M30S') to seconds."""
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    h, mn, s = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mn * 60 + s


def search_top_videos(topic: str, max_results: int = 50) -> dict:
    """Search YouTube for top videos on a topic and analyze title patterns.

    Returns dict with keys: top_videos, common_words, avg_views.
    Returns empty dict on failure.
    """
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        logger.warning("YOUTUBE_API_KEY not set — skipping YouTube search")
        return {}

    try:
        from googleapiclient.discovery import build

        youtube = build("youtube", "v3", developerKey=api_key)

        # Step 1: Search for videos (100 quota units)
        search_resp = (
            youtube.search()
            .list(q=topic, type="video", part="snippet", order="viewCount", maxResults=max_results)
            .execute()
        )

        video_ids = [item["id"]["videoId"] for item in search_resp.get("items", [])]
        if not video_ids:
            return {"top_videos": [], "common_words": [], "avg_views": 0}

        # Step 2: Get video statistics + duration (1 quota unit)
        stats_resp = (
            youtube.videos()
            .list(id=",".join(video_ids), part="snippet,statistics,contentDetails")
            .execute()
        )

        videos = []
        total_views = 0
        all_title_words: list[str] = []

        for item in stats_resp.get("items", []):
            title = item["snippet"]["title"]
            view_count = int(item["statistics"].get("viewCount", 0))
            video_id = item["id"]
            duration_sec = _parse_duration(item.get("contentDetails", {}).get("duration", ""))
            videos.append(
                {
                    "title": title,
                    "views": view_count,
                    "channel": item["snippet"]["channelTitle"],
                    "published": item["snippet"]["publishedAt"][:10],
                    "url": f"https://youtube.com/watch?v={video_id}",
                    "duration_sec": duration_sec,
                    "is_short": duration_sec <= 60,
                }
            )
            total_views += view_count

            # Extract meaningful words (filter short/common words)
            words = re.findall(r"[\w\u4e00-\u9fff]+", title.lower())
            all_title_words.extend(w for w in words if len(w) > 1)

        # Analyze common title words (top 20)
        word_counts = Counter(all_title_words)
        # Remove overly generic words
        stopwords = {
            "the",
            "of",
            "and",
            "in",
            "to",
            "for",
            "is",
            "on",
            "it",
            "with",
            "at",
            "by",
            "this",
            "that",
        }
        common_words = [
            {"word": word, "count": count}
            for word, count in word_counts.most_common(30)
            if word not in stopwords
        ][:20]

        avg_views = total_views // len(videos) if videos else 0

        return {
            "top_videos": videos[:20],  # Keep top 20 for shorts/long split
            "common_words": common_words,
            "avg_views": avg_views,
        }

    except Exception as e:
        logger.error(f"YouTube API error: {e}")
        return {}
