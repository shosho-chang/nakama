"""FSM + CRUD tests for proposal_metrics (ADR-023 §6).

Covers:
    - every legal transition fires (named helpers + generic transition)
    - every illegal transition is rejected
    - insert_candidate persists the 5-key frontmatter + lifecycle defaults
    - duplicate proposal_id rejected
    - hook stubs fire at promote / ship / verify
    - integration: full lifecycle candidate → ... → verified writes correct
      row state at each step

Tests rely on the autouse `isolated_db` fixture in tests/conftest.py to point
shared.state at a fresh tmp_path SQLite per test.
"""

from __future__ import annotations

import pytest

from agents.franky.state import proposal_metrics as pm
from agents.franky.state.proposal_metrics import (
    ALLOWED_TRANSITIONS,
    DuplicateProposalError,
    IllegalStatusTransitionError,
    ProposalNotFoundError,
)
from shared.schemas.proposal_metrics import ProposalFrontmatterV1

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fm() -> ProposalFrontmatterV1:
    return ProposalFrontmatterV1(
        proposal_id="adopt-mcp-serena",
        metric_type="checklist",
        success_metric="enable serena MCP + 3 demo queries",
        related_adr=["ADR-022"],
        related_issues=["#474"],
    )


@pytest.fixture
def inserted(fm):
    pm.insert_candidate(fm, week_iso="2026-W18", panel_recommended=True)
    return fm.proposal_id


@pytest.fixture(autouse=True)
def _clear_hooks():
    """Hooks are module-level lists; clear between tests."""
    pm.on_promote_hooks.clear()
    pm.on_ship_hooks.clear()
    pm.on_verify_hooks.clear()
    yield
    pm.on_promote_hooks.clear()
    pm.on_ship_hooks.clear()
    pm.on_verify_hooks.clear()


# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------


def test_insert_candidate_persists_frontmatter(fm):
    pm.insert_candidate(
        fm,
        week_iso="2026-W18",
        panel_recommended=True,
        baseline_source="news_synthesis",
        source_item_ids=["item-1", "item-2"],
    )
    row = pm.get(fm.proposal_id)
    assert row["status"] == "candidate"
    assert row["week_iso"] == "2026-W18"
    assert row["metric_type"] == "checklist"
    assert row["success_metric"].startswith("enable serena")
    assert row["related_adr"] == ["ADR-022"]
    assert row["related_issues"] == ["#474"]
    assert row["panel_recommended"] is True
    assert row["source_item_ids"] == ["item-1", "item-2"]
    assert row["promoted_at"] is None
    assert row["created_at"]


def test_insert_candidate_rejects_duplicate(fm):
    pm.insert_candidate(fm, week_iso="2026-W18")
    with pytest.raises(DuplicateProposalError):
        pm.insert_candidate(fm, week_iso="2026-W18")


def test_get_unknown_raises():
    with pytest.raises(ProposalNotFoundError):
        pm.get("does-not-exist")


# ---------------------------------------------------------------------------
# Legal transitions — one test per FSM edge
# ---------------------------------------------------------------------------


def test_candidate_to_promoted(inserted):
    out = pm.mark_promoted(inserted, issue_number=474)
    assert out["status"] == "promoted"
    assert out["issue_number"] == 474
    assert out["promoted_at"]


def test_candidate_to_rejected(inserted):
    out = pm.mark_rejected(inserted, reason="dup of #471")
    assert out["status"] == "rejected"
    assert "dup of #471" in out["post_ship_value"]


def test_promoted_to_triaged(inserted):
    pm.mark_promoted(inserted)
    out = pm.mark_triaged(inserted)
    assert out["status"] == "triaged"
    assert out["triaged_at"]


def test_promoted_to_rejected(inserted):
    pm.mark_promoted(inserted)
    out = pm.mark_rejected(inserted, reason="owner pushback")
    assert out["status"] == "rejected"


def test_triaged_to_ready(inserted):
    pm.mark_promoted(inserted)
    pm.mark_triaged(inserted)
    out = pm.mark_ready(inserted)
    assert out["status"] == "ready"


def test_triaged_to_wontfix(inserted):
    pm.mark_promoted(inserted)
    pm.mark_triaged(inserted)
    out = pm.mark_wontfix(inserted, reason="not aligned w/ pipeline")
    assert out["status"] == "wontfix"
    assert "not aligned" in out["baseline_source"]


def test_ready_to_shipped(inserted):
    pm.mark_promoted(inserted)
    pm.mark_triaged(inserted)
    pm.mark_ready(inserted)
    out = pm.mark_shipped(
        inserted,
        pr_url="https://github.com/x/y/pull/1",
        commit_sha="deadbeef",
    )
    assert out["status"] == "shipped"
    assert out["related_pr"].endswith("/pull/1")
    assert out["related_commit"] == "deadbeef"
    assert out["shipped_at"]


def test_ready_to_rejected(inserted):
    pm.mark_promoted(inserted)
    pm.mark_triaged(inserted)
    pm.mark_ready(inserted)
    out = pm.mark_rejected(inserted, reason="scope creep")
    assert out["status"] == "rejected"


def test_shipped_to_verified(inserted):
    pm.mark_promoted(inserted)
    pm.mark_triaged(inserted)
    pm.mark_ready(inserted)
    pm.mark_shipped(inserted, pr_url="pr", commit_sha="abc")
    out = pm.mark_verified(inserted, post_ship_value="3 demos passed")
    assert out["status"] == "verified"
    assert out["post_ship_value"] == "3 demos passed"
    assert out["verified_at"]


def test_shipped_to_rejected(inserted):
    pm.mark_promoted(inserted)
    pm.mark_triaged(inserted)
    pm.mark_ready(inserted)
    pm.mark_shipped(inserted, pr_url="pr", commit_sha="abc")
    out = pm.mark_rejected(inserted, reason="reverted post-ship")
    assert out["status"] == "rejected"


# ---------------------------------------------------------------------------
# Illegal transitions
# ---------------------------------------------------------------------------


def test_candidate_cannot_skip_to_shipped(inserted):
    with pytest.raises(IllegalStatusTransitionError):
        pm.mark_shipped(inserted, pr_url="pr", commit_sha="abc")


def test_terminal_wontfix_is_truly_terminal(inserted):
    pm.mark_promoted(inserted)
    pm.mark_triaged(inserted)
    pm.mark_wontfix(inserted, reason="nope")
    with pytest.raises(IllegalStatusTransitionError):
        pm.mark_rejected(inserted, reason="changed my mind")
    with pytest.raises(IllegalStatusTransitionError):
        pm.transition(inserted, "ready")


def test_terminal_verified_is_truly_terminal(inserted):
    pm.mark_promoted(inserted)
    pm.mark_triaged(inserted)
    pm.mark_ready(inserted)
    pm.mark_shipped(inserted, pr_url="pr", commit_sha="abc")
    pm.mark_verified(inserted, post_ship_value="ok")
    with pytest.raises(IllegalStatusTransitionError):
        pm.mark_rejected(inserted, reason="actually no")


def test_candidate_to_triaged_blocked(inserted):
    # Must go through promoted first.
    with pytest.raises(IllegalStatusTransitionError):
        pm.mark_triaged(inserted)


def test_transition_unknown_proposal_raises():
    with pytest.raises(ProposalNotFoundError):
        pm.transition("ghost", "promoted")


# ---------------------------------------------------------------------------
# FSM SoT integrity
# ---------------------------------------------------------------------------


def test_allowed_transitions_matches_adr_022_section_6():
    # Per ADR-023 §6 narrative:
    # candidate → promoted → triaged → ready|wontfix → shipped → verified|rejected
    # Plus reject is reachable as a defensive escape from non-terminal states.
    assert ALLOWED_TRANSITIONS["candidate"] == {"promoted", "rejected"}
    assert ALLOWED_TRANSITIONS["promoted"] == {"triaged", "rejected"}
    assert ALLOWED_TRANSITIONS["triaged"] == {"ready", "wontfix"}
    assert ALLOWED_TRANSITIONS["ready"] == {"shipped", "rejected"}
    assert ALLOWED_TRANSITIONS["shipped"] == {"verified", "rejected"}
    # Terminals
    for terminal in ("wontfix", "verified", "rejected"):
        assert ALLOWED_TRANSITIONS[terminal] == set()


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def test_hooks_fire_at_promote_ship_verify(inserted):
    promoted_rows: list = []
    shipped_rows: list = []
    verified_rows: list = []
    pm.on_promote_hooks.append(promoted_rows.append)
    pm.on_ship_hooks.append(shipped_rows.append)
    pm.on_verify_hooks.append(verified_rows.append)

    pm.mark_promoted(inserted, issue_number=474)
    pm.mark_triaged(inserted)
    pm.mark_ready(inserted)
    pm.mark_shipped(inserted, pr_url="pr", commit_sha="abc")
    pm.mark_verified(inserted, post_ship_value="ok")

    assert len(promoted_rows) == 1 and promoted_rows[0]["status"] == "promoted"
    assert len(shipped_rows) == 1 and shipped_rows[0]["status"] == "shipped"
    assert len(verified_rows) == 1 and verified_rows[0]["status"] == "verified"


def test_hook_failure_does_not_break_transition(inserted):
    def boom(_row):
        raise RuntimeError("hook is broken")
    pm.on_promote_hooks.append(boom)
    out = pm.mark_promoted(inserted)
    # Transition still committed even though the hook raised.
    assert out["status"] == "promoted"


# ---------------------------------------------------------------------------
# list_by_status
# ---------------------------------------------------------------------------


def test_list_by_status(fm):
    pm.insert_candidate(fm, week_iso="2026-W18")
    other = ProposalFrontmatterV1(
        proposal_id="other-prop",
        metric_type="quantitative",
        success_metric="latency drop 10%",
        related_adr=[],
        related_issues=[],
    )
    pm.insert_candidate(other, week_iso="2026-W18")
    pm.mark_promoted("other-prop")

    candidates = pm.list_by_status("candidate")
    assert [r["proposal_id"] for r in candidates] == ["adopt-mcp-serena"]
    promoted = pm.list_by_status("promoted")
    assert [r["proposal_id"] for r in promoted] == ["other-prop"]


# ---------------------------------------------------------------------------
# Integration: full lifecycle, assert state at every step
# ---------------------------------------------------------------------------


def test_full_lifecycle_candidate_to_verified(fm):
    pm.insert_candidate(
        fm,
        week_iso="2026-W18",
        panel_recommended=True,
        baseline_source="news_synthesis",
        baseline_value="42 tokens/req",
        verification_owner="shosho-chang",
        try_cost_estimate="$0.50/run",
        source_item_ids=["item-1", "item-2"],
    )

    row = pm.get(fm.proposal_id)
    assert row["status"] == "candidate"
    assert row["promoted_at"] is None
    assert row["triaged_at"] is None
    assert row["shipped_at"] is None
    assert row["verified_at"] is None

    pm.mark_promoted(fm.proposal_id, issue_number=474)
    row = pm.get(fm.proposal_id)
    assert row["status"] == "promoted"
    assert row["issue_number"] == 474
    assert row["promoted_at"]

    pm.mark_triaged(fm.proposal_id)
    row = pm.get(fm.proposal_id)
    assert row["status"] == "triaged"
    assert row["triaged_at"]

    pm.mark_ready(fm.proposal_id)
    row = pm.get(fm.proposal_id)
    assert row["status"] == "ready"

    pm.mark_shipped(
        fm.proposal_id,
        pr_url="https://github.com/shosho-chang/nakama/pull/999",
        commit_sha="cafef00d",
    )
    row = pm.get(fm.proposal_id)
    assert row["status"] == "shipped"
    assert row["shipped_at"]
    assert row["related_pr"].endswith("/pull/999")
    assert row["related_commit"] == "cafef00d"

    pm.mark_verified(fm.proposal_id, post_ship_value="38 tokens/req (-10%)")
    row = pm.get(fm.proposal_id)
    assert row["status"] == "verified"
    assert row["verified_at"]
    assert row["post_ship_value"] == "38 tokens/req (-10%)"

    # Baseline + verification context preserved through every step.
    assert row["baseline_source"] == "news_synthesis"
    assert row["baseline_value"] == "42 tokens/req"
    assert row["verification_owner"] == "shosho-chang"
    assert row["try_cost_estimate"] == "$0.50/run"
    assert row["panel_recommended"] is True
    assert row["source_item_ids"] == ["item-1", "item-2"]
