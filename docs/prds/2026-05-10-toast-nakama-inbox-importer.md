# PRD Draft — Toast -> Nakama Inbox Importer

**Status**: Draft for grill-with-docs  
**Author**: Codex  
**Created**: 2026-05-10  
**Stage anchor**: Stage 1 Discovery / import, feeding Stage 2 Reading Source and Stage 3 Source Promotion  
**Related**: ADR-024 Source Promotion, #509 Reading Source Registry, #510 Reading Overlay V3, #511 Promotion Preflight, #512 Promotion Manifest

---

## 1. Background

Nakama's Line 2 reading workflow now treats ebooks, inbox documents, and web documents as the same downstream concept: a **Reading Source**. Web documents are not a separate `web_document` kind. They enter the system by being captured into `Inbox/kb/`, then #509 normalizes them as `inbox_document`.

The missing piece is the Stage 1 browser-side import path:

1. 修修 reads a useful web page in Chrome.
2. The browser extension extracts the main article/document content.
3. It produces clean Markdown files in `Inbox/kb/`.
4. Reader opens the imported file for highlight / annotation / reflection.
5. Later, Source Promotion decides whether that source deserves formal KB integration.

Earlier Zotero work was superseded because publisher/web HTML capture produced cleaner Markdown than PDF/Zotero-derived paths, but the pipeline lacked a robust "web page -> clean Inbox/kb Reading Source" bridge. The current direction is to use **Toast** as the primary extension/controller while borrowing Obsidian Clipper / Defuddle-style main-content extraction and Markdown formatting.

This PRD is intentionally a **grill prep draft**. It captures current decisions and open questions before implementation.

---

## 2. Goals

### V1 Goals

- Provide a one-click browser workflow that imports the current web page into Nakama's `Inbox/kb/` as Reader-ready Markdown.
- Produce a clean **original/evidence track** when possible.
- Produce a clean **bilingual/display track** when requested.
- Reuse/adapt proven main-content extraction so imports exclude navigation, sidebars, ads, cookie banners, recommended articles, forms, and other page chrome.
- Emit frontmatter compatible with #509 Reading Source Registry and #511 Promotion Preflight.
- Preserve enough provenance for future Source Promotion: canonical URL, captured time, title, author/site, language, extraction method, and source relationship.
- Keep the Reader and Source Promotion pipeline downstream of `Inbox/kb/`; the plugin must not write formal KB pages directly.

### Non-Goals

- No direct KB promotion.
- No `KB/Wiki/Sources/...` writes.
- No Concept / Entity extraction.
- No Promotion Manifest creation or commit.
- No Review UI.
- No replacement for ebook import.
- No revival of Zotero as the primary ingest path.
- No raw full-DOM dump as the canonical import artifact.
- No long copyrighted full-text publication outside the private vault.

---

## 3. Current Decisions

### D1. Web documents land as Inbox documents

Web pages captured by the browser extension become files under `Inbox/kb/`. Downstream they are `ReadingSource.kind == "inbox_document"` rather than a separate kind.

### D2. Toast is the controller; extraction quality is delegated/reused

Toast should own the user-facing extension flow: translate/import/open Reader. It should not rely on naive rendered-DOM capture. It should reuse or adapt Obsidian Clipper / Defuddle / Readability-style main-content extraction.

### D3. Track split follows #509

The importer should prefer two files:

- `slug.md` — original/evidence track.
- `slug-bilingual.md` — bilingual/display track.

When both exist, #509 canonicalizes them as the same logical source. The bilingual file is the user-facing Reader file; the original file is the factual evidence track.

### D4. Bilingual-only is allowed but lower confidence

If the importer can only produce `slug-bilingual.md`, #509 and #511 must treat this as `has_evidence_track=False` with `evidence_reason="bilingual_only_inbox"`. Source Promotion then defaults to conservative actions such as `defer` or `annotation_only_sync`.

### D5. Stage 1 import must not bypass Stage 3 safeguards

The plugin may help capture and read sources. It must not decide that a source is KB-grade, write formal Concept pages, or commit claims into KB. Those decisions belong to Source Promotion and review.

---

## 4. User Workflows

### Flow A — Import Original Only

1. 修修 opens a web page.
2. Toast extracts the main readable content.
3. Toast writes `Inbox/kb/{slug}.md`.
4. Toast optionally opens Nakama Reader for that file.
5. Reader supports highlights / annotations / reflections.
6. Later #511/#513/#514 decide whether to promote.

Use when the page is already Chinese, or when 修修 only wants the original source first.

### Flow B — Import Original + Bilingual

1. 修修 opens an English page.
2. Toast extracts the main readable content as original Markdown.
3. Toast translates/formats a bilingual display track.
4. Toast writes:
   - `Inbox/kb/{slug}.md`
   - `Inbox/kb/{slug}-bilingual.md`
5. Reader lists one logical source and opens the bilingual display file.
6. Annotations attach to the canonical annotation key from the bilingual sibling.
7. Source Promotion uses the original file as evidence.

This is the target happy path.

### Flow C — Bilingual Only

1. Toast cannot or does not preserve original clean Markdown.
2. Toast writes only `Inbox/kb/{slug}-bilingual.md`.
3. Reader can still be used.
4. #511 preflight marks the source as missing evidence track.
5. Promotion defaults to conservative behavior.

This is acceptable for reading convenience but not ideal for factual KB integration.

### Flow D — Duplicate Import

1. 修修 imports a URL already present in `Inbox/kb/`.
2. The importer detects the existing source by canonical URL or normalized slug.
3. It offers one of:
   - open existing Reader item;
   - refresh bilingual track;
   - create a versioned duplicate.

The exact duplicate policy is an open grill question.

---

## 5. File Contract

### 5.1 Naming

Preferred shape:

```text
Inbox/kb/{slug}.md
Inbox/kb/{slug}-bilingual.md
```

Rules:

- `slug` should be deterministic from title/canonical URL with collision handling.
- The bilingual file must use the `-bilingual.md` suffix.
- If both files exist, they represent one logical Reading Source.
- If only the bilingual file exists, the logical original path may not exist; downstream must use `ReadingSource.variants[*].path`, never parse `source_id` as a real path.

### 5.2 Original Frontmatter

Required or strongly preferred:

```yaml
---
title: Example Article Title
source_url: https://example.com/article
canonical_url: https://example.com/article
captured_at: 2026-05-10T00:00:00+08:00
source_type: web_document
stage: 1
lang: en
site_name: Example
author: Author Name
published: 2026-05-01
extraction_method: defuddle
toast_import_version: 1
---
```

Notes:

- `source_type: web_document` is metadata only. It is not a #509 `ReadingSource.kind`.
- `lang` should describe the original content language when known.
- `canonical_url` should be preferred over the tab URL when available.
- Unknown metadata should be omitted rather than hallucinated.

### 5.3 Bilingual Frontmatter

Required or strongly preferred:

```yaml
---
title: Example Article Title
source_url: https://example.com/article
canonical_url: https://example.com/article
captured_at: 2026-05-10T00:00:00+08:00
source_type: web_document
stage: 1
lang: bilingual
bilingual: true
derived_from: Inbox/kb/example-article-title.md
site_name: Example
author: Author Name
published: 2026-05-01
extraction_method: defuddle
translation_method: toast
toast_import_version: 1
---
```

Notes:

- `derived_from` must point to the original file when it exists.
- If original is unavailable, `derived_from` should be omitted or point to the logical source only if #509/#511 explicitly accept that convention.
- Bilingual display text is not the factual evidence track.

---

## 6. Content Contract

### Original Markdown

The original file should preserve:

- article/document heading structure;
- paragraphs;
- lists;
- tables when possible;
- code blocks when present;
- math / chemical notation when possible;
- useful image references or image placeholders;
- citation/reference sections when they are part of the main content.

It should exclude:

- navigation;
- cookie banners;
- login prompts;
- newsletter signups;
- ads;
- sidebars;
- related/recommended articles;
- comments;
- share widgets;
- footer boilerplate.

### Bilingual Markdown

The bilingual file should preserve the same section structure as the original, with translated display content.

Open design questions:

- Whether to interleave original + translation paragraph by paragraph.
- Whether to display translation only and rely on `derived_from` for evidence.
- Whether to reuse the existing `translate_document()` bilingual format.
- How to keep annotation anchors stable across original and bilingual files.

---

## 7. Integration Contract

### With #509 Reading Source Registry

The importer must create files that #509 can resolve as an `InboxKey`:

- original only -> one original variant, `has_evidence_track=True`;
- original + bilingual -> original + display variants, `has_evidence_track=True`;
- bilingual only -> one display variant, `has_evidence_track=False`, `evidence_reason="bilingual_only_inbox"`.

### With #510 Reading Overlay

Reader annotation keys should follow the bilingual sibling collapse rule. If both `slug.md` and `slug-bilingual.md` exist, the user-facing bilingual sibling defines the annotation key.

### With #511 Promotion Preflight

Preflight must be able to inspect the imported source without special web-document code. It should see a normal `ReadingSource` with variants and evidence flags.

### With #513-#516 Source Promotion

The importer must not write promotion outputs. It only prepares source material. Source Map Builder, Concept Promotion, Commit Gate, and Review UI own formal KB changes.

---

## 8. Architecture Options To Grill

### Option A — Extension writes files directly to vault

The Chrome extension writes `.md` files directly into the local vault path.

Pros:

- Lowest backend work.
- Works offline if translation/extraction is local or extension-provided.
- Simple mental model.

Cons:

- Browser extension filesystem access is constrained.
- Vault path differs across machines.
- Harder to validate file contract centrally.
- Harder to support VPS/browser separation.

### Option B — Extension POSTs to local Nakama API

The extension sends extracted content and metadata to a local Thousand Sunny endpoint. Nakama writes `Inbox/kb/`.

Pros:

- Centralized validation.
- Easier tests.
- Can reuse Nakama config for vault path.
- Can enforce #509-compatible frontmatter and naming.
- Better audit log / duplicate detection.

Cons:

- Requires local server running.
- Needs auth / CORS / extension permissions.
- More backend work.

### Option C — Extension exports Markdown, user/Obsidian handles save

The plugin creates Markdown and hands it to Obsidian or downloads it.

Pros:

- Simple and safe.
- Minimal permissions.

Cons:

- More manual friction.
- Harder to guarantee exact `Inbox/kb/` placement and sibling pairing.
- Less useful as a reliable Nakama pipeline.

### Current Lean

Lean toward **Option B** for Nakama integration, with an MVP fallback that can export Markdown for manual inspection. Grill should validate whether the extra backend endpoint is worth it for V1.

---

## 9. Extraction And Translation Strategy

### Extraction

Candidates:

- Defuddle-style extraction from Obsidian Web Clipper.
- Mozilla Readability-style extraction.
- Existing Toast DOM pipeline plus main-content extraction adapter.

Acceptance requirement:

- Raw rendered DOM capture is not enough.
- Extraction must be tested against multiple page types and must visibly exclude page chrome.

### Translation

Candidates:

- Toast's existing translation flow.
- Nakama backend `translate_document()` / glossary flow.
- A hybrid: Toast extracts and sends original Markdown to Nakama for translation.

Open questions:

- Where API keys live.
- Whether translation should use Nakama's Taiwan terminology glossary.
- Whether cost logging belongs to Nakama backend.
- How failures surface in the extension UI.
- Whether bilingual formatting should be generated by backend or extension.

---

## 10. MVP Acceptance

### Functional Acceptance

- Import a normal article into `Inbox/kb/{slug}.md`.
- Import the same article as original + bilingual sibling.
- Reader can open the imported source.
- Reader list collapses original + bilingual siblings into one logical item.
- Highlight / annotation / reflection save works.
- #509 resolves the imported files correctly.
- #511 preflight can run on the resulting `ReadingSource`.
- Duplicate import does not silently overwrite user annotations.

### Quality Acceptance

- Main content extraction excludes navigation, sidebar, ads, recommendation widgets, cookie banners, and footer boilerplate.
- Original and bilingual files preserve heading hierarchy.
- Frontmatter contains enough provenance for future audit.
- Bilingual-only imports are explicitly marked as missing evidence track downstream.

### Test Fixtures

Use at least five fixture pages:

1. A clean blog article.
2. A news article with ads and related links.
3. A scientific/publisher article with abstract and numbered sections.
4. A page with tables / math / chemical notation.
5. A hostile page with sidebars, cookie banner, newsletter prompt, and comments.

---

## 11. Proposed Implementation Slices

### Slice 1 — Grill + PRD Freeze

- Run grill-with-docs against this PRD.
- Freeze decisions:
  - vault direct vs Nakama API;
  - extraction engine;
  - translation location;
  - duplicate policy;
  - frontmatter contract;
  - image handling;
  - snapshot retention.

### Slice 2 — Backend Import Endpoint Or File Contract Validator

If Option B wins:

- Add a local authenticated endpoint that accepts extracted original/bilingual Markdown and writes `Inbox/kb/`.
- Validate frontmatter and sibling pairing.
- Return Reader URL.

If Option A/C wins:

- Add a deterministic validator script/test for plugin-generated files.

### Slice 3 — Toast Fork Adapter

- Implement extraction + import action in Toast fork.
- Add settings for Nakama endpoint or vault path.
- Add import status UI.
- Add failure messages and retry behavior.

### Slice 4 — Fixture-Based Extraction Tests

- Add local HTML fixtures.
- Assert output excludes chrome and preserves headings.
- Assert frontmatter contract.

### Slice 5 — Manual Smoke + Reader Integration

- Import 3-5 real pages.
- Open Reader.
- Save annotations.
- Run #509/#511 checks.
- Record follow-ups.

---

## 12. Grill Questions

1. Should V1 write directly to vault, POST to Nakama, or support both?
2. Should translation run in Toast or Nakama backend?
3. Is `slug.md` + `slug-bilingual.md` mandatory for high-quality import, or is bilingual-only acceptable as common V1?
4. Do we store raw HTML snapshots? If yes, where?
5. How should images be handled: remote URLs, local attachments, or placeholders?
6. What is the duplicate policy for same canonical URL?
7. Should import immediately open Reader?
8. What minimum metadata is required before the importer refuses to write?
9. How do we avoid capturing translated DOM injected by other browser extensions?
10. What failure state should the user see if extraction succeeds but translation fails?
11. Should scientific tables be preserved as Markdown, HTML, or deferred?
12. Is this plugin personal-only, or should it be structured for eventual public release?
13. Should Toast import support "original only", "bilingual only", and "both" as explicit modes?
14. Does Nakama need an Inbox import log for audit/debug?
15. What is the smallest useful MVP that lets 修修 read real articles this week?

---

## 13. Open Risks

- Browser extension permissions may make direct vault write impractical.
- Translation output format may drift from existing Reader expectations.
- Main-content extraction quality may vary widely by site.
- Bilingual-only convenience may accidentally be treated as factual evidence unless #511 policy remains strict.
- Remote images may break later or leak browsing context.
- Full-text copyright boundaries must remain private-vault only.
- If the plugin bypasses Nakama validation, future agents may receive malformed `Inbox/kb/` files.

---

## 14. Definition Of Done For This PRD

- This file is merged to main.
- A grill-with-docs session is run against it.
- Grill output either updates this PRD or creates a follow-up implementation brief.
- A GitHub issue is opened for the first implementation slice only after decisions are frozen.

