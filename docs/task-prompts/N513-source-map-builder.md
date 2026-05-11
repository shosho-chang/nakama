# Agent Brief — N513 Source Map Builder (ADR-024 Slice 5)

**Issue:** [#513](https://github.com/shosho-chang/nakama/issues/513)
**Parent PRD:** #508
**Slice of:** ADR-024 (`docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`)
**Branch:** `impl/N513-source-map-builder`
**Worktree (host):** `E:/nakama-N513-source-map-builder`
**Drafted:** 2026-05-10
**Status:** awaiting 修修 review before relabeling `ready-for-agent`

P9 六要素。Brief 是 self-contained handoff，不靠群聊歷史也能上手。

---

## 0. Scope anchor

ADR-024 §Decision: "Promotion output is a **claim-dense source map**, not a full-text mirror. Full originals may remain in private evidence tracks, but `KB/Wiki/Sources/...` should preserve chapter / section structure, claims, key numbers, figures/tables summaries, short quote anchors, Concept / Entity links, and coverage manifests without distributing long verbatim source text."

`agents/robin/CONTEXT.md` § Source Promotion: "Promotion commit 的 source 輸出層級：長 source（book / textbook / long report）產生 chapter/section-level Source pages + index/Book Entity 總覽；短 source（article / short document）可維持 single Source page + section anchors。不要把長書寫成單一巨頁；`mentioned_in` 應盡量指到具體章節/section。"

Slice 5 (#513) builds the **deterministic claim-dense map builder** for one resolved Reading Source. Output is `list[SourcePageReviewItem]` candidates ready to be embedded into a `PromotionManifest` (#512). **No LLM call**, **no KB write**, **no UI**, **no manifest persistence**.

### Where Slice 1-4 land (in main as of 2026-05-10)

| Slice | PR | Commit | Surface |
|---|---|---|---|
| #509 ReadingSource Registry | #518 | `0f2742f` | `shared/reading_source_registry.py`, `shared/schemas/reading_source.py` |
| #510 Reading Overlay V3 | #522 | `e81e2e7` | `agents/robin/*` overlay paths |
| #511 Promotion Preflight | #523 | `4094239` | `shared/promotion_preflight.py`, `shared/schemas/preflight_report.py` |
| #512 Promotion Manifest Schema | #519 | `d0e1190` | `shared/schemas/promotion_manifest.py` |

`#513` consumes `ReadingSource.source_id` (transport only — no parse), `ReadingSource.variants[*].path` (via injected `blob_loader`), and emits `SourcePageReviewItem` instances per #512 schema. It does NOT consume `PreflightReport` directly — preflight gating is the caller's responsibility (see §3).

### Pipeline anchor

CONTENT-PIPELINE.md Stage 3 (整合). Source Map Builder is the upstream half of Source Promotion: it converts a Reading Source into reviewable per-page candidates, before #515 Acceptance Gate / Commit and #516 Review UI.

---

## 1. 目標 (Goal)

提供 `shared.source_map_builder.SourceMapBuilder.build(reading_source, extractor) -> SourceMapBuildResult`：

- 單一 `ReadingSource` input → ordered list of `SourcePageReviewItem` candidates (per #512 schema).
- **Long source** (multi-chapter ebook / long inbox markdown): one `SourcePageReviewItem` per chapter or section.
- **Short source** (single-section inbox doc, very short ebook): one consolidated `SourcePageReviewItem`.
- Each item carries: `recommendation`, `action`, `reason`, `evidence` (≥1 EvidenceAnchor for include candidates), `risk`, `confidence`, `source_importance`, `reader_salience`, `target_kb_path`, `chapter_ref`.
- **Output is claim-dense, not full-text**: `SourcePageReviewItem.reason` and `EvidenceAnchor.excerpt` are bounded; total emitted text MUST NOT exceed a configurable per-page char budget (default 800 chars excerpt, 200 chars reason).
- Pure deterministic — uses an injected **`extractor: ClaimExtractor` protocol** for claim/figure/table extraction. No LLM call inside this slice; tests use a deterministic fixture extractor.

不啟動 commit、不寫 Manifest、不跑 LLM、不渲染 UI、不寫 vault。

---

## 2. 範圍

### Add

| Path | Action | Reason |
|---|---|---|
| `shared/source_map_builder.py` | **新增** | `SourceMapBuilder` class + `ClaimExtractor` Protocol + `SourceMapBuildResult` value-object. |
| `shared/schemas/source_map.py` | **新增** | `ClaimExtractionResult`, `ChapterCandidate`, `SourceMapBuildResult` Pydantic value-objects. ALL `extra="forbid"` + `frozen=True`. |
| `tests/shared/test_source_map_builder.py` | **新增** | Unit tests covering long/short source, claim density invariant, evidence anchor presence, deterministic fake extractor injection, no `book_storage` import, no LLM client import, no fastapi/thousand_sunny import. |
| `tests/fixtures/source_map/` | **新增** | epub + markdown fixtures spanning the test matrix. epubs built dynamically via zipfile (mirror #511 conftest pattern). |

### Read-only consumption

| Path | Why |
|---|---|
| `shared/schemas/reading_source.py` (#509) | `ReadingSource.source_id`, `variants[*].path`, `kind`, `primary_lang`, `has_evidence_track`. |
| `shared/schemas/promotion_manifest.py` (#512) | Emit `SourcePageReviewItem`, `EvidenceAnchor`, `RiskFlag` per closed-set Literal contract. |
| `shared/schemas/preflight_report.py` (#511) | Read-only reference for risk_code parity (`weak_toc`, `ocr_artifact`, etc.). |
| `shared/epub_metadata.py` | `extract_metadata` for chapter / TOC / lang. |
| `shared/utils.py` | `read_text` for inbox markdown (note: uses `extract_frontmatter` cautiously — see #511 F6 lesson; if frontmatter parsing is needed, use a strict local helper). |

### 完全不碰

- `shared/book_storage.py` — same N3 contract as #511. Inspector reads via `blob_loader: Callable[[str], bytes]`.
- `agents/robin/*` — promotion route handlers / overlay logic.
- `thousand_sunny/routers/*` — UI is #516.
- `KB/Wiki/*` — materialized output is #515 commit.
- `shared/promotion_manifest_storage.py` (if it existed) — #515.
- 任何 LLM client (`anthropic`, `openai`, `claude_client`).
- 任何 vault / DB write、任何 file hashing 實際執行。

---

## 3. 輸入

### Caller contract

```python
class ClaimExtractor(Protocol):
    """Pure protocol — implementations may be LLM-backed or deterministic.

    Slice 5 ships ONLY the protocol + a deterministic fixture extractor for
    tests. LLM-backed implementation is the caller's responsibility (lives
    outside this slice, e.g. a future agents/robin/source_map_extractor.py).
    """

    def extract(
        self,
        chapter_text: str,
        chapter_title: str,
        primary_lang: str,
    ) -> ClaimExtractionResult:
        ...
```

`SourceMapBuilder.build` signature:

```python
def build(
    self,
    reading_source: ReadingSource,
    extractor: ClaimExtractor,
    *,
    max_excerpt_chars: int = 800,
    max_reason_chars: int = 200,
    min_chapter_chars: int = 1500,    # below → consolidate adjacent chapters
) -> SourceMapBuildResult:
    ...
```

`SourceMapBuilder.__init__(blob_loader: Callable[[str], bytes])` — same injection pattern as #511 `PromotionPreflight`, no `book_storage` runtime import.

### Caller responsibilities (out of scope for this slice)

- **Preflight gating**: caller MUST consult `PreflightReport.recommended_action` and only call `SourceMapBuilder.build` when `recommended_action ∈ {proceed_full_promotion, proceed_with_warnings}`. Builder does NOT re-check preflight status — caller invariant.
- **Extractor selection**: caller chooses LLM vs deterministic extractor. Builder is agnostic.
- **Manifest assembly**: caller wraps `SourceMapBuildResult.items` into a `PromotionManifest` with `RecommenderMetadata` + ids. Builder produces items only.

### Documentation hierarchy

- ADR-024 §Decision (claim-dense, not full-text mirror)
- `agents/robin/CONTEXT.md` § Source Promotion (chapter/section vs single-page rule, mentioned_in granularity)
- `shared/schemas/promotion_manifest.py` (`SourcePageReviewItem`, `EvidenceAnchor`, `RiskFlag` shapes)
- `shared/schemas/reading_source.py` (`ReadingSource` consumed shape)

---

## 4. 輸出

### 4.1 Schema sketch (additions to `shared/schemas/source_map.py`)

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from shared.schemas.promotion_manifest import (
    EvidenceAnchor, RiskFlag, SourcePageReviewItem,
)


class ChapterCandidate(BaseModel):
    """Internal builder intermediate — what the builder identified before
    extraction. Carried into ClaimExtractor input.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    chapter_ref: str
    """Free-form locator: 'ch-3' / 'sec-2.1' / 'whole'. Builder owns format."""

    chapter_title: str
    chapter_text: str
    """Raw chapter text. NOT stored downstream — builder discards after extraction."""

    char_count: int = Field(ge=0)
    word_count: int = Field(ge=0)


class ClaimExtractionResult(BaseModel):
    """One ClaimExtractor.extract() output — protocol return value."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: list[str] = Field(default_factory=list)
    """Each claim ≤ 200 chars. Builder enforces per-item budget downstream."""

    key_numbers: list[str] = Field(default_factory=list)
    """e.g. '7.5 mmol/L'. Empty if none found. ≤ 50 chars each."""

    figure_summaries: list[str] = Field(default_factory=list)
    table_summaries: list[str] = Field(default_factory=list)
    short_quotes: list[QuoteAnchor] = Field(default_factory=list)
    """Quote excerpts with locator strings. Used to build EvidenceAnchor."""

    extraction_confidence: float = Field(ge=0.0, le=1.0)


class QuoteAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    excerpt: str  # ≤ max_excerpt_chars
    locator: str  # CFI / line-range / xpath
    confidence: float = Field(ge=0.0, le=1.0)


class SourceMapBuildResult(BaseModel):
    """Builder output. Caller wraps `items` into a PromotionManifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    source_id: str  # mirrors ReadingSource.source_id (transport only)
    primary_lang: str
    has_evidence_track: bool
    chapters_inspected: int = Field(ge=0)
    items: list[SourcePageReviewItem] = Field(default_factory=list)
    """Ordered: index/Book Entity overview first (if long source), then per-chapter."""

    risks: list[RiskFlag] = Field(default_factory=list)
    """Build-level risks (e.g. weak_toc, OCR detected during text extraction).
    Distinct from per-item risk; caller decides how to surface."""

    error: str | None = None
    """Set on build failure (extractor raised, blob unreadable). On error,
    items is empty and caller routes to defer."""
```

### 4.2 Builder behavior (top-down first-match)

1. **Input gate**: `reading_source` MUST have `has_evidence_track=True` OR caller MUST have explicitly chosen `annotation_only_sync` upstream. Builder asserts the former; raises `ValueError` if `has_evidence_track=False` (callers seeking annotation-only sync must use a different path — Source Map Builder is for evidence-backed promotion only).
2. **Variant selection**: pick `variants[*]` where `role='original'` (since `has_evidence_track=True` ⇒ exactly one such variant exists per #509 invariant).
3. **Inspect**:
   - `kind='ebook'`: load via `blob_loader(variant.path)` → walk EPUB OPF spine → identify chapters via TOC + spine boundaries → text-extract per chapter.
   - `kind='inbox_document'`: read text → split on H1/H2 (markdown heading hierarchy) → if no headings, treat as single chapter `chapter_ref='whole'`.
4. **Decide layout**:
   - Total `char_count >= min_chapter_chars * 3`: long → per-chapter items + 1 index/overview item (`chapter_ref='index'`).
   - Else: short → single consolidated item (`chapter_ref='whole'`).
5. **Per-chapter extraction**: call `extractor.extract(chapter_text, chapter_title, primary_lang)` → assemble `SourcePageReviewItem` with:
   - `recommendation = "include"` if `len(short_quotes) >= 1` (have evidence) else `"defer"`.
   - `action = "create"` (Source Map output is always create-candidates; #514/#515 may down-grade).
   - `reason`: builder synthesizes ≤ `max_reason_chars` chars from `claims[0]` + chapter title.
   - `evidence`: convert `short_quotes` → `EvidenceAnchor(kind="chapter_quote", source_path=variant.path, locator=q.locator, excerpt=q.excerpt[:max_excerpt_chars], confidence=q.confidence)`.
   - `risk`: empty by default; builder adds `RiskFlag(code="low_signal_count", severity="medium", description="<5 claims extracted")` if `len(claims) < 5`.
   - `confidence = extraction_confidence`.
   - `source_importance = 0.5` (placeholder — #514/#515 may revise).
   - `reader_salience = 0.0` (Reading Overlay signal not joined here — that's #517's package).
   - `target_kb_path`: builder synthesizes a candidate path `KB/Wiki/Sources/{slug}/{chapter_ref}.md` where slug derives from `source_id` last segment (whitespace-safe). Caller may override.
   - `chapter_ref`: from step 3.
6. **Aggregate risks**: collect TOC weakness, OCR markers from inspection (mirror #511 risk codes); attach to top-level `risks`.
7. **Return** `SourceMapBuildResult`.

### 4.3 Hard invariants enforced at build time

| ID | Rule | Where |
|---|---|---|
| B1 | `has_evidence_track=False` ⇒ `ValueError` (caller contract violation) | `build()` entry |
| B2 | Every `recommendation="include"` item has ≥1 EvidenceAnchor | inherited from `SourcePageReviewItem` V1 |
| B3 | `EvidenceAnchor.excerpt` length ≤ `max_excerpt_chars` | builder truncates before embedding |
| B4 | Sum of all `EvidenceAnchor.excerpt` lengths across all items ≤ chapter text length × 0.30 (claim-dense, not mirror) | builder asserts in tests via T6 |
| B5 | `chapter_ref` values are unique within `items` | builder assigns deterministic refs |
| B6 | On extractor exception: catch (narrow tuple — see #511 F5 lesson), set `result.error`, return empty `items` | `build()` exception handler |
| B7 | Builder NEVER imports `shared.book_storage` (subprocess gate test) | T11 |
| B8 | Builder NEVER imports `fastapi`, `thousand_sunny.*`, `agents.*`, LLM clients | T12 |

---

## 5. 驗收 (Acceptance)

### Issue #513 listed AC

- Long-source fixture produces chapter or section Source pages.
- Short-source fixture produces a single Source page.
- Output includes source metadata, claims, key numbers, figure/table summaries where available, and evidence anchors.
- Tests assert output is not a long verbatim full-text mirror.
- Use temp fixture output only; do not write to the real KB.
- Do not run real LLM calls; use injected fakes / fixture extractors.

### Unit tests required

| # | Test | Asserts |
|---|---|---|
| T1 | `test_build_long_source_emits_per_chapter_items` | EPUB fixture w/ 5 chapters → ≥5 items + 1 `chapter_ref='index'` overview. |
| T2 | `test_build_short_source_emits_single_item` | Inbox markdown <2000 chars → 1 item, `chapter_ref='whole'`. |
| T3 | `test_build_includes_claims_and_evidence` | Output items have non-empty `evidence` list when extractor returned quotes; reason references first claim. |
| T4 | `test_build_uses_deterministic_fake_extractor` | Fake extractor returns canned `ClaimExtractionResult`; builder threads through unchanged. |
| T5 | `test_build_does_not_call_real_llm` | Module import + run with `extractor=DummyDeterministicExtractor()`; subprocess assertion that `anthropic`, `openai`, `claude_client` modules not in `sys.modules`. |
| T6 | `test_build_excerpt_total_below_30pct_of_chapter` | Long-fixture run; sum(excerpt lengths) < 0.30 × total chapter chars. |
| T7 | `test_build_excerpt_individual_length_capped` | Set `max_excerpt_chars=200`; assert no item evidence excerpt > 200. |
| T8 | `test_build_no_evidence_track_raises` | `ReadingSource(has_evidence_track=False)` → `ValueError`. |
| T9 | `test_build_extractor_failure_returns_error_state` | Extractor raises `ValueError` → `SourceMapBuildResult(items=[], error="extractor_failed: ...")`. |
| T10 | `test_build_blob_loader_injection` | EPUB fixture; assert `blob_loader` called with `variant.path` exactly once per inspection. |
| T11 | `test_no_book_storage_import` | Subprocess `python -c "import shared.source_map_builder"` → `shared.book_storage` not in `sys.modules`. |
| T12 | `test_no_runtime_imports_forbidden` | Subprocess assert `fastapi`, `thousand_sunny.*`, `agents.*`, `anthropic`, `openai` not in `sys.modules`. |
| T13 | `test_build_chapter_ref_unique` | Long-fixture run; assert `len({item.chapter_ref for item in items}) == len(items)`. |
| T14 | `test_build_target_kb_path_format` | Assert `target_kb_path` matches `KB/Wiki/Sources/{slug}/{chapter_ref}.md`; slug whitespace-safe. |
| T15 | `test_build_emits_low_signal_count_risk` | Fixture w/ extractor returning `claims=[]` → item has `RiskFlag(code='low_signal_count')`. |
| T16 | `test_build_inbox_section_split_on_headings` | Markdown w/ 3 H2 sections → 3 items each `chapter_ref='sec-1'..'sec-3'`. |
| T17 | `test_build_result_round_trips` | `model_dump()` + `model_validate()` identity on representative result. |

### Self-imposed gates

- [ ] 零新 dependency
- [ ] 全部 model `extra="forbid"`
- [ ] All Literal enums 用 `Literal[...]`，**不**用 `Enum`
- [ ] `schema_version: Literal[1] = 1` first field on `SourceMapBuildResult`
- [ ] 接受 `ReadingSource.source_id` 為 `str`，**不** parse、不 validate format
- [ ] **Closed-set extension protocol**: 新增 enum 成員必須 (a) bump `schema_version`、(b) 更新 docstring、(c) 更新 #514 / #515 downstream
- [ ] **No state mutation**: builder is a pure function; result is frozen value-object
- [ ] **Narrow exception tuples** in inspector code (per #511 F5 lesson — no bare `except Exception`)
- [ ] **Strict frontmatter parsing** if needed — use local strict helper, not `shared.utils.extract_frontmatter` (per #511 F6 lesson)
- [ ] `python -m pytest tests/shared/test_source_map_builder.py -v` 全綠
- [ ] `python -m ruff check shared/source_map_builder.py shared/schemas/source_map.py tests/shared/test_source_map_builder.py` 無 error
- [ ] `python -m ruff format --check shared/source_map_builder.py shared/schemas/source_map.py tests/shared/test_source_map_builder.py` clean
- [ ] PR body 含 P7-COMPLETION 區塊

---

## 6. 邊界（不能碰）

| # | Don't | Why |
|---|---|---|
| 1 | ❌ KB write / `KB/Wiki/*` 變更 | #515 commit |
| 2 | ❌ `shared.book_storage` import (runtime or test-time) | #509 N3 contract — same as #511 |
| 3 | ❌ Parse `source_id` for namespace prefix | #509 N3 contract |
| 4 | ❌ LLM call (`anthropic`, `openai`, `claude_client`, etc.) | use injected `ClaimExtractor` Protocol |
| 5 | ❌ Real `data/books/*` or real Inbox content as test input | use fixtures only |
| 6 | ❌ Vault path semantics enforcement | `target_kb_path` is a candidate string; #515 owns vault rules |
| 7 | ❌ State machine / promotion status transitions | #515 |
| 8 | ❌ Manifest persistence / file IO for storing manifests | #515 |
| 9 | ❌ Concept extraction / canonical matching | #514 |
| 10 | ❌ UI / Thousand Sunny route handler | #516 |
| 11 | ❌ Reading Overlay write (digest, notes, annotations) | #510 owns overlay; #517 reads it for context package |
| 12 | ❌ `shared.utils.extract_frontmatter` usage (silent YAMLError swallow) | #511 F6 lesson — write strict local helper |
| 13 | ❌ Bare `except Exception` in inspectors | #511 F5 lesson — narrow tuples |
| 14 | ❌ `git add .` / commit untracked files outside this slice's scope | hygiene |
| 15 | ❌ Any change to `shared/schemas/promotion_manifest.py` | #512 contract is frozen |
| 16 | ❌ Any change to `shared/schemas/reading_source.py` / `shared/schemas/preflight_report.py` | #509 / #511 contracts frozen |

---

## 7. References

### Primary

- ADR: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion
- Manifest schema: `shared/schemas/promotion_manifest.py` (#512)
- Reading Source schema: `shared/schemas/reading_source.py` (#509)
- Preflight report (downstream gate reference): `shared/schemas/preflight_report.py` (#511)

### Pattern reference

- `shared/promotion_preflight.py` (#511) — inspector pattern, `blob_loader` injection, narrow exception tuples
- `shared/epub_metadata.py` — chapter/TOC extraction from EPUB blob

### Issue / PR

- Issue #513: `[ADR-024 S5] Source Map Builder`
- Parent PRD #508
- Slice 4 (#512) shipped: `d0e1190`

---

## 8. Triage status

`needs-triage` → 等修修讀 Brief 後決定。**不要**自行 relabel `ready-for-agent`，**不要**直接開始 code。

Slice 5 是 deterministic builder，extractor 走 protocol injection — 適合 **sandcastle batch dispatch**。預估 ~700 LOC + tests (含 fixtures)。
