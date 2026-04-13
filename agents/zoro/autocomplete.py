"""YouTube Autocomplete — 取得搜尋建議關鍵字。"""

from __future__ import annotations

import json

import httpx

from shared.log import get_logger

logger = get_logger("nakama.zoro.autocomplete")

_SUGGEST_URL = "https://suggestqueries.google.com/complete/search"


def get_suggestions(topic: str) -> dict:
    """Fetch YouTube autocomplete suggestions for a topic.

    Uses Google's public suggest endpoint (no API key needed).
    Returns dict with key: suggestions (list of strings).
    Returns empty dict on failure.
    """
    try:
        resp = httpx.get(
            _SUGGEST_URL,
            params={"client": "youtube", "ds": "yt", "q": topic},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()

        # Response is JSONP: window.google.ac.h([ ... ])
        text = resp.text
        # Strip JSONP wrapper: everything between first ( and last )
        start = text.index("(") + 1
        end = text.rindex(")")
        data = json.loads(text[start:end])

        # data[1] contains suggestion arrays: [[suggestion, 0, [512,433]], ...]
        suggestions = []
        if len(data) > 1 and isinstance(data[1], list):
            for item in data[1]:
                if isinstance(item, list) and len(item) > 0:
                    suggestions.append(str(item[0]))

        return {"suggestions": suggestions[:20]}

    except Exception as e:
        logger.error(f"YouTube autocomplete error: {e}")
        return {}
