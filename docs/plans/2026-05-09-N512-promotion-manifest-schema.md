# N512 — Promotion Manifest Schema (ADR-024 Slice 4) — v2

**Issue:** #512
**Branch:** `impl/N512-promotion-manifest-schema`
**Worktree:** `E:/nakama-N512-promotion-manifest-schema`
**Status:** Plan v2 (micro-rework after Codex review) — no code yet, awaiting 修修 review
**Author:** Claude (Opus 4.7) — drafted 2026-05-09 v1 from main `0f2742f` (post-#509 ship); revised 2026-05-09 v2
**Companion Brief:** `docs/task-prompts/N512-promotion-manifest-schema.md` v2

---

## v1 → v2 diff (for reviewers)

No architectural changes. Three targeted tightenings:

| # | Area | v1 | v2 |
|---|---|---|---|
| 1 | Validators | V1-V10 | V1-V11. Added **V11**: `status ∈ {partial, complete, failed}` ⇒ `len(commit_batches) >= 1`. Inverse of V3; together they make `status="needs_review"` ⇔ `commit_batches=[]` bijective. Implemented in `_validate_invariants` of `PromotionManifest` (§4.1). |
| 2 | Test matrix | T7 single-direction (V3 only) | T7 **parametrized over (status, batches_count, should_raise)** covering V3 + V11 bidirectionally. Net test count unchanged: **14 functional + 1 reusability = 15 total**. |
| 3 | Dispatch decision (§8) | "Run locally in this worktree, not Sandcastle" | **"Suitable for sandcastle batch dispatch"** (per `feedback_sandcastle_default.md` default = sandcastle rule). After 修修 reviews this Brief and relabels `ready-for-agent`, dispatch via sandcastle. |

---

## 0. Scope anchor

Slice 4 of ADR-024 builds the **Promotion Manifest schema** — a Pydantic model + deterministic validators describing one promotion-review run for one Reading Source. It does **not** build:

- Persistence layer (filesystem path, DB rows) — owned by #515.
- Commit logic / file hashing execution — #515.
- Source map content (chapter pages, claims) — #513.
- Concept promotion logic — #514.
- Review UI — #516.
- Re-run diff logic — #515 (`replaces_manifest_id` is a schema hint only).

ADR-024 anchor (canonical):

- "Promotion commits are item-level partial commits recorded in a **Promotion Manifest**. The manifest is the decision and recovery record; `KB/Wiki` is the materialized output." — §Decision
- "Each review item must include recommendation, reason, evidence, risk, action, and confidence. Missing evidence means defer / needs evidence, not commit." — §Decision

CONTEXT.md (`agents/robin/CONTEXT.md` § Source Promotion) elaborations folded in:

- Per-batch transaction-like records (batch_id + approved/deferred/rejected ids + touched_files + errors + status).
- Touched files carry before/after hashes + operation + backup path.
- `source_importance` vs `reader_salience` separated.
- Concept items get `evidence_language` + `canonical_match.match_basis`.
- Closed-set status enum: `partial / complete / needs_review / failed`.

修修 verbal direction (per 2026-05-09 handoff):

- Manifest 用 `ReadingSource.source_id` 當 source key — 不再 derive。
- `#512` scope = schema / deterministic validation / tests only.

---

## 1. 目標

Provide `shared.schemas.promotion_manifest.PromotionManifest` Pydantic schema that:

- Carries one source_id (`ReadingSource.source_id` from #509) + a list of `ReviewItem` (discriminated union of `SourcePageReviewItem` + `ConceptReviewItem`) + a list of `CommitBatch` records.
- Enforces ADR-level invariants via `model_validator`s:
  - Missing-evidence → cannot be `recommendation="include"`.
  - `status="complete"` → all items have `human_decision`.
  - `commit_batches` non-empty → `status` ∈ {partial, complete, failed}.
  - `item_id` unique within manifest.
  - `replaces_manifest_id != manifest_id` (no self-reference).
  - Confidence / importance / salience ∈ [0.0, 1.0].
- Does NOT model state transitions, persistence, or commit execution.
- Public surface zero `fastapi` / `thousand_sunny` / `agents` import.

---

## 2. 範圍

### Add

| Path | Purpose |
|---|---|
| `shared/schemas/promotion_manifest.py` | Pydantic schema + Literal enums + `model_validator` rules + `now_iso_utc()` helper. |
| `tests/shared/test_promotion_manifest.py` | 14 unit tests (T1-T14) per Brief §5. Zero `fastapi` / `thousand_sunny` / `agents.robin` import. |
| `tests/fixtures/promotion_manifest/` | JSON fixtures for round-trip serialization tests + invalid-shape variants. |

### Touch (read-only — pattern reference)

| Path | Why |
|---|---|
| `shared/schemas/reading_source.py` | `source_id` semantics (#509 — already in main). Pattern reference for `extra="forbid"`, `Literal` enums, closed-set extension protocol. |
| `shared/schemas/annotations.py` | Discriminated-union pattern (`Annotated[Union[...], Field(discriminator="type")]`). `_now_iso` helper pattern. |
| `shared/schemas/ingest_result.py` | Literal enum docstring style. |
| `docs/principles/schemas.md` | Project-wide schema policy. |

### Out of scope (per Brief §6 boundaries)

- KB write
- Persistence (filesystem layout, DB schema)
- File hashing execution
- LLM integration
- ReadingSource enumeration / re-derivation
- State-machine transition logic
- Builder API (`create_manifest()`, `add_item()`, ...) — #515
- Cross-manifest diff logic — #515
- Entity + Conflict item kinds — future slice with closed-set extension protocol

---

## 3. 輸入 (upstream contracts)

| Input | Source | Notes |
|---|---|---|
| `source_id` value | `shared.schemas.reading_source.ReadingSource.source_id` | Stable namespace-qualified id (e.g. `ebook:abc123` / `inbox:Inbox/kb/foo.md`). #512 stores as plain `str`; **does NOT parse or validate format** (schema is transport, not identity rule). |
| `Recommendation` enum | ADR-024 + CONTEXT.md | `Literal["include", "exclude", "defer"]`. |
| `ManifestStatus` enum | CONTEXT.md (Promotion commit ownership boundary §) | `Literal["needs_review", "partial", "complete", "failed"]`. |
| `SourcePageAction` enum | CONTEXT.md (Promotion review item schema §) | `Literal["create", "update_merge", "update_conflict", "noop"]`. |
| `ConceptAction` enum | CONTEXT.md (Promotion concept levels §) | `Literal["keep_source_local", "create_global_concept", "update_merge_global", "update_conflict_global", "exclude"]`. |
| `MatchBasis` enum | CONTEXT.md (Promotion 多語言邊界 §) | `Literal["exact_alias", "semantic", "translation", "none"]`. |
| Schema conventions | `docs/principles/schemas.md` | `extra="forbid"`, Literal-over-str-enums, value-object `frozen=True`. |

**No transitive dependencies on un-merged work**:

- #509 is in main (`0f2742f`). Safe to import `from shared.schemas.reading_source import ReadingSource` if needed for type reference (currently only `source_id` string is needed; type import optional).
- #510 / #511 are not landed; #512 must NOT import from them.
- PR #507 (Phase 1 monolingual-zh PRD) is unrelated to manifest schema — no dependency.

---

## 4. 輸出 (Deliverables)

### 4.1 Schema sketch — `shared/schemas/promotion_manifest.py`

```python
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

    Used by Pydantic ``BeforeValidator`` on every timestamp field so malformed
    timestamps surface as ``ValidationError`` at construct time, not later.
    """
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
                raise ValueError(
                    "match_basis='none' requires matched_concept_path=None"
                )
        else:
            if self.matched_concept_path is None:
                raise ValueError(
                    f"match_basis={self.match_basis!r} requires matched_concept_path"
                )
        return self


class HumanDecision(BaseModel):
    """修修-side decision recorded post-review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: HumanDecisionKind
    decided_at: str
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
    model_version: str
    run_params: dict[str, str] = Field(default_factory=dict)
    recommended_at: str

    @model_validator(mode="after")
    def _validate_recommended_at(self) -> "RecommenderMetadata":
        _validate_iso_utc(self.recommended_at)
        return self


# ── Review items (discriminated union) ────────────────────────────────────

class _ReviewItemCommon(BaseModel):
    """Common fields shared by source_page + concept items.

    Note: not subclassed directly because Pydantic discriminator + extra=forbid
    interact awkwardly with BaseModel inheritance — each item type re-declares
    fields. This class is a documentation-only template.
    """


class SourcePageReviewItem(BaseModel):
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
    applicable. Set by #515 diff logic; schema permits None on first review."""

    human_decision: HumanDecision | None = None

    @model_validator(mode="after")
    def _validate_include_has_evidence(self) -> "SourcePageReviewItem":
        # V1 invariant
        if self.recommendation == "include" and len(self.evidence) == 0:
            raise ValueError(
                f"recommendation='include' requires non-empty evidence "
                f"(item_id={self.item_id!r})"
            )
        return self


class ConceptReviewItem(BaseModel):
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
    """One transaction-like commit batch within a manifest."""

    model_config = ConfigDict(extra="forbid")

    batch_id: str
    created_at: str
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
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: SchemaVersion = 1
    manifest_id: str
    source_id: str
    """Stable namespace-qualified Reading Source id from #509
    (``ebook:{book_id}`` / ``inbox:{logical_original_path}``). Schema does
    NOT parse or validate format; transport string only.
    """

    created_at: str
    status: ManifestStatus
    replaces_manifest_id: str | None = None
    """Pointer to a prior manifest this run supersedes (per ADR-024
    'newer-model re-runs should diff against prior manifests'). Schema
    enforces non-self-reference (V8). Diff logic itself lives in #515.
    """

    recommender: RecommenderMetadata
    items: list[ReviewItem] = Field(default_factory=list)
    commit_batches: list[CommitBatch] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_invariants(self) -> "PromotionManifest":
        _validate_iso_utc(self.created_at)

        # V8 — replaces_manifest_id != manifest_id
        if (
            self.replaces_manifest_id is not None
            and self.replaces_manifest_id == self.manifest_id
        ):
            raise ValueError(
                f"replaces_manifest_id cannot equal manifest_id "
                f"({self.manifest_id!r})"
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
            missing = [
                item.item_id for item in self.items if item.human_decision is None
            ]
            if missing:
                raise ValueError(
                    f"status='complete' requires human_decision on every item; "
                    f"missing: {missing}"
                )

        # V6 — any failed batch ⇒ manifest status cannot be 'complete'
        any_failed = any(
            batch.promotion_status == "failed" for batch in self.commit_batches
        )
        if any_failed and self.status == "complete":
            raise ValueError(
                "manifest cannot be 'complete' when any commit batch is 'failed'"
            )

        return self
```

### 4.2 Test matrix — `tests/shared/test_promotion_manifest.py`

15 tests (14 functional + 1 reusability); mirrors Brief §5. Build tests around 3 helper builders defined in `conftest.py`:

```python
# tests/shared/conftest.py (add — NOT modify existing)

import pytest
from shared.schemas.promotion_manifest import (
    PromotionManifest, RecommenderMetadata, SourcePageReviewItem,
    EvidenceAnchor, now_iso_utc,
)


@pytest.fixture
def recommender() -> RecommenderMetadata:
    return RecommenderMetadata(
        model_name="claude-opus-4-7",
        model_version="2026-04",
        run_params={},
        recommended_at=now_iso_utc(),
    )


@pytest.fixture
def evidence() -> list[EvidenceAnchor]:
    return [
        EvidenceAnchor(
            kind="chapter_quote",
            source_path="data/books/abc123/original.epub",
            locator="epubcfi(/6/14[id1]!/4/2/16/1:0,/6/14[id1]!/4/2/16/1:120)",
            excerpt="The author argues that...",
            confidence=0.92,
        )
    ]
```

| # | Test | Setup | Assertion |
|---|---|---|---|
| T1 | `test_minimal_manifest_constructs` | `PromotionManifest(manifest_id="m1", source_id="ebook:abc", created_at=now_iso_utc(), status="needs_review", recommender=<fixture>)` | Constructs cleanly; `model_dump_json()` round-trips through `model_validate_json(...)`; `status == "needs_review"`. |
| T2 | `test_include_requires_evidence` | `SourcePageReviewItem(recommendation="include", evidence=[], action="create", item_id="i1", reason="...", confidence=0.5, source_importance=0.5, reader_salience=0.5)` | `pytest.raises(ValidationError)` matching "non-empty evidence". |
| T3 | `test_concept_include_requires_evidence` | Same shape with `ConceptReviewItem` + `concept_label="HRV"`. | `pytest.raises(ValidationError)`. |
| T4 | `test_defer_with_no_evidence_ok` | `SourcePageReviewItem(recommendation="defer", evidence=[], action="noop", ...)` | Constructs cleanly. |
| T5 | `test_exclude_with_no_evidence_ok` | `SourcePageReviewItem(recommendation="exclude", evidence=[], action="noop", ...)` | Constructs cleanly. |
| T6 | `test_complete_status_requires_human_decisions` | Manifest with `status="complete"` and one item with `human_decision=None`. | `pytest.raises(ValidationError)` matching "human_decision on every item". |
| T7 | `test_status_commit_batches_consistency` | Parametrized over `(status, batches_count, should_raise)` — covers V3 + V11 bidirectionally. ValidationError cases: (a) `status="needs_review"` + `commit_batches=[<one>]` (V3); (b) `status="partial"` + `commit_batches=[]` (V11); (c) `status="complete"` + `commit_batches=[]` (V11); (d) `status="failed"` + `commit_batches=[]` (V11). Pass cases: (e) `status="needs_review"` + `commit_batches=[]`; (f) `status="partial"` + `commit_batches=[<one>]`. | Bidirectional consistency: `status="needs_review"` ⇔ `commit_batches=[]`. |
| T8 | `test_duplicate_item_ids_rejected` | Manifest with two items both `item_id="i1"`. | `pytest.raises(ValidationError)` matching "duplicate item_id". |
| T9 | `test_confidence_bounds` | parametrized: `confidence ∈ {-0.1, 1.5}` → ValidationError; `confidence ∈ {0.0, 1.0}` accepted. | Pydantic Field constraint. |
| T10 | `test_failed_batch_blocks_complete_status` | Manifest with one `CommitBatch(promotion_status="failed")` and `status="complete"`. | `pytest.raises(ValidationError)` matching "any commit batch is 'failed'". |
| T11 | `test_timestamp_format` | Manifest `created_at="not-an-iso"`. | `pytest.raises(ValidationError)` matching "ISO-8601 UTC". `created_at=now_iso_utc()` accepted. |
| T12 | `test_replaces_self_rejected` | Manifest with `manifest_id="m1"` and `replaces_manifest_id="m1"`. | `pytest.raises(ValidationError)` matching "cannot equal manifest_id". |
| T13 | `test_batch_item_id_set_disjoint` | `CommitBatch(approved_item_ids=["a"], rejected_item_ids=["a"], promotion_status="partial", batch_id="b1", created_at=now_iso_utc())`. | `pytest.raises(ValidationError)` matching "overlap between approved and rejected". |
| T14 | `test_canonical_match_basis_path_consistency` | parametrized: `(match_basis="none", matched_concept_path="X")` → ValidationError; `(match_basis="exact_alias", matched_concept_path=None)` → ValidationError; `(match_basis="none", matched_concept_path=None)` accepted; `(match_basis="exact_alias", matched_concept_path="KB/Wiki/Concepts/HRV.md")` accepted. | All 4 cases via parametrize. |
| T15 | `test_no_runtime_imports` | subprocess `python -c "import shared.schemas.promotion_manifest"` and assert `fastapi`, `thousand_sunny.*`, `agents.*` not in `sys.modules`. | Pass — confirms reusability outside route handlers. |

(T15 mirrors #509 test 14. **14 functional + 1 reusability = 15 total** — wording shared between Brief §5 and this plan.)

### 4.3 Fixtures — `tests/fixtures/promotion_manifest/`

JSON fixtures used in T1 round-trip + targeted invalid-shape tests. Built statically (no zipfile / no monkeypatch trickery — schema tests don't need it):

- `minimal.json` — manifest with required-only fields, status="needs_review".
- `with_one_include_item.json` — manifest with one `SourcePageReviewItem(recommendation="include", evidence=[<one>], action="create", ...)`.
- `with_one_concept_item.json` — manifest with one `ConceptReviewItem(canonical_match=<exact_alias>, ...)`.
- `with_one_commit_batch_partial.json` — manifest with one `CommitBatch` and `status="partial"`.
- `with_complete_status.json` — manifest with two items both `human_decision=<approved>`, one `CommitBatch`, `status="complete"`.

Each fixture round-trips via `PromotionManifest.model_validate_json(path.read_text())` ↔ `model_dump_json()`. T1 uses `minimal.json`; other fixtures used as positive-shape probes in extra tests if needed.

### 4.4 Helper API surface

Only one public helper:

```python
def now_iso_utc() -> str: ...
```

Exported alongside the schema classes. **Do NOT add** `create_manifest()`, `add_item()`, `commit_batch_from_decisions()`, etc. — these are #515.

---

## 5. 驗收

### Issue #512 列出的 4 條 AC（必過）

- [ ] Manifest schema validates review items with recommendation, reason, evidence, risk, action, and confidence.
- [ ] Missing evidence cannot be represented as a commit-ready item.
- [ ] Manifest supports partial decisions and commit batch records.
- [ ] Tests cover serialization, validation failures, defer/needs-evidence, and status transitions.

### Self-imposed gates (per Brief §5)

- [ ] No new dependency.
- [ ] `extra="forbid"` on every Pydantic model.
- [ ] All Literal enums use `Literal[...]`; no `Enum` subclasses.
- [ ] `schema_version: Literal[1] = 1` is the first field on `PromotionManifest`.
- [ ] Discriminator on `Annotated[Union[SourcePageReviewItem, ConceptReviewItem], Field(discriminator="item_kind")]`.
- [ ] Test file imports zero `fastapi` / `thousand_sunny` / `agents.robin` symbols (asserted by T15).
- [ ] `source_id` accepted as plain `str` — no parse / format validation in #512.
- [ ] Closed-set extension protocol documented on every Literal (mirrors #509 N6 contract).
- [ ] No state-machine transition logic (status transitions are #515).
- [ ] No builder API (`create_manifest()` etc. are #515).
- [ ] `python -m pytest tests/shared/test_promotion_manifest.py -v` clean.
- [ ] `python -m ruff check shared/schemas/promotion_manifest.py tests/shared/test_promotion_manifest.py` clean.
- [ ] `python -m ruff format --check shared/schemas/promotion_manifest.py tests/shared/test_promotion_manifest.py` clean (CI gate — see #509 5/9 ship lesson).
- [ ] PR body contains a P7-COMPLETION self-review block.

---

## 6. 邊界

Per Brief §6. Repeated here as one-line callouts:

- ❌ KB write / persistence
- ❌ Source Map Builder logic (#513)
- ❌ Concept Promotion logic (#514)
- ❌ Commit execution / file hashing (#515)
- ❌ Review UI (#516)
- ❌ LLM call
- ❌ State-machine transitions
- ❌ Builder API
- ❌ Cross-manifest diff logic
- ❌ Entity / conflict item kinds (future slice)
- ❌ ReadingSource re-derivation / parsing of `source_id`

---

## 7. Risks + open questions

| # | Item | Mitigation |
|---|---|---|
| R1 | Pydantic discriminator + frozen=True interaction on item subtypes | `SourcePageReviewItem` and `ConceptReviewItem` are NOT frozen (they may need mutation when human writes `human_decision` post-review). Top-level `PromotionManifest` is also not frozen. Frozen-True only on inner value-objects (`EvidenceAnchor`, `RiskFlag`, `CanonicalMatch`, `HumanDecision`, `RecommenderMetadata`, `TouchedFile`). |
| R2 | `dict[str, str]` metadata fields could leak typed values that need richer shape later | Mirror #509 approach: keep metadata as `dict[str, str]` for v1; future slices may introduce typed sub-models if needed. Document in module docstring that metadata is best-effort and consumers must coerce. |
| R3 | Item ordering in `items` and `commit_batches` is significant for diff replay | Schema preserves list order; #515 owns ordering rules. Document in `items` docstring that ordering is caller responsibility. |
| R4 | `CommitBatch` could let `approved_item_ids` reference an `item_id` not in `manifest.items` | Cross-list integrity check (batch ids ⊆ manifest item ids) is **NOT** enforced in #512 — manifest is partial and may receive items + batches in either order during construction. Defer to #515 lint pass. Document in `CommitBatch` docstring. |
| R5 | `_validate_iso_utc` is permissive (accepts any UTC offset, not strictly `Z`) | Acceptable. Tests use `now_iso_utc()` which emits `Z` form; permissiveness avoids breaking external producers that emit `+00:00`. |
| R6 | `prior_decision` field semantics overlap with `human_decision.decision` | They are distinct: `prior_decision` is from a previous manifest (set by #515 diff logic); `human_decision` is from this manifest. Document in field docstrings. |
| Q-OPEN-1 | Should `manifest_id` follow a specific format (e.g. `mfst_{ulid}` or `mfst_{sha256_prefix}`)? | **Lean: no** — #512 schema accepts any non-empty string. Generation rule is #515's choice. Document in `manifest_id` docstring. |
| Q-OPEN-2 | Should the schema include a top-level `frozen` flag indicating "this manifest is immutable post-completion" so #515 can reject writes to a `status="complete"` manifest? | **Lean: defer** — #515 owns immutability enforcement. #512 schema describes shape, not lifecycle locks. |

---

## 8. Step-by-step execution plan

1. Add `shared/schemas/promotion_manifest.py` with module docstring + Literal enums + helper functions (`now_iso_utc`, `_validate_iso_utc`).
2. Add inner value-object models (`EvidenceAnchor`, `RiskFlag`, `CanonicalMatch`, `HumanDecision`, `RecommenderMetadata`, `TouchedFile`) — frozen.
3. Add `SourcePageReviewItem` + `ConceptReviewItem` + `ReviewItem` discriminated union with V1 invariants.
4. Add `CommitBatch` with V9 invariant + ISO timestamp validator.
5. Add `PromotionManifest` with V2/V3/V4/V6/V8 invariants + ISO timestamp validator.
6. Add `tests/shared/test_promotion_manifest.py` skeleton with T1-T15 marked `@pytest.mark.xfail` (red).
7. Add `tests/fixtures/promotion_manifest/*.json` fixtures.
8. Un-xfail and fill tests one at a time as schema invariants pass.
9. `python -m pytest tests/shared/test_promotion_manifest.py -v` clean.
10. `python -m ruff check shared/schemas/promotion_manifest.py tests/shared/test_promotion_manifest.py` clean.
11. `python -m ruff format --check shared/schemas/promotion_manifest.py tests/shared/test_promotion_manifest.py` clean.
12. Open PR `impl/N512-promotion-manifest-schema` → main with P7-COMPLETION block.
13. **Do not** auto-merge; await 修修 review.

**Dispatch decision:** Slice 4 is schema-only and pure deterministic — **suitable for sandcastle batch dispatch** (per `memory/claude/feedback_sandcastle_default.md` default = sandcastle rule). After 修修 reviews this Brief and relabels `ready-for-agent`, dispatch via sandcastle. Estimated ~350 LOC + tests.

---

## 9. Downstream slice dependencies (informational)

| Slice | Issue | 怎麼用 #512 schema |
|---|---|---|
| #513 Source Map Builder | Produces `SourcePageReviewItem` candidates; emits `EvidenceAnchor` for each chapter quote. |
| #514 Concept Promotion Engine | Produces `ConceptReviewItem` candidates with `CanonicalMatch`; sets `evidence_language`. |
| #515 Promotion Commit + Acceptance Gate | Adds `CommitBatch` records + `TouchedFile` with hashes; transitions `status`; runs cross-manifest diff logic via `replaces_manifest_id`. |
| #516 Promotion Review UI | Reads all fields; writes `HumanDecision`; flips `status="needs_review"` → `partial`. |
| #517 Reading Context Package | Does not directly consume manifest (uses ReadingSource + KB output). |

---

## 10. References

- ADR: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md` (main, PR #441 merged)
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion (canonical vocabulary)
- Shared decision: `memory/shared/decision/source_promotion_stage3_stage4_architecture.md`
- Reading Source schema (#509): `shared/schemas/reading_source.py` (main `0f2742f`)
- Annotation discriminated-union pattern: `shared/schemas/annotations.py` (v3 `AnnotationItemV3`)
- Literal enum patterns: `shared/schemas/ingest_result.py`
- Schemas principle: `docs/principles/schemas.md`
- Issue #512: `[ADR-024 S4] Promotion Manifest Schema`
- Parent PRD #508: `Source Promotion and Reading Context Package`
- Slice 1 (#509) ship: PR #518 squash-merged at `0f2742f` (2026-05-09)

---

## 11. Drafting notes (for reviewers)

This is a v1 draft. Likely review hits:

- **V6 invariant** ("any failed batch ⇒ status cannot be 'complete'") — borderline. Could be argued that #515 should choose its own status semantics. Trade-off: schema enforcement gives stronger replay guarantee; loose schema gives #515 more flexibility. Open for review.
- **Item subtype fields are not DRY** — `SourcePageReviewItem` and `ConceptReviewItem` repeat ~12 fields (item_id, recommendation, evidence, risk, confidence, ...). Pydantic's discriminator + extra="forbid" + frozen interactions make subclassing fragile. Repetition is intentional; future refactor candidate when both types are stable.
- **`EvidenceAnchor.locator` is opaque string** — could be a discriminated union (CFI / line-range / xpath). Choose opacity for v1: #513 is the locator producer, and #515 is the locator consumer; #512 is just transport.
- **`metadata: dict[str, str]`** — same trade-off as #509 ReadingSource.metadata. Mirror that decision.
- **No `frozen=True` on `PromotionManifest`** — items list mutates as review proceeds (`human_decision` filled in, `commit_batches` appended). Frozen would block legitimate mutation. Inner value-objects (EvidenceAnchor etc.) ARE frozen.
