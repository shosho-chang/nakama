"""Tests for 5-dim scoring helpers (ADR-023 §7 S2b).

Covers:
- _compute_4dim_overall: weighted formula
- _compute_5dim_overall: weighted formula with relevance
- _shadow_pick: shadow mode gate logic
- _load_context_snapshot: file loading + missing file fallback
- _score(): snapshot inject + Python-overridden overall / pick
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.franky import news_digest as nd

# ---------------------------------------------------------------------------
# _compute_4dim_overall
# ---------------------------------------------------------------------------


def test_4dim_overall_formula():
    scores = {"signal": 5, "novelty": 4, "actionability": 5, "noise": 5}
    expected = (5 * 1.5 + 4 * 1.0 + 5 * 1.2 + 5 * 1.0) / 4.7
    assert abs(nd._compute_4dim_overall(scores) - expected) < 1e-9


def test_4dim_overall_missing_key_treated_as_zero():
    scores = {"signal": 4}
    expected = (4 * 1.5) / 4.7
    assert abs(nd._compute_4dim_overall(scores) - expected) < 1e-9


def test_4dim_overall_all_ones():
    scores = {"signal": 1, "novelty": 1, "actionability": 1, "noise": 1}
    expected = (1 * 1.5 + 1 * 1.0 + 1 * 1.2 + 1 * 1.0) / 4.7
    assert abs(nd._compute_4dim_overall(scores) - expected) < 1e-9


# ---------------------------------------------------------------------------
# _compute_5dim_overall
# ---------------------------------------------------------------------------


def test_5dim_overall_formula():
    scores = {"signal": 5, "novelty": 4, "actionability": 5, "noise": 5, "relevance": 4}
    expected = (5 * 1.5 + 4 * 1.0 + 5 * 1.2 + 5 * 1.0 + 4 * 1.3) / 6.0
    assert abs(nd._compute_5dim_overall(scores) - expected) < 1e-9


def test_5dim_overall_relevance_missing_treated_as_zero():
    scores = {"signal": 4, "novelty": 3, "actionability": 4, "noise": 4}
    expected = (4 * 1.5 + 3 * 1.0 + 4 * 1.2 + 4 * 1.0 + 0 * 1.3) / 6.0
    assert abs(nd._compute_5dim_overall(scores) - expected) < 1e-9


def test_5dim_always_less_than_or_equal_to_5():
    scores = {"signal": 5, "novelty": 5, "actionability": 5, "noise": 5, "relevance": 5}
    result = nd._compute_5dim_overall(scores)
    assert result <= 5.0


# ---------------------------------------------------------------------------
# _shadow_pick
# ---------------------------------------------------------------------------


def _scores_passing():
    return {"signal": 4, "novelty": 3, "actionability": 4, "noise": 4, "relevance": 3}


def test_shadow_pick_passes_when_all_gates_met():
    scores = _scores_passing()
    overall_4dim = nd._compute_4dim_overall(scores)
    assert overall_4dim >= 3.5
    assert nd._shadow_pick(scores, overall_4dim) is True


def test_shadow_pick_fails_when_overall_4dim_below_threshold():
    scores = {"signal": 3, "novelty": 2, "actionability": 2, "noise": 3, "relevance": 3}
    overall_4dim = nd._compute_4dim_overall(scores)
    assert overall_4dim < 3.5
    assert nd._shadow_pick(scores, overall_4dim) is False


def test_shadow_pick_fails_when_signal_below_3():
    scores = {"signal": 2, "novelty": 5, "actionability": 5, "noise": 5, "relevance": 4}
    overall_4dim = nd._compute_4dim_overall(scores)
    assert nd._shadow_pick(scores, overall_4dim) is False


def test_shadow_pick_fails_when_relevance_below_2():
    scores = {"signal": 4, "novelty": 4, "actionability": 4, "noise": 4, "relevance": 1}
    overall_4dim = nd._compute_4dim_overall(scores)
    assert nd._shadow_pick(scores, overall_4dim) is False


def test_shadow_pick_passes_at_relevance_exactly_2():
    scores = {"signal": 4, "novelty": 4, "actionability": 4, "noise": 4, "relevance": 2}
    overall_4dim = nd._compute_4dim_overall(scores)
    assert nd._shadow_pick(scores, overall_4dim) is True


# ---------------------------------------------------------------------------
# _load_context_snapshot
# ---------------------------------------------------------------------------


def test_load_context_snapshot_returns_empty_when_file_missing(tmp_path):
    missing = tmp_path / "no_such_file.md"
    result = nd._load_context_snapshot(path=missing)
    assert result == ""


def test_load_context_snapshot_returns_file_content(tmp_path):
    snap = tmp_path / "franky_context_snapshot.md"
    snap.write_text("# Snapshot\n\nPriority 1: ADR-023\n", encoding="utf-8")
    result = nd._load_context_snapshot(path=snap)
    assert "ADR-023" in result


# ---------------------------------------------------------------------------
# _score() — snapshot inject + Python-overridden fields
# ---------------------------------------------------------------------------


def _llm_score_response_5dim(
    signal: int = 4,
    novelty: int = 3,
    actionability: int = 4,
    noise: int = 5,
    relevance: int = 3,
    relevance_ref: str | None = "#475",
) -> str:
    return json.dumps(
        {
            "scores": {
                "signal": signal,
                "novelty": novelty,
                "actionability": actionability,
                "noise": noise,
                "relevance": relevance,
            },
            "overall": 99.0,  # LLM value intentionally wrong — Python overrides
            "overall_4dim": 99.0,  # LLM value intentionally wrong — Python overrides
            "relevance_ref": relevance_ref,
            "one_line_verdict": "test verdict",
            "why_it_matters": "test why",
            "key_finding": "test key",
            "noise_note": "無明顯炒作",
            "pick": True,  # LLM value — Python overrides with shadow gate
        }
    )


def _make_cand():
    return {
        "item_id": "i1",
        "title": "Test Article",
        "publisher": "TestPub",
        "url": "https://example.com/1",
        "summary": "Test summary.",
        "published": "2026-05-07T08:00:00+00:00",
    }


def test_score_returns_5dim_with_python_overrides(tmp_path, monkeypatch):
    """_score() must override overall / overall_4dim / pick with Python-computed values."""
    pipeline = _make_pipeline(tmp_path, monkeypatch)

    monkeypatch.setattr(nd.llm, "ask", lambda p, **kw: _llm_score_response_5dim())
    # Provide a real snapshot so context_snapshot kwarg is non-empty
    snap = nd._SNAPSHOT_PATH
    monkeypatch.setattr(nd, "_load_context_snapshot", lambda path=snap: "context text")

    cand = _make_cand()
    result = pipeline._score(cand, {"reason": "test", "category": "model_release"})

    scores = result["scores"]
    assert "relevance" in scores
    # Python-computed overalls must not be the LLM's bogus 99.0
    expected_4dim = nd._compute_4dim_overall(scores)
    expected_5dim = nd._compute_5dim_overall(scores)
    assert abs(result["overall_4dim"] - round(expected_4dim, 2)) < 1e-9
    assert abs(result["overall"] - round(expected_5dim, 2)) < 1e-9
    # Python-computed pick must follow shadow gate
    expected_pick = nd._shadow_pick(scores, expected_4dim)
    assert result["pick"] is expected_pick


def test_score_pick_false_when_relevance_1(tmp_path, monkeypatch):
    pipeline = _make_pipeline(tmp_path, monkeypatch)
    monkeypatch.setattr(
        nd.llm,
        "ask",
        lambda p, **kw: _llm_score_response_5dim(
            signal=5, novelty=5, actionability=5, noise=5, relevance=1
        ),
    )
    monkeypatch.setattr(nd, "_load_context_snapshot", lambda **kw: "")

    cand = _make_cand()
    result = pipeline._score(cand, {"reason": "r", "category": "model_release"})
    # relevance=1 < 2 → pick must be False
    assert result["pick"] is False


def test_score_prompt_receives_context_snapshot(tmp_path, monkeypatch):
    """Snapshot text must appear in the assembled prompt passed to llm.ask."""
    pipeline = _make_pipeline(tmp_path, monkeypatch)
    SENTINEL = "SENTINEL_SNAPSHOT_TEXT_12345"
    monkeypatch.setattr(nd, "_load_context_snapshot", lambda **kw: SENTINEL)

    captured_prompts: list[str] = []

    def _fake_ask(prompt, **kw):
        captured_prompts.append(prompt)
        return _llm_score_response_5dim()

    monkeypatch.setattr(nd.llm, "ask", _fake_ask)

    cand = _make_cand()
    pipeline._score(cand, {"reason": "r", "category": "model_release"})

    assert len(captured_prompts) == 1
    assert SENTINEL in captured_prompts[0]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pipeline(tmp_path: Path, monkeypatch) -> nd.NewsDigestPipeline:
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )
    return nd.NewsDigestPipeline(dry_run=True, feeds_config_path=cfg)
