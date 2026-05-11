"""Tests for panel_recommended deterministic positive list (ADR-023 §5)."""

from __future__ import annotations

from agents.franky import news_synthesis as ns


def _cand(description="", related_adr=None, related_issues=None, title="Test"):
    return {
        "proposal_id": "franky-proposal-2026-w18-1",
        "title": title,
        "pattern_type": "trend",
        "description": description,
        "metric_type": "checklist",
        "success_metric": "Success",
        "related_adr": related_adr or [],
        "related_issues": related_issues or [],
        "try_cost_estimate": "$1 + 1hr",
        "panel_recommended_reasons": [],
        "supporting_item_ids": ["2026-05-06-1", "2026-05-07-1"],
        "direct_issue_mapping": None,
        "direct_adr_mapping": None,
    }


# ---------------------------------------------------------------------------
# Trigger 1: mentions accepted ADR number
# ---------------------------------------------------------------------------


def test_panel_recommended_mentions_adr_number_in_description():
    cand = _cand(description="This ADR-020 assumption may be invalidated by new BGE-M4.")
    result = ns._apply_panel_recommended(cand)
    assert result["panel_recommended"] is True


def test_panel_recommended_adr_in_related_adr_field():
    cand = _cand(related_adr=["ADR-016"])
    result = ns._apply_panel_recommended(cand)
    assert result["panel_recommended"] is True


def test_panel_recommended_adr_in_direct_adr_mapping():
    cand = dict(_cand(), direct_adr_mapping="ADR-023")
    result = ns._apply_panel_recommended(cand)
    assert result["panel_recommended"] is True


# ---------------------------------------------------------------------------
# Trigger 2: involves changing agent public contract
# ---------------------------------------------------------------------------


def test_panel_recommended_agent_contract_change():
    cand = _cand(description="We should change agents/robin/__main__ to add new subcommand.")
    result = ns._apply_panel_recommended(cand)
    assert result["panel_recommended"] is True


def test_panel_recommended_shared_api_change():
    cand = _cand(description="Update shared API for llm.ask to support streaming.")
    result = ns._apply_panel_recommended(cand)
    assert result["panel_recommended"] is True


# ---------------------------------------------------------------------------
# Trigger 3: introduces new persistent dependency
# ---------------------------------------------------------------------------


def test_panel_recommended_new_pyproject_dep():
    cand = _cand(description="Add sentence-transformers to pyproject.toml for local reranking.")
    result = ns._apply_panel_recommended(cand)
    assert result["panel_recommended"] is True


def test_panel_recommended_requirements_mention():
    cand = _cand(description="We need to add this to requirements.txt.")
    result = ns._apply_panel_recommended(cand)
    assert result["panel_recommended"] is True


# ---------------------------------------------------------------------------
# Trigger 4: changes storage schema (state.db migration)
# ---------------------------------------------------------------------------


def test_panel_recommended_storage_schema_change():
    cand = _cand(description="Add column to state.db for tracking retry counts.")
    result = ns._apply_panel_recommended(cand)
    assert result["panel_recommended"] is True


def test_panel_recommended_migration_mention():
    cand = _cand(description="Requires a new migration for the proposal_metrics table.")
    result = ns._apply_panel_recommended(cand)
    assert result["panel_recommended"] is True


# ---------------------------------------------------------------------------
# Trigger 5: changes HITL boundary
# ---------------------------------------------------------------------------


def test_panel_recommended_hitl_change():
    cand = _cand(description="This would change the ADR-006 approval queue behavior.")
    result = ns._apply_panel_recommended(cand)
    assert result["panel_recommended"] is True


# ---------------------------------------------------------------------------
# Trigger 6: changes Slack/GitHub automation permissions
# ---------------------------------------------------------------------------


def test_panel_recommended_slack_permission_change():
    cand = _cand(description="Franky needs Slack permission to read messages, not just write.")
    result = ns._apply_panel_recommended(cand)
    assert result["panel_recommended"] is True


def test_panel_recommended_github_automation_change():
    cand = _cand(description="Add GitHub Actions workflow that auto-triages issues.")
    result = ns._apply_panel_recommended(cand)
    assert result["panel_recommended"] is True


# ---------------------------------------------------------------------------
# No trigger: LLM determines no (list not triggered)
# ---------------------------------------------------------------------------


def test_panel_recommended_no_trigger_returns_false():
    cand = _cand(
        description="Consider trying a new CSS framework for the blog front-end.",
        related_adr=[],
    )
    result = ns._apply_panel_recommended(cand)
    assert result["panel_recommended"] is False


def test_panel_recommended_preserves_llm_reasons():
    """LLM-supplied reasons in the input are preserved in the output."""
    cand = _cand(
        description="Just a tool comparison, no architectural impact.",
    )
    cand["panel_recommended_reasons"] = ["LLM said interesting"]
    result = ns._apply_panel_recommended(cand)
    # Whether triggered or not, pre-existing LLM reasons are preserved
    assert "LLM said interesting" in result["panel_recommended_reasons"]


# ---------------------------------------------------------------------------
# LLM cannot override list trigger
# ---------------------------------------------------------------------------


def test_panel_recommended_list_trigger_overrides_llm_no():
    """If list triggers, panel_recommended is True regardless of LLM input reasons."""
    cand = _cand(description="Change agents/brook/__main__ contract significantly.")
    # Even if panel_recommended_reasons is empty (LLM didn't add any), list match forces yes
    result = ns._apply_panel_recommended(cand)
    assert result["panel_recommended"] is True
