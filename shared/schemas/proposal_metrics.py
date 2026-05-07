"""Franky evolution-loop proposal lifecycle schema (ADR-023 §6).

`ProposalMetricV1` mirrors the `proposal_metrics` table row shape used by the
weekly synthesis (S3) → triage → ship → retrospective (S4) lifecycle. Schema
is frozen at v1; future column additions use a `__v2` suffix migration so old
column semantics are preserved (see ADR-023 §"Cost guard").

Schema follows docs/principles/schemas.md:
    - extra="forbid" + frozen=True
    - schema_version literal
    - Literal for status / metric_type enums (FSM SoT enforced separately
      in agents/franky/state/proposal_metrics.py)
"""

from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    constr,
)

# ---------------------------------------------------------------------------
# Enum literals — must stay in lockstep with migration 014 CHECK constraints
# and `agents/franky/state/proposal_metrics.ALLOWED_TRANSITIONS`.
# ---------------------------------------------------------------------------

ProposalStatus = Literal[
    "candidate",
    "promoted",
    "triaged",
    "ready",
    "wontfix",
    "shipped",
    "verified",
    "rejected",
]

ProposalMetricType = Literal[
    "quantitative",
    "checklist",
    "human_judged",
]


# ---------------------------------------------------------------------------
# Frontmatter input — minimum 5-key block extracted from vault page Stage 1
# or GitHub issue body before insert_candidate().
# ---------------------------------------------------------------------------

REQUIRED_FRONTMATTER_KEYS: tuple[str, ...] = (
    "proposal_id",
    "metric_type",
    "success_metric",
    "related_adr",
    "related_issues",
)


class ProposalFrontmatterV1(BaseModel):
    """The 5 mandatory keys a proposal page / issue body must declare.

    `related_adr` and `related_issues` are list[str] in the parsed form;
    they are persisted as JSON-encoded strings in the DB. Empty lists are
    allowed (a proposal may not reference any prior ADR / issue), but the
    keys themselves MUST be present in the source frontmatter — missing
    keys raise on extraction (see frontmatter_extractor.py).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    proposal_id: constr(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    metric_type: ProposalMetricType
    success_metric: constr(min_length=1, max_length=500)
    related_adr: list[str] = Field(default_factory=list)
    related_issues: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Full row schema (read path; CRUD helpers also accept this shape on insert)
# ---------------------------------------------------------------------------


class ProposalMetricV1(BaseModel):
    """Schema-validated view of a `proposal_metrics` row.

    Field ordering mirrors migration 014 / ADR-023 §6 verbatim.
    Timestamps are stored as ISO 8601 strings (matches existing state.db
    convention — see `alert_state`, `r2_backup_checks`).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1

    # Primary identity
    id: int | None = None  # filled by DB on insert
    proposal_id: constr(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    issue_number: int | None = None
    week_iso: constr(pattern=r"^\d{4}-W\d{2}$")  # e.g. '2026-W18'

    # Cross-references (stored as JSON-encoded strings in DB)
    related_adr: list[str] = Field(default_factory=list)
    related_issues: list[str] = Field(default_factory=list)

    # Metric definition
    metric_type: ProposalMetricType
    success_metric: constr(min_length=1, max_length=500)
    baseline_source: str | None = None
    baseline_value: str | None = None
    post_ship_value: str | None = None
    verification_owner: str | None = None
    try_cost_estimate: str | None = None

    # Panel signal + lifecycle status
    panel_recommended: bool = False
    status: ProposalStatus = "candidate"

    # Timestamps (ISO 8601 strings)
    created_at: str
    promoted_at: str | None = None
    triaged_at: str | None = None
    shipped_at: str | None = None
    verified_at: str | None = None

    # Ship artifacts
    related_pr: str | None = None
    related_commit: str | None = None

    # Provenance
    source_item_ids: list[str] = Field(default_factory=list)
