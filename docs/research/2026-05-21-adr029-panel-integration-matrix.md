# ADR-029 Bridge IA Restructure — Panel Integration Matrix

**Date:** 2026-05-21
**Panel:** Claude Opus 4.7 (drafter) + Codex GPT-5 (code-grounded audit) + Gemini 2.5 Pro (UX/IA lens)
**Audits:**
- Codex verbatim: `docs/research/2026-05-21-codex-adr029-audit.md`
- Gemini verbatim: `docs/research/2026-05-21-gemini-adr029-audit.md`

---

## Summary

Codex audit caught **factual / code-grounding errors** that would have shipped silently. Gemini audit raised **architectural / UX-pattern concerns** that escape line-level grounding. The two are complementary — Codex says "your facts are wrong"; Gemini says "your frame is wrong."

**29 distinct push-back items.** Categorized below.

## Matrix

| # | Topic | Claude v1 | Codex | Gemini | Pattern | Resolution |
|---|---|---|---|---|---|---|
| 1 | `/robin/kb` URL exists | claimed in §2 click target | **does not exist** (real: `/`, `/robin/read`, `/robin/books`) | (not raised) | code error | **ADOPT** — fix §2 table |
| 2 | `/bridge/zoro` URL exists | claimed in §2 | **does not exist** (real: `/bridge/zoro/keyword-research`) | (not raised) | code error | **ADOPT** — fix §2 table |
| 3 | `/brook` URL exists | claimed in §2 | **does not exist** (real: `/brook/handoff`, `/brook/chat`, `/brook/bridge`) | (not raised) | code error | **ADOPT** — fix §2 table |
| 4 | 34 templates consume `chassis_nav` | claimed in §Context + §6 | **actual = 19** app-template consumers | (not raised) | factual drift | **ADOPT** — fix the number |
| 5 | SEO surface owners | "Zoro + Brook" | **Zoro + Brook + Franky** (ADR-008 ranking telemetry) | (not raised) | spec drift | **ADOPT** — update §4 SEO description |
| 6 | Wordmark clickable | "wordmark = home" (§1) | currently `<div>` not `<a>` — implementation required | "good convention but ensure visual affordance" | universal | **ADOPT** — add implementation note to PR-2 |
| 7 | Theme toggle duplication | "migrate to chassis-nav top-right" | `theme.js:41-75` injects fixed pill into every page; needs mount-point API or opt-out | "creates new inconsistency: Bridge top-right vs other pages bottom-right; needs all-page strategy not deferral" | universal | **ADOPT** — PR-2 must include theme.js mount-slot API; revisit deferral |
| 8 | Sunny removal wording | "dashboard IS Sunny" | "platform/chassis, not 'dashboard is Sunny'" | "Sunny is the ship; Bridge is the control deck *of* Sunny; tighten the justification" | universal | **ADOPT** — rewrite §2 Sunny paragraph as "platform/chassis" |
| 9 | Workflows ▾ with only 2 items | proposed | "weak IA; keep DRAFTS+SEO top-level" | "hiding high-freq workflows behind dropdown adds interaction cost" | universal | **ADOPT** — promote DRAFTS + SEO to top-level |
| 10 | Card → direct nav vs drawer | "delete drawer entirely" | (not specifically pushed back) | "false dichotomy — keep drawer for monitoring stats (hover/stats-icon), make card body the link; drawer = at-a-glance status" | single-source (Gemini) | **ADOPT MODIFIED** — card body navigates; small stats affordance opens drawer; drawer demoted not deleted |
| 11 | 4 Under Construction cards = clickable | proposed equal-footing stub pages | "false affordances; planned agents should be disabled or non-primary" | "actively bad UX; visually distinguish, make non-interactive, tooltip for status" | universal | **ADOPT** — UC cards render disabled (visually distinct), non-clickable, tooltip on hover |
| 12 | Horizontal dropdown vs sidebar | dropdown chosen | sidebar listed as Alt C (admin pattern) | **strong push: adopt sidebar; dropdown is "lateral move not improvement"** | 2-of-3 (Codex+Gemini) | **ESCALATE TO USER** — sidebar is a different visual paradigm, deserves separate decision |
| 13 | Agent-first vs task-first IA | agent-first | "workflow-first" listed as Alt A | **strong push: task-first is "vastly superior"; agent-first is "organizing UI to match database schema"** | 2-of-3 (Codex+Gemini) | **ESCALATE TO USER** — fundamental framing question |
| 14 | Group names: Fleet/Workflows/Ops | English jargon labels | (not raised) | "Ops is pure technical jargon; Workflows acceptable; better: Content/System/Analytics or Content/Agents/System" | single-source (Gemini) | **ESCALATE** — name choice depends on whether sidebar/task-first adopted (#12, #13) |
| 15 | One Piece metaphor consistency | partially used (Fleet) | (not raised) | "inconsistent — leans on metaphor for Fleet, abandons for Workflows/Ops; dissonant" | single-source (Gemini) | **NOTE** — minor concern, dependent on #14 |
| 16 | Mobile/responsive deferral | "out of scope" | (not raised) | "critical blind spot; deferring is not neutral — locks in desktop-only paradigm" | single-source (Gemini) | **MOD** — add brief responsive plan to §Out of scope, not full design |
| 17 | CONTEXT-MAP needs more than one update | "chassis-nav reversal" only | **more updates needed**: `surface` example outdated, Sunny-as-platform vs agent, add Fleet/Workflows/Ops terms; current branch HEAD does NOT include CONTEXT-MAP update | (not raised) | code error | **ADOPT** — expand CONTEXT-MAP edit scope in PR-1 |
| 18 | "chassis-nav" principle replaces brittle with brittle | reverses CONTEXT-MAP rule | (not raised) | "new principle equally arbitrary; better: 'organize by task frequency + semantic similarity, use scalable component'" | single-source (Gemini) | **MOD** — rephrase the new CONTEXT-MAP principle to be component-agnostic |
| 19 | Migration: PR-2 + PR-3 atomicity | sequenced separately | (not raised) | "PR-2 (nav) and PR-3 (drawer) should be combined; partial state confusing" | single-source (Gemini) | **ADOPT** — combine PR-2 + PR-3 |
| 20 | Migration: PR-5 sequencing | remove REPURPOSE from nav after Brook console gains entry | "do not normalize health→franky and repurpose→brook before absorptions ship" | "remove REPURPOSE only after Brook console has clear entry; user adjustment time" | universal | **ADOPT** — formalize this constraint in §6 |
| 21 | Robin → KB / VAULT removal | VAULT removed; Robin console becomes canonical | "VAULT goes to `/` which is also Robin root; no `/robin/kb` route; regression risk" | (not raised) | code error | **ADOPT** — keep VAULT (or make Robin console URL real) until Robin has canonical home |
| 22 | Brook console = "/brook" | claimed | "Brook is context bridge per ADR-027, not full local console" | (not raised) | spec drift | **ADOPT** — qualify Brook console scope per ADR-027 |
| 23 | `nav_active='vault'` template usage | claimed needs migration | "no current app template uses it" | (not raised) | factual | **ADOPT** — note can be removed cleanly |
| 24 | Alt A: workflow-first nav | not considered | proposed | implicitly endorsed | 2-of-3 | (subsumed by #13 escalation) |
| 25 | Alt B: hybrid (DRAFTS + SEO top-level, Fleet ▾, Ops ▾) | not considered | proposed as preferred middle ground | implicitly endorsed (matches "task-first" lite) | 2-of-3 | (subsumed by #9 adoption) |
| 26 | Alt C: left sidebar | not considered | proposed | strongly endorsed | 2-of-3 | (subsumed by #12 escalation) |
| 27 | Drawer = monitoring board, not preview | "preview pane" framing | (not raised) | "drawer is primary monitoring UI; removing changes dashboard purpose from rich glanceable to dumbed-down launcher" | single-source (Gemini) | (subsumed by #10) |
| 28 | Future cross-agent workflows (e.g. script-driven video) | not discussed | (not raised) | "agent-first IA makes cross-agent workflows harder; task-first scales better" | single-source (Gemini) | (subsumed by #13 escalation) |
| 29 | Audit trail completeness | (not applicable to v1) | "ADR branch HEAD only adds ADR-029; promised CONTEXT-MAP update missing" | (not raised) | factual | **ADOPT** — bundle CONTEXT-MAP into PR-1 |

---

## Summary by resolution

- **ADOPT verbatim**: 16 items (#1-#9, #11, #17, #19-#23, #29)
- **ADOPT MODIFIED**: 2 items (#10 drawer-demote, #16 responsive note)
- **MOD wording**: 2 items (#18 principle phrasing, #15 metaphor consistency note)
- **ESCALATE to user**: 3 items (#12 sidebar, #13 task-first, #14 group names — interrelated)
- **Subsumed/derivative**: 6 items (#24-#28, plus internal merges)

## Confidence

- **High confidence**: All code/spec-grounding fixes (#1-#5, #17, #21, #22, #29) — Codex verified against actual files.
- **High confidence**: UX corrections #9 (workflows promote), #11 (UC visually distinct) — both auditors agreed.
- **Medium-high**: #10 drawer-demote — strong Gemini argument that drawer is monitoring affordance, not just preview. Owner should validate against actual usage.
- **Open for user decision**: #12 sidebar, #13 task-first, #14 group names. These are interrelated and represent a fork: stay with horizontal nav + agent-first (v2 lite) OR pivot to sidebar + task-first (v2 heavy).

## Recommended next step

Present the 3 escalation questions to owner. Their answers determine whether v2 is a tightened version of v1 (horizontal nav, agent-first, factual fixes) or a substantively different architecture (sidebar, task-first IA). v2 ADR draft follows the owner's call.
