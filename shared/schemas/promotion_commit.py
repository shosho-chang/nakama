"""Promotion Commit + Acceptance Gate schemas (ADR-024 Slice 7 / issue #515).

Pure pydantic value-objects describing the deterministic commit pipeline's
acceptance gate findings, per-item gate results, and final commit outcome.
Service output (``CommitOutcome``) is consumed by downstream slices (#516
review UI, #517 Reading Context Package).

Slice 7 is service + schema. Manifest assembly, LLM recommendation, route
handlers, and Review UI live in upstream / downstream slices.

Closed-set extension protocol (mirrors #509 N6 / #511 / #512 / #513 / #514):
every ``Literal`` enum is frozen for ``schema_version=1``. Adding a new
member requires (a) bumping ``schema_version`` on ``CommitOutcome``, (b)
updating this docstring + the Literal docstring, (c) updating downstream
policy in #516 / #517. Silent extension is forbidden.

``AcceptanceFindingCode`` is closed for ``schema_version=1``. Extending the
finding vocabulary (e.g. adding a new gate rule) requires bumping
schema_version and coordinating with #515 service callers.

Hard invariants enforced on this schema (Pydantic ``model_validator``):

- F1-analog (CommitOutcome): ``error is not None`` ⇒
  ``batch.approved_item_ids == []`` AND ``batch.promotion_status == "failed"``.
  Systemic commit failures (vault root invalid, write_adapter raised on
  every item) MUST surface as zero-approved + failed-status; downstream
  slices (#516-#517) MUST NOT consume an error+approved-non-empty
  combination. Mirrors #511 F1 / #513 / #514 patterns.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.schemas.promotion_manifest import CommitBatch

AcceptanceFindingCode = Literal[
    "target_kb_path_missing",
    "target_kb_path_outside_vault",
    "target_kb_path_traversal",
    "evidence_anchor_locator_invalid",
    "evidence_anchor_excerpt_empty",
    "human_decision_missing",
    "human_decision_not_approve",
    "duplicate_target_in_batch",
    "concept_canonical_match_path_invalid",
    "hash_mismatch_pre_write",
]
"""Closed for ``schema_version=1``. Each finding code corresponds to one
G1-G7 invariant (Brief §4.3) the gate enforces as a defense-in-depth check.

- ``target_kb_path_missing``                  : item missing ``target_kb_path``.
- ``target_kb_path_outside_vault``            : absolute path outside vault_root.
- ``target_kb_path_traversal``                : ``..`` segment escape attempt.
- ``evidence_anchor_locator_invalid``         : ``EvidenceAnchor.locator`` empty / whitespace-only.
- ``evidence_anchor_excerpt_empty``           : ``EvidenceAnchor.excerpt`` empty / whitespace-only.
- ``human_decision_missing``                  : item lacks ``human_decision``.
- ``human_decision_not_approve``              : ``human_decision.decision != "approve"``.
- ``duplicate_target_in_batch``               : two requested items resolve to the
                                                same target_kb_path within the batch.
- ``concept_canonical_match_path_invalid``    : concept item with non-``none``
                                                match_basis lacks
                                                ``matched_concept_path``
                                                (#512 V10 defense in depth).
- ``hash_mismatch_pre_write``                 : pre-write hash differs from prior
                                                batch's recorded ``after_hash``
                                                for an update operation.

Extension requires schema_version bump + downstream policy update in #516 / #517.
"""

AcceptanceSeverity = Literal["error", "warning"]
"""Closed for ``schema_version=1``. ``error`` blocks the item from commit
(passed=False); ``warning`` is informational (passed remains True if no
``error`` finding present)."""


class AcceptanceFinding(BaseModel):
    """One gate finding for a single review item."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: AcceptanceFindingCode
    severity: AcceptanceSeverity
    message: str
    """Free-form human-readable detail. Schema treats as opaque."""


class AcceptanceResult(BaseModel):
    """Per-item gate result. ``passed=False`` ⇔ at least one ``error``-severity
    finding is present."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    passed: bool
    findings: list[AcceptanceFinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_passed_consistent_with_findings(self) -> AcceptanceResult:
        # passed=True ⇒ no error-severity finding present
        # passed=False ⇒ at least one error-severity finding present
        has_error = any(f.severity == "error" for f in self.findings)
        if self.passed and has_error:
            raise ValueError(
                f"AcceptanceResult passed=True but has error-severity findings: "
                f"{[f.code for f in self.findings if f.severity == 'error']}"
            )
        if not self.passed and not has_error:
            raise ValueError(
                f"AcceptanceResult passed=False but no error-severity findings; "
                f"findings={[f.code for f in self.findings]}"
            )
        return self


class CommitOutcome(BaseModel):
    """``PromotionCommitService.commit()`` return value.

    Caller appends ``batch`` to ``manifest.commit_batches`` and persists the
    manifest. Schema is frozen — emit a new outcome on re-run; do not
    mutate.

    Hard invariant (F1-analog): ``error is not None`` ⇒
    ``batch.approved_item_ids == []`` AND ``batch.promotion_status == "failed"``.
    Systemic commit failures must surface as zero-approved + failed-status;
    downstream slices MUST NOT consume an error+approved-non-empty combination.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    """First field on the outcome — closed-set extension protocol marker.
    Adding any new ``AcceptanceFindingCode`` member or invariant requires
    bumping this and updating downstream consumers (#516 / #517)."""

    batch: CommitBatch
    """Per #512 schema; ``touched_files`` populated with hashes. Caller
    appends to ``manifest.commit_batches``."""

    acceptance_results: list[AcceptanceResult] = Field(default_factory=list)
    """Parallel record per requested ``item_id`` (one entry per item the
    caller asked to commit). ``passed=False`` entries are skipped from the
    approved list and surfaced in ``batch.errors`` and
    ``batch.deferred_item_ids`` per Brief §4.2."""

    error: str | None = None
    """``None`` on per-item success/partial. Set to a short, code-prefixed
    reason (e.g. ``"vault_root_invalid: ..."``) for systemic failure (vault
    root invalid / write_adapter raised on every item). On error,
    ``batch.approved_item_ids=[]`` and ``batch.promotion_status='failed'``."""

    @model_validator(mode="after")
    def _hard_invariant_error_implies_failed_batch(self) -> CommitOutcome:
        if self.error is not None:
            if self.batch.approved_item_ids:
                raise ValueError(
                    f"error is not None requires batch.approved_item_ids=[]; "
                    f"got {len(self.batch.approved_item_ids)} approved id(s) "
                    f"with error={self.error!r}. Systemic commit failures "
                    f"must surface as zero-approved + failed-status per "
                    f"Brief §4.3 G* invariants; downstream slices "
                    f"(#516-#517) MUST NOT consume an error+approved-non-empty "
                    f"combination. Mirrors #511 F1 / #513 / #514 patterns."
                )
            if self.batch.promotion_status != "failed":
                raise ValueError(
                    f"error is not None requires batch.promotion_status='failed'; "
                    f"got promotion_status={self.batch.promotion_status!r}."
                )
        return self
