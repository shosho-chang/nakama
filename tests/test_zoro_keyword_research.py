"""Unit tests for ``agents.zoro.keyword_research`` v1.1 changes (issue #33).

Covers:
- Item 2: auto_translate result lowercase normalization.
- Item 3: synthesis prompt receives ``today_iso`` (Asia/Taipei).
- Item 6: research_keywords drains the per-thread usage buffer into ``result['usage']``,
  and ``scripts.run_keyword_research._calc_cost_usd`` / ``_format_cost_summary``
  render the right dollars and structure.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import agents.zoro.keyword_research as kw_mod
import scripts.run_keyword_research as cli_mod
from shared.llm_context import start_usage_tracking, stop_usage_tracking
from shared.llm_observability import record_call

# ──────────────────────────────────────────────────────────────────────────
# Item 2: auto_translate lowercase
# ──────────────────────────────────────────────────────────────────────────


def test_auto_translate_lowercases_capitalized_response(monkeypatch):
    """Claude often returns ``Deep sleep`` — caller should see ``deep sleep``."""
    monkeypatch.setattr(kw_mod, "ask", lambda *a, **kw: "Deep Sleep")
    assert kw_mod._auto_translate("深度睡眠") == "deep sleep"


def test_auto_translate_strips_quotes_then_lowercases(monkeypatch):
    monkeypatch.setattr(kw_mod, "ask", lambda *a, **kw: '"Intermittent Fasting"')
    assert kw_mod._auto_translate("間歇性斷食") == "intermittent fasting"


# ──────────────────────────────────────────────────────────────────────────
# Item 3: synthesis prompt gets today_iso (Taipei)
# ──────────────────────────────────────────────────────────────────────────


def _stub_collectors(monkeypatch):
    """Stub every parallel data source so the synthesis branch is reached."""
    monkeypatch.setattr(kw_mod, "search_top_videos", lambda *_: {"top_videos": [], "avg_views": 0})
    monkeypatch.setattr(
        kw_mod,
        "get_trends",
        lambda *_: {"trend_direction": "stable", "related_top": [], "related_rising": []},
    )
    monkeypatch.setattr(kw_mod, "get_suggestions", lambda *_: {"suggestions": []})
    # `**__` swallows new kwargs (subreddit_allowlist / region) added for GH #33 Item 4+5
    monkeypatch.setattr(kw_mod, "search_recent_tweets", lambda *_, **__: {"tweets": []})
    monkeypatch.setattr(kw_mod, "search_reddit_posts", lambda *_, **__: {"posts": []})


def test_research_keywords_passes_today_iso_to_load_prompt(monkeypatch):
    captured: dict = {}

    def _fake_load_prompt(agent: str, name: str, **kwargs):
        captured.update(kwargs)
        return "stub-prompt"

    _stub_collectors(monkeypatch)
    monkeypatch.setattr(kw_mod, "load_prompt", _fake_load_prompt)
    monkeypatch.setattr(
        kw_mod, "ask", lambda *a, **kw: '{"core_keywords": [], "youtube_titles": []}'
    )

    kw_mod.research_keywords("深度睡眠", content_type="youtube", en_topic="deep sleep")

    assert "today_iso" in captured, "load_prompt must receive today_iso (Item 3)"
    expected = datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    assert captured["today_iso"] == expected, "today_iso should be Asia/Taipei wall-clock date"


# ──────────────────────────────────────────────────────────────────────────
# GH #33 Item 4 + 5 — reddit_zh / twitter_zh language channel biasing
# ──────────────────────────────────────────────────────────────────────────


def test_reddit_zh_collector_passes_subreddit_allowlist():
    """reddit_zh must invoke search_reddit_posts with the health subreddit allowlist
    so zh queries don't land on r/moneyfengcn etc. (eval finding 2026-04-19)."""
    captured_calls: list[dict] = []

    def _spy_reddit(*args, **kwargs):
        captured_calls.append({"args": args, "kwargs": kwargs})
        return {"posts": []}

    def _spy_twitter(*args, **kwargs):
        return {"tweets": []}

    # We don't actually need to invoke the full pipeline — just verify the
    # collector dict construction. Monkeypatch the leaf functions and run
    # research_keywords with a stubbed Claude ask.
    from unittest.mock import patch as _patch

    with (
        _patch.object(kw_mod, "search_reddit_posts", side_effect=_spy_reddit),
        _patch.object(kw_mod, "search_recent_tweets", side_effect=_spy_twitter),
        _patch.object(kw_mod, "search_top_videos", return_value={"top_videos": [], "avg_views": 0}),
        _patch.object(
            kw_mod,
            "get_trends",
            return_value={"trend_direction": "stable", "related_top": [], "related_rising": []},
        ),
        _patch.object(kw_mod, "get_suggestions", return_value={"suggestions": []}),
        _patch.object(kw_mod, "load_prompt", return_value="stub"),
        _patch.object(
            kw_mod,
            "ask",
            return_value='{"core_keywords": [], "youtube_titles": []}',
        ),
    ):
        kw_mod.research_keywords("深度睡眠", content_type="blog", en_topic="deep sleep")

    # Two reddit calls: zh (with allowlist) + en (without)
    zh_calls = [c for c in captured_calls if c["args"] and c["args"][0] == "深度睡眠"]
    en_calls = [c for c in captured_calls if c["args"] and c["args"][0] == "deep sleep"]
    assert len(zh_calls) == 1
    assert len(en_calls) == 1
    # zh must have allowlist
    assert "subreddit_allowlist" in zh_calls[0]["kwargs"]
    assert zh_calls[0]["kwargs"]["subreddit_allowlist"] == kw_mod._HEALTH_SUBREDDITS
    # en must NOT have allowlist (legacy global search behaviour)
    assert "subreddit_allowlist" not in en_calls[0]["kwargs"]


def test_twitter_zh_collector_passes_region_tw_tzh():
    """twitter_zh must invoke search_recent_tweets(region='tw-tzh') so DDG biases
    to Taiwan zh-TW results (eval finding 2026-04-19 — Charles Zhang zh-CN dominance)."""
    captured_calls: list[dict] = []

    def _spy_twitter(*args, **kwargs):
        captured_calls.append({"args": args, "kwargs": kwargs})
        return {"tweets": []}

    from unittest.mock import patch as _patch

    with (
        _patch.object(kw_mod, "search_recent_tweets", side_effect=_spy_twitter),
        _patch.object(kw_mod, "search_reddit_posts", return_value={"posts": []}),
        _patch.object(kw_mod, "search_top_videos", return_value={"top_videos": [], "avg_views": 0}),
        _patch.object(
            kw_mod,
            "get_trends",
            return_value={"trend_direction": "stable", "related_top": [], "related_rising": []},
        ),
        _patch.object(kw_mod, "get_suggestions", return_value={"suggestions": []}),
        _patch.object(kw_mod, "load_prompt", return_value="stub"),
        _patch.object(
            kw_mod,
            "ask",
            return_value='{"core_keywords": [], "youtube_titles": []}',
        ),
    ):
        kw_mod.research_keywords("深度睡眠", content_type="blog", en_topic="deep sleep")

    zh_calls = [c for c in captured_calls if c["args"] and c["args"][0] == "深度睡眠"]
    en_calls = [c for c in captured_calls if c["args"] and c["args"][0] == "deep sleep"]
    assert len(zh_calls) == 1
    assert len(en_calls) == 1
    # zh must have region=tw-tzh
    assert zh_calls[0]["kwargs"].get("region") == "tw-tzh"
    # en must NOT have region (legacy DDG default)
    assert "region" not in en_calls[0]["kwargs"]


# ──────────────────────────────────────────────────────────────────────────
# Item 6: usage buffer drains into result["usage"]
# ──────────────────────────────────────────────────────────────────────────


def test_research_keywords_returns_usage_records(monkeypatch):
    """research_keywords must drain the buffer; each Claude call appends one record."""

    def _fake_ask(*_a, **_kw):
        # Simulate what the real wrapper does internally — record_call appends
        # to the thread-local usage buffer (and best-effort to state.api_calls).
        record_call(model="claude-sonnet-4-6", input_tokens=120, output_tokens=35)
        return '{"core_keywords": [], "youtube_titles": []}'

    _stub_collectors(monkeypatch)
    monkeypatch.setattr(kw_mod, "load_prompt", lambda *a, **kw: "stub")
    monkeypatch.setattr(kw_mod, "ask", _fake_ask)

    result = kw_mod.research_keywords("深度睡眠", en_topic="deep sleep")

    usage = result["usage"]
    assert isinstance(usage, list)
    assert len(usage) == 1
    rec = usage[0]
    assert rec["input_tokens"] == 120
    assert rec["output_tokens"] == 35
    assert rec["model"] == "claude-sonnet-4-6"


def test_research_keywords_drains_buffer_on_exception(monkeypatch):
    """If synthesis raises, buffer must still clear so a reused thread doesn't inherit records."""

    def _boom(*_a, **_kw):
        record_call(model="claude-sonnet-4-6", input_tokens=50, output_tokens=10)
        raise RuntimeError("synthesis exploded")

    _stub_collectors(monkeypatch)
    monkeypatch.setattr(kw_mod, "load_prompt", lambda *a, **kw: "stub")
    monkeypatch.setattr(kw_mod, "ask", _boom)

    with pytest.raises(RuntimeError, match="synthesis exploded"):
        kw_mod.research_keywords("深度睡眠", en_topic="deep sleep")

    # After the failure, a fresh start/stop should see an empty buffer.
    start_usage_tracking()
    leftover = stop_usage_tracking()
    assert leftover == [], "buffer should not retain records from the failed run"


# ──────────────────────────────────────────────────────────────────────────
# Item 6: cost calc + summary rendering (CLI side)
# ──────────────────────────────────────────────────────────────────────────


def test_calc_cost_usd_sonnet():
    """1M input + 1M output @ Sonnet = $3 + $15 = $18."""
    records = [
        {
            "model": "claude-sonnet-4-6",
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
    ]
    assert cli_mod._calc_cost_usd(records) == pytest.approx(18.0)


def test_calc_cost_usd_haiku_cheaper_than_sonnet():
    haiku = [
        {
            "model": "claude-haiku-4-5",
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
    ]
    sonnet = [{**haiku[0], "model": "claude-sonnet-4-6"}]
    assert cli_mod._calc_cost_usd(haiku) < cli_mod._calc_cost_usd(sonnet)


def test_calc_cost_usd_unknown_model_falls_back_to_sonnet():
    rec = [
        {
            "model": "claude-future-99",
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
    ]
    assert cli_mod._calc_cost_usd(rec) == pytest.approx(3.0)


def test_format_cost_summary_includes_tokens_and_dollars():
    usage = [
        {
            "model": "claude-sonnet-4-6",
            "input_tokens": 4321,
            "output_tokens": 2222,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
    ]
    block = cli_mod._format_cost_summary(usage)
    assert "成本（實測）" in block
    assert "Claude API call(s)：1 次" in block
    assert "4,321" in block
    assert "2,222" in block
    assert "$" in block
    assert "歷史 N 次平均" in block


def test_format_cost_summary_empty_returns_empty_string():
    """No usage records → no cost block (don't print fake zeros)."""
    assert cli_mod._format_cost_summary([]) == ""
