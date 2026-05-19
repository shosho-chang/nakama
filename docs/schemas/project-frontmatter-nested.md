# Project Frontmatter — Nested-by-Source Schema

**Status:** Active
**Adopted:** 2026-05-17 ([ADR-027](../decisions/ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md) §7)
**Consumed by:** Brook synthesize, Brook 1b extractor, Robin RCP, Obsidian `Brook: Scaffold` button, dataviewjs widgets in Project templates

Obsidian LifeOS Project pages carry frontmatter blocks describing what
inputs each downstream agent should look at. ADR-027 §7 retired the flat
"every input is a top-level key" schema in favour of grouping inputs by
the agent / pipeline lane that owns them. This document is the
authoritative schema reference.

## Why nested?

The flat schema worked when there was one agent reading one set of fields.
Once Brook scaffold (synthesize), Brook 1b extractor, and Robin RCP all
needed to look at different overlapping subsets of the same Project page,
flat fields collided (which agent owns `keywords`? both?). Nesting by
source agent / line:

- Lets new agents add their own input block without breaking existing
  parsers — Brook reads `zoro_inputs`; if next quarter Franky adds
  `franky_inputs`, neither's parser is touched.
- Makes the per-line atomic content boundary visible: `line2_inputs` /
  `line1b_inputs` / `line3_inputs` mirror the three lines defined in
  `CONTENT-PIPELINE.md`.
- Documents the producer/consumer mapping inline — reading the
  frontmatter tells you which pipeline a Project is on.

## Top-level fields

| Key | Type | Required | Description |
|---|---|---|---|
| `type` | literal `"project"` | yes | Distinguishes Project pages from Tasks / Sources. |
| `line` | `"1b" \| "2" \| "3"` | yes (for ADR-027 flows) | Which `CONTENT-PIPELINE.md` line this Project sits on. Drives which `lineN_inputs` block is required. |
| `topic` | string | yes | One-sentence trad-Chinese topic. Used by Brook synthesize as the `topic` arg. |
| `content_type` | `"youtube" \| "blog" \| "research" \| "podcast"` | yes | Set by `project-bootstrap`; drives body template choice. |
| `status`, `priority`, `area`, `quarter`, `parent_kr`, `tags` | various | yes | LifeOS-standard governance fields (unchanged from the bootstrap skeleton). |

All other inputs live inside nested blocks below.

## `zoro_inputs` (cross-line; optional)

Holds keyword research output from Zoro. Read by Brook synthesize (Line 3
scaffold) and surfaced in the Obsidian `🗝️ Keyword Research` widget.

```yaml
zoro_inputs:
  keywords:
    - 創傷後成長
    - post-traumatic growth
    - PTG dose response
  trending_angles:
    - "dose-response curve framing"
    - "minimum effective stress"
```

| Key | Type | Required | Description |
|---|---|---|---|
| `keywords` | `list[str]` | no | Passed as `keywords` to `synthesize()`. Mix zh / en; the multi-query search lanes handle both. |
| `trending_angles` | `list[str]` | no | ADR-027 §Decision 4. Passed as `trending_angles` to `synthesize()`. The outline drafter may use these as section headings when evidence corresponds; unmatched angles surface on the store as `unmatched_trending_angles`. |

## `line3_inputs` (Line 3 topic-driven projects; required for Line 3)

Line 3 = topic-first articles where the atomic content is 修修's hand-
written draft. Brook synthesize is the scaffold producer.

```yaml
line3_inputs:
  # Currently no per-line fields beyond what zoro_inputs carries.
  # Reserved for future extensions (e.g. style_profile override per project).
  {}
```

This block is intentionally minimal today — keep it present as an empty
mapping so the parser is consistent across lines and so future fields can
land without a frontmatter migration.

## `line2_inputs` (Line 2 book/Source-driven projects; required for Line 2)

Line 2 = atomic content driven by a single Reading Source (book, paper,
long article) that 修修 has read and annotated. The scaffold producer is
**Robin's RCP** (`agents/robin/reading_context_package.py`), not Brook —
see ADR-024 + ADR-027 §3.

```yaml
line2_inputs:
  source: "[[book-creatine-clinical-handbook]]"
  rcp_slug: "creatine-clinical-handbook"  # optional override; defaults to the source slug
```

| Key | Type | Required | Description |
|---|---|---|---|
| `source` | wikilink string | yes | The KB Source page that owns the annotations + notes. |
| `rcp_slug` | string | no | Override for the RCP file slug if it differs from the source slug. |

The Obsidian `Brook: Scaffold` button reads this block to figure out
which RCP to display the link for — it does **not** call Brook synthesize
when `line: 2`; Line 2 scaffold is Robin's job.

## `line1b_inputs` (Line 1b interview projects; required for Line 1b)

Line 1b = 修修-hosted interview + curated pre-interview research pack.
The Stage 5 repurpose `line1b_extractor` consumes this block to produce
the cross-channel brief (ADR-027 §5).

```yaml
line1b_inputs:
  transcript: "[[interview-2026-05-20-author-x]]"
  research_pack:
    - "[[article-some-pre-interview-read]]"
    - "[[book-author-x]]"
```

| Key | Type | Required | Description |
|---|---|---|---|
| `transcript` | wikilink string | yes | The post-interview transcript (already ingested into KB). |
| `research_pack` | `list[wikilink string]` | yes | Closed pool of pre-interview materials. The `closed_pool` retrieval wrapper restricts KB search to `{transcript_slug} ∪ research_pack_slugs` with no transitive backlink traversal (ADR-027 §6, Layer 1). |

## Full example — Line 1b interview Project

```yaml
---
type: project
line: 1b
topic: "與 X 作者談 PTG 的劑量-反應曲線"
content_type: podcast
status: active
priority: high
area: work
quarter:
parent_kr:
tags:
  - project
  - podcast

zoro_inputs:
  keywords:
    - 創傷後成長
    - dose response
  trending_angles:
    - "minimum effective stress"

line1b_inputs:
  transcript: "[[interview-2026-05-20-author-x]]"
  research_pack:
    - "[[article-ptg-bonanno-2004]]"
    - "[[book-author-x-resilience]]"
---
```

## Extensibility rules

- **Adding a new input block**: create a new top-level key suffixed
  `_inputs` (e.g. `franky_inputs` for a future Franky agent). Do not
  reuse existing block names or add fields under another agent's block.
- **Adding a field to an existing block**: prefer additive optional
  fields. Required-field additions need an ADR amendment and a one-shot
  migration for existing Project pages.
- **Removing a field**: mark `Deprecated` in this doc first, leave the
  parser tolerant for one release cycle, then drop.

## Backwards compatibility

Projects created by `project-bootstrap` prior to PR-6 do not have any
`*_inputs` blocks. Parsers must treat all nested blocks as optional and
default to empty. The Obsidian `Brook: Scaffold` button degrades to
"please add zoro_inputs.keywords first" if the block is absent.

## References

- [ADR-027](../decisions/ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md) — §7 nested-by-source schema decision
- [ADR-024](../decisions/ADR-024-source-promotion-and-reading-context-package.md) — Robin RCP ownership
- [ADR-021](../decisions/ADR-021-annotation-substance-store-and-brook-synthesize.md) — Brook synthesize topic + keywords contract
- `agents/brook/synthesize/__init__.py` — `synthesize(slug, topic, keywords, *, trending_angles=...)`
- `agents/brook/line1b_extractor.py` — Line 1b extractor contract
- `agents/robin/reading_context_package.py` — RCP producer
- `shared/lifeos_templates/project_*.md.tpl` — Obsidian project body templates carrying the `Brook: Scaffold` button
