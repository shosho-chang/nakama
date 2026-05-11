"""Tests for ADR-022 §2 S1 trust-tier score ceiling.

Experimental low-trust candidates (e.g. github_trending) carry a
``score_ceiling`` (default 4). The pipeline must cap the LLM-returned
``overall`` and per-dim ``scores`` at that ceiling before the digest is
written, regardless of what the LLM said.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from agents.franky import news_digest as nd
from agents.franky.news_digest import _apply_trust_ceiling, _count_trust_tiers


def _trending_cand(**overrides) -> dict:
    base = {
        "item_id": "github-trending-foo-bar",
        "title": "foo/bar",
        "publisher": "GitHub Trending",
        "feed_name": "github_trending_python",
        "url": "https://github.com/foo/bar",
        "summary": "...",
        "published": "2026-05-07T00:00:00+00:00",
        "published_ts": 1.0,
        "age_hours": 4.0,
        "trust_tier": "experimental",
        "score_ceiling": 4,
    }
    base.update(overrides)
    return base


def _full_trust_cand(**overrides) -> dict:
    base = {
        "item_id": "rss-anthropic-1",
        "title": "Claude 4.7 1M context",
        "publisher": "Anthropic",
        "feed_name": "anthropic_news_html",
        "url": "https://www.anthropic.com/news/claude-47-1m",
        "summary": "...",
        "published": "2026-05-07T00:00:00+00:00",
        "published_ts": 2.0,
        "age_hours": 4.0,
    }
    base.update(overrides)
    return base


# ---- _apply_trust_ceiling ---------------------------------------------------


def test_ceiling_caps_overall_above_ceiling():
    cand = _trending_cand()
    score = {
        "scores": {"signal": 5, "novelty": 4, "actionability": 5, "noise": 5, "relevance": 3},
        "overall": 4.7,
        "pick": True,
    }
    out = _apply_trust_ceiling(cand, score)
    assert out["overall"] == 4
    assert out["scores"]["signal"] == 4  # capped
    assert out["scores"]["novelty"] == 4  # untouched (already ≤ ceiling)
    assert out["scores"]["actionability"] == 4  # capped
    assert out["scores"]["noise"] == 4  # capped
    assert out["score_ceiling_applied"] == 4
    assert out["trust_tier"] == "experimental"


def test_ceiling_no_op_when_overall_below_ceiling():
    cand = _trending_cand()
    score = {
        "scores": {"signal": 3, "novelty": 2, "actionability": 3, "noise": 4, "relevance": 3},
        "overall": 3.0,
        "pick": True,
    }
    out = _apply_trust_ceiling(cand, score)
    assert out["overall"] == 3.0
    assert out["scores"] == {
        "signal": 3,
        "novelty": 2,
        "actionability": 3,
        "noise": 4,
        "relevance": 3,
    }


def test_ceiling_skipped_for_full_trust_candidate():
    cand = _full_trust_cand()
    score = {
        "scores": {"signal": 5, "novelty": 5, "actionability": 5, "noise": 5, "relevance": 3},
        "overall": 5.0,
        "pick": True,
    }
    out = _apply_trust_ceiling(cand, score)
    assert out["overall"] == 5.0
    assert out["scores"]["signal"] == 5
    assert "score_ceiling_applied" not in out
    assert "trust_tier" not in out


def test_ceiling_does_not_mutate_input():
    cand = _trending_cand()
    score = {
        "scores": {"signal": 5, "novelty": 5, "actionability": 5, "noise": 5, "relevance": 3},
        "overall": 5.0,
    }
    _apply_trust_ceiling(cand, score)
    assert score["overall"] == 5.0
    assert score["scores"]["signal"] == 5


def test_ceiling_handles_non_numeric_gracefully():
    cand = _trending_cand()
    score = {"overall": "n/a", "scores": {"signal": "n/a"}}
    out = _apply_trust_ceiling(cand, score)
    assert out["overall"] == "n/a"
    assert out["scores"]["signal"] == "n/a"


def test_ceiling_emits_log_when_capped(caplog):
    cand = _trending_cand()
    score = {"scores": {"signal": 5}, "overall": 4.9}
    logger = logging.getLogger("test.ceiling")
    with caplog.at_level(logging.INFO, logger="test.ceiling"):
        _apply_trust_ceiling(cand, score, logger=logger)
    assert any("trust ceiling" in r.message for r in caplog.records)


# ---- _count_trust_tiers -----------------------------------------------------


def test_count_trust_tiers_default_full_trust():
    cands = [
        _full_trust_cand(item_id="a"),
        _full_trust_cand(item_id="b"),
        _trending_cand(item_id="c"),
    ]
    assert _count_trust_tiers(cands) == {"full_trust": 2, "experimental": 1}


def test_count_trust_tiers_empty():
    assert _count_trust_tiers([]) == {}


# ---- Pipeline-level: ceiling actually applied during run() ------------------


def test_pipeline_caps_experimental_score_during_run(tmp_path, monkeypatch):
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )
    trending = _trending_cand()
    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [trending])
    monkeypatch.setattr(nd.anthropic_html, "gather_candidates", lambda **kw: [])
    monkeypatch.setattr(nd.awesome_diff, "gather_candidates", lambda *a, **kw: [])
    monkeypatch.setattr(nd.github_trending, "gather_candidates", lambda *a, **kw: [])

    def fake_ask(prompt, **kw):
        import json as _json

        if "8-12" in prompt or "篩出當日" in prompt:
            return _json.dumps(
                {
                    "selected": [
                        {
                            "item_id": "github-trending-foo-bar",
                            "rank": 1,
                            "category": "agent_framework",
                            "reason": "r",
                        }
                    ],
                    "summary": {
                        "total_candidates": 1,
                        "selected_count": 1,
                        "main_categories": ["agent_framework"],
                        "editor_note": "t",
                    },
                }
            )
        return _json.dumps(
            {
                "scores": {
                    "signal": 5,
                    "novelty": 5,
                    "actionability": 5,
                    "noise": 5,
                    "relevance": 3,
                },
                "overall": 4.8,
                "one_line_verdict": "v",
                "why_it_matters": "w",
                "key_finding": "k",
                "noise_note": "n",
                "pick": True,
            }
        )

    monkeypatch.setattr(nd.llm, "ask", fake_ask)

    monkeypatch.setattr(nd, "write_page", MagicMock())
    monkeypatch.setattr(nd, "append_to_file", MagicMock())
    monkeypatch.setattr(nd, "mark_seen", MagicMock())

    pipeline = nd.NewsDigestPipeline(dry_run=True, feeds_config_path=cfg, slack_bot=MagicMock())
    summary = pipeline.run()
    assert "selected=1" in summary
    assert "experimental" in str(pipeline._trust_tier_breakdown)
