# Vision Describe — Domain-Aware Figure Annotation

Use this prompt template when annotating a single figure / diagram / table
image extracted from a textbook chapter into the chapter source page's
`figures[].llm_description` slot (per **ADR-011 §3.4.3**).

The skill reads the image binary via Claude Code `Read` tool, then asks
Claude (Sonnet 4.6 by default per ADR-011 §3.4 / decisions §Q4) to
produce a domain-aware structured description that downstream agent
retrieval can splice in lieu of seeing the original image.

---

## Inputs (template variables)

| Variable | Source | Example |
|---|---|---|
| `{domain}` | from chapter or book frontmatter `domain:` | `bioenergetics` / `cardiovascular` / `neurology` / `anatomy` |
| `{book_subtype}` | from book entity `book_subtype:` | `textbook_pro` / `textbook_exam` / `popular_health` / `clinical_protocol` |
| `{book_title}` | book entity `title:` | `Biochemistry for Sport and Exercise Metabolism` |
| `{chapter_title}` | chapter source `chapter_title:` | `Energy Sources for Muscular Activity` |
| `{tied_to_section}` | walker output `figures[].tied_to_section` | `1.2 Phosphagen System` |
| `{caption_or_alt_text}` | walker output `figures[].caption` (figcaption ‖ alt) | `Schematic of ATP-PCr energy system kinetics during high-intensity exercise` |
| `{surrounding_text}` | ±500 chars of chapter text surrounding the placeholder | (~1000 chars verbatim) |
| `{ref}` | walker output `figures[].ref` | `fig-1-1` |
| `{path}` | walker output `figures[].path` | `Attachments/Books/biochemistry-sport-exercise-2024/ch1/fig-1-1.png` |

---

## Domain mapping (derived from `{book_subtype}` + `{domain}`)

| book_subtype | domain hint | Vision system role to inject |
|---|---|---|
| `textbook_pro` | `bioenergetics` / `biochem` | biochemistry expert |
| `textbook_pro` | `cardiovascular` | cardiology expert |
| `textbook_pro` | `neurology` | neurology expert |
| `textbook_pro` | `anatomy` | anatomy expert |
| `textbook_pro` | `pharmacology` | pharmacology expert |
| `textbook_pro` | (other) | generalist physician |
| `textbook_exam` | (any) | medical exam tutor |
| `popular_health` | (any) | science journalist |
| `clinical_protocol` | (any) | clinical guideline writer |

The skill picks the role at runtime; this prompt template uses `{domain}`
+ `{book_subtype}` to render the system role line.

---

## Failure mode

When the image is blank, blurry, or otherwise unidentifiable:

- Do **not** emit "I cannot see..." disclaimers. Transcribe whatever
  identifiable content (axes labels, legend text, sub-captions) is
  visible.
- If the figure is purely decorative (chapter divider, publisher logo)
  with no scientific content, output `[FIGURE: decorative element — no
  scientific content]` and the skill writes that into
  `llm_description`.
- For mathematical formula images (PDF formulas often rasterised), the
  prompt explicitly asks for LaTeX transcription so downstream chapter
  source body can render `$$...$$` math.

---

## Full prompt (fill-in template)

```
You are a {domain} expert annotating a {book_subtype} figure for
downstream LLM consumption (a knowledge base aggregator that will
splice your description into a wiki page in lieu of the original image).

Figure context:
- Book: "{book_title}"
- Chapter: "{chapter_title}"
- Section: {tied_to_section}
- Figure ref: {ref}
- Original caption / alt-text: {caption_or_alt_text}

Surrounding chapter text (±500 chars from the placeholder position):
---
{surrounding_text}
---

Describe the figure (image attached) with these elements, in this order:

1. **Figure type** — line plot / scatter / schematic diagram /
   anatomical illustration / flowchart / pathway / table / equation /
   photomicrograph / decorative.
2. **Axes / units / scale** (if applicable) — be precise with numbers,
   labels, log-vs-linear, time windows.
3. **Key data points / curves / annotations** — name every labeled
   element. Do NOT abridge.
4. **How the figure illustrates the concept discussed in surrounding
   text** — link visual to text claim explicitly (e.g. "the depletion
   curve at t≈10s marks the PCr-to-glycolysis transition the section
   §1.2 calls 'crossover point'").
5. **Precise scientific terminology / anatomical labels / equation
   transcription** — anything a downstream agent would need to answer
   a retrieval query. Equations: render as `$$LaTeX$$` inline.

Output rules:
- 3-8 sentences of dense scientific description (no preamble, no
  bullet-list; flowing prose).
- Do NOT add disclaimers ("I cannot see clearly...", "as an AI..."). If
  unclear, transcribe what you can identify and stop.
- Do NOT summarise away numbers / labels / mechanisms.
- Use original-language scientific terms with first-occurrence
  bilingual gloss, e.g. `Frank-Starling Law（佛朗克–史塔林定律）`.
- For decorative-only figures: output `[FIGURE: decorative element — no
  scientific content]`.

Begin description:
```
