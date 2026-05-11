# Agent Brief — N512 Promotion Manifest Schema (ADR-024 Slice 4) — v2

**Issue:** [#512](https://github.com/shosho-chang/nakama/issues/512)
**Parent PRD:** #508
**Slice of:** ADR-024 (`docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`)
**Branch:** `impl/N512-promotion-manifest-schema`
**Worktree (host):** `E:/nakama-N512-promotion-manifest-schema`
**Implementation plan:** `docs/plans/2026-05-09-N512-promotion-manifest-schema.md` v2 (read alongside)
**Drafted:** 2026-05-09 v1; revised 2026-05-09 v2
**Status:** v2 draft (micro-rework after Codex review) — awaiting 修修 review before relabeling `ready-for-agent`

P9 六要素。Brief 是 self-contained handoff，不靠群聊歷史也能上手。

---

## 0. Revision summary

### v1 → v2 (micro-rework after Codex review)

No architectural changes. Three targeted tightenings:

- **V11 invariant added**: `status ∈ {partial, complete, failed}` ⇒ `len(commit_batches) >= 1`. Inverse of V3; together they make `status="needs_review"` ⇔ `commit_batches=[]` bijective. Removes the ambiguous "post-review status with no batch" state. T7 promoted to a parametrized test covering both directions (no net test count change).
- **Dispatch decision flipped**: schema-only is pure deterministic — suitable for **sandcastle batch dispatch** (per `feedback_sandcastle_default.md` default = sandcastle). v1 said "本機 worktree 跑（不上 Sandcastle）" which contradicted the project default. After 修修 reviews this Brief and relabels `ready-for-agent`, dispatch via sandcastle.
- **Test count wording normalized**: 14 functional + 1 reusability = 15 total. v1 Brief §5 listed only 14 functional rows; v2 adds T15 (reusability) row for parity with the plan §4.2 table.

---

## 0.1 Scope anchor

ADR-024 §Decision: "Promotion commits are item-level partial commits recorded in a **Promotion Manifest**. The manifest is the decision and recovery record; `KB/Wiki` is the materialized output."

ADR-024 elaborations from `agents/robin/CONTEXT.md` § Source Promotion:

- Per-run replayable record. Future newer-model re-runs MUST diff against prior manifests, not silently overwrite.
- Each review item carries `recommendation` (include/exclude/defer), `reason`, `evidence`, `risk`, `action`, `confidence`, `source_importance`, `reader_salience`.
- Annotation can be `reader_salience` signal but cannot directly fabricate factual claims — items missing `evidence` go to `defer` / `needs_evidence`, not commit.
- Commit is item-level partial: each commit batch is transaction-like with `batch_id`, approved/deferred/rejected ids, touched files, errors, and resulting `promotion_status` ∈ {`partial`, `complete`, `needs_review`, `failed`}.
- Touched files carry before/after hashes, operation type, and backup path when applicable. Hash-mismatch on restore requires human confirmation (recovery is in #515; #512 only supplies schema fields).

Slice 4 (#512) builds **only the schema + deterministic validation**. No persistence layer, no commit logic, no LLM integration, no UI.

### Where Slice 1 (#509) lands

`#509` shipped to main at commit `0f2742f` (2026-05-09). The Reading Source Registry resolves a `BookKey` / `InboxKey` to a `ReadingSource` value-object with stable `source_id` (`ebook:{book_id}` / `inbox:{logical_original_path}`). **`#512` MUST use `ReadingSource.source_id` as the Manifest's source key** — never re-derive identity.

```python
# Read-only consumption — verify these symbols are still in main before coding.
from shared.schemas.reading_source import ReadingSource
# `ReadingSource.source_id` is the stable namespace-qualified id.
```

---

## 1. 目標 (Goal)

在 `shared/schemas/promotion_manifest.py` 提供 Pydantic schema 描述：

- 一份 **Promotion Manifest**：對單一 `ReadingSource` 的一次 promotion-review 結果記錄（per-source-per-run）。
- 一組 **ReviewItem**：discriminated union (源頁 / Concept) 載 recommendation / reason / evidence / risk / action / confidence / salience。
- 一組 **CommitBatch**：item-level partial commit 的 transaction-like 記錄。
- 一組 **TouchedFile**：每個 commit batch 觸碰的檔案 + before/after hash + operation + backup path。

並提供 **deterministic validators** 把 ADR 級不變式硬硬寫進 Pydantic：

- Missing-evidence ⇒ recommendation 不可為 `include`（commit-ready 排除空 evidence）。
- `complete` status ⇒ 所有 items 必有 `human_decision`。
- `commit_batches` 非空 ⇒ status ∈ {`partial`, `complete`, `failed`}（不可仍 `needs_review`）。
- `item_id` 在 manifest 內唯一。
- 數值欄位 `confidence` / `source_importance` / `reader_salience` 介於 0.0–1.0。

不啟動 commit、不寫 KB、不 fetch ReadingSource、不跑 LLM、不渲染 UI。為 #513 (Source Map Builder)、#514 (Concept Promotion)、#515 (Promotion Commit + Acceptance Gate)、#516 (Promotion Review UI) 提供共同的決策 / 通訊 schema。

---

## 2. 範圍

新增三個檔：

| 路徑 | 動作 |
|---|---|
| `shared/schemas/promotion_manifest.py` | **新增** — Pydantic 模型 (`PromotionManifest`, `ReviewItem` discriminated union, `SourcePageReviewItem`, `ConceptReviewItem`, `EvidenceAnchor`, `RiskFlag`, `CanonicalMatch`, `HumanDecision`, `RecommenderMetadata`, `CommitBatch`, `TouchedFile`)。`extra="forbid"` + value-object `frozen=False`（Manifest 在 review 過程會 mutate；item 內小 value-object frozen）。|
| `tests/shared/test_promotion_manifest.py` | **新增** — unit tests（見 §5 驗收）。零 `fastapi` import；零 `thousand_sunny` import。|
| `tests/fixtures/promotion_manifest/` | **新增** — JSON fixtures：minimal manifest、full manifest、invalid-shape variants（用於 ValidationError tests）。|

讀取（不改）：

- `shared/schemas/reading_source.py` (#509 — `source_id` semantics)
- `shared/schemas/annotations.py` (pattern reference — discriminated union shape per `Annotated[Union[...], Field(discriminator="type")]`)
- `shared/schemas/ingest_result.py` (pattern reference — Literal status enums)
- `docs/principles/schemas.md` (`extra="forbid"`, Literal-over-str-enums policy)
- `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`
- `agents/robin/CONTEXT.md` § Source Promotion

**完全不碰**：

- `agents/robin/*` — promotion logic 是 #513 / #514 / #515
- `thousand_sunny/routers/*` — UI 是 #516
- `KB/Wiki/*` — materialized output 是 #515 commit
- `shared/reading_source_registry.py` — 已 ship；read-only 引用 `ReadingSource.source_id` 形態
- 任何 LLM call、任何 vault/KB write、任何 SQL migration、任何 file hashing 實際執行

---

## 3. 輸入

### 主要 contracts (read-only)

- **`ReadingSource.source_id`** (from `#509`, in main): 穩定 namespace-qualified id (`ebook:{book_id}` / `inbox:{logical_original_path}`)。Manifest schema 用 `source_id: str` 接收，**不**做格式 parse / validation — Manifest 是 transport schema，不是 source identity 規約。
- **ADR-024 vocabulary**: `recommendation` ∈ `{include, exclude, defer}`、`promotion_status` ∈ `{partial, complete, needs_review, failed}`、source_page action ∈ `{create, update_merge, update_conflict, noop}`、concept action ∈ `{keep_source_local, create_global_concept, update_merge_global, update_conflict_global, exclude}`、`canonical_match.match_basis` ∈ `{exact_alias, semantic, translation, none}`。
- **schema 慣例**: `extra="forbid"`, Literal over str enums (per `docs/principles/schemas.md`), `schema_version: Literal[1] = 1` first field, `_now_iso()` helper for default timestamps (mirror `shared/schemas/annotations.py:23`).

### Documentation hierarchy

- ADR-024 (main, PR #441 merged) — canonical decision
- `agents/robin/CONTEXT.md` § Source Promotion — domain rules
- `memory/shared/decision/source_promotion_stage3_stage4_architecture.md` — shared decision
- `shared/schemas/reading_source.py` (#509) — `source_id` semantics
- `shared/schemas/annotations.py` — discriminated-union pattern reference

### Out-of-scope downstream consumers (informational only)

| Slice | 怎麼用 #512 schema |
|---|---|
| #513 Source Map Builder | 產出 `SourcePageReviewItem` 候選；不消費 commit fields |
| #514 Concept Promotion Engine | 產出 `ConceptReviewItem` 候選 + `CanonicalMatch` |
| #515 Promotion Commit + Acceptance Gate | 加 `CommitBatch` + `TouchedFile` records；轉 `status` |
| #516 Promotion Review UI | 讀全部 fields；寫 `HumanDecision` |
| #517 Reading Context Package | 不直接消費 manifest（用 ReadingSource + KB output 即可） |

---

## 4. 輸出

### 4.1 Schema sketch

詳見 plan §4。重點結構：

```python
SchemaVersion = Literal[1]

ManifestStatus = Literal["needs_review", "partial", "complete", "failed"]
Recommendation = Literal["include", "exclude", "defer"]
SourcePageAction = Literal["create", "update_merge", "update_conflict", "noop"]
ConceptAction = Literal[
    "keep_source_local", "create_global_concept",
    "update_merge_global", "update_conflict_global", "exclude",
]
HumanDecisionKind = Literal["approve", "reject", "defer"]
EvidenceAnchorKind = Literal[
    "chapter_quote", "section_quote", "frontmatter_field", "external_ref",
]
RiskCode = Literal[
    "weak_toc", "ocr_artifact", "mixed_language", "missing_evidence",
    "low_signal_count", "duplicate_concept", "cross_lingual_uncertain", "other",
]
RiskSeverity = Literal["low", "medium", "high"]
MatchBasis = Literal["exact_alias", "semantic", "translation", "none"]
TouchedFileOperation = Literal["create", "update", "delete", "skip"]
ItemKind = Literal["source_page", "concept"]


class EvidenceAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: EvidenceAnchorKind
    source_path: str       # e.g. data/books/{book_id}/original.epub, Inbox/kb/foo.md
    locator: str           # CFI / line-range / xpath / xref — opaque to schema
    excerpt: str
    confidence: float = Field(ge=0.0, le=1.0)


class RiskFlag(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: RiskCode
    severity: RiskSeverity
    description: str


class CanonicalMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    match_basis: MatchBasis
    confidence: float = Field(ge=0.0, le=1.0)
    matched_concept_path: str | None = None   # e.g. KB/Wiki/Concepts/HRV.md


class HumanDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    decision: HumanDecisionKind
    decided_at: str                # ISO-8601 UTC
    decided_by: str
    note: str | None = None


class RecommenderMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model_name: str                # e.g. "claude-opus-4-7"
    model_version: str             # frozen identity for replay
    run_params: dict[str, str] = Field(default_factory=dict)
    recommended_at: str            # ISO-8601 UTC


# ── Discriminated union — one shape per item_kind ─────────────────────

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
    target_kb_path: str | None = None      # e.g. KB/Wiki/Sources/{slug}/chapter-3.md
    chapter_ref: str | None = None
    prior_decision: HumanDecisionKind | None = None
    human_decision: HumanDecision | None = None


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
    evidence_language: str | None = None     # BCP-47 short
    canonical_match: CanonicalMatch | None = None
    prior_decision: HumanDecisionKind | None = None
    human_decision: HumanDecision | None = None


ReviewItem = Annotated[
    Union[SourcePageReviewItem, ConceptReviewItem],
    Field(discriminator="item_kind"),
]


class TouchedFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str                      # vault-relative
    operation: TouchedFileOperation
    before_hash: str | None = None # sha256 hex; None on create
    after_hash: str | None = None  # sha256 hex; None on delete
    backup_path: str | None = None


class CommitBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    batch_id: str
    created_at: str                # ISO-8601 UTC
    approved_item_ids: list[str] = Field(default_factory=list)
    deferred_item_ids: list[str] = Field(default_factory=list)
    rejected_item_ids: list[str] = Field(default_factory=list)
    touched_files: list[TouchedFile] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    promotion_status: Literal["partial", "complete", "needs_review", "failed"]


class PromotionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: SchemaVersion = 1
    manifest_id: str
    source_id: str                            # ReadingSource.source_id (no parsing)
    created_at: str                           # ISO-8601 UTC
    status: ManifestStatus
    replaces_manifest_id: str | None = None   # for diff-against-prior support
    recommender: RecommenderMetadata
    items: list[ReviewItem] = Field(default_factory=list)
    commit_batches: list[CommitBatch] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
```

### 4.2 Deterministic validators

Implemented as Pydantic `model_validator(mode="after")` on `PromotionManifest` (and one on each item subtype):

| ID | Rule | Where | Test |
|---|---|---|---|
| V1 | `recommendation == "include"` ⇒ `len(evidence) >= 1` | item subtype validators | T2, T3 |
| V2 | `status == "complete"` ⇒ every item has `human_decision is not None` | manifest | T6 |
| V3 | `len(commit_batches) > 0` ⇒ `status in {partial, complete, failed}` | manifest | T7 |
| V4 | `item_id` 在 `items` 內唯一 | manifest | T8 |
| V5 | `confidence` / `source_importance` / `reader_salience` ∈ [0.0, 1.0] | Pydantic Field constraints | T9 |
| V6 | `commit_batches[*].promotion_status` 與 manifest `status` 不矛盾 — 若任一 batch 是 `failed` 則 manifest status 不可為 `complete` | manifest | T10 |
| V7 | 所有 timestamp fields 必為 ISO-8601 UTC（`Z` 結尾或 `+00:00`） | reusable validator helper | T11 |
| V8 | `replaces_manifest_id` 不得 == `manifest_id`（不能自指） | manifest | T12 |
| V9 | `commit_batches[*]` 中同一 `item_id` 不可同時出現在 approved+deferred+rejected 三個 list | per-batch validator | T13 |
| V10 | `ConceptReviewItem.canonical_match` 的 `matched_concept_path` 與 `match_basis == "none"` 互斥（none 代表無 match，不可有 path；非 none 必有 path） | item validator | T14 |
| V11 | `status in {partial, complete, failed}` ⇒ `len(commit_batches) >= 1`（V3 inverse — 兩者一起讓 `status="needs_review"` ⇔ `commit_batches=[]` 形成 bijection） | manifest | T7 (parametrized) |

### 4.3 Helper API surface

僅暴露 schema + 一個 helper：

```python
def now_iso_utc() -> str:
    """ISO-8601 UTC timestamp helper (mirror shared/schemas/annotations.py:23)."""
```

**不**暴露 `create_manifest()` / `add_item()` / `commit_batch()` 等 builder — 這些是 #515 的責任。

---

## 5. 驗收

### Issue #512 列出的 4 條 AC（必過）

- [ ] Manifest schema validates review items with recommendation, reason, evidence, risk, action, and confidence.
- [ ] Missing evidence cannot be represented as a commit-ready item.
- [ ] Manifest supports partial decisions and commit batch records.
- [ ] Tests cover serialization, validation failures, defer/needs-evidence, and status transitions.

### Unit tests 必過（14 functional + 1 reusability = 15 total）

| # | Test | 驗 |
|---|---|---|
| T1 | `test_minimal_manifest_constructs` | Required fields only (no items, no batches), status="needs_review"; round-trips through `model_dump()` + `model_validate()`. |
| T2 | `test_include_requires_evidence` | `SourcePageReviewItem(recommendation="include", evidence=[])` → `ValidationError`. |
| T3 | `test_concept_include_requires_evidence` | `ConceptReviewItem(recommendation="include", evidence=[])` → `ValidationError`. |
| T4 | `test_defer_with_no_evidence_ok` | `recommendation="defer", evidence=[]` constructs cleanly (the AC: missing-evidence → defer is the legitimate state). |
| T5 | `test_exclude_with_no_evidence_ok` | `recommendation="exclude", evidence=[]` constructs cleanly. |
| T6 | `test_complete_status_requires_human_decisions` | `status="complete"` with any item missing `human_decision` → `ValidationError`. |
| T7 | `test_status_commit_batches_consistency` | Parametrized — covers V3 + V11 bidirectionally: (a) `status="needs_review"` + `commit_batches=[<one>]` → ValidationError; (b)/(c)/(d) `status ∈ {partial, complete, failed}` + `commit_batches=[]` → ValidationError; (e) `status="needs_review"` + `commit_batches=[]` → OK; (f) `status="partial"` + `commit_batches=[<one>]` → OK. |
| T8 | `test_duplicate_item_ids_rejected` | Two items with same `item_id` → `ValidationError`. |
| T9 | `test_confidence_bounds` | parametrized: `confidence=-0.1` and `confidence=1.5` → `ValidationError`; `0.0` and `1.0` accepted. |
| T10 | `test_failed_batch_blocks_complete_status` | `commit_batches=[<failed>]` with `status="complete"` → `ValidationError`. |
| T11 | `test_timestamp_format` | `created_at="not-an-iso-string"` → `ValidationError`. Helper `now_iso_utc()` output passes. |
| T12 | `test_replaces_self_rejected` | `replaces_manifest_id == manifest_id` → `ValidationError`. |
| T13 | `test_batch_item_id_set_disjoint` | `CommitBatch(approved_item_ids=["a"], rejected_item_ids=["a"])` → `ValidationError`. |
| T14 | `test_canonical_match_basis_path_consistency` | `CanonicalMatch(match_basis="none", matched_concept_path="X")` → `ValidationError`; `match_basis="exact_alias"` 必有 path else `ValidationError`. |
| T15 | `test_no_runtime_imports` | subprocess `python -c "import shared.schemas.promotion_manifest"` → assert `fastapi`, `thousand_sunny.*`, `agents.*` not in `sys.modules`. The +1 reusability gate (mirrors #509 test 14). |

### Self-imposed gates

- [ ] 零新 dependency
- [ ] 全部 model `extra="forbid"`
- [ ] Discriminator (`item_kind`) on `Annotated[Union[SourcePageReviewItem, ConceptReviewItem], Field(discriminator="item_kind")]`
- [ ] All Literal enums 用 `Literal[...]`，**不**用 `Enum`（per `docs/principles/schemas.md`）
- [ ] `schema_version: Literal[1] = 1` first field on `PromotionManifest`
- [ ] Test 檔零 `fastapi` / `thousand_sunny` / `agents.robin` import (asserted by T15 subprocess check)
- [ ] `source_id` 接收成 `str`，**不** parse、不 validate format（schema 是 transport，不是 identity 規約）
- [ ] **Closed-set extension protocol**: 每個 `Literal` 是 frozen for `schema_version=1`；新增成員必須 (a) bump `schema_version`、(b) 更新 docstring、(c) 更新 #515 / #516 downstream policy。Silent extension forbidden（mirrors #509 N6 contract）.
- [ ] **No state machine logic**: schema 只 validate state shape，不 model state transition。Status transition 邏輯（`needs_review` → `partial` → `complete` etc.）是 #515 的責任。
- [ ] `python -m pytest tests/shared/test_promotion_manifest.py -v` 全綠
- [ ] `python -m ruff check shared/schemas/promotion_manifest.py tests/shared/test_promotion_manifest.py` 無 error
- [ ] `python -m ruff format --check shared/schemas/promotion_manifest.py tests/shared/test_promotion_manifest.py` clean (per CI gate — see 5/9 N509 ship lesson)
- [ ] PR body 含 P7-COMPLETION 區塊

---

## 6. 邊界（不能碰）

| # | Don't | Why |
|---|---|---|
| 1 | ❌ KB write / `KB/Wiki/*` 變更 | #515 commit |
| 2 | ❌ Source Map Builder logic | #513 |
| 3 | ❌ Concept Promotion logic | #514 |
| 4 | ❌ Reading Overlay 寫入或變更 | #510 |
| 5 | ❌ UI / Thousand Sunny route handler | #516 |
| 6 | ❌ 任何 LLM call | pure deterministic schema |
| 7 | ❌ Vault / DB / KB / file hashing 實際執行 | schema 只描述 hash 形態，不算 hash |
| 8 | ❌ 新 SQL migration / 新 persistence layer | #515 |
| 9 | ❌ ReadingSource 重新 derive / 變更 | read-only via `source_id: str` |
| 10 | ❌ State-machine transition logic | schema 只 validate state shape |
| 11 | ❌ 自定 timestamp library；mirror `shared/schemas/annotations.py:_now_iso` 簡單 helper | one source of truth |
| 12 | ❌ 在 #512 內展開 entity / conflict item_kinds | 留給 future slice 用 closed-set extension protocol |
| 13 | ❌ 投機塞 `commit_batches` builder API / `status` transition method | schema only |
| 14 | ❌ Hash 字串 normalize（lowercase、strip）— 接什麼存什麼 | 一致性是 #515 caller 責任 |
| 15 | ❌ Manifest persistence 路徑 / filename 規約 | #515 |
| 16 | ❌ 跨 manifest diff logic | #515 (`replaces_manifest_id` 是 schema 上的 hint，diff 是 #515 邏輯) |

---

## 7. References

### Primary

- ADR: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md` (main, PR #441 merged)
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion
- Shared decision: `memory/shared/decision/source_promotion_stage3_stage4_architecture.md`
- Schemas principle: `docs/principles/schemas.md`
- ReadingSource (#509): `shared/schemas/reading_source.py` (main as of `0f2742f`)
- Discriminated-union pattern: `shared/schemas/annotations.py` (v3 `AnnotationItemV3`)
- Literal enum pattern: `shared/schemas/ingest_result.py` (`IngestStatus`, `IngestFullTextLayer`)

### Implementation plan

- `docs/plans/2026-05-09-N512-promotion-manifest-schema.md` v1 (read alongside this Brief)

### Issue / PR

- Issue #512: `[ADR-024 S4] Promotion Manifest Schema`
- Parent PRD #508: `Source Promotion and Reading Context Package`
- Slice 1 (#509) shipped: PR #518 squash-merged at `0f2742f` 2026-05-09

---

## 8. Triage status

`needs-triage` → 等修修讀 v2 Brief + plan 後決定。**不要**自行 relabel `ready-for-agent`，**不要**直接開始 code。

Slice 4 是 schema-only 的純 deterministic 工作 — 適合 **sandcastle batch dispatch**（per `memory/claude/feedback_sandcastle_default.md` default = sandcastle 規則）。修修 review Brief OK 後可 relabel `ready-for-agent` 並派 sandcastle。預估 ~350 LOC + tests。
