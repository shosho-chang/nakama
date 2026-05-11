# Plan: monolingual-zh book reader pilot — minimum viable subset

**Date:** 2026-05-10
**Owner:** shosho-chang
**Worktree:** `E:/nakama-mono-zh-pilot` on `feat/monolingual-zh-pilot-minimal` (off `origin/main` HEAD `d5b9b5f`)
**Parent PRD:** [`docs/plans/2026-05-09-monolingual-zh-reader-annotation-pilot-prd.md`](2026-05-09-monolingual-zh-reader-annotation-pilot-prd.md) (PR #507, OPEN, docs-only)

## Why this plan exists vs the PRD

PRD #507 specs 7 vertical slices (S1-S7) covering ebook + document + ingest gate + acceptance. 修修 wants to **walk through reading one Chinese book today**, not ship the full Phase 1. This plan carves the **minimum viable subset** to enable that walkthrough without mortgaging the PRD's design — anything not in this subset stays scoped for later PRs.

**Subset scope**:
- ✅ Cover only the **EPUB end** (PRD §2.A); skip Document/inbox path (S5, S6) and formal ingest gate (S7)
- ✅ Cover only the **server-side** schema + upload contract (S1, S2 minus the UI niceties); UI gets a tiny badge + button hide, no full mode-toggle UI (S3 deferred)
- ✅ Walkthrough subs for the formal e2e test in S7 — 修修 manually uploads a real 台版中譯 EPUB and verifies disk artifacts

## Out of scope (deferred to follow-up issues)

- ❌ S3 EPUB upload UI mode toggle (radio button + auto-fill from metadata.lang) — UI nicety; today 修修 can use the existing form (bilingual slot accepts the zh EPUB, server auto-detects mode)
- ❌ S5 Document mode detection + override endpoint
- ❌ S6 Document Reader + inbox UI mode-aware
- ❌ S7 Formal ingest gate change for `POST /api/books/{id}/ingest-request` + `POST /robin/start` (gates today fail loud via existing `has_original` check; not pretty, but correct for monolingual-zh which has `has_original=False`)
- ❌ ADR-024 number collision resolution (PR #507's `ADR-024-cross-lingual-concept-alignment.md` will collide with main's `ADR-024-source-promotion-and-reading-context-package.md` when #507 merges — that's PR #507's owner to handle, not this plan)
- ❌ All §6 PRD Out-of-Scope items (annotation_merger sync, textbook ingest cross-lingual, Concept namespace zh aliases, etc.)

## Slices in this PR

### α — Schema + lang_detect (server-side, sandcastle-style isolation)

**Files**:
- New `shared/lang_detect.py` — `detect_lang(text: str) -> Literal["zh-Hant", "en", "unknown"]` thin wrapper over [langdetect-py](https://pypi.org/project/langdetect/) (PRD §4.6 selection rationale). `unknown` for text < 30 chars or langdetect's `LangDetectException`. NFC-normalise input.
- New `shared/source_mode.py` — `Mode = Literal["monolingual-zh", "bilingual-en-zh"]` + `detect_book_mode(metadata_lang: str | None, body_sample: str | None) -> Mode` priority: explicit metadata.lang `zh*` → monolingual-zh; `en*` → bilingual-en-zh; missing/ambiguous → fall to body sample via `detect_lang()`; final fallback → bilingual-en-zh (preserve existing default behavior).
- Modify `shared/schemas/books.py` `Book` — add required `mode: Mode` field. `lang_pair` kept for back-compat read; new code reads `mode`.
- Modify `shared/state.py` — add `mode TEXT NOT NULL DEFAULT 'bilingual-en-zh'` to `books` table DDL (matches existing inline DDL pattern; CLAUDE.md says canonical DDL is `migrations/*.sql` but `_init_tables` is the live source-of-truth).
- New `migrations/NNN_add_book_mode.sql` — `ALTER TABLE books ADD COLUMN mode TEXT NOT NULL DEFAULT 'bilingual-en-zh';` for any deployed env.
- Modify `shared/book_storage.py` `insert_book` / `get_book` — read/write the new column, default to `'bilingual-en-zh'` when absent (back-compat for any pre-migration row).

**Tests** (`tests/shared/test_lang_detect.py`, `tests/shared/test_source_mode.py`, `tests/shared/test_book_storage.py` updates):
- 5 zh fixtures (台版繁中科普 + 台版繁中文學 + 台版繁中政治 + 中國簡中科技 + zh-TW 詩詞) → all label `zh-Hant`. Simplified content gets `zh-Hant` too (PRD §1: 修修永遠 zh-Hant + en 兩語言；簡體不考慮 — fold into zh-Hant)
- 5 en fixtures → all `en`
- < 30-char strings → `unknown`
- `detect_book_mode("zh-TW", None)` → `monolingual-zh`
- `detect_book_mode(None, "<chinese body>")` → `monolingual-zh`
- `detect_book_mode(None, None)` → `bilingual-en-zh`
- `detect_book_mode("en", "<chinese body>")` → `bilingual-en-zh` (explicit metadata wins)
- `Book(mode="monolingual-zh", ...)` validates; `Book(mode="garbage")` raises `ValidationError`
- `insert_book(Book(mode="monolingual-zh", ...))` then `get_book(id)` round-trip preserves mode

**Dependency**:
- Add `langdetect` to `requirements.txt`

### β — `/books/upload` mode-aware (server-side, sandcastle-style)

**Files**:
- Modify `thousand_sunny/routers/books.py:118-197` (`books_upload`):
  - `bilingual: UploadFile = File(...)` → `bilingual: UploadFile | None = File(None)`
  - Add `mode: str = Form("auto")` parameter
  - Server validates: **at least one of `bilingual` or `original` must be present**, else 400
  - When `mode == "auto"`: call `shared.source_mode.detect_book_mode(meta.lang, body_sample)`. `body_sample` extracted from first chapter (first 500 chars after sanitize). For `bilingual=None`, the source EPUB is `original` (English-only upload — covered for completeness even though Phase 1 main path is monolingual-zh).
  - When `mode in ("monolingual-zh", "bilingual-en-zh")`: trust caller, skip detection
  - When mode resolves to `monolingual-zh`: store the only uploaded EPUB as `bilingual.epub` slot (per PRD §4.2: "keep `bilingual.epub` slot name for Phase 1 — Phase 2 rename refactor"); `has_original=False`; `lang_pair="zh-zh"`.
  - `Book` constructor includes `mode=resolved_mode`.

**Tests** (`tests/thousand_sunny/test_books_upload.py` updates):
- POST with bilingual=zh epub, mode="auto" → 303 → Book.mode="monolingual-zh"
- POST with bilingual=en epub, mode="auto" → 303 → Book.mode="bilingual-en-zh"
- POST with bilingual=None, original=en epub, mode="auto" → 303 → Book.mode="bilingual-en-zh", has_original=True
- POST with bilingual=None, original=None → 400
- POST with bilingual=zh epub, mode="monolingual-zh" (explicit) → 303 → mode preserved

### γ — Reader UI mode badge + ingest hide

**Files**:
- Modify `thousand_sunny/templates/robin/book_reader.html`:
  - Add a small `<span class="mode-badge">{{ book.mode }}</span>` in the reader-bar `.left` next to title; CSS for visual differentiation between two modes
  - Wrap `<button id="ingestBtn">` in `{% if book.has_original %}…{% endif %}` so monolingual-zh book reader doesn't show the disabled button at all (cleaner than disabled-with-tooltip)

**No JS changes** needed — `book_reader.js` consumes `book.has_original` only for ingest button enable/disable; with the button DOM-removed for monolingual-zh, JS code paths stay inert.

**No new test** — manual smoke during walkthrough.

### Acceptance walkthrough (run today after slices α/β/γ merged-locally)

1. Locate a real 中文 EPUB on disk (修修 picks one)
2. Boot uvicorn from worktree pointed at `E:/Shosho LifeOS` (same as N518 ebook QA setup)
3. `curl POST /books/upload` with `bilingual=@<path-to-zh-epub>` and `mode=auto` (or via UI; both should work)
4. Verify response: `303 See Other` redirect to `/books/{book_id}`
5. Inspect `books` SQLite row: `mode='monolingual-zh'`, `has_original=0`, `lang_pair='zh-zh'`
6. Open `/books/{book_id}` in browser — confirm:
   - Reader loads
   - Mode badge visible
   - 📥 Ingest 整本書 button **not present**
   - foliate-js renders the book in single-column zh layout
7. Highlight 3 segments + annotate 1 + reflection 1 (uses existing reader UI, no changes)
8. Inspect `E:/Shosho LifeOS/KB/Annotations/{slug}.md`: v3 schema, items list contains the 5 entries with correct `cfi` + `text_excerpt` + `text` (highlight) / `note` (annotation) / `body` (reflection)
9. Wait for BG task; inspect `E:/Shosho LifeOS/KB/Wiki/Sources/Books/{slug}/digest.md`: file exists, chapter sections rendered, KB hits section shows either `_none yet — run KB sync first_` (PRD §10 risk) or actual hits if ADR-022 multilingual rebuild already done
10. (Optional) `POST /api/books/{id}/ingest-request` → expect 400 (existing `has_original=False` gate fires correctly)

If steps 1-9 all pass, slices α/β/γ are correct and we ship the PR.

## Deferred follow-up issues to file post-walkthrough

| Issue | Slice | Why deferred |
|---|---|---|
| N6XX UI mode toggle in `book_upload.html` | S3 | UI-only nicety; server-side mode detection already works |
| N6XX Document/inbox mode detection + `mode:` frontmatter | S5 | Different code path; out of demo scope |
| N6XX Document Reader + inbox UI mode badge | S6 | Pairs with S5 |
| N6XX Formal ingest gate change (mode-aware error msgs) | S7 | Existing `has_original` gate fails correctly; cosmetic improvement |
| N6XX `bilingual.epub` storage rename → `display.epub` | PRD §6.2 | Phase 2 cleanup |
| N6XX ADR-024 number collision resolution | (PR #507 owner) | Not this plan's concern |
| N6XX Backfill script for existing `bilingual: true` frontmatter → `mode: bilingual-en-zh` | S5 | Pairs with S5 |

## Risks acknowledged

- **R1**: `langdetect` is non-deterministic on short strings (uses random seed). Mitigation: set `DetectorFactory.seed = 0` in `shared/lang_detect.py` per [langdetect docs](https://pypi.org/project/langdetect/#basic-usage). Tests verify deterministic output.
- **R2**: `metadata.lang` field can be missing or BCP-47-malformed (e.g. `zho`, `chi`, `zh_TW` underscore). `detect_book_mode` matches on `lang.startswith("zh")` (case-insensitive, after normalising `_` → `-`) for permissive zh detection.
- **R3**: ADR-022 multilingual embedding rebuild status unknown. PRD §10 already flags as risk; walkthrough step 9 records actual digest.md state — if KB hits empty, file follow-up issue against ADR-022 rebuild ops, don't re-scope this plan.
- **R4**: `Book.mode` becomes required on a table that has no migration applied locally. Mitigation: `_init_tables` DDL has the default; new `migrations/*.sql` covers prod deploys; `get_book` defensive read defaults to `'bilingual-en-zh'` when column missing.

## What this plan deliberately does NOT do (sanity guard)

- ❌ Does not call any LLM (no translator, no annotation_merger, no ingest)
- ❌ Does not touch any `KB/Wiki/Concepts/*.md` page
- ❌ Does not change any existing English-bilingual upload flow (back-compat tests guard this)
- ❌ Does not move `book_storage` away from cwd-relative `data/books/` (F06 fix preserved)
- ❌ Does not introduce a new ADR — operates within existing ADR-021 v3 annotation contract

End of plan.
