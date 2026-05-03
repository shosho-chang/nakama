"""agents/zoro/reddit_api.py — hot_in_health_subreddits() discovery 測試。

真 Reddit API 不打（會慢且 flaky）；mock httpx.get。
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from agents.zoro import reddit_api


def _make_reddit_response(posts: list[dict]) -> dict:
    """組 Reddit API 回傳格式（data.children[].data）。"""
    return {"data": {"children": [{"data": p} for p in posts]}}


def _ts_hours_ago(hours: float) -> float:
    return datetime.now(timezone.utc).timestamp() - hours * 3600


def test_hot_filters_old_posts():
    """超過 max_age_hours 的 post 丟掉。"""
    payload = _make_reddit_response(
        [
            {
                "title": "fresh",
                "score": 50,
                "num_comments": 10,
                "subreddit": "biohacking",
                "created_utc": _ts_hours_ago(2),
                "permalink": "/r/biohacking/x",
            },
            {
                "title": "stale",
                "score": 500,
                "num_comments": 100,
                "subreddit": "biohacking",
                "created_utc": _ts_hours_ago(72),
                "permalink": "/r/biohacking/y",
            },
        ]
    )

    fake = MagicMock()
    fake.json.return_value = payload
    fake.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=fake):
        posts = reddit_api.hot_in_health_subreddits(max_age_hours=48)

    assert len(posts) == 1
    assert posts[0]["title"] == "fresh"


def test_hot_filters_low_score():
    payload = _make_reddit_response(
        [
            {
                "title": "hot",
                "score": 100,
                "num_comments": 10,
                "subreddit": "biohacking",
                "created_utc": _ts_hours_ago(2),
                "permalink": "/r/biohacking/a",
            },
            {
                "title": "cold",
                "score": 0,
                "num_comments": 0,
                "subreddit": "biohacking",
                "created_utc": _ts_hours_ago(2),
                "permalink": "/r/biohacking/b",
            },
        ]
    )

    fake = MagicMock()
    fake.json.return_value = payload
    fake.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=fake):
        posts = reddit_api.hot_in_health_subreddits(min_score=1)

    assert [p["title"] for p in posts] == ["hot"]


def test_hot_computes_velocity_capped_at_100():
    """score / age_hours 上限 100。"""
    payload = _make_reddit_response(
        [
            {
                "title": "explosive",
                "score": 500,
                "num_comments": 100,
                "subreddit": "biohacking",
                "created_utc": _ts_hours_ago(1),
                "permalink": "/r/biohacking/a",
            },  # 500/1 = 500 → clamped 100
            {
                "title": "moderate",
                "score": 60,
                "num_comments": 5,
                "subreddit": "nutrition",
                "created_utc": _ts_hours_ago(3),
                "permalink": "/r/nutrition/b",
            },  # 60/3 = 20
        ]
    )

    fake = MagicMock()
    fake.json.return_value = payload
    fake.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=fake):
        posts = reddit_api.hot_in_health_subreddits()

    assert posts[0]["title"] == "explosive"
    assert posts[0]["velocity_score"] == 100.0
    assert posts[1]["velocity_score"] == 20.0  # 60/3


def test_hot_sorts_by_velocity_descending():
    payload = _make_reddit_response(
        [
            {
                "title": "slow",
                "score": 30,
                "num_comments": 5,
                "subreddit": "fitness",
                "created_utc": _ts_hours_ago(10),
                "permalink": "/r/fitness/a",
            },  # 3.0
            {
                "title": "fast",
                "score": 80,
                "num_comments": 10,
                "subreddit": "longevity",
                "created_utc": _ts_hours_ago(2),
                "permalink": "/r/longevity/b",
            },  # 40
        ]
    )

    fake = MagicMock()
    fake.json.return_value = payload
    fake.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=fake):
        posts = reddit_api.hot_in_health_subreddits()

    assert [p["title"] for p in posts] == ["fast", "slow"]


def test_hot_handles_api_error_returns_empty():
    with patch("httpx.get", side_effect=RuntimeError("connection refused")):
        posts = reddit_api.hot_in_health_subreddits()
    assert posts == []


def test_hot_uses_multi_subreddit_syntax():
    """URL 走 /r/sub1+sub2+.../hot.json 一次抓完，省 N-1 個 call。"""
    fake = MagicMock()
    fake.json.return_value = {"data": {"children": []}}
    fake.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=fake) as m_get:
        reddit_api.hot_in_health_subreddits()

    assert m_get.call_count == 1
    url = m_get.call_args.args[0]
    assert "hot.json" in url
    # 多 subreddit 合起來用 + 串
    assert "+" in url
    assert "biohacking" in url
    assert "longevity" in url


def test_hot_skips_posts_missing_created_utc():
    payload = _make_reddit_response(
        [
            {
                "title": "no timestamp",
                "score": 100,
                "num_comments": 10,
                "subreddit": "biohacking",
                "permalink": "/r/x/a",
            },  # created_utc 缺
            {
                "title": "valid",
                "score": 100,
                "num_comments": 10,
                "subreddit": "biohacking",
                "created_utc": _ts_hours_ago(1),
                "permalink": "/r/x/b",
            },
        ]
    )
    fake = MagicMock()
    fake.json.return_value = payload
    fake.raise_for_status = MagicMock()

    with patch("httpx.get", return_value=fake):
        posts = reddit_api.hot_in_health_subreddits()

    assert [p["title"] for p in posts] == ["valid"]


# ──────────────────────────────────────────────────────────────────────────
# search_reddit_posts — subreddit_allowlist for zh-content keyword_research
# (GH #33 Item 4: reddit_zh query 精度)
# ──────────────────────────────────────────────────────────────────────────


def _empty_search_response() -> MagicMock:
    fake = MagicMock()
    fake.json.return_value = {"data": {"children": []}}
    fake.raise_for_status = MagicMock()
    return fake


def test_search_reddit_posts_without_allowlist_uses_global_search_url():
    """Backward compat: bare call hits /search.json, no restrict_sr param."""
    with patch("httpx.get", return_value=_empty_search_response()) as mock_get:
        reddit_api.search_reddit_posts("creatine")

    args, kwargs = mock_get.call_args
    assert args[0] == "https://www.reddit.com/search.json"
    assert "restrict_sr" not in kwargs["params"]


def test_search_reddit_posts_with_allowlist_uses_multi_subreddit_url():
    """allowlist → URL becomes /r/sub1+sub2+.../search.json + restrict_sr=on."""
    allowlist = ["longevity", "nutrition", "biohacking"]
    with patch("httpx.get", return_value=_empty_search_response()) as mock_get:
        reddit_api.search_reddit_posts("深度睡眠", subreddit_allowlist=allowlist)

    args, kwargs = mock_get.call_args
    assert args[0] == "https://www.reddit.com/r/longevity+nutrition+biohacking/search.json"
    assert kwargs["params"]["restrict_sr"] == "on"
    assert kwargs["params"]["q"] == "深度睡眠"


def test_search_reddit_posts_empty_allowlist_falls_back_to_global():
    """Falsy allowlist (empty list, None) keeps legacy global search."""
    with patch("httpx.get", return_value=_empty_search_response()) as mock_get:
        reddit_api.search_reddit_posts("topic", subreddit_allowlist=[])

    args, kwargs = mock_get.call_args
    assert args[0] == "https://www.reddit.com/search.json"
    assert "restrict_sr" not in kwargs["params"]
