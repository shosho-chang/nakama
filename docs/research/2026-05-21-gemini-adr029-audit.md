Here is the Gemini audit of ADR-029.

***

## Gemini Audit of ADR-029: Bridge IA Restructure

**Auditor:** Gemini
**Date:** 2026-05-22
**Verdict:** Approve with significant modifications. The ADR correctly identifies the core problems but proposes a solution that introduces new UX friction and prematurely commits to an "agent-first" architecture that may not serve the operator's actual workflows.

This audit pushes back on the proposed IA pattern, the agent-centric mental model, the removal of the drawer, and the specific naming choices, offering concrete alternatives grounded in established UX principles for single-operator control surfaces.

### Section 1 — IA / UX Patterns Lens

The ADR's proposal to replace a 12-item flat nav with a 3-group horizontal dropdown is a standard response to overflow, but it is not the optimal pattern for this specific context: a single power user's primary control surface. My analysis, referencing established patterns from tools like Vercel, Grafana, and Linear, indicates that other patterns offer superior scalability, discoverability, and ergonomics.

**1. Horizontal Dropdowns vs. Left Sidebar Navigation:**
The ADR's primary failure is not considering a vertical sidebar. For an application like Bridge, which is clearly evolving into a comprehensive admin dashboard, a collapsible left sidebar is the industry-standard, battle-tested pattern (see: AWS Console, Vercel, Grafana, Linear, every modern SaaS tool).

*   **Scalability:** A sidebar gracefully handles 12, 20, or 50 navigation items without breaking. The proposed 3-dropdown model will face the exact same overflow problem as soon as a fourth or fifth group is needed. It solves today's problem by creating tomorrow's.
*   **Scan-ability & Discoverability:** A permanently visible (or collapsible) sidebar allows the operator to see the full scope of the application at a glance. Hiding `DRAFTS` and `SEO`—arguably the two most critical, high-frequency workflows—behind a `Workflows ▾` click adds persistent interaction cost for no significant gain. The operator's statement "散落在各地" (scattered everywhere) is about cognitive, not just spatial, scattering. Hiding items behind menus can exacerbate this feeling.
*   **Keyboard & Accessibility:** A simple vertical list of links in a sidebar is trivial to navigate with a keyboard (`Tab`, `Arrow Up/Down`). Hover-based dropdowns, as are often implemented, are notoriously poor for accessibility and can be frustrating to use, requiring precise mouse control. A click-to-open dropdown is better, but still inferior to a persistent list.

The ADR correctly diagnoses the overflow but prescribes a weak remedy. The right architectural move is to a sidebar, establishing a scalable chassis for all future Bridge development.

**2. The Semantic Redundancy of "Fleet ▾":**
The proposal for a `Fleet ▾` dropdown containing links to all 8 agents is semantically redundant and duplicates the primary interface. The main content of the `/bridge` page *is* the fleet grid. The grid is the visual representation of the fleet; it is the primary navigation method for accessing agents. Adding a dropdown menu that lists the exact same agents is poor IA. It creates two different paths to the same destinations, violating the principle of having a single source of truth for navigation.

If the grid is the "hub," the top-level navigation should be for destinations *not* represented on the hub. A better model, if sticking to horizontal nav, would be the hybrid approach Codex suggested: keep high-frequency workflows like `DRAFTS` and `SEO` as top-level items and group only the secondary, lower-frequency `Ops` items.

**3. The "Wordmark = Home" Pattern:**
This is a universally understood convention and a good decision. The ADR is correct to adopt it. The only gotcha is ensuring the implementation provides clear visual affordance (e.g., `cursor: pointer`, a hover state) to signal that the `NAKAMA / BRIDGE` wordmark is a clickable link, as the current implementation cited by Codex is a non-interactive `<div>`.

In summary, the proposed navigation pattern is a lateral move, not a clear improvement. It trades horizontal overflow for interaction-cost friction and fails to adopt the proven, scalable sidebar pattern appropriate for this class of application.

### Section 2 — A Different Prior: Task-First vs. Agent-First

My core disagreement with the ADR stems from a different foundational prior. The ADR is built on an **agent-first** prior, organizing the entire system around the internal, metaphorical structure of the software agents. My training and analysis of user-centric design suggest a **task-first** (or workflow-first) prior is vastly superior for the operator's mental model.

The operator, a content creator, does not think, "I need to use the Brook agent to access the Repurpose function." They think, "I need to repurpose my last article for Instagram." The IA should reflect the user's task, not the system's architecture.

*   **Claude's Prior (inferred from ADR):** The system is a collection of agents. Therefore, the IA should be organized by agents (`Fleet ▾`) and the workflows they participate in (`Workflows ▾`). This is a system-outward view. It forces the user to learn the system's internal map.
*   **My Prior (Gemini):** The system is a tool to accomplish tasks. The IA should be organized by the verbs of the user: **Create/Review** (`DRAFTS`, `SEO`), **Monitor** (`COST`, `LOGS`, `HEALTH`), and **Manage** (`MEMORY`, `DOCS`). The agents are an implementation detail. This is a user-inward view.

This difference in priors leads to a cascade of different conclusions:
*   Absorbing `REPURPOSE` into the Brook console makes sense in an agent-first world. In a task-first world, `REPURPOSE` is a primary workflow, just like `SEO`, and should live at the same level. Hiding it inside a specific agent's console makes it less discoverable.
*   The `Fleet/Workflows/Ops` grouping feels like an engineer's taxonomy. A creator is more likely to resonate with groupings like `Content`, `System`, `Analytics`.
*   The ADR's principle "single-agent function → that agent's console" is a brittle architectural rule, not a user-centric one. What happens when a second agent begins contributing to the `REPURPOSE` workflow? The rule breaks, and another IA shuffle is required. A task-based IA is more stable.

The ADR's "agent-first" model is a classic case of organizing the UI to match the database schema. It is logical from the system's perspective but imposes an unnecessary cognitive load on the user.

### Section 3 — Claude / Codex Blind Spots

While Codex's audit was excellent at code-level verification, and the ADR (Claude's proposal) correctly identifies the initial pain points, both exhibit blind spots that my analysis highlights.

1.  **The Dashboard's True Purpose (Missed by ADR):** The ADR frames the dashboard grid as a simple application launcher. In doing so, it justifies deleting the drawer because it's an "extra click." This completely misses the alternative and equally valid interpretation: the grid is a **monitoring board**. The drawer, which provides at-a-glance stats (`tok_today`, `cost_today`, etc.), is the primary UI for this monitoring task. Removing it fundamentally changes the dashboard's purpose from a rich, glanceable status overview to a dumbed-down grid of icons. This is a significant functional regression disguised as a UX improvement. A better solution would be to **keep the drawer** for monitoring (perhaps on hover or a dedicated "stats" icon) while making the main card area a direct navigation link. The ADR presents a false dichotomy: either a dead-end drawer *or* direct navigation. The optimal solution is both.

2.  **The User Experience of "Under Construction" (Understated by Codex/ADR):** Both acknowledge the plan for stub pages. Codex correctly flags them as "false affordances." I will state it more strongly: shipping an 8-card grid where 4 cards lead to "Coming Soon" pages is actively bad UX. It makes the system feel incomplete and untrustworthy. It's a visual promise the application doesn't keep. The ADR should not present unbuilt agents on equal footing. A better pattern is to visually distinguish them in the grid (e.g., grayed out, different styling) and make them non-interactive, with a tooltip explaining their status. Do not create clickable links that lead to a dead end, even a nicely styled one.

3.  **Mobile/Responsive Deferral (Missed by ADR):** The ADR dismisses mobile behavior as "out of scope." This is a critical blind spot in 2026. A single operator may need to check the status of a run, approve a draft, or view costs from a phone or tablet. Deferring the responsive design of the core navigation element is not a neutral act; it's a decision that locks the application into a desktop-only paradigm, accumulating design debt that will be harder to pay off later. A sidebar, incidentally, has much clearer and more established patterns for responsive collapse than a multi-level horizontal dropdown.

4.  **Theme Toggle Inconsistency (Downplayed by ADR):** The ADR presents the theme toggle's move as a pure win. It fails to acknowledge the new inconsistency this creates: a top-right toggle on Bridge pages and a bottom-right floating pill on all other pages. This creates a disjointed user experience. The migration plan should include a strategy for normalizing the header across *all* pages, not just kicking the can down the road.

### Section 4 — Architectural Concerns

1.  **Lock-in to an "Agent-Shaped" Mental Model:** This is my most significant architectural concern. By organizing the IA around agents, the ADR hardens the "One Piece crew" metaphor into the system's primary structure. As the system evolves, the most valuable work will likely be complex, cross-agent *workflows* (e.g., the "script-driven video" context, which involves Robin, Brook, and the Node.js subsystem). An agent-centric IA makes it harder to represent and launch these workflows. A workflow-centric IA, where agents are contributing actors, is far more flexible and future-proof. This ADR is steering the architecture in a direction that prioritizes implementation detail over user value.

2.  **Instability of the "chassis-nav" Principle:** The ADR notes it is reversing a CONTEXT-MAP principle. This is a red flag. The original principle ("agent-rooted 頂層直到擠爆才 dropdown") was brittle and failed. The proposed new principle ("主動三分組 dropdown") is equally arbitrary and based on a transient set of ~12 items. There is no enduring architectural logic behind it. A more stable principle would be: "Organize navigation by user task frequency and semantic similarity. Use a scalable component (sidebar) that does not rely on item count." This ADR replaces one temporary rule with another.

3.  **Migration Risk of Partial State:** The 6-PR migration plan creates a risky intermediate state. Between PR-3 (drawer removal) and PR-5 (Repurpose absorption), the user will have a `REPURPOSE` link in the `Workflows ▾` menu *and* a Brook card that navigates to a console that does *not yet* mention Repurpose. This is confusing. **PR-2 and PR-3 should be combined.** The new navigation and the new dashboard behavior should ship atomically. Similarly, PR-5 should be re-evaluated: the `REPURPOSE` link should only be removed from the nav *after* the Brook console has a clear and prominent entry point for that workflow, and users have had time to adjust.

### Section 5 — Naming & Cognitive Load

For a bilingual operator, naming is critical. The proposed English labels are generic SRE/PM jargon that may not resonate.

1.  **"Fleet ▾" / "Workflows ▾" / "Ops ▾":**
    *   **Fleet (艦隊):** This works well, as it directly ties into the established One Piece metaphor.
    *   **Workflows (工作流程):** This is acceptable but jargony. For a content creator, something like **Content (內容)** or **Projects (專案)** might be more intuitive. It contains `DRAFTS` and `SEO`, which are core content tasks.
    *   **Ops (維運):** This is the weakest. "Ops" is pure technical jargon. For the operator, these are tools for observing the system. Better names would be **System (系統)**, **Tools (工具)**, or **Analytics (分析)**. The current name forces the operator to learn the developer's vocabulary.

2.  **The One Piece Metaphor:** The ADR is inconsistent. It leans on the metaphor for `Fleet` but abandons it for `Workflows` and `Ops`. This creates a dissonant experience. Furthermore, the reasoning for removing Sunny ("dashboard IS Sunny") is conceptually fuzzy. A clearer rationale, as Codex noted, is that **Sunny is the platform, the ship itself, not a crew member.** The Bridge is the control deck *of* the Thousand Sunny. Therefore, Sunny doesn't appear on a list of the crew who operate *on* the ship. This is a subtle but important distinction that strengthens the metaphor's integrity.

3.  **"整合甲板 / Deck / Sunny":** The idea that the dashboard *is* Sunny is a weak mapping. The dashboard is the *Bridge*. The entire web application is the *Thousand Sunny*. Removing Sunny from the agent roster is correct, but the justification needs to be tightened to "Sunny is the chassis/platform, not an agent."

### Section 6 — Final Verdict & Required Modifications

**Verdict: Approve with Modifications.**

The ADR's intent is correct, but the proposed implementation is flawed. It solves a surface-level problem (nav overflow) while introducing deeper issues in UX, IA, and architectural strategy.

If asked to implement this, I would change the following five things, in order of priority:

1.  **Adopt a Collapsible Left Sidebar.** Reject the horizontal dropdown pattern entirely. This is the single most important change to ensure the UI is scalable, discoverable, and aligned with modern admin dashboard best practices. It solves the overflow problem permanently.

2.  **Re-center the IA on Tasks, Not Agents.** Abandon the `Fleet/Workflows/Ops` grouping. Reorganize the sidebar into task-oriented groups like `Content` (Drafts, SEO, Repurpose), `Agents` (the 8 agent consoles), and `System` (Cost, Logs, Memory, Docs). This aligns the IA with the user's mental model.

3.  **Preserve the Dashboard's Monitoring Function.** Do not delete the drawer. Modify the dashboard so that clicking the main body of an agent card navigates directly to its console, but retain an affordance (e.g., a stats icon on the card, or a hover interaction) to open the drawer and view detailed stats. This provides the best of both worlds: quick navigation and glanceable monitoring.

4.  **Visually Differentiate Unbuilt Agents.** Do not create clickable cards for the four "Under Construction" agents. Instead, render them in a disabled or visually distinct state within the grid. Make them non-interactive, using a tooltip to communicate their planned status. This maintains honesty in the UI.

5.  **Combine the Migration.** Merge the proposed PR-2 (chassis rewrite) and PR-3 (drawer removal) into a single, atomic PR. The new navigation system and the new dashboard interaction model are two sides of the same coin and should be introduced together to prevent user confusion in a partial, intermediate state.