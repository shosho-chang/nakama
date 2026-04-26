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
Step 1. Parse args + locate book file            [Read user message]
Step 2. Run parse_book.py to extract outline      [Bash: chapter boundaries]
Step 3. Confirm chapter detection with user       [CONFIRM, never skip]
Step 4. Ingest each chapter (loop, one turn each) [Read → summarize → write]
Step 5. Build Book Entity index page              [Write final entry page]
Step 6. Smoke-check + sync hint                   [Tell user to wait Obsidian Sync]
```

### Step 1 — Parse args from user message

Extract:

- ``book_path`` (required) — absolute path to the book file (.epub
  preferred / .pdf fallback)
- ``book_id`` (optional) — slug; if omitted, derive from filename
- ``book_subtype`` (optional) — one of:
  ``textbook_exam`` / ``textbook_pro`` / ``popular_health`` /
  ``clinical_protocol`` / ``reference``. Default: ``textbook_pro``
- ``language`` (optional) — ``en`` / ``zh-TW`` / ``zh-CN``. Default: ``en``

If ``book_path`` is missing or unclear, ask the user. Do NOT guess.
**Prefer EPUB over PDF when both editions are available** — EPUB has
authoritative chapter structure (OPF spine + nav) so chapter boundaries
are 100% accurate; PDF requires outline / regex / Opus self-detection
fallback chains and may mis-segment.

### Step 2 — Extract outline + chapter boundaries (with figures / tables)

Run from any cwd (script has sys.path shim, do NOT use ``python -m``):

```bash
# EPUB (preferred — authoritative chapter structure from OPF/nav)
python .claude/skills/textbook-ingest/scripts/parse_book.py \
    --path "/Users/shosho/Books/harrison-21e.epub" \
    --out /tmp/textbook-outline.json \
    --export-chapters-dir /tmp/textbook-chapters/ \
    --attachments-base-dir "/Users/shosho/Documents/Shosho LifeOS/Attachments/Books/harrison-21e"

# PDF (fallback — when no EPUB edition exists)
python .claude/skills/textbook-ingest/scripts/parse_book.py \
    --path "/Users/shosho/Books/harrison-21e.pdf" \
    --out /tmp/textbook-outline.json \
    --export-chapters-dir /tmp/textbook-chapters/ \
    --attachments-base-dir "/Users/shosho/Documents/Shosho LifeOS/Attachments/Books/harrison-21e"
```

**EPUB path** (`strategy: epub_nav`, ADR-011 §3.4.1):

1. OPF metadata → title / authors / language / publisher / pub_year (from `dc:date`)
2. nav TOC top-level entries → chapters (in spine reading order)
3. h2/h3 within chapter HTML → ``section_anchors``
4. Walker handles `<img>` `<svg>` → `Attachments/.../ch{n}/fig-N.{ext}` +
   `<<FIG:fig-{ch}-{N}>>` placeholder; `<table>` →
   `Attachments/.../ch{n}/tab-N.md` (markdown) + `<<TAB:tab-{ch}-{N}>>`
   placeholder; `<math>` → inline `$$LaTeX$$` (via mathml2latex if
   installed, else alt-text fallback)
5. Page numbers are **estimated from word count** (250 words/page,
   EPUB is reflowable); citation will say "estimated p.X" not exact

**PDF path** (`strategy: pdf_outline | regex_fallback | manual_toc`,
ADR-011 §3.4.2):

1. PDF outline / bookmarks (most textbooks have them)
2. heading regex (`^(Chapter|第)\s*\d+`) + font-size heuristic
3. ``--toc-yaml`` manual override
4. Chapter text rendered via ``pymupdf4llm.to_markdown(with_tables=True)``
   so tables survive; per-chapter images extracted via
   ``page.get_images()`` + ``doc.extract_image(xref)`` and appended as
   `## Figures (extracted, awaiting Vision describe)` placeholder
   block (PDF lacks reliable inline position attribution)

If the script reports `status: needs_manual`:

- (PDF only) Run with ``--toc-yaml /path/to/manual-toc.yaml`` to override
- (EPUB) ``--toc-yaml`` is rejected — nav is authoritative. If nav is
  empty, the EPUB is degenerate; ask user to inspect or convert

**Outline JSON figures / tables shape** (per chapter entry):

```json
{
  "index": 1,
  "title": "Energy Sources",
  "section_anchors": ["1.1 Introduction", "1.2 Phosphagen System"],
  "figures": [
    {
      "ref": "fig-1-1",
      "extension": ".png",
      "alt": "ATP-PCr kinetics curve",
      "caption": "Schematic of ATP-PCr energy system kinetics",
      "tied_to_section": "1.2 Phosphagen System",
      "placeholder": "<<FIG:fig-1-1>>"
    }
  ],
  "tables": [
    {
      "ref": "tab-1-1",
      "caption": "ATP yield per substrate",
      "tied_to_section": "1.4 Oxidative System",
      "placeholder": "<<TAB:tab-1-1>>"
    }
  ]
}
```

Binary lives at ``{attachments-base-dir}/ch{n}/{ref}{extension}`` (figures)
and ``{attachments-base-dir}/ch{n}/{ref}.md`` (tables).

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

ADR-011 §3.3 splits this into 7 sub-steps; Vision describe (4b) is new
in v2 and Concept extract (4d) now uses the 4-action dispatcher.

For each chapter (loop):

1. **Read chapter text** — use Read tool on the chapter slice from
   ``/tmp/textbook-chapters/ch{n}.md``. Body contains
   `<<FIG:fig-{ch}-{N}>>` / `<<TAB:tab-{ch}-{N}>>` placeholders + inline
   `$$LaTeX$$` for math. Aim for the whole chapter in a single Read
   (Opus 1M can handle 30-100 pages).

2. **Vision describe each figure** (ADR-011 §3.3 Step 2 — new in v2).
   For each `figure` in the chapter's outline JSON:

   a. Read the image binary via `Read` tool on
      ``{attachments-base-dir}/ch{n}/{ref}{extension}``.
   b. Use the prompt template at
      ``.claude/skills/textbook-ingest/prompts/vision-describe.md`` —
      fill in `{domain}`, `{book_subtype}`, `{book_title}`,
      `{chapter_title}`, `{tied_to_section}`, `{caption_or_alt_text}`,
      and `{surrounding_text}` (±500 chars from chapter text around the
      placeholder). Pick the system role from the domain mapping table
      in the prompt.
   c. Default model: Sonnet 4.6 (per ADR-011 §3.4 / decisions §Q4).
      Upgrade to Opus 4.7 only when Sonnet's output is visibly
      insufficient on a specific figure (e.g. complex mitochondria
      cross-sections).
   d. Store the description for use in Step 4c (chapter source body)
      and Step 4e (frontmatter `figures[].llm_description`).

   For tables: read the markdown file at
   ``{attachments-base-dir}/ch{n}/{ref}.md``; no Vision call needed
   (markdown already preserves structure). For inline math
   (`$$...$$`): no Vision call needed.

   Skip Vision describe entirely on re-ingest if the existing chapter
   source page already has `figures[].llm_description` populated for
   the same `ref` (idempotency / cost guard).

3. **Compose Chapter Source Summary** using the template at
   ``.claude/skills/textbook-ingest/prompts/chapter-summary.md``
   (rewritten for v2 — no word limit, verbatim quote per section,
   Section concept map per section). **Placeholder swap is mandatory**:
   every `<<FIG:fig-{ch}-{N}>>` / `<<TAB:tab-{ch}-{N}>>` / `<<EQ:eq-{ch}-{N}>>`
   placeholder in `chapter_content` must be swapped to its final
   markdown form in the body — placeholders **must not leak to the
   final page** (Obsidian renders them as plain text and the figure
   never displays).

   **Swap rules** (also documented in `chapter-summary.md`
   "Placeholder swap rules" section):

   - `<<FIG:fig-{ch}-{N}>>` → two-line Obsidian image embed + italic caption:
     ```
     ![[Attachments/Books/{book_id}/ch{n}/{ref}.{extension}]]
     *{caption}*
     ```
     `llm_description` stays in frontmatter `figures[].llm_description`
     for retrieval reverse-look-up; do **not** also splice it into the
     body (duplication causes desync risk).
   - `<<TAB:tab-{ch}-{N}>>` → bold caption + spliced markdown table content:
     ```
     **{caption}**

     {markdown table content read from Attachments/Books/{book_id}/ch{n}/{ref}.md}
     ```
     Do not use Obsidian transclude (`![[tab-1-1.md]]`) — visually
     fragmenting and retrieval can't read transcluded content.
   - `<<EQ:eq-{ch}-{N}>>` → `$$LaTeX$$` inline math.

   **Why this is strict**: PR C ch1 v2 ingest leaked 13 `<<FIG:>>` +
   2 `<<TAB:>>` placeholders into the published page, Obsidian render
   showed plain text and 13 figures invisible — see
   `docs/plans/2026-04-26-ch1-v2-acceptance-checklist.md` F3 for
   incident detail.

4. **Extract Concept / Entity candidates** using the v2 prompt at
   ``agents/robin/prompts/extract_concepts.md`` via Robin's
   `_get_concept_plan` (or directly if running in a non-Robin context).
   Returns 4-action plan: `create` / `update_merge` / `update_conflict`
   / `noop` per concept candidate.

5. **Dispatch each concept action via `kb_writer.upsert_concept_page`**
   (ADR-011 §3.5; Robin handles this in `_execute_concept_action`):

   - `create` — new concept page with v2 schema (8 H2 skeleton +
     `mentioned_in: [chapter source link]`)
   - `update_merge` — LLM diff-merge new extract into existing body
   - `update_conflict` — append structured `### Topic` block under
     `## 文獻分歧 / Discussion`
   - `noop` — append source link to `mentioned_in` only

6. **Write chapter Source page** to
   ``KB/Wiki/Sources/Books/{book_id}/ch{n}.md`` with v2 frontmatter
   (ADR-011 §3.2.1: includes `figures: [{ref, extension, caption,
   llm_description, tied_to_section}]` list — the skill driver derives
   the on-disk `path` from `{attachments-base-dir}/ch{n}/{ref}{extension}`
   when it needs to read the binary; it is not stored in frontmatter).

7. **Update Book Entity progress** — set ``status: partial`` and bump
   the ``ingested_at`` timestamp on the Book Entity if it exists; create
   a stub if not (full Book Entity assembled in Step 5).

8. **Report progress** to the user:

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
