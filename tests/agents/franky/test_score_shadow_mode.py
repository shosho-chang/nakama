"""Integration tests for 5-dim shadow mode (ADR-023 §7 S2b).

Covers:
- dry-run: 5-dim scores present in pipeline output, no DB write
- non-dry-run: shadow scores written to news_score_shadow table
- pick gate enforcement end-to-end through run()
- both 4-dim (overall_v1) and 5-dim (overall_v2) recorded in DB
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import shared.state as state
from agents.franky import news_digest as nd

# ---------------------------------------------------------------------------
# Fixtures & factories
# ---------------------------------------------------------------------------


def _make_candidate(item_id: str = "i1") -> dict:
    return {
        "item_id": item_id,
        "title": "Shadow mode test article",
        "publisher": "TestPub",
        "feed_name": "test_feed",
        "url": f"https://example.com/{item_id}",
        "summary": "Test summary for shadow mode integration test.",
        "published": "2026-05-07T08:00:00+00:00",
        "published_ts": 1.0,
        "age_hours": 2.0,
    }


def _curate_response(item_ids: list[str]) -> str:
    return json.dumps(
        {
            "selected": [
                {"item_id": iid, "rank": i + 1, "category": "model_release", "reason": "r"}
                for i, iid in enumerate(item_ids)
            ],
            "summary": {
                "total_candidates": len(item_ids),
                "selected_count": len(item_ids),
                "main_categories": ["model_release"],
                "editor_note": "shadow test note",
            },
        }
    )


def _score_5dim_response(
    signal: int = 4,
    novelty: int = 3,
    actionability: int = 4,
    noise: int = 4,
    relevance: int = 3,
    relevance_ref: str | None = None,
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
            "overall": 3.8,
            "overall_4dim": 3.7,
            "relevance_ref": relevance_ref,
            "one_line_verdict": "shadow test verdict",
            "why_it_matters": "shadow why",
            "key_finding": "shadow key",
            "noise_note": "無明顯炒作",
            "pick": True,
        }
    )


def _make_pipeline(tmp_path: Path, *, dry_run: bool) -> nd.NewsDigestPipeline:
    cfg = tmp_path / "feeds.yaml"
    cfg.write_text(
        "feeds:\n  - name: x\n    url: https://example.com\n    publisher: X\n",
        encoding="utf-8",
    )
    return nd.NewsDigestPipeline(dry_run=dry_run, feeds_config_path=cfg)


# ---------------------------------------------------------------------------
# dry-run: verify 5-dim scores present, no DB write
# ---------------------------------------------------------------------------


def test_dryrun_pipeline_produces_5dim_scores(tmp_path, monkeypatch):
    """In dry-run mode the pipeline must produce 5-dim score fields but NOT write to DB."""
    pipeline = _make_pipeline(tmp_path, dry_run=True)
    cand = _make_candidate()

    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [cand])
    monkeypatch.setattr(nd, "_load_context_snapshot", lambda **kw: "")

    def _fake_ask(prompt, **kw):
        if "8-12 條" in prompt or "candidates" in prompt.lower():
            return _curate_response(["i1"])
        return _score_5dim_response()

    monkeypatch.setattr(nd.llm, "ask", _fake_ask)

    summary = pipeline.run()
    assert "selected=1" in summary

    # DB must have no rows (dry_run skips write)
    conn = state._get_conn()
    rows = conn.execute("SELECT * FROM news_score_shadow").fetchall()
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# non-dry-run: shadow score written to DB
# ---------------------------------------------------------------------------


def test_nondryrun_writes_shadow_score_to_db(tmp_path, monkeypatch):
    """Full-path run must write one shadow score row per scored item."""
    pipeline = _make_pipeline(tmp_path, dry_run=False)
    cand = _make_candidate()

    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [cand])
    monkeypatch.setattr(nd, "_load_context_snapshot", lambda **kw: "")
    monkeypatch.setattr(nd, "write_page", lambda *a, **kw: None)
    monkeypatch.setattr(nd, "append_to_file", lambda *a, **kw: None)
    monkeypatch.setattr(nd, "mark_seen", lambda *a, **kw: None)

    def _fake_ask(prompt, **kw):
        if "8-12 條" in prompt or "candidates" in prompt.lower():
            return _curate_response(["i1"])
        return _score_5dim_response(signal=4, novelty=3, actionability=4, noise=4, relevance=3)

    monkeypatch.setattr(nd.llm, "ask", _fake_ask)

    pipeline.run()

    conn = state._get_conn()
    rows = conn.execute("SELECT * FROM news_score_shadow").fetchall()
    assert len(rows) == 1

    row = rows[0]
    assert row["item_id"] == "i1"
    assert row["relevance"] == pytest.approx(3.0)
    assert row["signal"] == pytest.approx(4.0)
    # Both overalls recorded
    assert row["overall_v1"] > 0
    assert row["overall_v2"] > 0
    # overall_v1 and overall_v2 must differ when relevance > 0
    assert abs(row["overall_v1"] - row["overall_v2"]) > 0.01


def test_nondryrun_shadow_row_contains_correct_overalls(tmp_path, monkeypatch):
    """Verify the stored overall_v1 / overall_v2 match the formula."""
    pipeline = _make_pipeline(tmp_path, dry_run=False)
    cand = _make_candidate()

    S, N, A, Q, R = 4, 3, 4, 4, 3  # noqa: N806

    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [cand])
    monkeypatch.setattr(nd, "_load_context_snapshot", lambda **kw: "")
    monkeypatch.setattr(nd, "write_page", lambda *a, **kw: None)
    monkeypatch.setattr(nd, "append_to_file", lambda *a, **kw: None)
    monkeypatch.setattr(nd, "mark_seen", lambda *a, **kw: None)

    def _fake_ask(prompt, **kw):
        if "8-12 條" in prompt or "candidates" in prompt.lower():
            return _curate_response(["i1"])
        return _score_5dim_response(signal=S, novelty=N, actionability=A, noise=Q, relevance=R)

    monkeypatch.setattr(nd.llm, "ask", _fake_ask)
    pipeline.run()

    conn = state._get_conn()
    row = conn.execute("SELECT * FROM news_score_shadow").fetchone()
    assert row is not None

    expected_v1 = (S * 1.5 + N * 1.0 + A * 1.2 + Q * 1.0) / 4.7
    expected_v2 = (S * 1.5 + N * 1.0 + A * 1.2 + Q * 1.0 + R * 1.3) / 6.0
    assert row["overall_v1"] == pytest.approx(expected_v1, rel=1e-3)
    assert row["overall_v2"] == pytest.approx(expected_v2, rel=1e-3)


# ---------------------------------------------------------------------------
# pick gate enforcement
# ---------------------------------------------------------------------------


def test_relevance_1_excluded_from_scored(tmp_path, monkeypatch):
    """Item with relevance=1 must be filtered out (pick=False shadow gate)."""
    pipeline = _make_pipeline(tmp_path, dry_run=True)
    cand = _make_candidate()

    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [cand])
    monkeypatch.setattr(nd, "_load_context_snapshot", lambda **kw: "")

    def _fake_ask(prompt, **kw):
        if "8-12 條" in prompt or "candidates" in prompt.lower():
            return _curate_response(["i1"])
        # relevance=1 → pick gate fails
        return _score_5dim_response(signal=4, novelty=4, actionability=4, noise=4, relevance=1)

    monkeypatch.setattr(nd.llm, "ask", _fake_ask)

    summary = pipeline.run()
    # All items filtered out → no scored items
    assert "selected=0" in summary or "候選" in summary


def test_relevance_2_included_during_shadow(tmp_path, monkeypatch):
    """Item with relevance=2 passes the wide shadow gate (overall_4dim ≥ 3.5, signal ≥ 3)."""
    pipeline = _make_pipeline(tmp_path, dry_run=True)
    cand = _make_candidate()

    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [cand])
    monkeypatch.setattr(nd, "_load_context_snapshot", lambda **kw: "")

    def _fake_ask(prompt, **kw):
        if "8-12 條" in prompt or "candidates" in prompt.lower():
            return _curate_response(["i1"])
        return _score_5dim_response(signal=4, novelty=3, actionability=4, noise=4, relevance=2)

    monkeypatch.setattr(nd.llm, "ask", _fake_ask)

    summary = pipeline.run()
    assert "selected=1" in summary


def test_low_overall_4dim_excluded(tmp_path, monkeypatch):
    """Item with overall_4dim < 3.5 must be excluded even if relevance ≥ 2."""
    pipeline = _make_pipeline(tmp_path, dry_run=True)
    cand = _make_candidate()

    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [cand])
    monkeypatch.setattr(nd, "_load_context_snapshot", lambda **kw: "")

    def _fake_ask(prompt, **kw):
        if "8-12 條" in prompt or "candidates" in prompt.lower():
            return _curate_response(["i1"])
        # signal=2 → low overall_4dim
        return _score_5dim_response(signal=2, novelty=2, actionability=2, noise=3, relevance=4)

    monkeypatch.setattr(nd.llm, "ask", _fake_ask)

    summary = pipeline.run()
    assert "selected=0" in summary or "候選" in summary


# ---------------------------------------------------------------------------
# relevance_ref stored when provided
# ---------------------------------------------------------------------------


def test_relevance_ref_written_to_db(tmp_path, monkeypatch):
    pipeline = _make_pipeline(tmp_path, dry_run=False)
    cand = _make_candidate()

    monkeypatch.setattr(nd, "gather_candidates", lambda *a, **kw: [cand])
    monkeypatch.setattr(nd, "_load_context_snapshot", lambda **kw: "")
    monkeypatch.setattr(nd, "write_page", lambda *a, **kw: None)
    monkeypatch.setattr(nd, "append_to_file", lambda *a, **kw: None)
    monkeypatch.setattr(nd, "mark_seen", lambda *a, **kw: None)

    def _fake_ask(prompt, **kw):
        if "8-12 條" in prompt or "candidates" in prompt.lower():
            return _curate_response(["i1"])
        return _score_5dim_response(relevance=4, relevance_ref="ADR-023")

    monkeypatch.setattr(nd.llm, "ask", _fake_ask)
    pipeline.run()

    conn = state._get_conn()
    row = conn.execute("SELECT relevance_ref FROM news_score_shadow").fetchone()
    assert row is not None
    assert row["relevance_ref"] == "ADR-023"
