# Agent Brief — N511 Promotion Preflight (ADR-024 Slice 3) — v2

**Issue:** [#511](https://github.com/shosho-chang/nakama/issues/511)
**Parent PRD:** #508
**Slice of:** ADR-024 (`docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`)
**Branch:** `impl/N511-promotion-preflight`
**Worktree (host):** `E:/nakama-N511-promotion-preflight`
**Drafted:** 2026-05-09 v1; revised 2026-05-09 v2 (post-Codex review)
**Pending corrections memo:** `memory/claude/project_n511_v2_revisions_pending.md` (in main)
**Status:** v2 draft — awaiting 修修 review before relabeling `ready-for-agent`

P9 六要素。Brief 是 self-contained handoff，不靠群聊歷史也能上手。

---

## 0. Revision summary

### v1 → v2 (post-Codex review)

Two design errors corrected:

| Error | v1 framing | v2 fix | Source |
|---|---|---|---|
| `has_evidence_track=False` default | v1 §4.2 mapped moderate-large content with `has_evidence_track=False` to `partial_promotion_only` as the standard recommendation. | **Wrong.** Per ADR-024 + `agents/robin/CONTEXT.md` § Source Promotion, missing evidence means **defer** / needs evidence, **not commit**. v2 inverts the mapping: `has_evidence_track=False` defaults to `defer` (long content) or `annotation_only_sync` (short content). **`partial_promotion_only` is REMOVED from the deterministic preflight `recommended_action` enum** — narrow exception that requires explicit human override, deferred to #515 Commit Gate / #516 Review UI. | `project_n511_v2_revisions_pending.md` Correction 1 |
| EPUB inspector path | v1 §3 said "load via `book_storage.read_book_blob(book_id, lang=...)`" — derives `book_id` by parsing `ReadingSource.source_id`. | **Violates #509 N3 contract** (`source_id` is logical identity, not filesystem lookup). v2 EPUB inspector takes an **injected `blob_loader: Callable[[str], bytes]`** keyed on `variant.path`. Preflight never imports `book_storage` and never parses `source_id`. T12 enforces import absence. | `project_n511_v2_revisions_pending.md` Correction 2 |

Other v1 sections substantively unchanged: §0 enumeration scope decision stays; §6 boundaries stay; T1-T13 numbering preserved with policy-table-aware updates.

---

## 0.1 Scope anchor

Per ADR-024 §Decision: "Source Promotion requires a lightweight preflight before any expensive analysis job."

Per `agents/robin/CONTEXT.md` § Source Promotion: preflight inspects metadata, chapter/section count, word count, language/evidence track availability, rough token/cost/time estimate, and structural risks (weak TOC, OCR issues, mixed language, missing original track) **without heavy LLM spend**. Full promotion analysis is a queued cancellable job started only after explicit confirmation.

Slice 3 (#511) builds the **deterministic preflight** for one resolved Reading Source. Output is a `PreflightReport` value-object describing language, evidence-track status, approximate size, structural risks, and a recommended next action. **No LLM call**, **no KB write**, **no UI**.

### Anchor on `#509` Reading Source Registry

`#509` shipped at `0f2742f`. `#511` consumes:

- `ReadingSourceRegistry.resolve(key) -> ReadingSource | None`
- `ReadingSource.has_evidence_track: bool`
- `ReadingSource.evidence_reason: Literal["no_original_uploaded", "bilingual_only_inbox"] | None`
- `ReadingSource.primary_lang: str` (Q2 contract: in case (b) bilingual-only inbox, this is **low-confidence** — preflight MUST consult `evidence_reason == "bilingual_only_inbox"` to know).
- `ReadingSource.variants[*].path` — preflight reads files at these paths via the injected `blob_loader` (see §3 / §4.3).

`#511` does NOT re-derive Reading Source identity; it accepts a `ReadingSource` argument (or a `SourceKey` and resolves it via the registry). It does NOT parse `source_id` to recover any namespace-prefix internals (per #509 N3 contract).

### Scope decision: NO enumeration API in `#511`

Per `#509` Q3 拍板: "`#511` extends registry surface when needed". Decision for **this** Brief: **#511 does NOT need enumeration**.

- Issue #511 AC: "Preflight can run against a normalized Reading Source." Singular.
- Preflight is per-source. The caller (UI / batch script / future #516 Review UI) decides which source to preflight.
- Listing all candidate sources is a separate concern (UI affordance, not preflight logic).

If a future caller needs a `list_book_keys()` / `list_inbox_keys()` / `iter_all_keys()` API, it lives in:

- A new method on `ReadingSourceRegistry` (extension per Q3), OR
- A new `ReadingSourceCatalog` class.

Either way, that's a separate slice. **`#511` MUST NOT add enumeration silently** — if the implementer finds they need enumeration, raise it as a blocker before coding, not as a quiet addition. T13 reflectively asserts no `list_*` / `iter_*` / `enumerate_*` methods on `PromotionPreflight`.

---

## 1. 目標

提供 `shared.promotion_preflight.PromotionPreflight.run(reading_source) -> PreflightReport`：

- 單一 `ReadingSource` input → 單一 `PreflightReport` output。Pure deterministic — 無 LLM、無 KB write、無 vault mutation。
- Report 內容：language fact、evidence-track 狀態、approximate size（chapter / word count）、structural risks（弱 TOC、OCR、mixed language、缺 original track）、recommended next action。
- **`has_evidence_track=False` policy** 預設 `defer` / `annotation_only_sync`；`proceed_full_promotion` 永遠 require `has_evidence_track=True`（hard invariant）。**`partial_promotion_only` REMOVED from this slice's enum** — see §4.2.
- **EPUB / markdown inspectors** 透過 injected `blob_loader: Callable[[str], bytes]` 讀取，不直接 import `book_storage`，不直接 parse `source_id`。
- 不啟動 commit、不寫 Manifest、不跑 LLM、不渲染 UI。
- 不啟動 enumeration（per §0 scope decision）。

---

## 2. 範圍

### Add

| Path | Action | Reason |
|---|---|---|
| `shared/schemas/preflight_report.py` | **新增** | Pydantic schema: `PreflightReport`, `PreflightSizeSummary`, `PreflightRiskFlag`, `PreflightAction` (5 values, no `partial_promotion_only`), `PreflightReason` Literals. `extra="forbid"` + value-object `frozen=True`. |
| `shared/promotion_preflight.py` | **新增** | `PromotionPreflight` class: `run(reading_source: ReadingSource) -> PreflightReport`. Per-variant inspectors (epub TOC + word-count, markdown section + word-count). EPUB inspector reads via injected `blob_loader`. |
| `tests/shared/test_promotion_preflight.py` | **新增** | Unit tests covering high-quality, low-quality, missing-evidence, weak-structure, bilingual-only-inbox, and language fixtures. Zero `fastapi` / `thousand_sunny` / `book_storage` import. |
| `tests/fixtures/preflight/` | **新增** | epub + markdown fixtures spanning the test matrix. epubs built dynamically via zipfile (mirror #509 conftest pattern). |

### Read-only consumption

| Path | Why |
|---|---|
| `shared/reading_source_registry.py` (#509) | `ReadingSourceRegistry.resolve(...)`. |
| `shared/schemas/reading_source.py` (#509) | `ReadingSource` value-object shape; `has_evidence_track`, `evidence_reason`, `variants[*].path`, `primary_lang`. |
| `shared/epub_metadata.py` | `extract_metadata` for chapter / TOC inspection (extend if needed via separate helper, NOT modify in-place). |
| `shared/utils.py` | `read_text` for inbox markdown. |

### Out of scope

- Promotion Manifest schema (#512)
- Source Map Builder (#513)
- Concept Promotion (#514)
- Promotion Commit + Acceptance Gate (#515 — `partial_promotion_only` belongs here as an explicit-override action, not in #511)
- Promotion Review UI (#516)
- Reading Context Package (#517)
- Reader UI changes
- Enumeration / listing API (per §0 scope decision)
- Caching / lru_cache on preflight results (preflight is cheap; caller manages invalidation)
- LLM call (preflight is deterministic per CONTEXT.md "without heavy LLM spend")
- Cost / token estimate beyond a rough word-count → token heuristic (no `tiktoken` dependency in this slice — accept rough char-based heuristic)
- **`book_storage` import / use in preflight or tests** (per §3 / Correction 2)

---

## 3. 輸入

### Primary contracts

- **`ReadingSource`**: from `#509`. Fields preflight reads:
  - `kind` ∈ {`ebook`, `inbox_document`}
  - `primary_lang`: BCP-47 short. **Low-confidence when `evidence_reason == "bilingual_only_inbox"`** (Q2 contract from #509).
  - `has_evidence_track: bool`
  - `evidence_reason: Literal[...] | None`
  - `variants: list[SourceVariant]` — preflight inspects the file at each `variants[i].path` via the injected `blob_loader`.
  - **NOT consumed**: `source_id`. `#511` MUST NOT parse it (per #509 N3 contract — `source_id` is logical identity, not filesystem lookup key).

### Variant inspectors (read via injected `blob_loader`)

- **EPUB inspector** (`kind="ebook"`):
  - Receives `variant: SourceVariant` (with `.path`, e.g. `data/books/{book_id}/original.epub`).
  - Calls `blob = self._blob_loader(variant.path)` to get bytes.
  - Parses OPF / spine / TOC via `xml.etree` (no `BeautifulSoup`); rough word-count via stripped text.
  - **Failure mode**: any IO / parse failure → return `PreflightReport(error=..., recommended_action="defer", ...)`, NOT raise.
- **Markdown inspector** (`kind="inbox_document"`):
  - Receives `variant: SourceVariant` (with `.path`, e.g. `Inbox/kb/foo-bilingual.md`).
  - Calls `blob = self._blob_loader(variant.path)`; decodes as UTF-8 string.
  - Counts `^#` headings (sections) + word count (whitespace-split); inspects frontmatter via `_strict_parse_frontmatter` helper (mirror #509 — borrow the helper or import as private if it's stable).
  - Same failure mode as EPUB inspector.

### `blob_loader` injection contract

```python
from typing import Callable

BlobLoader = Callable[[str], bytes]
"""Maps a SourceVariant.path string to the raw file bytes. Production callers
(thousand_sunny endpoints) inject a loader that resolves vault_root + path
and reads from disk. Tests inject an in-memory dict-backed loader.

Preflight never imports book_storage / vault helpers. Path resolution is
the loader's job, not preflight's. This keeps preflight pure-deterministic
on its inputs and import-light (asserted by T12 + T14).
"""
```

### Documentation hierarchy

- ADR-024 (main, PR #441 merged) — overall ADR
- `agents/robin/CONTEXT.md` § Source Promotion — preflight scope
- `shared/schemas/reading_source.py` (#509 in main) — `ReadingSource` shape; `source_id` is logical-identity (do NOT parse)
- `shared/reading_source_registry.py:181-200, 251-273` (in main) — variant path semantics for ebook + inbox
- `docs/principles/schemas.md` — schema discipline

---

## 4. 輸出

### 4.1 Schema sketch — `shared/schemas/preflight_report.py`

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


PreflightAction = Literal[
    "proceed_full_promotion",
    "proceed_with_warnings",
    "annotation_only_sync",
    "defer",
    "skip",
]
"""Closed for schema_version=1.

- proceed_full_promotion : has_evidence_track=True, no high-severity risks.
- proceed_with_warnings  : has_evidence_track=True, ≥1 medium-severity risk.
- annotation_only_sync   : has_evidence_track=False, content is short/structural risk;
                           Reader Overlay only (annotations sync to existing pages).
                           Promotion-side does NOT proceed.
- defer                  : has_evidence_track=False AND moderate-to-large content;
                           OR has_evidence_track=True with high-severity risks;
                           OR low-confidence signals; OR inspector error.
                           Wait for more reading / explicit user resolution.
- skip                   : irrelevant content (e.g. <200 words, junk import).

NOTE: `partial_promotion_only` is intentionally absent from this enum.
That state requires an explicit human override / waiver and is owned by
#515 Commit Gate / #516 Review UI, not by deterministic preflight.
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
"""Closed for schema_version=1. Multiple reasons may co-exist on one report."""

PreflightRiskCode = Literal[
    "weak_toc",
    "ocr_artifact",
    "mixed_language",
    "missing_evidence",
    "low_signal_count",
    "frontmatter_minimal",
    "other",
]

PreflightRiskSeverity = Literal["low", "medium", "high"]


class PreflightSizeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    chapter_count: int = Field(ge=0)
    word_count_estimate: int = Field(ge=0)
    char_count_estimate: int = Field(ge=0)
    rough_token_estimate: int = Field(ge=0)  # char_count // 4 heuristic


class PreflightRiskFlag(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: PreflightRiskCode
    severity: PreflightRiskSeverity
    description: str


class PreflightReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1

    source_id: str   # ReadingSource.source_id (transport string only — NOT parsed)
    primary_lang: str
    primary_lang_confidence: Literal["high", "low"]
    """`low` when ReadingSource.evidence_reason == 'bilingual_only_inbox'
    (per #509 Q2 contract); `high` otherwise.
    """

    has_evidence_track: bool
    evidence_reason: str | None  # mirror ReadingSource

    size: PreflightSizeSummary
    risks: list[PreflightRiskFlag] = Field(default_factory=list)
    reasons: list[PreflightReason] = Field(default_factory=list)
    recommended_action: PreflightAction
    error: str | None = None
    """Set when an inspector failed (IO error, malformed blob); recommended_action
    falls back to 'defer'. None on success.
    """

    @model_validator(mode="after")
    def _hard_invariant_full_promotion_requires_evidence(self) -> "PreflightReport":
        if self.recommended_action == "proceed_full_promotion" and not self.has_evidence_track:
            raise ValueError(
                "recommended_action='proceed_full_promotion' requires has_evidence_track=True; "
                "missing evidence must default to 'defer' or 'annotation_only_sync'"
            )
        return self
```

### 4.2 Action mapping table (deterministic policy)

Per ADR-024 + `agents/robin/CONTEXT.md`: factual claims require evidence. Preflight defaults missing-evidence cases to `defer` / `annotation_only_sync` — never `partial_promotion_only` (which is removed from this slice's enum entirely; see §4.1 docstring).

| Inputs | `recommended_action` | `reasons` |
|---|---|---|
| `has_evidence_track=True` AND no high-severity risks | `proceed_full_promotion` | `["ok"]` |
| `has_evidence_track=True` AND ≥1 medium-severity risk AND no high-severity | `proceed_with_warnings` | risk codes |
| `has_evidence_track=True` AND ≥1 high-severity risk | `defer` | risk codes |
| `has_evidence_track=False` AND `word_count_estimate < 1000` | `annotation_only_sync` | `["missing_evidence_track", "very_short"]` |
| `has_evidence_track=False` AND (`weak_toc` OR `no_chapters_detected`) AND `word_count_estimate < 5000` | `annotation_only_sync` | `["missing_evidence_track", "weak_toc"]` |
| `has_evidence_track=False` AND `word_count_estimate >= 1000` (and no auto-`annotation_only_sync` trigger above) | `defer` | `["missing_evidence_track"]` (+ `"low_confidence_lang"` if `evidence_reason == "bilingual_only_inbox"`) |
| inspector `error` set | `defer` | `["frontmatter_minimal"]` (placeholder; details in `error`) |
| `word_count_estimate < 200` AND no errors | `skip` | `["very_short"]` |

**Hard invariant (Pydantic-enforced, T7 asserts)**:

- `recommended_action == "proceed_full_promotion"` ⇒ `has_evidence_track == True`
- `recommended_action ∉ {proceed_full_promotion, proceed_with_warnings, annotation_only_sync, defer, skip}` is impossible (Literal enum; T-N1 `extra=forbid` covers).

**Policy contract for downstream slices** (read by #513, #514, #515, #516):

- `proceed_full_promotion` / `proceed_with_warnings` → #513 + #514 produce review items; #515 commits per Manifest.
- `annotation_only_sync` → only Reading Overlay (#510) sync runs; #513 + #514 do nothing.
- `defer` → no promotion-side work proceeds. User must address cause (upload original, etc.) and re-run preflight.
- `skip` → irrelevant; nothing runs.
- (`partial_promotion_only` — if the user wants this state, it's owned by #515 Commit Gate, set by an explicit human override field on the Manifest. Preflight does NOT emit it.)

### 4.3 Service API

```python
# shared/promotion_preflight.py
from collections.abc import Callable
from pathlib import Path

from shared.reading_source_registry import ReadingSourceRegistry
from shared.schemas.preflight_report import PreflightReport
from shared.schemas.reading_source import ReadingSource


BlobLoader = Callable[[str], bytes]


class PromotionPreflight:
    def __init__(
        self,
        blob_loader: BlobLoader,
        registry: ReadingSourceRegistry | None = None,
    ) -> None:
        """`blob_loader` resolves a SourceVariant.path → file bytes.
        Production wiring at the call site (e.g. `thousand_sunny/routers/...`)
        composes it from the project's vault helpers; tests inject in-memory
        loaders.

        `registry` is optional — preflight does NOT call it on the hot path
        (input is already a ReadingSource); kept as a convenience for callers
        that want to chain `resolve(...) → run(...)` via this class.
        """
        self._blob_loader = blob_loader
        self._registry = registry

    def run(self, reading_source: ReadingSource) -> PreflightReport:
        """Pure deterministic. ReadingSource resolution is the caller's job
        (use `registry.resolve(key)` first). Preflight inspects only the
        variants attached to the ReadingSource via `self._blob_loader`.
        """
        ...
```

No public enumeration / listing methods on `PromotionPreflight` (T13 enforces).

No `book_storage` import anywhere in `shared/promotion_preflight.py` or `shared/schemas/preflight_report.py` (T14 enforces).

---

## 5. 驗收

### Issue #511 列出的 4 條 AC（必過）

- [ ] Preflight can run against a normalized Reading Source.
- [ ] Preflight reports language, evidence-track status, approximate size, structural risk, and recommended next action.
- [ ] Preflight does not write formal `KB/Wiki` pages.
- [ ] Tests cover high-quality, low-quality, missing evidence, and weak-structure fixtures.

### Specific unit tests

| # | Test | Setup | Asserts |
|---|---|---|---|
| T1 | `test_preflight_high_quality_ebook` | `ReadingSource(kind="ebook", has_evidence_track=True, primary_lang="en", variants=<original+display>)` with full TOC, ~50k words. blob_loader returns crafted EPUB bytes. | `recommended_action == "proceed_full_promotion"`; `reasons == ["ok"]`; no high-severity risks. |
| T2 | `test_preflight_high_quality_inbox` | inbox `ReadingSource(kind="inbox_document", has_evidence_track=True)` with frontmatter `lang: en`, ~5k words, multiple sections. | `recommended_action == "proceed_full_promotion"`. |
| T3 | `test_preflight_bilingual_only_inbox_defaults_to_defer` | inbox `ReadingSource(kind="inbox_document", has_evidence_track=False, evidence_reason="bilingual_only_inbox")`, ~5k words. | `recommended_action == "defer"`; `reasons` contains `"missing_evidence_track"` AND `"low_confidence_lang"`; `primary_lang_confidence == "low"`. |
| T4 | `test_preflight_no_original_uploaded_defaults_to_defer` | ebook `ReadingSource(has_evidence_track=False, evidence_reason="no_original_uploaded")`, large bilingual blob. | `recommended_action == "defer"`; `reasons` contains `"missing_evidence_track"`; `primary_lang_confidence == "high"`. |
| T5 | `test_preflight_short_no_evidence_routes_to_annotation_only_sync` | inbox `ReadingSource(has_evidence_track=False)`, ~600 words. | `recommended_action == "annotation_only_sync"`; `reasons` contains `"missing_evidence_track"` + `"very_short"`. |
| T6 | `test_preflight_short_content_skips` | inbox source, ~150 words. | `recommended_action == "skip"`; `reasons == ["very_short"]`. |
| T7 | `test_full_promotion_requires_evidence_track` (Pydantic invariant) | construct `PreflightReport(recommended_action="proceed_full_promotion", has_evidence_track=False, ...)`. | `pytest.raises(ValidationError)` with message mentioning `proceed_full_promotion`. |
| T8 | `test_preflight_lang_normalization_high_confidence` | parametrized over `primary_lang ∈ {en, en-US, zh-Hant, zh-CN}` with `evidence_reason=None`. | `primary_lang_confidence == "high"` in all cases. |
| T9 | `test_preflight_inspector_error_falls_back_to_defer` | mock `blob_loader` raises `OSError` for a variant path. | `recommended_action == "defer"`; `error` field is populated; logs WARNING. |
| T10 | `test_preflight_no_kb_write` | Run preflight; assert vault directory has no new files. | No mutations. |
| T11 | `test_preflight_no_llm_call` | Use a transport-level patch (mock `anthropic.Client` or similar) — assert no calls made. Alternative: subprocess-import check that no LLM client module is imported. | No LLM call. |
| T12 | `test_preflight_module_no_fastapi_or_thousand_sunny_imports` | subprocess `python -c "import shared.promotion_preflight, sys; print([m for m in sys.modules if m.startswith(('fastapi','thousand_sunny'))])"`. | Output is `[]`. |
| T13 | `test_no_enumeration_api_exposed` | `dir(PromotionPreflight)` must NOT contain `list_*` / `iter_*` / `enumerate_*` methods. | Per §0 scope decision. Asserted by reflection. |
| T14 | `test_preflight_module_no_book_storage_import` | subprocess `python -c "import shared.promotion_preflight, sys; print([m for m in sys.modules if m.startswith('shared.book_storage')])"`. | Output is `[]`. **Asserts Correction 2**: preflight reads via injected `blob_loader`, never via `book_storage`. |
| T15 | `test_preflight_no_partial_promotion_only_in_action_enum` | Reflectively introspect `PreflightAction.__args__` (Literal type). | `"partial_promotion_only"` NOT present. Per Correction 1 (the action requires explicit human override; lives in #515, not here). |

### Self-imposed gates

- [ ] No new dependency (no `tiktoken`; rough char→token heuristic is `chars // 4`).
- [ ] No LLM call inside `PromotionPreflight.run` (T11 asserts).
- [ ] No KB / vault write inside preflight (T10 asserts).
- [ ] No enumeration API on `PromotionPreflight` (T13 asserts; per §0 scope decision).
- [ ] No `book_storage` import in preflight module (T14 asserts; per Correction 2).
- [ ] No `source_id` parsing — preflight reads `variant.path` only (asserted by code review + grep `source_id` in `shared/promotion_preflight.py` returning only field-access references).
- [ ] `has_evidence_track=False` policy fully implemented per §4.2 mapping table.
- [ ] Hard invariant `proceed_full_promotion ⇒ has_evidence_track=True` enforced via `model_validator` (T7 asserts).
- [ ] `partial_promotion_only` NOT in `PreflightAction` Literal (T15 asserts; per Correction 1).
- [ ] Schema closed-set extension protocol on every Literal (mirror #509 N6 contract).
- [ ] Test file imports zero `fastapi` / `thousand_sunny` symbols (T12 asserts).
- [ ] `python -m pytest tests/shared/test_promotion_preflight.py -v` clean.
- [ ] `python -m ruff check shared/ tests/shared/` clean.
- [ ] `python -m ruff format --check ...` clean.
- [ ] PR body contains a P7-COMPLETION self-review block.

---

## 6. 邊界（不能碰）

| # | Don't | Why |
|---|---|---|
| 1 | ❌ Promotion Manifest writes (#512 schema; #515 commit) | wrong slice |
| 2 | ❌ Source Map building (#513) | wrong slice |
| 3 | ❌ Concept Promotion (#514) | wrong slice |
| 4 | ❌ Commit / KB writes (#515) | wrong slice |
| 5 | ❌ Review UI (#516) | wrong slice; `partial_promotion_only` override lives there |
| 6 | ❌ Reader Overlay changes (#510) | wrong slice |
| 7 | ❌ LLM call | preflight is deterministic per CONTEXT.md |
| 8 | ❌ Vault / KB writes | preflight is read-only |
| 9 | ❌ ReadingSource re-derivation | accept `ReadingSource` as input; do not re-resolve |
| 10 | ❌ Enumeration API on `PromotionPreflight` | per §0 scope decision; future caller adds via Q3 |
| 11 | ❌ `tiktoken` / heavy tokenizer dependency | rough char-based heuristic is enough for #511 |
| 12 | ❌ Caching / lru_cache | caller manages invalidation |
| 13 | ❌ Mutate `shared/epub_metadata.py` in-place | add new helper if needed; do not change `extract_metadata` signature |
| 14 | ❌ `book_storage` import in `shared/promotion_preflight.py` or `shared/schemas/preflight_report.py` | per Correction 2; T14 asserts |
| 15 | ❌ `book_storage.write_*` / DB writes | read-only |
| 16 | ❌ `_resolve_book` / `_resolve_inbox` re-implementation | `ReadingSource` is the input, not `BookKey` / `InboxKey` |
| 17 | ❌ Closed-set Literal extension without schema bump | mirror #509 N6 protocol |
| 18 | ❌ `partial_promotion_only` in `PreflightAction` enum | per Correction 1; that state requires explicit human override and lives in #515 |
| 19 | ❌ `source_id` parsing of any kind | violates #509 N3 contract — `source_id` is logical identity |

---

## 7. References

### Primary

- ADR: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion (preflight scope)
- ReadingSource (#509): `shared/schemas/reading_source.py`, `shared/reading_source_registry.py` (main `0f2742f`)
- Schemas principle: `docs/principles/schemas.md`
- **Pending corrections memo: `memory/claude/project_n511_v2_revisions_pending.md`** (in main; v2 derives from this)

### Issue / PR

- Issue #511: `[ADR-024 S3] Promotion Preflight`
- Parent PRD #508: `Source Promotion and Reading Context Package`
- Slice 1 (#509) ship: PR #518 squash-merged at `0f2742f` (2026-05-09)
- Investigation note + N510 v1 + N511 v1 memo + memory in main: PR #519 squash-merged at `b981d2e` (2026-05-09)

### Related upcoming

- #512 (`docs/task-prompts/N512-promotion-manifest-schema.md` v2 in main) — manifest schema; preflight does NOT consume it but `recommended_action` informs which review items #513 / #514 produce.
- #515 (Commit Gate) — owns the explicit-human-override path that maps a manifest to `partial_promotion_only`. Not in #511 scope.

---

## 8. Triage status

`needs-triage` → 等修修讀 v2 Brief 後決定。**不要**自行 relabel `ready-for-agent`，**不要**直接開始 code。

實作前 reviewer 注意：

- §4.2 `has_evidence_track=False` 的 thresholds（5000 words / 1000 words / 200 words）是 Brief 起手值；可在 review 階段調整，但 **hard invariants** (`proceed_full_promotion ⇒ has_evidence_track=True`; `partial_promotion_only` 不在 enum 內) 不可放寬。
- §0 enumeration scope decision 需要修修確認。若認為 `#511` 應該包含 `list_*` API（為了 #516 UI 鋪路），請在 review 時 explicit 標出；本 Brief 預設不做。
- §4.3 `blob_loader` injection 模式是 Correction 2 的解。production 端的 wiring（`thousand_sunny` 端點怎麼組 loader）刻意不在本 slice 處理 — 由消費者 slice (`#516` Review UI / 任何 batch caller) 在他們自己的 PR 補。

LOC estimate: ~400-500 + tests.

Dispatch decision: **Suitable for sandcastle batch dispatch** after relabel `ready-for-agent` (per `feedback_sandcastle_default.md` default = sandcastle rule). Slice is bounded — schema + service + variant inspectors + tests.
