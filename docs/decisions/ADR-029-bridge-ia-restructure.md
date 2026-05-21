# ADR-029: Bridge IA Restructure — Dual-Axis (Agent + Workflow) Navigation

**Date:** 2026-05-21 (v1) / 2026-05-21 (v2 post-panel)
**Status:** Accepted
**Deciders:** shosho-chang, Claude Opus 4.7
**Related:** ADR-001 (agent roles), ADR-006 (HITL approval queue), ADR-008 (SEO observability), ADR-009 (SEO solution architecture), ADR-012 (Zoro/Brook boundary), ADR-014 (Repurpose engine), ADR-027 (Brook scope reduction), CONTEXT-MAP.md

> **v2 audit trail (2026-05-21):** Multi-agent panel review (Claude + Codex GPT-5 + Gemini 2.5 Pro) ran on v1.
> - **Codex** caught 5 factual errors in v1: three click-target URLs (`/robin/kb`, `/bridge/zoro`, `/brook`) don't exist; the "34 templates" count is wrong (actual = 19 app-template consumers); SEO surface owners listed as "Zoro + Brook" miss Franky (ADR-008 ranking telemetry); wordmark currently `<div>` not `<a>` — clickability is implementation work not just a decision; theme.js will duplicate controls without a mount-point API change.
> - **Gemini** raised UX/IA architectural concerns Codex missed: drawer-as-monitoring-board (deleting it changes dashboard purpose from glanceable status to dumbed-down launcher), "Under Construction" cards = "actively bad UX" (false affordances), mobile/responsive deferral is design debt accumulation, group names "Fleet/Workflows/Ops" are SRE jargon vs creator-friendly alternatives.
> - **Two architectural pushes (sidebar over horizontal, task-first over agent-first) escalated and rejected** with documented rationale (§Open questions resolved).
> - Owner adjudicated 29 distinct push-back items: 18 adopted (16 verbatim + 2 modified), 2 modifications to wording, 3 architectural escalations rejected with rationale, 6 subsumed into other items.
>
> Audits preserved at `docs/research/2026-05-21-codex-adr029-audit.md` and `docs/research/2026-05-21-gemini-adr029-audit.md`. Integration matrix at `docs/research/2026-05-21-adr029-panel-integration-matrix.md`.

---

## Context

The Bridge web UI at `/bridge/*` is 修修's primary control surface when working outside Slack. After ~6 months of organic growth, the IA has drifted into a 12-item flat nav that overflows to two rows and mixes three different conceptual lanes (agent-rooted, topic-rooted, external) without grouping.

A 2026-05-21 grilling session ([session handoff](../../.nakama/session_handoff_2026-05-21_theme-toggle-bridge-brief.md)) audited Bridge against 修修's stated frustrations:

1. **「Navbar 已換到第二行，超級醜」** — `templates/bridge/_chassis_nav.html:40-53` ships 12 flat `<a>` items: BRIDGE / DRAFTS / SEO / ZORO / REPURPOSE / MEMORY / COST / FRANKY / HEALTH / DOCS / LOGS / VAULT. At standard viewport widths the nav wraps to two rows.
2. **「整個散落在各地，完全沒有任何架構跟邏輯」** — IA fails: agent-rooted entries (ZORO, FRANKY) coexist with topic-rooted entries (SEO, REPURPOSE) and external (VAULT → `/`) with no visual or semantic grouping. CONTEXT-MAP's freeze "agent-rooted 頂層直到擠爆才 dropdown" has hit the overflow point.
3. **Dashboard agent card → drawer preview → OPEN INTERFACE** is one click too many. Drawer (`templates/bridge/index.html:113-133`, `openDrawer()` :229-265) is conceptually a preview pane, but only Robin and Brook have entries in `AGENT_URLS` (index.html:146-149), meaning **7 of 9 agents land in a drawer with no exit** — pure dead end. The two cards that DO have exits target broken URLs (`/robin/kb` and `/brook/handoff` — the first does not exist; see §Codebase inventory).
4. **L/D toggle 在 Bridge「看不到」** — solved by PR #655 (theme.js floating bottom-right pill), but the underlying symptom is that Bridge has no canonical top-right toggle slot. The 34 app pages do not share a normalized header.

### 修修's framing (v2 added)

修修 framed the dashboard's purpose explicitly during grilling:

> 「在 Bridge 裡面，針對每一個 Agent 負責的項目點進卡片後，可以在 UI 上操作他們負責的事務，並觀看執行的現況或監視的內容。」

This is **dual-purpose** — each agent card is both an **operations entry point** (control the things the agent owns) AND a **monitoring affordance** (watch what the agent is doing). Critically, this is *not* a pure agent-first IA: 修修 explicitly maintains separate top-level workflow surfaces (DRAFTS, SEO 中控台) where the work spans multiple agents. The dual-axis frame is intentional, not accidental.

### Codebase inventory (verified 2026-05-21, Codex-grounded)

**12 chassis-nav items** (`_chassis_nav.html:41-52`):

| Slug | Lane | Target | Owner agent(s) |
|---|---|---|---|
| bridge | home | `/bridge` | (dashboard) |
| drafts | topic | `/bridge/drafts` (ADR-006 HITL queue) | Brook + Sanji → Usopp |
| seo | topic | `/bridge/seo` (SEO 中控台) | Zoro + Brook + Franky |
| zoro | agent | `/bridge/zoro/keyword-research` | Zoro |
| repurpose | topic | `/bridge/repurpose` (ADR-014 fan-out) | Brook |
| memory | topic | `/bridge/memory` | (cross-agent) |
| cost | topic | `/bridge/cost` | (cross-agent) |
| franky | agent | `/bridge/franky` | Franky |
| health | topic | `/bridge/health` | Franky |
| docs | topic | `/bridge/docs` (FTS over docs/ + memory/) | (maintainer) |
| logs | topic | `/bridge/logs` | (cross-agent) |
| vault | external | `/` (Obsidian root, also Robin root) | Robin |

**`chassis_nav` macro consumers**: **19 app templates** + 1 macro definition = 20 files total (Codex-verified). All 19 listed in `docs/research/2026-05-21-codex-adr029-audit.md` §2.

**9 agents in `AGENT_ROSTER`** (`routers/bridge.py:60-133`) — built console URLs verified against actual routes:

| Code | Agent | Real console URL today | Status |
|---|---|---|---|
| R-01 | Robin · Knowledge | `/` (root, when Robin enabled; `robin.py:237`); `/robin/read`, `/robin/books`, `/robin/promotion/*` for sub-tools | ✅ built (multi-route) |
| N-02 | Nami · Secretary | — (Slack-native via `gateway/handlers/nami.py`) | Future web TBD |
| Z-03 | Zoro · Scout | `/bridge/zoro/keyword-research` (`bridge_zoro.py:100`); no `/bridge/zoro` root | ✅ built (single tool) |
| B-04 | Brook · Composer | `/brook/handoff` (`brook.py:72`), `/brook/chat`, `/brook/bridge`; no `/brook` root | ✅ built per ADR-027 (context bridge, not full local console) |
| S-05 | Sanji · Community | — | Under Construction |
| F-06 | Franky · Systems | `/bridge/franky` (`franky.py:256`) | ✅ built |
| U-07 | Usopp · Publisher | — | Under Construction |
| C-08 | Chopper · Counsel | — | Under Construction |
| D-09 | Sunny | — (Sunny is the **platform/chassis**, not an executable agent; see §2) | Removed from grid |

**Drawer reachability (with corrected URL facts)**: `AGENT_URLS` (index.html:146-149) only maps `robin` and `brook`. Even for those two, the configured URL `/robin/kb` does **not exist** (Codex finding) — Robin currently has no canonical landing URL configured. Functionally, the working exit count is **1 of 9** (`/brook/handoff`), not 2 of 9.

### Cross-cutting constraints

- **CONTEXT-MAP "SEO 中控台" term** (2026-04-29) — freezes `/bridge/seo` as a deliberate cross-agent surface. v2 corrects the agent list to include Franky (ranking telemetry per ADR-008), not just Zoro + Brook.
- **CONTEXT-MAP "chassis-nav" term** — declares "agent-rooted 頂層直到擠爆才 dropdown". This ADR reverses that principle (overflow threshold has been crossed). The new principle is component-agnostic (see §Decision §1) so it does not assume horizontal nav forever.
- **CONTEXT-MAP "breadcrumb" term** — unchanged.
- **CONTEXT-MAP `Thousand Sunny` term** — says Sunny is the web presentation layer (the whole `thousand_sunny/`), which is correct as a context name but **conflicts with Sunny appearing in `AGENT_ROSTER` as a 9th crew member**. v2 resolves this by removing Sunny from the agent roster on the grounds that Sunny is the *ship/platform*, not a crew member (see §2).
- **CONTEXT-MAP `surface` term** — example says "each Agent context exposes web surfaces through `thousand_sunny/routers/<agent>.py`", which is already false for Nami, Sanji, Usopp, Chopper, Sunny. Must be updated in same PR.
- **19 app templates** consume `_chassis_nav.html` directly. Slug semantics must remain stable or be migrated atomically.

## Decision

Restructure Bridge IA from **flat 12-item nav** to **dual-axis model** (agent grid + workflow surfaces + ops dropdown) with drawer demoted from middle-layer preview to optional monitoring affordance, and unbuilt agents visually distinguished as non-clickable.

### 1. Navigation structure — agent + workflow dual axis

```
[● NAKAMA / BRIDGE]  Fleet ▾   DRAFTS   SEO   Ops ▾                [☀ toggle]
                     │                         │
                     │                         ├ COST      成本
                     │                         ├ LOGS      日誌
                     │                         ├ MEMORY    記憶
                     │                         └ DOCS      文件搜尋
                     ├ ROBIN  NAMI  ZORO  BROOK
                     └ SANJI  FRANKY  USOPP  CHOPPER
```

**5 top-level slots**: wordmark · Fleet ▾ · DRAFTS · SEO · Ops ▾ · (utility slot for theme toggle). Fits on one row.

**Dual-axis principle (v2 explicit)**:
- **Agent axis** (`Fleet ▾` + dashboard grid): every agent has exactly one canonical home — its console (the grid card → that console). The dropdown is a keyboard/familiar-user shortcut; the grid is the primary visual entry point.
- **Workflow axis** (top-level `DRAFTS`, `SEO`): work that **genuinely spans multiple agents** gets its own top-level surface, not nested inside any single agent's console. These are the highest-frequency operator actions (review drafts, audit SEO) — they earn direct top-level slots.
- **Ops axis** (`Ops ▾`): cross-cutting instrumentation that doesn't belong to any single agent — COST, LOGS, MEMORY, DOCS.

This is **neither pure agent-first nor pure task-first** (panel push-back). Agents own single-agent functions; workflows that span agents get standalone surfaces. Both axes coexist, with the cleavage line being: "does this work require coordination across agents, or is it one agent's domain?"

**Wordmark `NAKAMA / BRIDGE` becomes clickable → `/bridge` home**. Implementation note: current `<div class="chassis-wordmark">` (`_chassis_nav.html:35`) must change to `<a>` with `cursor: pointer` + hover state. Removes the dedicated `BRIDGE` nav item.

**Theme toggle migrates** to a chassis-nav right slot. This requires a code change to `static/shosho/theme.js`: the script currently auto-injects a fixed bottom-right pill into every `<body>` (theme.js:41-75, tokens.css:259-263). After this ADR, `theme.js` exposes a mount-point API — if a `[data-theme-toggle-slot]` element exists in the page (chassis-nav provides one), inject there; else fall back to the floating pill. Bridge pages (which use chassis-nav) get the top-right slot; pages without chassis-nav keep the pill. **No duplicate controls.**

**CONTEXT-MAP "chassis-nav" principle is rewritten** (in the same PR as this ADR) from "agent-rooted 頂層直到擠爆才 dropdown" to a component-agnostic principle:

> Bridge nav organizes by **task frequency × semantic similarity**: highest-frequency cross-agent workflows get top-level slots; agents collapse into a single Fleet dropdown (with the dashboard grid as primary visual entry); lower-frequency cross-cutting ops collapse into an Ops dropdown. The principle is independent of the implementation pattern (horizontal dropdown today; left sidebar is a known future option — see §Out of scope).

### 2. Dashboard agent grid — 8 cards, drawer demoted

`AGENT_ROSTER` reduces from 9 to 8. **Sunny is removed on the grounds that Sunny is the platform/chassis (the entire `thousand_sunny/` web layer), not a crew member.** The Bridge is the control deck *of* Thousand Sunny. CONTEXT-MAP's `Thousand Sunny` term already names this; v2 fixes the inconsistency by removing the agent-roster entry. Readout strip "fleet on watch" denominator changes from `/9` to `/8`.

**Card click behaviour — direct nav with drawer-on-affordance**:
- The main body of each agent card becomes an `<a>` (or click handler) navigating directly to the agent's console.
- A small explicit affordance (icon button or hover trigger) on each card opens the drawer for at-a-glance stats (today's tokens / runs / cost / model / state). Drawer is **demoted from middle-layer preview to optional monitoring view** (panel push-back #10 / #27).
- This preserves the dashboard's monitoring function (Gemini §3 §6: drawer = monitoring board, deleting changes dashboard purpose) while removing the dead-end behaviour for cards without an OPEN INTERFACE target.

**Card click target table** (URLs verified against actual routes):

| Card | Built? | Click target | Notes |
|---|---|---|---|
| Robin | ✅ | `/` (Robin root) | Codex finding: no `/robin/kb` route exists; canonical Robin home is `/`. ADR commits to keeping VAULT-equivalent functionality at `/` until Robin gets a proper console URL. |
| Nami | future | (non-clickable, disabled state) | "Web 介面尚在規劃" tooltip on hover |
| Zoro | ✅ | `/bridge/zoro/keyword-research` | Zoro's only built tool surface is keyword research. Acceptable as console-de-facto until/unless Zoro grows additional tools. |
| Brook | ✅ | `/brook/handoff` | Per ADR-027 Brook is a context bridge, not full local console; `/brook/handoff` is the canonical context-handoff surface. |
| Sanji | no | (non-clickable, disabled state) | tooltip + "Under Construction" |
| Franky | ✅ | `/bridge/franky` | Absorbs HEALTH as a tab/section (see §3) |
| Usopp | no | (non-clickable, disabled state) | tooltip + "Under Construction" |
| Chopper | no | (non-clickable, disabled state) | tooltip + "Under Construction" |

**Unbuilt agents render disabled** (panel push-back #11 — both Codex and Gemini): visually distinct (grayed / lower contrast / no hover state), `aria-disabled="true"`, tooltip explaining planned status. They are NOT clickable and do NOT lead to stub "Under Construction" pages. The grid is honest about which agents the operator can actually drive today. Stubs are not added.

### 3. Single-agent topic absorption — agent consoles aggregate their domain

Topic pages that are **single-agent functions** collapse into that agent's console as sub-sections:

- **Franky console** absorbs `/bridge/health`. After PR-4, `/bridge/health` content merges into `/bridge/franky` as a tabbed section (or `/bridge/health` 301-redirects to `/bridge/franky#health`). HEALTH is removed from chassis-nav.
- **Brook console** absorbs `/bridge/repurpose` (and `repurpose_detail`). REPURPOSE is removed from chassis-nav. Brook console (`/brook/handoff`) gains a "REPURPOSE" section/tab linking to existing `repurpose_list.html` / `repurpose_detail.html`.
- **Brook console** + **Zoro console** each expose a soft link `「SEO 中控台 →」` pointing at `/bridge/seo` (the cross-agent workflow, not absorbed — see §4).
- **VAULT external link is removed from chassis-nav.** Per Codex finding, `/` is already Robin's root URL — Robin console (the Robin card click target) is the canonical entry to Obsidian-equivalent functionality. No regression.

Principle: **single-agent function → that agent's console; cross-agent workflow → top-level nav slot.**

### 4. Cross-agent workflows — top-level slots (DRAFTS, SEO)

Pages that genuinely span multiple agents stay as **top-level nav items** (v2 change from v1's `Workflows ▾` dropdown — panel push-back #9):

- **DRAFTS** (`/bridge/drafts`) — ADR-006 HITL approval queue, fed by Brook (long-form) + Sanji (social), consumed by Usopp (publishing). Top-level because: (a) it's the highest-frequency operator action; (b) Usopp console is Under Construction so the queue can't be absorbed into an unbuilt console; (c) hiding behind a 2-item dropdown adds friction for no scan-ability gain.
- **SEO 中控台** (`/bridge/seo`) — CONTEXT-MAP-frozen cross-agent workflow hub spanning **Zoro (keyword research) + Brook (audit / enrich) + Franky (ranking telemetry)** (v2 correction per ADR-008). Stays standalone per existing freeze. SEO sub-pages (`/bridge/seo/audits/*`, `/bridge/seo/posts/{id}/audits`) remain unchanged.

Dashboard readout-strip `DRAFTS · PENDING` cell (`index.html:66-75`) retains its direct-link shortcut behaviour.

### 5. Cross-agent observation — Ops ▾ group

Telemetry / instrumentation surfaces not owned by any agent collapse under `Ops ▾`:

- **COST** (`/bridge/cost`) — token cost telemetry
- **LOGS** (`/bridge/logs`) — FTS5 over structured nakama logs
- **MEMORY** (`/bridge/memory`) — agent memory store viewer + PATCH editor
- **DOCS** (`/bridge/docs`) — FTS over `docs/` + `memory/` markdown (maintainer reference tool)

All four routes are unchanged; only chassis-nav grouping changes. Chinese labels: `成本 · 日誌 · 記憶 · 文件搜尋`.

### 6. Slug stability + nav_active migration

Existing `nav_active` slugs used by **19 app templates** (Codex-corrected count) remain valid (macro signature preserved). New behaviour:

- `nav_active='bridge'` — wordmark active state (no nav item highlights).
- `nav_active='zoro' | 'franky'` — Fleet ▾ group + the agent item inside it both highlight (`aria-current` on the leaf).
- `nav_active='drafts' | 'seo'` — top-level item highlights (no group; these are top-level in v2).
- `nav_active='cost' | 'logs' | 'memory' | 'docs'` — Ops ▾ group + the leaf item highlight.
- `nav_active='health'` — macro normalizes to `nav_active='franky'` (HEALTH absorbed). Backward compat: macro accepts `health` and renders franky-active.
- `nav_active='repurpose'` — macro normalizes to `nav_active='brook'`. Backward compat: macro accepts `repurpose` and renders brook-active.
- `nav_active='vault'` — no current app template uses this slug (Codex-verified). Removed cleanly with no migration needed.

`_chassis_nav.html` macro is rewritten; the 19 consumer templates are checked for breakage but signatures stay backward-compatible.

## Consequences

### Positive

- Nav goes from 12 flat items (two rows) to 5 top-level slots (one row): wordmark + Fleet ▾ + DRAFTS + SEO + Ops ▾.
- Agent cards navigate directly to consoles — removes 8 dead-end drawers (7 with no exit + 1 with a broken URL).
- Drawer preserved as monitoring affordance — dashboard's status-board purpose retained (Gemini push-back).
- Unbuilt agents disabled in grid — honest UI, no false affordances (panel push-back).
- Cross-agent workflows (DRAFTS, SEO) get top-level slots — earned by frequency + cross-agent nature.
- Single-agent topics (HEALTH, REPURPOSE) move closer to their owning agent — system reads as agent-first internally without imposing agent-first on cross-agent work.
- SEO 中控台 freeze (ADR-008/009/012) preserved + corrected (Franky included).
- Theme toggle gets a normalized top-right slot via mount-point API; floating pill fallback covers non-chassis pages — no duplicates.
- CONTEXT-MAP principle becomes component-agnostic — future sidebar pivot doesn't require another ADR reversal.

### Negative / risk

- **CONTEXT-MAP needs 4 updates atomically** (not just 1): `chassis-nav` term (rewrite principle), `Thousand Sunny` term (clarify platform vs agent), `surface` term (correct outdated example), add Fleet/DRAFTS/SEO/Ops grouping concept. All bundled into PR-1.
- **Drawer code shrinks but doesn't disappear** — small affordance-trigger + drawer DOM stays; only the auto-open-on-card-click behaviour is removed.
- **`/robin/kb` is broken today** — fixed by routing Robin card to `/` in PR-3 (no new Robin route required; `/` already serves Robin per `robin.py:237`).
- **`/bridge/health` and `/bridge/repurpose` absorption** is a multi-step refactor — PR-4 + PR-5 sequenced after the nav/drawer overhaul ships green.
- **19 templates audit** required to ensure `nav_active` slugs still render correctly after macro rewrite.

### Neutral

- CONTEXT-MAP "breadcrumb" term unchanged.
- Sub-routes under `/bridge/seo/*`, `/bridge/zoro/keyword-research/*`, `/bridge/franky` unchanged.
- API routes (`/bridge/api/agents`, `/bridge/api/cost`, `/bridge/api/memory*`) unchanged.
- Mobile/responsive behaviour gets a brief planning note (see §Out of scope) but no implementation in this ADR.

## Open questions resolved

Panel raised three architectural escalations. All three rejected, with rationale:

### A. Sidebar (left vertical nav) vs horizontal dropdown — Codex + Gemini both pushed sidebar

**Rejected for this ADR.** Sidebar is the right pattern for large admin dashboards (panel correctly cites AWS Console, Vercel, Grafana, Linear). However:

1. Bridge's internal pages (cost.html, franky.html, seo.html, etc.) are already laid out for full-width chassis-nav header. A sidebar pivot requires reflowing every page's internal layout — significantly larger scope than IA cleanup.
2. Bridge's actual problem is **IA scatter**, not space exhaustion. Reducing 12 items to 5 top-level slots solves the stated pain.
3. A Claude Design visual exploration pass is planned next (修修 confirmed). That phase is where sidebar belongs as a candidate, not at the IA-decision layer.

Sidebar is recorded in §Out of scope as a known candidate for the visual exploration phase. CONTEXT-MAP "chassis-nav" principle is rewritten to be component-agnostic so a future sidebar pivot doesn't require another ADR reversal.

### B. Task-first IA (Content / System / Analytics) vs agent-first (Fleet / Workflows / Ops) — Gemini strongly pushed task-first

**Rejected as framed.** Gemini's argument ("organizing UI to match database schema, not user mental model") assumes a generic user. 修修 is the *system author + operator*. Agent identity is a first-class ontological concept in this system (ADR-001 freezes agent roles; ADR-012 freezes Zoro/Brook 向外/對內 philosophy; ADR-027 freezes Brook scope; ADR-014 freezes Repurpose as Brook engine). For this specific user, agents are *not* an implementation detail — they are the primary mental model.

**However, panel correctly identified that pure agent-first breaks down for cross-agent workflows.** DRAFTS (Brook + Sanji → Usopp), SEO (Zoro + Brook + Franky), and future script-driven video (Robin + Brook + video subproject) cannot be cleanly absorbed into any single agent's console.

v2 resolves this by adopting a **dual-axis frame** (§1): agents own single-agent functions; cross-agent work earns top-level workflow slots. This is neither pure agent-first nor task-first. The cleavage line is explicit and stable — "does this work require coordination across agents, or is it one agent's domain?"

### C. Group names (Fleet/Workflows/Ops vs Content/Agents/System) — Gemini called Ops jargon

**Partially adopted.** v2 reduces nav to 5 top-level slots, eliminating `Workflows ▾` (DRAFTS and SEO promoted top-level). Remaining groups:
- `Fleet ▾` / `艦隊` — kept, semantically tied to One Piece metaphor; works for 修修.
- `Ops ▾` / `觀測` — kept English `Ops` (修修's vocabulary in commits / ADRs), but Chinese label switched from `維運` to `觀測` (more accurate: cost/logs/memory/docs are observation surfaces, not maintenance actions).

The One Piece metaphor consistency concern (Gemini §5) is noted but minor; resolved by removing the inconsistent `Workflows` middle group entirely.

## Migration plan

Sequenced to keep each PR atomic and reviewable:

**PR-1 (this ADR + CONTEXT-MAP)** — ADR-029 + atomic CONTEXT-MAP updates (chassis-nav term rewrite, Thousand Sunny term clarification, surface term correction, add dual-axis term). No code change. Panel audits + integration matrix included as research docs.

**PR-2 — chassis-nav rewrite + theme.js mount-point API + dashboard direct-nav + disabled UC cards** (PR-2 and former PR-3 combined per Gemini push-back #19)
- Rewrite `_chassis_nav.html` macro: 5 top-level slots (wordmark + Fleet ▾ + DRAFTS + SEO + Ops ▾) + utility slot for theme toggle
- Make wordmark `<a>` with hover/cursor states
- Add `[data-theme-toggle-slot]` attribute API to `theme.js`; chassis-nav provides the slot
- Rewrite `index.html` `renderCard`: card body = `<a>` to console URL; small stats-affordance icon opens drawer
- Drawer DOM retained for the affordance; `openDrawer()` JS retained; auto-open-on-card-click removed
- Remove Sunny from `AGENT_ROSTER`; readout strip `/9` → `/8`
- 4 unbuilt agent cards render `aria-disabled` + tooltip; no Under Construction stub routes added
- Verify all 19 consumer templates render with new macro (visual smoke + lint)
- Robin card URL `/robin/kb` → `/`

**PR-3 — Franky console absorbs HEALTH**
- Merge `bridge/health.html` content into `bridge/franky.html` as a tab/section
- `/bridge/health` route returns 301 redirect to `/bridge/franky#health`
- Macro normalizes `nav_active='health'` → `'franky'` (was already in PR-2 spec, ships here when absorption is real)

**PR-4 — Brook console absorbs REPURPOSE**
- Brook console (`/brook/handoff`) gains a REPURPOSE section/tab linking to existing `repurpose_list.html`
- `/bridge/repurpose` continues to work standalone (detail page reachable directly); the second entry inside Brook console is additive
- REPURPOSE removed from chassis-nav AFTER Brook console gains the entry — owner verifies before removing the chassis-nav slot
- Macro normalizes `nav_active='repurpose'` → `'brook'`

**PR-5 — Cleanup**
- Remove unused legacy templates / dead JS branches
- Final visual QA across the 19 surfaces

Each PR is independently reversible.

## Out of scope

- **Per-agent console deep redesign** (Robin / Zoro / Brook / Franky internals) — separate Claude Design pass per console.
- **Nami's future web interface** — when built, replaces her disabled card; not part of this ADR.
- **Left sidebar pattern as a future option** — Codex + Gemini recommended for scalability. Recorded as a known candidate for the Claude Design visual exploration phase. CONTEXT-MAP principle (§1) is component-agnostic to keep the door open.
- **Mobile / narrow-viewport behaviour for dropdown nav** — Gemini flagged deferral as design debt. v2 acknowledges: in the visual exploration pass, responsive behaviour for the chassis-nav (dropdown collapse, drawer responsiveness, theme toggle placement at narrow widths) is required scope — not full design here, but explicitly named so it doesn't get forgotten.
- **`/bridge/api/*` API redesign** — not changed by this ADR.
- **Brook scope expansion beyond ADR-027 context-bridge role** — if Brook later grows a full local console, the card click target updates; out of scope here.
