# Phase A subagent prompt template

Parameterized template for one Phase A background subagent (one chapter). The driver fills in the variables below and dispatches via `Agent({subagent_type: 'general-purpose', model: 'opus', run_in_background: true, prompt: <filled template>})`. See [ADR-016](../../../docs/decisions/ADR-016-parallel-textbook-ingest.md) for the architecture.

## Variables (driver fills)

| Variable | Description | Example |
|----------|-------------|---------|
| `{book_id}` | book slug | `sport-nutrition-jeukendrup-2024` |
| `{book_title}` | full title | `Sport Nutrition, 4E` |
| `{book_authors}` | for context | `Jeukendrup & Gleeson, Human Kinetics` |
| `{chapter_index}` | N (matches walker_nav for non-rebased books) | `7` |
| `{chapter_title}` | the title for this chapter | `Fat` |
| `{fig_count}` | expected figures | `19` |
| `{table_count}` | expected tables | `2` |
| `{walker_chapter_path}` | walker output for this chapter | `E:\textbook-ingest\sport-nutrition-jeukendrup-2024\chapters\ch7.md` |
| `{style_ref_path}` | already-ingested reference chapter (sister book OK) | `E:\Shosho LifeOS\KB\Wiki\Sources\Books\biochemistry-sport-exercise-2024\ch5.md` |
| `{attachments_dir}` | this chapter's figures + tables | `E:\Shosho LifeOS\Attachments\Books\sport-nutrition-jeukendrup-2024\ch7` |
| `{vault_output_path}` | where to write ch{N}.md | `E:\Shosho LifeOS\KB\Wiki\Sources\Books\sport-nutrition-jeukendrup-2024\ch7.md` |
| `{walker_nav_chapter}` | walker EPUB nav number (= chapter_index for non-rebased books, else map) | `7` |
| `{ingest_date}` | for frontmatter `ingested_at` | `2026-05-03` |
| `{domain_hint_paragraph}` | one paragraph of domain-specific concept seeds for wikilinks | (see Domain hint section below for guidance) |
| `{sibling_book_constraints}` | additional read-only book paths (if cross-book ingest in flight) | `E:\Shosho LifeOS\KB\Wiki\Sources\Books\biochemistry-sport-exercise-2024\` (read only) |

**Staged-write threshold rule**: if `{fig_count}` ≥ 20, the staged-write block below is mandatory. If `{fig_count}` < 20 the staged-write block is recommended but optional (single-Write was OK in pilot for ch7=19, ch8=17). When in doubt, default to staged-write — overhead is small, retry cost is high.

---

## Template (fill variables, paste as `prompt` to Agent tool)

```
You are a Phase A+2 subagent for ingesting *{book_title}* ({book_authors}) into an Obsidian Knowledge Base. Your two jobs: (1) write ONE high-quality `ch{chapter_index}.md` source page, then (2) inline Phase 2 concept dispatch — extract concepts and upsert concept pages immediately after writing the source page.

# Context
Parallel ingest: this chapter runs as a background subagent alongside other chapter subagents. You MAY write Concept pages for concepts introduced in THIS chapter only (per ADR-020 §Phase 2 — inline sync dispatch replaces ADR-016's deferred Phase B stub approach). Phase B housekeeping still runs after all subagents complete, but it no longer generates stubs — you do that inline.

User has stated 「最高品質優先」(highest quality priority). Use Opus 4.7 multimodal vision on every PNG. Do not rush.

{domain_hint_paragraph}

# CRITICAL: staged-write protocol (mandatory if figs ≥ 20; recommended otherwise)

Compose-output for textbook chapters can exceed 600s stream watchdog if generated in a single Write call. Use staged-write to keep tool calls flowing within the 600s watchdog interval:

**W1**: After all reads + vision, Write `ch{chapter_index}.md` with:
- Complete YAML frontmatter (all figs with full llm_descriptions, all tables, ingest metadata)
- Empty body skeleton — one heading per section with `TODO-<id>` placeholder body:

```markdown
# {chapter_title}

## Learning Outcomes
TODO-learning-outcomes

## Key words
TODO-keywords

## {chapter_index}.1 <section title from walker>
TODO-{chapter_index}.1

... (one TODO per section walker provides)

## {chapter_index}.{last} Key Points
TODO-key-points

## References
TODO-references

## Cross-references within this book
TODO-cross-refs
```

Use distinctive `TODO-<section-id>` placeholders so Edit string-match is unambiguous later.

**W2..Wn**: For EACH section, do ONE Edit call replacing the `TODO-<id>` placeholder with the actual rendered content. Each Edit is bounded (1-3K tokens) → watchdog sees periodic tool activity. **Issue tool calls back-to-back without elaborate intermediate planning** — if you pause for >5 min between calls, watchdog may stall (ch11 retry-1 pilot incident).

**W-final (MANDATORY two-part audit — do not skip)**:

1. Use Grep tool with pattern `^```mermaid$` on your output file. Count results. Edit frontmatter `mermaid_diagrams:` to that exact integer.
2. **Hard constraint check**: if count < 3, add more mermaid blocks (one Edit each) until count ≥ 3. Do NOT lower the bar by editing frontmatter to match a sub-3 body — add real concept maps where they clarify cause-and-effect / decision tree / cascade. Re-grep + re-update frontmatter after additions.

**Why mandatory ≥3**: ch3 Sport Nutrition pilot (B1 batch) wrote frontmatter `mermaid_diagrams: 4` but body only had 2 — agent committed to count in W1 skeleton then forgot to backfill. Original ingest required user-triggered re-run (~210k tokens) to fix. The W-final audit is the cheapest possible insurance against this regression.

# Inputs (read in order)

1. **Walker EPUB extract** (raw textbook markdown — your chapter source content):
   `{walker_chapter_path}`
   Walker uses EPUB nav numbering. This file is human print chapter {chapter_index} (walker nav chapter {walker_nav_chapter}). Walker `<<FIG:fig-{walker_nav_chapter}-X>>` references map to your output `fig-{chapter_index}-X.png` filename — figure number X stays the same; only chapter number rebases (if at all).

2. **Style reference** (already-ingested chapter — follow its template precisely):
   `{style_ref_path}`
   Read in full. Gold standard for: frontmatter schema, body section organization, idiom usage (`**bold-define**`, `> verbatim quote`, `![[fig-X-Y.png]]` embed pattern, mermaid concept map placement, `$LaTeX$` math, `[[wikilink]]` density, structured English narrative).

3. **Attachments dir**:
   `{attachments_dir}`
   Expected: {fig_count} figures (fig-{chapter_index}-1 ... fig-{chapter_index}-{fig_count}) + {table_count} tables (tab-{chapter_index}-1 ... tab-{chapter_index}-{table_count}).

# Output (write only this)
`{vault_output_path}`

# Style spec (mirror style ref)

## Frontmatter (YAML — write all in W1)
```yaml
---
type: book_chapter
schema_version: 2
source_type: book
content_nature: textbook
lang: en
book_id: {book_id}
chapter_index: {chapter_index}
chapter_title: "{chapter_title}"
section_anchors: [extract from sections]
page_range: "..."
figures:
  - id: fig-{chapter_index}-1
    caption: "..."
    page: ...
    llm_description: |
      (5-7 lines from YOUR vision read — be precise; this powers downstream RAG search)
  ...
tables:
  - id: tab-{chapter_index}-1
    caption: "..."
    page: ...
mermaid_diagrams: <count — verify in W-final>
ingested_at: {ingest_date}
ingested_by: opus-4-7-1m
ingest_method: in-session-vision
walker_nav_chapter: {walker_nav_chapter}
---
```

## Body sections (match style ref flow)
- `# {chapter_title}` (H1 title)
- `## Learning Outcomes` — bullet list from walker
- `## Key words` — bullet list from walker
- `## {chapter_index}.1 ...`, `## {chapter_index}.2 ...`, etc.
- `## {chapter_index}.{last} Key Points` — summary bullets
- `## References` — citations from walker
- `## Cross-references within this book` — wikilinks to sibling chapters with one-line rationale (sibling files may not exist yet — that's OK, wikilinks resolve when phase B finishes)

## Body idioms (consistent with style ref)
- `**bold-define**` for new technical terms on first use
- `> "verbatim quote"` for definitional/key sentences quoted directly from textbook (don't paraphrase the most important definitions). **Aim for 8-15+ verbatim quotes per chapter — every quote = an authoritative citation anchor for downstream RAG agents. No upper bound; if the chapter is dense (e.g. metabolism / regulation / fatigue mechanism chapters) target 20-40.**
- `![[Attachments/Books/{book_id}/ch{chapter_index}/fig-{chapter_index}-X.{extension}]]` then italic caption `*Figure {chapter_index}.X. <caption>*`
- Tables: render inline as markdown table by reading content from `tab-{chapter_index}-X.md` and inlining (do NOT use `![[tab.md]]` transclusion — visually fragmenting and RAG can't read transcluded content)
- Mermaid concept maps: ≥3 per chapter (HARD CONSTRAINT, verified at W-final). Place at high-leverage sections: cascades, decision trees, hormonal × intracellular regulation cross-talk, intensity × fuel × fatigue × mitigation matrices, signalling pathway integrations.
- `$LaTeX$` inline math for any equation/reaction
- `[[wikilink]]` (繁體中文 for concept refs). **Aim for 100+ on dense metabolic/regulatory chapters; 50+ on standard chapters.** Higher density = better RAG hit rate. **Don't worry if wikilink targets don't exist** — phase B creates them.
- Body main narrative in structured English (academic but readable, bullet definitions, no long unbroken paragraphs). Only wikilinks and select captions are Chinese.

## Fig llm_description format (new convention — better RAG retrieval)

Don't write llm_description as flowing prose paragraph. Use **structured `**bold**` markers** for every label / panel / component / pathway node so downstream RAG keyword-matching lands in structurally-bracketed labels rather than buried in prose. Pattern:

```
llm_description: |
  **Figure type**: <one-line type, e.g. "Three-panel anatomical schematic of skeletal muscle ultrastructure">
  **Panel (a)**: <description with **bold** for every named component, label, axis, color cue>
  **Panel (b)**: ...
  **Concept linkage**: <one-line tying this figure to the body section that uses it>
```

Why: ch3 Sport Nutrition pilot diff (B1 vs C-full retry) showed prose llm_descriptions had keyword hits buried in 12-line paragraphs; structured-bold versions had every anatomical term findable. Downstream Robin/Chopper RAG retrieval precision is materially higher with structured-bold.

# Cross-reference handling (MANDATORY title mapping table)

The driver MUST inject an explicit chapter title mapping table for the WHOLE BOOK in `{domain_hint_paragraph}` or as a separate variable, and you MUST use ONLY those exact aliases when writing `## Cross-references within this book`. Do NOT improvise alias titles from your training data — old editions of the same textbook commonly have different chapter titles, leading to LLM hallucination.

Example mapping table the driver provides:

```
[[ch1|Nutrients and Recommended Intakes]]
[[ch2|Healthy Eating]]
[[ch3|Fuel Sources for Muscle and Exercise Metabolism]]
... (one line per chapter in this book)
```

Pick the 6-12 most relevant siblings for THIS chapter (not all of them — relevance > completeness), and write each with a one-line rationale tying that sibling to a specific section of THIS chapter (e.g. "extends §3.4's ATP discussion into whole-body bioenergetics").

**Why mandatory**: ch3 Sport Nutrition pilot (B1 batch) wrote 6 hallucinated cross-ref aliases using old-edition TOC titles (`[[ch1|Sport Nutrition Foundations]]` instead of correct `[[ch1|Nutrients and Recommended Intakes]]`); required user-triggered manual fix. Adding the title mapping in the retry-batch prompt eliminated all hallucinations.

# Walker artifact handling

If a fig PNG looks like a stray walker pipeline artifact (blank, lone arrow, isolated equation rasterized, single-word layout artifact like "AND" / "OR", clipart icon), document in frontmatter `notes:` field and either skip body embed OR render as inline LaTeX — preserve fig numbering. Precedents: BSE ch6 fig-6-12 (down-arrow), BSE ch8 fig-8-2..8-8 (inline equations rasterized), BSE ch11 fig-11-28 (sprinter clipart).

# 5MB image limit

If `Read` tool on a PNG fails due to size > 5MB, note in frontmatter `notes:` field with `vision_skipped: true` and provide caption-derived description as fallback. Do not attempt to resize (driver-side concern, out of scope for subagent).

# Phase A+2 HARD CONSTRAINTS (absolute)

You write TWO things: the source page + Concept pages for concepts from THIS chapter.

**Allowed writes:**
- `{vault_output_path}` — source page (main deliverable)
- `E:\Shosho LifeOS\KB\Wiki\Concepts\{slug}.md` — Concept pages for concepts introduced in THIS chapter only (create OR update_merge/update_conflict/noop per 4-action rules)

**Forbidden writes:**
- DO NOT modify `E:\Shosho LifeOS\KB\index.md` or `E:\Shosho LifeOS\KB\log.md` (Phase B housekeeping)
- DO NOT modify Book Entity at `E:\Shosho LifeOS\KB\Wiki\Entities\Books\{book_id}.md` (Phase B)
- DO NOT modify any other ch{X}.md in `E:\Shosho LifeOS\KB\Wiki\Sources\Books\{book_id}\`
- DO NOT create/modify Concept pages for concepts from OTHER chapters of this book (only this chapter's scope)
- {sibling_book_constraints}
- DO NOT touch `E:\nakama\` repo
- DO NOT run git/install/rm operations
- DO NOT use `rm` / `rmdir` (project policy — banned)

**Phase 2 hard invariants (ADR-020 §Phase 2 — violations = ingest fail):**
- Zero tolerance for placeholder stubs: NEVER write body text containing "Will be enriched later", "Will be enriched as Robin", or "Stub — auto-created by Phase B"
- L3 active concept body word count MUST be ≥ 200 words (body only, not frontmatter)
- If you cannot write a substantive L3 body, route the concept to L2 stub (single source, high-value signal) or L1 alias (low-value mention) — do NOT fall back to phase-b-style one-liner

You CAN read anything you need to. The constraint is on writes.

# Process
1. Read walker output `{walker_chapter_path}` in full.
2. Read style reference `{style_ref_path}` in full. Internalize frontmatter shape, section organization, idiom usage.
3. Glob `{attachments_dir}\*.{png,jpg}` and `*.md` to inventory. Verify count matches expected ({fig_count} figs + {table_count} tables).
4. Vision-describe each `fig-{chapter_index}-X.{ext}`: use Read tool on the image path. Write `llm_description` using the **structured `**bold**` markers** convention (see Body idioms above) — every label / panel / component / pathway node bracketed for RAG keyword retrieval. Apply walker artifact precedent if needed.
5. Read each `tab-{chapter_index}-X.md`.
6. **W1**: Write `ch{chapter_index}.md` = full frontmatter (all fig llm_descriptions + tables + mermaid count placeholder) + body skeleton with `TODO-<id>` placeholders.
7. **W2..Wn**: One Edit call per section, replacing `TODO-<id>` with rendered content. Issue back-to-back; don't pause for elaborate planning between calls.
8. **W-final part 1**: Grep `^```mermaid$` on output file. Count matches. Edit frontmatter `mermaid_diagrams:` to that exact integer.
9. **W-final part 2 (HARD CONSTRAINT)**: If count < 3, add mermaid blocks (one Edit each) until count ≥ 3. Do NOT lower the bar by editing frontmatter to match a sub-3 body. Re-grep + re-update frontmatter after additions.

## Phase 2: Inline Concept Dispatch (ADR-020 §Phase 2 — runs after W-final)

After the source page is fully written, dispatch concept pages for every `[[wikilink]]` identified in the `### Wikilinks introduced` sections. Do this INLINE (not deferred to Phase B).

**4-action dispatch rules per concept slug:**

| Situation | Action | What to write |
|-----------|--------|--------------|
| `KB/Wiki/Concepts/{slug}.md` does not exist | `create` | v3 concept page with body extracted from THIS chapter |
| File exists + new extract is complementary | `update_merge` | Read existing body → LLM diff-merge new extract in → write back |
| File exists + new extract conflicts with existing data | `update_conflict` | Append to `## 文獻分歧 / Discussion` section |
| File exists + no new substantive content | `noop` | Append source link to `mentioned_in:` only |

**Concept page v3 schema (write this for `create`):**

```yaml
---
title: "{slug in 繁體中文 or English}"
aliases: ["{English equivalent or alternate Chinese}"]
en_source_terms:                   # English terms from THIS chapter for this concept
  - "exact phrase from chapter text"
type: concept
domain: "{domain}"
schema_version: 3
status: "active"                   # or "stub" for L2
maturity_level: "L2"               # L2=single-source high-value, L3=multi-source or confirmed
high_value_signals:                # why L2 not L1 (omit for L3)
  - "section_heading"              # term is a ## heading
  - "bolded_define"                # textbook **bolds** the definition
mentioned_in:
  - "[[Sources/Books/{book_id}/ch{chapter_index}]]"
created: {ingest_date}
created_by: phase-2-concept-dispatcher
---

# {title}

## Definition
[From THIS chapter — min 200 words for L3 active, substantive content for L2 stub]

## Core Mechanism
[Key mechanism from THIS chapter]

## Practical Applications
[Sport nutrition / exercise physiology application]

## See also
[Related [[wikilinks]]]
```

**Maturity level routing:**

- **L1 (alias only)**: 1 mention + low-value (no section heading, no bold define, passing mention) → do NOT create a page; add to `_alias_map.md` if that file exists
- **L2 stub**: 1 source but high-value (is a section heading, OR bolded define, OR ≥3 mentions across ≥2 sections, OR follows "is defined as" type phrase) → create concept page with initial body + `status: stub`
- **L3 active**: ≥2 sources OR confirmed by content → `status: active`, body ≥ 200 words (HARD MIN)

**en_source_terms field:** For each concept, list every exact English phrase from THIS chapter that maps to this concept. Example: concept `[[腸道菌群]]` → `en_source_terms: ["gut microbiota", "intestinal flora", "gut microbiome"]`. This powers bilingual RAG query expansion.

**Concurrency note:** Multiple subagents may dispatch to the same concept slug. Write atomically: Read → compute new content → Write in one operation. If you see unexpected existing content (another subagent wrote since your Read), merge rather than overwrite.

# Quality bar (hard constraints unless noted)
- Every fig llm_description from real vision read (not caption inference) **with structured `**bold**` markers**.
- Verbatim quotes ≥ 8 (no upper bound — every quote = authoritative citation anchor for downstream RAG).
- **Mermaid concept maps ≥ 3 (HARD — verified at W-final, must add if under).**
- Wikilinks ≥ 50 standard / ≥ 100 dense metabolic-or-regulatory chapters.
- Body in structured English with bullet definitions; not long unbroken prose.
- Frontmatter YAML must parse cleanly.
- Cross-references use ONLY the chapter title mapping table (zero LLM-invented aliases).

# DoD report (≤ 300 words)
- Line count
- Fig count (vision-described / handled-as-artifact / vision_skipped)
- Table count
- Mermaid count (frontmatter == actual after W-final)
- Verbatim quote count
- Wikilink count + sample 5-8 targets
- Section count (= number of W2..Wn Edits performed)
- **Phase 2 concept dispatch summary**:
  - Concepts dispatched total (create / update_merge / update_conflict / noop counts)
  - L3 active: count + min body word count seen
  - L2 stub: count
  - L1 aliases skipped (not dispatched): count
  - IngestFailError raised: count (must be 0 — any non-zero = ingest abort)
- Anomalies (stray figs, OCR artifacts, walker truncation, ambiguous content, dispatch errors)
- Phase A+2 constraint compliance confirmation

This is real production output going into a Knowledge Base that compounds over years. Write at the highest quality bar — your output will be reviewed by a human curator (修修).
```

## Domain hint section (driver writes one paragraph)

The driver injects ONE paragraph in `{domain_hint_paragraph}` to seed Chinese wikilink targets relevant to this chapter's content. Keep it under 80 words. Pattern:

> Domain hint for {chapter_title}: {2-3 sentences describing the chapter's scientific/practical scope}. Wikilinks should hit `[[繁中term1]]`, `[[繁中term2|english_alias]]`, ... (list 8-15 concept seeds the subagent should use as `[[wikilink]]` targets).

This isn't restrictive — the subagent will discover more targets reading the chapter. The hint just primes vocabulary so style stays consistent across chapters.

## Pitfalls

1. **Don't forget `walker_nav_chapter` mapping** — for books where walker nav doesn't equal human chapter (e.g. BSE walker nav 10 = human ch5), the subagent's `<<FIG:fig-{walker_nav}-X>>` translation must point to `fig-{chapter_index}-X.png`. For non-rebased books (Sport Nutrition where walker 7 = human 7), they're equal.
2. **Style ref must exist** — first chapter of a brand-new book has no in-book style ref; use a sister book's already-ingested chapter (cross-book reference works fine — format is universal).
3. **Concurrent dispatch** — Anthropic API rate limits cap concurrent subagents per account. Pilot baseline: 8 parallel subagents from one parent works; 17 parallel from one parent runs into RPM caps. Batch as 8 + then-rest.
4. **Subagent re-vision on retry** — if a subagent fails mid-compose, retry doesn't have its prior vision results in cache. Cost overhead. Consider future enhancement: subagent W0 step writes vision descriptions to `/tmp/vision-cache-{book_id}-{chapter_index}.json`, retry loads from cache.
