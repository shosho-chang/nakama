# PRD — News Coo Inbox Importer

**Status**: Frozen (post-grill)
**Author**: Codex (initial draft) → revised by Claude after 2026-05-10 grill
**Created**: 2026-05-10
**Last revised**: 2026-05-10
**Stage anchor**: Stage 1 Discovery / import, feeding Stage 2 Reading Source and Stage 3 Source Promotion
**Related**: ADR-024 Source Promotion, #509 Reading Source Registry, #510 Reading Overlay V3, #511 Promotion Preflight, #512 Promotion Manifest
**Implementation path**: `extensions/news-coo/` in this Nakama monorepo (subtree-imported from original standalone `E:\news-coo` on 2026-05-10)
**Issue tracker**: This Nakama repo, label `area:news-coo`

---

## 0. Revision history

| Date | Change |
|------|--------|
| 2026-05-10 (AM) | Initial draft as `2026-05-10-toast-nakama-inbox-importer.md` (Codex). Assumed Toast fork + backend POST endpoint + bilingual format inside extension. |
| 2026-05-10 (PM) | Renamed to News Coo. Grill flipped scope: standalone extension named **News Coo**, FSA direct write, **no backend endpoint**, translation moved to Robin (separate workstream). Old filename preserved in git history (renamed via `git mv`). |

---

## 1. Background

Nakama's Line 2 reading workflow now treats ebooks, inbox documents, and web documents as the same downstream concept: a **Reading Source**. Web documents are not a separate `web_document` kind. They enter the system by being captured into `Inbox/kb/`, then #509 normalizes them as `inbox_document`.

The missing piece is the Stage 1 browser-side import path:

1. 修修 reads a useful web page in Chrome.
2. The browser extension extracts the main article/document content.
3. It writes a clean Markdown file (with downloaded images) into `Inbox/kb/` directly via File System Access API.
4. Robin's existing translator pipeline picks up unprocessed files (separate workstream) and writes the bilingual sibling.
5. Reader opens the imported file for highlight / annotation / reflection.
6. Later, Source Promotion decides whether that source deserves formal KB integration.

Earlier Zotero work was superseded because publisher/web HTML capture produced cleaner Markdown than PDF/Zotero-derived paths, but the pipeline lacked a robust "web page → clean Inbox/kb Reading Source" bridge. Manual Obsidian Web Clipper usage proved Defuddle quality wins, but Clipper-produced files have no automatic translation trigger and Clipper itself does not match Nakama's frontmatter / image-folder conventions.

News Coo is a small (~800-1000 LOC) standalone Chrome extension that solves exactly this gap: Defuddle extraction + Nakama-conventional frontmatter + image download + FSA write to vault. It explicitly does not handle translation or any LLM call — that remains Robin's job.

---

## 2. Goals

### V1 Goals

- One-click (and one-keystroke) Chrome extension flow that imports the current web page into the Nakama Obsidian vault under `Inbox/kb/{slug}.md`.
- Download referenced images into `KB/Attachments/web/{slug}/` mirroring the existing `shared/image_fetcher.py` convention; rewrite markdown image refs to vault-relative paths.
- Produce a clean **original/evidence track** (no nav, no sidebar, no ads, no recommendation widgets, no cookie banners, no comments).
- Emit frontmatter compatible with #509 Reading Source Registry and #511 Promotion Preflight.
- Preserve enough provenance for Source Promotion: canonical URL, captured time, title, author/site, language, extraction method, source relationship.
- Allow selection-aware clipping (only the selected text becomes the file content) and pre-clip highlight collection (highlights flow into frontmatter as annotation seeds for ADR-021).
- Provide both **Preview mode** (popup with editable metadata) and **Quick mode** (one-keystroke save with no preview).
- Detect specific high-value source types (V1: PubMed) to add domain-specific frontmatter (`doi`, `pmid`).

### Non-Goals

- No translation (Robin owns this; News Coo never invokes an LLM).
- No bilingual file generation.
- No Reader rendering (Robin owns the Reader surface).
- No POST to a Nakama backend endpoint — direct FSA write only.
- No KB promotion or `KB/Wiki/...` writes.
- No Concept / Entity extraction.
- No Promotion Manifest creation or commit.
- No multi-vault support (one vault per browser profile).
- No Firefox / Safari (Chrome MV3 only V1).
- No revival of Zotero as primary ingest path.
- No raw full-DOM dump as the canonical import artifact.
- No long copyrighted full-text publication outside the private vault.

---

## 3. Frozen Decisions

### D1. Web documents land as Inbox documents

Web pages captured by News Coo become files under `Inbox/kb/`. Downstream they are `ReadingSource.kind == "inbox_document"` rather than a separate kind.

### D2. News Coo is a thin delivery extension

News Coo owns: extraction, frontmatter, slug, dedup, image download, FSA write. It does **not** own translation, bilingual format, glossary, LLM invocation, Reader rendering, or any post-import processing.

### D3. Direct FSA write — no backend endpoint

News Coo writes to the local Obsidian vault via the File System Access API. No HTTP call to Nakama is made. The user picks the vault root once at install/options time; News Coo persists the directory handle in IndexedDB and re-validates on each launch. If the handle is invalidated (revoked permission, vault path changed), News Coo prompts the user to re-pick.

### D4. Track split follows #509

The importer produces:

- `Inbox/kb/{slug}.md` — original/evidence track (the only file News Coo writes).
- `Inbox/kb/{slug}-bilingual.md` — bilingual/display track, **created later by Robin's translator pipeline** (separate PR / workstream, not News Coo).

When both exist, #509 canonicalizes them as the same logical source.

### D5. Bilingual-only imports are NOT a News Coo case

News Coo always writes the original. The bilingual sibling appears later when Robin processes the file. `Flow C` from the original PRD draft (bilingual-only imports) is no longer a News Coo case — it can only arise from manual / external sources.

### D6. Stage 1 import must not bypass Stage 3 safeguards

News Coo may help capture and read sources. It must not decide that a source is KB-grade, write formal Concept pages, or commit claims into KB. Those decisions belong to Source Promotion and review.

### D7. Image conventions mirror existing Nakama infrastructure

News Coo mirrors `shared/image_fetcher.py`:

- Path: `KB/Attachments/web/{slug}/img-N.{ext}`
- Hard limit: 20 MB per image
- Timeout: 15 s per fetch
- Failed fetches: keep remote URL inline (don't break markdown), set `images_partial: true` in frontmatter
- Supported formats: jpg, png, webp, gif, svg, tiff, bmp, avif

This requires the user's vault root permission to cover both `Inbox/` and `KB/`. The picker prompt will be the vault root, not `Inbox/kb/` directly.

### D8. Robin's auto-translate trigger is out of News Coo scope

The "watcher / endpoint that detects new `Inbox/kb/*.md` without `-bilingual` sibling and runs `translator.py`" is a separate Robin enhancement, with its own grill / PR. News Coo MVP can ship without it; users see only the original file initially, the bilingual sibling appears once Robin gets the wire-up. Reader still works for the original.

---

## 4. User Workflows

### Flow A — Quick Clip (default keyboard / context menu)

1. 修修 opens a web page.
2. Either:
   - Presses `Alt+Shift+Q` (quick clip shortcut), or
   - Right-clicks anywhere on page → `News Coo: Clip page`.
3. News Coo extracts (Defuddle), downloads images, generates frontmatter + slug, writes file.
4. Toast notification shows `✓ Saved Inbox/kb/{slug}.md`.
5. No popup is opened. No confirmation needed.

### Flow B — Preview Clip (toolbar icon)

1. 修修 opens a web page.
2. Clicks News Coo toolbar icon (or `Alt+Shift+N`).
3. Popup opens showing: title (editable), author, site, word count, image count, slug preview, dedup warning if applicable.
4. 修修 confirms or edits → `Save` button.
5. Same write as Flow A; popup shows result; auto-closes after 2 s.

### Flow C — Selection Clip

1. 修修 selects text on the page (`window.getSelection()` is non-empty).
2. Either keyboard / context menu / popup `Save` triggers selection-aware extraction.
3. Only selected content is processed (Defuddle is given the selection's HTML, not full document).
4. Frontmatter records `extraction_method: selection`, `selection_only: true`.
5. Otherwise identical to Flow A/B.

### Flow D — Highlights Seed

1. While reading, 修修 selects text passages and presses a "mark" shortcut (TBD in S6 implementation; possibly `Alt+Shift+M`).
2. Each marked passage is recorded in `chrome.storage.session` keyed by tab.
3. When a clip is finally triggered (any flow), accumulated highlights for the tab are included in the file: frontmatter `highlights: [{text, offset}]` + body section `## Highlights` with quote blocks.
4. Highlights are then cleared for that tab.
5. Robin's annotation pipeline (ADR-021) can later promote these highlights into formal annotations.

### Flow E — Duplicate URL

1. 修修 imports a URL whose slug `{slug}.md` already exists under `Inbox/kb/`.
2. **Preview mode**: popup shows "已存在 (captured at {time})" with three buttons: `Open existing` (opens vault file via `obsidian://` URI for now, TBD), `Overwrite`, `Save as {slug}-2.md`.
3. **Quick mode**: silently saves as `{slug}-2.md` (auto-increment), notification mentions the suffix.
4. Canonical URL collision across different slugs is **not detected by News Coo** (Robin owns vault-wide dedup awareness in its own time).

---

## 5. File Contract

### 5.1 Naming

```text
Inbox/kb/{slug}.md
KB/Attachments/web/{slug}/img-1.{ext}
KB/Attachments/web/{slug}/img-2.{ext}
...
```

Slug rules:

- Source: page title (preferred) or canonical URL hostname + path.
- Lowercase, replace non-word characters with `-`, collapse runs of `-`, trim leading/trailing `-`.
- Preserve CJK characters verbatim.
- Maximum 80 characters.
- Collision: append `-2`, `-3`, ... until free.

### 5.2 Frontmatter (original — the only file News Coo writes)

Required:

```yaml
---
title: Example Article Title
source_url: https://example.com/article
canonical_url: https://example.com/article
captured_at: 2026-05-10T14:32:18+08:00
source_type: web_document
stage: 1
lang: en
extraction_method: defuddle
news_coo_version: 1
---
```

Strongly preferred when available from Defuddle:

```yaml
site_name: Example
author: Author Name
published: 2026-05-01
description: Short description from meta tag.
word_count: 1842
favicon: https://example.com/favicon.ico
```

News Coo–specific extensions:

```yaml
selection_only: false
highlights:
  - text: "Selected passage"
    offset: 1024
images_partial: false
images_count: 3
```

PubMed detector adds:

```yaml
doi: 10.1001/jama.2024.12345
pmid: 38765432
journal: JAMA
```

Notes:

- `source_type: web_document` is metadata only. It is not a #509 `ReadingSource.kind`.
- Unknown metadata is omitted, never hallucinated.
- Image refs in body markdown use vault-relative paths: `![alt](KB/Attachments/web/{slug}/img-1.jpg)`.

### 5.3 Bilingual frontmatter (NOT News Coo's responsibility)

When Robin's translator pipeline later writes `Inbox/kb/{slug}-bilingual.md`, frontmatter shape per existing `translator.py` convention. News Coo never writes this file.

---

## 6. Content Contract

The original Markdown file should preserve:

- Article/document heading hierarchy.
- Paragraphs, lists, tables, code blocks, math / chemical notation.
- Image references with alt text (rewritten to vault-relative paths after image download).
- Citation/reference sections when present in main content.

It should exclude (Defuddle handles this):

- Navigation, sidebars, footers, headers.
- Ads, share widgets, newsletter signups, cookie banners.
- Comments, recommended articles, related links.
- Hidden / aria-hidden elements.

Selection mode (Flow C) follows the same rules but applied to the selection subtree only.

Highlights (Flow D) are appended at end of body in a `## Highlights` section as quote blocks, in selection order.

---

## 7. Integration Contract

### With #509 Reading Source Registry

News Coo files must satisfy `ReadingSource.kind == "inbox_document"` resolution:

- File path: `Inbox/kb/{slug}.md`.
- Frontmatter has `source_type: web_document`, `stage: 1`, `lang`, `captured_at`.
- One variant when bilingual sibling absent: `has_evidence_track=True`, single `original` variant.
- When Robin later writes `{slug}-bilingual.md`: two variants (original + display), `has_evidence_track=True`.

### With #510 Reading Overlay

Reader annotation keys follow the bilingual sibling collapse rule (Robin's territory, not News Coo's).

### With #511 Promotion Preflight

Preflight inspects the imported source as a normal `ReadingSource` with variants and evidence flags. News Coo–specific fields (`extraction_method`, `news_coo_version`, `selection_only`, `highlights`) are passive metadata; preflight does not require knowledge of them.

### With ADR-021 Annotation Substance Store

`highlights[]` in frontmatter is a Robin-readable seed for annotation promotion. Robin (not News Coo) decides whether to materialize each highlight as a formal annotation.

### With #513-#516 Source Promotion

News Coo writes nothing under `KB/Wiki/`. Source Map Builder, Concept Promotion, Commit Gate, and Review UI own all formal KB changes.

### With Robin auto-translate (future, separate workstream)

News Coo writes the original. Robin (when wired) detects unprocessed files and writes the bilingual sibling. The contract between the two is: News Coo guarantees a vault file with valid frontmatter and a clean main-content body; Robin reads that file, translates, and writes a sibling. No direct IPC.

---

## 8. Architecture (Frozen)

### Repo

`E:\news-coo` (standalone, local-only for V1).

### Stack

- Chrome MV3
- TypeScript strict, Rolldown, Vitest + happy-dom, ESLint typescript-eslint recommended-type-checked
- Defuddle ^0.18.1 (npm dep)
- File System Access API for vault writes
- IndexedDB for FSA handle persistence

### Permissions

```json
{
  "permissions": ["activeTab", "storage", "contextMenus"],
  "host_permissions": ["<all_urls>"]
}
```

### Surfaces

- Toolbar action → popup (Preview mode default)
- `Alt+Shift+N` → opens popup (same as toolbar)
- `Alt+Shift+Q` → Quick mode (no popup)
- Right-click context menu → "News Coo: Clip page" / "News Coo: Clip selection"
- Options page → vault picker, handle status, re-pick, default mode (preview/quick) toggle

### Data flow

```
content script (Defuddle, image references collected)
    ↕ message passing
service worker (image fetch via fetch(), CORS-permitted)
    ↕ message passing
popup or background (assembles frontmatter, writes via FSA)
```

### Code reuse

- Defuddle: npm dep, no vendoring.
- ~100 LOC adapted from Obsidian Web Clipper `src/content.ts:199-296` (Defuddle wrap + shadow DOM flatten + URL normalization). MIT attribution in `NOTICE`.
- ~700 LOC new TypeScript.

---

## 9. Implementation Slices

### S1 — Skeleton ✅ Done 2026-05-10

Repo init at `E:\news-coo`, MV3 manifest, rolldown + vitest + ESLint scaffolding, popup / options / sw / content stubs, LICENSE + NOTICE + README. `npm run check / build / test / lint` all clean.

### S2 — Extraction (Defuddle wrapper + content-script wiring + PubMed detector)

- Add Defuddle to content script with shadow-DOM flatten + URL normalization (adapt from Clipper, MIT attribution).
- Define EXTRACT message: popup/background → content → returns `{markdown, metadata, imageRefs[]}`.
- Add PubMed site detector (URL host match `pubmed.ncbi.nlm.nih.gov` or `ncbi.nlm.nih.gov/pmc/`) → extract `doi`, `pmid`, `journal`.
- Tests: extraction wrapper, PubMed detector, message routing.

### S3 — FSA writer (vault picker, frontmatter, slug, dedup)

- Options-page vault picker using `showDirectoryPicker()`.
- IndexedDB persistence for `FileSystemDirectoryHandle`; verify on each launch.
- Slug generator (per §5.1).
- Frontmatter generator (per §5.2).
- Dedup: `getFileHandle({create:false})` to test existence; preview mode prompts, quick mode auto-suffix.
- Write `Inbox/kb/{slug}.md`.
- Tests: slug, frontmatter, dedup logic, mocked FSA writer.

### S4 — Image fetcher

- Mirror `shared/image_fetcher.py` behavior (limits, timeouts, format support, failure handling).
- Fetch images from content script (host_permissions covers all URLs); fall back to remote URL on CORS / timeout failure.
- Write to `KB/Attachments/web/{slug}/img-N.{ext}`.
- Rewrite markdown body image references to vault-relative paths.
- Set `images_partial: true` if any failed; `images_count` always.
- Tests: extension detection, path generation, failure fallback (mocked fetch).

### S5 — UX surfaces (popup preview, quick mode, context menu, kbd shortcut)

- Popup preview UI: title (editable input) + author + site + word count + image count + slug preview + dedup warning + Save button.
- Quick mode: triggered by `Alt+Shift+Q` or context menu, no popup, toast notification on success.
- Context menu registration in service worker (page-action vs selection variant).
- Toast notification mechanism (in-page or chrome.notifications).
- Tests: popup state machine, quick-mode flow.

### S6 — Selection-aware clipping + highlights seed

- Detect `window.getSelection()` non-empty in content script; pass selection HTML to Defuddle wrapper.
- "Mark" shortcut to capture highlights into `chrome.storage.session` per tab.
- Include accumulated highlights in clipped file (frontmatter + `## Highlights` body section).
- Clear highlights on successful save.
- Tests: selection serialization, highlight accumulator state.

### S7 — Polish

- i18n (en + zh-TW) for popup and options strings; TS const dictionaries.
- Error states: extraction failure, FSA permission revoked, image-fetch CORS failure rendered cleanly.
- Test coverage: aim for >80% on `src/extract/`, `src/vault/`, `src/shared/`.
- README install instructions for unpacked extension load.

Each slice is one issue (S2 → S7 = 6 issues). Open in Nakama repo with label `area:news-coo`. PR for each slice merges directly to News Coo `main`. News Coo doesn't yet have a GitHub remote — issues live in Nakama; code is local-only V1.

---

## 10. MVP Acceptance

### Functional

- Quick clip via `Alt+Shift+Q` writes `Inbox/kb/{slug}.md` and downloads images to `KB/Attachments/web/{slug}/`.
- Preview clip via popup shows accurate metadata and respects user edits.
- Selection clip writes selection-only content with `selection_only: true`.
- Highlights flow into a saved file's frontmatter when accumulated.
- Duplicate URL handled per Flow E without overwrite by default in quick mode.
- Robin Reader (existing) opens the resulting file correctly.
- #509 resolves the imported file as `inbox_document`.
- #511 preflight runs without News Coo–specific code.

### Quality

- Defuddle output excludes navigation, sidebar, ads, recommendations, cookie banners, footer.
- Frontmatter contains all required fields per §5.2.
- Slug is deterministic and collision-safe.
- Image fetch failures degrade gracefully to remote URL (file is still useful).
- Vault writes never land outside `Inbox/kb/` or `KB/Attachments/web/`.

### Test fixtures

Five fixture pages stored as `tests/fixtures/*.html`:

1. Clean blog article (zero ads, structured headings).
2. News article with ads, related links, comments.
3. Scientific publisher article (Nature / NEJM / similar) with abstract, numbered sections, references, math.
4. Page with tables and inline math (medical journal).
5. Hostile page: heavy sidebars, cookie banner, newsletter prompt, comments, share widgets.

---

## 11. Risks

- **FSA permission revocation**: User clears site data → handle invalidated. Mitigation: explicit re-pick prompt with friendly message.
- **CORS-blocked images**: Some sites block cross-origin image fetches. Mitigation: graceful fallback to remote URL + `images_partial: true` flag.
- **Defuddle quality regression**: Upstream Defuddle changes could break extraction. Mitigation: pin minor version in package.json; fixture tests catch regressions.
- **Filename collisions across canonical URLs**: Different URLs producing same slug. Mitigation: canonical URL is preferred slug source; auto-suffix on collision.
- **Vault path differences across machines**: News Coo only knows the picker-selected root; not portable across machines. Acceptable for V1 (single-machine personal tool).
- **Copyright**: Full-text capture stays in private vault. No re-publication path exists in News Coo.

---

## 12. Out of scope (record of explicitly rejected ideas)

- Backend POST to Nakama (rejected: violates standalone scope, adds endpoint engineering).
- Translation in-extension (rejected: Robin owns it, glossary already in backend).
- Bilingual file generation (rejected: same).
- Reader rendering (rejected: Robin owns Reader).
- Side panel UI (deferred to V2; popup sufficient for V1).
- Multi-vault support (deferred to V2).
- Firefox / Safari support (deferred to V2).
- YouTube transcript import (deferred to V2; goes through yt-dlp on backend).
- Per-site templates beyond PubMed (deferred to V2; PubMed is the highest-value daily case).
- Highlight SVG overlay on live page (rejected: simplified version stores in chrome.storage without DOM injection).
- History log / clip count statistics (rejected: vault file existence is source of truth).

---

## 13. Definition Of Done (PRD-level)

- This PRD is merged to Nakama main.
- 6 issues filed in Nakama with label `area:news-coo`, one per slice (S2-S7), each with: scope, files affected, DoD, test plan.
- News Coo S1 skeleton is committed in `E:\news-coo` with passing `check / build / lint / test`.
