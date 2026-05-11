---
title: ADR-020 textbook ingest v3 — multi-agent panel integration matrix
date: 2026-05-08
sources:
  - claude_v1: docs/research/2026-05-08-claude-textbook-ingest-v1-draft.md
  - codex_r1: docs/research/2026-05-08-codex-textbook-ingest-audit-round1.md
  - codex_r2_gpt55: docs/research/2026-05-08-codex-textbook-ingest-audit-round2-gpt55.md
  - gemini_r1: docs/research/2026-05-08-gemini-textbook-ingest-audit.md
---

# Integration matrix

Rows ordered by P(future-burn) × cost-to-fix (high → low impact).

| # | Topic | Claude v1 | Codex r1 | Codex r2 (gpt-5.5) | Gemini r1 | Pattern | Resolution | Trace |
|---|---|---|---|---|---|---|---|---|
| 1 | **B4 alias_map preflight pollutes live KB** | HIGH, must fix before scale | (not in r1, found later) | PASS confirm; HIGH; trivial fix; "B4 is patch-now AND design smell — replace monkeypatches with explicit vault injection" | (covered only generically as "contract failure") | universal | **adopt** as P0; immediate fix preflight call site + investigate vault_root injection refactor as P2 | Claude §6 B4, Codex r2 §A B4 §B Q4 |
| 2 | **B12 concept fragmentation** (ATP + Adenosine Triphosphate as 2 pages) | HIGH; canonicalize layer | not flagged in r1 | PASS confirm; HIGH; major multi-file; canonicalize v0 = NFKC + casefold + plural/acronym + seed alias dict; before SN ingest | HIGH; canonical entity model with frontmatter `aliases: [ATP, 腺苷三磷酸]`; this is Path C cornerstone | universal | **adopt** with mod: implement codex r2 v0 normalization + Gemini's frontmatter aliases dict; before SN ingest | Codex r2 §A B12 §B Q5b, Gemini §1.4 §6.1 |
| 3 | **B13 mentioned_in 96% incomplete** | HIGH; missing function call | (not in r1) | PASS confirm; HIGH; moderate; "reconcile mentioned_in for existing concepts without triggering LLM update_merge" | "graph-aware dispatcher updates existing concept's mentioned_in" — top-3 fix | universal | **adopt** as P0; Phase 2 dispatch must update existing pages' `mentioned_in` directly without LLM call | Claude §6 B13, Codex r2 §A B13 §E #5, Gemini §6.3 |
| 4 | **B15 alias_map downstream-useless / red links still happen** | HIGH | A: appendix reconciliation; replace L1 wikilinks with "Aliases Recorded" plain-text list | "remove L1 from `[[…]]` output; list as plain mentions; only L2/L3 get resolving links" | "delete `_alias_map.md` entirely; L1 → always-create-stub concept page" | 2-of-3 (Codex r1+r2 align "demote-to-plain-text"; Gemini wants "always-stub") | **mod**: adopt Codex's "demote L1 wikilinks to plain text in body appendix" as P0; Gemini's always-stub deferred — codex r2 warns stub explosion can poison search; revisit if user reports plain-text mentions are unsearchable | Codex r1 B, Codex r2 §A B15 §B Q5a, Gemini §6.2 |
| 5 | **B14 frontmatter dual source of truth** (already 156≠157 in production) | HIGH | (general appendix-reconciliation suggestion in r1 §C) | PASS confirm; HIGH; "appendix and frontmatter wikilinks_introduced from single source = dispatch log" | "unify frontmatter and appendix generation, top-4 fix" | universal | **adopt** as P0; both fields generated from final dispatch log post-Phase-2 | Claude §6 B14, Codex r2 §A B14 §B Q6, Gemini §6.4 |
| 6 | **Acceptance gate insufficient** (verbatim is tautological by Path B) | gate redesign suggested | dispatch-health + wikilink-resolve checks | "minimum gate: dispatch health + every visible wikilink resolves + FM/body consistency + no live writes + no placeholder bodies + no canonical collision + golden chapter" | "needs end-to-end golden chapter test" | universal | **adopt**: codex r2's 7-condition gate + 1 golden chapter as P0/P1 mix | Claude §6 Q6, Codex r1 C, Codex r2 §B Q6 §E #3 #8, Gemini §5.4 |
| 7 | **B6 source_count=1 hardcode → L3 unreachable** | HIGH; needs cross-chapter pass | (not in r1) | PASS HIGH moderate; structurally collapses maturity model | "Maturity contract is unreachable" — listed as 1 of 3 contract failures | universal | **adopt** as P1 (after BSE single-book ship): implement cross-chapter L2→L3 promotion pass after batch | Claude §6 B6, Codex r2 §A B6, Gemini §2 |
| 8 | **B5 CJK classifier failure** (`\b` undefined for CJK; only 2 zh definition patterns) | MED noted | (not in r1) | PARTIAL: MED for English batch / HIGH for CJK books; not English-batch blocker | BLOCKER; classifier 100% silently fails for any CJK term; expanded zh patterns: 是指/即/也就是說/又稱/的定義是 | contradiction (Codex r2 says English-batch not blocker, Gemini says blocker) | **escalate**: ship BSE/SN (English) with current classifier + DEFER fix to before any CJK book ingest. Document as known constraint in v2. Gemini's expanded zh patterns adopted into the deferred fix. | Codex r2 §A B5 §C, Gemini §1.1 |
| 9 | **Path B vs Path A vs Path C architecture** | keep Path B | C: appendix reconciliation (still Path B) | "keep Path B; fix the gate, not body architecture" | "Path C: walker→LLM rich metadata→canonicalization→graph-aware dispatcher→assembler builds appendix from dispatch result" | 2-of-3 (Codex r1+r2 keep Path B; Gemini Path C) | **mod**: keep Path B body verbatim ✓ + adopt Gemini's "appendix built from dispatch result" (this aligns with row 5) + reject Path C re-architecture as too aggressive given English batch is workable | Codex r2 §B Q1, Gemini §5.3 |
| 10 | **B7 update_merge cost path** (LLM Opus diff per repeat) | MED-HIGH; question per-chapter merge strategy | (not in r1) | PASS MED-HIGH; "stop per-chapter LLM update_merge; canonicalize + append mentioned_in during batch; queue rich body merges for offline reconciliation pass" | (not directly addressed; aligns with Path C reconciliation) | 2-of-3 align | **adopt**: per-chapter merge → simple `mentioned_in` append; offline reconciliation pass post-batch for rich body merges | Codex r2 §B Q3 §E #5 |
| 11 | **B11 alias_map write-only (never read back)** | HIGH | (not in r1) | PASS HIGH | covered (top-2 fix is delete alias_map) | universal (with caveat — see row 4 resolution split) | **mod**: defer file deletion; demote-to-plain-text (row 4) makes alias_map redundant for body; keep map as low-value index for now | Claude §6 B11, Codex r2 §A B11, Gemini §3 |
| 12 | **Phase 1 wikilinks game-theoretic over-emit** (no LLM penalty for noise) | (not directly addressed) | (not in r1) | (not addressed) | UNIQUE: re-prompt LLM to "list 5-7 most critical concepts per section + justify each" instead of flat list | single-source Gemini | **adopt mod**: prompt change is low-risk + cheap; will reduce L1 noise + improve B12 resolution. Try in next preflight. | Gemini §3 |
| 13 | **B16 zero inline body wikilinks** (Patch 3 over-correction?) | MED | (not in r1) | PASS MED moderate; "appendix-only is acceptable short term; later add first-occurrence-per-section L2/L3 only"; product tradeoff not bug | (not directly addressed) | 2-of-3 (Codex r2 says product tradeoff, Claude flagged as concern) | **mod**: keep appendix-only for English ship; revisit after UAT if user reports search regression | Claude §6 B16 §Q5c, Codex r2 §A B16 §B Q5c |
| 14 | **B18 placeholder string in concept bodies** | (missed) | (not in r1) | UNIQUE: `kb_writer.PLACEHOLDER` (`_(尚無內容)_`) still present; `_check_hard_invariants` only blocks older patterns; ADR-020 "no placeholder" invariant weakened | (missed) | single-source Codex r2 | **adopt** as P1: extend `_check_hard_invariants` patterns to include `_(尚無內容)_`; clean up existing 192 staged pages | Codex r2 §D |
| 15 | **B19 advisory lock no caller passes lock_conn** | (missed) | (not in r1) | UNIQUE: future parallel dispatch will race; lock release deletes by key not holder | (missed) | single-source Codex r2 | **adopt deferred**: P2 (after single-book ship); write a follow-up issue, not blocker | Codex r2 §D |
| 16 | **B20 figure attachment overwrites by basename** | (missed) | (not in r1) | UNIQUE: cross-EPUB same basename collision; no manifest cleanup | (missed) | single-source Codex r2 | **adopt deferred**: P2; write follow-up issue + investigate before 3rd book | Codex r2 §D |
| 17 | **B21 batch checkpoint write-only / no resume** | (missed) | (not in r1) | UNIQUE: only progress JSON, no skip semantics | (missed) | single-source Codex r2 | **adopt deferred**: P2; not needed for single-book ship | Codex r2 §D |
| 18 | **B22 ADR-019 vs ADR-020 contract drift** | (missed) | (not in r1) | UNIQUE: Raw + Annotated sibling not cross-linked in staged source pages | (missed) | single-source Codex r2 | **adopt deferred**: P3; doc-level reconciliation, not blocker | Codex r2 §D |
| 19 | **B23 verify_staging batch spot-check wrong path** (`Concepts/alias_map.md` instead of `Wiki.staging/_alias_map.md`) | (missed) | (not in r1) | UNIQUE: Codex r2 found script bug | (missed) | single-source Codex r2 | **adopt** as P0 (trivial): fix path; piggyback with row 1 alias-map staging fix | Codex r2 §D |
| 20 | **B24 192 staged concept pages need migration** | (acknowledged in v1) | (not in r1) | UNIQUE: explicit migration script for ATP variants, case/plural, alias-map L1 rows, backlinks | "192 concept page need dedupe before publish" (general from §3) | universal | **adopt** as P1: migration script before any publish to live Wiki | Codex r2 §D §E #7, Gemini §3 |
| 21 | **Patch 1 regex gaps (nested bracket / multi-line / escaped]) and Patch 2 NFKC + Windows reserved name** | (missed in v1, codex r1 raised) | A: regex gaps + NFKC + Windows reserved | (covered indirectly via B5 CJK normalization in row 8) | (NFKC mentioned in §1.3) | 2-of-3 align | **adopt mod**: Patch 1 regex hardening = P2 (English batch unaffected); Patch 2 NFKC = P0 piggyback with row 2 canonicalization (CJK comes free) | Codex r1 A, Gemini §1.3 |
| 22 | **B17 YAML anchors `&id001`/`*id001`** | LOW | (not in r1) | PASS LOW trivial; "remove YAML anchors" P1-bag item | (missed) | single-source Codex r2 (Claude flagged) | **adopt deferred** as P2: trivial cleanup; non-blocking | Claude §6 B17, Codex r2 §A B17 §E #6 |
| 23 | **B8 dead frontmatter fields** (source_refs, aliases, confidence) | LOW | (not in r1) | PASS LOW; "remove dead frontmatter fields" P1-bag item | (general) | universal | **adopt deferred** as P2: cleanup with row 22 | Codex r2 §A B8 §E #6 |
| 24 | **B9 anchor tolerance** | LOW open question | (not in r1) | PARTIAL LOW moderate; nuisance reject not silent corruption | (not addressed) | single-source Codex r2 | **reject** for now: cost > benefit; keep current `_anchor_equiv` | Codex r2 §A B9 |
| 25 | **B10 walker tables unused** | LOW | (not in r1) | PARTIAL LOW trivial; tables in payload but downstream contract minimal | (not addressed) | single-source Codex r2 | **reject** for now: not blocking ship | Codex r2 §A B10 |
| 26 | **Decision frame: ship 28 / ship 1 book / pause / rewrite** | 3 options laid out | patch-4-then-go | **patch + ship 1 book (BSE) to staging UAT, no fundamental rewrite** | **architectural pause** + ship 1 book after refactor + 1 week UAT | 2-of-3 (Codex r1+r2 favor patch-then-go-1-book; Gemini wants pause+refactor) | **adopt Codex r2 framing**: patch P0 items → ship BSE only → user UAT in Obsidian → SN after pass | Codex r1 D, Codex r2 §E, Gemini §6 |
| 27 | **End-to-end golden chapter test** | (missed) | (not in r1) | "add one golden end-to-end chapter fixture" #8 | "lack of E2E golden master is unacceptable" top-5 fix | universal | **adopt** as P1: select 1 BSE chapter as golden + check in expected output; CI runs ingest-and-diff | Codex r2 §E #8, Gemini §5.4 §6.5 |

# Pattern summary

- **Universal-agreement adopt**: rows 1, 2, 3, 5, 6, 11, 20, 27 (8 items P0/P1)
- **2-of-3 adopt with mod**: rows 4, 9, 10, 13, 21 (5 items)
- **Single-source adopt**: rows 12 (Gemini), 14, 19 (Codex r2 — placeholder + advisory lock); 4 unique catches make panel value visible
- **Single-source defer/queue**: rows 15, 16, 17, 18, 22, 23 (Codex r2 P2/P3 items)
- **Single-source reject**: rows 24, 25 (Codex r2 — judged not worth fix cost now)
- **Contradiction escalated**: row 8 (CJK BLOCKER vs deferred — resolution: ship English now, defer CJK fix)
- **Decision frame**: row 26 — adopt Codex r2's "patch + ship 1 book" framing

# P0 / P1 / P2 / P3 patch list (for v2)

**P0 (must fix before any ship — even staging UAT)**:
1. B4 alias_map preflight staging fix + B23 verify_staging path fix (rows 1, 19) — trivial
2. B12 canonicalization v0: NFKC + casefold + plural/acronym + seed alias dict + Patch 2 NFKC piggyback (rows 2, 21) — moderate
3. B13 mentioned_in reconciliation (row 3) — moderate
4. B14 frontmatter / appendix unified from dispatch log (row 5) — moderate
5. B15 demote L1 wikilinks to plain text in body appendix (row 4) — moderate
6. Acceptance gate hardening: 7-condition gate (row 6) — moderate
7. B12 prompt change: Phase 1 LLM "list 5-7 critical concepts + justify" (row 12) — trivial

**P1 (before SN ingest)**:
8. B6 cross-chapter L3 promotion pass (row 7) — moderate
9. B7 offline concept-merge pass replacing per-chapter update_merge (row 10) — moderate
10. B18 placeholder string check extended (row 14) — trivial
11. B24 migration script for 192 staged concepts (row 20) — major (one-shot script)
12. End-to-end golden chapter fixture (row 27) — moderate

**P2 (post-1-book UAT)**:
13. Patch 1 regex hardening (row 21) — trivial
14. B17 YAML anchors + B8 dead FM fields cleanup (rows 22, 23) — trivial
15. B19 advisory lock plumbing (row 15) — moderate
16. B20 figure attachment idempotency (row 16) — moderate
17. B21 batch checkpoint resume (row 17) — moderate

**P3 (latent, follow-up tickets)**:
18. B22 ADR-019/020 reconciliation (row 18) — doc-level
19. B5 CJK classifier rules + zh definition pattern expansion (row 8) — defer to before first Chinese book

# Decision recommendation (synthesized from row 26)

**Patch P0 → re-ingest BSE only (11 chapters) → user UAT in Obsidian → patch P1 + golden test → ingest SN → final gate pass → publish to live `KB/Wiki/`.**

Cost: P0 patches ~1-2 days dev + ~$5 for re-ingest BSE 11 chapters. SN ingest after user sign-off ~$15. Total ~$20-25 + dev time.

Reject: ship 28 chapters now (5/7 pattern), full architectural pause (overkill given English batch is workable).

Adopt: phased rollout with explicit human-eyeball gate between BSE and SN.
