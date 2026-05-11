"""Promotion Manifest schema (ADR-024 Slice 4 / issue #512).

A Promotion Manifest is the per-source-per-run replayable decision record for
ADR-024 Source Promotion. One manifest captures:

- The Reading Source under review (via stable ``source_id`` from #509).
- The recommender's run identity (model_name + version + params).
- Review items: include/exclude/defer recommendations with reason, evidence,
  risk, action, confidence, source_importance, reader_salience.
- Commit batches: item-level partial-commit transaction records with
  approved/deferred/rejected ids, touched files (with before/after hashes),
  errors, and resulting promotion status.

Slice 4 is schema-only. State transition logic, persistence, file hashing,
and commit execution are owned by #515. LLM recommendation generation is
owned by #513 / #514. Review UI is owned by #516.

Closed-set extension protocol (mirrors #509 N6 contract):
every ``Literal`` enum is frozen for ``schema_version=1``. Adding a new
member requires (a) bumping ``schema_version`` on ``PromotionManifest``,
(b) updating this docstring + the Literal docstring, (c) updating
downstream policy in #515 / #516. Silent extension is forbidden.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


def now_iso_utc() -> str:
    """ISO-8601 UTC timestamp helper (mirror shared/schemas/annotations.py)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_iso_utc(value: str) -> str:
    """Validate an ISO-8601 UTC timestamp string. Accepts trailing 'Z' or '+00:00'.

    Used by per-model ``model_validator(mode="after")`` so malformed timestamps
    surface as ``ValidationError`` at construct time, not later.
    """
    if not isinstance(value, str):
        raise ValueError(f"timestamp must be a string: {value!r}")
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"timestamp must be ISO-8601 UTC: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"timestamp must carry UTC offset: {value!r}")
    return value


# ── Closed-set Literal enums ──────────────────────────────────────────────

SchemaVersion = Literal[1]

ManifestStatus = Literal["needs_review", "partial", "complete", "failed"]
"""Closed for schema_version=1.

- needs_review : recommender produced items; no commit batch yet.
- partial      : at least one commit batch landed; some items still pending.
- complete     : all items have human_decision; final batch committed cleanly.
- failed       : commit batch ended in failure; manifest needs operator action.

Extension requires schema_version bump + downstream policy update in #515.
"""

Recommendation = Literal["include", "exclude", "defer"]
"""Closed for schema_version=1. ADR-024 §Decision.

- include  : LLM recommends commit. Requires non-empty evidence (V1 invariant).
- exclude  : LLM recommends not committing. Evidence optional.
- defer    : LLM cannot recommend yet (e.g. needs more evidence). Evidence optional.
"""

SourcePageAction = Literal["create", "update_merge", "update_conflict", "noop"]
"""Closed for schema_version=1. CONTEXT.md (Promotion review item schema §)."""

ConceptAction = Literal[
    "keep_source_local",
    "create_global_concept",
    "update_merge_global",
    "update_conflict_global",
    "exclude",
]
"""Closed for schema_version=1. CONTEXT.md (Promotion concept levels §)."""

HumanDecisionKind = Literal["approve", "reject", "defer"]
"""Closed for schema_version=1. 修修-side decision shape; matches Recommendation
plus 'approve' replacing LLM 'include' to disambiguate human vs model voice."""

EvidenceAnchorKind = Literal[
    "chapter_quote",
    "section_quote",
    "frontmatter_field",
    "external_ref",
]
"""Closed for schema_version=1. Anchor types supported by #515 commit."""

RiskCode = Literal[
    "weak_toc",
    "ocr_artifact",
    "mixed_language",
    "missing_evidence",
    "low_signal_count",
    "duplicate_concept",
    "cross_lingual_uncertain",
    "other",
]
"""Closed for schema_version=1. Mirror CONTEXT.md preflight risks + review risks."""

RiskSeverity = Literal["low", "medium", "high"]

MatchBasis = Literal["exact_alias", "semantic", "translation", "none"]
"""Closed for schema_version=1. CONTEXT.md (Promotion 多語言邊界 §).

V10 invariant: ``match_basis="none"`` ⇔ ``matched_concept_path is None``.
Non-none match basis requires ``matched_concept_path``.
"""

TouchedFileOperation = Literal["create", "update", "delete", "skip"]
"""Closed for schema_version=1. ``skip`` covers idempotent no-op rewrites."""

ItemKind = Literal["source_page", "concept"]
"""Closed for schema_version=1.

Future slices may extend with ``entity`` and ``conflict`` per CONTEXT.md.
Extension requires schema_version bump (mirrors #509 N6 protocol).
"""


# ── Value objects (frozen) ────────────────────────────────────────────────


class EvidenceAnchor(BaseModel):
    """One anchor pointing at the original-language evidence backing a claim."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: EvidenceAnchorKind
    source_path: str
    """Path to the variant carrying the evidence. Format depends on kind:

    - ebook chapter_quote / section_quote → ``data/books/{book_id}/original.epub``
    - inbox quote                          → ``Inbox/kb/foo.md``
    - frontmatter_field                    → ``Inbox/kb/foo.md`` (with locator
                                             pointing at the field name)
    - external_ref                         → free-form URL or DOI
    """

    locator: str
    """Format depends on kind. EPUB CFI for ebook quotes; line range
    ``L42-L58`` for markdown quotes; field name (e.g. ``original_url``) for
    frontmatter; URL/DOI for external_ref. Schema treats locator as opaque.
    """

    excerpt: str
    confidence: float = Field(ge=0.0, le=1.0)


class RiskFlag(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: RiskCode
    severity: RiskSeverity
    description: str


class CanonicalMatch(BaseModel):
    """Cross-source concept canonical match (CONTEXT.md Promotion 多語言邊界 §)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    match_basis: MatchBasis
    confidence: float = Field(ge=0.0, le=1.0)
    matched_concept_path: str | None = None

    @model_validator(mode="after")
    def _validate_match_basis_path(self) -> "CanonicalMatch":
        # V10 invariant
        if self.match_basis == "none":
            if self.matched_concept_path is not None:
                raise ValueError("match_basis='none' requires matched_concept_path=None")
        else:
            if self.matched_concept_path is None:
                raise ValueError(f"match_basis={self.match_basis!r} requires matched_concept_path")
        return self


class HumanDecision(BaseModel):
    """修修-side decision recorded post-review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: HumanDecisionKind
    decided_at: str
    """ISO-8601 UTC timestamp string. Validated on construction."""

    decided_by: str
    note: str | None = None

    @model_validator(mode="after")
    def _validate_decided_at(self) -> "HumanDecision":
        _validate_iso_utc(self.decided_at)
        return self


class RecommenderMetadata(BaseModel):
    """Frozen identity of the LLM run that produced this manifest. Used by
    #515 to determine 'is this a re-run with a newer model?'.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_name: str
    """e.g. ``"claude-opus-4-7"``."""

    model_version: str
    """Frozen identity for replay (e.g. ``"2026-04"``)."""

    run_params: dict[str, str] = Field(default_factory=dict)
    """Best-effort string-only params (mirrors #509 metadata convention).
    Consumers must coerce types; future slices may introduce typed sub-models."""

    recommended_at: str
    """ISO-8601 UTC timestamp string."""

    @model_validator(mode="after")
    def _validate_recommended_at(self) -> "RecommenderMetadata":
        _validate_iso_utc(self.recommended_at)
        return self


# ── Review items (discriminated union) ────────────────────────────────────


class SourcePageReviewItem(BaseModel):
    """Per-page promotion review item (one entry per source page candidate).

    Mutability: NOT frozen — ``human_decision`` is filled in post-review.
    """

    model_config = ConfigDict(extra="forbid")

    item_kind: Literal["source_page"] = "source_page"
    item_id: str
    recommendation: Recommendation
    action: SourcePageAction
    reason: str
    evidence: list[EvidenceAnchor] = Field(default_factory=list)
    risk: list[RiskFlag] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    source_importance: float = Field(ge=0.0, le=1.0)
    reader_salience: float = Field(ge=0.0, le=1.0)
    target_kb_path: str | None = None
    """e.g. ``KB/Wiki/Sources/{slug}/chapter-3.md``. Schema does not validate
    path shape; #515 commit logic owns vault path semantics."""

    chapter_ref: str | None = None
    prior_decision: HumanDecisionKind | None = None
    """Prior manifest's human decision on the same logical item, when
    applicable. Set by #515 diff logic; schema permits None on first review.
    Distinct from ``human_decision`` (this manifest's decision)."""

    human_decision: HumanDecision | None = None

    @model_validator(mode="after")
    def _validate_include_has_evidence(self) -> "SourcePageReviewItem":
        # V1 invariant
        if self.recommendation == "include" and len(self.evidence) == 0:
            raise ValueError(
                f"recommendation='include' requires non-empty evidence (item_id={self.item_id!r})"
            )
        return self


class ConceptReviewItem(BaseModel):
    """Per-concept promotion review item (one entry per concept candidate).

    Mutability: NOT frozen — ``human_decision`` is filled in post-review.
    """

    model_config = ConfigDict(extra="forbid")

    item_kind: Literal["concept"] = "concept"
    item_id: str
    recommendation: Recommendation
    action: ConceptAction
    reason: str
    evidence: list[EvidenceAnchor] = Field(default_factory=list)
    risk: list[RiskFlag] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    source_importance: float = Field(ge=0.0, le=1.0)
    reader_salience: float = Field(ge=0.0, le=1.0)
    concept_label: str
    evidence_language: str | None = None
    """BCP-47 short tag (en / zh-Hant / unknown). #514 owns derivation."""

    canonical_match: CanonicalMatch | None = None
    prior_decision: HumanDecisionKind | None = None
    """Prior manifest's human decision on the same logical concept; distinct
    from ``human_decision`` (this manifest's decision)."""

    human_decision: HumanDecision | None = None

    @model_validator(mode="after")
    def _validate_include_has_evidence(self) -> "ConceptReviewItem":
        # V1 invariant
        if self.recommendation == "include" and len(self.evidence) == 0:
            raise ValueError(
                f"recommendation='include' requires non-empty evidence "
                f"(item_id={self.item_id!r}, concept={self.concept_label!r})"
            )
        return self


ReviewItem = Annotated[
    Union[SourcePageReviewItem, ConceptReviewItem],
    Field(discriminator="item_kind"),
]


# ── Commit batches ────────────────────────────────────────────────────────


class TouchedFile(BaseModel):
    """One file touched by a commit batch.

    Schema field shapes only — actual hashing is owned by #515. Hash strings
    are accepted as-is (no normalization). Caller invariants:

    - operation='create'  → before_hash=None, after_hash=str
    - operation='update'  → before_hash=str, after_hash=str
    - operation='delete'  → before_hash=str, after_hash=None
    - operation='skip'    → before_hash=str, after_hash=str (equal allowed)

    These invariants are NOT enforced by #512 (would constrain #515's hash
    strategy choice). #515 may add a model_validator if it wants the rule
    enforced at schema level.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    operation: TouchedFileOperation
    before_hash: str | None = None
    after_hash: str | None = None
    backup_path: str | None = None


class CommitBatch(BaseModel):
    """One transaction-like commit batch within a manifest.

    Cross-list integrity (batch ids ⊆ manifest item ids) is NOT enforced at
    the batch level — manifest is partial and may receive items + batches in
    either order during construction. #515 owns that lint pass.
    """

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    created_at: str
    """ISO-8601 UTC timestamp string."""

    approved_item_ids: list[str] = Field(default_factory=list)
    deferred_item_ids: list[str] = Field(default_factory=list)
    rejected_item_ids: list[str] = Field(default_factory=list)
    touched_files: list[TouchedFile] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    promotion_status: Literal["partial", "complete", "needs_review", "failed"]

    @model_validator(mode="after")
    def _validate_disjoint_item_id_sets(self) -> "CommitBatch":
        # V9 invariant — an item_id appears in exactly one of approved/deferred/rejected
        approved = set(self.approved_item_ids)
        deferred = set(self.deferred_item_ids)
        rejected = set(self.rejected_item_ids)
        for label_a, set_a, label_b, set_b in [
            ("approved", approved, "deferred", deferred),
            ("approved", approved, "rejected", rejected),
            ("deferred", deferred, "rejected", rejected),
        ]:
            overlap = set_a & set_b
            if overlap:
                raise ValueError(
                    f"CommitBatch item_ids overlap between "
                    f"{label_a} and {label_b}: {sorted(overlap)} "
                    f"(batch_id={self.batch_id!r})"
                )
        return self

    @model_validator(mode="after")
    def _validate_created_at(self) -> "CommitBatch":
        _validate_iso_utc(self.created_at)
        return self


# ── Top-level manifest ────────────────────────────────────────────────────


class PromotionManifest(BaseModel):
    """Per-source-per-run replayable decision record.

    See module docstring for ADR-024 anchor and slice scope.

    Mutability: NOT frozen — ``items`` mutate as review proceeds
    (``human_decision`` filled in, ``commit_batches`` appended). Inner
    value-objects (``EvidenceAnchor``, ``RiskFlag``, ``CanonicalMatch``,
    ``HumanDecision``, ``RecommenderMetadata``, ``TouchedFile``) ARE frozen.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: SchemaVersion = 1
    manifest_id: str
    """Free-form non-empty string. Generation rule (e.g. ``mfst_{ulid}``) is
    #515's choice; #512 schema accepts any string."""

    source_id: str
    """Stable namespace-qualified Reading Source id from #509
    (``ebook:{book_id}`` / ``inbox:{logical_original_path}``). Schema does
    NOT parse or validate format; transport string only.
    """

    created_at: str
    """ISO-8601 UTC timestamp string."""

    status: ManifestStatus
    replaces_manifest_id: str | None = None
    """Pointer to a prior manifest this run supersedes (per ADR-024
    'newer-model re-runs should diff against prior manifests'). Schema
    enforces non-self-reference (V8). Diff logic itself lives in #515.
    """

    recommender: RecommenderMetadata
    items: list[ReviewItem] = Field(default_factory=list)
    """Items list. Ordering is significant for #515 diff replay; preservation
    of caller order is the caller's responsibility."""

    commit_batches: list[CommitBatch] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
    """Best-effort string-only metadata. Consumers must coerce types."""

    @model_validator(mode="after")
    def _validate_invariants(self) -> "PromotionManifest":
        _validate_iso_utc(self.created_at)

        # V8 — replaces_manifest_id != manifest_id
        if self.replaces_manifest_id is not None and self.replaces_manifest_id == self.manifest_id:
            raise ValueError(
                f"replaces_manifest_id cannot equal manifest_id ({self.manifest_id!r})"
            )

        # V4 — item_id unique within manifest
        item_ids = [item.item_id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            seen: set[str] = set()
            dups: list[str] = []
            for iid in item_ids:
                if iid in seen and iid not in dups:
                    dups.append(iid)
                seen.add(iid)
            raise ValueError(f"duplicate item_id(s) in manifest: {dups}")

        # V3 — commit_batches non-empty ⇒ status ∈ {partial, complete, failed}
        if len(self.commit_batches) > 0 and self.status == "needs_review":
            raise ValueError(
                "manifest with commit_batches cannot have status='needs_review'; "
                "use 'partial' / 'complete' / 'failed'"
            )

        # V11 — status ∈ {partial, complete, failed} ⇒ commit_batches non-empty
        # (inverse of V3; together they make 'needs_review' ⇔ commit_batches=[]
        #  bijective, removing the ambiguous 'post-review status with no batch'
        #  state.)
        if self.status in {"partial", "complete", "failed"} and len(self.commit_batches) == 0:
            raise ValueError(
                f"status={self.status!r} requires at least one commit_batch; "
                f"use 'needs_review' if no batch has been committed yet"
            )

        # V2 — status='complete' ⇒ all items have human_decision
        if self.status == "complete":
            missing = [item.item_id for item in self.items if item.human_decision is None]
            if missing:
                raise ValueError(
                    f"status='complete' requires human_decision on every item; missing: {missing}"
                )

        # V6 — any failed batch ⇒ manifest status cannot be 'complete'
        any_failed = any(batch.promotion_status == "failed" for batch in self.commit_batches)
        if any_failed and self.status == "complete":
            raise ValueError("manifest cannot be 'complete' when any commit batch is 'failed'")

        return self
