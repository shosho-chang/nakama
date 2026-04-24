"""Reddit public JSON API — 搜尋熱門討論貼文 + scout discovery。"""

from __future__ import annotations

from datetime import datetime, timezone

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


def hot_in_health_subreddits(
    *,
    limit: int = 50,
    max_age_hours: float = 48.0,
    min_score: int = 1,
) -> list[dict]:
    """抓 _HEALTH_SUBREDDITS 合集的 hot posts，供 Zoro scout 當 signal 用。

    用 Reddit multi-subreddit 語法 `/r/sub1+sub2+.../hot.json` 一次取完（一 call 搞定）。

    過濾：
      - 超過 `max_age_hours` 的舊 post 丟掉（只要現在正在燒的）
      - `score < min_score` 丟掉（冷 post）
      - 缺 `created_utc` 的丟掉

    每則 post 計算 `velocity_score = min(100, score / max(age_hours, 1))`，
    即 "每小時 upvote 數，上限 100"。scout 用這個當 velocity gate 輸入。

    回傳 list[dict]，按 velocity 由高到低排。失敗回 []。
    """
    subs = "+".join(_HEALTH_SUBREDDITS)
    url = f"https://www.reddit.com/r/{subs}/hot.json"
    try:
        resp = httpx.get(
            url,
            params={"limit": min(limit, 100), "raw_json": 1},
            # Reddit 要求 UA 格式 `<platform>:<appID>:<version> (by /u/<user>)` —
            # 泛用的「bot」字眼會被鎖 429。repo URL 當 contact。
            headers={
                "User-Agent": (
                    "linux:tw.shosho.nakama.zoro-scout:1.0 "
                    "(by https://github.com/shosho-chang/nakama)"
                )
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"Reddit hot API error: {e}")
        return []

    now_ts = datetime.now(timezone.utc).timestamp()
    posts: list[dict] = []
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        created_utc = post.get("created_utc", 0)
        if not created_utc:
            continue
        age_hours = (now_ts - created_utc) / 3600.0
        if age_hours < 0 or age_hours > max_age_hours:
            continue
        score = post.get("score", 0)
        if score < min_score:
            continue
        velocity = min(100.0, score / max(age_hours, 1.0))
        posts.append(
            {
                "title": post.get("title", ""),
                "score": score,
                "num_comments": post.get("num_comments", 0),
                "subreddit": post.get("subreddit", ""),
                "created_utc": created_utc,
                "age_hours": round(age_hours, 2),
                "velocity_score": round(velocity, 2),
                "url": f"https://reddit.com{post.get('permalink', '')}",
            }
        )

    posts.sort(key=lambda p: p["velocity_score"], reverse=True)
    return posts
