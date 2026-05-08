"""Integration tests for agents/franky/news_retrospective.py (ADR-023 §7 S4).

Integration scenario: tmpdir state.db with 5 mock proposals (ship/wontfix/
verified/candidate/checklist) → run retrospective → assert:
  - vault page written with correct month label
  - Slack DM sent
  - proposal_metrics rows transitioned correctly (shipped → verified)
  - quantitative shipped proposal gets mark_verified called
  - human_judged shipped proposal does NOT fake a quantitative value
  - wontfix proposals remain untouched (terminal state)
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

import agents.franky.news_retrospective as retro
from agents.franky.state.proposal_metrics import (
    get as get_proposal,
)
from agents.franky.state.proposal_metrics import (
    insert_candidate,
    mark_promoted,
    mark_ready,
    mark_shipped,
    mark_triaged,
    mark_wontfix,
)
from shared.schemas.proposal_metrics import ProposalFrontmatterV1

_TAIPEI = ZoneInfo("Asia/Taipei")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_proposal(
    proposal_id: str,
    metric_type: str,
    *,
    week_iso: str = "2026-w17",
    baseline_source: str | None = None,
    verification_owner: str | None = None,
) -> None:
    fm = ProposalFrontmatterV1(
        proposal_id=proposal_id,
        metric_type=metric_type,
        success_metric=f"Success metric for {proposal_id}",
        related_adr=[],
        related_issues=[],
    )
    insert_candidate(
        fm,
        week_iso=week_iso,
        baseline_source=baseline_source,
        verification_owner=verification_owner,
    )


def _advance_to_shipped(proposal_id: str) -> None:
    mark_promoted(proposal_id)
    mark_triaged(proposal_id)
    mark_ready(proposal_id)
    mark_shipped(
        proposal_id,
        pr_url="https://github.com/test/pull/1",
        commit_sha="abc123",
    )


def _advance_to_wontfix(proposal_id: str) -> None:
    mark_promoted(proposal_id)
    mark_triaged(proposal_id)
    mark_wontfix(proposal_id, reason="Not worth pursuing")


# ---------------------------------------------------------------------------
# 5-proposal integration test
# ---------------------------------------------------------------------------


@pytest.fixture()
def five_proposals(isolated_db):
    """Set up 5 proposals in various states, all created in April 2026.

    The isolated_db fixture provides a clean tmp db; we use monkeypatched
    created_at values by inserting at a known timestamp and then directly
    writing into the db via connection for the month filter to work.

    Proposals:
      q-1:  quantitative, shipped (with robin api_calls data)
      c-1:  checklist, shipped
      hj-1: human_judged, shipped, verification_owner=shosho
      w-1:  checklist, wontfix (terminal — no transition expected)
      ca-1: checklist, candidate (still in flight — no transition expected)
    """
    from shared.state import _get_conn

    conn = _get_conn()

    # Insert proposals via insert_candidate (creates rows with current timestamp).
    # Then patch created_at to an April 2026 date so list_for_month(2026, 4) finds them.
    _insert_proposal("q-1", "quantitative", baseline_source="api_calls where agent='robin'")
    _insert_proposal("c-1", "checklist")
    _insert_proposal("hj-1", "human_judged", verification_owner="shosho")
    _insert_proposal("w-1", "checklist")
    _insert_proposal("ca-1", "checklist")

    # Backdate all created_at to April 2026
    conn.execute(
        "UPDATE proposal_metrics SET created_at = ? WHERE proposal_id IN (?,?,?,?,?)",
        (
            "2026-04-15T10:00:00+00:00",
            "q-1",
            "c-1",
            "hj-1",
            "w-1",
            "ca-1",
        ),
    )
    conn.commit()

    # Insert api_calls data for robin (for quantitative verification)
    conn.execute(
        "INSERT INTO api_calls (agent, model, input_tokens, output_tokens, called_at)"
        " VALUES (?, ?, ?, ?, ?)",
        ("robin", "claude-sonnet-4-6", 5000, 2000, "2026-04-20T10:00:00+00:00"),
    )
    conn.commit()

    # Advance lifecycle states
    _advance_to_shipped("q-1")
    _advance_to_shipped("c-1")
    _advance_to_shipped("hj-1")
    _advance_to_wontfix("w-1")
    # ca-1 stays as candidate

    # Backdate shipped_at for quantitative to before the api_calls row
    conn.execute(
        "UPDATE proposal_metrics SET shipped_at = ? WHERE proposal_id = ?",
        ("2026-04-18T00:00:00+00:00", "q-1"),
    )
    conn.commit()

    return conn


def test_integration_retrospective_vault_written(five_proposals, monkeypatch):
    """Retrospective writes a vault page for April 2026."""
    write_mock = MagicMock()
    monkeypatch.setattr(retro, "write_page", write_mock)
    monkeypatch.setattr(retro, "append_to_file", MagicMock())
    monkeypatch.setattr(retro.llm, "ask", lambda *a, **kw: "## LLM retrospective output")

    # Run on May 31, 2026 (last Sunday of May) → last month = April 2026
    now = datetime(2026, 5, 31, 22, 0, 0, tzinfo=_TAIPEI)
    pipeline = retro.NewsRetrospectivePipeline(
        dry_run=False, no_publish=True, slack_bot=MagicMock(), now=now
    )
    pipeline.run()

    assert write_mock.called
    call_args = write_mock.call_args
    vault_path = call_args.args[0] if call_args.args else call_args.kwargs.get("relative_path")
    assert vault_path == "KB/Wiki/Digests/AI/Retrospective-2026-04.md"


def test_integration_retrospective_slack_sent(five_proposals, monkeypatch):
    """Retrospective sends Slack DM with funnel stats and metric_type distribution."""
    monkeypatch.setattr(retro, "write_page", MagicMock())
    monkeypatch.setattr(retro, "append_to_file", MagicMock())
    monkeypatch.setattr(retro.llm, "ask", lambda *a, **kw: "## LLM output")

    slack_mock = MagicMock()
    slack_mock.post_plain.return_value = "ts-123"

    now = datetime(2026, 5, 31, 22, 0, 0, tzinfo=_TAIPEI)
    pipeline = retro.NewsRetrospectivePipeline(
        dry_run=False, no_publish=False, slack_bot=slack_mock, now=now
    )
    pipeline.run()

    slack_mock.post_plain.assert_called_once()
    call_kwargs = slack_mock.post_plain.call_args
    text = call_kwargs.args[0] if call_kwargs.args else ""
    assert "2026-04" in text
    assert "proposals" in text


def test_integration_quantitative_shipped_gets_mark_verified(five_proposals, monkeypatch):
    """Shipped quantitative proposal gets mark_verified called with api_calls data."""
    monkeypatch.setattr(retro, "write_page", MagicMock())
    monkeypatch.setattr(retro, "append_to_file", MagicMock())
    monkeypatch.setattr(retro.llm, "ask", lambda *a, **kw: "## LLM output")

    now = datetime(2026, 5, 31, 22, 0, 0, tzinfo=_TAIPEI)
    pipeline = retro.NewsRetrospectivePipeline(
        dry_run=False, no_publish=True, slack_bot=MagicMock(), now=now
    )
    pipeline.run()

    # q-1 should now be 'verified'
    row = get_proposal("q-1")
    assert row["status"] == "verified"
    # post_ship_value should mention robin and token count
    assert row["post_ship_value"] is not None
    assert "robin" in row["post_ship_value"]


def test_integration_checklist_shipped_gets_mark_verified(five_proposals, monkeypatch):
    """Shipped checklist proposal gets mark_verified with logged outcome."""
    monkeypatch.setattr(retro, "write_page", MagicMock())
    monkeypatch.setattr(retro, "append_to_file", MagicMock())
    monkeypatch.setattr(retro.llm, "ask", lambda *a, **kw: "## LLM output")

    now = datetime(2026, 5, 31, 22, 0, 0, tzinfo=_TAIPEI)
    pipeline = retro.NewsRetrospectivePipeline(
        dry_run=False, no_publish=True, slack_bot=MagicMock(), now=now
    )
    pipeline.run()

    row = get_proposal("c-1")
    assert row["status"] == "verified"


def test_integration_human_judged_no_fake_quantitative(five_proposals, monkeypatch):
    """Shipped human_judged proposal gets mark_verified but with a non-numeric value."""
    monkeypatch.setattr(retro, "write_page", MagicMock())
    monkeypatch.setattr(retro, "append_to_file", MagicMock())
    monkeypatch.setattr(retro.llm, "ask", lambda *a, **kw: "## LLM output")

    now = datetime(2026, 5, 31, 22, 0, 0, tzinfo=_TAIPEI)
    pipeline = retro.NewsRetrospectivePipeline(
        dry_run=False, no_publish=True, slack_bot=MagicMock(), now=now
    )
    pipeline.run()

    row = get_proposal("hj-1")
    assert row["status"] == "verified"
    # Must not be a fake numeric value — must mention verification
    post_ship = row["post_ship_value"] or ""
    assert "verification" in post_ship.lower() or "verif" in post_ship.lower()
    # Must NOT be a pure number
    import re

    assert not re.fullmatch(r"\d[\d,\.]+", post_ship.strip())


def test_integration_wontfix_stays_terminal(five_proposals, monkeypatch):
    """Wontfix proposals remain in wontfix status (terminal state — no transition)."""
    monkeypatch.setattr(retro, "write_page", MagicMock())
    monkeypatch.setattr(retro, "append_to_file", MagicMock())
    monkeypatch.setattr(retro.llm, "ask", lambda *a, **kw: "## LLM output")

    now = datetime(2026, 5, 31, 22, 0, 0, tzinfo=_TAIPEI)
    pipeline = retro.NewsRetrospectivePipeline(
        dry_run=False, no_publish=True, slack_bot=MagicMock(), now=now
    )
    pipeline.run()

    row = get_proposal("w-1")
    assert row["status"] == "wontfix"


def test_integration_candidate_stays_candidate(five_proposals, monkeypatch):
    """In-flight candidate proposals are not modified by retrospective."""
    monkeypatch.setattr(retro, "write_page", MagicMock())
    monkeypatch.setattr(retro, "append_to_file", MagicMock())
    monkeypatch.setattr(retro.llm, "ask", lambda *a, **kw: "## LLM output")

    now = datetime(2026, 5, 31, 22, 0, 0, tzinfo=_TAIPEI)
    pipeline = retro.NewsRetrospectivePipeline(
        dry_run=False, no_publish=True, slack_bot=MagicMock(), now=now
    )
    pipeline.run()

    row = get_proposal("ca-1")
    assert row["status"] == "candidate"


def test_integration_dry_run_skips_vault_and_db(five_proposals, monkeypatch):
    """dry_run=True: vault not written, DB not modified, Slack not sent."""
    write_mock = MagicMock()
    monkeypatch.setattr(retro, "write_page", write_mock)
    monkeypatch.setattr(retro, "append_to_file", MagicMock())
    monkeypatch.setattr(retro.llm, "ask", lambda *a, **kw: "## LLM output")

    now = datetime(2026, 5, 31, 22, 0, 0, tzinfo=_TAIPEI)
    pipeline = retro.NewsRetrospectivePipeline(
        dry_run=True, no_publish=True, slack_bot=MagicMock(), now=now
    )
    pipeline.run()

    write_mock.assert_not_called()
    # Shipped proposals should NOT be verified in dry_run mode
    assert get_proposal("q-1")["status"] == "shipped"
    assert get_proposal("c-1")["status"] == "shipped"


def test_integration_no_proposals_returns_early(isolated_db, monkeypatch):
    """Empty month returns early without calling LLM or writing vault."""
    write_mock = MagicMock()
    llm_mock = MagicMock()
    monkeypatch.setattr(retro, "write_page", write_mock)
    monkeypatch.setattr(retro.llm, "ask", llm_mock)

    now = datetime(2026, 5, 31, 22, 0, 0, tzinfo=_TAIPEI)
    pipeline = retro.NewsRetrospectivePipeline(
        dry_run=False, no_publish=True, slack_bot=MagicMock(), now=now
    )
    result = pipeline.run()

    assert "無 proposal" in result or "略過" in result
    write_mock.assert_not_called()
    llm_mock.assert_not_called()
