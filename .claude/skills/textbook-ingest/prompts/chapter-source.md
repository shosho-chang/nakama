# Chapter Source Page — Phase 1 Lossless Ingest (v3)

Use this prompt when generating the structured JSON for a single chapter source
page (`KB/Wiki/Sources/Books/{book_id}/ch{n}.md`) from a Phase 1
`ChapterPayload` produced by `shared.source_ingest.walk_book_to_chapters`.

Per **ADR-020 §Phase 1**, the page body is assembled by the Python runner
(`_assemble_body`) directly from the walker output — byte-for-byte. The LLM's
sole job is to produce **structured JSON** — frontmatter fields, per-section
concept maps, and wikilinks. The runner writes the page body; you must NOT.

---

## Output schema (binding)

Output **only** JSON. No markdown fences around the JSON itself. No prose, no
preamble, no commentary. The first character of your response MUST be `{` and
the last MUST be `}`.

```json
{
  "frontmatter": {
    "title": "Chapter {chapter_index} — {chapter_title}",
    "book_id": "{book_id}",
    "chapter_index": 1,
    "chapter_title": "{raw chapter title}",
    "section_anchors": ["1.1 First Section", "1.2 Second Section"],
    "wikilinks_introduced": ["[[Term1]]", "[[Term2]]"],
    "figures": [
      {
        "vault_path": "Attachments/Books/{book_id}/{filename}",
        "alt_text": "caption verbatim from raw",
        "vision_class": "schematic | photo | data-graph | flow-diagram | other",
        "vision_status": "caption_only"
      }
    ],
    "tables_overview": [{"caption": "...", "row_count": 5}],
    "ingest_date": "YYYY-MM-DD",
    "vision_status": "caption_only"
  },
  "sections": [
    {
      "anchor": "1.1 First Section",
      "concept_map_md": "```mermaid\nflowchart LR\n  A --> B\n```",
      "wikilinks": ["Term1", "Term2"]
    }
  ]
}
```

`sections[i].anchor` is the heading text WITHOUT the `## ` prefix — exactly as
it appears in `section_anchors` from the walker. The runner uses this for
identity matching; a wrong anchor causes a fatal error.

---

## Hard rules

1. **JSON only.** Output **only** JSON. No markdown fences around the JSON
   itself. No preamble, no commentary, no trailing explanation.

2. **No body.** DO NOT emit a `body` field. DO NOT include verbatim chapter
   text. The Python runner assembles the page body from the walker output
   literally; you only contribute structure (frontmatter + per-section concept
   maps + wikilinks).

3. **No `llm_description`.** DO NOT write a `llm_description` field on figures.
   It has been retired. Each figure entry must have exactly: `vault_path`,
   `alt_text`, `vision_class`, `vision_status: "caption_only"`. A future
   vision-describe pass will upgrade `vision_status` to `"true_vision_done"` and
   backfill descriptions.

4. **Anchor identity.** `sections[i].anchor` MUST equal `section_anchors[i]`
   **byte-for-byte**, including Unicode punctuation. DO NOT normalize curly
   apostrophes (`'` U+2019) to ASCII (`'` U+0027), DO NOT replace en-dash (`–`)
   or em-dash (`—`) with hyphens (`-`), DO NOT change no-break spaces. The runner
   does an NFKC-tolerant fallback compare for safety, but you should still emit
   anchors verbatim — drift here triggers a warning log even when the run
   continues. Same string, same order, no `## ` prefix. If you cannot satisfy
   this for any section, return JSON with an `"error"` key describing the problem
   rather than guessing an anchor.

5. **Concept maps are meaningful.** Each `concept_map_md` must be one fenced
   ` ```mermaid ` flowchart that reflects actual concept relationships in that
   section's text — max ~12 nodes. Do not generate a decorative or placeholder
   graph.

6. **Wikilinks are book terms.** Each item in `wikilinks` and in
   `wikilinks_introduced` uses `[[Term]]` format. Include only book-relevant
   named concepts: molecules, enzymes, pathways, structures, processes, named
   principles. No stop-words, no chapter numbers, no generic phrases.

7. **Source language.** Wikilinks must match the term as it appears in the
   source chapter language (English for English chapters, 繁中 for 繁中 chapters).
   Do NOT translate.

---

## Inputs

| Field | Source | Description |
|-------|--------|-------------|
| `book_title` | caller | Full human title |
| `book_id` | ChapterPayload | Book slug, e.g. `bse-2024` |
| `chapter_index` | ChapterPayload | 1-based integer |
| `chapter_title` | ChapterPayload | Text of the H1 heading |
| `section_anchors` | ChapterPayload | H2 heading texts in order, WITHOUT `## ` (authoritative — sections[] must match exactly) |
| `figures` | ChapterPayload | List of `{vault_path, alt_text}` dicts |
| `tables` | ChapterPayload | List of `{caption, row_count}` dicts |
| `ingest_date` | caller | ISO date string |
| `verbatim_body` | ChapterPayload | Raw chapter text (read-only reference — extract concepts from here; do NOT copy into output) |

---

## Wikilinks coverage

Be comprehensive. A typical biochemistry chapter introduces 20-40 wikilink-worthy
concepts. Include terms that:

- Appear in a section heading
- Are **bolded** or *italicised* on first use
- Follow a definitional construct ("X is defined as", "X refers to", "X is …")
- Recur as central terms across multiple sections

Exclude pure passing mentions (a single non-defining mention with no signal),
stop-words, and generic descriptors. Coverage is measured by the acceptance gate
(`len(wikilinks_introduced) ≥ char_count // 2000`).

---

## Full prompt (fill-in template)

````
You are generating Phase 1 structured metadata for a chapter source page of
*{book_title}* (book_id: {book_id}).

The page BODY is assembled by the Python runner from the walker verbatim output.
Your only job is to emit structured JSON — do NOT write any body text.

Output ONLY a JSON object with this exact shape:
{
  "frontmatter": { title, book_id, chapter_index, chapter_title,
                   section_anchors, wikilinks_introduced, figures,
                   tables_overview, ingest_date, vision_status },
  "sections":    [ { anchor, concept_map_md, wikilinks }, ... ]
}

Hard rules — violations fail the acceptance gate:
- Output ONLY JSON. No markdown fences around the JSON. No prose. No commentary.
- DO NOT emit a "body" field. DO NOT include verbatim chapter text.
- figures[].vision_status = "caption_only". DO NOT write "llm_description".
- sections[i].anchor MUST equal section_anchors[i] byte-for-byte, including Unicode
  punctuation (keep curly quotes ‘ ’ “ ”, en-dash –, em-dash —, no-break space verbatim).
  No "## " prefix, same order. If you cannot satisfy this for any section, return { "error": "<reason>" }.
- concept_map_md: one ```mermaid block per section, max ~12 nodes,
  reflecting actual concept relationships in that section (not decorative).
- wikilinks: [[Term]] format, book-relevant terms only (no stop-words).
- Wikilinks must be in SOURCE LANGUAGE (English for English text, 繁中 for 繁中 text).

Chapter metadata:
- Chapter index: {chapter_index}
- Chapter title: {chapter_title}
- Section anchors (authoritative — sections[].anchor must match these exactly, no ## prefix):
  {section_anchors}
- Ingest date: {ingest_date}

Figures ({len_figures}):
{figures_json}

Tables ({len_tables}):
{tables_json}

Chapter text (read-only — extract concepts from here; do NOT copy into output):
{verbatim_body}
````
