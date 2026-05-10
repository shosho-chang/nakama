# Agent Brief — N516 Promotion Review UI (ADR-024 Slice 8)

**Issue:** [#516](https://github.com/shosho-chang/nakama/issues/516)
**Parent PRD:** #508
**Slice of:** ADR-024 (`docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`)
**Branch:** `impl/N516-promotion-review-ui`
**Worktree (host):** `E:/nakama-N516-promotion-review-ui`
**Drafted:** 2026-05-10
**Status:** awaiting #515 merge + 修修 review before relabeling `ready-for-agent`

P9 六要素。Brief 是 self-contained handoff，不靠群聊歷史也能上手。

---

## 0. Scope anchor

ADR-024 §Decision: "Full Source Promotion must pass through Promotion Review before writing formal `KB/Wiki`. The LLM is the primary recommender; 修修 is the checkpoint / brake. Each review item must include recommendation, reason, evidence, risk, action, and confidence."

`agents/robin/CONTEXT.md` § Source Promotion: "Source Promotion ownership boundary: Robin owns domain logic; Thousand Sunny owns presentation and human checkpoint UI. Robin/shared should implement source quality analysis, source-local concept extraction, global Concept matching, promotion manifest storage, acceptance gates, and KB commit. Thousand Sunny should expose entry points, review UI, approve/reject/defer actions, and progress/status display. Do not bury promotion domain logic in routes/templates; CLI and future agents must be able to reuse the same Robin/shared service."

Slice 8 (#516) builds the **Thousand Sunny operational review UI** for promotion review. Routes are thin presentation handlers; all domain logic lives in `shared/promotion_*` services from #511/#513/#514/#515.

### Where Slice 1-7 land (in main as of #516 dispatch time)

`#516` consumes:

- `shared/promotion_preflight.py` (#511) — kicks off preflight from "Start review" affordance.
- `shared/source_map_builder.py` (#513) — invokes source map build.
- `shared/concept_promotion_engine.py` (#514) — invokes concept proposals.
- `shared/promotion_commit.py` (#515) — invokes commit on approved items.
- `shared/schemas/promotion_manifest.py` (#512) — display + serialization shape.

### Pipeline anchor

CONTENT-PIPELINE.md Stage 3 (整合) — operator surface. This is the human-in-the-loop checkpoint between LLM recommendations and KB writes.

### UI direction

This is a **dense operational review tool**, NOT a marketing surface. Read `docs/design-system.md` first. No hero card, no ghostwriting, no decorative grid; default to information-density patterns, table/list layouts, semantic HTML, AAA contrast for body text. Keyboard-first interactions.

---

## 1. 目標 (Goal)

Provide a Thousand Sunny set of routes + templates for promotion review:

- **Entry list**: list Reading Sources with `PreflightReport.recommended_action ∈ {proceed_full_promotion, proceed_with_warnings}` (preflighted but not yet reviewed/committed); show preflight summary + "Start review" affordance.
- **Review surface**: per-source review page; lists `SourcePageReviewItem` and `ConceptReviewItem` from a `PromotionManifest`; for each item shows recommendation, reason, evidence excerpts, risk flags, action, confidence, source_importance, reader_salience.
- **Decision affordances**: per-item Approve / Reject / Defer with optional note. Decisions persist as `HumanDecision` on items (mutating the manifest).
- **Commit trigger**: "Commit approved" button → invokes `PromotionCommitService.commit(...)` on currently-approved items.
- **Progress display**: per-source promotion_status indicator (`needs_review` / `partial` / `complete` / `failed`).

UI affordance MUST be keyboard-navigable, dense, AAA contrast, and use design tokens from `docs/design-system.md` (no hardcoded colors/fonts/spacing in templates).

不啟動 LLM、不 directly write KB (delegate to #515)、不重新 implement promotion domain logic in handlers.

---

## 2. 範圍

### Add

| Path | Action | Reason |
|---|---|---|
| `thousand_sunny/routers/promotion_review.py` | **新增** | FastAPI route handlers (list / review / decide / commit). Thin handlers — call `shared/` services. |
| `thousand_sunny/templates/promotion_review/list.html` | **新增** | List of preflighted sources awaiting review. |
| `thousand_sunny/templates/promotion_review/review.html` | **新增** | Per-source review surface. |
| `thousand_sunny/templates/promotion_review/_item_card.html` | **新增** | Reusable per-item card partial (used in review.html). |
| `thousand_sunny/static/promotion_review.css` | **新增 (or extend existing app stylesheet)** | Tokens-only; no hardcoded values. |
| `shared/promotion_review_service.py` | **新增** | Service facade composing #511/#513/#514/#515 calls; persistence helpers (load / save manifest from disk). Handlers use this; tests mock its public methods. |
| `shared/schemas/promotion_review_state.py` | **新增** | `PromotionReviewState` value-object describing UI-side aggregation (preflighted-not-reviewed list entry, review session state). |
| `tests/thousand_sunny/test_promotion_review_routes.py` | **新增** | Route-level tests via FastAPI TestClient; assert page loads, decision updates persist, commit trigger calls service. |
| `tests/shared/test_promotion_review_service.py` | **新增** | Service-layer tests with mocked shared services. |
| `tests/fixtures/promotion_review/` | **新增** | Manifests + fixture vault snapshots. |

### Read-only consumption

| Path | Why |
|---|---|
| `shared/schemas/promotion_manifest.py` (#512), `shared/schemas/preflight_report.py` (#511), `shared/schemas/source_map.py` (#513), `shared/schemas/concept_promotion.py` (#514), `shared/schemas/promotion_commit.py` (#515) | Display contracts. |
| `shared/promotion_preflight.py` (#511) | Service call (start review entry). |
| `shared/source_map_builder.py` (#513), `shared/concept_promotion_engine.py` (#514) | Service calls (build manifest). |
| `shared/promotion_commit.py` (#515) | Commit service call. |
| `docs/design-system.md` | Tokens, density patterns, accessibility rules. |

### 完全不碰

- `KB/Wiki/*` write — delegate to #515.
- LLM calls in routes/templates.
- Promotion domain logic in handlers (must live in `shared/promotion_review_service.py` or further down).
- `agents/robin/*` direct writes from UI.
- Backend route handlers performing manifest mutation directly (must go through `PromotionReviewService.record_decision(...)`).
- New JS frameworks. Default to HTMX + semantic HTML + minimal vanilla JS for keyboard handlers.

---

## 3. 輸入

### Caller contract (handler side)

Routes:

| Method | Path | Purpose | Service call |
|---|---|---|---|
| GET | `/promotion-review/` | List preflighted-not-reviewed sources | `service.list_pending() -> list[PromotionReviewState]` |
| GET | `/promotion-review/source/{source_id_b64}` | Per-source review surface | `service.load_review_session(source_id) -> PromotionManifest` |
| POST | `/promotion-review/source/{source_id_b64}/decide/{item_id}` | Record per-item human decision | `service.record_decision(source_id, item_id, decision, note)` |
| POST | `/promotion-review/source/{source_id_b64}/commit` | Trigger commit on approved items | `service.commit_approved(source_id, batch_id) -> CommitOutcome` |
| POST | `/promotion-review/source/{source_id_b64}/start` | First-time start review (runs builder + engine) | `service.start_review(source_id) -> PromotionManifest` |

`source_id_b64` is base64url-encoded `ReadingSource.source_id` (since source_id contains `:` / `/`). Service decodes; handler does not parse the inner namespace structure (per #509 N3 contract).

Service signatures (read-only spec):

```python
class PromotionReviewService:
    def __init__(
        self,
        manifest_store: ManifestStore,         # filesystem-backed manifest persistence
        registry: ReadingSourceRegistry,
        preflight: PromotionPreflight,
        builder: SourceMapBuilder,
        concept_engine: ConceptPromotionEngine,
        commit_service: PromotionCommitService,
        extractor: ClaimExtractor,             # injected; tests pass deterministic fake
        matcher: ConceptMatcher,
        kb_index: KBConceptIndex,
    ): ...

    def list_pending(self) -> list[PromotionReviewState]: ...
    def start_review(self, source_id: str) -> PromotionManifest: ...
    def load_review_session(self, source_id: str) -> PromotionManifest | None: ...
    def record_decision(self, source_id: str, item_id: str, decision: HumanDecisionKind, note: str | None) -> PromotionManifest: ...
    def commit_approved(self, source_id: str, batch_id: str, vault_root: Path) -> CommitOutcome: ...
```

`ManifestStore` Protocol abstracts manifest filesystem persistence (load / save by source_id). Slice 8 ships a default filesystem-backed implementation.

### Caller responsibilities

- Auth / session — out of scope; assume single-user local app.
- Live KB path — caller (app config) supplies `vault_root`.
- Real LLM — replace `extractor` / `matcher` with LLM-backed versions in production wiring (NOT in routes; in app `__init__`).

### Documentation hierarchy

- ADR-024 §Decision (review item shape, ownership boundary)
- `agents/robin/CONTEXT.md` § Source Promotion (UI rules, ownership boundary)
- `docs/design-system.md` (tokens, density, accessibility)

---

## 4. 輸出

### 4.1 Service surface (`shared/promotion_review_service.py`)

Composes #511/#513/#514/#515. NO promotion domain logic — only:

- Manifest CRUD via `ManifestStore`.
- Service orchestration (preflight → build → engine → commit chain).
- Decision recording (sets `human_decision` on items, persists manifest).

### 4.2 Schema sketch (`shared/schemas/promotion_review_state.py`)

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from shared.schemas.preflight_report import PreflightReport, PreflightAction


class PromotionReviewState(BaseModel):
    """One entry in the 'pending review' list view."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    source_id: str
    primary_lang: str
    preflight_action: PreflightAction
    preflight_summary: str  # ≤ 200 chars synopsis
    has_existing_manifest: bool
    manifest_status: Literal["needs_review", "partial", "complete", "failed"] | None
```

### 4.3 UI contract (template behavior)

- Each per-item card displays: action, recommendation, reason, ≥1 evidence excerpt (chapter ref + locator + excerpt), risk badges (color-coded by severity), confidence (numeric + visual bar), source_importance, reader_salience.
- Approve / Reject / Defer buttons keyboard-accessible (Tab focus, Enter/Space activate). HTMX swap on click → server records decision and re-renders item card.
- "Commit approved" disabled if no items have `human_decision="approve"`.
- Progress indicator at top: `{n_approved} / {n_total} items, status={manifest_status}`.

### 4.4 Hard invariants

| ID | Rule | Where |
|---|---|---|
| U1 | Routes call ONLY `shared/promotion_review_service.py` for domain operations — no `shared/promotion_*` direct imports in routers/templates | route module imports |
| U2 | Templates use design tokens (CSS custom properties) — no hardcoded colors/fonts/spacing | static analysis check |
| U3 | All interactive elements have semantic HTML + ARIA labels + keyboard activation | accessibility audit |
| U4 | `source_id` is base64url-encoded in URL paths — handlers decode but do NOT parse the inner namespace | route module |
| U5 | Service passes through to shared services; service NEVER imports `book_storage`, LLM clients | T11/T12 |
| U6 | Service handles `commit_approved` errors by recording into the manifest's commit_batch.errors AND returning structured CommitOutcome — does NOT swallow | service exception handling |

---

## 5. 驗收 (Acceptance)

### Issue #516 listed AC

- User can start or inspect a Promotion Review for a preflighted Reading Source.
- UI displays include/exclude/defer recommendations with reason, evidence, risk, action, and confidence.
- User can approve, reject, or defer individual items.
- Route/template code must call Robin/shared services rather than implementing promotion domain logic in handlers.
- Tests cover review page/API behavior and decision updates.

### Unit tests (route layer)

| # | Test | Asserts |
|---|---|---|
| RT1 | `test_list_pending_renders_preflighted_sources` | GET `/promotion-review/` returns 200 + lists fixture sources with `recommended_action ∈ {proceed_*}`. |
| RT2 | `test_review_surface_renders_items` | GET `/promotion-review/source/{id}` shows fixture manifest items: per-item card contains recommendation, reason, evidence excerpt, risk badge, confidence. |
| RT3 | `test_decide_approve_persists_human_decision` | POST `/decide/{item_id}` with decision=approve → manifest persisted with `human_decision.decision="approve"`. |
| RT4 | `test_decide_reject_persists_human_decision` | analogous. |
| RT5 | `test_decide_defer_persists_human_decision` | analogous. |
| RT6 | `test_commit_invokes_commit_service` | POST `/commit` → service.commit_approved called with approved item_ids; CommitOutcome rendered in response. |
| RT7 | `test_commit_disabled_when_no_approvals` | View when no items have `human_decision="approve"` shows commit button disabled (HTML attribute). |
| RT8 | `test_start_review_runs_builder_and_engine` | POST `/start` → builder + concept engine called; new manifest persisted; redirect to `/source/{id}`. |
| RT9 | `test_route_handlers_use_service_only` | Static check (grep): `thousand_sunny/routers/promotion_review.py` does NOT import `shared.source_map_builder`, `shared.concept_promotion_engine`, `shared.promotion_commit` directly. |
| RT10 | `test_source_id_b64_encoding_round_trips` | URL contains base64url-encoded id; handler decodes to original; no `:` / `/` parsing. |

### Unit tests (service layer)

| # | Test | Asserts |
|---|---|---|
| ST1 | `test_service_record_decision_updates_manifest` | record_decision saves manifest via store. |
| ST2 | `test_service_start_review_chains_preflight_builder_engine` | start_review calls preflight → builder → engine in order; assembled manifest persisted. |
| ST3 | `test_service_commit_approved_filters_to_approve_only` | Manifest has approved+rejected+deferred; commit only operates on approved item_ids. |
| ST4 | `test_service_no_book_storage_import` | Subprocess. |
| ST5 | `test_service_no_llm_client_import` | Subprocess: `anthropic`, `openai` absent (LLM is dependency-injected). |

### UI / accessibility gates

- [ ] All interactive elements have visible focus indicator
- [ ] Buttons reachable via Tab; activate via Enter/Space
- [ ] AAA contrast on body text; AA on secondary text
- [ ] No hardcoded colors / fonts / spacing in templates (check via grep — only `var(--token-*)` references)
- [ ] `prefers-reduced-motion` respected (no decorative animation)
- [ ] Semantic HTML: `<main>`, `<section>`, `<button type="button">`, `<form>`; no `<div onclick>`
- [ ] Browser smoke: load list page, click into one source, approve one item, click commit → no console errors, decision persists, commit succeeds

### Self-imposed gates

- [ ] 零新 dependency (HTMX is vendored if not already; no React/Vue/Svelte)
- [ ] Route module ≤ 250 LOC (thin handlers)
- [ ] Service module owns the orchestration; ≤ 600 LOC
- [ ] Templates use Jinja `{% include %}` for `_item_card.html` partial
- [ ] CSS uses design tokens from `docs/design-system.md`
- [ ] `python -m pytest tests/thousand_sunny/test_promotion_review_routes.py tests/shared/test_promotion_review_service.py -v` 全綠
- [ ] `python -m ruff check thousand_sunny/routers/promotion_review.py shared/promotion_review_service.py shared/schemas/promotion_review_state.py tests/...` 無 error
- [ ] `python -m ruff format --check ...` clean
- [ ] PR body 含 P7-COMPLETION 區塊 + Aesthetic direction 段落

---

## 6. 邊界（不能碰）

| # | Don't | Why |
|---|---|---|
| 1 | ❌ Promotion domain logic in route handlers | ADR-024 ownership boundary; CLI / future agents must reuse service |
| 2 | ❌ Direct write to `KB/Wiki/*` | delegate to #515 commit service |
| 3 | ❌ Direct LLM call in routes/services | services accept injected protocol; LLM lives in app wiring |
| 4 | ❌ New JS framework (React, Vue, Svelte) | HTMX + minimal vanilla JS only |
| 5 | ❌ Hardcoded colors / fonts / spacing in templates | use design tokens |
| 6 | ❌ Marketing-style hero card / decorative grid | this is a dense operational tool |
| 7 | ❌ `book_storage` import | N3 contract |
| 8 | ❌ Parse `source_id` namespace prefix in URL handlers or templates | base64url encode/decode opaque string |
| 9 | ❌ Modify upstream schemas | upstream contracts frozen |
| 10 | ❌ `shared.utils.extract_frontmatter` for manifest loading | use Pydantic `model_validate_json()` |
| 11 | ❌ Long-running synchronous LLM calls in HTTP handlers | for `/start` endpoint, dispatch background OR explicitly use deterministic dummy in tests; document the production wiring decision in PR body |
| 12 | ❌ Bare `except Exception` | #511 F5 lesson |
| 13 | ❌ Auto-merge / auto-commit triggers | ADR-024: 修修 is checkpoint/brake; UI is gate, not bypass |
| 14 | ❌ `git add .` outside scope | hygiene |

---

## 7. References

### Primary

- ADR: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion (UI ownership)
- Design system: `docs/design-system.md`

### Pattern reference

- Existing Thousand Sunny routes for thin-handler pattern (e.g. `thousand_sunny/routers/robin.py`)
- `shared/promotion_preflight.py` (#511) — service composition pattern

### Issue / PR

- Issue #516: `[ADR-024 S8] Promotion Review UI`
- Parent PRD #508
- Slice 7 (#515): merge before dispatch; UI reflects real commit state

---

## 8. Triage status

`needs-triage` → 等修修讀 Brief 後決定。**不要**自行 relabel `ready-for-agent`，**不要**直接開始 code。

Slice 8 是 UI + service composition — UI exploration needs `docs/design-system.md` review. Browser smoke test is hard to automate; PR must include manual verification screenshots. 預估 ~1200 LOC + tests + templates.
