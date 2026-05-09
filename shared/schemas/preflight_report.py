"""Promotion Preflight report schema (ADR-024 Slice 3 / issue #511).

Pure pydantic value-object describing a deterministic preflight inspection of
one normalized Reading Source (#509). The report is consumed by downstream
slices (#513 source map, #514 concept promotion, #515 commit gate, #516
review UI) to decide whether full promotion analysis is worth queuing.

Slice 3 is schema + service. Persistence, replay, and commit semantics are
owned by #515. LLM recommendation generation is owned by #513 / #514.

Closed-set extension protocol (mirrors #509 N6 / #512 contract): every
``Literal`` enum is frozen for ``schema_version=1``. Adding a new member
requires (a) bumping ``schema_version`` on ``PreflightReport``, (b) updating
this docstring + the enum docstring, (c) updating downstream policy in #513
/ #514 / #515 / #516. Silent extension is forbidden.

Hard invariants (Pydantic-enforced):

- ``recommended_action == "proceed_full_promotion"`` ⇒ ``has_evidence_track == True``
  (evidence-mandatory commit; missing evidence MUST default to ``defer`` or
  ``annotation_only_sync`` per ADR-024 + ``agents/robin/CONTEXT.md`` §
  Source Promotion).

The action ``partial_promotion_only`` is intentionally absent from
``PreflightAction`` — that state requires an explicit human override / waiver
and is owned by #515 Commit Gate / #516 Review UI, not by deterministic
preflight (per N511 Brief Correction 1).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PreflightAction = Literal[
    "proceed_full_promotion",
    "proceed_with_warnings",
    "annotation_only_sync",
    "defer",
    "skip",
]
"""Closed for ``schema_version=1``.

- ``proceed_full_promotion`` : ``has_evidence_track=True``, no high-severity
  risks. Downstream may queue full promotion analysis.
- ``proceed_with_warnings``  : ``has_evidence_track=True``, ≥1 medium-severity
  risk. Downstream queues with operator visibility on the warning codes.
- ``annotation_only_sync``   : ``has_evidence_track=False`` and content is
  short / structurally weak. Reader Overlay (#510) sync runs; promotion-side
  produces nothing.
- ``defer``                  : ``has_evidence_track=False`` AND
  moderate-to-large content; OR ``has_evidence_track=True`` with
  high-severity risks; OR low-confidence signals; OR inspector error.
  Wait for upstream resolution (upload original, fix OCR) before re-running.
- ``skip``                   : irrelevant content (e.g. <200 words).

NOTE: ``partial_promotion_only`` is intentionally absent. That state requires
an explicit human override / waiver and is owned by #515 Commit Gate / #516
Review UI, not by deterministic preflight.
"""

PreflightReason = Literal[
    "missing_evidence_track",
    "low_confidence_lang",
    "weak_toc",
    "ocr_artifact_suspected",
    "mixed_language_suspected",
    "very_short",
    "very_long",
    "no_chapters_detected",
    "frontmatter_minimal",
    "ok",
]
"""Closed for ``schema_version=1``. Multiple reasons may co-exist on one
report. ``ok`` is reserved for the all-clear ``proceed_full_promotion`` case.
"""

PreflightRiskCode = Literal[
    "weak_toc",
    "ocr_artifact",
    "mixed_language",
    "missing_evidence",
    "low_signal_count",
    "frontmatter_minimal",
    "other",
]
"""Closed for ``schema_version=1``. Mirrors a subset of #512's ``RiskCode``
(``duplicate_concept`` / ``cross_lingual_uncertain`` are post-promotion
review concerns, not preflight)."""

PreflightRiskSeverity = Literal["low", "medium", "high"]
"""Closed for ``schema_version=1``. Mirrors #512's ``RiskSeverity``."""


class PreflightSizeSummary(BaseModel):
    """Approximate size signals from a single inspected variant. All counts
    are rough — preflight uses cheap whitespace tokenization, not NLP.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    chapter_count: int = Field(ge=0)
    word_count_estimate: int = Field(ge=0)
    char_count_estimate: int = Field(ge=0)
    rough_token_estimate: int = Field(ge=0)
    """``char_count_estimate // 4`` heuristic. No tokenizer dependency in this
    slice (see Brief §6 boundary 11)."""


class PreflightRiskFlag(BaseModel):
    """One structural risk surfaced by the inspector."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: PreflightRiskCode
    severity: PreflightRiskSeverity
    description: str


class PreflightReport(BaseModel):
    """Per-source preflight result. Frozen value-object — emit a new report
    on re-run; do not mutate.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1

    source_id: str
    """Mirrors ``ReadingSource.source_id``. Transport string only — preflight
    NEVER parses it (per #509 N3 contract; see Brief §6 boundary 19).
    """

    primary_lang: str
    """Mirrors ``ReadingSource.primary_lang``."""

    primary_lang_confidence: Literal["high", "low"]
    """``low`` when ``ReadingSource.evidence_reason == "bilingual_only_inbox"``
    (per #509 NB2 / Q2 contract — translator's ``lang:`` is not pinned in case
    (b)); ``high`` otherwise."""

    has_evidence_track: bool
    """Mirrors ``ReadingSource.has_evidence_track``."""

    evidence_reason: str | None
    """Mirrors ``ReadingSource.evidence_reason``. ``None`` when
    ``has_evidence_track=True``."""

    size: PreflightSizeSummary
    risks: list[PreflightRiskFlag] = Field(default_factory=list)
    reasons: list[PreflightReason] = Field(default_factory=list)
    recommended_action: PreflightAction
    error: str | None = None
    """Set when an inspector failed (IO error, malformed blob); in that case
    ``recommended_action`` falls back to ``defer``. ``None`` on success."""

    @model_validator(mode="after")
    def _hard_invariant_full_promotion_requires_evidence(self) -> PreflightReport:
        if self.recommended_action == "proceed_full_promotion" and not self.has_evidence_track:
            raise ValueError(
                "recommended_action='proceed_full_promotion' requires "
                "has_evidence_track=True; missing evidence must default to "
                "'defer' or 'annotation_only_sync'"
            )
        return self
