"""agents/zoro/trends_api.py — discover_trending_health() 測試。

真 Trends 不打（慢且會變），mock trendspy.Trends。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agents.zoro import trends_api


def _make_trend(keyword, volume=100_000, growth_pct=500, trend_keywords=None):
    """組 TrendKeyword-like 物件（trendspy 回 namedtuple，測試用 SimpleNamespace 代）。"""
    return SimpleNamespace(
        keyword=keyword,
        normalized_keyword=keyword,
        volume=volume,
        volume_growth_pct=growth_pct,
        trend_keywords=trend_keywords or [keyword],
        geo="US",
    )


def _patch_trends(trends_list):
    """patch trendspy.Trends class with a mock instance returning trends_list."""
    mock_cls = MagicMock()
    mock_inst = MagicMock()
    mock_inst.trending_now.return_value = trends_list
    mock_cls.return_value = mock_inst

    import trendspy

    return patch.object(trendspy, "Trends", mock_cls)


# ── _is_health_related ─────────────────────────────────────────────────────


def test_is_health_related_hits_keyword():
    assert trends_api._is_health_related("ozempic news", []) is True


def test_is_health_related_hits_trend_keywords():
    assert (
        trends_api._is_health_related(
            "some unrelated headline",
            ["nfl", "glucose monitor", "diet"],
        )
        is True
    )


def test_is_health_related_misses_non_health():
    assert trends_api._is_health_related("nfl draft picks", ["nfl", "draft", "raiders"]) is False


# ── velocity computation ───────────────────────────────────────────────────


def test_velocity_low_growth_low_volume():
    # growth_pct=50, volume=100 → base=5, no boost → 5
    assert trends_api._velocity_from_trend(100, 50) == 5.0


def test_velocity_high_growth_gets_clamped_at_100():
    # growth_pct=2000, volume=2M → base=200 + boost 30 → clamp 100
    assert trends_api._velocity_from_trend(2_000_000, 2000) == 100.0


def test_velocity_medium_volume_gets_boost():
    # growth_pct=100 → base=10, volume=200K → +15 boost → 25
    assert trends_api._velocity_from_trend(200_000, 100) == 25.0


def test_velocity_handles_none_values():
    assert trends_api._velocity_from_trend(None, None) == 0.0


# ── discover_trending_health ───────────────────────────────────────────────


def test_discover_filters_to_health_only():
    trends = [
        _make_trend(
            "nfl draft",
            volume=2_000_000,
            growth_pct=1000,
            trend_keywords=["nfl", "draft", "raiders"],
        ),
        _make_trend(
            "ozempic side effects",
            volume=500_000,
            growth_pct=800,
            trend_keywords=["ozempic", "weight loss", "glp-1"],
        ),
        _make_trend(
            "taylor swift tour",
            volume=1_000_000,
            growth_pct=500,
            trend_keywords=["taylor", "swift", "concert"],
        ),
    ]
    with _patch_trends(trends):
        results = trends_api.discover_trending_health()

    assert len(results) == 1
    assert results[0]["title"] == "ozempic side effects"
    assert results[0]["subreddit"] == "trends"  # source tag alignment with reddit shape


def test_discover_sorts_by_velocity_descending():
    trends = [
        _make_trend(
            "sleep apnea", volume=50_000, growth_pct=100, trend_keywords=["sleep", "apnea"]
        ),  # slow
        _make_trend(
            "fasting protocol", volume=200_000, growth_pct=800, trend_keywords=["fasting", "diet"]
        ),  # fast
    ]
    with _patch_trends(trends):
        results = trends_api.discover_trending_health()

    assert [r["title"] for r in results] == ["fasting protocol", "sleep apnea"]


def test_discover_handles_trendspy_error():
    mock_cls = MagicMock()
    mock_inst = MagicMock()
    mock_inst.trending_now.side_effect = RuntimeError("Trends rate limited")
    mock_cls.return_value = mock_inst

    import trendspy

    with patch.object(trendspy, "Trends", mock_cls):
        results = trends_api.discover_trending_health()

    assert results == []


def test_discover_handles_none_trends():
    with _patch_trends(None):
        results = trends_api.discover_trending_health()
    assert results == []


def test_discover_skips_trends_missing_keyword():
    trends = [
        SimpleNamespace(
            keyword=None,
            normalized_keyword=None,
            volume=100_000,
            volume_growth_pct=500,
            trend_keywords=["glucose"],
            geo="US",
        ),
        _make_trend("glucose monitor", trend_keywords=["glucose", "cgm"]),
    ]
    with _patch_trends(trends):
        results = trends_api.discover_trending_health()

    assert [r["title"] for r in results] == ["glucose monitor"]


def test_discover_includes_related_keywords_capped():
    trends = [
        _make_trend(
            "cgm",
            trend_keywords=["cgm", "glucose", "biohack"] + [f"related{i}" for i in range(20)],
        ),
    ]
    with _patch_trends(trends):
        results = trends_api.discover_trending_health()

    # `related` 在 dict 層 cap 10
    assert len(results[0]["related"]) == 10
    assert results[0]["related"][:3] == ["cgm", "glucose", "biohack"]
