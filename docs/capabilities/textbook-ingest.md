# Capability Card — `textbook-ingest`

**Status:** Phase 1 (Claude Code skill scaffold) — live at
`.claude/skills/textbook-ingest/`
**License:** MIT (planned open-source extraction)
**Scope:** Whole-book PDF → Robin KB Wiki ingest. Produces per-chapter
Source pages (section-by-section), a Book Entity index page, and
appends `mentioned_in:` backlinks on shared Concept / Entity wiki pages
(Karpathy-style cross-source wiki). Driven interactively in Claude Code
on Mac, using Opus 4.7's 1M context window — no map-reduce, no
embedding pipeline.

---

## Capability

Given a book file path (`.epub` preferred / `.pdf` fallback) and optional
metadata overrides, the skill:

1. Calls `parse_book.py` to extract chapter outline. **EPUB is the
   primary path** — OPF metadata + nav TOC + spine order give 100%
   authoritative chapter boundaries. PDF is the fallback chain (PDF
   outline → manual TOC YAML → regex). Optionally exports per-chapter
   text to `/tmp/textbook-chapters/` for the LLM to Read.
2. Confirms the chapter list with the user (mandatory hand-off).
3. Loops one Claude Code turn per chapter, reading the chapter into
   Opus 1M context, generating a section-by-section Source Summary,
   and extracting cross-source Concept / Entity candidates.
4. Writes chapter Source pages (`KB/Wiki/Sources/Books/{book_id}/ch{n}.md`)
   plus creates / updates Concept and Entity pages in the shared
   `KB/Wiki/Concepts/` / `KB/Wiki/Entities/` pools, appending
   `mentioned_in:` backlinks.
5. Builds the Book Entity index page (`KB/Wiki/Entities/Books/{book_id}.md`)
   listing every chapter as wikilinks, top concepts, top entities, and
   cross-book references.
6. Reports completion + token cost; instructs the user to wait for
   Obsidian Sync to propagate the vault to the VPS where Chopper
   `kb-search` will pick up the new pages.

The skill itself does no embedding, no vector store, no model serving —
all heavy lifting is Opus 4.7 in-context per chapter. Retrieval at
query-time is delegated to the existing `kb-search` skill (which uses
LLM-based ranking + symbolic backlink expansion, not vector similarity).

## Input / Output Contract

**Input** — interactive Claude Code prompt:

```
ingest 這本教科書 /Users/shosho/Books/harrison-21e.pdf
（or "textbook ingest <path>"）

Optional overrides via natural language:
  - book_id: harrison-internal-medicine-21e
  - book_subtype: textbook_pro|textbook_exam|popular_health|clinical_protocol|reference
  - language: en|zh-TW|zh-CN
```

**Outputs** — three artefact types written directly to vault:

### Book Entity (`KB/Wiki/Entities/Books/{book_id}.md`)

````markdown
---
type: book
schema_version: 1
book_id: harrison-internal-medicine-21e
title: "Harrison's Principles of Internal Medicine"
authors: ["Loscalzo J", "Fauci AS", ...]
isbn: "9781265060190"
edition: "21st"
pub_year: 2022
publisher: "McGraw-Hill"
language: en
book_subtype: textbook_pro
chapter_count: 30
ingested_at: 2026-04-25
ingested_by: "claude-code-opus-4.7"
status: complete
---

# Harrison's Principles of Internal Medicine (21st)
…body lists chapters, top concepts, cross-book refs…
````

### Chapter Source (`KB/Wiki/Sources/Books/{book_id}/ch{n}.md`)

````markdown
---
type: book_chapter
schema_version: 1
source_type: book
content_nature: textbook
lang: en
book_id: harrison-internal-medicine-21e
chapter_index: 3
chapter_title: "Cardiovascular Examination"
section_anchors: ["3.1 Inspection", "3.2 Auscultation", ...]
page_range: "142-187"
ingested_at: 2026-04-25
---

# Chapter 3 — Cardiovascular Examination

## 3.1 Inspection
（section-by-section summary, 2-3 paragraphs each）
````

### Concept page (`KB/Wiki/Concepts/{slug}.md`) — created or appended

````markdown
---
title: Beta Blocker
type: concept
domain: cardiovascular
mentioned_in:
  - "[[Sources/Books/harrison-21e/ch5]]"
  - "[[Sources/pubmed-12345]]"
  - "[[Sources/popular-health-article-xyz]]"
---
# Beta Blocker
…concept body…
````

The `schema_version: 1` discriminator is frozen for the lifetime of
Phase 1; downstream consumers (Chopper kb-search, future
`textbook-audit`) parse that to detect the textbook ingest output shape.

## Dependencies

- **Runtime**
  - Python 3.10+
  - `ebooklib >= 0.18` — EPUB structural reader (spine + TOC + metadata)
  - `beautifulsoup4 >= 4.12` — EPUB HTML → plain text + heading extraction
  - `pymupdf >= 1.23` (PDF fallback path, `fitz` API for outline extraction)
  - `pymupdf4llm >= 0.0.10` (markdown text extraction, used indirectly
    via existing `shared/pdf_parser.py`)
  - `pyyaml` (PDF manual TOC override path)
- **Internal**
  - `shared/pdf_parser.py` — `parse_pdf()` markdown text helper
  - `shared/obsidian_writer.py` — `write_page()` vault writer
  - `shared/config.py` — `get_vault_path()` resolves the vault root
  - `agents/robin/prompts/extract_concepts.md` — concept-extract prompt
    (this skill ships an adapted variant in `prompts/concept-extract.md`)
- **Credentials**
  - None for the helper script (`parse_book.py` is offline)
  - Claude Code session credentials (Max 200 subscription) for the
    interactive driver

No GCP / DataForSEO / Anthropic API keys needed in code — the LLM cost is
absorbed by the Claude Code subscription.

## Cost

- **LLM**: Claude Code Opus 4.7 via Max 200 subscription. One mid-size
  textbook (1500 pages, 30 chapters) ≈ 1.8M tokens (~50k input + ~10k
  output per chapter).
- **API$ if you switched to programmatic Sonnet via Anthropic SDK**:
  ~$3-5 per book (rejected in ADR-010 §Alternatives Considered).
- **Wall clock**: 30-90 minutes per textbook, dominated by per-chapter
  Opus turns.
- **Network**: zero external — only local PDF read + vault writes.
- **Effective per-run cost**: subscription quota only; one book ≈
  one session worth of Opus turns.

## Open-Source Readiness

Parameterized extension points so the skill can be lifted out of Nakama:

1. **Vault path injection** — uses `shared.config.get_vault_path()`; a
   fork wires up its own vault root via env / config without touching
   the skill code.
2. **Format dispatch by file extension** — `parse_book.py` dispatches on
   `.epub` → `_parse_epub` / `.pdf` → `_parse_pdf`; a fork adding `.docx`
   / `.azw3` / etc. inserts a new branch + matching `_parse_<fmt>` function,
   the JSON outline contract stays.
3. **EPUB parser uses standard `ebooklib` + BeautifulSoup** — drop-in
   replacement of the metadata extraction or HTML→text logic does not
   touch the dispatcher or downstream prompt templates.
4. **PDF parser is swappable** — `parse_book.py` calls `pymupdf` for
   outline extraction; a fork adopting Docling replaces only `_parse_pdf`.
3. **Manual TOC override path** — `--toc-yaml /path/to/toc.yaml` lets a
   fork bypass detection entirely with hand-written chapter boundaries.
4. **Prompt templates are isolated** — `prompts/chapter-summary.md` /
   `prompts/concept-extract.md` / `prompts/book-entity.md` are
   self-contained; a fork using domain-specific prompts (legal /
   technical / fiction textbooks) only edits these files.
5. **Schema discriminator + version** — `schema_version: 1` on Book
   Entity / Chapter Source frontmatter gives downstream code a stable
   contract.
6. **Provider-agnostic shape** — `ingested_by:` field tags which model
   produced the output; Phase 2 multi-provider (OpenAI / Google) reuses
   the same schema with a different tag value.

## Contract Tests

- Unit: `tests/skills/textbook_ingest/test_parse_book.py` — synthetic
  EPUB built in-memory via `ebooklib`, asserts metadata extraction,
  chapter count, section_anchors, page estimation, chapter export, and
  format dispatch (8 tests, all green).
- Live smoke: run `parse_book.py --help` from any cwd to verify
  `sys.path` shim + arg parser; full ingest is interactive and run
  manually per book.

## Limitations (Phase 1)

- **English textbooks only by default** — Chinese / Japanese support
  works mechanically but Concept extraction prompts are English-biased;
  Phase 2 includes localized prompts
- **EPUB preferred over PDF** — EPUB has authoritative chapter
  structure (OPF spine + nav). PDF requires outline / regex / Opus
  self-detection fallbacks; expect occasional chapter mis-segmentation
  on PDFs without bookmarks
- **EPUB pages are estimated** — 250 words/page heuristic;
  citations show "estimated p.X". For exact citation use PDF input
- **Scanned PDFs unsupported** — no OCR fallback; `parse_book.py` falls
  through to `status: needs_manual` and exits
- **Single-machine session** — the skill runs in one Claude Code
  session on Mac; can't span multiple sessions or machines mid-book
- **Token budget exposure** — very large books (Harrison's 4000pp) can
  exhaust Max 200 weekly quota; check usage before ingesting back-to-back
- **No re-ingest delta detection** — re-running the skill on an
  already-ingested book duplicates Concept page additions; manual vault
  cleanup or future `--rebuild` flag (Phase 2)
- **No web UI** — Phase 2 backlog item (Bridge Hub upload + progress
  bar); Phase 1 is Claude Code interactive only
- **Single provider (Claude Opus)** — Phase 2 backlog item
  (multi-provider adapter for OpenAI Pro / Google AI Ultra)

## Roadmap

- [x] Phase 1 — Skill scaffold + parse_book helper + 3 prompt templates
- [x] Phase 1.1 — EPUB primary path (this PR): `_parse_epub` via
  `ebooklib`, 8 unit tests, format dispatcher
- [ ] Phase 1.5 — End-to-end MVP run on one mid-size English textbook
  (EPUB), verify all artefact types written correctly + Chopper
  retrieval works
- [ ] Phase 2A — Web UI (Bridge Hub upload + SSE progress bar)
- [ ] Phase 2B — Multi-provider adapter (Anthropic / OpenAI / Google)
- [ ] Phase 3 — Chinese textbook support (localized prompts)
- [ ] Phase 3 — Re-ingest delta detection (`--rebuild` flag with diff)

## References

- ADR: `docs/decisions/ADR-010-textbook-ingest.md`
- Skill: `.claude/skills/textbook-ingest/SKILL.md`
- Helper: `.claude/skills/textbook-ingest/scripts/parse_book.py`
- Prompts: `.claude/skills/textbook-ingest/prompts/`
- KB philosophy: [Karpathy gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- Compute tier: `memory/claude/feedback_compute_tier_split.md`
- Skill scaffolding pitfalls: `memory/claude/feedback_skill_scaffolding_pitfalls.md`
