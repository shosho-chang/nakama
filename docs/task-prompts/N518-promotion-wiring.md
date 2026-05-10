# Agent Brief — N518 Production Wiring (ADR-024 Slice 10)

**Issue:** TBD (to be filed under PRD #508)
**Parent PRD:** #508
**Slice of:** ADR-024 (`docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`)
**Branch:** `feat/N518-promotion-wiring`
**Worktree (host):** `E:/nakama-N518-promotion-wiring`
**Drafted:** 2026-05-10
**Status:** `needs-triage` — awaiting 修修 review before relabeling `ready-for-agent`. Findings that motivated this slice live on branch `chore/adr024-qa` at `qa/findings.md` (commit 7207018).

P9 六要素。Brief 是 self-contained handoff，不靠群聊歷史也能上手。

---

## 0. Scope anchor

ADR-024 §Decision: *"Full Source Promotion must pass through Promotion Review before writing formal `KB/Wiki`. The LLM is the primary recommender; 修修 is the checkpoint / brake."*

`agents/robin/CONTEXT.md` § Source Promotion: *"Robin owns domain logic; Thousand Sunny owns presentation and human checkpoint UI."*

### Why this slice exists

Slices S5-S9 (#513–#517) shipped Protocol-only seams. PR bodies for S8 (#529) and S9 (#530) explicitly deferred production wiring:

- #529 body: *"production wiring injects ReadingSourceRegistry-backed adapter (out of scope for #516)"*; *"Browser smoke test (Playwright) — orchestrator follow-up"*
- #530 body: *"Production wiring path for the writing-assist service ... — future-slice / app-config decision"*

The follow-up slice was never created. Result: `thousand_sunny/app.py:94, 103` mounts both routers but never calls `set_service(...)`, and `PromotionReviewService.__init__` requires five Protocol-only seams (`ClaimExtractor`, `ConceptMatcher`, `KBConceptIndex`, `ReadingSourceLister`, `SourceResolver`) that have no production class. End-user routes return 503; `POST /start` would explode on construction even if it weren't 503.

This slice closes that gap.

### Pipeline anchor

CONTENT-PIPELINE.md Stage 3 (整合) — closes the gap between Protocol-only S5-S9 and a deployable promotion review surface.

### What unblocks after this slice

- E2E QA (the abandoned task list in `chore/adr024-qa` `qa/findings.md`)
- 修修 actually using promotion review on real Chinese ebooks
- PRD #508 closure

---

## 1. 目標 (Goal)

Wire production-grade implementations of every seam ADR-024 currently leaves Protocol-only, plus the FastAPI startup hook that constructs and injects them. After this slice, a fresh `python -m uvicorn thousand_sunny.app:app` boots a fully functional promotion review surface against real disk + real LLM.

Concrete deliverables:

1. Five new adapter classes (one per Protocol seam) that implement real production behavior.
2. App startup wiring (lifespan / startup hook in `thousand_sunny/app.py`) that constructs adapters + services + calls `set_service(...)` for both the Promotion Review and Writing Assist routers.
3. Playwright smoke test asserting both surfaces return 200 against the wired dev server.
4. Existing unit tests still pass; new adapter tests added.

不啟動實際 LLM batch run、不重寫任何上游 service、不修改任何已 freeze 的 schema、不在 routes/templates 放 promotion 領域邏輯（U1 in #516 invariants）。

---

## 2. 範圍

### Add

| Path | Action | Reason |
|---|---|---|
| `shared/source_resolver.py` | **新增** | Registry-backed `SourceResolver` impl; delegates to #509 `ReadingSourceRegistry.resolve()`. |
| `shared/reading_source_lister.py` | **新增** | `ReadingSourceLister` impl; walks Inbox + books to enumerate candidates. |
| `shared/kb_concept_index_default.py` | **新增** | Vault-backed `KBConceptIndex` impl; scans `KB/Wiki/Concepts/` for global concepts. |
| `shared/llm/__init__.py` | **新增** | Marker file for `shared.llm` namespace. |
| `shared/llm/claim_extractor.py` | **新增** | Anthropic-backed `ClaimExtractor` impl; chapter/section text → `list[ClaimRecord]`. |
| `shared/llm/concept_matcher.py` | **新增** | Anthropic-backed `ConceptMatcher` impl; source-local concept + global candidates → match decision + confidence. |
| `thousand_sunny/app.py` | **修改** | Add `lifespan` (or startup hook); construct adapters + services from config; call `promotion_review.set_service(...)` and `writing_assist.set_service(...)`. |
| `tests/shared/test_source_resolver.py` | **新增** | Adapter tests with in-memory registry fixture. |
| `tests/shared/test_reading_source_lister.py` | **新增** | Adapter tests with tmp Inbox + books fixture. |
| `tests/shared/test_kb_concept_index_default.py` | **新增** | Adapter tests with tmp `KB/Wiki/Concepts/` fixture. |
| `tests/shared/llm/test_claim_extractor.py` | **新增** | Adapter tests; LLM call mocked via `anthropic` test client / VCR fixture. |
| `tests/shared/llm/test_concept_matcher.py` | **新增** | Adapter tests; same pattern. |
| `tests/thousand_sunny/test_app_startup_wiring.py` | **新增** | Integration test: app boots, both `set_service` calls succeed, `GET /promotion-review/` and `GET /writing-assist/{id_b64}` return 200 (via TestClient). |
| `tests/playwright/test_promotion_routes_smoke.py` | **新增** | Playwright smoke against a live dev server; deferred from #529. |

### Read-only consumption

| Path | Why |
|---|---|
| `shared/promotion_review_service.py` (#516) | Service constructor signature; protocol contracts. |
| `shared/source_map_builder.py` (#513) | `ClaimExtractor` Protocol definition; `ClaimRecord` schema. |
| `shared/concept_promotion_engine.py` (#514) | `ConceptMatcher` + `KBConceptIndex` Protocol definitions; `GlobalConcept` schema. |
| `shared/reading_source_registry.py` (#509) | `ReadingSourceRegistry.resolve()` API; `ReadingSource` schema. |
| `shared/promotion_preflight.py` (#511), `shared/promotion_commit.py` (#515) | Service constructors used at startup. |
| `shared/schemas/reading_context_package.py` | `WritingAssistService` package schema. |
| `thousand_sunny/routers/promotion_review.py` (#516) | `set_service(service)` injection point. |
| `thousand_sunny/routers/writing_assist.py` (#517) | `set_service(service)` injection point + existing `_build_default_service(package_root)` factory (lines 239-242). |
| `agents/robin/reading_context_package.py` (#517) | Builder used during writing-assist package creation; surface contract. |
| `.env.example` | Existing env var conventions. |

### 完全不碰

- Any `shared/promotion_*` service — they are frozen contracts; this slice writes adapter classes that satisfy their Protocol seams, not modifications to them.
- Schemas in `shared/schemas/` — frozen.
- `thousand_sunny/routers/promotion_review.py` and `thousand_sunny/routers/writing_assist.py` — except as needed to read the `set_service` signature; do NOT modify route logic.
- `KB/Wiki/*` content — read-only scan only.
- `agents/robin/*` — except read-only consumption.
- Hardcoded credentials — all secrets via env / `.env`.

---

## 3. 輸入

### Caller contract — startup wiring

`thousand_sunny/app.py` must construct services with these adapters and call `set_service(...)` BEFORE any request handler runs. Recommended pattern: FastAPI `lifespan` async context manager.

Configuration source: env vars (existing convention via `.env` + `python-dotenv`). New env vars to introduce (or document existing if already present):

| Env var | Purpose | Default |
|---|---|---|
| `NAKAMA_VAULT_ROOT` | Path to Obsidian vault root (e.g. `E:/nakama-vault`) | required |
| `NAKAMA_PROMOTION_MANIFEST_ROOT` | Where filesystem-backed manifests live | `<vault>/.promotion-manifests` (proposed) |
| `NAKAMA_READING_CONTEXT_PACKAGE_ROOT` | Where reading context packages persist (S9) | `<vault>/.reading-context-packages` (proposed) |
| `ANTHROPIC_API_KEY` | LLM access (existing) | required |
| `NAKAMA_LLM_MODEL_CLAIM_EXTRACTOR` | Override model for claim extraction | `claude-opus-4-7` |
| `NAKAMA_LLM_MODEL_CONCEPT_MATCHER` | Override model for concept match | `claude-opus-4-7` |

`DISABLE_ROBIN=1` already short-circuits Robin routes; promotion review depends on Robin being enabled. If `DISABLE_ROBIN` is set, do NOT wire promotion review services either — fall through to the existing redirect path.

### Adapter contracts (read protocol definitions in upstream files)

```python
# from shared/source_map_builder.py
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

---

## 4. 輸出

### 4.1 Five adapter classes

Each adapter:

- Implements one Protocol from §3.
- Lives in `shared/...` (or `shared/llm/...` for LLM-backed).
- Uses dependency injection — no module-level singletons.
- Has a public `__init__` that takes only data dependencies (paths, clients, registries) — no env reads inside the class; env reads happen in `app.py` startup.
- Raises documented narrow exception types on failure (mirror #511 F5 lesson — no bare `except Exception`).

| Class | Constructs from | Failure modes |
|---|---|---|
| `RegistrySourceResolver` | `ReadingSourceRegistry` instance | `OSError` (registry IO); `KeyError` propagates. |
| `RegistryReadingSourceLister` | `ReadingSourceRegistry` + Inbox path + books table path | `OSError`; `ValueError` on schema mismatch. |
| `VaultKBConceptIndex` | `concepts_root: Path` | `OSError`; concept frontmatter parse errors logged + skipped. |
| `AnthropicClaimExtractor` | `anthropic.Anthropic` client + model name + prompt template path | `anthropic.APIError`; `ValueError` on JSON parse. |
| `AnthropicConceptMatcher` | `anthropic.Anthropic` client + model name + prompt template path | `anthropic.APIError`; `ValueError` on JSON parse. |

### 4.2 App startup wiring shape

Pattern (illustrative):

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.getenv("DISABLE_ROBIN"):
        config = _load_config_from_env()
        registry = ReadingSourceRegistry(vault_root=config.vault_root)
        # ... construct each adapter
        # ... construct PromotionPreflight, SourceMapBuilder, ConceptPromotionEngine,
        #     PromotionCommitService
        review_service = PromotionReviewService(
            manifest_store=FilesystemManifestStore(config.manifest_root),
            preflight=preflight,
            builder=builder,
            concept_engine=concept_engine,
            commit_service=commit_service,
            extractor=claim_extractor,
            matcher=concept_matcher,
            kb_index=kb_index,
            source_lister=lister,
            source_resolver=resolver,
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
| W2 | LLM adapters live under `shared/llm/`; non-LLM adapters NEVER import `anthropic` | static check |
| W3 | App startup honours `DISABLE_ROBIN=1` — when set, do NOT construct promotion services (parallels existing line-59 guard) | unit test |
| W4 | Startup failure must NOT silently swallow — raise + crash uvicorn so systemd's `Restart=on-failure` retries with operator visibility | unit test (intentionally inject bad config) |
| W5 | Service U5 invariant preserved — `PromotionReviewService` itself never imports LLM clients; LLM access stays inside adapter classes | already-existing T11/T12 in #516 tests |
| W6 | All env reads happen in `app.py` (or a config helper) — adapter classes receive resolved values, never `os.getenv` themselves | grep check |
| W7 | LLM adapter prompts and models versioned via env var defaults; no hardcoded model strings inside method bodies | code review |

---

## 5. 驗收 (Acceptance)

### Functional acceptance

- [ ] `python -m uvicorn thousand_sunny.app:app` starts cleanly — exit code 0, no startup exception in logs.
- [ ] `GET /promotion-review/` returns **200** (not 503) and renders the list view (empty list is acceptable on first run if no source has been preflighted).
- [ ] `GET /writing-assist/{id_b64}` returns 200 for an `id_b64` that has a persisted `ReadingContextPackage`; returns appropriate 404 (not 503) for a missing one.
- [ ] `POST /promotion-review/source/{id_b64}/start` against a real Reading Source produces a `PromotionManifest` with non-empty `items` and persists it. (This implies real LLM extraction ran end-to-end.)
- [ ] `DISABLE_ROBIN=1 python -m uvicorn thousand_sunny.app:app` still boots — falls back to existing redirect; no attempt to construct promotion services.

### Unit tests

| # | Test | Asserts |
|---|---|---|
| AT1 | `test_registry_source_resolver_delegates_to_registry` | `RegistrySourceResolver.resolve(id)` returns `registry.resolve(id)`. |
| AT2 | `test_registry_reading_source_lister_enumerates_inbox_and_books` | Walks tmp Inbox + tmp books fixture; returns expected `ReadingSource` list. |
| AT3 | `test_vault_kb_concept_index_lists_concepts` | Tmp `KB/Wiki/Concepts/` with 3 concept md files; returns 3 `GlobalConcept` entries. |
| AT4 | `test_vault_kb_concept_index_skips_malformed_frontmatter` | Tmp dir with one good + one bad concept; bad one logged + skipped, good one returned. |
| AT5 | `test_anthropic_claim_extractor_parses_json_response` | Mock client returns canned JSON; assert returned `list[ClaimRecord]`. |
| AT6 | `test_anthropic_claim_extractor_handles_api_error` | Mock client raises `APIError`; adapter raises documented exception type. |
| AT7 | `test_anthropic_concept_matcher_parses_json_response` | Analog of AT5. |
| AT8 | `test_anthropic_concept_matcher_handles_api_error` | Analog of AT6. |
| WT1 | `test_app_lifespan_wires_promotion_review_service` | Boot app via `TestClient`, assert `routers.promotion_review._service is not None`. |
| WT2 | `test_app_lifespan_wires_writing_assist_service` | Analog. |
| WT3 | `test_app_startup_get_promotion_review_returns_200` | TestClient `GET /promotion-review/` → 200 (not 503). |
| WT4 | `test_app_startup_disable_robin_skips_wiring` | Set `DISABLE_ROBIN=1`; verify services NOT wired and `/` redirects to `/brook/chat`. |
| WT5 | `test_app_startup_bad_config_raises` | Inject missing `NAKAMA_VAULT_ROOT`; expect startup to raise (NOT silently swallow). |
| WT6 | `test_no_module_singleton_in_adapters` | Subprocess: import each adapter module, assert no top-level instances. |
| WT7 | `test_no_fastapi_import_in_adapters` | Subprocess static check. |
| WT8 | `test_no_anthropic_import_outside_shared_llm` | Subprocess static check. |

### Playwright smoke test

| # | Test | Asserts |
|---|---|---|
| PT1 | `test_promotion_review_list_loads` | Page renders without console errors. |
| PT2 | `test_writing_assist_renders_for_existing_package` | Given a fixture-built `ReadingContextPackage`, the scaffold renders. |

### Self-imposed gates

- [ ] 零新 dependency 在 `requirements.txt` 之外（`anthropic` 應已存在）
- [ ] Each adapter module ≤ 250 LOC
- [ ] App startup wiring section in `app.py` ≤ 80 LOC of new code
- [ ] `python -m pytest tests/shared/test_source_resolver.py tests/shared/test_reading_source_lister.py tests/shared/test_kb_concept_index_default.py tests/shared/llm/ tests/thousand_sunny/test_app_startup_wiring.py -v` 全綠
- [ ] `python -m pytest tests/` 全綠（既有測試不可 regression）
- [ ] `python -m ruff check shared/source_resolver.py shared/reading_source_lister.py shared/kb_concept_index_default.py shared/llm/ thousand_sunny/app.py tests/` 無 error
- [ ] `python -m ruff format --check ...` clean
- [ ] PR body 含 P7-COMPLETION 區塊 + manual smoke evidence (uvicorn 起得來 + curl 兩條 route 200 截圖或 log)

---

## 6. 邊界（不能碰）

| # | Don't | Why |
|---|---|---|
| 1 | ❌ Modify any `shared/promotion_*` service | frozen contracts; this slice writes adapters that satisfy them |
| 2 | ❌ Modify any schema in `shared/schemas/` | upstream contracts frozen |
| 3 | ❌ Add LLM imports outside `shared/llm/` | W2 invariant; protect existing U5 |
| 4 | ❌ Module-level singletons / global state in adapters | W6 invariant; configuration must flow through constructors |
| 5 | ❌ `os.getenv` inside adapter classes | env reads live in `app.py` startup helper |
| 6 | ❌ Bare `except Exception` | #511 F5 lesson |
| 7 | ❌ Silently swallow startup failures | W4 — let uvicorn crash visibly |
| 8 | ❌ Hardcoded model names / prompts inside method bodies | W7 — version through env defaults + prompt template files |
| 9 | ❌ Real LLM calls in CI | tests mock `anthropic` client; PT1/PT2 Playwright runs against fixture-data dev server |
| 10 | ❌ `git add .` outside scope | hygiene |
| 11 | ❌ Touch `thousand_sunny/routers/promotion_review.py` or `routers/writing_assist.py` route logic | already shipped in #529/#530; only invoke their `set_service` |
| 12 | ❌ Auto-relabel issue from `needs-triage` to `ready-for-agent` | 修修 reviews this brief first |

---

## 7. References

### Primary

- ADR: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion
- QA findings driving this slice: `chore/adr024-qa` branch, `qa/findings.md` (commit 7207018)

### Pattern reference

- Existing `app.py` conditional wiring (line 59 `DISABLE_ROBIN` guard) — extend the same gate
- `_build_default_service(package_root)` factory at `thousand_sunny/routers/writing_assist.py:239-242` — use for S9 wiring instead of writing a new factory
- `FilesystemManifestStore` at `shared/promotion_review_service.py:114-155` — already exists, just construct with a path

### Issue / PR

- Parent PRD: #508
- Slices that ship Protocol-only and motivate this slice: #529 (S8), #530 (S9), #526 (S5), #527 (S6), #528 (S7)
- This slice's issue: TBD (file as `[ADR-024 S10] Production Wiring` after Brief review)

---

## 8. Triage status

`needs-triage` → 等修修讀 Brief 後決定。**不要**自行 relabel `ready-for-agent`，**不要**直接開始 code。

### Optional sub-slice split

If reviewer finds the scope too large for a single PR, suggested split:

- **N518a**: items 1, 2, 3, 6 (disk-backed adapters + app wiring) — uses deterministic-fake fallback for items 4 & 5 to unblock routing-level smoke. ~half day.
- **N518b**: item 4 — `AnthropicClaimExtractor` (real LLM). ~half day including prompt engineering.
- **N518c**: item 5 — `AnthropicConceptMatcher` (real LLM). ~half day.
- **N518d**: item 7 — Playwright smoke test. ~quarter day.

Single-slice (~1.5-2 days for one implementer) is also acceptable if the implementer wants to keep wiring + tests together.

**預估**：~1500-2000 LOC + tests if shipped as a single slice.
