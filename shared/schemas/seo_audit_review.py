"""SEO audit review session contracts (PRD #226 §"Schema additions").

Two shapes ride together:

    AuditSuggestionV1     — one fail/warn rule from a single audit run, plus
                            review state (status / edited_value / reviewed_at).
                            Persisted as JSON array elements inside
                            `audit_results.suggestions_json` (slice 4 onward).
    AuditReviewSessionV1  — view model bundling an audit row with its
                            list[AuditSuggestionV1]. Built by the slice 5
                            review router; not persisted directly (the
                            `audit_results` row is the SoT).

Schema rules (per `docs/principles/schemas.md` §1-§4):
- `extra="forbid"` + `frozen=True` on all persisted shapes.
- `AwareDatetime` for any `*_at` field; no naive datetimes allowed.
- `Literal` for closed enums (severity / status / overall_grade).

Why no `schema_version` field on `AuditSuggestionV1`: the parent row carries
the schema_version implicitly via its DB schema; embedding `schema_version`
on every list element would balloon the JSON for no benefit. If the
suggestion shape ever needs a backward-incompatible bump we tag the
container row instead (a future `audit_results.suggestions_schema_version`
column).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
)

from shared.schemas.publishing import TargetSite

# A / B+ / B / C+ / C / D / F — keep aligned with `audit.py::_grade` ladder
# (the audit script is the producer; we are the consumer).
OverallGrade = Literal["A", "B+", "B", "C+", "C", "D", "F"]

# Subset of `AuditCheck.status` actually persisted: `pass` / `skip` rules are
# excluded by `audit_runner.run` per PRD §"Review semantics" (per-rule cards
# only show fail/warn).
PersistedSeverity = Literal["fail", "warn"]

# Per-suggestion review state (PRD §"Review semantics" Q9).
SuggestionStatus = Literal["pending", "approved", "edited", "rejected"]

# Per audit-result lifecycle (PRD §"Audit result schema" Q8 enum).
ReviewStatus = Literal["fresh", "in_review", "exported", "archived"]


class AuditSuggestionV1(BaseModel):
    """One reviewable suggestion (a single fail/warn rule from one audit run)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str = Field(
        min_length=1,
        max_length=64,
        description="Stable rule identifier from `shared/seo_audit/*` (e.g. M1, H1, L9).",
    )
    severity: PersistedSeverity = Field(
        description="`fail` (must-fix) or `warn` (advisory). `pass`/`skip` are not persisted.",
    )
    title: str = Field(
        min_length=1,
        max_length=200,
        description="Short human-readable title (`AuditCheck.name`).",
    )
    current_value: str = Field(
        default="",
        description="What the audit observed (`AuditCheck.actual`). May be long; no max length.",
    )
    suggested_value: str = Field(
        default="",
        description=(
            "What the audit suggests (`AuditCheck.expected` or `fix_suggestion`); "
            "this is the LLM/deterministic recommendation pre-edit."
        ),
    )
    rationale: str = Field(
        default="",
        description="Why this matters — explanatory copy for the reviewer.",
    )

    # Review state (mutated by slice #234 via `audit_results_store.update_suggestion`).
    status: SuggestionStatus = Field(
        default="pending",
        description=(
            "`pending` (untouched), `approved` (use suggested_value verbatim), "
            "`edited` (use edited_value), `rejected` (skip on export)."
        ),
    )
    edited_value: Optional[str] = Field(
        default=None,
        description=(
            "User-supplied alternative to `suggested_value`. Required when status='edited'."
        ),
    )
    reviewed_at: Optional[AwareDatetime] = Field(
        default=None,
        description="When the reviewer last touched this suggestion. None until first review.",
    )


class AuditReviewSessionV1(BaseModel):
    """View model for slice #234's review page — bundles an audit run + its
    suggestions in a single typed object.

    Not persisted: read-side aggregate built from the `audit_results` row.
    Slice #234 will re-validate on POST to keep edits typed.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1

    audit_id: int = Field(
        ge=1,
        description="audit_results.id — the canonical row this session reviews.",
    )
    post_url: str = Field(
        min_length=1,
        description="The URL that was audited (`audit_results.url`).",
    )
    target_site: Optional[TargetSite] = Field(
        default=None,
        description="WP target site key, or None for external/non-WP audits.",
    )
    wp_post_id: Optional[int] = Field(
        default=None,
        description="WP post id (None for non-WP audits).",
    )
    audited_at: AwareDatetime = Field(
        description="When the audit subprocess finished (UTC).",
    )
    overall_grade: OverallGrade = Field(
        description="A / B+ / B / C+ / C / D / F per `audit.py::_grade`.",
    )
    pass_count: int = Field(ge=0)
    warn_count: int = Field(ge=0)
    fail_count: int = Field(ge=0)
    skip_count: int = Field(ge=0)
    suggestions: list[AuditSuggestionV1] = Field(
        default_factory=list,
        description="All persisted fail/warn suggestions (per PRD §6a per-rule cards).",
    )
    review_status: ReviewStatus = Field(
        default="fresh",
        description="Lifecycle stage of the audit row.",
    )
    exported_to_approval_queue: bool = Field(
        default=False,
        description="True iff `approval_queue_id` is set (slice #235 export ran).",
    )
    approval_queue_id: Optional[int] = Field(
        default=None,
        description="approval_queue.id when exported; None otherwise.",
    )


__all__ = [
    "AuditSuggestionV1",
    "AuditReviewSessionV1",
    "OverallGrade",
    "PersistedSeverity",
    "SuggestionStatus",
    "ReviewStatus",
]
