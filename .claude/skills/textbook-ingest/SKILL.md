---
name: textbook-ingest
description: >
  Ingest a whole textbook (PDF / EPUB) into the Robin KB by chapter,
  producing a per-chapter Source Summary (section-by-section structure)
  + Book Entity index page + cross-book Concept/Entity wiki pages with
  ``mentioned_in:`` backlinks (Karpathy-style cross-source wiki).
  Use when the user says "ingest 這本教科書 <path>" / "把這本書加進 KB" /
  "textbook ingest <path>" / "教科書 ingest <path>". Do NOT use for
  short articles / single papers (that is Robin Reader ``/start`` flow),
  for keyword research (use ``keyword-research``), or for KB query
  (use ``kb-search``). Runs Mac-side via Claude Code Opus 4.7 1M context.
---

# Textbook Ingest — Whole-book → KB Wiki Pipeline

You are the interactive driver for ingesting a whole textbook into the
Robin knowledge base. Your job: take a PDF / EPUB path, parse chapter
boundaries, and for each chapter produce a Source Summary + extracted
Concept/Entity wiki pages, all written directly to the Obsidian vault
(`shared.obsidian_writer.write_page`). Final step: a Book Entity index
page that lists every chapter as wikilinks.

This is a long-running interactive workflow (~30-90 min for a typical
30-chapter textbook). Use Opus 4.7's 1M context window — read the full
chapter into your context, no map-reduce needed.

Reference design: [docs/decisions/ADR-010-textbook-ingest.md](../../../docs/decisions/ADR-010-textbook-ingest.md).

## When to Use This Skill

Trigger on intent like:

- "ingest 這本教科書 /Users/shosho/Books/harrison-21e.pdf"
- "把這本書加進 KB"
- "textbook ingest <path>"
- "教科書 ingest <path>"
- "幫我吸收這本醫學教科書"

Do NOT trigger for:

- **Short articles / single papers** — use Robin Reader (`POST /robin/start`)
  or `pubmed-to-reader`, that's the right tool for ≤ 100-page documents
- **Keyword research from a topic** — use `keyword-research`
- **Querying the KB** — use `kb-search`
- **Re-ingest of an already-ingested book** — manual vault edit + re-run only
  the affected chapters; do not blanket-rerun (Concept/Entity pages would
  duplicate `mentioned_in:` entries)

## Prerequisites

- ``shared/pdf_parser.py`` available (pymupdf4llm extracts page-level text)
- ``shared/obsidian_writer.write_page`` available (writes vault MD files)
- ``shared/config.get_vault_path()`` resolves to the vault root with `KB/`
- The vault has the directory structure expected by ADR-010 §D3:
  - ``KB/Raw/Books/``
  - ``KB/Wiki/Entities/Books/``
  - ``KB/Wiki/Sources/Books/``
  - ``KB/Wiki/Concepts/`` (shared cross-source pool)
  - ``KB/Wiki/Entities/`` (shared cross-source pool)
- Claude Code session running on Mac (or wherever vault is locally mounted)
- Max 200 subscription quota available — one mid-size book ≈ 1.8M tokens

## Workflow Overview

```
Step 1. Parse args + locate PDF                  [Read user message]
Step 2. Run parse_book.py to extract outline      [Bash: chapter boundaries]
Step 3. Confirm chapter detection with user       [CONFIRM, never skip]
Step 4. Ingest each chapter (loop, one turn each) [Read → summarize → write]
Step 5. Build Book Entity index page              [Write final entry page]
Step 6. Smoke-check + sync hint                   [Tell user to wait Obsidian Sync]
```

### Step 1 — Parse args from user message

Extract:

- ``pdf_path`` (required) — absolute path to the PDF file
- ``book_id`` (optional) — slug; if omitted, derive from filename
- ``book_subtype`` (optional) — one of:
  ``textbook_exam`` / ``textbook_pro`` / ``popular_health`` /
  ``clinical_protocol`` / ``reference``. Default: ``textbook_pro``
- ``language`` (optional) — ``en`` / ``zh-TW`` / ``zh-CN``. Default: ``en``

If ``pdf_path`` is missing or unclear, ask the user. Do NOT guess.

### Step 2 — Extract outline + chapter boundaries

Run from any cwd (script has sys.path shim, do NOT use ``python -m``):

```bash
python .claude/skills/textbook-ingest/scripts/parse_book.py \
    --path "/Users/shosho/Books/harrison-21e.pdf" \
    --out /tmp/textbook-outline.json
```

The script attempts (in order):

1. PDF outline / bookmarks (most textbooks have them)
2. heading regex (`^(Chapter|第)\s*\d+`) + font-size heuristic
3. Returns structured JSON: ``{book_metadata, chapters: [{index, title, page_start, page_end}]}``

If the script reports fallback to manual mode, tell the user and offer:

- Run with ``--toc-yaml /path/to/manual-toc.yaml`` to override
- Or proceed and let Opus self-detect chapters in Step 4

### Step 3 — Confirm chapter detection (NEVER skip)

Show the user the chapter list:

```
偵測到 30 章：
  ch1  (p.1-25)   Introduction to Clinical Medicine
  ch2  (p.26-58)  History Taking
  ch3  (p.59-92)  Physical Examination — Cardiovascular
  ...
書 metadata：
  title: Harrison's Principles of Internal Medicine
  edition: 21st
  isbn: 9781265060190
  pub_year: 2022

確認要 ingest 這 30 章嗎？(y / 改章節邊界 / cancel)
```

If user says `y`, proceed. If user wants to override, take their input
and adjust the chapter list. Do NOT proceed without explicit confirmation.

### Step 4 — Ingest each chapter (one Claude turn per chapter)

For each chapter (loop):

1. **Read chapter text** — use Read tool on the chapter slice (helper
   script can write per-chapter MD files to ``/tmp/textbook-chapters/`` for
   you to Read, or you read the PDF page range directly). Aim for the
   whole chapter in a single Read call (Opus 1M can handle 30-100 pages).

2. **Compose Chapter Source Summary** using the template at
   ``.claude/skills/textbook-ingest/prompts/chapter-summary.md``. The summary
   must follow section-by-section structure (one ``## section_anchor``
   per section, 2-3 paragraphs each), per ADR-010 §D2.

3. **Extract Concept / Entity candidates** using the template at
   ``.claude/skills/textbook-ingest/prompts/concept-extract.md``. This
   reuses Robin's existing prompt with one tweak: skip ``book`` Entity
   type (Book Entity is created at Step 5, not via concept-extract).

4. **For each concept candidate**:
   - Check if ``KB/Wiki/Concepts/{slug}.md`` exists (use Glob / Read)
   - If exists: append the chapter Source path to the ``mentioned_in:``
     frontmatter list (preserve existing entries; deduplicate)
   - If new: create with full content + ``mentioned_in: ["[[Sources/Books/{book_id}/ch{n}]]"]``

5. **Write chapter Source page** to
   ``KB/Wiki/Sources/Books/{book_id}/ch{n}.md`` with frontmatter per
   ADR-010 §D2 (Chapter Source schema).

6. **Update Book Entity progress** — set ``status: partial`` and bump
   the ``ingested_at`` timestamp on the Book Entity if it exists; create
   a stub if not (full Book Entity assembled in Step 5).

7. **Report progress** to the user:

   ```
   ✓ ch3/30 — Physical Examination — Cardiovascular (p.59-92)
     Source: KB/Wiki/Sources/Books/harrison-21e/ch3.md
     New concepts: 4 (frank-starling-law, jvp-waveform, …)
     Updated concepts: 2 (heart-failure ← already from pubmed-12345)
     New entities: 1 (eugene-braunwald — cardiology authority)
     Tokens used so far: ~140k / book budget ~1.8M
   ```

### Step 5 — Build Book Entity index page

After all chapters complete, write
``KB/Wiki/Entities/Books/{book_id}.md`` using the template at
``.claude/skills/textbook-ingest/prompts/book-entity.md``. Frontmatter
per ADR-010 §D2 (Book Entity schema), body lists all 30 chapters as
wikilinks plus a "Related concepts / entities" section pulled from the
chapter aggregations.

Set ``status: complete`` once all chapters successfully written.

### Step 6 — Smoke-check + sync hint

Final report:

```
完成！Harrison's Principles of Internal Medicine, 21st edition

✓ Book Entity:    KB/Wiki/Entities/Books/harrison-21e.md
✓ Chapter Sources: 30 / 30  (KB/Wiki/Sources/Books/harrison-21e/ch{1..30}.md)
✓ New Concept pages: 47
✓ Updated Concept pages: 12 (with new mentioned_in: backlinks)
✓ New Entity pages: 8

Token cost: ~1.85M tokens (Opus 4.7, Max 200 quota)
Wall time: 47 minutes

下一步：
  → 等 Obsidian Sync 把 vault 同步到 VPS（通常 2-5 分鐘）
  → 在 VPS 端用 kb-search 驗證：「kb search frank starling law」
  → Chopper 答題會自動從 mentioned_in: 串到本書章節
```

---

## Output Contract (for downstream consumers)

Three artefact types are produced; each has a stable schema for
downstream skills (Chopper kb-search, future ``textbook-audit``) to rely on.

### 1. Book Entity (`KB/Wiki/Entities/Books/{book_id}.md`)

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

# Harrison's Principles of Internal Medicine

…body lists chapters as wikilinks…
````

### 2. Chapter Source (`KB/Wiki/Sources/Books/{book_id}/ch{n}.md`)

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
section_anchors:
  - "3.1 Inspection"
  - "3.2 Auscultation"
page_range: "142-187"
ingested_at: 2026-04-25
---

# Chapter 3 — Cardiovascular Examination

## 3.1 Inspection
（section summary 2-3 paragraphs）

## 3.2 Auscultation
（section summary 2-3 paragraphs）
````

### 3. Concept page with backlink (`KB/Wiki/Concepts/{slug}.md`)

````markdown
---
title: Beta Blocker
type: concept
domain: cardiovascular
mentioned_in:
  - "[[Sources/Books/harrison-21e/ch5]]"
  - "[[Sources/pubmed-12345]]"
---

# Beta Blocker

…concept body…
````

### Stable guarantees

- ``schema_version: 1`` during Phase 1 lifetime
- ``mentioned_in:`` always a list of wikilinks (never inlined paths)
- ``status: complete`` only set once all chapters succeed

### Not stable (may evolve)

- Body markdown structure (headings within section summaries)
- ``ingested_by:`` value format (Phase 2 multi-provider may use other tags)

---

## Cost

- **LLM**: Claude Code Opus 4.7 via Max 200 subscription. One mid-size
  textbook (1500 pages, 30 chapters) ≈ 1.8M tokens (~50k input + ~10k
  output per chapter). Quota usage spread over the ingest session.
- **API$ if you switched to programmatic Sonnet**: ~$3-5 per book. The
  skill explicitly chose Opus via Max 200 to avoid this; see ADR-010 D1.
- **Wall clock**: ~30-90 minutes for a typical 30-chapter textbook,
  dominated by per-chapter LLM turns.
- **Network**: zero external — only local PDF read + vault writes.

## Open-Source Friendliness

This skill targets the Nakama repo but is extractable. Design constraints:

1. **No hardcoded vault path** — uses ``shared.config.get_vault_path()``;
   a fork wires up its own vault root via env / config.
2. **No hardcoded book metadata** — book_id / book_subtype / language are
   user-provided or derived from PDF outline; fork uses its own conventions.
3. **PDF parser is swappable** — ``parse_book.py`` calls
   ``shared.pdf_parser.parse_pdf``; fork can replace with Docling / EPUB
   / Word parser as needed.
4. **Concept-extract prompt is reusable** — adapted from Robin's
   ``agents/robin/prompts/extract_concepts.md``; fork can plug their own.
5. **Frozen output schema** — ``schema_version: 1`` lets downstream code
   discriminate.

See `docs/capabilities/textbook-ingest.md` for the full capability card.

---

## Pitfalls (skill-specific)

- **Do NOT re-extract Book Entity from concept-extract** — Step 5
  builds Book Entity separately. Concept-extract should skip
  ``entity_type: book``.
- **`mentioned_in:` deduplication** — when updating an existing Concept
  page, dedupe the backlink list (a chapter may mention the same
  concept multiple times across sections).
- **Chapter boundary errors** — if Step 3 detection is wrong, ingest
  Phase 4 will produce mis-aligned summaries; rerun Step 2 with
  ``--toc-yaml manual-toc.yaml`` rather than continuing.
- **Token budget overrun** — for very large books (Harrison's 4000pp =
  ~2.6M tokens) some chapters will exceed even Opus 1M context per
  turn; ``parse_book.py`` warns when a chapter > 200 pages and offers
  to sub-split.

See ``memory/claude/feedback_skill_scaffolding_pitfalls.md`` for general
skill scaffolding pitfalls (sys.path shim, 4-backtick fences, etc.).

---

## References

| When | Read |
|---|---|
| Final design | `docs/decisions/ADR-010-textbook-ingest.md` |
| Design process | `docs/plans/2026-04-25-textbook-ingest-design.md` |
| KB Wiki philosophy | [Karpathy gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) |
| PDF parser | `shared/pdf_parser.py` |
| Vault writer | `shared/obsidian_writer.py` |
| Robin existing prompts | `agents/robin/prompts/` (extract_concepts, summarize, write_concept, write_entity) |
| Compute tier | `memory/claude/feedback_compute_tier_split.md` |
| Skill scaffolding pitfalls | `memory/claude/feedback_skill_scaffolding_pitfalls.md` |
