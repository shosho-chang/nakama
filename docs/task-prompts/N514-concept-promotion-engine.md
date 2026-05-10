# Agent Brief — N514 Concept Promotion Engine (ADR-024 Slice 6)

**Issue:** [#514](https://github.com/shosho-chang/nakama/issues/514)
**Parent PRD:** #508
**Slice of:** ADR-024 (`docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`)
**Branch:** `impl/N514-concept-promotion-engine`
**Worktree (host):** `E:/nakama-N514-concept-promotion-engine`
**Drafted:** 2026-05-10
**Status:** awaiting #513 merge + 修修 review before relabeling `ready-for-agent`

P9 六要素。Brief 是 self-contained handoff，不靠群聊歷史也能上手。

---

## 0. Scope anchor

ADR-024 §Decision: "Concept handling is two-level: extract Source-local Concepts first; promote only selected high-value concepts into Global KB Concepts when they have cross-source value, long-term output value, enough evidence, clear definitions, useful relations, or recurrence."

`agents/robin/CONTEXT.md` § Source Promotion (Concept levels): "先抽 source-local concepts，再決定少數是否升為 global KB Concept... Review action 應區分 `keep_source_local`、`create_global_concept`、`update_merge_global`、`update_conflict_global`、`exclude`."

`agents/robin/CONTEXT.md` § Source Promotion (多語言邊界): "Concept canonical layer 要能跨語言聚合，不可因中文/英文名稱不同就各開一頁... 標記 `evidence_language` 與 `canonical_match`(match_basis: exact_alias / semantic / translation / none + confidence)。低信心 cross-lingual match 進 exception，不自動 merge."

Slice 6 (#514) builds the **deterministic concept promotion engine** that turns a Source Map (#513 output) + ReadingSource into `list[ConceptReviewItem]` (per #512 schema). Engine produces source-local-only candidates as well as global candidates carrying `CanonicalMatch`. **No LLM call**, **no KB write**, **no UI**, **no manifest persistence**.

### Where Slice 1-5 land (in main as of #514 dispatch time)

`#514` consumes:

- `ReadingSource` (#509) — for `source_id`, `primary_lang`, `has_evidence_track`.
- `SourceMapBuildResult.items` (#513) — for chapter context, evidence anchors, claims.
- A `ConceptMatcher` Protocol injected by caller (deterministic fixture in tests; LLM-backed in production).
- A `KBConceptIndex` Protocol injected by caller (deterministic fixture in tests; real KB index out of slice).

### Pipeline anchor

CONTENT-PIPELINE.md Stage 3 (整合). Engine is the second-half producer for promotion review: where #513 produces source-page candidates, #514 produces concept candidates that downstream #515 commits and #516 reviews.

---

## 1. 目標 (Goal)

提供 `shared.concept_promotion_engine.ConceptPromotionEngine.propose(reading_source, source_map, kb_index, matcher, *, min_global_confidence=0.75) -> ConceptPromotionResult`：

- 從 `SourceMapBuildResult.items` 抽取候選 concepts (concept-level units distinct from source pages).
- For each candidate decide a `ConceptAction`:
  - `keep_source_local` — useful inside this source only, no global page recommended.
  - `create_global_concept` — high-value, no global match.
  - `update_merge_global` — high-confidence match against existing global concept.
  - `update_conflict_global` — match exists but content disagrees → human review.
  - `exclude` — low-quality / out-of-scope.
- For cross-lingual candidates, attach `CanonicalMatch(match_basis ∈ {exact_alias, semantic, translation, none}, confidence, matched_concept_path)`.
- Low-confidence cross-lingual match (`< min_global_confidence` AND `match_basis ∈ {semantic, translation}`) → does NOT auto-merge; instead emits `update_conflict_global` (or `keep_source_local` if confidence very low) for human review.
- Pure deterministic — uses injected `ConceptMatcher` Protocol and `KBConceptIndex` Protocol. No LLM call inside this slice.

不啟動 commit、不寫 KB、不跑 LLM、不渲染 UI、不建 backlinks。

---

## 2. 範圍

### Add

| Path | Action | Reason |
|---|---|---|
| `shared/concept_promotion_engine.py` | **新增** | `ConceptPromotionEngine` class + `ConceptMatcher` / `KBConceptIndex` Protocols + `ConceptPromotionResult` value-object. |
| `shared/schemas/concept_promotion.py` | **新增** | `ConceptCandidate`, `MatchOutcome`, `ConceptPromotionResult` Pydantic value-objects. ALL `extra="forbid"` + `frozen=True`. |
| `tests/shared/test_concept_promotion_engine.py` | **新增** | Unit tests covering source-local, create, update_merge, update_conflict, exclude, and low-confidence cross-lingual exception cases. |
| `tests/fixtures/concept_promotion/` | **新增** | Source Map fixtures + fake KB index fixtures + cross-lingual scenarios. |

### Read-only consumption

| Path | Why |
|---|---|
| `shared/schemas/reading_source.py` (#509) | `ReadingSource.source_id`, `primary_lang`, `has_evidence_track`. |
| `shared/schemas/source_map.py` (#513) | `SourceMapBuildResult` shape. |
| `shared/schemas/promotion_manifest.py` (#512) | Emit `ConceptReviewItem`, `CanonicalMatch`, `EvidenceAnchor`, `RiskFlag` per closed-set Literal contract. |

### 完全不碰

- `shared/book_storage.py` — out-of-bounds per #509 N3.
- `agents/robin/*` — promotion route handlers.
- `thousand_sunny/routers/*` — UI is #516.
- `KB/Wiki/Concepts/*` — materialized output is #515 commit.
- 任何 LLM client.
- 任何 vault / DB write、real KB index access.
- `shared/source_map_builder.py` (#513) — engine consumes its result, doesn't re-build.

---

## 3. 輸入

### Caller contracts

```python
class ConceptMatcher(Protocol):
    """Cross-source / cross-lingual matcher protocol.

    Slice 6 ships ONLY the protocol + a deterministic fixture matcher for
    tests. LLM-backed implementation is the caller's responsibility.
    """

    def match(
        self,
        candidate: ConceptCandidate,
        kb_index: KBConceptIndex,
        primary_lang: str,
    ) -> MatchOutcome:
        """Returns best canonical match (or 'none' basis) + confidence."""
        ...


class KBConceptIndex(Protocol):
    """Read-only view of existing global KB concepts.

    Slice 6 ships ONLY the protocol + a deterministic fixture index for
    tests. Real KB index reader is out of scope.
    """

    def lookup(self, alias: str) -> KBConceptEntry | None: ...
    def aliases_starting_with(self, prefix: str) -> list[str]: ...
```

`ConceptPromotionEngine.propose` signature:

```python
def propose(
    self,
    reading_source: ReadingSource,
    source_map: SourceMapBuildResult,
    kb_index: KBConceptIndex,
    matcher: ConceptMatcher,
    *,
    min_global_confidence: float = 0.75,
    min_recurrence_for_global: int = 2,  # appear in ≥2 chapters → global candidate
) -> ConceptPromotionResult:
    ...
```

### Caller responsibilities (out of scope)

- **Concept candidate generation**: Slice 6 reads candidates from `SourceMapBuildResult.items` claims/quotes. If the caller wants richer LLM-extracted candidates, they pass an enriched `SourceMapBuildResult` produced by an LLM-backed `ClaimExtractor` (#513 protocol). Engine does not re-extract from raw text.
- **Matcher selection**: caller chooses LLM vs deterministic.
- **Manifest assembly**: caller wraps `ConceptPromotionResult.items` into a `PromotionManifest`. Engine produces items only.

### Documentation hierarchy

- ADR-024 §Decision (concept two-level)
- `agents/robin/CONTEXT.md` § Source Promotion (Concept levels, 多語言邊界)
- `shared/schemas/promotion_manifest.py` (`ConceptReviewItem`, `CanonicalMatch` shapes)

---

## 4. 輸出

### 4.1 Schema sketch (additions to `shared/schemas/concept_promotion.py`)

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from shared.schemas.promotion_manifest import (
    CanonicalMatch, ConceptReviewItem, EvidenceAnchor, RiskFlag,
)


class ConceptCandidate(BaseModel):
    """Internal engine intermediate — extracted from Source Map items.

    Carried into ConceptMatcher.match() input.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    label: str  # canonical form (e.g. 'HRV' or '心率變異')
    aliases: list[str] = Field(default_factory=list)
    """Other surface forms found in source. Used for alias matching."""

    evidence_language: str
    """BCP-47 short tag — derived from primary_lang or detected per quote."""

    chapter_refs: list[str] = Field(default_factory=list)
    """Source map chapter_refs where this candidate appeared. Used for
    recurrence check (cross-chapter ⇒ global candidate)."""

    raw_quotes: list[str] = Field(default_factory=list)
    """≤ 3 short excerpts. Used to seed EvidenceAnchor for the eventual item."""


class KBConceptEntry(BaseModel):
    """One existing global KB concept entry — minimal projection used by engine."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    concept_path: str  # e.g. KB/Wiki/Concepts/HRV.md
    canonical_label: str
    aliases: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


class MatchOutcome(BaseModel):
    """ConceptMatcher.match() return value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical_match: CanonicalMatch
    """Includes match_basis, confidence, matched_concept_path."""

    conflict_signals: list[str] = Field(default_factory=list)
    """Free-form notes on disagreement (e.g. 'definition diverges',
    'aliases overlap but languages differ'). Caller may surface via risk."""


class ConceptPromotionResult(BaseModel):
    """Engine output. Caller wraps `items` into a PromotionManifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    source_id: str  # transport only
    primary_lang: str
    candidates_extracted: int = Field(ge=0)
    items: list[ConceptReviewItem] = Field(default_factory=list)
    """Ordered: source-local first, then create_global, then update variants,
    then exclude. Caller may re-sort."""

    risks: list[RiskFlag] = Field(default_factory=list)
    error: str | None = None
```

### 4.2 Engine action policy (top-down first-match)

| Row | Condition | Action | Reason emitted |
|---|---|---|---|
| 1 | `len(chapter_refs) < min_recurrence_for_global` AND no high-confidence canonical_match | `keep_source_local` | "single-chapter mention; not promoting globally" |
| 2 | canonical_match.match_basis = "exact_alias" AND confidence ≥ 0.90 AND no conflict_signals | `update_merge_global` | "exact alias match against {path}" |
| 3 | canonical_match.match_basis = "exact_alias" AND conflict_signals non-empty | `update_conflict_global` | "exact alias match but content conflicts: {signals}" |
| 4 | canonical_match.match_basis ∈ {"semantic", "translation"} AND confidence ≥ min_global_confidence AND no conflict_signals | `update_merge_global` | "{basis} match against {path}, conf={conf}" |
| 5 | canonical_match.match_basis ∈ {"semantic", "translation"} AND confidence < min_global_confidence | `update_conflict_global` (if conf ≥ 0.50) OR `keep_source_local` (if conf < 0.50) | "low-confidence cross-lingual match ({conf}); requires human review" |
| 6 | canonical_match.match_basis = "none" AND `len(chapter_refs) ≥ min_recurrence_for_global` AND total raw_quotes ≥ 3 | `create_global_concept` | "recurring across {n} chapters; no global match found" |
| 7 | canonical_match.match_basis = "none" AND insufficient recurrence/evidence | `keep_source_local` | "no global match; insufficient evidence for global" |
| 8 | extractor returned empty/blank label | `exclude` | "candidate label empty or noise" |

### 4.3 Hard invariants enforced at propose time

| ID | Rule | Where |
|---|---|---|
| C1 | Every emitted `ConceptReviewItem` is per #512 schema (`extra="forbid"`, V1 invariant: include ⇒ evidence non-empty) | inherited from #512 |
| C2 | `update_conflict_global` items MUST have `recommendation="defer"` (since human conflict resolution required) | engine sets explicitly |
| C3 | `keep_source_local` items MUST have `recommendation ∈ {"include", "defer"}` (NOT exclude — local concepts still useful) | engine policy |
| C4 | `create_global_concept` items MUST have ≥1 EvidenceAnchor (per V1) AND `confidence ≥ min_global_confidence` | engine bounds |
| C5 | All `ConceptReviewItem.evidence_language` derived from candidate.evidence_language; non-null when source is monolingual | engine sets |
| C6 | Engine NEVER imports `shared.book_storage`, `fastapi`, `thousand_sunny.*`, `agents.*`, LLM clients | T11/T12 |
| C7 | On matcher exception: catch (narrow tuple), set `result.error`, return whatever items completed | engine error path |

---

## 5. 驗收 (Acceptance)

### Issue #514 listed AC

- Source-local concept candidates can remain local without creating global pages.
- High-value concepts can recommend global create/update_merge/update_conflict/exclude actions.
- Cross-language matches record match basis and confidence.
- Tests cover source-local, create, update_merge, update_conflict, exclude, and low-confidence cross-lingual exception cases.
- Do not write to the real KB.
- Do not run real LLM calls; use deterministic fake matchers / fixture KB indexes.

### Unit tests required

| # | Test | Asserts |
|---|---|---|
| T1 | `test_propose_source_local_when_single_chapter` | Single-chapter candidate → `action="keep_source_local"`. |
| T2 | `test_propose_create_global_when_recurrent_no_match` | Recurrence ≥ 2, matcher returns `match_basis="none"` → `action="create_global_concept"`, recommendation="include", evidence non-empty. |
| T3 | `test_propose_update_merge_on_exact_alias_high_conf` | Matcher returns `exact_alias`, confidence=0.95, no conflict → `action="update_merge_global"`. |
| T4 | `test_propose_update_conflict_on_exact_alias_with_conflict` | Matcher returns `exact_alias`, confidence=0.95, conflict_signals=["definition diverges"] → `action="update_conflict_global"`, recommendation="defer". |
| T5 | `test_propose_update_conflict_on_low_conf_semantic` | `semantic`, confidence=0.60 (< 0.75) → `action="update_conflict_global"`, recommendation="defer". |
| T6 | `test_propose_keep_local_on_very_low_conf_translation` | `translation`, confidence=0.30 (< 0.50) → `action="keep_source_local"` per row 5b. |
| T7 | `test_propose_exclude_blank_label` | Candidate label is empty/whitespace → `action="exclude"`. |
| T8 | `test_propose_cross_lingual_records_match_basis` | Output items where match_basis ∈ {semantic, translation} carry the canonical_match field with non-null `matched_concept_path` and confidence. |
| T9 | `test_propose_evidence_language_set` | Monolingual zh source → all items have `evidence_language="zh-Hant"` (or matching primary_lang). |
| T10 | `test_propose_uses_deterministic_fake_matcher` | Fake matcher returns canned `MatchOutcome`; engine threads through unchanged. |
| T11 | `test_no_book_storage_import` | Subprocess assertion. |
| T12 | `test_no_runtime_imports_forbidden` | Subprocess: `fastapi`, `thousand_sunny.*`, `agents.*`, LLM clients absent from `sys.modules`. |
| T13 | `test_propose_matcher_failure_returns_error_state` | Matcher raises → `result.error` set, partial items preserved. |
| T14 | `test_propose_concept_review_items_pass_v1_invariant` | All emitted items where `recommendation="include"` have `len(evidence) ≥ 1` (V1 inheritance). |
| T15 | `test_propose_result_round_trips` | `model_dump()` + `model_validate()` identity. |
| T16 | `test_propose_min_recurrence_threshold_configurable` | Set `min_recurrence_for_global=3`; assert candidate appearing in 2 chapters → `keep_source_local`. |

### Self-imposed gates

- [ ] 零新 dependency
- [ ] 全部 model `extra="forbid"`
- [ ] All Literal enums via #512 (no new Literals introduced in this slice unless needed for builder intermediates)
- [ ] `schema_version: Literal[1] = 1` first field on `ConceptPromotionResult`
- [ ] **Closed-set extension protocol**: 新增 enum 成員必須 (a) bump `schema_version`、(b) 更新 docstring、(c) 更新 #515 / #516 downstream
- [ ] **Narrow exception tuples** (per #511 F5 lesson)
- [ ] `python -m pytest tests/shared/test_concept_promotion_engine.py -v` 全綠
- [ ] `python -m ruff check shared/concept_promotion_engine.py shared/schemas/concept_promotion.py tests/shared/test_concept_promotion_engine.py` 無 error
- [ ] `python -m ruff format --check ...` clean
- [ ] PR body 含 P7-COMPLETION 區塊

---

## 6. 邊界（不能碰）

| # | Don't | Why |
|---|---|---|
| 1 | ❌ KB write / `KB/Wiki/Concepts/*` 變更 | #515 commit |
| 2 | ❌ `shared.book_storage` import | #509 N3 contract |
| 3 | ❌ Parse `source_id` for namespace prefix | #509 N3 contract |
| 4 | ❌ LLM call | use injected `ConceptMatcher` Protocol |
| 5 | ❌ Re-extract candidates from raw chapter text | #513 owns extraction; engine consumes Source Map |
| 6 | ❌ Real KB index read | use injected `KBConceptIndex` Protocol; tests use fixtures |
| 7 | ❌ Vault / DB write | #515 |
| 8 | ❌ State machine / promotion status transitions | #515 |
| 9 | ❌ UI / Thousand Sunny route handler | #516 |
| 10 | ❌ Reading Overlay write | #510 / #517 |
| 11 | ❌ `git add .` / commit untracked files outside scope | hygiene |
| 12 | ❌ Any change to #509 / #511 / #512 / #513 schemas | upstream contracts frozen |
| 13 | ❌ Auto-merge low-confidence cross-lingual matches | ADR-024 §Decision (CONTEXT.md 多語言邊界 rule) |
| 14 | ❌ Bare `except Exception` | #511 F5 lesson |

---

## 7. References

### Primary

- ADR: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion (Concept levels, 多語言邊界)
- Manifest schema: `shared/schemas/promotion_manifest.py` (#512)
- Reading Source schema: `shared/schemas/reading_source.py` (#509)
- Source Map schema: `shared/schemas/source_map.py` (#513)

### Pattern reference

- `shared/promotion_preflight.py` (#511) — protocol injection + narrow-exception pattern
- `shared/source_map_builder.py` (#513) — sibling pattern (deterministic engine + protocol injection)

### Issue / PR

- Issue #514: `[ADR-024 S6] Concept Promotion Engine`
- Parent PRD #508
- Slice 5 (#513): merge before dispatch

---

## 8. Triage status

`needs-triage` → 等修修讀 Brief 後決定。**不要**自行 relabel `ready-for-agent`，**不要**直接開始 code。

Slice 6 等 #513 merge 後再 dispatch (engine consumes #513 SourceMap shape). 預估 ~600 LOC + tests.
