# ADR-038 Panel Integration Matrix

**Date:** 2026-05-28
**Panel:** Claude Opus 4.7 (v1 draft) → Codex GPT-5 (audit) → Gemini 2.5 Pro (audit)
**Verbatim audits:**
- [`docs/research/2026-05-28-codex-adr038-audit.md`](2026-05-28-codex-adr038-audit.md)
- [`docs/research/2026-05-28-gemini-adr038-audit.md`](2026-05-28-gemini-adr038-audit.md)

## Verdict summary

- **Claude v1**: Approve (self-vote, baseline)
- **Codex**: Approve **with modifications** (do not accept as-is)
- **Gemini**: **Reject** (rewrite D1, OQ1; fix D2, D3; add Phase 1 gate)

Both external reviewers converge on the same substantive direction. Gemini is more aggressive ("reject") but agrees with Codex on all major points. No genuine 3-way contradictions — Claude v1 was systematically off on the same axis both reviewers caught.

## Integration matrix

| # | Topic | Claude v1 | Codex | Gemini | Pattern | Resolution |
|---|---|---|---|---|---|---|
| **P1** | **D1 Lua-via-fuscript primary** | Lua primary, Python API deferred to Phase 2.5 | Python-native first, Lua fallback after documented failure | Python-native MUST be primary; fuscript is "cargo-culted" | **2-of-2 against v1** | **Adopt** — D1 rewritten Python-native primary; `fuscript` deferred or removed |
| **P2** | **§OQ1 verbatim Lua port** | File GH issue, fallback 1-2d rewrite | No verbatim port from unlicensed repo; clean-room upfront | Naive and legally unsound; rewrite from official docs only | **2-of-2 against v1** | **Adopt** — no Lua copying; clean-room from Resolve official Python docs |
| **P3** | **D2 hash inputs (layout YAML?)** | `broll_decision/layout/component/params/render_target` only | Hash design not "ready"; emitter + worker still hardcode beat-id | Catastrophic silent failure — must include layout YAML content digest | **2-of-2 against v1** | **Adopt** — hash inputs extended to include `layouts/*.yaml` content digest + composition HTML digest |
| **P4** | **D3 char-offset anchors** | `shift_anchor(direction, char_count)` etc. | char counts brittle for Mandarin; 6-op surface incomplete; missing `set_layout / set_transition / patch_broll_params / set_timing / duplicate_broll_from / restore_previous_render` | "guaranteed to fail" in Mandarin; redesign around semantic quote anchors; `replace_quote(beat_id, old_quote, new_quote)` | **2-of-2 against v1** | **Adopt** — D3 tool surface redesigned around quotes (semantic), not char offsets; tool list expanded |
| **P5** | **Phase 2 / Phase 1 acceptance gate** | "Can proceed in parallel" | PR-D/E/F must NOT merge before real-episode acceptance closes | "Premature commitment invalidates original gate"; gate Resolve work | **2-of-2 against v1** | **Adopt** — PR-D/E/F gated on Phase 1 acceptance closure (real episode end-to-end) |
| **P6** | **Token cost claim ($0.20)** | $0.20 per re-plan | Math wrong: 300k input × $3/MTok ≈ $0.90 input alone | Adds: $0.02-vs-$0.90 framing misses "cost per successful outcome" | **2-of-2 against v1** | **Adopt** — correct math + reframe to per-successful-outcome cost |
| **P7** | **PR-D 3d estimate** | 3d | Optimistic for the cited LOC + path handling + subprocess lifecycle | 5-7d for Python-native rewrite | **2-of-2 against v1** | **Adopt** — PR-D revised to 5-7d (Python-native scope) |
| **P8** | **Sandcastle Lua eligibility** | "PR-D sandcastle-tagged via Lua-mock" | No Lua runtime in sandcastle Dockerfile; Lua-mock fine for subprocess contract only | (not addressed — Codex unique) | **Codex unique, code-grounded** | **Adopt** — sandcastle eligibility clarified; Python-native version is fully sandcastle-eligible anyway |
| **P9** | **3-path dispatcher drift (reader/web Playwright still stub)** | ADR-038 silent | ADR-032 §Phase 1.5 made these the next sibling backlog; ADR-038 jumps to Resolve | (not addressed) | **Codex unique** | **Adopt as new §** — ADR-038 acknowledges Phase 1.5 dual-path backlog still owed; not blocking Phase 2 but called out |
| **P10** | **D2 code-change scope** | Implies emitter "reads via storyboard lookup" — ready | emitter + hyperframes_worker both hardcode `b_roll_{beat_id}.mp4`; needs schema + worker + dispatcher + emitter + README + tests changed together | (not addressed) | **Codex unique, code-grounded** | **Adopt** — PR-A scope expanded with explicit file-change list |
| **P11** | **Borrowings research artifact missing** | Cites `docs/research/2026-05-28-course-video-manager-borrowings.md` | File does not exist on disk; ADR asks reviewers to trust missing report | (not addressed) | **Codex unique** | **Adopt** — write the borrowings report (from agent research session) into the PR alongside v2 |
| **P12** | **"Resolve is open" workflow** | Implicit assumption | (not addressed) | Catastrophic data corruption risk if 修修 has wrong project open; Python API can `LoadProject(name)` explicitly | **Gemini unique** | **Adopt** — Resolve driver must explicitly target project by name, not "active project" |
| **P13** | **D4 Resolve ASR Mandarin support** | "English-only" | (not addressed) | Outdated; Resolve 18.5+ supports zh; mandate a 15-min spike test | **Gemini unique factual** | **Adopt** — D4 reframed; spike test before committing English-only design |
| **P14** | **Language tag schema** | `zh-TW / en / bilingual` enum | (not addressed) | Use BCP 47 (`zh-Hant-TW`, `zh-Hans-CN`, `ja-JP`) | **Gemini unique** | **Adopt** — BCP 47 tags |
| **P15** | **`[N]` ambiguity in CJK** | `[N]` clip-index | (not addressed) | `[12]` vs `[1,2]` confusion in CJK comma context; use `[beat:12]` or strong prompt framing | **Gemini unique** | **Adopt** — D6 syntax changed to `[beat:N]` to avoid CJK ambiguity |
| **P16** | **Driver abstraction for testability** | Implicit direct subprocess | (not addressed) | Driver behind interface; mock driver for CI | **Gemini unique** | **Adopt** — `ResolveDriver` is a protocol; concrete impl + `MockResolveDriver` for tests |
| **P17** | **Two-tiered D3 agent (style vs ops)** | Single LLM emits ops | (not addressed) | Decompose "make this more dynamic" → high-level style chooser + deterministic ops translator | **Gemini unique** | **Defer (Phase 2.5)** — interesting but adds scope; ship single-tier first, dogfood, then maybe |
| **P18** | **D7 LCS diff value claim** | Enables multi-episode history view | (not addressed) | LCS alone doesn't enable history UI; needs context (who, what, when, version) | **Gemini unique** | **Mod** — D7 reframed as "richer edit_log entries" only; not "history view enabler" |
| **P19** | **Headless Resolve fixture for CI** | "PR-D unit tests run against headless Resolve fixture" | (not addressed) | "Highly improbable"; Resolve needs GUI; needs dedicated long-lived test machine + manual/scheduled tests | **Gemini unique** | **Adopt** — test strategy rewritten: unit tests + mock driver in CI; integration tests on 修修's machine, manual |
| **P20** | **Dependency lock-in cost** | Not analyzed | (not addressed) | Hard runtime dep on proprietary GUI app; locks core value into Blackmagic ecosystem | **Gemini unique** | **Adopt as risk** — add to §Risks table; mitigation = driver abstraction (P16) |
| **P21** | **D1 "battle-tested in Matt's workflow"** | Cited as rationale | "Does not transfer cleanly"; different product, WSL2 path, session-stateful | "Workflow mismatch"; Matt's structured script vs 修修's conversational podcast | **2-of-2** | **Adopt** — remove "battle-tested" framing; treat Matt as inspiration not authority |

## Confidence summary

- **High confidence (2-of-2 against v1)**: P1, P2, P3, P4, P5, P6, P7, P21 — all adopted with full rewrite of D1, OQ1, D2, D3
- **Medium-high (single-source, code-grounded)**: P8, P9, P10, P11 — all adopted; concrete code findings
- **Medium (single-source, reasoning-based)**: P12, P13, P14, P15, P16, P18, P19, P20 — all adopted; align with 修修's stated values (Mandarin-first, reliability, simplicity)
- **Single-source deferred**: P17 (two-tiered D3 agent — interesting but Phase 2.5)

## Items NOT adopted

- **P17 two-tiered D3 agent** — adds scope, deferred to Phase 2.5 after dogfood reveals whether single-tier captures intent

## Direct contradictions requiring user judgment

**None.** All push-backs converge directionally. Where Gemini was more aggressive (reject vs approve-with-mods), the substantive changes overlap entirely with Codex's required mods.

## Big-picture takeaway

Claude v1 made one structural mistake that propagated through every decision: **let `course-video-manager` dictate the architecture instead of choosing best-fit tools**. The Lua-via-fuscript was Matt's choice for his TS app calling into Resolve; for a Python app, Python-native API is strictly superior on every axis (latency, error handling, data marshalling, session state, project targeting, license). Once that anchor flipped, license question dissolves, PR-D scope grows, sandcastle eligibility improves, and the work order changes.

The other big learning: ADR-038 was written before the Phase 1 acceptance gate closed. That's premature commitment. Phase 2 may have the wrong priorities if the real episode reveals different friction than "manual FCPXML import".

## v2 ADR changes

See ADR-038 v2 in PR #776. Headline:

1. **D1 rewritten** Python-native (`DaVinciResolveScript`) primary; fuscript removed
2. **§OQ1 rewritten** clean-room from Resolve official Python docs only; no Matt code lift
3. **D2 hash inputs expanded** to include layout YAML + composition HTML digest
4. **D3 redesigned** around semantic quote anchors; tool surface expanded to 10 ops
5. **Phase 1 acceptance gate enforced** for PR-D/E/F (must close before Resolve work merges)
6. **PR-D estimate raised** to 5-7d
7. **Token cost math corrected**; reframed to cost-per-successful-outcome
8. **Driver abstraction** + `MockResolveDriver` added for testability
9. **Project-targeted** Resolve operations (no "active project" assumption)
10. **D6 syntax** changed to `[beat:N]` (CJK ambiguity)
11. **BCP 47 language tags** in frontmatter
12. **D7 reframed** as edit_log enricher only, not history UI enabler
13. **Test strategy rewritten** — unit + mock in CI, integration manual on 修修's machine
14. **Borrowings research report** committed to PR alongside v2
15. **Phase 1.5 dual-path backlog** explicitly acknowledged as still-owed (not blocking but visible)

## User sign-off needed on

- Acceptance gate: confirm PR-A/B/C (utilities, no Resolve) can ship before Phase 1 acceptance closes, but PR-D/E/F gates wait. Or stricter: all Phase 2 waits?
- Phase 2.5 P17 deferral (two-tiered D3 agent) — confirm OK to defer or want it in Phase 2?
- §OQ1: courtesy GitHub issue to Matt as attribution gesture even though no code lifted — yes/no?
