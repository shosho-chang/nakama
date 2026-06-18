1. CODE GROUNDING

D-A is overstated. `agents/robin/ingest.py:135-155` reads the raw body and passes `content` into `_generate_summary`. `_generate_summary` at `agents/robin/ingest.py:296` does not call `_truncate_at_boundary`; for `len(content) <= LARGE_DOC_THRESHOLD` it passes `content=content` into `load_prompt` at `agents/robin/ingest.py:309-325`. For larger docs it map-reduces via `_map_reduce_summary(content=content)` and `chunk_document(content)` at `agents/robin/ingest.py:330-350`. So: full body is the input, no direct truncation, but large docs are chunk-summary-reduce, not one full-text LLM call.

The “Literature Note renders from annotations” claim is correct. `ingest()` calls `_render_literature(annotation_slug, source_type)` at `agents/robin/ingest.py:150-151`; `_render_literature()` calls `write_literature_note(annotation_slug, ...)` at `agents/robin/ingest.py:243-253`; `shared/literature_writer.py:527-548` loads the annotation store and writes `KB/Literature/{slug}.md`.

The “Concept extraction runs on full text” claim is false. `ingest()` calls `_get_concept_plan(summary_body, ...)` at `agents/robin/ingest.py:199-202`. `_get_concept_plan()` only passes `summary=summary_body` into `load_prompt("robin", "extract_concepts", ...)` at `agents/robin/ingest.py:414-438`. `prompts/robin/extract_concepts.md:6-8` is explicitly `## Source Summary {summary}`.

D-B’s Permanent guard exists, but ADR-045 does not add it. `shared/permanent_layer.py:33-37` defines `KB/Permanent` and allowed bookkeeping keys only; `assert_not_permanent_target()` rejects agent targets at `shared/permanent_layer.py:69-80`; `update_permanent_bookkeeping()` rejects illegal keys like `status`/`body` at `shared/permanent_layer.py:134-168`; `shared/promotion_targets.py:89` applies the tripwire.

D-C-4 is wrong as written. `.claude/skills/kb-search/SKILL.md` and `.claude/skills/kb-search/scripts/search.py` are thin HTTP wrappers over `/kb/research`, not a direct FTS5 wrapper. The endpoint calls `search_kb(query, get_vault_path())` at `thousand_sunny/routers/robin.py:1527-1533`; default `search_kb(..., engine="hybrid")` delegates to `shared.kb_hybrid_search`, and `shared/kb_indexer.py:9-10` indexes `KB/Wiki`, `KB/Annotations`, and `KB/Permanent`.

2. DRIFT DETECTION

ADR-045 mostly restates ADR-043, but calls it “two independent brains.” ADR-043 already says `KB/Wiki/Concepts/` is candidate and `KB/Permanent/` is authoritative/human-owned at `docs/decisions/ADR-043-centaur-zettelkasten-permanent-layer.md:34-38`. The reconciliation is sound only if “Robin’s brain” means candidate workspace, not a second authority tier.

The reconciliation is currently hand-wavy because retrieval already mixes tiers. `shared/kb_hybrid_search.py:286-308` explicitly boosts matching `KB/Permanent/` hits ahead of Wiki hits. If `kb-query` is a wrapper over existing `kb-search`, it is not Wiki-only unless ADR-045 adds a scope filter.

Centaur v0.2 §7 and §8 are stricter than ADR-045’s prose. §7 red line 1 bars AI from `KB/Permanent/` body/status at `docs/plans/centaur-zettelkasten/Centaur-Zettelkasten-規格-v0.2.md:126-132`; §8 downgrades Phase 5 to bookkeeping/mirror/defer suggestions, not Permanent edits, at lines `138-141`. ADR-045 aligns in intent, but does not specify the actual write surface for “defer suggestions on Concept pages.”

CONTENT-PIPELINE Stage 4 Line 2 forbids ghostwriting atomic content: `CONTENT-PIPELINE.md:33-36` limits Stage 4 to context/scaffold/evidence/outline assist. ADR-045’s `insight`, `recommend`, and `daily-review-assist` need explicit output contracts or they will drift into Line 2 drafting.

3. NUMERICAL / FACTUAL CLAIMS

The proposed 8-skill family exists only in ADR-045. In this checkout there is no `.claude/skills/ingest`, `kb-query`, `kb-lint`, `gap-scout`, `insight`, `recommend`, `daily-review-assist`, or `taste-profile`.

`.claude/skills` has 16 directories, but only 15 `SKILL.md` files; `domain-model` has `ADR-FORMAT.md` and `CONTEXT-FORMAT.md`, no `SKILL.md`. If ADR-045 says “15 skills,” clarify it means actual skill definitions, not directories.

D-C-5 is stale/misleading. `.claude/skills/textbook-ingest` does not exist. `.agents` does not exist. The remaining textbook artifacts are docs/memory/prompts/scripts, e.g. `docs/capabilities/textbook-ingest.md`, `prompts/robin/categories/textbook`, and `scripts/Invoke-IngestTextbook.ps1`.

Zoro Scout is overstated. `agents/zoro/brainstorm_scout.py` declares `SignalSource = ... "pubmed"` and comments mention Reddit/YouTube/PubMed, but `gather_signals()` currently only calls `_gather_trends_signals()`.

`KB/Wiki/Concepts/` live-vault count is not verifiable here. There is no `VAULT_PATH` env var, and this repo does not contain a real `KB/Wiki` tree, only test fixtures.

4. ASSUMPTION PUSH-BACK

“Two brains” is not a real architecture unless it changes enforcement. Right now the enforceable architecture is ADR-043’s candidate workspace plus human Permanent layer. Calling `KB/Wiki/` “Robin’s brain” adds ceremony but no new invariant.

The “attention mirror” premise is not implemented. Annotations render Literature, but concept extraction sees `summary_body`, not annotations plus full text. If annotations are supposed to weight extraction, the pipeline needs an explicit annotation payload into `_get_concept_plan()`.

The skill family assumes skills are boundaries. They are not. Existing skills like `kb-search` and `seo-audit-post` are wrappers over code/API. The real boundaries are endpoint scopes, write guards, prompt inputs, output schemas, and lint gates.

D-D is partly over-cautious. Waiting for real ingests before `gap-scout`/`recommend` is reasonable. Waiting for `kb-query` is not: the existing search already works over Permanent/Annotations/Wiki, so the real issue is tier scoping, not corpus emptiness.

5. ALTERNATIVES

Use “Candidate Workspace + Authoritative Permanent” instead of “two brains.” Keep ADR-043 as the architecture, and define ADR-045 as interface layering: `ingest` fills candidate/evidence stores, `query` retrieves with explicit `scope={permanent,candidate,all}`, and review/assist tools produce non-authoritative suggestions.

A stronger alternative is an evidence-ledger framing: `KB/Raw`, `KB/Annotations`, `KB/Literature`, and `KB/Wiki` are append/merge evidence surfaces; `KB/Permanent` is the only authored knowledge layer. Robin never “has a brain”; Robin maintains candidate evidence and task queues.

6. VERDICT

Reject as written.

1. In `docs/decisions/ADR-045-robin-two-brain-and-kb-skill-family.md §D-A`, replace “Concept extraction runs on full text” with “Concept extraction runs on `summary_body`; summary generation is full-body or map-reduce.”

2. In `§D-A`, either remove the “attention mirror” justification or change `agents/robin/ingest.py` so `_get_concept_plan()` receives annotation/Literature excerpts explicitly.

3. In `§D-B`, rename “two independent brains” to “candidate workspace + authoritative Permanent layer,” and state that ADR-045 creates no new authority tier beyond ADR-043.

4. In `§D-C-4`, fix `kb-query`: either document it as mixed-tier/permanent-first, or add a scoped search contract across `agents/robin/kb_search.py`, `/kb/research`, and `.claude/skills/kb-search/scripts/search.py`.

5. In `.claude/skills/kb-search/SKILL.md` and `scripts/search.py`, update stale “KB/Wiki only / Haiku-ranked” language to match current `engine="hybrid"` and `KB/Permanent` indexing behavior.

6. In `§D-C-2`, define `daily-review-assist` as a wrapper over `agents/robin/daily_review.py` and N523 bundle/UI, with no duplicate LLM judgment pipeline.

7. In `§D-C-3`, remove PubMed/arXiv/Zoro implementation claims or add a future-slice note; current Zoro Scout is Google Trends only.

8. In `§D-C-5`, correct the textbook-ingest facts: no `.claude/skills/textbook-ingest` or `.agents/skills/textbook-ingest` exists; name the actual remaining docs/prompts/scripts if they matter.

9. In `§D-D`, split waves by dependency: ship `ingest`; allow `kb-query`/`kb-lint` once scoped; delay only `gap-scout`/`recommend`/`insight` until real Wiki ingests exist.