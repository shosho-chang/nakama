"""Schema tests for ProposalMetricV1 / ProposalFrontmatterV1 (ADR-023 §6)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.schemas.proposal_metrics import (
    REQUIRED_FRONTMATTER_KEYS,
    ProposalFrontmatterV1,
    ProposalMetricV1,
)


# ---------------------------------------------------------------------------
# Frontmatter
# ---------------------------------------------------------------------------


def test_required_frontmatter_keys_match_spec():
    # ADR-023 §6 mandates exactly these 5 keys; lock the constant down.
    assert REQUIRED_FRONTMATTER_KEYS == (
        "proposal_id",
        "metric_type",
        "success_metric",
        "related_adr",
        "related_issues",
    )


def test_frontmatter_minimal_valid():
    fm = ProposalFrontmatterV1(
        proposal_id="adopt-mcp-serena",
        metric_type="checklist",
        success_metric="enable serena MCP and run 3 demo queries",
        related_adr=[],
        related_issues=[],
    )
    assert fm.proposal_id == "adopt-mcp-serena"
    assert fm.related_adr == []


def test_frontmatter_rejects_unknown_metric_type():
    with pytest.raises(ValidationError):
        ProposalFrontmatterV1(
            proposal_id="x",
            metric_type="invented",  # type: ignore[arg-type]
            success_metric="...",
            related_adr=[],
            related_issues=[],
        )


def test_frontmatter_rejects_bad_proposal_id_slug():
    with pytest.raises(ValidationError):
        ProposalFrontmatterV1(
            proposal_id="Has Spaces!",
            metric_type="checklist",
            success_metric="...",
            related_adr=[],
            related_issues=[],
        )


def test_frontmatter_extra_keys_forbidden():
    with pytest.raises(ValidationError):
        ProposalFrontmatterV1(
            proposal_id="ok-slug",
            metric_type="checklist",
            success_metric="...",
            related_adr=[],
            related_issues=[],
            unknown_field="boom",  # type: ignore[call-arg]
        )


# ---------------------------------------------------------------------------
# Full row schema
# ---------------------------------------------------------------------------


def test_proposal_metric_defaults():
    m = ProposalMetricV1(
        proposal_id="prop-1",
        week_iso="2026-W18",
        metric_type="quantitative",
        success_metric="tokens/req drops 20%",
        created_at="2026-05-07T10:00:00+00:00",
    )
    assert m.schema_version == 1
    assert m.status == "candidate"
    assert m.panel_recommended is False
    assert m.related_adr == []


def test_proposal_metric_week_iso_pattern():
    with pytest.raises(ValidationError):
        ProposalMetricV1(
            proposal_id="prop-1",
            week_iso="2026-18",  # missing the W prefix
            metric_type="quantitative",
            success_metric="x",
            created_at="2026-05-07T10:00:00+00:00",
        )


def test_proposal_metric_status_enum_strict():
    with pytest.raises(ValidationError):
        ProposalMetricV1(
            proposal_id="prop-1",
            week_iso="2026-W18",
            metric_type="quantitative",
            success_metric="x",
            created_at="2026-05-07T10:00:00+00:00",
            status="invented",  # type: ignore[arg-type]
        )
