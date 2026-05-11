# Agent Brief — N515 Promotion Commit + Acceptance Gate (ADR-024 Slice 7)

**Issue:** [#515](https://github.com/shosho-chang/nakama/issues/515)
**Parent PRD:** #508
**Slice of:** ADR-024 (`docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`)
**Branch:** `impl/N515-promotion-commit-gate`
**Worktree (host):** `E:/nakama-N515-promotion-commit-gate`
**Drafted:** 2026-05-10
**Status:** awaiting #513 + #514 merge + 修修 review before relabeling `ready-for-agent`

P9 六要素。Brief 是 self-contained handoff，不靠群聊歷史也能上手。

---

## 0. Scope anchor

ADR-024 §Decision: "Promotion commits are item-level partial commits recorded in a Promotion Manifest. The manifest is the decision and recovery record; KB/Wiki is the materialized output."

`agents/robin/CONTEXT.md` § Source Promotion: "Promotion commit is item-level partial commit, not whole-source all-or-nothing. Review can be gradual; approved items may commit while deferred/rejected items remain in the manifest. Each commit batch must be transaction-like: batch id, approved/deferred/rejected item ids, touched files, errors, and resulting promotion_status (`partial` / `complete` / `needs_review` / `failed`). Partial failures must be visible and auditable."

`agents/robin/CONTEXT.md`: "Promotion commit recovery is manifest-driven, not automatic destructive rollback... record touched files with before/after hashes, operation type, backup path when applicable, errors, and status. On failure, the UI may offer reviewed restore/resume/cleanup actions, but must not silently delete or reset files. Hash mismatch during restore requires human confirmation."

Slice 7 (#515) builds the **deterministic commit + acceptance gate service** that takes a `PromotionManifest` with approved items and writes to a vault root, recording the operation as a `CommitBatch`. The acceptance gate prevents invalid Source/Concept writes from silently landing.

### Where Slice 1-6 land (in main as of #515 dispatch time)

`#515` consumes:

- `PromotionManifest` (#512) — read items, append CommitBatch.
- `ReadingSource` (#509) — for source_id lookups (no parsing).
- `SourceMapBuildResult` (#513) — read items via the manifest.
- `ConceptPromotionResult` (#514) — read items via the manifest.

### Pipeline anchor

CONTENT-PIPELINE.md Stage 3 (整合) → Stage 4 boundary. This slice closes the Stage 3 loop: review decisions → durable KB writes. Stage 4 (Reading Context Package, #517) reads the committed output.

---

## 1. 目標 (Goal)

提供 `shared.promotion_commit.PromotionCommitService.commit(manifest, batch_id, item_ids, vault_root) -> CommitOutcome`：

- Take a `PromotionManifest` with `human_decision` set on the chosen `item_ids` (subset).
- For each approved item: pass through `AcceptanceGate.validate(item, vault_root)` → on pass, write the materialized output (Source page or Concept page) to the fixture vault.
- Record a `CommitBatch` per #512 schema with: `batch_id`, `created_at`, `approved_item_ids` / `deferred_item_ids` / `rejected_item_ids`, `touched_files` (with sha256 before/after hashes, operation type, backup path), `errors`, `promotion_status`.
- On any acceptance failure: skip the item, record error, continue with next; final batch `promotion_status = "partial"` (or `"failed"` if zero items committed).
- On hash mismatch during pre-write read-back: refuse to overwrite, record error, return `"failed"` for that item; surface for human review.
- Resumable: a manifest with prior batches can accept a new batch via subsequent `commit()` call; new `CommitBatch` is appended.

不啟動 LLM、不渲染 UI、不 fetch ReadingSource fresh, 不寫 real vault (tests use temp fixtures).

---

## 2. 範圍

### Add

| Path | Action | Reason |
|---|---|---|
| `shared/promotion_commit.py` | **新增** | `PromotionCommitService` class + `CommitOutcome` value-object + `KbWriteAdapter` Protocol. |
| `shared/promotion_acceptance_gate.py` | **新增** | `AcceptanceGate` class + `AcceptanceFinding` value-object. Validates one ReviewItem against a vault snapshot. |
| `shared/schemas/promotion_commit.py` | **新增** | `AcceptanceFinding`, `AcceptanceResult`, `CommitOutcome` Pydantic value-objects. ALL `extra="forbid"` + `frozen=True`. |
| `tests/shared/test_promotion_commit.py` | **新增** | Unit tests covering successful partial commit, validation failure, hash mismatch, resumable failure, idempotent rerun. |
| `tests/shared/test_promotion_acceptance_gate.py` | **新增** | Unit tests for gate failure modes (path traversal, target_kb_path malformed, evidence anchor invalid, etc.). |
| `tests/fixtures/promotion_commit/` | **新增** | Fixture vaults + manifest scenarios. |

### Read-only consumption

| Path | Why |
|---|---|
| `shared/schemas/promotion_manifest.py` (#512) | `PromotionManifest`, `CommitBatch`, `TouchedFile`, `SourcePageReviewItem`, `ConceptReviewItem` shapes. |
| `shared/schemas/source_map.py` (#513), `shared/schemas/concept_promotion.py` (#514) | Reference for item content shapes (engine read-through). |
| `shared/schemas/reading_source.py` (#509) | source_id transport only. |

### 完全不碰

- **Real `KB/Wiki/*`** — use temp fixture vaults only. Tests under `tests/fixtures/promotion_commit/` create temp dirs, never write to live vault.
- `shared.book_storage` — N3 contract.
- `agents/robin/*` route handlers.
- `thousand_sunny/routers/*` — UI is #516.
- 任何 LLM client.
- `shared/source_map_builder.py` (#513), `shared/concept_promotion_engine.py` (#514) — these produce the manifest; #515 consumes only.

---

## 3. 輸入

### Caller contract

```python
class KbWriteAdapter(Protocol):
    """Vault write adapter. Slice 7 ships ONLY a filesystem-backed
    implementation operating on a `vault_root: Path`. Production may swap
    for a more sophisticated adapter (e.g. version-controlled writer)."""

    def read_file(self, vault_path: str) -> bytes | None: ...
    def write_file(self, vault_path: str, content: bytes, *, backup_path: str | None) -> None: ...
    def hash_file(self, vault_path: str) -> str | None:
        """sha256 hex, or None if file missing."""
        ...
    def make_backup(self, vault_path: str) -> str | None:
        """Return backup path on success, None if no original."""
        ...
```

`PromotionCommitService.commit` signature:

```python
def commit(
    self,
    manifest: PromotionManifest,
    batch_id: str,
    item_ids: list[str],
    vault_root: Path,
    *,
    write_adapter: KbWriteAdapter | None = None,  # default: filesystem-backed adapter
) -> CommitOutcome:
    ...
```

`AcceptanceGate.validate` signature:

```python
def validate(
    self,
    item: SourcePageReviewItem | ConceptReviewItem,
    vault_root: Path,
    write_adapter: KbWriteAdapter,
) -> AcceptanceResult:
    """Returns AcceptanceResult(passed: bool, findings: list[AcceptanceFinding]).
    No writes performed."""
    ...
```

### Caller responsibilities

- Caller sets `human_decision` on items they want committed BEFORE calling `commit()`. Items lacking `human_decision="approve"` are skipped (recorded as deferred/rejected per the decision).
- Caller chooses `batch_id` (e.g. `batch_{ulid}`).
- Caller manages manifest persistence (load → call commit → save updated manifest with appended CommitBatch). #515 returns the new CommitBatch; caller writes manifest back to disk.
- Caller chooses vault_root. Real vault paths are out of scope for tests.

### Documentation hierarchy

- ADR-024 §Decision (item-level partial commit, manifest as decision record)
- `agents/robin/CONTEXT.md` § Source Promotion (commit + recovery rules)
- `shared/schemas/promotion_manifest.py` (CommitBatch, TouchedFile shapes)

---

## 4. 輸出

### 4.1 Schema sketch (additions to `shared/schemas/promotion_commit.py`)

```python
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from shared.schemas.promotion_manifest import CommitBatch, TouchedFile


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
"""Closed for schema_version=1. Extension requires bump."""


class AcceptanceFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: AcceptanceFindingCode
    severity: Literal["error", "warning"]
    message: str


class AcceptanceResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    item_id: str
    passed: bool
    findings: list[AcceptanceFinding] = Field(default_factory=list)


class CommitOutcome(BaseModel):
    """commit() return value. Caller appends `batch` to manifest.commit_batches."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    batch: CommitBatch
    """Per #512 schema; touched_files populated with hashes."""

    acceptance_results: list[AcceptanceResult] = Field(default_factory=list)
    """Parallel record per requested item_id (passed=False entries are skipped
    from approved list; surfaced in batch.errors)."""

    error: str | None = None
    """Set on systemic failure (vault root invalid, write_adapter raised)."""
```

### 4.2 Commit pipeline (per item)

For each `item_id` in `item_ids`:

1. **Locate**: find item in `manifest.items` by `item_id`. Missing → record error, skip.
2. **Acceptance gate**: call `gate.validate(item, vault_root, adapter)` → if `passed=False`, record into `acceptance_results`; treat as deferred (not approved, not rejected — explicit defer reason from gate).
3. **Pre-write read-back**: `before_hash = adapter.hash_file(target_path)`. Compare against `item.prior_decision` flow if applicable (none required for fresh writes; #515 skips this for first-time create operations).
4. **Backup**: if file exists (operation = update), `backup_path = adapter.make_backup(target_path)`.
5. **Render**: synthesize markdown content from item fields. SourcePage page = frontmatter + claims + evidence + risks. Concept page = frontmatter + canonical match + aliases + evidence anchors. Builder lives in `shared/promotion_renderer.py` (helper module) — keep simple, deterministic.
6. **Write**: `adapter.write_file(target_path, content, backup_path=backup_path)`.
7. **Post-write hash**: `after_hash = adapter.hash_file(target_path)`.
8. **Record `TouchedFile`**: `path=target_path, operation, before_hash, after_hash, backup_path`.
9. **Append to batch.approved_item_ids**.

After all items processed:

10. **Compute `promotion_status`**:
    - All items committed cleanly → `complete` if all manifest items now have `human_decision` else `partial`.
    - At least one item committed but some failed → `partial`.
    - Zero items committed (all failed gate or write) → `failed`.
11. **Return** `CommitOutcome(batch=CommitBatch(...), acceptance_results=[...])`.

### 4.3 Hard invariants enforced at commit time

| ID | Rule | Where |
|---|---|---|
| G1 | All `touched_files[*]` paths under `vault_root` (no traversal escape) | acceptance gate |
| G2 | Per-item `target_kb_path` non-empty AND under `vault_root` | acceptance gate |
| G3 | `human_decision.decision == "approve"` required for items in `approved_item_ids`; items with `decision in {"reject", "defer"}` go to corresponding lists | commit pipeline |
| G4 | `hash_mismatch_pre_write` finding → item NOT written; recorded as defer | commit + gate |
| G5 | `EvidenceAnchor.locator` non-empty (already #512 V1; gate cross-checks) | gate |
| G6 | Concept items where `canonical_match.match_basis != "none"` MUST have `matched_concept_path` set (already #512 V10; gate cross-checks) | gate |
| G7 | Resumable: calling `commit()` twice with same `batch_id` raises `ValueError("duplicate batch_id")` | commit entry |
| G8 | `CommitBatch.promotion_status` per #512 V11 (status ⇔ commit_batches non-empty) | computed after all items |
| G9 | Service NEVER imports `book_storage`, `fastapi`, `thousand_sunny.*`, `agents.*`, LLM clients | T11/T12 |
| G10 | Service NEVER writes outside `vault_root` (filesystem isolation enforced; tests assert) | adapter |

---

## 5. 驗收 (Acceptance)

### Issue #515 listed AC

- Approved items can commit while deferred/rejected items remain in the manifest.
- Commit batches record touched files, before/after hashes, operation status, errors, and recovery metadata.
- Acceptance gate prevents invalid Source/Concept writes from silently landing.
- Tests cover successful partial commit, validation failure, hash mismatch, and resumable failure status.
- Use temp fixture vaults only.
- Never write to the real vault in tests.

### Unit tests required (commit service)

| # | Test | Asserts |
|---|---|---|
| T1 | `test_commit_partial_writes_approved_items` | Manifest has 5 items, 3 approved → 3 written to fixture vault, batch.approved=3, deferred=0, rejected=2 (others). |
| T2 | `test_commit_records_touched_files_with_hashes` | After commit, batch.touched_files entries have non-None `after_hash`; for create ops `before_hash is None`; for update ops both set. |
| T3 | `test_commit_creates_backup_on_update` | Pre-existing file → backup made; backup_path recorded on TouchedFile. |
| T4 | `test_commit_skips_failed_acceptance` | Item with target_kb_path traversal → not written; acceptance_results entry passed=False; batch records skip. |
| T5 | `test_commit_hash_mismatch_blocks_write` | File hash differs from prior batch's after_hash → item NOT overwritten; finding=hash_mismatch_pre_write; treated as defer. |
| T6 | `test_commit_resumable_after_partial_failure` | First call: 2 succeed, 1 fail. Second call (different batch_id) on same manifest: previously failed item retried successfully → second batch.approved=1. |
| T7 | `test_commit_duplicate_batch_id_raises` | Calling commit() twice with same batch_id → `ValueError`. |
| T8 | `test_commit_status_transitions` | All items approved + committed → status="complete"; subset → "partial"; zero committed → "failed". |
| T9 | `test_commit_does_not_write_outside_vault` | Adversarial item with `target_kb_path="../escape.md"` → blocked by gate; no file outside vault_root. |
| T10 | `test_commit_render_source_page_format` | Generated source page contains frontmatter + claims + evidence + chapter_ref. |
| T11 | `test_commit_render_concept_page_format` | Generated concept page contains canonical_label + aliases + evidence + cross-lingual match (if any). |
| T12 | `test_no_book_storage_import` | Subprocess assertion. |
| T13 | `test_no_runtime_imports_forbidden` | Subprocess: `fastapi`, `thousand_sunny.*`, `agents.*`, LLM clients absent. |
| T14 | `test_commit_outcome_round_trips` | `model_dump()` + `model_validate()` identity. |
| T15 | `test_commit_idempotent_rerun_with_same_state` | After committing item A, re-running with same item_id and same content → finding="duplicate_target_in_batch" or operation="skip" (your choice; document). |

### Unit tests required (acceptance gate)

| # | Test | Asserts |
|---|---|---|
| GT1 | `test_gate_passes_clean_source_page_item` | All fields valid → `passed=True`, findings empty. |
| GT2 | `test_gate_fails_target_kb_path_traversal` | `target_kb_path="../etc/passwd"` → finding `target_kb_path_traversal`. |
| GT3 | `test_gate_fails_target_kb_path_outside_vault` | Absolute path outside vault_root → finding `target_kb_path_outside_vault`. |
| GT4 | `test_gate_fails_evidence_anchor_locator_empty` | EvidenceAnchor with empty `locator` → finding. |
| GT5 | `test_gate_fails_human_decision_missing` | Item without `human_decision` → finding `human_decision_missing`. |
| GT6 | `test_gate_fails_concept_canonical_match_path_invalid` | Concept item with `match_basis="exact_alias"` but `matched_concept_path` missing — though already #512 V10, gate double-checks (defense in depth). |
| GT7 | `test_gate_fails_duplicate_target_in_batch` | Two items in batch with same `target_kb_path` → finding on second. |

### Self-imposed gates

- [ ] 零新 dependency
- [ ] 全部 model `extra="forbid"`
- [ ] All Literal enums frozen for `schema_version=1`
- [ ] **Closed-set extension protocol**: 新增 `AcceptanceFindingCode` 必須 bump `schema_version`
- [ ] **Narrow exception tuples** in commit / gate code
- [ ] **No real KB write**: tests use `tempfile.TemporaryDirectory()` for `vault_root`
- [ ] **No file-locking dependencies**: pure filesystem; concurrent writes are caller's problem
- [ ] `python -m pytest tests/shared/test_promotion_commit.py tests/shared/test_promotion_acceptance_gate.py -v` 全綠
- [ ] `python -m ruff check shared/promotion_commit.py shared/promotion_acceptance_gate.py shared/schemas/promotion_commit.py tests/shared/test_promotion_*.py` 無 error
- [ ] `python -m ruff format --check ...` clean
- [ ] PR body 含 P7-COMPLETION 區塊

---

## 6. 邊界（不能碰）

| # | Don't | Why |
|---|---|---|
| 1 | ❌ Write to real `KB/Wiki/*` | use fixture vault_root only |
| 2 | ❌ `shared.book_storage` import | N3 contract |
| 3 | ❌ Modify upstream schemas (#509/#510/#511/#512/#513/#514) | upstream contracts frozen unless explicit need |
| 4 | ❌ LLM call | commit is deterministic |
| 5 | ❌ UI / route handler | #516 |
| 6 | ❌ Manifest persistence path/filename convention | caller's choice |
| 7 | ❌ Auto destructive rollback on failure | recovery is reviewed via UI per ADR-024; commit only records errors |
| 8 | ❌ Cross-batch state machine ("retry queue") | callers retry by issuing a new commit() with new batch_id |
| 9 | ❌ Rendering / template engine dependency | use `string.Template` or simple f-strings; no Jinja |
| 10 | ❌ `git add .` outside scope | hygiene |
| 11 | ❌ Bare `except Exception` | #511 F5 lesson |
| 12 | ❌ Use `shared.utils.extract_frontmatter` for parsing manifest content | use strict parser if needed |
| 13 | ❌ Concurrent batch execution within one `commit()` call | sequential per-item; concurrency is caller's concern |
| 14 | ❌ Real-vault path enforcement against the live `KB/Wiki/` directory in tests | parameterize `vault_root` everywhere |

---

## 7. References

### Primary

- ADR: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion (commit + recovery)
- Manifest schema: `shared/schemas/promotion_manifest.py` (#512)
- Source Map schema: `shared/schemas/source_map.py` (#513)
- Concept Promotion schema: `shared/schemas/concept_promotion.py` (#514)

### Pattern reference

- `shared/promotion_preflight.py` (#511) — protocol injection + narrow exception tuples + subprocess gate tests

### Issue / PR

- Issue #515: `[ADR-024 S7] Promotion Commit + Acceptance Gate`
- Parent PRD #508
- Slices 5 (#513) + 6 (#514): merge before dispatch

---

## 8. Triage status

`needs-triage` → 等修修讀 Brief 後決定。**不要**自行 relabel `ready-for-agent`，**不要**直接開始 code。

Slice 7 等 #513 + #514 merge 後再 dispatch (commit consumes both their schemas via #512). 預估 ~900 LOC + tests (含 acceptance gate + render helpers + fixtures).
