# Agent Brief — N518 Production Wiring + Browser Smoke (ADR-024 Slice 10)

**Issue:** [#540](https://github.com/shosho-chang/nakama/issues/540)
**Parent PRD:** #508
**Slice of:** ADR-024 (`docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`)
**Branch:** `feat/N518-promotion-wiring`
**Worktree (host):** `E:/nakama-N518-promotion-wiring`
**Drafted:** 2026-05-10
**Updated:** 2026-05-10 — incorporated Codex supplemental review (dry-run-default mode, blob_loader adapter, tightened test cases + boundaries).
**Status:** `needs-triage` — awaiting 修修 review before relabeling `ready-for-agent`. Findings that motivated this slice live on branch `chore/adr024-qa` at `qa/findings.md` (commit 7207018).

P9 六要素。Brief 是 self-contained handoff，不靠群聊歷史也能上手。

---

## 0. Scope anchor

ADR-024 §Decision: *"Full Source Promotion must pass through Promotion Review before writing formal `KB/Wiki`. The LLM is the primary recommender; 修修 is the checkpoint / brake."*

`agents/robin/CONTEXT.md` § Source Promotion: *"Robin owns domain logic; Thousand Sunny owns presentation and human checkpoint UI."*

### Why this slice exists

Slices S5-S9 (#513–#517) shipped Protocol-only seams. PR bodies for S8 (#529) and S9 (#530) explicitly deferred production wiring:

- #529: *"production wiring injects ReadingSourceRegistry-backed adapter (out of scope for #516)"*; *"Browser smoke test (Playwright) — orchestrator follow-up"*
- #530: *"Production wiring path for the writing-assist service ... — future-slice / app-config decision"*

The follow-up slice was never created. Result: `thousand_sunny/app.py:94, 103` mounts both routers but never calls `set_service(...)`, and `PromotionReviewService.__init__` requires Protocol-only seams (`SourceResolver`, `ReadingSourceLister`, `KBConceptIndex`, `ClaimExtractor`, `ConceptMatcher`) plus the upstream services need a `BlobLoader` — none have a production class. End-user routes return 503; even if they didn't, the service couldn't be constructed.

This slice closes that gap.

### Codex supplemental review (2026-05-10)

Codex GPT-5 reviewed the original draft of this brief. Two scope-changing notes:

1. **Default mode = deterministic dry-run; LLM-backed = future / opt-in slice.** "For first wiring PR, do not jump directly into uncontrolled real LLM batch. Prefer a configurable mode: default local/dev: deterministic dry-run extractor/matcher so UI can be smoke-tested safely. Future/optional: LLM-backed extractor/matcher behind explicit config flag." Real LLM wiring "must be opt-in, cost-visible, cancellable or clearly bounded, and not run automatically on page load."
2. **`BlobLoader` is a sixth required adapter.** Both `PromotionPreflight` and `SourceMapBuilder` take `blob_loader: BlobLoader` at construction. The production loader "reads variant paths safely; must not parse source_id as a filesystem path."

Both incorporated below. LLM-backed adapters move to a follow-up slice (provisionally **N519**), not in N518's scope.

### Pipeline anchor

CONTENT-PIPELINE.md Stage 3 (整合) — closes the gap between Protocol-only S5-S9 and a deployable promotion review surface.

### What unblocks after this slice

- E2E QA (the abandoned task list in `chore/adr024-qa` `qa/findings.md`) becomes viable in dry-run mode.
- 修修 can exercise the full UI flow against fixture / dry-run data without LLM cost or risk.
- PRD #508 closure (after this slice's smoke passes).

---

## 1. 目標 (Goal)

Wire production-grade implementations of every seam ADR-024 currently leaves Protocol-only, plus the FastAPI startup hook that constructs and injects them. After this slice, a fresh `python -m uvicorn thousand_sunny.app:app` boots a fully functional promotion review surface in **dry-run mode by default** — UI / routing / persistence / decision recording / commit-to-temp-vault all work end-to-end against real disk, with deterministic claim/concept generators that need no LLM and no external API.

LLM-backed extractor / matcher is **explicitly out of scope**. They land in a follow-up slice (N519) behind an opt-in config flag.

Concrete deliverables:

1. Six new adapter classes (one per Protocol seam, including `BlobLoader`) implementing safe production behavior.
2. Two deterministic dry-run extractor / matcher classes — used by default in dev / local / CI / 修修's first end-to-end exercise.
3. App startup wiring (lifespan / startup hook in `thousand_sunny/app.py`) that constructs adapters + services + calls `set_service(...)` for both Promotion Review and Writing Assist routers.
4. Config flag (`NAKAMA_PROMOTION_MODE`) selecting dry-run (default) vs. LLM (future). LLM mode raises a clear "not yet wired" error in N518 — placeholder so N519 only touches the LLM adapter.
5. Playwright smoke test asserting both surfaces return 200 against the wired dev server.
6. Issue #508 closure on smoke pass.

不啟動實際 LLM call、不重寫任何上游 service、不修改任何已 freeze 的 schema、不在 routes/templates 放 promotion 領域邏輯。

---

## 2. 範圍

### Add

| Path | Action | Reason |
|---|---|---|
| `shared/blob_loader.py` | **新增** | `VaultBlobLoader` — sandboxed file reader: given a variant path string, returns bytes; rejects path traversal + paths outside vault root. Used by `PromotionPreflight` + `SourceMapBuilder`. |
| `shared/source_resolver.py` | **新增** | `RegistrySourceResolver` — delegates to #509 `ReadingSourceRegistry.resolve()`. Does NOT parse `source_id` as filesystem path. |
| `shared/reading_source_lister.py` | **新增** | `RegistryReadingSourceLister` — walks `data/books/` + `Inbox/kb/` using #509 registry semantics (collapses original+bilingual sibling, marks bilingual-only as missing-evidence). |
| `shared/kb_concept_index_default.py` | **新增** | `VaultKBConceptIndex` — scans `KB/Wiki/Concepts/` for global concepts; skips malformed frontmatter with logged warnings. |
| `shared/dry_run_extractor.py` | **新增** | `DryRunClaimExtractor` — deterministic claim generator: given chapter/section text, returns a small bounded list of fixture-shaped `ClaimRecord`s. No LLM call. |
| `shared/dry_run_matcher.py` | **新增** | `DryRunConceptMatcher` — deterministic match policy: returns "no global match" + low confidence for any source-local concept (forces source-local-only mode in dry-run). No LLM call. |
| `thousand_sunny/app.py` | **修改** | Add lifespan (or startup hook); read config from env; construct adapters + services per `NAKAMA_PROMOTION_MODE`; call `promotion_review.set_service(...)` and `writing_assist.set_service(...)`. |
| `tests/shared/test_blob_loader.py` | **新增** | Adapter tests — happy path, traversal rejection, missing file, outside-vault rejection. |
| `tests/shared/test_source_resolver.py` | **新增** | Adapter tests — `ebook:{id}`, `inbox:Inbox/kb/foo.md`, never-parse-as-path, unknown source returns None. |
| `tests/shared/test_reading_source_lister.py` | **新增** | Adapter tests — books candidate, Inbox/kb original-only, original+bilingual sibling collapsed, bilingual-only marked missing-evidence, unsafe paths ignored. |
| `tests/shared/test_kb_concept_index_default.py` | **新增** | Adapter tests — lists 3 fixture concepts, skips malformed frontmatter, returns empty list on missing dir. |
| `tests/shared/test_dry_run_extractor.py` | **新增** | Determinism (same input → same output), bounded output size, no `anthropic` import. |
| `tests/shared/test_dry_run_matcher.py` | **新增** | Determinism, no `anthropic` import. |
| `tests/thousand_sunny/test_app_startup_wiring.py` | **新增** | Integration: app boots, both `set_service` calls succeed, `GET /promotion-review/` and `GET /writing-assist/{id_b64}` return 200; `DISABLE_ROBIN=1` skips wiring; bad config raises. |
| `tests/playwright/test_promotion_routes_smoke.py` | **新增** | Playwright smoke against a live dev server (deferred from #529): list page loads, click into one fixture source, approve one item, click commit (writes to temp test vault), open writing assist scaffold; no console errors; no ghostwritten prose. |

### Read-only consumption

| Path | Why |
|---|---|
| `shared/promotion_review_service.py` (#516) | Service constructor signature; protocol contracts. |
| `shared/source_map_builder.py` (#513) | `ClaimExtractor` Protocol; `BlobLoader` Protocol; `ClaimRecord` schema. |
| `shared/concept_promotion_engine.py` (#514) | `ConceptMatcher` + `KBConceptIndex` Protocols; `GlobalConcept` schema. |
| `shared/promotion_preflight.py` (#511) | `BlobLoader` Protocol; `PromotionPreflight` constructor. |
| `shared/promotion_commit.py` (#515) | `PromotionCommitService` constructor. |
| `shared/reading_source_registry.py` (#509) | `ReadingSourceRegistry.resolve()` API; `ReadingSource` schema; namespace semantics (`ebook:` / `inbox:`). |
| `shared/schemas/reading_context_package.py` | `WritingAssistService` package schema. |
| `thousand_sunny/routers/promotion_review.py` (#516) | `set_service(service)` injection point. |
| `thousand_sunny/routers/writing_assist.py` (#517) | `set_service(service)` injection point + existing `_build_default_service(package_root)` factory (lines 239-242) — REUSE this for S9 wiring. |
| `agents/robin/reading_context_package.py` (#517) | Builder used during writing-assist package creation; surface contract. |
| `.env.example` | Existing env var conventions. |

### 完全不碰

- Any `shared/promotion_*` service — they are frozen contracts; this slice writes adapter classes that satisfy their Protocol seams, not modifications to them.
- Schemas in `shared/schemas/` — frozen.
- `thousand_sunny/routers/promotion_review.py` and `thousand_sunny/routers/writing_assist.py` — except as needed to call `set_service()`. Do NOT modify route logic, error mapping, or template rendering.
- LLM-backed extractor / matcher implementations — those are N519. N518 only adds dry-run defaults + a config-mode hook so N519 can plug in without re-touching the wiring.
- `KB/Wiki/*` real-vault writes — read-only scan only; commit tests use temp vault.
- `agents/robin/*` — except read-only consumption.
- Hardcoded credentials — N/A in N518 (no LLM); future N519 must use env / `.env`.

---

## 3. 輸入

### Caller contract — startup wiring

`thousand_sunny/app.py` must construct services with these adapters and call `set_service(...)` BEFORE any request handler runs. Recommended pattern: FastAPI `lifespan` async context manager.

Configuration source: env vars (existing convention via `.env` + `python-dotenv`).

| Env var | Purpose | Default | Required? |
|---|---|---|---|
| `NAKAMA_VAULT_ROOT` | Path to Obsidian vault root (e.g. `E:/nakama-vault`) | — | yes (when Robin enabled) |
| `NAKAMA_PROMOTION_MANIFEST_ROOT` | Where filesystem-backed manifests live | `<vault>/.promotion-manifests` | no |
| `NAKAMA_READING_CONTEXT_PACKAGE_ROOT` | Where reading context packages persist (S9) | `<vault>/.reading-context-packages` | no |
| `NAKAMA_PROMOTION_MODE` | `dry_run` (default) or `llm` | `dry_run` | no |
| `ANTHROPIC_API_KEY` | LLM access — only required if `NAKAMA_PROMOTION_MODE=llm` (future N519) | — | conditional (N519) |

`DISABLE_ROBIN=1` already short-circuits Robin routes; promotion review depends on Robin being enabled. If `DISABLE_ROBIN` is set, do NOT wire promotion review services either — fall through to the existing redirect path (mirror line-59 guard in `app.py`).

If `NAKAMA_PROMOTION_MODE=llm` is set in N518 (before N519 lands), startup must raise a clear `RuntimeError("LLM mode not yet wired; set NAKAMA_PROMOTION_MODE=dry_run or wait for N519")` — explicit failure, not silent fallback.

### Adapter contracts (verify exact signatures in upstream files)

```python
# from shared/source_map_builder.py — verify exact name (BlobLoader vs Callable alias)
BlobLoader = Callable[[str], bytes]  # or Protocol-class equivalent

class ClaimExtractor(Protocol):
    def extract(self, source: ReadingSource, chapter: ChapterRef, text: str) -> list[ClaimRecord]: ...

# from shared/concept_promotion_engine.py
class ConceptMatcher(Protocol):
    def match(self, source_local: SourceLocalConcept, candidates: list[GlobalConcept]) -> ConceptMatchResult: ...

class KBConceptIndex(Protocol):
    def list_global_concepts(self) -> list[GlobalConcept]: ...

# from shared/promotion_review_service.py
class ReadingSourceLister(Protocol):
    def list_sources(self) -> list[ReadingSource]: ...

class SourceResolver(Protocol):
    def resolve(self, source_id: str) -> ReadingSource | None: ...
```

(Implementer must verify exact signatures in the upstream files; quotes above are illustrative.)

### Documentation hierarchy

- ADR-024 §Decision (review item shape, ownership boundary)
- `agents/robin/CONTEXT.md` § Source Promotion
- `qa/findings.md` on `chore/adr024-qa` (this session's evidence trail)
- This brief's §0 (Codex supplemental review notes)

---

## 4. 輸出

### 4.1 Adapter classes

Each adapter:

- Implements one Protocol from §3.
- Lives in `shared/...`.
- Uses dependency injection — no module-level singletons.
- Has a public `__init__` that takes only data dependencies (paths, registries) — no env reads inside the class; env reads happen in `app.py` startup helper.
- Raises documented narrow exception types on failure (mirror #511 F5 lesson — no bare `except Exception`).

| Class | Constructs from | Failure modes |
|---|---|---|
| `VaultBlobLoader` | `vault_root: Path` | `OSError` (IO); `ValueError` on path traversal or outside-root paths. |
| `RegistrySourceResolver` | `ReadingSourceRegistry` instance | `OSError` (registry IO); `KeyError` propagates; unknown `source_id` returns `None` (NOT raises). |
| `RegistryReadingSourceLister` | `ReadingSourceRegistry` + Inbox path + books path | `OSError`; `ValueError` on schema mismatch; unsafe paths skipped + logged. |
| `VaultKBConceptIndex` | `concepts_root: Path` | `OSError`; concept frontmatter parse errors logged + skipped (returns partial list). |
| `DryRunClaimExtractor` | (no deps) | None expected — pure deterministic function. |
| `DryRunConceptMatcher` | (no deps) | None expected — pure deterministic function. |

`DryRunClaimExtractor` behavior detail: given a chapter/section text, hash the text deterministically and synthesize 1-3 fixture-shaped `ClaimRecord`s with the chapter ref, a synthetic excerpt anchor, and clearly-marked dry-run-flagged content (e.g., `claim_text` includes `[DRY-RUN]` prefix). Output bounded; deterministic across runs.

`DryRunConceptMatcher` behavior detail: always returns "no global match" + low confidence — so the engine routes everything as source-local concepts (lets the rest of the pipeline exercise without depending on KB content).

### 4.2 App startup wiring shape

Pattern (illustrative — implementer adjusts per existing app.py conventions):

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("DISABLE_ROBIN"):
        config = _load_config_from_env()
        registry = ReadingSourceRegistry(vault_root=config.vault_root)

        blob_loader = VaultBlobLoader(vault_root=config.vault_root)
        source_resolver = RegistrySourceResolver(registry=registry)
        source_lister = RegistryReadingSourceLister(
            registry=registry,
            inbox_root=config.vault_root / "Inbox" / "kb",
            books_root=config.vault_root / "data" / "books",
        )
        kb_index = VaultKBConceptIndex(concepts_root=config.vault_root / "KB" / "Wiki" / "Concepts")

        if config.promotion_mode == "dry_run":
            extractor = DryRunClaimExtractor()
            matcher = DryRunConceptMatcher()
        elif config.promotion_mode == "llm":
            raise RuntimeError("LLM mode not yet wired; set NAKAMA_PROMOTION_MODE=dry_run or wait for N519")
        else:
            raise RuntimeError(f"Unknown NAKAMA_PROMOTION_MODE={config.promotion_mode!r}")

        preflight = PromotionPreflight(blob_loader=blob_loader)
        builder = SourceMapBuilder(blob_loader=blob_loader)
        concept_engine = ConceptPromotionEngine()
        commit_service = PromotionCommitService()

        review_service = PromotionReviewService(
            manifest_store=FilesystemManifestStore(config.manifest_root),
            preflight=preflight,
            builder=builder,
            concept_engine=concept_engine,
            commit_service=commit_service,
            extractor=extractor,
            matcher=matcher,
            kb_index=kb_index,
            source_lister=source_lister,
            source_resolver=source_resolver,
        )
        promotion_review.set_service(review_service)

        wa_service = writing_assist._build_default_service(
            package_root=config.reading_context_package_root,
        )
        writing_assist.set_service(wa_service)
    yield
    # No teardown needed currently.

app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)
```

### 4.3 Hard invariants

| ID | Rule | Where checked |
|---|---|---|
| W1 | Adapter classes do NOT import `fastapi`, `thousand_sunny.*`, `agents.*` | static check (subprocess test) |
| W2 | NO module imports `anthropic` anywhere in N518 (LLM is N519) | static check |
| W3 | App startup honours `DISABLE_ROBIN=1` — when set, do NOT construct promotion services | unit test |
| W4 | Startup failure must NOT silently swallow — raise + crash uvicorn so systemd's `Restart=on-failure` retries with operator visibility | unit test (intentionally inject bad config) |
| W5 | Service U5 invariant preserved — `PromotionReviewService` itself never imports LLM clients; LLM access (when N519 lands) stays inside adapter classes | already-existing T11/T12 in #516 tests |
| W6 | All env reads happen in `app.py` (or a config helper) — adapter classes receive resolved values, never `os.getenv` themselves | grep check |
| W7 | `source_id` is NEVER parsed as a filesystem path — adapters take registries / resolved paths only | grep check + tests AT2/AT3 |
| W8 | `BlobLoader` rejects path traversal (`../`) and paths outside vault root | unit test |
| W9 | `NAKAMA_PROMOTION_MODE=llm` in N518 raises explicit error, NOT falls back to dry-run | unit test |
| W10 | Tests NEVER write to real vault — all commits go through temp dirs | unit test (assert vault_root passed to commit is a tmp_path fixture) |
| W11 | Writing assist missing-package returns controlled 404 / empty state from existing `_HTTP_BOUNDARY_FAILURES` mapping — NEVER 503 after wiring | unit test |

---

## 5. 驗收 (Acceptance)

### Functional acceptance

- [ ] `python -m uvicorn thousand_sunny.app:app` starts cleanly with `NAKAMA_PROMOTION_MODE=dry_run` (default). No startup exception.
- [ ] `GET /promotion-review/` returns **200** (not 503). List view renders; empty list is acceptable when no source has been preflighted.
- [ ] When a fixture / safe local source exists, list view shows it.
- [ ] `POST /promotion-review/source/{id_b64}/start` creates a `PromotionManifest` with deterministic dry-run items (non-empty if extractor produces ≥1 claim per chapter).
- [ ] `GET /promotion-review/source/{id_b64}` displays review items.
- [ ] `POST .../decide/{item_id}` with approve/reject/defer persists `HumanDecision`.
- [ ] `POST .../commit` against a temp/test vault writes only the approved items; failure is visible (CommitOutcome.error surfaced); does NOT silently write broken KB files.
- [ ] `GET /writing-assist/{id_b64}` returns 200 for a source with persisted package; returns controlled 404 / empty-state for missing package (NEVER 503 after wiring).
- [ ] `DISABLE_ROBIN=1 python -m uvicorn thousand_sunny.app:app` still boots — falls back to existing `/` redirect; no attempt to construct promotion services.
- [ ] `NAKAMA_PROMOTION_MODE=llm` startup raises `RuntimeError` with clear message pointing at N519.

### Unit tests

| # | Test | Asserts |
|---|---|---|
| AT1 | `test_vault_blob_loader_reads_within_vault` | Tmp vault with `data/x.txt`; `loader("data/x.txt")` returns bytes. |
| AT2 | `test_vault_blob_loader_rejects_traversal` | `loader("../etc/passwd")` raises `ValueError`. |
| AT3 | `test_vault_blob_loader_rejects_outside_vault` | Absolute path outside vault → `ValueError`. |
| AT4 | `test_registry_source_resolver_resolves_ebook_namespace` | `resolve("ebook:abc123")` delegates to registry; returns the registry's result. |
| AT5 | `test_registry_source_resolver_resolves_inbox_namespace` | `resolve("inbox:Inbox/kb/foo.md")` works without parsing the path. |
| AT6 | `test_registry_source_resolver_unknown_returns_none` | Registry says missing → resolver returns `None` (NOT raises). |
| AT7 | `test_registry_source_resolver_never_parses_path` | Mock registry; assert `resolver.resolve(id)` calls `registry.resolve(id)` exactly once with the raw `id`. No file IO from resolver itself. |
| AT8 | `test_registry_reading_source_lister_books_candidate` | Tmp `data/books/foo/{original.epub,bilingual.epub}`; lister returns one entry. |
| AT9 | `test_registry_reading_source_lister_inbox_original_only` | Tmp `Inbox/kb/foo.md`; lister returns one entry. |
| AT10 | `test_registry_reading_source_lister_original_plus_bilingual_collapsed` | Tmp `Inbox/kb/foo.md` + `Inbox/kb/foo.bilingual.md`; lister collapses to one entry with both variants. |
| AT11 | `test_registry_reading_source_lister_bilingual_only_missing_evidence` | Tmp `Inbox/kb/foo.bilingual.md` without sibling original; lister marks missing-evidence. |
| AT12 | `test_registry_reading_source_lister_skips_unsafe_paths` | Symlink / `..` paths in Inbox → skipped + logged. |
| AT13 | `test_vault_kb_concept_index_lists_concepts` | Tmp `KB/Wiki/Concepts/` with 3 valid concept md files; returns 3 entries. |
| AT14 | `test_vault_kb_concept_index_skips_malformed` | One malformed frontmatter; skipped + warned; valid 2 returned. |
| AT15 | `test_vault_kb_concept_index_empty_dir` | Missing dir → returns `[]` cleanly. |
| AT16 | `test_dry_run_claim_extractor_deterministic` | Same input → identical output across calls. |
| AT17 | `test_dry_run_claim_extractor_bounded` | Output ≤ 3 ClaimRecords per chapter; each marked `[DRY-RUN]`. |
| AT18 | `test_dry_run_concept_matcher_no_match` | Always returns "no global match" + low confidence. |
| AT19 | `test_dry_run_modules_no_anthropic_import` | Subprocess: `import shared.dry_run_extractor; assert "anthropic" not in sys.modules`. |
| WT1 | `test_app_lifespan_wires_promotion_review_service` | Boot via `TestClient`, assert `routers.promotion_review._service is not None`. |
| WT2 | `test_app_lifespan_wires_writing_assist_service` | Analog. |
| WT3 | `test_app_get_promotion_review_returns_200_dry_run` | TestClient `GET /promotion-review/` → 200 (not 503). |
| WT4 | `test_app_get_writing_assist_missing_package_returns_404_not_503` | `GET /writing-assist/{id_b64}` for missing package → 404 (W11). |
| WT5 | `test_app_disable_robin_skips_wiring` | `DISABLE_ROBIN=1`; services NOT wired; `/` redirects. |
| WT6 | `test_app_bad_config_raises` | Missing `NAKAMA_VAULT_ROOT` → startup raises (NOT swallow). |
| WT7 | `test_app_llm_mode_raises_in_n518` | `NAKAMA_PROMOTION_MODE=llm` → startup `RuntimeError` mentioning N519 (W9). |
| WT8 | `test_no_module_singleton_in_adapters` | Subprocess: import each adapter module, assert no top-level instances. |
| WT9 | `test_no_fastapi_import_in_adapters` | Subprocess static check. |
| WT10 | `test_no_anthropic_import_anywhere_in_n518` | Subprocess: import all of `shared.*`; assert `anthropic` not in `sys.modules` (W2). |
| RT1 | `test_route_start_creates_dry_run_manifest` | TestClient `POST /promotion-review/source/{id_b64}/start` → manifest persisted with deterministic items. |
| RT2 | `test_route_decide_persists_decision` | `POST .../decide/{item_id}` with approve → manifest updated. |
| RT3 | `test_route_commit_writes_to_temp_vault_only` | `POST .../commit` with `vault_root=tmp_path`; assert files written to tmp_path; assert REAL `KB/Wiki/` untouched (W10). |

### Playwright smoke test

| # | Test | Asserts |
|---|---|---|
| PT1 | `test_promotion_review_list_loads_no_console_errors` | Page renders; browser console has 0 errors. |
| PT2 | `test_full_flow_start_decide_commit_against_fixture` | Click into one fixture source, approve one item, click commit → no console errors, decision persists, commit succeeds against temp vault. |
| PT3 | `test_writing_assist_renders_for_existing_package` | Given a fixture-built `ReadingContextPackage`, the scaffold renders. |
| PT4 | `test_writing_assist_no_ghostwritten_prose` | Inspect rendered HTML; assert NO completed sentences / paragraphs / first-person prose tokens (mirrors W1-W7 from #517 invariants). |

### Self-imposed gates

- [ ] 零新 dependency 在 `requirements.txt` 之外 (no `anthropic` use in N518)
- [ ] Each adapter module ≤ 250 LOC
- [ ] App startup wiring section in `app.py` ≤ 100 LOC of new code
- [ ] `python -m pytest tests/shared/test_blob_loader.py tests/shared/test_source_resolver.py tests/shared/test_reading_source_lister.py tests/shared/test_kb_concept_index_default.py tests/shared/test_dry_run_extractor.py tests/shared/test_dry_run_matcher.py tests/thousand_sunny/test_app_startup_wiring.py -v` 全綠
- [ ] `python -m pytest tests/` 全綠 (no regression in existing tests)
- [ ] `python -m ruff check shared/blob_loader.py shared/source_resolver.py shared/reading_source_lister.py shared/kb_concept_index_default.py shared/dry_run_extractor.py shared/dry_run_matcher.py thousand_sunny/app.py tests/` no error
- [ ] `python -m ruff format --check ...` clean
- [ ] PR body 含 P7-COMPLETION 區塊 + manual smoke evidence (uvicorn 起得來 + curl 兩條 route 200 + screenshot of list page in dry-run mode)

### Issue closure (Codex requirement)

- [ ] Comment on PRD #508 explaining S1-S9 merged + N518 added after QA (this session) found the 503 / Protocol-only gap
- [ ] Close PRD #508 only when N518 PR merges AND smoke test passes
- [ ] Do NOT close N518 (#540) just because services instantiate — full functional acceptance + smoke required

---

## 6. 邊界（不能碰）

| # | Don't | Why |
|---|---|---|
| 1 | ❌ Modify any `shared/promotion_*` service | frozen contracts |
| 2 | ❌ Modify any schema in `shared/schemas/` | upstream contracts frozen |
| 3 | ❌ Add `anthropic` import / call anywhere in N518 | LLM is N519; W2 |
| 4 | ❌ Module-level singletons / global state in adapters | W6 |
| 5 | ❌ `os.getenv` inside adapter classes | env reads live in `app.py` |
| 6 | ❌ Bare `except Exception` | #511 F5 |
| 7 | ❌ Silently swallow startup failures | W4 |
| 8 | ❌ Real LLM calls in CI | W2 + Codex constraint |
| 9 | ❌ Tests writing to real vault — all commits go through `tmp_path` | W10 + Codex constraint |
| 10 | ❌ Touch `thousand_sunny/routers/promotion_review.py` or `routers/writing_assist.py` route logic | already shipped in #529/#530 |
| 11 | ❌ Parse `source_id` as a filesystem path anywhere | W7 + Codex constraint |
| 12 | ❌ Writing-assist 503 on missing package | must use existing `_HTTP_BOUNDARY_FAILURES` → 404 (W11) |
| 13 | ❌ Auto-relabel issue from `needs-triage` to `ready-for-agent` | 修修 reviews this brief first |
| 14 | ❌ Close PRD #508 prematurely | Codex constraint — wait until smoke passes |
| 15 | ❌ `git add .` outside scope | hygiene |
| 16 | ❌ Long-running synchronous calls in startup hook (no LLM batches even when N519 lands) | startup must remain fast; LLM calls happen per-request, not at boot |

---

## 7. References

### Primary

- ADR: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion
- QA findings driving this slice: `chore/adr024-qa` branch, `qa/findings.md` (commit `7207018`)
- Codex supplemental review: 2026-05-10 conversation handoff, integrated into §0 above

### Pattern reference

- Existing `app.py` conditional wiring (line 59 `DISABLE_ROBIN` guard) — extend the same gate with `lifespan`
- `_build_default_service(package_root)` factory at `thousand_sunny/routers/writing_assist.py:239-242` — REUSE for S9
- `FilesystemManifestStore` at `shared/promotion_review_service.py:114-155` — already exists, just construct with a configured path
- Existing `ReadingSourceRegistry` at `shared/reading_source_registry.py` (#509) — use as the data source for resolver + lister
- `_HTTP_BOUNDARY_FAILURES` mapping in `thousand_sunny/routers/writing_assist.py` — model for graceful 404 on missing package

### Issue / PR

- Parent PRD: #508
- Slices that ship Protocol-only and motivate this slice: #529 (S8), #530 (S9), #526 (S5), #527 (S6), #528 (S7)
- Filed issue: [#540 [ADR-024 S10] Production Wiring](https://github.com/shosho-chang/nakama/issues/540)

---

## 8. Triage status

`needs-triage` → 等修修讀 Brief 後決定。**不要**自行 relabel `ready-for-agent`，**不要**直接開始 code。

### Sizing after Codex feedback

The original brief estimated ~1500-2000 LOC and 1.5-2 days. With LLM-backed adapters moved to N519, this slice shrinks:

- **N518 (this slice)**: ~800-1200 LOC + tests; estimate 1 day for one implementer.
- **N519 (LLM-backed adapters)**: separate brief; ~half day per adapter + LLM cost/cancel/bounding harness.

Sub-slice split (optional, if reviewer wants thinner PRs):

- **N518a**: blob_loader + source_resolver + reading_source_lister + kb_concept_index + app wiring (no extractor/matcher yet — fail at start_review). ~half day.
- **N518b**: dry_run_extractor + dry_run_matcher + Playwright smoke. ~half day.

Single-PR is also acceptable if implementer keeps wiring + dry-run + smoke together (still <1500 LOC).
