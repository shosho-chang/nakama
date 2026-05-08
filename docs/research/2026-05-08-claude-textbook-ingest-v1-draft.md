---
title: Textbook ingest v3 — 5/8 patch panel review v1 draft (Claude lens)
date: 2026-05-08
author: Claude (Opus 4.7 1M context)
purpose: v1 draft for multi-agent-panel skill (Codex round 2 + Gemini audit input)
covers: 5/7 burn → 5/8 3 patches → ch1/3/6 validation → codex round 1 → newly-found alias-map staging bug
---

# Textbook ingest v3 — patch effort review v1 draft

## §0. TL;DR for auditors

ADR-020 textbook ingest v3 burned $45 on 5/7 (28-chapter batch shipped without Obsidian human-eyeball validation; 3 systemic usability failures discovered post-hoc). 5/8 morning landed 3 root cause patches (commit `feee4d8`), validated on BSE ch1/3/6, ready to ship 28 chapters. Codex round 1 review found additional bugs and pushed for patch 4 + gate hardening before scale. Claude (this draft) found a 4th bug independently: **`run_s8_preflight` single-chapter mode fails to redirect `_alias_map.md` writes to staging** — 5/8 ch1/3/6 ingests polluted live `KB/Wiki/_alias_map.md`. Question for panel: do we ship after patches 4-7, or is the architecture under-specified enough that we should pause and rewrite the dispatch + gate contract?

## §1. Pipeline architecture (Path B, 5/7 redesign)

```
EPUB → raw_ingest.epub_to_raw_markdown → KB/Raw/Books/{book_id}.md
       (figures → Attachments/Books/{book_id}/)

KB/Raw/{book_id}.md
  ↓ shared.source_ingest.walk_book_to_chapters (pure Python, splits by H1)
ChapterPayload(verbatim_body, section_anchors, figures, tables)
  ↓ scripts.run_s8_preflight.run_phase1_source_page (Sonnet 4.6 LLM)
LLM emits ONLY metadata JSON {frontmatter, sections: [{anchor, concept_map_md, wikilinks}]}
  — body is NEVER sent to LLM for re-emission; LLM never sees verbatim_body twice
  ↓ Python _assemble_body (run_s8_preflight.py:245-310)
Final source page = walker_verbatim + figure_transform + chapter-end appendix
  (## Section Concept Maps + ## Wikilinks Introduced)
  ↓ Phase 2: scripts.run_s8_preflight.run_phase2_dispatch
For each LLM-emitted wikilink term:
  1. _slug_from_term(term) → slug (strip <>:"/\|?* etc.)
  2. concept_classifier.route_concept(term, chapter_text, source_count=1)
     → L1 (no rule fires) | L2 (≥1 rule, source_count=1) | L3 (≥1 rule, source_count≥2)
  3. L1 → concept_classifier.append_alias_entry → KB/Wiki/_alias_map.md
     L2/L3 → concept_dispatch.dispatch_concept → kb_writer.upsert_concept_page

Output: KB/Wiki.staging/Sources/Books/{book_id}/ch{N}.md + Concepts/{slug}.md + _alias_map.md
```

Acceptance gate (`compute_acceptance` in `run_s8_preflight.py:680-740`):
- R1 verbatim_match_pct ≥ 0.99 (paragraph substring match against walker after appendix-strip + figure-transform-reverse)
- R2 section_anchors_match (exact list equality of H2 in body vs walker.section_anchors)
- R3 figures_embedded == walker.figures_count
- R4 wikilinks_count ≥ char_count // 2000

## §2. 5/7 burn — what failed

Ran 28 chapters BSE+SN ($45), no Obsidian eyeball on any sample. Post-hoc opening of vault revealed:

**B1. EPUB internal markdown links preserved verbatim** in `walker.verbatim_body`. Pattern: `[Chapter 12](chapter12.xhtml)`, `[Smith 2020](#c1-bib-0001)`. In Obsidian, click → auto-stub blank `chapter12.xhtml.md` orphan files in same directory. SN chapters had 25-58 such links each.

**B2. Concept slug validator rejected wikilink-shaped names**. `_validate_slug` regex `^[\w一-鿿][\w\-一-鿿]*$` rejected `NADPH oxidase` (space), `Na+-K+ pump` (`+`), `Branched-chain amino acid (BCAA)` (parens). LLM emitted body wikilink `[[NADPH oxidase]]`, but `concept_dispatch` raised `ValueError("unsafe concept slug")` → concept page never written → wikilink red.

**B3. Per-section metadata interleave** broke textbook flow. `_assemble_body` (old) inserted `### Section concept map\n\n{mermaid}\n\n### Wikilinks introduced\n\n- [[T]]\n` after every H2 textbook section. Visual chaos.

**Meta-failure**: 4-rule acceptance gate is partly tautological by Path B design (verbatim_match is `Python_assembled_body == python_walker_body` after Python normalize — passes by construction unless figure regex / H2 split breaks). Gate PASS was treated as ship signal. The miss was not a regression test gap — the gate was never measuring usability.

Memory captured: `memory/claude/feedback_validate_with_eyeball_before_scale.md`.

## §3. 5/8 morning — 3 patches

Commit `feee4d8` on `docs/kb-stub-crisis-memory`. Full unit suite: 3881 pass / 18 skip / 1 unrelated fail (test_log).

**Patch 1** (`shared/source_ingest.py`): regex strip `[text](*.xhtml[#anchor]?)` and `[text](#anchor)` in `_strip_epub_internal_links`, called from `_parse_raw_file` before chapter split. Negative lookbehind `(?<!!)` preserves figure `![alt](path)`. 9 unit tests in `tests/shared/test_source_ingest_strip_epub_links.py`.

**Patch 2** (`shared/kb_writer.py:_validate_slug`): split by `kind`. `kind="concept slug"` relaxed: forbidden chars only `/ \ : * ? " < > |` + `..` + leading `.` + leading/trailing whitespace. `kind="book_id"` strict (unchanged). 25 new tests in `tests/shared/test_concept_slug_normalization.py`. 2 obsolete `bad_slug` cases removed from `tests/test_kb_writer.py`.

**Patch 3** (`scripts/run_s8_preflight.py:_assemble_body`): body = walker_verbatim + figure_transform only. All metadata moves to single chapter-end appendix `\n\n---\n\n## Section Concept Maps\n\n### {anchor1}\n\n{cmap1}\n\n... ## Wikilinks Introduced\n\n- [[term]]\n...`. `normalize_for_verbatim_compare` + `section_anchors_match` updated via shared `_strip_chapter_appendix` helper. 13 existing assemble_body tests rewritten + 2 acceptance/dry-run tests updated.

## §4. 5/8 BSE 3-chapter validation

Re-ingested ch1, ch3, ch6 via `run_s8_preflight --chapter-index N`. 3 parallel Explore subagents ran 4-condition checklist:

| Check | ch1 | ch3 | ch6 |
|---|---|---|---|
| 1. Body structure clean (single appendix at end, no mid-body interleave) | PASS | PASS | PASS |
| 2. No `xhtml` / `](#c…` / inline markdown link → no auto-stub | PASS (0 links) | PASS (0 links) | PASS (0 links) |
| 3. Wikilink resolution rate (concept page exists / alias-only / pure red) | 17 green / 20 alias / 7 reported red = 84% covered | 134 green / 20 alias / 3 red = 98.1% | 50 green / 22 alias / 3 red = 96% |
| 4. Figures exist on disk (Explore reported FAIL but Bash recheck = ALL EXIST) | 13/13 ✓ | 34/34 ✓ | 25/25 ✓ |

ch1 reported pure-red (Adenine, Ribose, High Energy Bonds, Membrane Transport, Catabolic Pathway, Anabolic Pathway, Intramuscular Triacylglycerol). Cross-check vs both alias maps (live + staging) revealed Adenine/Ribose/Intramuscular Triacylglycerol DO exist in both maps — Explore agent missed checking the LIVE map. **True pure-red on ch1 = 4 terms**, not 7.

## §5. Codex round 1 verbatim findings

(Saved at `docs/research/2026-05-08-codex-textbook-ingest-audit-round1.md` — full transcript verbatim per skill protocol. Headline findings repeated here for panel context.)

- **Patch 1 regex gaps**: nested brackets `[Fig [A]](...)`, escaped `\]`, multi-line links not covered. Tests miss these cases.
- **Patch 2 missing checks**: no NFKC normalize → Unicode confusables / RTL override pass; Windows reserved names (`CON`, `AUX`, etc.) pass.
- **Patch 3 silent regression**: empty `concept_map_md` emits empty `### anchor` H3 blocks; appendix-strip regex uses literal marker, body content matching literal would be silently dropped from verbatim compare.
- **Pure-red root cause (corrects Claude's framing)**: Codex points out `concept_classifier.py:9-12` and `:88-93` document L1 as no-rule fallback (NOT skip). My (Claude's) Option A "L1 fallback fix" is no-op because L1 is already the path. My Option B "appendix trim pre-dispatch" structurally impossible — `_assemble_body` runs BEFORE Phase 2 dispatch. **Pick Option C (Codex sketch)**: post-dispatch appendix rewrite using dispatch log. Replace `## Wikilinks Introduced` with only page-resolved terms; add `## Aliases Recorded` plain-text list for alias-only terms.
- **Acceptance gate insufficient**: frontmatter `wikilinks_introduced` count vs body appendix `[[…]]` are assembled independently → silent diverge possible. Add: dispatch-health check (no error/skip), wikilink-resolve check (every `[[term]]` exists on disk).
- **ADR-020 contract drift**: `concept_dispatch.py:9-12` documents 4-action routing (create/update_merge/update_conflict/noop), but `run_s8_preflight.run_phase2_dispatch` only ever calls `create`/`exists-skip`.
- **Stale comment**: `_slug_from_term` docstring at `run_s8_preflight.py:534` says "replace whitespace + path separators" but code only replaces path separators (intentional post-Patch-2, comment not updated).
- **Codex ship recommendation**: patch-4-then-go.

## §6. New findings from Claude deep-read (post-codex)

These are findings auditors should treat as Claude-original (not duplicates of Codex round 1). Push back where you disagree.

### B4. Single-chapter `run_s8_preflight` does NOT redirect `_alias_map.md` to staging — POLLUTES LIVE WIKI

Severity: **HIGH** (silent contract violation; data hygiene).

`scripts/run_s8_preflight.py:1010` calls `_patch_kb_writer_to_staging()` (which redirects `KB/Wiki/Concepts` → `KB/Wiki.staging/Concepts`) but does NOT call `_patch_alias_map_to_staging()` — that monkey-patch only exists in `scripts/run_s8_batch.py:163-193` and is only activated by the batch entrypoint at line 751.

`shared/concept_classifier.append_alias_entry` (line 134) hardcodes `vault_path / "KB" / "Wiki" / "_alias_map.md"` — without the staging patch, ALL L1 alias writes land in live Wiki.

Empirical evidence: `E:/Shosho LifeOS/KB/Wiki/_alias_map.md` modification timestamp `May 8 07:44` exactly matches my (Claude's) ch6 re-ingest run on 5/8. File grew to 773 lines with terms like `Trans-fats | [[Sources/Books/biochemistry-for-sport-and-exercise-maclaren/ch6]]` appended. Live map polluted.

Implication for ch1/3/6 validation: the "alias-only" classification by Explore agents was checking the staging map only; some terms did get alias entries but to live map → reported as "pure red" or "alias-only" inconsistently.

Implication for ship: scaling without fixing means 28 more chapters of L1 aliases pollute live `KB/Wiki/_alias_map.md` — staging guarantee broken silently. **This must be patched before any scale run.**

### B5. `concept_classifier` 4-rule routing has ASCII-only assumption + brittleness

`_rule_section_heading` regex `^#{1,4}\s+.*<term>.*$` — works.
`_rule_bolded_define` matches `**term**` and `*term*` — works for ASCII; uncertain for CJK terms wrapped in `**` if context contains zh-CN unicode.
`_rule_freq_multi_section` requires `\b<term>\b` word boundaries — `\b` is undefined for CJK text. So a CJK term mentioned 5× across 3 sections would NOT match this rule. Significance: Chinese/Japanese textbooks (future books) will silently route everything to L1.
`_rule_definition_phrase` has 4 English patterns + 2 Chinese (稱為, 定義為) — narrow, brittle, but at least bilingual.

### B6. `route_concept(source_count=1)` hardcoded in `run_phase2_dispatch`

`run_s8_preflight.py:573` calls `route_concept(term, chapter_text, source_count=1)`. The classifier's L3 promotion path requires `source_count ≥ 2`. So for single-chapter ingest (the preflight path), L3 is structurally unreachable. The maturity model collapses to L1/L2 only. ADR-020 §149 expects L3 active concepts to be the highest-value tier. This means: even when 28 chapters share a concept (e.g. ATP appears in BSE ch1, ch2, ch5, SN ch3, ch7…), the per-chapter dispatch never sees source_count > 1.

L3 promotion would have to come from a separate cross-chapter pass (ADR-020 §Phase 2.5? — not implemented).

### B7. `update_merge` action: LLM diff-merge per repeat-mention

Phase 2 hits `upsert_concept_page(action="create", ...)`. If page already exists (`abs_path.exists()` → True, `kb_writer.py:658`), it falls back to `update_merge`. update_merge calls `_ask_llm` (Opus 4.7) for diff-merge on every cross-chapter repeat (kb_writer.py:766). 28-chapter batch with even modest concept overlap means 100+ Opus update_merge calls @ ~$0.15-1.20 each — easily $30+ in update_merge calls alone, on top of the Phase 1 Sonnet cost.

5/7 batch cost was $45 total. Update_merge churn is plausibly a third of that. Question for panel: is `update_merge` the right strategy for cross-chapter dedup, or should we accumulate per-chapter L2 stubs first, then run a single offline merge pass (cheaper, batchable)?

### B8. `confidence` and `source_refs` create-time semantics

`upsert_concept_page` action="create" sets `confidence = confidence` (caller's, can be None) and `source_refs = []` (always empty). `mentioned_in = [source_link]` is the populated field. So:
- `source_refs` is dead-on-create — never populated, never read in canonical paths. Frontmatter clutter.
- `confidence` accepts None → frontmatter has `confidence: null` (YAML serialization).

The phase-b-reconciliation prompt and concept-extract prompt may reference these fields — would need cross-check with `.claude/skills/textbook-ingest/prompts/`.

### B9. Anchor equivalence tolerance (`_anchor_equiv`) — design choice not validated

`run_s8_preflight.py:218-243` `_anchor_equiv` does NFKC + curly→ASCII apostrophe + curly→ASCII quote + en/em dash → hyphen. Tested at `tests/scripts/test_assemble_body.py:152-211` for: curly vs ASCII apostrophe, em vs en dash, real word change rejected.

Question: what other LLM drift modes does the LLM produce? Whitespace collapse? Trailing punctuation drop? Number-formatting (`1.5` vs `1·5`)? `_anchor_equiv` only knows the ones tested. New drift mode → silent acceptance OR silent rejection (depending on whether normalization aligns by accident).

### B10. Walker `tables` extraction is unused

`shared/source_ingest._extract_tables` produces `InlineTable(markdown, caption)` list. `ChapterPayload.tables` is set but `_assemble_body` doesn't reference it; Phase 1 prompt doesn't consume it. The walker spends compute extracting tables for no downstream use. Either remove from contract or wire into prompt for table-aware concept extraction.

### B11. `_alias_map.md` is write-only

Nothing reads it back into the pipeline — concept routing doesn't check existing aliases for a term. So if BSE ch1 routes "Adenine" to L1 (alias entry), then BSE ch5 mentions "Adenine" again, classifier re-runs from scratch with no memory. If 5 chapters route to L1, alias map gets 5 rows for the same term, but no concept page is ever promoted. The file is purely a passive index.

For Obsidian wikilink resolution: `_alias_map.md` is a flat markdown table. Obsidian doesn't resolve `[[Adenine]]` → row in that table — it only resolves to a `Adenine.md` file. So aliases as currently designed do not help wikilinks resolve at all. They're useful only if a future agent reads `_alias_map.md` to suggest concept page promotion (no such consumer exists today).

This makes Patch 2's "concept slug allows spaces" useful (`NADPH oxidase.md` exists) but the L1 fallback path useless for the user's actual goal (no red links in Obsidian).

## §6.5 Findings from parallel Claude downstream-perspective review (general-purpose subagent)

A second Claude pass (`general-purpose` subagent) read staged ch1/3/6 + concept pages from the Obsidian downstream user's POV. Brought several findings Codex round 1 missed:

### B12. Concept page fragmentation — duplicate pages for same biochemical entity

Within BSE alone (no SN ingested yet):
- `Concepts/ATP.md` AND `Concepts/Adenosine Triphosphate.md` — same molecule, two pages, neither aliases the other.
- `Concepts/Glycolysis.md` AND `Concepts/Anaerobic Glycolysis.md` — identical opening prose lifted from same ch1 paragraph (compare line 22 of each).
- `Concepts/Lactic Acid.md` AND `Concepts/lactate.md` — case-only difference; on Windows NTFS resolves unpredictably, on macOS+iCloud or Linux these are 2 truly distinct files.
- `Concepts/Phospholipid.md` (singular) AND `Concepts/phospholipids.md` (plural).

Root cause: `concept-extract.md` prompt at line 148 says "slug 用 kebab-case lowercase" but actual files are Title Case With Spaces. **No `canonicalize(term) → slug` layer between LLM concept extraction and `upsert_concept_page`**. LLM emits whatever surface form, dispatcher creates new file each variant. `_validate_slug` (Patch 2 relaxed) accepts. There's no upstream normalization.

Implication for SN ingest (about to run): SN mentions ATP → LLM may emit `{title: "Adenosine triphosphate (ATP)"}` as slug → 3rd ATP page born. `update_merge` (kb_writer.py:748) only fires if dispatcher chose `update_merge` action — which is based on slug string match. With unnormalized slugs, "merge" never gets the chance.

Severity: **HIGH** (cross-book ingest will compound, not fix).

### B13. `mentioned_in` reverse-backlink 96% incomplete

192 staged concept pages: only 7 have `mentioned_in` listing 2+ chapters; 185 are single-chapter. Empirical: `Concepts/ATP.md` `mentioned_in:` lists ch1 only — but `grep -ci "\bATP\b"` returns 40 occurrences in ch3 and 13 in ch6. Forward link (chapter appendix → concept page) works. Reverse link (concept → all chapters that mention it) is silently incomplete.

Root cause: Phase 2 dispatch only updates `mentioned_in` on the chapter currently being ingested. Subsequent chapters that reference an existing concept don't update its `mentioned_in` unless they trigger an `update_merge` (which requires the dispatcher to identify them as referring to the same concept — see B12 — or unless the term appears in their LLM-emitted wikilinks, which is intermittent).

`kb_writer.py:536` apparently has an `update_mentioned_in` function but it's not being called from the textbook ingest path — strict missing function call, not redesign.

Severity: **HIGH** (concept→chapter graph silently broken; 修修 asks "which chapters cover ATP?" gets wrong answer).

### B14. Frontmatter dual source of truth — already off-by-one in wild

`ch3.md` frontmatter has `wikilinks_introduced` (156 entries, lines 238–394). `ch3.md` body has `## Wikilinks Introduced` (157 entries, lines 1217–1375). **Already diverged by 1**. Both fields are independently constructed in `_assemble_body` and the YAML preamble. This is the dual-source-of-truth bug Codex round 1 hinted at, now confirmed in production output.

Plus other dead/inconsistent fields (downstream review):
- `schema_version: 2` for fresh `upsert_concept_page(action="create")` writes (kb_writer.py:667), but concept pages observed in wild already on `schema_version: 3` after `_patch_v3_frontmatter` runs. Future creates regress to 2.
- `source_refs: []` always empty (textbook ingest never populates).
- `aliases: []` always empty (LLM doesn't emit, ingest doesn't merge).
- `confidence: null` always (caller's `confidence` defaults to None).

Severity: **MEDIUM-HIGH** (frontmatter looks tidy but is misleading; agent reads will surface inconsistency).

### B15. `_alias_map.md` is downstream-useless

When ch3.md body says `[[fatty acid oxidation]]` and `Concepts/fatty acid oxidation.md` doesn't exist (just an alias_map row): Obsidian creates a blank stub `Concepts/fatty acid oxidation.md` on click — Obsidian doesn't read `_alias_map.md`. So Patch 1 (preventing auto-stub of `chapter12.xhtml.md`) only solved one variant; the alias-only red link variant still auto-stubs.

`_alias_map.md` is appended-on-encounter (sorted by ingest order, not alphabetical). Same term re-appended each chapter encounter — `adenosine diphosphate` shows on lines 5 and 63 (duplicate rows). Useful only to a Python script that knows to read it; useless to user, minimally useful to a Grep agent.

Severity: **HIGH** (the L1 fallback path doesn't actually help wikilink resolution in Obsidian).

### B16. Patch 3 over-correction: zero inline body `[[concept]]` wikilinks

Confirmed via grep on ch3.md: 347 wikilink occurrences total, all are figure embeds (`![[Attachments/...]]`), frontmatter list entries, or appendix list. Inline body has 0.

For agent search: `Grep "phosphocreatine" ch3.md` still works on plain text (term appears in body prose). But the LLM-friendly structured `[[…]]` graph signal is gone. Agent asking "what does ch3 say about phosphocreatine" gets only "appendix says ch3 introduces this term" — no anchor to the actual paragraph context.

Question for panel: was Patch 3's "remove all inline wikilinks" the right tradeoff vs visual cleanliness, or should we put SOME inline wikilinks back (e.g. first occurrence of each L2/L3 term per section)?

Severity: **MEDIUM** (depends on how heavily future agents will rely on `[[…]]` graph).

### B17. YAML anchors `&id001` / `*id001` in created/updated fields

Every concept page line 13–14: `created: &id001 2026-05-08\nupdated: *id001`. PyYAML's `safe_load` handles anchors, but some Obsidian plugins / `python-frontmatter` default loader may choke. Worth a sanity test pre-ship.

Severity: **LOW** (latent, may bite a downstream consumer).

## §7. Specific questions for panel auditors

Panel should give explicit verdicts on each:

**Q1.** Is the Path B walker-verbatim + LLM-metadata-only architecture fundamentally right, given the gate it produces is partly tautological by construction? Or is a single-LLM-call body-emission + structural validators a more honest design?

**Q2.** L1 fallback writes alias_map but Obsidian wikilinks don't resolve aliases. Should L1 always create a stub concept page (no body, just frontmatter), so wikilinks resolve? Or should L1 be removed and any term either get a real page or get its wikilink stripped from body? Compare 3 designs.

**Q3.** `update_merge` LLM-diff-merge per repeat: cost is potentially 30%+ of batch budget. Is per-chapter merge correct, or should we batch concept-merge into a single offline reconciliation pass after all chapters are ingested?

**Q4.** New finding B4 (single-chapter mode pollutes live `_alias_map.md`): is this a Patch 4a fix-and-ship, or a deeper signal that the staging-redirect pattern is fragile (multiple call sites, hardcoded paths)? Should we do a `vault_root` injection refactor instead?

**Q5.** Codex says ship-after-patch-4. Claude (post-downstream-review) says ship-after addressing **at minimum** B4 (alias staging) + B12 (concept canonicalization) + B13 (mentioned_in reconciliation) + Codex's appendix reconciliation, possibly also B14 (FM dual-source-of-truth) and B15 (alias_map → real concept stubs). Is this tractable as a 4-patch effort, or does it indicate the entire concept-dispatch contract needs rewriting before another batch run?

**Q5a.** **NEW post-downstream-review specific question**: should L1 fallback be replaced with "always create a minimal concept stub page" so Obsidian wikilinks always resolve? Tradeoff: ~50% more concept pages (most will be 1-line stubs), vs guaranteed zero red wikilinks. Or alternative: make `_alias_map.md` a dataview-readable table that Obsidian DOES resolve via aliases frontmatter on a single root page?

**Q5b.** **NEW post-downstream-review specific question**: B12 concept fragmentation (ATP + Adenosine Triphosphate, Glycolysis + Anaerobic Glycolysis, etc.) — proposed fix is upstream `canonicalize(term) → slug` layer + alias dictionary. Should this be implemented before SN ingest (which would compound fragmentation 2×) or is post-hoc deduplication acceptable?

**Q5c.** **NEW post-downstream-review specific question**: B16 zero inline body wikilinks — Patch 3 visual win vs LLM-graph-signal loss. Is the right design "appendix only" / "first-occurrence-per-section" / "all occurrences"? Different downstream consumers (修修 reading vs agent search) want different things.

**Q6.** Acceptance gate redesign: post-Patch-3, the verbatim rule is a Python self-test. What's the minimum gate that meaningfully gates ship? Codex suggested dispatch-health + wikilink-resolve. Claude additionally proposes: appendix-vs-frontmatter consistency + "no orphan stubs in concepts dir" check. Is this enough, or is the gate fundamentally re-thought (eg. test-set of 3 known-good chapters that must round-trip identically)?

**Q7.** Cost vs value: $45 burned on 5/7. What's the right per-chapter ingest budget that user should accept, and where should we instrument it?

## §8. Decision space

Three end-states under consideration:

**SHIP-NOW**: fix B4 (alias staging), accept the rest, ship 28 chapters. Cost ~$30, time ~3hr.

**PATCH-4-THEN-SHIP** (Codex recommendation, Claude lean): fix B4 + Codex's appendix reconciliation + acceptance gate hardening. Cost ~+$1 + 1.5 hr. Then ship 28 chapters at ~$30.

**ARCHITECTURAL-PAUSE**: address B6 (cross-chapter L3 promotion) and B7 (merge strategy) before any scale. Cost: 1-2 days of design + implementation. Then ship.

What does panel recommend? Justify against (a) opportunity cost — Stage 5/6/7 of CONTENT-PIPELINE blocked behind this, (b) sunk cost trap risk — 5/7 already taught us not to ship-then-fix, (c) the user's $10k OpenAI free credit (effectively makes API cost negligible for review iterations).
