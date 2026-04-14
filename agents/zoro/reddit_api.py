"""Reddit public JSON API — 搜尋熱門討論貼文。"""

from __future__ import annotations

import httpx

from shared.log import get_logger

logger = get_logger("nakama.zoro.reddit")

_SEARCH_URL = "https://www.reddit.com/search.json"

# Health & Wellness 相關 subreddit，用於精準搜尋
_HEALTH_SUBREDDITS = [
    "supplements",
    "fitness",
    "longevity",
    "biohacking",
    "nutrition",
    "health",
    "keto",
    "intermittentfasting",
    "nootropics",
    "Peptides",
]


def search_reddit_posts(topic: str, max_results: int = 10) -> dict:
    """Search Reddit for popular posts about a topic.

    Uses Reddit's public JSON API (no API key needed).
    Searches across all of Reddit, sorted by relevance within the past month.

    Returns dict with keys: posts (list of dicts with title, score, comments, url, etc.).
    Returns empty dict on failure.
    """
    try:
        resp = httpx.get(
            _SEARCH_URL,
            params={
                "q": topic,
                "sort": "relevance",
                "t": "year",
                "limit": min(max_results, 25),
                "type": "link",
            },
            headers={"User-Agent": "nakama-bot/1.0 (keyword research)"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        posts = []
        for child in data.get("data", {}).get("children", []):
            post = child.get("data", {})
            subreddit = post.get("subreddit", "")
            posts.append(
                {
                    "title": post.get("title", ""),
                    "score": post.get("score", 0),
                    "num_comments": post.get("num_comments", 0),
                    "subreddit": subreddit,
                    "created_utc": post.get("created_utc", 0),
                    "url": f"https://reddit.com{post.get('permalink', '')}",
                    "is_health_sub": subreddit.lower() in [s.lower() for s in _HEALTH_SUBREDDITS],
                }
            )

        # Sort by engagement (score + comments)
        posts.sort(key=lambda p: p["score"] + p["num_comments"], reverse=True)

        return {"posts": posts}

    except Exception as e:
        logger.error(f"Reddit API error: {e}")
        return {}
