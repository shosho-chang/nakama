---
title: ADR-020 textbook ingest v3 — patch effort v2 (post-panel)
date: 2026-05-08
authors:
  - Claude (Opus 4.7 1M context, primary author)
  - Codex round 1 (initial audit)
  - Codex round 2 / gpt-5.5 high reasoning (deep audit + new findings)
  - Gemini 2.5-pro (multilingual + systemic-contract lens)
panel_skill: multi-agent-panel (5-step)
inputs:
  - docs/research/2026-05-08-claude-textbook-ingest-v1-draft.md
  - docs/research/2026-05-08-codex-textbook-ingest-audit-round1.md
  - docs/research/2026-05-08-codex-textbook-ingest-audit-round2-gpt55.md
  - docs/research/2026-05-08-gemini-textbook-ingest-audit.md
  - docs/research/2026-05-08-integration-matrix.md
status: pending v2 sign-off from Codex + Gemini → user final decision
---

# Textbook ingest v3 — patch effort v2 draft

## §0. v1 → v2 change summary

v1 listed 14 issues (B4–B17), 7 panel questions, and 3 decision options (ship-now / patch-4-then-ship / architectural pause). v2 incorporates panel adjudication via 27-row integration matrix:

- **+ 7 new findings** from Codex r2 (B18–B24): placeholder string regression, advisory lock race, figure basename collision, batch checkpoint no-resume, ADR-019/020 drift, verify_staging script bug, 192-page migration plan
- **+ 1 new finding** from Gemini: game-theoretic LLM wikilink over-emit (re-prompt to demand prioritization)
- **B5 CJK severity contradiction resolved**: ship English (BSE/SN) now, DEFER CJK fix to before any Chinese book — Codex r2 pushed back successfully on Gemini's BLOCKER classification for English batch
- **B15 alias-handling resolved by 2-of-3 (mod)**: demote L1 to plain text in body appendix (Codex r1+r2) over Gemini's "always-create-stub" (rejected as poison-search risk)
- **Decision frame collapsed**: from 3 options to 1 — patch P0 → ship BSE-only to staging → user UAT → patch P1 → ship SN → publish to live Wiki

## §1. Patch list (4-tier priority)

### P0 — must fix before any ship (including staging UAT)

(Updated 2026-05-08 post Codex r3 sign-off mods: golden fixture promoted P1→P0; placeholder-string check folded into P0.5 gate; P0.1 file attribution corrected.)

| # | Patch | Files | Complexity | Why |
|---|---|---|---|---|
| P0.1 | Alias-map staging in preflight + verify_staging script path fix | `scripts/run_s8_preflight.py:1010` (add `_patch_alias_map_to_staging` call); `scripts/run_s8_batch.py:382` batch spot-check (`alias_map.md` path is wrong — should be `_alias_map.md` in `Wiki.staging`) | trivial | B4: live KB pollution; B23: batch spot-check + verify_staging script bug |
| P0.2 | Canonicalization v0 + Patch 2 NFKC | new `shared/concept_canonicalize.py`; `shared/kb_writer.py:_validate_slug`; `scripts/run_s8_preflight.py:_slug_from_term` | moderate | B12 fragmentation (ATP + Adenosine Triphosphate); covers Codex r1's NFKC concern as side-effect |
| P0.3 | Reverse-backlink reconciliation | new pass in `run_phase2_dispatch` after dispatch loop | moderate | B13 96% incomplete `mentioned_in` |
| P0.4 | Unified frontmatter + appendix from dispatch log | `scripts/run_s8_preflight.py:_assemble_body` + post-dispatch reassembly | moderate | B14 156≠157 dual-source-of-truth + B15 L1 demote-to-plain-text |
| P0.5 | Acceptance gate hardening (file-based, scans disk not writer internals — Codex r3 §2.3) | `scripts/run_s8_preflight.py:compute_acceptance` | moderate | 8-condition gate: dispatch 0 errors / every visible `[[…]]` resolves to file on disk / FM-body wikilink count consistent / 0 writes to live `KB/Wiki/` / **0 placeholder strings on disk including `_(尚無內容)_` / "Will be enriched" / "phase-b-reconciliation"** (B18 absorbed) / 0 canonical-slug collisions reported by canonicalize layer / golden chapter fixture round-trip equality |
| P0.6 | Phase 1 prompt: prioritized concept extraction | `.claude/skills/textbook-ingest/prompts/chapter-source.md` | trivial | Gemini §3: LLM emit "5-7 most critical + justify" not flat noise list |
| P0.7 | Golden chapter end-to-end fixture (PROMOTED from P1.5) | `tests/integration/test_golden_chapter.py` + `tests/fixtures/golden/bse-ch3-expected/` | moderate | universal panel agreement; P0.5 gate condition 8 depends on this existing |

**P0 estimate**: ~2-3 days dev + tests (golden fixture adds ~half day); cost ~$0 (no LLM during dev); re-ingest BSE 11 chapters ~$5.

### P1 — before SN ingest

| # | Patch | Files | Complexity | Why |
|---|---|---|---|---|
| P1.1 | Cross-chapter L3 promotion pass | new `scripts/run_l3_promotion.py` | moderate | B6 source_count=1 hardcode |
| P1.2 | Offline concept-merge pass | new `scripts/run_concept_reconcile.py` (replaces per-chapter `update_merge`) | moderate | B7 cost path |
| P1.3 | ~~Placeholder string invariant check~~ — **FOLDED INTO P0.5** (Codex r3 §1) | — | — | absorbed by gate condition 6 |
| P1.4 | Migration script for 192 staged concepts. **Mandatory before live `KB/Wiki/` publish AND before any run that reads/reuses the staged 192 pages** (Codex r3 §1 clarification) | new `scripts/migrate_staged_concepts.py` (one-shot: dedupe ATP variants, case/plural variants, rebuild backlinks from `_alias_map.md` rows) | major (one-shot) | B24 |
| ~~P1.5~~ | ~~Golden chapter end-to-end fixture~~ — **PROMOTED TO P0.7** (Codex r3 §1) | — | — | gate condition 8 depends on it |

**P1 estimate**: ~2-3 days dev + tests; SN re-ingest ~$15.

### P2 — post-1-book UAT

| # | Patch | Files | Complexity | Why |
|---|---|---|---|---|
| P2.1 | Patch 1 regex hardening (nested bracket / multi-line / escaped]) | `shared/source_ingest.py` | trivial | Codex r1 A |
| P2.2 | YAML anchor + dead FM field cleanup | `shared/kb_writer.py:upsert_concept_page` create-path frontmatter | trivial | B17 + B8 |
| P2.3 | Advisory lock plumbing in batch | `scripts/run_s8_batch.py` + `shared/concept_dispatch.dispatch_concept` callers | moderate | B19 future parallel race |
| P2.4 | Figure attachment idempotency + manifest | `shared/raw_ingest.py` figure copy path | moderate | B20 cross-EPUB collision |
| P2.5 | Batch checkpoint resume semantics | `scripts/run_s8_batch.py` checkpoint logic | moderate | B21 |

### P3 — latent / follow-up

| # | Patch | Why |
|---|---|---|
| P3.1 | ADR-019/020 contract reconciliation | doc-level; B22 |
| P3.2 | ~~CJK classifier rules~~ — **CANCELLED for textbook path**: all future textbook ingest is English-only per user (2026-05-08); Chinese-source ingest happens via separate ebook + web-article pipelines (TBD). Gemini's expanded zh definition patterns recorded for those future pipelines but not applicable here. |

## §2. Architectural decisions (from panel adjudication)

### Path B vs Path C

**Decision**: keep Path B (walker verbatim + LLM metadata-only). Reject Gemini's Path C re-architecture.

**Rationale**: Codex r1 + r2 both argued Path B is correct; the failures are not in the body-vs-metadata split but in the unverified contracts BETWEEN walker / dispatch / assembler. The fix is "appendix built from dispatch result" (P0.4), not architectural rewrite. Gemini's Path C language ("walker → LLM rich metadata → canonicalization → graph-aware dispatcher → assembler from dispatch result") is essentially Path B + P0.2 + P0.3 + P0.4 in different framing. We adopt the practical patches without the rewrite.

### L1 alias handling

**Decision**: demote L1 wikilinks to plain text in body appendix (`## Aliases Recorded` plain bullet list). Keep `_alias_map.md` as low-value index for now. Reject "always-create-stub" for L1.

**Rationale**: 2-of-3 (Codex r1 + r2) over Gemini. Codex r2 specifically warned 50 books × ~20 L1/chapter = thousands of empty stubs that "poison search" by inflating the concept-page corpus with noise. Plain-text demotion preserves the term as readable + greppable without polluting the resolvable concept graph. Revisit if user reports plain-text mentions are unsearchable.

### Acceptance gate (post-Path-B redesign)

**Decision**: 7-condition gate (P0.5). Replace the partly-tautological 4-rule gate.

The 7 conditions:
1. Dispatch log: zero `dispatch-error`, zero `ingest-fail`, zero `classifier-skipped`
2. Every visible body `[[concept]]` (in appendix `## Wikilinks Introduced` only — body has no inline links per Patch 3) resolves to an existing concept page on disk
3. Frontmatter `wikilinks_introduced` count == body appendix `[[…]]` count (single-source-of-truth via dispatch log)
4. Zero writes to live `KB/Wiki/` or `KB/Wiki/_alias_map.md` during ingest (assertion at end)
5. Zero `_(尚無內容)_` placeholder + zero "Will be enriched" + zero "phase-b-reconciliation" in any concept page body (extends `_check_hard_invariants`)
6. Zero canonical-slug collisions in dispatched concepts (canonicalize layer reports collisions)
7. Golden chapter fixture (BSE ch3) round-trip equality post-ingest

### Decision frame: ship phased

**Decision**: patch P0 → re-ingest BSE 11 chapters → user UAT in Obsidian → patch P1 + golden test → ingest SN 17 chapters → final gate pass → publish to live `KB/Wiki/`.

**Rationale**: 5/7 burn lesson is "validate-with-eyeball before scale". Single-book staging UAT is the cheapest honest validation. SN ingest gated on user UAT pass. Live Wiki write gated on second gate pass.

Reject:
- Ship 28 now (Codex r1 patch-4-then-go is too aggressive given B12/B13/B14 are HIGH and unfixed)
- Architectural pause / fundamental rewrite (Gemini) — overkill given English batch is workable with P0 patches; rewrite cost > UAT cost

## §3. Defer decisions

- **B5 CJK in textbook ingest pipeline — DEFERRED PERMANENTLY** (user clarification 2026-05-08): all future textbook ingest sources are English-language only. Chinese sources will only enter via separate pipelines (general ebook ingest + web article ingest), not via this `run_s8_preflight` / `run_s8_batch` textbook path. So `concept_classifier`'s CJK rules need not apply to the textbook path. The expanded zh definition patterns (`是指`, `即`, `也就是說`, `又稱`, `的定義是`) Gemini supplied are still recorded for the general-ebook / web-article pipelines when those are built, but B5 is a P3-permanent-defer for the textbook pipeline (no fix required).
- **B16 inline body wikilinks** — keep appendix-only for English ship; revisit if user reports search regression in UAT
- **B19/B20/B21** — P2 follow-up tickets; not blockers for English single-book ship

## §4. Audit trail

This v2 draft cites the integration matrix at every decision. Specific traces:

- §1 P0.1 ← row 1 + row 19 of matrix (Codex r2 confirm + script bug)
- §1 P0.2 ← row 2 + row 21 (universal Codex+Gemini canonicalize, Patch 2 NFKC piggyback)
- §1 P0.3 ← row 3 (universal mentioned_in fix)
- §1 P0.4 ← row 5 + row 4 (FM-body consistency + L1 demote)
- §1 P0.5 ← row 6 (universal acceptance gate hardening)
- §1 P0.6 ← row 12 (Gemini single-source: prompt change)
- §1 P1.1 ← row 7 (universal L3 promotion)
- §1 P1.2 ← row 10 (Codex r2 cost path)
- §1 P1.3 ← row 14 (Codex r2 placeholder regression)
- §1 P1.4 ← row 20 (universal migration plan)
- §1 P1.5 ← row 27 (universal golden test)
- §2 Path B ← row 9 (2-of-3 keep B, reject Gemini Path C)
- §2 L1 alias ← row 4 (2-of-3 demote-to-plain-text, reject Gemini always-stub)
- §2 Decision frame ← row 26 (Codex r2's framing adopted)

## §5. Open questions for v2 sign-off

When this v2 goes back to Codex + Gemini for sign-off, address:

1. Is P0 list complete or did v2 drop a critical patch?
2. Is "patch P0 → ship BSE only → UAT" the right pace, or is "patch P0+P1 then ship BSE+SN together" more honest given panel learning?
3. Is the 7-condition gate (§2) the minimum honest gate, or are we still missing a check that would have caught the 5/7 burn?
4. Is rejecting Gemini's Path C the right call, or is panel re-evaluating after seeing all 24 Bs?
5. Should we run the panel a 3rd round on v2, or is 2 rounds + integration matrix sufficient?

## §6. User decision points

修修, after panel signs off on v2, you decide:

1. **Approve v2 patch plan** as written → I implement P0 → re-ingest BSE → you UAT
2. **Approve with modifications** → tell me which P0/P1 items to drop / promote / reorder
3. **Reject v2** → run panel round 3 on v1+v2 with different model lens (Grok? local model?)

Cost projection if approved: ~2 days dev + ~$5 BSE re-ingest + your 1-week UAT + ~2 days dev + ~$15 SN ingest + final UAT. Total wall ~2 weeks; total OpenAI/Anthropic spend ~$25 (well within $10k credit).
