"""Twitter/X API v2 — 搜尋近期熱門推文。"""

from __future__ import annotations

import os

import httpx

from shared.log import get_logger

logger = get_logger("nakama.zoro.twitter")

_SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"


def search_recent_tweets(topic: str, max_results: int = 10) -> dict:
    """Search recent tweets about a topic using Twitter API v2.

    Requires TWITTER_BEARER_TOKEN environment variable.

    Returns dict with keys: tweets (list of dicts with text, metrics, url, author).
    Returns empty dict on failure.
    """
    bearer = os.environ.get("TWITTER_BEARER_TOKEN", "")
    if not bearer:
        logger.warning("TWITTER_BEARER_TOKEN not set — skipping Twitter search")
        return {}

    try:
        resp = httpx.get(
            _SEARCH_URL,
            params={
                "query": f"{topic} -is:retweet lang:en",
                "max_results": min(max_results, 100),
                "sort_order": "relevancy",
                "tweet.fields": "created_at,public_metrics,author_id",
                "expansions": "author_id",
                "user.fields": "username,name",
            },
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        # Build author lookup from includes
        authors = {}
        for user in data.get("includes", {}).get("users", []):
            authors[user["id"]] = {
                "username": user["username"],
                "name": user["name"],
            }

        tweets = []
        for tweet in data.get("data", []):
            metrics = tweet.get("public_metrics", {})
            author_id = tweet.get("author_id", "")
            author = authors.get(author_id, {})
            username = author.get("username", "")
            tweets.append({
                "text": tweet["text"],
                "created_at": tweet.get("created_at", "")[:10],
                "likes": metrics.get("like_count", 0),
                "retweets": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
                "impressions": metrics.get("impression_count", 0),
                "author": author.get("name", ""),
                "username": username,
                "url": f"https://x.com/{username}/status/{tweet['id']}"
                if username
                else "",
            })

        # Sort by engagement (likes + retweets)
        tweets.sort(key=lambda t: t["likes"] + t["retweets"], reverse=True)

        return {"tweets": tweets}

    except Exception as e:
        logger.error(f"Twitter API error: {e}")
        return {}
