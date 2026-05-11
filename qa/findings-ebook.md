# N518 Ebook E2E QA — findings

**Branch**: `chore/N518-ebook-qa` (from `origin/main` @ `d5b9b5f`, post F06 PR #546)
**Mode**: `NAKAMA_PROMOTION_MODE=dry_run` (default)
**Vault**: `E:/Shosho LifeOS`
**Books root**: `E:/nakama-N518-ebook-qa/data/books/` (cwd-relative default of `book_storage.books_root()`)
**Started**: 2026-05-10
**Goal**: Mirror the inbox QA in [`qa/findings.md`](findings.md) (commit `bc1e27d`) but exercise the **ebook** half of the promotion pipeline now that PR #546 (F06) aligns the lister + blob loader to `book_storage.books_root()`. Two seeded ebooks cover both `has_original` branches the registry treats differently.

**Status**: ✅ **PASS** — F06 wiring verified end-to-end for ebooks. 13/14 checks pass; Q13 N/A (same as inbox QA — package authoring is N518-out-of-scope). 3 ebook-specific findings + 3 inbox findings (F03/F04/F05) re-bite — none block ship.

## Findings 一覽

| ID | Severity | Title | 阻塞 N518？ | 建議 |
|----|----------|-------|-----------|------|
| E01 | LOW | bogus `ebook:` id 解析失敗時無 log warning，inbox unknown-namespace 卻會 log；observability 不一致 | 否 | 順 F03 redesign PR 補一行 `_logger.warning("ebook get_book missed", ...)` 在 `_resolve_book` 的 `book is None` 分支 |
| E02 | MEDIUM (UX) | `annotation_only_sync` ebooks 被 list view 完全過濾，使用者沒地方看到「這本書 preflight 還缺什麼才能 promote」 | 否 | 獨立 PR：list view 加「待補強」section（preflight=annotation_only_sync / proceed_with_warnings 但帶 missing-evidence-track 的 source）|
| E03 | LOW | 多個 concept item_id 含 literal whitespace + colon（`cand_001_chapter 1 chapter 1: qa `）；下游 commit 對 disk path 可能有 slug 安全問題 | 否（dry-run 不寫 KB）| 與 5/7 batch handoff §Stage 8 follow-up 「concept slug validator patch」合併處理 |
| F03* | HIGH UX | review surface 認知負擔過高（同 [`qa/findings.md`](findings.md) F03） | 否 | 已在 inbox findings 標記為獨立 redesign worktree 的 scope |
| F04* | LOW | bogus source GET 渲染相同 empty-state 不是 404（同 inbox F04） | 否 | 順 F03 redesign PR 一起 |
| F05* | LOW | HTTPException 在瀏覽器顯示 raw JSON `{"detail":"..."}`（同 inbox F05）| 否 | 順 F03 redesign PR 一起 |

`*` = 同 inbox findings.md 的 F-id 在 ebook 軸再次顯現；不重新編號，只記錄 ebook 變體 repro。

## Verification: F06 fix is wired correctly

Pre-fix `qa/findings.md` F06 said: 「ingest 一本書到 `book_storage.store_book_files(...)` → 落到 `{cwd}/data/books/{book_id}/`；但 GET `/promotion-review/` 看不到該書」（list-vs-resolver 走不同 root）。

Post-fix（PR #546）after seeding `qa-bilingual-and-original` via `store_book_files` + `insert_book` and booting uvicorn from the worktree cwd, the list view surfaces it as `ebook:qa-bilingual-and-original` immediately. The lister's `books_root` resolves to the same `E:/nakama-N518-ebook-qa/data/books/` that `store_book_files` wrote to via `book_storage.books_root()`. Q14 cache invalidation also confirms the lister re-scans on every request — adding a third book mid-session surfaces it without server restart.

## Next steps

1. Commit findings + seed script to `chore/N518-ebook-qa` branch + push + open PR for review
2. Surface E01/E02/E03 as follow-up issues OR fold into F03 redesign PR per agent triage
3. (No 14-check redo required — F06 fix verified)

---

## Pre-flight checks

- [x] **PF1** `NAKAMA_VAULT_ROOT="E:/Shosho LifeOS"` exported in uvicorn shell
- [x] **PF2** `DISABLE_ROBIN` unset
- [x] **PF3** `E:/nakama-N518-ebook-qa/data/books/` has 2 seeded book directories (`qa-bilingual-only`, `qa-bilingual-and-original`)
- [x] **PF4** `E:/Shosho LifeOS/KB/Wiki/Concepts/` exists (carried over from inbox QA)
- [x] **PF5** uvicorn boots; lifespan logs `nakama.web.promotion_wiring INFO — promotion surfaces wired`

## Seed setup

`qa/seed_ebooks.py` mirrors `tests/shared/test_books_path_alignment.py` two_root_layout fixture but writes to the worktree's actual `data/books/` (the cwd-relative default of `book_storage.books_root()`). Two books cover both `has_original` branches:

| book_id | has_original | preflight action | surfaces in list? |
|---------|--------------|------------------|-------------------|
| `qa-bilingual-only` | `False` | `annotation_only_sync` | ❌ filtered (E02) |
| `qa-bilingual-and-original` | `True` | `proceed_full_promotion` | ✅ |

Each EPUB has 5 chapters × ~190 words filler + a 5-entry TOC so it clears the `_VERY_SHORT_THRESHOLD=200` and `_MIN_TOC_ENTRIES=3` guards in `shared/promotion_preflight.py`.

## Smoke (Q1-Q5) — wiring works end-to-end for ebooks

- [x] **Q1** `GET /promotion-review/` returns **200** with the same chassis-nav list page as inbox; ebook entry rendered alongside the 14 inbox candidates
- [x] **Q2** `ebook:qa-bilingual-and-original` visible in the list (the bilingual-only book is filtered by preflight — see E02)
- [x] **Q3** `GET /promotion-review/source/{base64url(ebook:qa-bilingual-and-original)}` → **200** with empty-state template + `Start review` button (manifest not yet built)
- [x] **Q4** `POST .../start` → **303** redirect to source URL; subsequent **GET** renders 11 items: `::index` + `ch-1`..`ch-5` source_page items + 5 concept candidates
- [x] **Q5** All 6 source_page item reasons carry the `[DRY-RUN]` fingerprint emitted by `shared.dry_run_extractor`. 5 concept reasons (`single-chapter mention (1 chapter ref); not promoting globally`) come from the deterministic chapter-ref counter in `shared.dry_run_matcher`, by-design no `[DRY-RUN]` (matches inbox Q8)

## Manifest persistence (Q6-Q8)

- [x] **Q6** Manifest at `E:/Shosho LifeOS/.promotion-manifests/ZWJvb2s6cWEtYmlsaW5ndWFsLWFuZC1vcmlnaW5hbA.json` (= base64url(`ebook:qa-bilingual-and-original`)) — same encoding rule as inbox manifest
- [x] **Q7** Manifest JSON: `schema_version=1`, `source_id="ebook:qa-bilingual-and-original"`, `status="needs_review"`, `items=11` (6 source_page + 5 concept), recommender model_name carried per N518a
- [x] **Q8** Source_page reasons all carry `[DRY-RUN]` fingerprint. Concept items follow the deterministic chapter-ref logic (1 chapter ref → `keep_source_local`). `target_kb_path` resolves to `KB/Wiki/Sources/qa-bilingual-and-original/index.md` for the source-page index item

## Failure surfaces (Q9-Q11)

- [x] **Q9** `GET /promotion-review/source/{base64url(ebook:does-not-exist-foo)}` → **200** + same empty-state UI as a real-but-unreviewed source. Same F04 bite as inbox — no 404 differentiation
- [⚠] **Q10** **No log warning emitted** for the bogus ebook id (E01). Inbox QA Q10 expected `source_resolver got unrecognized namespace prefix` for unknown namespaces; the `ebook:` namespace IS recognized so the resolver path goes through `_resolve_book` which silently returns `None` when `book_storage.get_book(book_id) is None`. Different from inbox unknown-namespace path. Documented as E01 below
- [x] **Q11** `POST .../start` with bogus ebook id → **400 Bad Request** + body `{"detail":"source_id='ebook:does-not-exist-foo' did not resolve to a ReadingSource"}` — same shape as inbox Q11

## Writing Assist surface (Q12-Q13)

- [x] **Q12** `GET /writing-assist/{base64url(ebook:does-not-exist-foo)}` → **404** + `{"detail":"\"package not found for source_id='ebook:does-not-exist-foo'\""}` — route is wired (the 503 'service not configured' regression we'd see if N518a wiring broke is absent), and the F05 raw-JSON bite repeats here
- [~] **Q13** **N/A** — `ReadingContextPackage` authoring pipeline is out of N518 scope; vault has no `.reading-context-packages/` for ebook source_ids either. Same N/A as inbox Q13

## Cache invalidation (Q14)

- [x] **Q14** mtime-based / iterdir invalidation works for ebooks: baseline 1 ebook entry → seeded 3rd book mid-session via `qa/seed_ebooks.py` extension → re-scan immediately surfaces `ebook:qa-cache-invalidation-test`. **No uvicorn restart required**. The lister's `_list_books` walks `books_root.iterdir()` on every call (`shared/reading_source_lister.py:115`) and the registry resolves each book via `book_storage.get_book` → fresh DB row → fresh on-disk EPUB read. No process-level cache to bust

---

## Findings (file as discovered)

### Format

```
### Exx [severity] — short title
**Repro**: <minimal steps>
**Expected**: <what should happen>
**Actual**: <what happened>
**Evidence**: <log lines, screenshots, request/response>
**Root cause**: <if known>
**Fix proposal**: <or "open">
```

Severity: BLOCKER (can't proceed) / HIGH (functional bug) / MEDIUM (UX issue) / LOW (cosmetic / observability)

`E*` numbering for ebook-specific. Inbox-derived issues that re-bite reuse their `F*` ids from `qa/findings.md`.

---

## Findings log

### E01 [LOW] — bogus `ebook:` id resolves to None silently; no observability log

**Repro**: `GET /promotion-review/source/{base64url("ebook:does-not-exist-foo")}` against running uvicorn with empty/missing book row. Then `tail` `qa/uvicorn.log`.

**Expected** (per inbox QA Q10 contract): a `WARNING` line categorizing the missed resolve so an operator can tell "this id never matched anything".

**Actual**: no log line at all. Subsequent `POST .../start` *does* surface a 400 + ValueError (Q11), but the GET path silently shows the empty-state — operator sees nothing in logs.

**Evidence**: `qa/uvicorn.log` lines around 21:28:29 — only the access log "GET ... 200 OK" entry; no `nakama.shared.reading_source_registry` warning.

**Root cause**: [`shared/reading_source_registry.py:145-155`](../shared/reading_source_registry.py#L145-L155) — `_resolve_book` calls `book_storage.get_book(book_id)` which returns `None` for missing rows; the code path takes `if book is None: return None` with no log call. The matching log call exists for `book_storage.get_book` *raising* (line 149) but not for the `None` return.

**Why N518 didn't catch it**: inbox QA Q10 was framed against the unknown-namespace branch (`source_resolver got unrecognized namespace prefix`); ebook bogus-id sits in a different code path (recognized namespace, missing row). Asymmetric coverage.

**Fix proposal**: 1-line patch: in `_resolve_book`'s `if book is None: return None` add a `_logger.warning("ebook resolve missed", extra={"category": "ebook_resolve_missed", "book_id": book_id})` — mirrors the existing `ebook_get_book_failed` shape so log filters already aggregating that category pick it up. Independent ~5 LOC PR; can ride F03 redesign PR.

---

### E02 [MEDIUM] — `annotation_only_sync` ebooks vanish from list view; no place to see "needs original uploaded"

**Repro**: seed `qa-bilingual-only` (5-chapter EPUB, no original variant) via `qa/seed_ebooks.py`. `GET /promotion-review/` — book is missing.

**Expected**: even though preflight rules out full promotion, the user has *some* surface that says "this ebook is in the system but missing an evidence track; upload an original to enable promotion." Otherwise the book is invisible until that upload happens, but the upload UI requires knowing the book exists.

**Actual**: `list_pending` filters strictly to `preflight_action ∈ {proceed_full_promotion, proceed_with_warnings}` ([`shared/promotion_review_service.py:260`](../shared/promotion_review_service.py#L260)). `annotation_only_sync` (and `defer_*`, `skip`) all silently disappear. The book IS resolvable via direct GET if you know the source_id, and POST `/start` correctly raises 400 ("preflight action=annotation_only_sync ∉ proceed actions"), but discovery is broken.

**Evidence**:
- `preflight.run(rs)` for `qa-bilingual-only`: `recommended_action='annotation_only_sync'`, `reasons=['evidence_track_missing']`, `word_count=950`
- list view body grep for `qa-bilingual-only` → 0 matches
- Direct GET `/promotion-review/source/{base64url(ebook:qa-bilingual-only)}` → 200 + empty-state (no Start button reasoning visible)

**Root cause**: by-design filter in `list_pending`. The Brief §1 contract reads "filter to proceed actions", which is correct for the *promote* path but leaves the *needs-attention* surface unbuilt.

**Fix proposal**: Independent PR — list view adds a collapsed "待補強 / pending input" section listing sources with `recommended_action ∈ {annotation_only_sync, defer_bilingual_only}` + their `evidence_reason` + a "Upload original" CTA pointing at the (future) ebook upload flow. Or, lighter weight: just include them in the same list with a `lang` / `action` badge so the row says "需上傳原文 EPUB". Pairs with F03 redesign so the affordance lands cleanly.

---

### E03 [LOW] — concept item_id contains literal whitespace + colon, repeating chapter title

**Repro**: complete the Q4 POST start flow against `qa-bilingual-and-original`. Inspect manifest concept items.

**Expected**: concept item_ids look slug-safe (`cand_001_qa-filler` or `cand_001_chapter-1`). No raw whitespace, no double colons, no duplicated tokens.

**Actual**: 5 concept item_ids of the form `cand_001_chapter 1 chapter 1: qa ` — note the trailing whitespace, the literal space-colon-space, and the doubled "Chapter 1 Chapter 1" coming from `<h1>Chapter N</h1>` adjacent to `Chapter N: QA Filler`. Same `item_id` is used as `cand_xxx_<concept-name>` and likely flows into `target_kb_path` slug derivation when commit happens.

**Evidence**: `E:/Shosho LifeOS/.promotion-manifests/ZWJvb2s6cWEtYmlsaW5ndWFsLWFuZC1vcmlnaW5hbA.json` — items 7-11.

**Root cause**: `shared/dry_run_matcher.py` builds candidate phrases by sliding-window over chapter title + first paragraph; for the QA fixture's repetitive heading + filler text the match overlaps the heading bounds twice. Real production books would have different shape but the same code path can produce trailing-whitespace / mixed-punctuation slugs.

**Cross-reference**: `memory/claude/project_session_2026_05_07_pm_stage4_batch_handoff.md` §Stage 8 follow-up flagged "concept slug validator patch — `Ergogenic Aid`, `Na+-K+ Pump`, `creatine supplementation` 等含空格/+號被拒" against the textbook ingest pipeline. Same general class.

**Fix proposal**: Independent PR — apply `shared.concept_validators` slug normalizer (already shipped in Stage 1.5 freeze per ADR-020) inside `dry_run_matcher` candidate construction. Or assert `target_kb_path` doesn't contain raw whitespace via Pydantic field_validator on the manifest schema. Defer the textbook side until S8 follow-up lands.

---

## Inbox findings that re-bite on the ebook surface

For the avoidance of new tickets, F03 / F04 / F05 from `qa/findings.md` reproduce identically against `ebook:` source_ids:

- **F03** (review surface UX) — same heterogeneous-item / action-vs-decision overlap. Ebook-specific note: with 5 chapters there are now 6 source_page items (index + ch-1..ch-5) which makes the cognitive load even higher than the typical inbox 2-3 page case. F03 redesign should section "目錄頁 + 每章引用" together rather than listing them flat.
- **F04** (bogus → empty-state) — Q9 above
- **F05** (HTTPException raw JSON) — Q12 above
