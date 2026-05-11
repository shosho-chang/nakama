# Phase B reconciliation prompt template

Parameterized template for the single Robin background subagent that does cross-book housekeeping after Phase A+2 completes: audits backlink consistency → updates `mentioned_in:` gaps → flags unresolved wikilinks → updates Book Entity status / KB/index.md / KB/log.md.

**ADR-020 §Cross-source post-pass**: Phase A+2 subagents already inline-dispatch concept pages during ingest (S2). Phase B no longer creates stub Concept pages — that was the ADR-016 deferred approach superseded by inline dispatch. Phase B is now pure housekeeping.

Dispatched ONCE per book after all Phase A+2 subagents complete. See [ADR-020](../../../docs/decisions/ADR-020-textbook-ingest-v3.md) §Phase-B for why this is serial (shared mutable state — can't parallelize).

Driver fills variables and dispatches via `Agent({subagent_type: 'general-purpose', model: 'opus', run_in_background: true, prompt: <filled template>})`.

## Variables (driver fills)

| Variable | Description | Example |
|----------|-------------|---------|
| `{book_id}` | book slug | `sport-nutrition-jeukendrup-2024` |
| `{book_title}` | full title for log message | `Sport Nutrition, 4E` |
| `{chapter_count}` | total chapters | `17` |
| `{book_sources_dir}` | vault Sources path | `E:\Shosho LifeOS\KB\Wiki\Sources\Books\sport-nutrition-jeukendrup-2024` |
| `{book_entity_path}` | vault Book Entity path | `E:\Shosho LifeOS\KB\Wiki\Entities\Books\sport-nutrition-jeukendrup-2024.md` |
| `{ingest_date}` | for log + frontmatter timestamps | `2026-05-03` |
| `{domain}` | for new Concept page frontmatter `domain:` | `sport-nutrition` |
| `{pilot_notes_optional}` | extra context for log message — leave empty if none | `via parallel subagent architecture (8+9 batch); staged-write protocol used for chapters with figs ≥ 20` |

---

## Template (fill variables, paste as `prompt` to Agent tool)

```
You are the Phase B housekeeping agent for the *{book_title}* (`{book_id}`) Obsidian KB ingest. Phase A+2 produced {chapter_count} chapter pages (`ch1.md` through `ch{chapter_count}.md`) at `{book_sources_dir}\` AND inline-dispatched Concept pages per ADR-020 §Phase 2. Your job: post-ingest housekeeping — backlink consistency + gap audit + Book Entity + KB/index.md + KB/log.md.

# Mission

Ensure the wiki is consistent AFTER inline concept dispatch already ran:
1. Every `[[wikilink]]` in ch1-ch{chapter_count} that maps to a Concept page has that chapter listed in its `mentioned_in:` frontmatter.
2. Wikilinks with NO Concept page are flagged in the DoD report for human follow-up (do NOT create stubs — inline dispatch during Phase A+2 was the right time; stubs here create placeholder debt).
3. Book Entity + KB/index.md + KB/log.md are updated.

# Context

Karpathy-style wiki design (per `docs/decisions/ADR-010-textbook-ingest.md`): Concept pages are shared across all KB sources. Each Concept's frontmatter `mentioned_in:` is a list of wikilinks back to source pages (chapters, articles) that reference it.

Phase A+2 subagents inline-dispatch Concept pages during ingest (ADR-020 S2). Phase B is NOT responsible for concept creation. It is responsible for catching any `mentioned_in:` gaps that inline dispatch may have missed (race conditions, dispatched noop without backlink update, etc.).

Earlier chapters (e.g. ch1-ch4 if part of a multi-session ingest) MAY already have Concept pages with partial mentioned_in. Handle gap-fill for ALL {chapter_count} chapters.

# Inputs (read in order)

1. **Chapter pages** (read all {chapter_count}):
   - `{book_sources_dir}\ch1.md` ... `{book_sources_dir}\ch{chapter_count}.md`

2. **Existing Concepts dir** (Glob to inventory):
   - `E:\Shosho LifeOS\KB\Wiki\Concepts\`
   - List `*.md` to know which slugs already exist

3. **Existing Book Entity**:
   - `{book_entity_path}`

4. **KB index** (read structure):
   - `E:\Shosho LifeOS\KB\index.md`

5. **KB log** (append-only — read structure to match format):
   - `E:\Shosho LifeOS\KB\log.md`

# Process

## Step 1 — Extract wikilink targets

For each ch{N}.md (N=1..{chapter_count}), extract all `[[target]]` and `[[target|alias]]` patterns. Use Grep with pattern `\[\[([^\]|]+)(?:\|[^\]]+)?\]\]` (multiline mode if needed) — capture group 1 is the target slug.

**Filter out**:
- Targets matching `Sources/...` (those are source page refs, not Concept pages)
- Targets matching `Entities/...` (separate handling, out of Phase B scope)
- Targets matching `chN` shorthand or `Chapter N` / `第N章` patterns (intra-book chapter cross-refs, not concepts)
- Targets matching `Attachments/...` (image embeds, not concept refs)
- Empty/whitespace-only targets
- Targets with trailing backslash (Obsidian table-pipe escape artifact `[[X\|alias]]`) — strip the trailing `\` before deciding existence

Build a map: `{concept_slug → set of chapter_indices that mention it}`.

## Step 2 — Diff against existing Concepts

Glob `E:\Shosho LifeOS\KB\Wiki\Concepts\*.md` to inventory existing slugs. For each `concept_slug` in your map:

- **NEW** (no file exists at `Concepts\{slug}.md`): needs stub creation in Step 3
- **EXISTING** (file exists): needs mentioned_in update if chapters not already listed (Step 4)

## Step 3 — Backlink consistency audit (gap report — no stub creation)

Phase A+2 inline dispatch already created Concept pages for concepts it dispatched. Phase B audits consistency and reports gaps.

**For each concept_slug in your map:**

- **EXISTS** with correct `mentioned_in:` entries → no action needed (note as "consistent" in DoD)
- **EXISTS** but missing some `mentioned_in:` entries → backlink gap; handled in Step 4
- **DOES NOT EXIST** → this is an unresolved wikilink; **DO NOT create a stub page**. Record it in the DoD gap list for human follow-up.

**Why no stubs**: ADR-020 §Phase 2 mandates that Concept pages carry substantive body content — "Will be enriched later" stubs are explicitly forbidden (0 tolerance). Phase A+2 subagents had the chapter context to write real bodies; Phase B does not. Any concept page that wasn't created inline was either an L1 alias (intentionally not dispatched) or an error that needs human attention.

Build two lists for the DoD report:
- `gap_concepts`: slugs with NO existing page (unresolved wikilinks) — report count + up to 20 sample slugs
- `backlink_gaps`: existing pages missing some `mentioned_in:` entries — handled in Step 4

## Step 4 — Update mentioned_in on EXISTING Concept pages

For each existing concept page that should reference any of ch1-ch{chapter_count}:

1. Read the page's frontmatter
2. Look at current `mentioned_in:` list
3. For each chapter that mentions this concept BUT is not yet in `mentioned_in:`, append `"[[Sources/Books/{book_id}/chN]]"`
4. If list changed, Edit the frontmatter (dedupe — don't double-add)
5. If `mentioned_in:` field doesn't exist in frontmatter, add it
6. Do NOT modify the body of existing Concept pages
7. Leave broken/stale entries alone (e.g. shorthand `[[ch3]]` from older ingests) — cleanup is out of Phase B scope

## Step 5 — Update Book Entity

Read `{book_entity_path}`. Edit frontmatter:
- `chapters_ingested: {chapter_count}`
- `status: complete`
- `ingested_at: {ingest_date}` (only if currently older or missing)
- `updated: {ingest_date}`

If body has a chapter status table, ensure all {chapter_count} chapters are marked ✓ ingested. Don't reorganize the body otherwise.

## Step 6 — Append KB/index.md

Read `E:\Shosho LifeOS\KB\index.md`. Find the appropriate section (probably under a Books or Concepts section). Add entry for the book if not already there:

```
- [[Entities/Books/{book_id}]]
```

If no Books section exists, append `## Books` at file end with the entry. Don't reorganize otherwise. Use the existing format pattern.

## Step 7 — Append KB/log.md

Read `E:\Shosho LifeOS\KB\log.md` to learn its format. Append a new entry at the bottom matching that format:

```
{ingest_date} — {book_title} (`{book_id}`) ch1-ch{chapter_count} ingest milestone:
  - Phase A+2 complete ({chapter_count} chapters, inline concept dispatch) {pilot_notes_optional}
  - Phase B housekeeping: {N_updated} existing Concept pages backlink-updated; {N_gaps} unresolved wikilinks flagged for human follow-up
  - Book Entity status: complete
```

# Constraints (HARD)

- DO NOT modify chapter pages at `{book_sources_dir}\ch{1..chapter_count}.md` (read-only)
- DO NOT touch any other book's Sources directory
- DO NOT touch other in-flight ingest staging dirs (e.g. `E:\textbook-ingest\<other-book>\`)
- DO NOT touch `E:\nakama\` repo
- DO NOT delete files
- DO NOT use `rm` / `rmdir` (project policy)
- For idempotency: this agent may be re-run later; ensure all Edit/Write operations dedupe (check before append, check before write)

# DoD report (≤ 400 words)

- Wikilink targets total / unique
- Concept pages with consistent backlinks (count — no action needed)
- Existing Concept pages with backlink gaps updated (count + 5-10 sample slugs)
- **Unresolved wikilinks** (no Concept page exists — for human follow-up): count + up to 20 sample slugs
- Skipped (chapter cross-refs, entity refs, etc.): count
- Book Entity status update: confirmed (before → after value)
- KB/index.md: entries added (count + locations)
- KB/log.md: milestone appended (yes/no + line count after)
- Anomalies: ambiguous slugs / sanitization decisions / wikilinks that look broken

This agent runs once and completes Phase B for one book. Quality > speed.
```

## When to dispatch

After ALL Phase A subagents for `{book_id}` complete (success or skip — partial is OK as long as the present-but-incomplete chapters are flagged for retry separately).

**Two-book serial coordination**: if multiple books are running Phase B at the same time, they will compete on `KB/index.md` and `KB/log.md` writes. Don't run two Phase B subagents concurrently across books. Schedule them serially.

## Cost / wall time benchmark

- BSE textbook (11 chapters, ~6000 lines aggregated body): wall time **10 min** / 115K tokens / 62 tool uses (2026-05-03 BSE pilot)
- Mid-sized book (~15-20 chapters): est 15-25 min wall time
- Large book (30+ chapters): est 30-45 min wall time; if chapter pages are very long (>1000 lines each), the agent may itself need staged-write for the Concept stubs — but each stub is only ~15 lines so this is unlikely until 100+ stubs.
