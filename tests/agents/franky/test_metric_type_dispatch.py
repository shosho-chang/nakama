"""Tests for metric_type三類分流 (ADR-023 §7 S4).

Verifies that:
  quantitative  → _compute_quantitative_post_ship queries api_calls and returns value
  checklist     → _build_proposals_payload correctly groups and formats
  human_judged  → no quantitative value is faked; owner noted for verification
"""

from __future__ import annotations

from unittest.mock import patch

import agents.franky.news_retrospective as retro

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proposal(
    proposal_id: str = "franky-proposal-2026-w18-1",
    metric_type: str = "checklist",
    status: str = "shipped",
    baseline_source: str | None = None,
    baseline_value: str | None = None,
    post_ship_value: str | None = None,
    shipped_at: str | None = None,
    verification_owner: str | None = None,
    success_metric: str = "Feature shipped and smoke test passes",
) -> dict:
    return {
        "proposal_id": proposal_id,
        "metric_type": metric_type,
        "status": status,
        "baseline_source": baseline_source,
        "baseline_value": baseline_value,
        "post_ship_value": post_ship_value,
        "shipped_at": shipped_at,
        "verification_owner": verification_owner,
        "success_metric": success_metric,
        "related_adr": [],
        "related_issues": [],
        "week_iso": "2026-w18",
        "panel_recommended": False,
    }


# ---------------------------------------------------------------------------
# Quantitative: api_calls 取數
# ---------------------------------------------------------------------------


def test_quantitative_extracts_agent_name_from_baseline_source():
    """_extract_agent_name parses agent name from a baseline_source expression."""
    cases = [
        ("api_calls where agent='robin'", "robin"),
        ("shared.pricing.calc_cost over api_calls.where(agent='zoro')", "zoro"),
        ("agent=brook", "brook"),
        ("agent: franky", "franky"),
        (None, None),
        ("no agent mention here", None),
    ]
    for source, expected in cases:
        assert retro._extract_agent_name(source) == expected, f"source={source!r}"


def test_quantitative_fetch_api_calls_total(isolated_db):
    """_fetch_api_calls_total queries api_calls and returns token sum."""
    from shared.state import _get_conn

    conn = _get_conn()
    shipped_ts = "2026-04-15T00:00:00+00:00"
    conn.executemany(
        "INSERT INTO api_calls (agent, model, input_tokens, output_tokens, called_at)"
        " VALUES (?, ?, ?, ?, ?)",
        [
            ("robin", "claude-sonnet-4-6", 1000, 500, "2026-04-16T10:00:00+00:00"),
            ("robin", "claude-sonnet-4-6", 2000, 800, "2026-04-20T10:00:00+00:00"),
            ("franky", "claude-sonnet-4-6", 500, 200, "2026-04-18T10:00:00+00:00"),
            # Before shipped_at — should be excluded
            ("robin", "claude-sonnet-4-6", 9999, 9999, "2026-04-10T10:00:00+00:00"),
        ],
    )
    conn.commit()

    total = retro._fetch_api_calls_total("robin", shipped_ts)
    assert total == 1000 + 500 + 2000 + 800  # 4300, excludes pre-ship row


def test_quantitative_compute_post_ship_returns_string(isolated_db):
    """_compute_quantitative_post_ship returns a human-readable summary for shipped quantitative."""
    from shared.state import _get_conn

    conn = _get_conn()
    conn.execute(
        "INSERT INTO api_calls (agent, model, input_tokens, output_tokens, called_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("robin", "claude-sonnet-4-6", 1000, 500, "2026-04-20T10:00:00+00:00"),
    )
    conn.commit()

    proposal = _make_proposal(
        metric_type="quantitative",
        baseline_source="api_calls where agent='robin'",
        shipped_at="2026-04-15T00:00:00+00:00",
    )
    result = retro._compute_quantitative_post_ship(proposal)

    assert result is not None
    assert "robin" in result
    assert "1,500" in result  # 1000 + 500


def test_quantitative_no_agent_name_returns_none():
    """_compute_quantitative_post_ship returns None when baseline_source has no agent name."""
    proposal = _make_proposal(
        metric_type="quantitative",
        baseline_source="some other source without agent name",
    )
    result = retro._compute_quantitative_post_ship(proposal)
    assert result is None


# ---------------------------------------------------------------------------
# Checklist: ✓/✗ grouping
# ---------------------------------------------------------------------------


def test_checklist_proposals_grouped_correctly():
    """_group_proposals_by_type correctly groups checklist proposals."""
    proposals = [
        _make_proposal("id-1", "checklist", "shipped"),
        _make_proposal("id-2", "checklist", "wontfix"),
        _make_proposal("id-3", "quantitative", "shipped"),
    ]
    groups = retro._group_proposals_by_type(proposals)
    assert len(groups["checklist"]) == 2
    assert len(groups["quantitative"]) == 1
    assert groups["human_judged"] == []


def test_build_proposals_payload_checklist_text():
    """_build_proposals_payload includes checklist proposals in checklist_text."""
    proposals = [
        _make_proposal("chk-1", "checklist", "shipped", success_metric="Deploy feature X"),
        _make_proposal("chk-2", "checklist", "wontfix", success_metric="Deploy feature Y"),
    ]
    with patch.object(retro, "_compute_quantitative_post_ship", return_value=None):
        payload = retro._build_proposals_payload(proposals)

    assert "chk-1" in payload["checklist_text"]
    assert "chk-2" in payload["checklist_text"]
    assert payload["checklist_text"] != "（無）"


# ---------------------------------------------------------------------------
# Human-judged: no fake quantitative
# ---------------------------------------------------------------------------


def test_human_judged_no_fake_quantitative(isolated_db):
    """Pipeline does NOT call mark_verified with a numeric value for human_judged proposals.

    human_judged post_ship_value must mention 'verification' not a number.
    """
    from unittest.mock import MagicMock  # noqa: PLC0415

    proposal = _make_proposal(
        "hj-1",
        "human_judged",
        "shipped",
        verification_owner="shosho",
        shipped_at="2026-04-15T00:00:00+00:00",
    )

    pipeline = retro.NewsRetrospectivePipeline(dry_run=True, slack_bot=MagicMock())

    # Use a real isolated DB with the proposal inserted
    from agents.franky.state.proposal_metrics import insert_candidate  # noqa: PLC0415
    from shared.schemas.proposal_metrics import ProposalFrontmatterV1  # noqa: PLC0415

    fm = ProposalFrontmatterV1(
        proposal_id="hj-1",
        metric_type="human_judged",
        success_metric="Human judged outcome",
        related_adr=[],
        related_issues=[],
    )
    insert_candidate(fm, week_iso="2026-w18")
    # Advance to shipped via FSM
    from agents.franky.state.proposal_metrics import (  # noqa: PLC0415
        mark_promoted,
        mark_ready,
        mark_shipped,
        mark_triaged,
    )

    mark_promoted("hj-1")
    mark_triaged("hj-1")
    mark_ready("hj-1")
    mark_shipped("hj-1", pr_url="https://github.com/test/pr/1", commit_sha="abc123")

    captured_post_ship_values: list[str] = []
    original_mark_verified = retro.mark_verified

    def _capture_mark_verified(pid: str, *, post_ship_value: str) -> dict:
        captured_post_ship_values.append(post_ship_value)
        return original_mark_verified(pid, post_ship_value=post_ship_value)

    with patch.object(retro, "mark_verified", side_effect=_capture_mark_verified):
        payload = retro._build_proposals_payload([proposal])
        pipeline._mark_shipped_verified([proposal], payload)

    # Must not contain numeric-only values — should mention 'verification'
    assert captured_post_ship_values, "mark_verified should be called for shipped proposal"
    for val in captured_post_ship_values:
        assert "verification" in val or "verif" in val.lower(), (
            f"human_judged post_ship_value must not be a fake number; got: {val!r}"
        )


def test_human_judged_build_proposals_payload():
    """_build_proposals_payload puts human_judged proposals in human_judged_text."""
    proposals = [
        _make_proposal(
            "hj-2",
            "human_judged",
            "shipped",
            verification_owner="shosho",
            success_metric="Quality improved",
        ),
    ]
    payload = retro._build_proposals_payload(proposals)
    assert "hj-2" in payload["human_judged_text"]
    assert payload["human_judged_text"] != "（無）"
    assert payload["quantitative_text"] == "（無）"
