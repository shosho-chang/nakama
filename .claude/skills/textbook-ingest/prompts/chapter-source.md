# Chapter Source Page — Phase 1 Lossless Ingest (v3)

Use this prompt when writing a single chapter source page
(`KB/Wiki/Sources/Books/{book_id}/ch{n}.md`) from a Phase 1
:class:`ChapterPayload` produced by `shared.source_ingest.walk_book_to_chapters`.

Per **ADR-020 §Phase 1**, this is a **lossless source page** — the body is
verbatim from the raw markdown, and the LLM's sole job is to add a structured
wrapper after each section. The four binding invariants:

| # | Invariant | Operational meaning |
|---|-----------|---------------------|
| **V1** | Verbatim body | Copy `verbatim_body` field **byte-for-byte** — do not paraphrase, summarise, reorder, re-punctuate, or "tidy up". This includes: keep all curly quotes (`'` `'` `"` `"`) as-is — do NOT normalise to straight quotes. Keep all em-dashes, en-dashes, ellipsis characters, non-breaking spaces. Keep `[Figure X.Y](#anchor)` markdown cross-reference links unchanged (these are inline references inside paragraphs, NOT figure embeds — only `![alt](path)` image syntax gets converted per V2). Every sentence, number, citation, and unicode character from the source chapter must appear unchanged in the output body. **Any paraphrase or punctuation rewrite is a verbatim-invariant violation.** |
| **V2** | Figure inline embed | Every figure reference `![alt](path)` in the body must be rendered as `![[path]]` (Obsidian wikilink embed) followed by `*caption*` on the next line. No transclude (`![[tab-X.md]]`). |
| **V3** | Table inline | Every pipe table in the body must appear as-is (inline markdown), preceded by a bold caption line if one was detected (`**caption**`). Tables must not be transcluded. |
| **V4** | Wrapper only | LLM adds `### Section concept map` + `### Wikilinks introduced` **after** each section's verbatim content — not mixed into the body text. |

**HARD CONSTRAINT (S1, lifted in S2)**: Do **not** create or edit any Concept
pages (`KB/Wiki/Concepts/`). Phase 2 sync concept dispatch handles that.

---

## Inputs

| Field | Source | Description |
|-------|--------|-------------|
| `book_id` | ChapterPayload | Book slug, e.g. `bse-2024` |
| `book_title` | caller | Full human title |
| `chapter_index` | ChapterPayload | 1-based integer |
| `chapter_title` | ChapterPayload | Text of the H1 heading |
| `verbatim_body` | ChapterPayload | Exact raw markdown slice — copy verbatim |
| `section_anchors` | ChapterPayload | H2 headings in order |
| `figures` | ChapterPayload | List of `{vault_path, alt_text}` dicts |
| `tables` | ChapterPayload | List of `{markdown, caption}` dicts |
| `ingest_date` | caller | ISO date string, e.g. `2026-05-06` |

---

## Output structure (binding)

```markdown
---
title: "Chapter {chapter_index} — {chapter_title}"
book_id: "{book_id}"
chapter_index: {chapter_index}
status: "source"
schema_version: 3
ingested_at: {ingest_date}
figures:
  - vault_path: "Attachments/Books/{book_id}/{filename}"
    alt_text: "{original alt text}"
    llm_description: "{6-class Vision description — see Vision triage rules below; leave blank if Decorative}"
    vision_class: "Quantitative | Structural | Process | Comparative | Tabular | Decorative"
tables:
  - caption: "{bold caption text or empty string}"
    row_count: {N}
wikilinks_introduced:
  - "[[concept-slug-1]]"
  - "[[concept-slug-2]]"
---

# {chapter_title}

(The H1 line MUST be **byte-identical** to the H1 line in `verbatim_body`. Do
NOT prepend `Chapter N —` or rewrite. If the raw H1 is `# 1 Energy Sources for
Muscular Activity`, your output H1 must be exactly `# 1 Energy Sources for
Muscular Activity`.)

## {section_anchor_1}

{verbatim paragraph text — copy exactly}

![[Attachments/Books/{book_id}/{fig_filename}]]
*{figure caption}*

**{table caption}**

| col1 | col2 |
|------|------|
| ... | ... |

### Section concept map

{mermaid flowchart OR nested bullet — describe the concept structure of this section}

```mermaid
flowchart LR
    ConceptA --> ConceptB
    ConceptB --> ConceptC
```

### Wikilinks introduced

[[concept-slug-1]] [[concept-slug-2]] [[concept-slug-3]]

## {section_anchor_2}

{verbatim paragraph text — copy exactly}

### Section concept map

{concept map for this section}

### Wikilinks introduced

[[concept-slug-4]]

…
```

---

## Figure inline embed rules (V2 — mandatory)

Every figure in `figures` list must appear in the body at its natural position:

```markdown
![[Attachments/Books/{book_id}/{filename}]]
*{caption from alt_text or figure metadata}*
```

- Convert `![alt](path)` → `![[path]]` Obsidian embed
- Caption is the `alt_text` from the FigureRef; if empty, use `*Figure {chapter_index}.{N}*`
- Do **not** duplicate `llm_description` into the body — it belongs in frontmatter only
- Do **not** use transclude syntax (`![[fig-X.md]]`) — embed the image file directly

### Vision 6-class triage rules

For each figure, classify and describe according to these six classes (ADR-020 §Phase 1):

| Class | When | Action |
|-------|------|--------|
| **Quantitative** | Axes / legends / error bars / data points | Extract data series, trends, axis labels, key values; transcribe equations |
| **Structural** | Anatomy / molecular / histology / microscopy | Label all significant components + spatial relationships |
| **Process** | Flowchart / metabolic pathway / multi-panel sequence | Input/output sequence + stage relationships; group multi-panel (a)(b)(c) as one unit |
| **Comparative** | Side-by-side / before-after | Focus on differences AND similarities — not individual descriptions |
| **Tabular** | Grid of text/numbers (image-as-table) | Transcribe as markdown table; preserve merged cells, footnote symbols |
| **Decorative** | Stock photo / portrait / contextual image | No Vision call; caption only + `[decorative]` tag in description |

Multi-panel figures (`fig-5-1a.png`, `fig-5-1b.png`) must be grouped into one
description unit, not described individually.

---

## Table inline rules (V3 — mandatory)

Every table in `tables` list must be rendered at its natural position:

```markdown
**{caption}**

{full pipe table markdown}
```

- If `caption` is empty, omit the bold caption line
- Do **not** transclude the table (`![[tab-X.md]]`)
- Copy table rows exactly from `tables[].markdown` — do not reformat columns

---

## Section concept map guidelines

After each section's verbatim body, add a `### Section concept map` block:

- **Mermaid flowchart**: recommended for pathways, hierarchies, causal chains
- **Nested bullet**: recommended for linear concept lists
- **Plain bullet**: acceptable for simple sections

The concept map reflects the structure of the section as written in the source.
Do not introduce concepts not present in this section.

---

## Wikilinks introduced guidelines

After the section concept map, add `### Wikilinks introduced` listing new
wikilink targets for concepts **first introduced or centrally defined** in this
section.

**LANGUAGE RULE (binding — do not deviate)**:

The wikilink text MUST match the term as it appears in the **source chapter
body**. Do NOT translate.

- If the chapter is written in English (e.g. textbook in English), the wikilink
  is the English term: `[[ATP]]`, `[[phosphocreatine]]`, `[[anaerobic glycolysis]]`,
  `[[lactate]]`, `[[creatine kinase]]`. Match capitalisation as it first appears
  in the source.
- If the chapter is written in 繁體中文, the wikilink is the Chinese term as it
  appears: `[[腺苷三磷酸]]`, `[[磷酸肌酸]]`.
- Do NOT translate English source terms into Chinese in this list. Translation
  happens later via the alias map; emitting Chinese wikilinks for an English
  source page makes the downstream maturity classifier blind to the term
  (regex rules run on the verbatim body).

Coverage:
- Be **comprehensive**, not minimal. A typical biochemistry chapter introduces
  20-40 wikilink-worthy concepts (named molecules, enzymes, pathways, body
  structures, processes, named principles). Aim for that order of magnitude.
- Include only terms that would merit a Concept page: appears in a section
  heading, is **bolded** or *italicised* on first use, is followed by a
  definitional construct ("X is defined as", "X is referred to as", "X is …"),
  or is a recurring central term used across multiple sections.
- Skip pure passing mentions (a single non-defining mention with no signal).
- Do **not** create the Concept pages here (HARD CONSTRAINT S1)

---

## Writing guidelines

1. **Copy verbatim_body exactly — byte-for-byte**. Paste the `verbatim_body`
   string character-for-character. Do NOT:
   - paraphrase, summarise, condense, expand, or "improve" any sentence
   - normalise punctuation (curly quotes `'` `'` `"` `"` stay curly; em-dash
     `—` stays em-dash; ellipsis `…` stays as one character)
   - reorder paragraphs or sentences
   - drop "filler" or transition phrases
   - rewrite the H1 line — keep it identical to the raw H1 (e.g. `# 1 Energy
     Sources for Muscular Activity`, NOT `# Chapter 3 — 1 Energy Sources …`)
   - convert `[Figure X.Y](#anchor)` cross-reference links — those are
     paragraph-internal references, not figure embeds. Keep them as-is.

   **Self-check**: before finalising output, mentally diff each non-figure
   paragraph against `verbatim_body`. If any paragraph differs by even one
   character (quotes, dashes, words), revert it. Aim for ≥ 99% paragraph match.

2. **Wrapper goes after body** — `### Section concept map` and
   `### Wikilinks introduced` are H3 subsections appended after the verbatim
   content of each H2 section. Never insert them mid-paragraph.
3. **Figure embeds at natural position** — when the body contains `![alt](path)`
   image syntax (the lone-line form), replace the entire line with
   `![[path]]\n*caption*` at the exact location. This is the ONLY transformation
   permitted on the body. Inline `[Figure X.Y](#anchor)` text-cross-references
   are NOT image embeds and stay unchanged.
4. **Tables at natural position** — tables stay in place; add bold caption
   before them if detected.
5. **Frontmatter first** — write the complete YAML frontmatter block before the
   body, including all detected figures with `vision_class` and
   `llm_description`, all tables, and the full `wikilinks_introduced` list.

### Failure modes to avoid

| Symptom | Why it's wrong |
|---------|----------------|
| Paragraph reads differently from raw (even one word changed) | V1 violation — body is lossless verbatim, not your retelling |
| Curly quotes turned into straight quotes | V1 violation — punctuation is part of "verbatim" |
| H1 prefixed with `Chapter N —` | V1 violation — frontmatter has chapter_index; body H1 stays raw |
| `[Figure 1.2](#c01-fig-0002)` removed or replaced | V1 violation — only `![alt](path)` image syntax converts |
| Chinese wikilinks emitted for an English source chapter | Maturity classifier blind — emit English wikilinks matching source language |
| < 10 wikilinks for a 20-page biochem chapter | Under-coverage — most named molecules / enzymes / pathways merit concept pages |

---

## Full prompt (fill-in template)

```
You are writing a Phase 1 lossless Chapter Source Page for the textbook
*{book_title}* (book_id: {book_id}).

This is NOT a summary. Your job has two parts that must NOT bleed into each other:

PART A — FRONTMATTER (you generate from scratch):
  - YAML frontmatter block at the top: title, book_id, chapter_index, status,
    schema_version, ingested_at, figures[], tables[], wikilinks_introduced[]
  - Vision descriptions for figures (per 6-class triage)
  - Wikilinks list — comprehensive, in SOURCE LANGUAGE (English for English
    chapters, 繁中 for 繁中 chapters; do NOT translate)

PART B — BODY (you COPY VERBATIM from `verbatim_body` input):
  - Paste `verbatim_body` byte-for-byte, paragraph-for-paragraph
  - Curly quotes stay curly. Em-dashes stay em-dashes. Whitespace stays.
  - The H1 line stays IDENTICAL to the raw (no `Chapter N —` prefix)
  - `[Figure X.Y](#anchor)` cross-reference links inside paragraphs stay as-is
  - The ONLY permitted body transformation: replace each `![alt](path)` image
    line with `![[path]]\n*caption*` at the exact same position.
  - The ONLY permitted insertion: append `### Section concept map` +
    `### Wikilinks introduced` blocks AFTER each H2 section's verbatim content.

If you find yourself rewriting a sentence "for clarity", STOP and paste it as-is.
Any paraphrasing in the body is a bug; the verbatim invariant is checked
mechanically (paragraph-substring match, target ≥ 99%).

HARD CONSTRAINT: Do NOT create or edit any Concept pages. Phase 2 handles that.

---

Chapter metadata:
- Chapter index: {chapter_index}
- Chapter title: {chapter_title}
- Section anchors: {section_anchors}
- Ingest date: {ingest_date}

Figures detected ({len_figures}):
{figures_json}

Tables detected ({len_tables}):
{tables_json}

---

VERBATIM BODY (copy this BYTE-FOR-BYTE into your output body — do NOT paraphrase,
do NOT normalise punctuation, do NOT rewrite the H1 heading. The text between the
fences below is what your output body must contain, with only:
  (a) `![alt](path)` image lines converted to `![[path]]\n*caption*`, and
  (b) `### Section concept map` + `### Wikilinks introduced` H3 blocks appended
      after each H2 section's content.):

<<<BEGIN_VERBATIM_BODY>>>
{verbatim_body}
<<<END_VERBATIM_BODY>>>

---

Instructions:

1. Write the YAML frontmatter block:
   - Include `title`, `book_id`, `chapter_index`, `status: "source"`,
     `schema_version: 3`, `ingested_at`
   - For each figure: `vault_path`, `alt_text`, `vision_class` (use 6-class
     triage rules), `llm_description` (full Vision description; blank if Decorative)
   - For each table: `caption`, `row_count`
   - `wikilinks_introduced`: full list of [[wikilink]] targets you identify

2. Write the chapter body:
   - Copy `verbatim_body` byte-for-byte (preserve curly quotes, em-dashes,
     whitespace, H1 wording — see PART B above)
   - Convert each `![alt](path)` image line to `![[path]]` + `*caption*` line
     at the exact same position; this is the ONLY body transformation
   - Keep `[Figure X.Y](#anchor)` cross-reference links inside paragraphs as-is
   - Add bold caption before each pipe table if caption was provided
   - After each `## Section` block's verbatim content, append:
     ### Section concept map
     [mermaid or bullet]
     ### Wikilinks introduced
     [[concept-1]] [[concept-2]] ...

Verbatim invariant check: before outputting, verify that every paragraph from
`verbatim_body` appears UNCHANGED (byte-for-byte) in your output body. The
acceptance gate measures paragraph-substring match against the raw and a
threshold of ≥ 99% is enforced. If any paragraph differs by even one character
(quote style, dash style, word choice), revert it to the raw form.
```
