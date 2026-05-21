**1. CODE GROUNDING**

The ADR’s low-level citations mostly point at real code, but several “built console” and migration claims are not grounded.

`_chassis_nav.html:40-53` is real: the macro is defined at [thousand_sunny/templates/bridge/_chassis_nav.html](/E:/nakama/thousand_sunny/templates/bridge/_chassis_nav.html:31), and the 12 flat `<a>` entries are at [lines 41-52](/E:/nakama/thousand_sunny/templates/bridge/_chassis_nav.html:41). The current wordmark is not clickable; it is a `<div class="chassis-wordmark">` at [line 35](/E:/nakama/thousand_sunny/templates/bridge/_chassis_nav.html:35).

The dashboard drawer citations are accurate. The drawer DOM is [index.html:113](/E:/nakama/thousand_sunny/templates/bridge/index.html:113), `AGENT_URLS` is [index.html:146](/E:/nakama/thousand_sunny/templates/bridge/index.html:146), `openDrawer()` is [index.html:229](/E:/nakama/thousand_sunny/templates/bridge/index.html:229), and the hidden-button branch is [index.html:243](/E:/nakama/thousand_sunny/templates/bridge/index.html:243). The drafts shortcut is also correctly cited at [index.html:66](/E:/nakama/thousand_sunny/templates/bridge/index.html:66).

`AGENT_ROSTER` exists at [thousand_sunny/routers/bridge.py:60](/E:/nakama/thousand_sunny/routers/bridge.py:60). `/bridge/seo` exists at [bridge.py:342](/E:/nakama/thousand_sunny/routers/bridge.py:342), `/bridge/health` exists at [bridge.py:1207](/E:/nakama/thousand_sunny/routers/bridge.py:1207), and `/bridge/repurpose` exists through `repurpose.page_router = APIRouter(prefix="/bridge/repurpose")` at [repurpose.py:34](/E:/nakama/thousand_sunny/routers/repurpose.py:34), with list/detail routes at [repurpose.py:147](/E:/nakama/thousand_sunny/routers/repurpose.py:147) and [repurpose.py:163](/E:/nakama/thousand_sunny/routers/repurpose.py:163). These routers are mounted in [thousand_sunny/app.py:77-84](/E:/nakama/thousand_sunny/app.py:77).

The serious grounding problem: ADR §2’s proposed click targets are not all real. `/bridge/zoro` does not exist; only `/bridge/zoro/keyword-research` and its history pages exist at [bridge_zoro.py:100](/E:/nakama/thousand_sunny/routers/bridge_zoro.py:100), [253](/E:/nakama/thousand_sunny/routers/bridge_zoro.py:253), and [309](/E:/nakama/thousand_sunny/routers/bridge_zoro.py:309). `/brook` does not exist; the mounted Brook routes are `/brook/chat`, `/brook/bridge`, and `/brook/handoff` at [brook.py:60](/E:/nakama/thousand_sunny/routers/brook.py:60), [66](/E:/nakama/thousand_sunny/routers/brook.py:66), and [72](/E:/nakama/thousand_sunny/routers/brook.py:72). `/robin/kb` also does not exist, even though the current dashboard already points Robin there at [index.html:147](/E:/nakama/thousand_sunny/templates/bridge/index.html:147). Robin’s actual root UI is `/` at [robin.py:237](/E:/nakama/thousand_sunny/routers/robin.py:237), with `/robin/read` at [robin.py:249](/E:/nakama/thousand_sunny/routers/robin.py:249) and `/robin/books` at [books.py:250](/E:/nakama/thousand_sunny/routers/books.py:250).

So: the drawer is real, the nav is real, the routes `/bridge/health`, `/bridge/repurpose`, and `/bridge/seo` are real. But ADR §2’s “built console URL” table is not safe as written. It must either use existing URLs or explicitly add root redirects/consoles before card direct-navigation ships.

**2. DRIFT DETECTION**

ADR §4 does not contradict ADR-012/CONTEXT-MAP by preserving `/bridge/seo`; preserving it is correct. ADR-012 explicitly says the SEO control center should remain topic-rooted `/bridge/seo`, not `/bridge/brook` or `/bridge/zoro`, because agent-rooting would mislead for a mixed Brook/Zoro surface ([ADR-012](/E:/nakama/docs/decisions/ADR-012-zoro-brook-boundary.md:38)). CONTEXT-MAP also freezes `SEO 中控台` as `/bridge/seo` at [CONTEXT-MAP.md:46](/E:/nakama/CONTEXT-MAP.md:46).

But ADR §4 under-describes the SEO surface as only “Zoro + Brook.” ADR-008 assigns ranking observation to Franky: Zoro chooses target keywords, Franky tracks ranking changes ([ADR-008](/E:/nakama/docs/decisions/ADR-008-seo-observability.md:18)). The current `/bridge/seo` route renders the rank-change panel as part of the page ([bridge.py:342-356](/E:/nakama/thousand_sunny/routers/bridge.py:342)). The ADR should call `/bridge/seo` a cross-agent workflow spanning Zoro, Brook, and Franky telemetry, not just Zoro + Brook.

The §6 slug migration plan does not preserve “all 34 templates” because I cannot verify 34 current consumers. In the current repo there are 37 HTML templates total, but only 19 app templates call `chassis_nav(...)`; the 20th match is `_chassis_nav.html` itself. The actual consumers are listed by `nav_active=` in [cost.html](/E:/nakama/thousand_sunny/templates/bridge/cost.html:16), [docs.html](/E:/nakama/thousand_sunny/templates/bridge/docs.html:14), [drafts.html](/E:/nakama/thousand_sunny/templates/bridge/drafts.html:15), [draft_detail.html](/E:/nakama/thousand_sunny/templates/bridge/draft_detail.html:15), [franky.html](/E:/nakama/thousand_sunny/templates/bridge/franky.html:14), [health.html](/E:/nakama/thousand_sunny/templates/bridge/health.html:14), [index.html](/E:/nakama/thousand_sunny/templates/bridge/index.html:16), [logs.html](/E:/nakama/thousand_sunny/templates/bridge/logs.html:14), [memory.html](/E:/nakama/thousand_sunny/templates/bridge/memory.html:16), two repurpose templates, five SEO templates, and three Zoro templates. No current app template uses `nav_active='vault'`.

CONTEXT-MAP needs more than the ADR’s claimed “chassis-nav reversal.” It currently says `Thousand Sunny` is the whole web presentation layer, including all web UI, Bridge dashboard, and agent routers ([CONTEXT-MAP.md:22](/E:/nakama/CONTEXT-MAP.md:22)). It also says each Agent context exposes web surfaces through `thousand_sunny/routers/<agent>.py` ([line 29](/E:/nakama/CONTEXT-MAP.md:29)), which is already false for Nami, Sanji, Usopp, Chopper, and Sunny. If ADR-029 formalizes Fleet/Workflows/Ops, CONTEXT-MAP must update `chassis-nav`, add those group terms, correct the `surface` example, and clarify Sunny-as-platform versus Sunny-as-agent. The ADR branch HEAD only adds `docs/decisions/ADR-029-bridge-ia-restructure.md`; it does not include the promised CONTEXT-MAP update.

**3. NUMERICAL / FACTUAL CLAIMS**

“12 chassis-nav items” is correct. There are exactly 12 `<a href>` entries in `_chassis_nav.html`, [lines 41-52](/E:/nakama/thousand_sunny/templates/bridge/_chassis_nav.html:41).

“9 agents in AGENT_ROSTER” is correct. The roster has Robin, Nami, Zoro, Brook, Sanji, Franky, Usopp, Chopper, and Sunny at [bridge.py:60-133](/E:/nakama/thousand_sunny/routers/bridge.py:60). The default-state claims also check out: Usopp is `hold` at [line 115](/E:/nakama/thousand_sunny/routers/bridge.py:115), Chopper is `offline` at [line 123](/E:/nakama/thousand_sunny/routers/bridge.py:123), and Sunny is `offline` at [line 131](/E:/nakama/thousand_sunny/routers/bridge.py:131). The API preserves hold/offline states at [bridge.py:1706-1711](/E:/nakama/thousand_sunny/routers/bridge.py:1706).

“Only 4 have built web consoles” is directionally true but the URLs are wrong or overstated. Franky has `/bridge/franky` ([franky.py:256](/E:/nakama/thousand_sunny/routers/franky.py:256)). Zoro has a tool surface, not a root console: `/bridge/zoro/keyword-research` ([bridge_zoro.py:100](/E:/nakama/thousand_sunny/routers/bridge_zoro.py:100)). Brook has `/brook/handoff`, and ADR-027 defines it as a context bridge, not a full local console ([ADR-027](/E:/nakama/docs/decisions/ADR-027-brook-scope-reduction-to-scaffold-and-repurpose.md:143)). Robin has root/books/reader routes, not `/robin/kb`.

“34 app templates consume `_chassis_nav.html`” is false in the current repo. I find 19 app-template consumers.

“7 of 9 agents land in a drawer with no exit” is mostly correct by button logic: only `robin` and `brook` are in `AGENT_URLS` ([index.html:146-149](/E:/nakama/thousand_sunny/templates/bridge/index.html:146)), and the button is hidden for all other agents ([index.html:243-250](/E:/nakama/thousand_sunny/templates/bridge/index.html:243)). But Robin’s configured URL is currently broken, so functionally the working exit count may be 1 of 9, not 2 of 9.

**4. ASSUMPTION PUSH-BACK**

ADR §2’s agent-first model is premature if it ships as eight direct cards plus Fleet dropdown. Four cards route to future stubs by design, and two “built” targets in the ADR table are not implemented. That creates a prettier IA over false affordances. Do not put unbuilt agents on equal visual footing with built consoles. Either disable planned agents with explicit state, or keep them in the telemetry grid but out of primary navigation until they have real operator actions.

Sunny removal is correct only if the wording changes. “Dashboard is Sunny” is too narrow. CONTEXT-MAP defines Thousand Sunny as the entire web presentation layer, not just `/bridge`. Remove Sunny from `AGENT_ROSTER` because it is platform/chassis rather than an executable agent, not because the dashboard alone absorbs it.

ADR §1’s `Workflows ▾` with only DRAFTS and SEO is not obviously better. DRAFTS and SEO are high-value, live workflows; hiding both behind a two-item dropdown adds interaction cost. A better first pass is: wordmark, `Fleet ▾`, `DRAFTS`, `SEO`, `Ops ▾`. That still reduces the top row to five items and keeps the two real workflows one click away.

Do not remove VAULT until Robin has a valid canonical home. Current VAULT goes to `/`, and `/` is also the mounted Robin root when Robin is enabled ([robin.py:237](/E:/nakama/thousand_sunny/routers/robin.py:237)). ADR §3 says Robin console can expose “前往 Obsidian Vault,” but no `/robin/kb` route exists. Removing VAULT before normalizing Robin is a regression.

Theme toggle migration also needs implementation discipline. `theme.js` currently injects a fixed `.sho-theme-toggle` into every page body ([theme.js:41-75](/E:/nakama/thousand_sunny/static/shosho/theme.js:41)), and CSS fixes it bottom-right ([tokens.css:259-263](/E:/nakama/thousand_sunny/static/shosho/tokens.css:259)). If the chassis-nav also renders a top-right toggle, Bridge pages will duplicate controls unless `theme.js` gains a mount-point API or an opt-out. Do not ship two placement models without code-level reconciliation.

**5. ALTERNATIVES NOT CONSIDERED**

Alternative A: make Bridge workflow-first and kill the agent grid. Primary nav becomes `DRAFTS`, `SEO`, `REPURPOSE`, `Ops ▾`, with `Fleet ▾` only for built agent consoles. The dashboard becomes a workflow launcher plus telemetry summary. Tradeoff: it weakens the One Piece agent metaphor, but it matches actual work: review drafts, audit SEO, repurpose content, inspect ops.

Alternative B: hybrid high-frequency nav. Use `NAKAMA / BRIDGE`, `Fleet ▾`, top-level `DRAFTS`, top-level `SEO`, and `Ops ▾`. Keep dashboard cards, but only built cards are clickable. Planned agents render as status cards without primary-action affordance. Tradeoff: less taxonomically pure than §1, but it solves wrapping while preserving one-click access to the two live workflows.

Alternative C: left sidebar. Admin dashboards commonly use a collapsible sidebar with sections: Fleet, Workflows, Ops. This avoids horizontal wrap entirely, supports future pages, and makes keyboard focus order clearer than hover dropdowns. Tradeoff: bigger visual redesign and more responsive-layout work. If Bridge is becoming the operator control surface, this is the architecture that scales best.

**6. FINAL VERDICT**

Approve with modifications. Do not approve ADR-029 as-is.

Required changes:

1. Fix §Context and §2 URL facts. Replace `/robin/kb`, `/bridge/zoro`, and `/brook` with existing URLs or explicitly add redirects/consoles before direct-card navigation ships. Current claims are code-inaccurate.

2. Rewrite §1 navigation. Keep `DRAFTS` and `SEO` top-level unless the ADR proves the dropdown is better for the owner’s real workflows. A two-item Workflows dropdown is weak IA.

3. Rewrite §2 Sunny language. Remove Sunny from the roster as platform/chassis, not “dashboard is Sunny.” Update CONTEXT-MAP accordingly.

4. Fix §6 migration numbers and sequencing. The current repo has 19 app-template consumers, not 34. Do not normalize `health → franky` and `repurpose → brook` before those absorptions actually ship.

5. Add a theme-toggle implementation requirement. `theme.js` must mount into chassis-nav when a slot exists, otherwise use the floating fallback. No duplicate controls.

Core diagnosis is right: the flat nav and dead drawer need to go. The ADR’s current facts are not strong enough to be the decision record.
