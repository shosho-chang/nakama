## Section A — B4-B17 Confirmation Table

| ID | Confirm | Severity | Fix complexity | Rationale |
|---|---:|---:|---:|---|
| B4 alias map writes live in single-chapter mode | PASS | HIGH | trivial <10 lines | `run_s8_preflight` calls `_patch_kb_writer_to_staging()` but not `_patch_alias_map_to_staging()`, while `append_alias_entry()` hardcodes `KB/Wiki/_alias_map.md`; batch mode patches it separately. |
| B5 CJK/ASCII classifier brittleness | PARTIAL | MED | moderate <1 day | `_rule_freq_multi_section()` uses `\b<term>\b`, which fails for embedded CJK terms without separators; headings/bold exact-match still work, so this is not a current English-book blocker. |
| B6 `source_count=1` hardcoded | PASS | HIGH | major multi-file | `run_phase2_dispatch()` always calls `route_concept(..., source_count=1)`, making L3 unreachable in both preflight and batch paths. |
| B7 `update_merge` cost path | PASS | MED | moderate <1 day | `upsert_concept_page(create)` falls back to `update_merge` on existing slug and calls `_ask_llm`; current fragmentation reduces hits, but canonicalization will increase them. |
| B8 dead `confidence` / `source_refs` fields | PASS | LOW | trivial <10 lines | create frontmatter writes `source_refs: []` and `confidence: null`; neither is meaningfully populated by this ingest path. |
| B9 narrow `_anchor_equiv` tolerance | PASS | LOW | moderate <1 day | The whitelist handles NFKC, curly quotes, dash variants; acceptable as a fail-fast boundary, but it is not corpus-derived. |
| B10 tables extraction unused | PARTIAL | LOW | trivial <10 lines | tables are extracted and summarized into the Phase 1 prompt by caption/row count, but full table markdown is not used beyond the verbatim body. |
| B11 `_alias_map.md` write-only | PASS | HIGH | moderate <1 day | dispatcher never reads alias history for routing/promotion, and Obsidian does not resolve `[[term]]` from a row in `_alias_map.md`. |
| B12 concept fragmentation | PASS | HIGH | major multi-file | staged files confirm `ATP.md` and `Adenosine Triphosphate.md`; the old prompt still says kebab-case slug while S8 writes raw surface terms. |
| B13 incomplete `mentioned_in` | PASS | HIGH | moderate <1 day | `ATP.md` / `Adenosine Triphosphate.md` list only ch1 despite ch3 references; `update_mentioned_in()` exists but is not wired into S8 reconciliation. |
| B14 frontmatter/body/schema drift | PASS | MED | moderate <1 day | ch3 frontmatter and appendix diverge: body includes `dehydration` / `concentration`, while frontmatter duplicates `nucleus`; concept create starts schema v2 then patch bumps v3. |
| B15 alias map downstream-useless | PASS | HIGH | moderate <1 day | L1 alias entries still leave visible `[[term]]` red in Obsidian if emitted; alias rows are only useful to future custom tooling. |
| B16 zero inline body wikilinks | PASS | MED | moderate <1 day | `_assemble_body()` only appends appendix links; body remains clean, but paragraph-level graph signal is intentionally gone. |
| B17 YAML anchors in dates | PASS | LOW | trivial <10 lines | created/updated reuse the same `date` object, and PyYAML emits `&id001` / `*id001`; staged concept pages confirm it. |

## Section B — Panel Question Verdicts

**Q1.** Verdict: keep Path B.  
Top recommendation: walker-verbatim + LLM metadata-only is the right fidelity architecture, but stop treating verbatim match as a ship gate.  
Tradeoff: the body gate becomes mostly a Python self-test.  
Alternative: single LLM body emission only for a small experimental branch, not production.

**Q2.** Verdict: L1 should not be visible wikilinks.  
Top recommendation: post-dispatch rewrite the appendix so only resolved L2/L3/canonical pages remain `[[linked]]`; L1 becomes plain-text “Aliases Recorded.”  
Tradeoff: less graph density.  
Alternative: create explicit `alias_stub` pages for L1 only if zero visible red links outranks corpus cleanliness.

**Q3.** Verdict: stop per-chapter `update_merge` as default.  
Top recommendation: ingest chapters first, update backlinks/noop for existing canonical pages, then run one offline merge pass.  
Tradeoff: concept bodies lag until reconciliation.  
Alternative: allow `update_merge` only when crossing L2→L3 or after manual allowlist.

**Q4.** Verdict: B4 is quick-fix plus design smell.  
Top recommendation: inject target wiki root into alias/concept writers instead of monkey-patching two call sites.  
Tradeoff: touches shared APIs.  
Alternative: copy `_patch_alias_map_to_staging()` into preflight immediately.

**Q5.** Verdict: tractable patch set, not fundamental rewrite.  
Top recommendation: patch B4, post-dispatch reconciliation, minimal canonicalization, mentioned_in reconciliation, and gate hardening before next ingest.  
Tradeoff: delays scale by roughly a day.  
Alternative: full architectural pause only if canonicalization cannot be bounded.

**Q5a.** Verdict: do not always create L1 stubs yet.  
Top recommendation: remove L1 from visible wikilinks and keep it as candidate metadata.  
Tradeoff: no page for low-confidence terms.  
Alternative: create `status: alias_stub` pages in a quarantined namespace.

**Q5b.** Verdict: canonicalization before SN ingest.  
Top recommendation: seed an alias dictionary for ATP/ADP/lactate/glycolysis/plurals/case, NFKC-normalize slugs, and migrate staged duplicates first.  
Tradeoff: wrong merges are possible.  
Alternative: ship BSE only, then do human-reviewed dedupe before SN.

**Q5c.** Verdict: appendix-only for now.  
Top recommendation: keep body visually clean and add section-scoped appendix entries instead of inline wikilinks.  
Tradeoff: weaker paragraph graph signal.  
Alternative: first occurrence per section for L2/L3 only.

**Q6.** Verdict: current gate is not sufficient.  
Top recommendation: add dispatch-health, wikilink-resolution, frontmatter-vs-appendix consistency, staging-isolation, and one golden end-to-end chapter.  
Tradeoff: more false blocks.  
Alternative: keep current 4-rule gate as smoke test only.

**Q7.** Verdict: instrument by phase/action, not just chapter.  
Top recommendation: record Phase 1, dispatch, update_merge, retries, and total cost per chapter; alert above about `$2/chapter` or any unexpected `update_merge` burst.  
Tradeoff: more bookkeeping.  
Alternative: keep only the `$50` batch cap.

## Section C — Audit Gemini’s Audit

I agree with Gemini that wikilink resolution, maturity routing, canonicalization, and backlink integrity are broken contracts, not isolated bugs.

I push back on the **CJK BLOCKER** label for this batch. The code supports “future multilingual high risk,” not “block current 28 English chapters.” `\b` makes frequency matching unreliable for CJK prose, but headings, bolded terms, and explicit definitions can still trigger. Severity: HIGH before Chinese-source ingest; MED for BSE/SN.

The “systemic contract failure” frame is useful for L1, L3, and gates. It is a distraction when applied to “verbatim is a lie” because appending a clearly separated appendix does not violate body fidelity if validators model it honestly.

Gemini’s Path C is directionally right but under-specified. “Canonicalization layer + graph-aware dispatcher” needs exact slug rules, alias schema, merge policy, migration plan, and golden fixtures. Without those, it is an architecture slogan.

Always-create-stub is risky. Stub explosion is real: 50 books × many chapters × ~20 L1 terms means thousands to tens of thousands of low-value files. Manageable only if quarantined as `alias_stub`; otherwise it recreates the earlier empty-stub crisis under a cleaner name.

## Section D — Fresh Push-Back On Claude And Gemini

Claude is over-weighting some acceptable debt. B8, B9, B10, and B17 should not block one-book validation. B16 is a product tradeoff, not automatically a bug. B5 is not a current English-book blocker. B7 cost is real, but fragmentation currently suppresses update_merge calls; the quality bug is worse than the cost bug.

Claude also under-calls that batch spot-check has its own alias-map bug: it checks `Concepts/alias_map.md`, while the staging patch writes `KB/Wiki.staging/_alias_map.md`.

Gemini over-corrects toward rewrite. The deterministic classifier is brittle, but traceable rules plus a reconciliation pass are a reasonable near-term design. Deleting alias_map and stubbing every L1 term optimizes “no red links” while damaging corpus hygiene.

Both missed or under-specified:

- SQLite advisory locks are optional and never passed by S8; future parallel dispatch can race on concept create/update, and `_write_page_file()` is non-atomic.
- Figure attachment idempotency is shallow: EPUB extraction overwrites existing files but never reconciles stale/orphaned attachments or verifies hashes/existence in the gate.
- Checkpoint/resume is only a progress JSON snapshot; rerun restarts from chapter 1 and can re-trigger concept churn.
- ADR-019’s two-file raw/annotated source pattern drifted: ADR-020 has Raw outside Wiki plus a hybrid source page with machine appendix, but no explicit annotated sibling contract.
- `verify_staging.py` still runs the old 4-rule gate; it does not check dispatch health, red links, alias staging, or frontmatter/body reconciliation.
- No end-to-end golden chapter exists.
- No migration plan exists for already-staged concept pages: duplicates, schema anchors, aliases, and `mentioned_in` need repair before scale.

## Section E — Decision Recommendation

Recommendation: **patch then ship 1 book**, not 28 chapters, not fundamental rewrite.

Ranked patch list by burn probability × fix cost:

1. Fix staging isolation: alias writer must target staging in preflight and batch.
2. Build chapter appendix/frontmatter from post-dispatch results; no visible L1 wikilinks.
3. Extend `verify_staging.py` with dispatch-health, wikilink-resolution, FM/body consistency, and alias staging checks.
4. Add minimal canonicalization + alias map for known biochemical synonyms/case/plurals; migrate the staged duplicates.
5. Reconcile `mentioned_in` across staged chapters and canonical concept pages.
6. Disable default `update_merge` during batch; queue offline merge and instrument per-action costs.
7. Fix schema/date serialization: no YAML anchors, no `confidence: null`, no dead `source_refs` unless populated.
8. Add real resume semantics using the progress snapshot.
9. Add concept write locking or keep batch explicitly sequential and document that invariant.
10. Add one golden end-to-end chapter fixture plus an Obsidian eyeball checklist.

Do not ship all 28 now. Patch the high-burn contract issues, ingest BSE only, validate in Obsidian, then ingest SN after canonicalization/backlink behavior is proven.