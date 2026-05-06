# Chapter Source Page — Phase 1 Lossless Ingest (v3)

Use this prompt when writing a single chapter source page
(`KB/Wiki/Sources/Books/{book_id}/ch{n}.md`) from a Phase 1
:class:`ChapterPayload` produced by `shared.source_ingest.walk_book_to_chapters`.

Per **ADR-020 §Phase 1**, this is a **lossless source page** — the body is
verbatim from the raw markdown, and the LLM's sole job is to add a structured
wrapper after each section. The four binding invariants:

| # | Invariant | Operational meaning |
|---|-----------|---------------------|
| **V1** | Verbatim body | Copy `verbatim_body` field exactly — do **not** paraphrase, summarise, or reorder paragraphs. Every sentence, number, and citation from the source chapter must appear unchanged in the output body. |
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

# Chapter {chapter_index} — {chapter_title}

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

- Use `[[繁體中文 slug]]` for Chinese concept names
- Use `[[English-slug]]` for concepts with no established Chinese term
- Include only terms that would merit a Concept page (section heading /
  bolded definition / central term — not passing mentions)
- Do **not** create the Concept pages here (HARD CONSTRAINT S1)

---

## Writing guidelines

1. **Copy verbatim_body exactly** — paste the `verbatim_body` string character-
   for-character. Do not change word order, add summaries, or remove detail.
2. **Wrapper goes after body** — `### Section concept map` and
   `### Wikilinks introduced` are H3 subsections appended after the verbatim
   content of each H2 section. Never insert them mid-paragraph.
3. **Figure embeds at natural position** — replace `![alt](path)` with
   `![[path]]\n*caption*` at the exact location in the body.
4. **Tables at natural position** — tables stay in place; add bold caption
   before them if detected.
5. **Frontmatter first** — write the complete YAML frontmatter block before the
   body, including all detected figures with `vision_class` and
   `llm_description`, all tables, and the full `wikilinks_introduced` list.

---

## Full prompt (fill-in template)

```
You are writing a Phase 1 lossless Chapter Source Page for the textbook
*{book_title}* (book_id: {book_id}).

This is NOT a summary. You are:
1. Copying the chapter body **verbatim** (zero paraphrase, zero omission)
2. Adding structured wrappers after each section (concept map + wikilinks)
3. Converting figure markdown links to Obsidian embed syntax
4. Ensuring tables are inline (no transclude)

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

VERBATIM BODY (copy exactly — do not paraphrase):

{verbatim_body}

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
   - Copy `verbatim_body` exactly
   - Convert each `![alt](path)` to `![[path]]` + `*caption*` line
   - Add bold caption before each pipe table if caption was provided
   - After each `## Section` block's verbatim content, append:
     ### Section concept map
     [mermaid or bullet]
     ### Wikilinks introduced
     [[concept-1]] [[concept-2]] ...

Verbatim invariant check: before outputting, verify that every sentence from
`verbatim_body` appears unchanged in your output body. If any sentence is
missing, re-insert it.
```
