# PRD: Zotero as primary ingest path + two-file source ingest pattern

> **Stage anchor**: Stage 1 Discovery + Stage 2 Reading + Stage 3 Synthesis (CONTENT-PIPELINE.md)
> **Line beneficiary**: Line 2 讀書心得 critical path; Line 3 文獻科普 source supply
> **Date**: 2026-05-05
> **Related**: ADR-018, ADR-019, ADR-017, PR #354, [grill snapshot](../../memory/claude/project_zotero_integration_grill_2026_05_05.md), [Robin CONTEXT](../../agents/robin/CONTEXT.md)

---

## Problem Statement

修修 (nakama's primary user, a Health & Wellness content creator with a sustained academic reading practice) wants to read English-Chinese bilingual academic papers in nakama's Reader, with the highlights and annotations he makes during reading captured as **first-class personal voice** in the KB.

Previous attempt — DIY URL scraping with a 5-layer OA fallback (PR #352-356) — was QA'd on 2026-05-05. The pipeline reported `status=ready`, but the resulting markdown quality (PDF parsing 劣化, paywall edge cases, lazy-load failures, JS-rendered content) is **not good enough** for actual reading. The strategic conclusion: do not compete with Zotero browser-extension on web capture quality — Zotero already solves paywall (browser session), full DOM + assets, 1000+ community translators for metadata, and ToS risk transfer.

修修 already has a working Zotero workflow: open paper → save via browser-extension → annotate in Zotero. The gap is that Zotero content sits inside Zotero's database and is **not visible** to nakama's bilingual Reader, his annotations cannot be captured into KB, and immersive-translate's DOM injection pollutes the saved snapshot if used naively before saving.

The real gap is to **bridge Zotero → vault**, not redo capture. 修修's annotations need to land in KB as first-class data so they inform future content creation (Line 2 心得, Line 3 文獻科普).

## Solution

Sync Zotero items into nakama as the **primary** ingest path. The end-to-end flow:

1. 修修 saves a paper to Zotero (existing browser-extension workflow, unchanged)
2. 修修 right-clicks the item in Zotero → "Copy Link" → pastes the resulting `zotero://select/library/items/{itemKey}` URI into nakama's Inbox UI form (same form as URL ingest)
3. The synced paper appears as a clean English markdown in the Inbox, with figures rendered correctly in both Obsidian and the Reader
4. 修修 opens the paper in the bilingual Reader, hits "翻譯" to generate the Chinese sibling (existing PR #354 mechanism)
5. 修修 reads + highlights + annotates; annotations are stored per ADR-017
6. When done, 修修 hits "ingest" — **two files** appear in `KB/Wiki/Sources/`:
   - **Raw source page** — LLM-untouched, re-extracted from Zotero's snapshot
   - **Annotated source page** — bilingual + 修修's annotations woven inline

DIY URL scrape stays as escape hatch for non-Zotero content (YouTube transcripts, podcasts, social posts). PubMed digest cron stays unchanged. Translation engine stays self-built (`shared/translator.py`); Immersive Translation Pro is a separate evaluation軸 that does not affect this pipeline.

## User Stories

1. As 修修, I want to paste a `zotero://select/library/items/{itemKey}` link into the Inbox UI form and have the paper synced into vault, so that I do not need to develop Zotero plugins or run separate scripts.
2. As 修修, I want the synced paper to appear as English-only markdown in Inbox, so that I can read the original content untouched before deciding whether to translate.
3. As 修修, I want figures embedded in the paper to display correctly in both Obsidian and the nakama Reader, so that I can read figures alongside text without opening Zotero.
4. As 修修, I want to click "翻譯" in the Reader and have a bilingual sibling generated, so that I can read English/Chinese side by side using the existing PR #354 mechanism.
5. As 修修, I want my highlights and annotations made on the bilingual page to be saved per ADR-017, so that they survive across reading sessions.
6. As 修修, I want my annotations made on either the raw English page or the bilingual page to merge into a single annotation file, so that I do not have to think about which file my notes belong to.
7. As 修修, I want to click an "ingest" button when done reading and have two source pages produced — one raw (LLM-untouched) and one annotated (bilingual + my notes inline) — so that the KB has both my citable source and my personal voice as first-class data.
8. As 修修, I want the raw source page to be re-extracted from Zotero's snapshot file (not copied from inbox), so that the file is guaranteed LLM-untouched (zero-trust pure source).
9. As 修修, I want the annotated source page to weave annotation callouts inline at highlight positions, so that future Concept extraction (Phase 2) can read my framings as part of the source content.
10. As 修修, I want both source pages to cross-reference each other via frontmatter, so that I and downstream tools can navigate between them.
11. As 修修, I want Wiki link `[[slug]]` to default to the raw page (not annotated), so that citations from other Wiki pages point to the citable source.
12. As 修修, I want PDF-only items (preprints from arXiv / bioRxiv) to also work via PDF fallback, so that I do not get an error when I sync a paper that has no HTML snapshot.
13. As 修修, I want HTML snapshot to win when both HTML and PDF attachments exist, so that I get the highest-fidelity rendering of the paper text.
14. As 修修, I want pasting the same Zotero link twice to be a no-op (skip silently, return existing inbox path), so that I do not accidentally lose annotations by overwriting.
15. As 修修, I want the sync to NOT trigger Robin's full concept/entity extraction pipeline, so that the MVP ships fast and observation #3 (annotation-aware extraction) can be designed properly later.
16. As 修修, I want the sync to run against the local Zotero SQLite database, so that I do not need to set up Zotero cloud sync or expose API keys.
17. As 修修, I want the sync to copy the SQLite file to a tmp location before reading, so that Zotero desktop can run normally without DB lock contention.
18. As 修修, I want the synced markdown frontmatter to carry Zotero metadata (item key, attachment path, attachment type, DOI, authors, publication), so that downstream Robin tools and KB search can use this metadata.
19. As 修修, I want the snapshot's `_assets/` (figures, CSS) to be copied into the vault, so that the vault is self-contained and figures show in Obsidian even when Zotero is closed.
20. As 修修, I want the snapshot's HTML file itself to stay in Zotero storage (not copied into vault), so that the vault stays minimal and Zotero remains the archival source-of-truth.
21. As 修修, I want the Inbox UI form to accept either a regular https URL or a Zotero URI in the same input field, so that I do not need to remember which form to use.
22. As 修修, I want the inbox row UI to indicate Zotero-sourced items distinctly from URL-scraped items (badge / icon / source_type field), so that I can visually tell where the content came from.
23. As 修修, I want the sync agent to run on my local machine (Windows primary, Mac occasional) — not on the VPS — so that it can directly access local Zotero storage paths.
24. As 修修, I want the sync to fail gracefully (writing a placeholder file) when an item has neither HTML snapshot nor PDF, so that I see the failure in inbox UI rather than a silent error.
25. As 修修, I want the existing Reader translate button (PR #354) to work on Zotero-synced files unchanged, so that the codebase does not fork into two reader/translator paths.
26. As 修修, I want the sync to NOT switch translation engine to Immersive Translation Pro, so that I retain control over台灣繁中 glossary and user_terms learning, and so that PR #354 + ADR-017 architecture stays intact.
27. As 修修, I want a separate ingest endpoint distinct from the regular URL ingest endpoint, so that the two-file fan-out only runs for Zotero-sourced files (reusing single-source ingest for URL items).
28. As 修修, I want sync of a single Zotero item to be a one-shot transaction (paste → status updates → done), so that I can debug failures without batch state to manage.
29. As 修修, I want to read the PDF directly in Zotero when I need higher-resolution figures (since `_assets/` from HTML snapshot may be web-display-resolution), so that I have a clear fallback path for figure-heavy papers.
30. As 修修, I want the sync agent's Zotero storage path to be configurable via environment or config (default `%USERPROFILE%\Zotero\` on Windows, `~/Zotero/` on Mac), so that future re-installation or non-default paths work.

## Implementation Decisions

### Modules

**New modules**:

- **zotero_reader** — *deep module*. Owns SQLite-backed access to the local Zotero library. Public surface: `parse_zotero_uri(uri)` returns the item key from a `zotero://select/library/items/{key}` URI; `ZoteroReader(storage_root).get_item(item_key)` returns a `ZoteroItem` value object (item key, title, DOI, authors, publication, date, chosen attachment absolute path, attachment type). Internally: copies `zotero.sqlite` to a tmp path before each read (handles desktop lock); resolves child attachments; selects primary attachment with HTML snapshot preferred and PDF fallback; raises a sentinel when no usable attachment is present.

- **zotero_assets** — *deep module*. Pure filesystem + string operations. Public surface: `copy_assets(snapshot_dir, vault_assets_dir)` copies the entire `_assets/` folder verbatim from Zotero storage to the vault attachments directory and returns a mapping from old asset paths to new vault-relative paths; `rewrite_image_paths(md, asset_map)` is a pure function that rewrites image src strings in the markdown to vault-relative paths.

- **zotero_sync** — *shallow orchestrator*. Public surface: `sync_zotero_item(item_key)` runs the full sync: ZoteroReader.get_item → HTML or PDF extractor → asset copy → IngestResult. HTML path delegates to existing web-scraper Trafilatura on the local snapshot file (adapter accepts a Path instead of a URL). PDF path delegates to existing pdf-parser pymupdf4llm. Returns an IngestResult that the existing inbox writer can persist.

- **zotero_ingest** — *deep module*. Public surface: `ingest_two_files(slug, item_key, snapshot_path, bilingual_path)` produces both source pages. Re-extracts the raw English by re-running the same extractor used during sync (zero-trust isolation from any inbox-side mutations), reads the bilingual sibling and the annotation set, calls `annotation_weave.weave()` to produce the annotated markdown, writes both files to KB Wiki Sources with cross-link frontmatter, and returns the two file paths. Does NOT trigger Robin concept/entity extraction.

- **annotation_weave** — *deep, pure function*. Public surface: `weave(bilingual_md, annotation_set)` returns annotated markdown. For each annotation in the set, finds the `ref` text in the bilingual content and inserts a `> [!annotation]` callout below the matching paragraph. Highlights are preserved verbatim (the bilingual md output from the existing reader-save flow already carries them). MVP uses exact substring match on `ref`; ref-not-found cases log a warning and skip that annotation.

**Modified existing modules**:

- **url_dispatcher** — branch on URI scheme. When the input is a `zotero://` URI, delegate to zotero_sync; otherwise existing behavior unchanged.

- **inbox_writer** — frontmatter schema gains five Zotero fields (item key, attachment path, attachment type, DOI, authors, publication) plus the existing `source_type: zotero` marker. New method `find_existing_for_zotero_item(item_key)` mirrors the existing `find_existing_for_url(original_url)` reverse-lookup pattern, used for re-sync skip.

- **robin router** — new endpoint for triggering the two-file ingest given a Zotero-sourced inbox slug. Returns a 303 redirect to inbox upon success.

- **Inbox UI form template** — input regex / validator extended to accept `zotero://select/library/items/[A-Z0-9]+` in addition to https URLs.

**Reused unchanged**:

- web-scraper Trafilatura extraction (HTML → markdown)
- pdf-parser pymupdf4llm extraction (PDF → markdown, from PR #71)
- self-translator Claude Sonnet + glossary (PR #354 bilingual sibling generation)
- annotation_store (ADR-017 — title-based slug derivation makes raw and bilingual share the same annotation slug naturally)

### Architectural decisions (locked)

- Zotero is **primary** ingest path; DIY URL scrape (PR #352-356) is escape hatch (ADR-018)
- Sync agent runs on local machine only (Windows primary, Mac occasional); no VPS, no cloud sync (修修 has no Zotero cloud sync)
- SQLite **direct read** (not Web API)
- Vault holds **MD + assets**; HTML snapshot file stays in Zotero storage (Q2 + Q3)
- Two-file fan-out at ingest: raw (re-extracted) + annotated (woven) (ADR-019)
- Translation engine stays self-built; Immersive Translation Pro is decoupled (Q9)
- Re-sync defaults to skip (existing-item short-circuit); Phase 2 adds explicit re-sync
- Robin concept/entity extraction is NOT modified for MVP (Q10); Phase 2 解 observation #3

### Schema additions

- Inbox MD frontmatter (Zotero items): `zotero_item_key`, `zotero_attachment_path`, `attachment_type` (`text/html` or `application/pdf`), `doi`, `authors`, `publication`, `source_type: zotero`, plus existing fulltext_status / fulltext_layer / fulltext_source / title fields
- KB Wiki Sources frontmatter: `raw_source` (in annotated page) ↔ `annotated_sibling` (in raw page), plus all Zotero metadata
- New vault subdirectory: `KB/Attachments/zotero/{slug}/_assets/`

### API contract

- POST inbox form action: extends to detect URI scheme; payload shape unchanged
- New POST endpoint for Zotero ingest, distinct from existing save-annotations / translate / scrape-translate routes; returns 303 redirect to inbox upon success
- Annotation API and Reader API surfaces unchanged

## Testing Decisions

### What makes a good test

- Tests verify **external behavior** through public interfaces, not implementation details (no patching of private functions, no asserting on internal cache state)
- Tests use real fixtures where possible: minimal SQLite seeded with sample items, real snapshot.html files committed under test fixtures
- Tests are deterministic — no live network, no live Zotero desktop, no time-dependent state
- Pure functions get table-driven tests (input → expected output)
- Fixtures are committed alongside the test, not generated at test time

### Modules with unit tests (strong coverage)

- **zotero_reader** — fixture is a small SQLite seeded with 4-5 items covering all branches: HTML snapshot only, PDF only, both attachments, metadata-only (no attachment), nested collection. Tests: URI parsing (valid / malformed / non-Zotero); item lookup (existing / missing); attachment selection logic (HTML wins over PDF, PDF fallback when HTML missing, sentinel when neither); SQLite copy-to-tmp lock handling.
- **zotero_assets** — fixture is a sample `_assets/` directory containing image and CSS files. Tests: copy correctness (file count, byte-equality), asset_map shape, idempotence on rerun, image path rewriting (markdown image syntax with relative paths → vault paths).
- **annotation_weave** — table-driven. Cases: single annotation, multiple annotations on same paragraph, annotation on multilingual segment, ref not found, highlight-only-no-annotation, unicode ref, ref containing markdown-special chars.
- **zotero_ingest** — fixture is a sample snapshot.html + a bilingual MD + an annotation set. Tests: two-file output filenames are correct; raw page content matches re-extraction (verifies isolation from inbox); annotated page content has callouts woven at correct positions; cross-link frontmatter present in both; raw `[[slug]]` resolves to raw page (not annotated).

### Modules with integration tests (light coverage)

- **zotero_sync** — single happy-path integration test using fixture SQLite + real Trafilatura, verifying full pipeline produces an IngestResult that inbox_writer can persist.
- **url_dispatcher** patch — one test case per URI scheme branch (https URL, `zotero://` URI, malformed input) verifying correct delegation.
- **inbox_writer** patch — golden test for Zotero frontmatter shape; one test case for `find_existing_for_zotero_item` reverse-lookup including the re-sync skip path.
- **Zotero ingest route** — single httpx end-to-end test from POST request to two files existing in KB Wiki Sources with frontmatter cross-links.

### Prior art for fixtures

- PubMed flow (PR #71) tests use sample PMC XML and PDF fixtures committed under `tests/fixtures/` — same pattern works for Zotero snapshot.html and `_assets/`
- inbox_writer already has frontmatter golden tests — extend with a Zotero variant
- annotation_store tests use fixture annotation files — reuse pattern for annotation_weave

## Out of Scope

- **Concept/entity extraction integration with annotated source page** (observation #3 真正解): MVP does not modify Robin's concept extraction. This is Phase 2 after修修 runs the Zotero MVP for 3-5 papers and decides how annotation should inform extraction.
- **Tag-driven batch sync**: MVP supports single-item paste only. Tag-based batch (Q7 alternative — Zotero `to-read-nakama` tag → sync many) is Phase 2.
- **Cron / daemon auto-sync**: MVP is manual paste-trigger only.
- **Inbox lifecycle policy**: Inbox files persist indefinitely after ingest until manually removed. Auto-archive (e.g., 30-day staleness) is Phase 2.
- **Force re-sync button**: Re-sync defaults to skip; explicit re-sync UI for forcing metadata refresh + re-extraction is Phase 2.
- **Zotero annotation 回沖到 KB**: 修修 annotates in nakama Reader, not in Zotero. Bidirectional sync is not needed for MVP.
- **Multi-machine sync**: 修修 has no Zotero cloud sync; single-machine assumption holds. Multi-machine support is not needed.
- **Immersive Translation Pro engine swap**: Translation engine stays self-built per Q9. Pro evaluation is a separate Phase 2 decision triggered only if单篇 paper translation quality complaints arise after MVP usage.
- **EPUB book translation軸**: Different grill window (PR #376 EPUB grill prep). MVP does not touch EPUB.
- **VPS deployment of sync agent**: Sync runs local-machine only; VPS receives MDs via Syncthing replication of vault.

## Further Notes

- This PRD covers three vertical slices intended to ship as a sequence:
  1. **Slice 1** — HTML happy path end-to-end (paste link → inbox MD → translate → annotate flow)
  2. **Slice 2** — PDF fallback (when HTML snapshot missing)
  3. **Slice 3** — Two-file ingest fan-out (raw + annotated source pages with cross-links)
- Downstream `to-issues` skill should break this PRD into per-slice issues
- All architectural decisions locked via ADR-018 + ADR-019; refer there for trade-off rationale
- Robin context glossary (Zotero terms) lives at `agents/robin/CONTEXT.md` — lazy-created during the grill; future grill sessions for Robin should append there
- The grill snapshot at `memory/claude/project_zotero_integration_grill_2026_05_05.md` is the authoritative pre-implementation reference — start each implementation session from there
- After MVP ships, run 3-5 real Zotero papers through the full flow before deciding whether to start Phase 2 (annotation-aware concept extraction) — observation #3 in CONTENT-PIPELINE.md is the critical follow-up gate
- The two-file ingest pattern (ADR-019) is intentionally generic — applies to future EPUB ingest軸 (PR #376) and other "修修 reads + annotates" source types; single-file ingest stays for batch automated cases (PubMed digest cron)
