# ADR-029: Bridge IA Restructure — Agent-First Navigation

**Date:** 2026-05-21
**Status:** Proposed
**Deciders:** shosho-chang, Claude Opus 4.7
**Related:** ADR-001 (agent roles), ADR-006 (HITL approval queue), ADR-008 (SEO observability), ADR-009 (SEO solution architecture), ADR-014 (Repurpose engine), CONTEXT-MAP.md (chassis-nav term, SEO 中控台 term, breadcrumb term)

---

## Context

The Bridge web UI at `/bridge/*` is 修修's primary control surface when working outside Slack. After ~6 months of organic growth, the IA has drifted into a 12-item flat nav that overflows to two rows and mixes three different conceptual lanes (agent-rooted, topic-rooted, external) without grouping.

A 2026-05-21 grilling session ([session handoff](../../.nakama/session_handoff_2026-05-21_theme-toggle-bridge-brief.md)) audited Bridge against 修修's stated frustrations:

1. **「Navbar 已換到第二行，超級醜」** — `templates/bridge/_chassis_nav.html:40-53` ships 12 flat `<a>` items: BRIDGE / DRAFTS / SEO / ZORO / REPURPOSE / MEMORY / COST / FRANKY / HEALTH / DOCS / LOGS / VAULT. At standard viewport widths the nav wraps to two rows.
2. **「整個散落在各地，完全沒有任何架構跟邏輯」** — IA fails: agent-rooted entries (ZORO, FRANKY) coexist with topic-rooted entries (SEO, REPURPOSE) and external (VAULT → `/`) with no visual or semantic grouping. CONTEXT-MAP's freeze "agent-rooted 頂層直到擠爆才 dropdown" has hit the overflow point.
3. **Dashboard agent card → drawer preview → OPEN INTERFACE** is one click too many. Drawer (`templates/bridge/index.html:113-133`, `openDrawer()` :229-265) is conceptually a preview pane, but only Robin and Brook have entries in `AGENT_URLS` (index.html:146-149), meaning **7 of 9 agents land in a drawer with no exit** — pure dead end.
4. **L/D toggle 在 Bridge「看不到」** — solved by PR #655 (theme.js floating bottom-right pill), but the underlying symptom is that Bridge has no canonical top-right toggle slot. The 34 app pages do not share a normalized header.

### Codebase inventory (verified 2026-05-21)

**12 chassis-nav items** (`_chassis_nav.html:41-52`):

| Slug | Lane | Target | Owner agent |
|---|---|---|---|
| bridge | home | `/bridge` | (dashboard) |
| drafts | topic | `/bridge/drafts` (ADR-006 HITL queue) | Brook + Sanji → Usopp |
| seo | topic | `/bridge/seo` (SEO 中控台) | Zoro + Brook |
| zoro | agent | `/bridge/zoro/keyword-research` | Zoro |
| repurpose | topic | `/bridge/repurpose` (ADR-014 fan-out) | Brook |
| memory | topic | `/bridge/memory` | (cross-agent) |
| cost | topic | `/bridge/cost` | (cross-agent) |
| franky | agent | `/bridge/franky` | Franky |
| health | topic | `/bridge/health` | Franky |
| docs | topic | `/bridge/docs` (FTS over docs/ + memory/) | (maintainer) |
| logs | topic | `/bridge/logs` | (cross-agent) |
| vault | external | `/` (Obsidian root) | Robin |

**9 agents in `AGENT_ROSTER`** (`routers/bridge.py:60`) — only 4 have built web consoles:

| Code | Agent | Built console URL | Status |
|---|---|---|---|
| R-01 | Robin · Knowledge | `/robin/kb` | ✅ built |
| N-02 | Nami · Secretary | — (Slack-native via `gateway/handlers/nami.py`) | Web 介面 deferred |
| Z-03 | Zoro · Scout | `/bridge/zoro/keyword-research` | ✅ built |
| B-04 | Brook · Composer | `/brook/handoff` | ✅ built |
| S-05 | Sanji · Community | — | Under Construction |
| F-06 | Franky · Systems | `/bridge/franky` | ✅ built |
| U-07 | Usopp · Publisher | — | Under Construction |
| C-08 | Chopper · Counsel | — | Under Construction |
| D-09 | Sunny · Deck | — (conceptually = Bridge dashboard itself) | Removed from grid |

**Drawer reachability**: `AGENT_URLS` (index.html:146-149) only maps `robin` and `brook`. The other 7 agent cards open a drawer with no working OPEN INTERFACE button — the button is hidden when no URL exists (index.html:246-250).

### Cross-cutting constraints

- **CONTEXT-MAP "SEO 中控台" term** (2026-04-29) — explicitly freezes `/bridge/seo` as a cross-agent workflow hub. Any restructure must preserve this surface.
- **CONTEXT-MAP "chassis-nav" term** — declares "agent-rooted 頂層直到擠爆才 dropdown". This ADR finds that the overflow threshold has been crossed and reverses the implicit rule.
- **CONTEXT-MAP "breadcrumb" term** — already provides backward-trail; not changed by this ADR.
- **34 app templates** consume `_chassis_nav.html` directly or via macro `chassis_nav(nav_active=...)`. Slug semantics must remain stable or be migrated atomically.

## Decision

Restructure Bridge IA from **flat 12-item nav** to **agent-first model** with three semantic groups in a dropdown nav, drawer-removed direct-navigation cards, and absorbed single-agent topic pages.

### 1. Navigation structure — 3 dropdown groups + 1 utility

```
[● NAKAMA / BRIDGE]  Fleet ▾  Workflows ▾  Ops ▾                  [☀ theme toggle]
                     │        │            │
                     │        │            ├ COST      成本
                     │        │            ├ LOGS      日誌
                     │        │            ├ MEMORY    記憶
                     │        │            └ DOCS      文件搜尋
                     │        ├ DRAFTS    待審
                     │        └ SEO       中控台
                     ├ ROBIN   NAMI    ZORO   BROOK
                     └ SANJI   FRANKY  USOPP  CHOPPER
```

**Wordmark `NAKAMA / BRIDGE` is clickable → `/bridge` home.** Removes the dedicated `BRIDGE` nav item.

**Theme toggle migrates** from floating bottom-right pill (theme.js) to the chassis-nav right side, top-right utility slot. (The PR #655 floating pill remains the fallback for non-bridge pages without a normalized header — future migration tracked separately.)

### 2. Dashboard agent grid — 8 cards, drawer removed

`AGENT_ROSTER` reduces from 9 to 8 (Sunny removed — the dashboard *is* Sunny / 整合甲板). Readout strip "fleet on watch" denominator changes from `/9` to `/8`.

Each card navigates **directly** to its agent console URL on click. The drawer (`drawer-overlay`, `openDrawer()`, `closeDrawer()`, `openAgentInterface()` in `index.html:113-283`) is **deleted in its entirety**. No middle preview layer.

Card click target table:

| Card | Built? | Click target |
|---|---|---|
| Robin | ✅ | `/robin/kb` |
| Nami | future | `/bridge/nami` (Under Construction page) |
| Zoro | ✅ | `/bridge/zoro` (Zoro console — see §3) |
| Brook | ✅ | `/brook` (Brook console — see §3) |
| Sanji | no | `/bridge/sanji` (Under Construction page) |
| Franky | ✅ | `/bridge/franky` (Franky console — see §3) |
| Usopp | no | `/bridge/usopp` (Under Construction page) |
| Chopper | no | `/bridge/chopper` (Under Construction page) |

**Under Construction pages** are stub landings with: agent role description, current state chip, "此 agent 介面尚在規劃" message, and a back link to `/bridge`. They use the standard chassis-nav (so the 8-card grid concept stays internally consistent — every card resolves to a real URL).

### 3. Single-agent topic absorption — agent consoles aggregate their domain

Topic pages that are **single-agent functions** collapse into that agent's console as sub-sections:

- **Franky console** absorbs `/bridge/health`. After this ADR, `/bridge/health` either redirects to `/bridge/franky#health` or is merged into the Franky console template as a tabbed section. HEALTH is removed from chassis-nav.
- **Brook console** absorbs `/bridge/repurpose` (and `repurpose_detail`). REPURPOSE is removed from chassis-nav. The Brook console gains a "REPURPOSE" section/tab linking to the existing `repurpose_list.html` / `repurpose_detail.html` flow.
- **Brook console** + **Zoro console** each expose a soft link `「SEO 中控台 →」` pointing at `/bridge/seo` (the cross-agent workflow, not absorbed — see §4).
- **Robin console** is the canonical KB entry point; the `VAULT` external link is removed from chassis-nav (Robin console exposes a "前往 Obsidian Vault →" link if needed for direct Obsidian root access).

Principle: **single-agent function → that agent's console; cross-agent workflow → Workflows group.**

### 4. Cross-agent topic preservation — Workflows group

Pages that genuinely span multiple agents stay as standalone surfaces under the `Workflows ▾` group:

- **DRAFTS** (`/bridge/drafts`) — ADR-006 HITL approval queue, fed by Brook (long-form) + Sanji (social), consumed by Usopp (publishing). Stays standalone because Usopp console is Under Construction and the queue is live now (cannot be absorbed into an unbuilt console).
- **SEO 中控台** (`/bridge/seo`) — CONTEXT-MAP-frozen cross-agent workflow hub spanning Zoro (keyword research) + Brook (audit / enrich). Stays standalone per existing freeze. SEO sub-pages (`/bridge/seo/audits/*`, `/bridge/seo/posts/{id}/audits`) remain unchanged.

Dashboard readout-strip `DRAFTS · PENDING` cell (`index.html:66-75`) retains its direct-link shortcut behavior.

### 5. Cross-agent observation — Ops group

Telemetry / instrumentation surfaces not owned by any agent collapse under `Ops ▾`:

- **COST** (`/bridge/cost`) — token cost telemetry
- **LOGS** (`/bridge/logs`) — FTS5 over structured nakama logs
- **MEMORY** (`/bridge/memory`) — agent memory store viewer + PATCH editor
- **DOCS** (`/bridge/docs`) — FTS over `docs/` + `memory/` markdown (maintainer reference tool)

All four routes are unchanged; only chassis-nav grouping changes.

### 6. Slug stability + nav_active migration

Existing `nav_active` slugs used by 34 templates remain valid (the macro signature is preserved). New behaviour:

- `nav_active='bridge'` — wordmark active state (no nav item highlights).
- `nav_active='zoro' | 'franky'` — Fleet ▾ group + the agent item inside it both highlight (aria-current on the leaf).
- `nav_active='drafts' | 'seo'` — Workflows ▾ group + the leaf item highlight.
- `nav_active='cost' | 'logs' | 'memory' | 'docs'` — Ops ▾ group + the leaf item highlight.
- `nav_active='health'` — maps to Franky console (HEALTH absorbed); `_chassis_nav.html` macro accepts the slug for backward compat but renders as `nav_active='franky'`.
- `nav_active='repurpose'` — maps to Brook console; macro normalizes to `nav_active='brook'` (new slug).
- `nav_active='vault'` — removed; pages previously using it use `nav_active='robin'` if they're inside Robin console, otherwise no active state.

`_chassis_nav.html` macro is rewritten; usage in 34 templates is checked for breakage but signatures stay backward-compatible.

## Consequences

### Positive

- Nav goes from 12 flat items (two rows) to 4 groups (one row), with grouping signalling the three operational lanes (Fleet / Workflows / Ops).
- Agent cards navigate directly — removes 7 dead-end drawers and one extra click for the 2 working ones.
- Every agent has a canonical home (the card → console), eliminating the "ZORO duplicated in card + nav" symptom.
- Single-agent topic pages (HEALTH, REPURPOSE) move closer to their owning agent, making the system legible from an agent-first reading.
- SEO 中控台 freeze (ADR-008/009) preserved without contradiction.
- Theme toggle gets a normalized top-right slot in chassis-nav (no more bottom-right pill on Bridge surfaces).

### Negative / risk

- **Reverses CONTEXT-MAP "chassis-nav" principle** "agent-rooted 頂層直到擠爆才 dropdown" → now "主動三分組 dropdown，agent 由 grid 為主入口". CONTEXT-MAP must be updated atomically in the same PR.
- **Drawer code deletion** loses the "today's stats per agent" preview surface. The same numbers are still visible on the card itself (TOK / RUN / MODEL footer) so loss is small.
- **Under Construction pages** for Nami / Sanji / Usopp / Chopper require 4 new stub templates. Low effort but adds files.
- **`/bridge/health` and `/bridge/repurpose` absorption** is a multi-step refactor — cannot ship in a single PR safely. ADR ships first; migration sequenced over multiple PRs (see Migration plan).
- **34 templates audit** required to ensure `nav_active` slugs still render correctly after macro rewrite.

### Neutral

- CONTEXT-MAP "breadcrumb" term unchanged.
- Sub-routes under `/bridge/seo/*`, `/bridge/zoro/keyword-research/*`, `/bridge/franky` unchanged.
- API routes (`/bridge/api/agents`, `/bridge/api/cost`, `/bridge/api/memory*`) unchanged.

## Migration plan

Sequenced to keep each PR atomic and reviewable:

**PR-1 (this ADR)** — ADR-029 + CONTEXT-MAP update (chassis-nav term reverses; new term entries for Fleet/Workflows/Ops groups). No code change.

**PR-2 — chassis-nav rewrite + Under Construction pages**
- Rewrite `_chassis_nav.html` macro: 3-dropdown groups + utility slot
- Add 4 stub templates: `bridge/nami.html`, `bridge/sanji.html`, `bridge/usopp.html`, `bridge/chopper.html`
- Add 4 routes in `routers/bridge.py` for Under Construction pages
- Verify all 34 templates render with new macro (auto-test or visual smoke)
- Move theme toggle to chassis-nav right slot; remove floating pill on bridge pages only (other pages keep pill until they get a header)

**PR-3 — dashboard drawer removal + direct nav**
- Delete `drawer-overlay` block from `index.html` (lines ~113-133)
- Delete `openDrawer` / `closeDrawer` / `openAgentInterface` JS (lines ~229-283)
- Rewrite `renderCard` so the card `<div>` is wrapped in an `<a>` (or `onclick=window.location.href=...`)
- Expand `AGENT_URLS` to all 8 agents (4 real + 4 Under Construction stubs)
- Remove Sunny from `AGENT_ROSTER`; readout strip `/9` → `/8`

**PR-4 — Franky console absorbs HEALTH**
- Merge `bridge/health.html` content into `bridge/franky.html` as a tab/section
- `/bridge/health` route returns 301 redirect to `/bridge/franky#health` (or merged page)
- Remove HEALTH from chassis-nav after merge ships green

**PR-5 — Brook console absorbs REPURPOSE**
- Add REPURPOSE section/tab to Brook console (links to existing `repurpose_list.html`)
- `/bridge/repurpose` continues to work standalone (used by detail page); REPURPOSE just gains a second entry inside Brook console
- Remove REPURPOSE from chassis-nav after Brook console gains the entry

**PR-6 — Cleanup**
- Remove unused legacy templates if any
- Final visual QA across the 34 surfaces

Each PR is independently reversible.

## Out of scope

- Per-agent console deep redesign (Robin/Zoro/Brook/Franky internals) — separate effort, likely Claude Design pass per console.
- Nami's future web interface — when built, replaces her Under Construction page; not part of this ADR.
- Sunny becoming a real agent in the future — if it ever does, the dashboard-as-Sunny mapping is re-examined.
- Mobile / narrow-viewport behavior for dropdown nav — to be specified in PR-2 design pass.
- `/bridge/api/*` API redesign — not changed by this ADR.

## Open questions for panel review

1. Is the dropdown nav (`Fleet ▾ Workflows ▾ Ops ▾`) genuinely simpler than the flat 12 for keyboard / accessibility / scan-ability? Or trading one form of friction for another?
2. Is **absorbing single-agent topics into agent consoles** vs **keeping them as standalone Workflows entries** the right cleavage line? Specifically: is REPURPOSE absorption into Brook actually better than leaving it at the same level as SEO?
3. Is the Sunny removal from grid (8 cards) the right call, vs keeping Sunny as a "you are here" self-reference card?
4. Does the migration sequencing (6 PRs) carry risk that v2 should consolidate (e.g. ship chassis-nav + drawer removal together)?
5. Is there a missing concern around 34-template audit — are there templates that reference `nav_active='health'` or `nav_active='repurpose'` we haven't traced?
