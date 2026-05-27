---
title: Title × Thumbnail Playbook v1 — 3-way Panel Integration Matrix
date: 2026-05-27
status: in-progress (v1.1 fixes being applied)
related:
  - prompts/thumbnail/playbook_v1.md
  - docs/research/2026-05-27-codex-thumbnail-playbook-audit.md
  - docs/research/2026-05-27-gemini-thumbnail-playbook-audit.md
  - docs/research/2026-05-26-thumbnail-playbook-design.md
---

# 3-way Panel Integration — Playbook v1 → v1.1

Multi-agent-panel skill step 4. Both external audits **APPROVE WITH MODIFICATIONS** — no rejection, but substantial fixes required before brainstorm-prompt integration.

## Audit summary

| Auditor | Verbatim path | Verdict | Word count |
|---|---|---|---|
| Claude (drafter, self-critique in §7) | `prompts/thumbnail/playbook_v1.md` | n/a (drafter) | 1083 lines |
| Codex (GPT-5) | `docs/research/2026-05-27-codex-thumbnail-playbook-audit.md` | Approve with modifications | ~12KB |
| Gemini (gemini-2.5-pro) | `docs/research/2026-05-27-gemini-thumbnail-playbook-audit.md` | Approve with significant modifications | ~22KB |

## Integration matrix

| # | Topic | Claude v1 stance | Codex audit | Gemini audit | 3-way pattern | Resolution |
|---|---|---|---|---|---|---|
| I-1 | Numerical/frequency count drift | claimed 19/140 for T-V1, 20 for T-V2, 12 for T-V3, etc. | Documented errors: T-A10 freq=6 but raw=2; T-V1 sums to 22 not 19; T-V2 sums to 15 not 20; UP-1 claims 35/35 all creators but raw is 31/35/34/33; UP-3 121+ co-occurrence but actual is 108 | Could not verify directly (no JSON access) but accepts Codex | **2-of-3 + verifiable** | **Adopt** — recompute all freq/distribution from raw JSON; flag T-A10 as catalog mismatch |
| I-2 | §1 anchor citation bugs | references "Pattern interrupt (§1.5)" etc throughout §2/§3 | All wrong: §1.5 is Loss Aversion not Pattern Interrupt; §1.2 is MrBeast PVP not Cialdini authority; §1.8 is Face Emotion not Insider knowledge; §1.10 is Familiarity Scaffolding not MrBeast PVP | (didn't specifically check) | **Codex unique + verifiable** | **Adopt** — script-fix all §1.X anchors in §2/§3 mechanism lines |
| I-3 | "NOT ad-hoc" false confidence | §1 opens "Click-driver attributions in this playbook are NOT ad-hoc." | "Most dangerous false-confidence spot... overstates what corpus proves" | (agrees implicitly) | **Codex single-source, sound** | **Adopt** — soften §1 framing |
| I-4 | T-A9 / T-A10 primary vs modifier | listed as primary archetypes | "Year-Anchor and Loss Framing are modifier tags, not clean primary archetypes" | (agrees, top-5 priority) | **2-of-3** | **Adopt** — relabel as M-1 / M-2 modifiers; remove from §2 primary list |
| I-5 | T-A2 / T-A3 / T-A5 / T-A8 conflation | recognized in MC-3 for T-A4 + T-V1 only | "MC-3 undercounts the problem... T-A2 conflates 3 behaviors, T-A3 conflates 3 frames, T-A5 overlaps T-A3, T-A8 is too broad" | (agrees Codex's framing) | **2-of-3** | **Adopt** — expand MC-3 enumeration; flag for v2 split |
| I-6 | T-V1 vs T-V4 merge | separate archetypes | "Production orientations of same dual-zone layout — merge unless CTR proves orientation matters" | (agrees implicitly) | **Codex single-source** | **Adopt as v2 follow-up** — keep separate in v1.1 with cross-reference note |
| I-7 | T-V6 grade C → D/Avoid | C (risky) | C is fair | **D / Avoid** — "Regulatory risk under Taiwan Health Food Control Act + clickbait association with content farms" | **Gemini unique, mission-critical** | **Adopt** — downgrade to D, add regulatory caveat |
| I-8 | zh-Hant examples are "translationese" | written as conceptual sketches | "Many overclaim causality, invent metrics, violate own §5.4 length rule" — specific examples called out | "Catastrophic failures of adaptation... read as stilted, unnatural translationese... require full rewrite by native copywriter" — provided concrete native-first T-A3 spec | **2-of-3 + Gemini provides spec** | **Adopt** — mark all §2/§3/§4 中文化範例 as REQUIRES-REWRITE; insert Gemini's T-A3 spec as exemplar |
| I-9 | §5.4 missing zh-Hant nuances | 7-row bilingual table | (didn't specifically address) | "Missing: modal particles (喔/啊/啦), 【】 lenticular brackets, TW/HK terminology (優格 vs 乳酪), simplified char contamination QA, numerical unit cultural connotation" | **Gemini unique, additive** | **Adopt** — expand §5.4 with 5 new rows |
| I-10 | Identity hook cultural porting | §1.4 mentions 修修-adaptation but stays individualist | (didn't address) | "Missing collectivist/Confucian identity hooks — family responsibility, prudent planning, in-group knowledge sharing" with specific examples | **Gemini unique** | **Adopt** — add §1.4 supplementary paragraph |
| I-11 | Missing zh-Hant creator baseline | §7 MC-1 acknowledges English-only bias | "Sample misses actual target niche — no Attia, Huberman, Bryan Johnson, Saladino, Rhonda Patrick or zh-Hant baseline" | "Both Claude and Codex missed Taiwanese/HK wellness aesthetic — Magazine Cover aesthetic; Calm Expert persona" | **2-of-3 + Gemini deeper** | **VETOED by 修修 (2026-05-27)** — positioning is intentionally Western-aesthetic-in-Chinese-language as differentiation strategy; foreign-looking in 中文-wellness feed is the desired pattern interrupt. Do NOT propose Chinese-creator reference corpora in future work. (Attia / Huberman / Bryan Johnson etc — English-language longevity creators — remain valid v2 expansion candidates if 修修 chooses.) |
| I-12 | Mechanism post-hoc attribution | MC-2 self-acknowledges | "MC-2 too gentle — T-A1 commitment-consistency not earned; T-V5 unreadable-diagram 'more motivating' is storytelling; T-V10 'short-circuits credibility evaluation' dangerous" | "T-V5 actual mechanism is Proof-of-Work / Complexity Signaling not 'tantalising incompleteness'" | **2-of-3 + Gemini provides alternative** | **Adopt** — replace mechanism for T-V5; soften causal language ("hypothesised mechanism" not "the framework fires") |
| I-13 | T-V5 mechanism | "tantalising incompleteness... more motivating than readable diagram" | "Pure post-hoc storytelling" | "Actual mechanism is Proof of Work / Complexity Signaling — value isn't in details but in existence as credibility signal" | **2-of-3 + Gemini better hypothesis** | **Adopt** — rewrite T-V5 mechanism using Gemini's framing |
| I-14 | JP-8 confrontational health overlay | "BMI 是錯的" recipe | (didn't specifically address) | "Critical context collapse — confrontational tone re public health metric is brand suicide for evidence-based channel" | **Gemini unique, brand-protective** | **Adopt** — replace JP-8 recipe with non-confrontational reframe; downgrade JP-8 as a whole |
| I-15 | No Anti-Playbook | not addressed | (didn't address) | "Methodology should specify negative corpus — what low-CTR videos from same creators show; what to definitely avoid" | **Gemini unique, generative** | **Adopt as v2 followup** — document as design requirement |
| I-16 | No portfolio strategy | not addressed | (didn't address) | "Aim for monthly mix of 3x How-To, 1x Contrarian, 1x Personal Story, 1x Question — using T-A3 three times in row makes channel cynical" | **Gemini unique** | **Adopt as v2** — add §6.4 Portfolio Strategy |
| I-17 | No feedback loop for dynamic grades | static §5.2 grades | (didn't address) | "After every 10 videos, recompute average CTR per archetype used; adjust grades >20% above/below" | **Gemini unique** | **Adopt as v2** — add §8 grade-update protocol |
| I-18 | Frequency threshold ≥3 / ≥2 too weak | design doc §1.3 | "T-V6 7/8 Cleo; JP-3 9/10 Jeff; JP-5 7/8 Cleo — those are creator signatures not robust universals" | (didn't specifically address) | **Codex single-source** | **Adopt** — tighten to ≥5 / ≥3 in v2 + flag creator-skewed archetypes in v1.1 |

## Confidence summary

- **3 changes universal-confidence (apply immediately in v1.1)**: I-1 (numerical), I-2 (§1 anchors), I-3 ("NOT ad-hoc")
- **5 changes 2-of-3 confidence (apply in v1.1)**: I-4 (T-A9/T-A10 modifier), I-5 (conflation MC-3 expand), I-7 (T-V6 D-grade), I-8 (zh-Hant rewrite flag), I-12 (mechanism softening), I-13 (T-V5 mechanism)
- **4 changes Gemini-unique high-value (apply in v1.1)**: I-9 (§5.4 expansion), I-10 (identity hook collectivism), I-14 (JP-8 reframe)
- **5 changes deferred to v2 with documentation**: I-6 (T-V1/T-V4 merge), I-11 (zh-Hant baseline corpus), I-15 (anti-playbook), I-16 (portfolio strategy), I-17 (feedback loop), I-18 (threshold tightening)

## v1.1 application plan

1. Recompute all numerical / creator distributions from `data/thumbnail_reference_extraction_v1.json` raw data → script-fix in playbook_v1.md
2. Script-fix all §1.X anchor references to match actual section numbers
3. Replace §1 "NOT ad-hoc" sentence with hedged language
4. Relabel T-A9 → M-1, T-A10 → M-2; remove from primary list in §5.2 matrix; add "Modifier Tags" subsection
5. Expand MC-3 enumeration of conflated archetypes
6. T-V6 grade C → D, add regulatory caveat citing Taiwan Health Food Control Act
7. T-V5 mechanism rewrite: "Proof of Work / Complexity Signaling"
8. JP-8 recipe replace; flag as cautionary case study
9. §5.4 bilingual table: add 5 new rows (modal particles / lenticular brackets / TW-HK terminology / simplified char QA / numerical unit cultural connotation)
10. §1.4 paragraph addition: collectivist/Confucian identity hooks with examples
11. Insert Gemini's native T-A3 spec as the EXEMPLAR for "what zh-Hant adaptation should look like"
12. Mark all other 中文化範例 as REQUIRES-REWRITE-BY-NATIVE pending Phase 8
13. Soften causal language throughout §2/§3 mechanisms ("hypothesised mechanism" / "consistent with" / "candidate framework")
14. Add §8 v2 backlog: anti-playbook, portfolio strategy, feedback loop, threshold tightening

## What stays as v1.1 limitations (require 修修 collaboration)

- **Native zh-Hant rewrite of all 32+ 中文化範例**: cannot be done without 修修's voice + brand judgement. v1.1 marks them as REQUIRES-REWRITE. 修修 will iterate from real-output observations rather than preemptive rewrite (per 2026-05-27 feedback).
- **CTR feedback data**: requires 修修's actual publishing history.

## VETOED by 修修 (2026-05-27)

- **I-11 zh-Hant Chinese-creator baseline corpus**: explicitly declined. Positioning is Western-aesthetic-in-zh-Hant-language as differentiation. Looking foreign in 中文 wellness feed is the desired pattern interrupt, not a defect. Do NOT propose Chinese-creator reference material in v2/v3 work.

## Cost

- Codex audit: ~$1.5
- Gemini audit: ~$0.3
- Phase 7 integration: ~$0.5 (this doc + automated fixes)
- Total panel cost: ~$2.3 on top of original ~$10.
