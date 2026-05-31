# ADR-039 — Gemini Panel Audit (verbatim)

_Multi-agent panel step 3 · 2026-05-31 · model: gemini-2.5-pro (self-reported header may differ) · read ADR + Codex audit_

# ADR-039 LifeOS Weekly Dashboard — Gemini Panel Audit

**Auditor:** Gemini 1.5 Pro (via Google AI Studio)
**Date:** 2026-06-01
**Perspective:** Independent third-party audit, focusing on conceptual integrity, human-computer interaction, and multilingual/locale-specific nuances. This audit reviews both the original ADR from Claude and the technical audit from Codex (GPT-5).

---

### Section 1 — VAULT-SUBSTRATE & PLUGIN-CONTRACT LENS

I concur with Codex's assessment (Codex §1, §5) that the proposed `plan[]` field on `TaskNotes/Tasks/*.md` files constitutes a fragile, implicit contract with a third-party plugin. My perspective deepens this concern from a systemic to a conceptual level.

**The `plan[]` contract is a parasitic integration, not a stable API.** The ADR correctly identifies that TaskNotes tolerates unknown frontmatter. However, this tolerance is an implementation detail, not a feature. The integration is one-way: the Nakama system will depend on TaskNotes' schema and behavior, but TaskNotes is entirely unaware of Nakama. A future TaskNotes update could:
1.  Introduce its own multi-day planning feature using a different schema, creating direct conflict.
2.  Change its frontmatter parsing/validation logic, breaking the Bridge's writes.
3.  Alter the behavior of the `scheduled` field, invalidating the ADR's "auto-maintained" sync logic (D4) and causing the "surprising TaskNotes calendar behavior" Codex alludes to (Codex §4).

The risk is not just that a schema bump will break the code, but that it will silently corrupt the user's planning system. The proposed mitigation—a "future task-frontmatter schema doc"—is insufficient. A document does not enforce a contract on third-party code. This approach accepts a foundational risk for the core planning primitive.

**The ADR creates a second, conflicting "week" identity, undermining vault coherence.** The ADR's claim in D2 that its date-range aggregation "fully decouples" the new weekly view from the vault's existing ISO week infrastructure is factually incorrect. Decoupling the *read model* does not decouple the *data model*.

The vault will now contain two parallel, contradictory definitions of a "week":
1.  **ISO Week:** `Journals/Daily/{YYYY-MM-DD}.md` files contain `week: "[[2026-W23]]"` in their frontmatter. This is a structural, Monday-start link used for backlinks and potentially other dataview queries.
2.  **修修 Week:** The Bridge dashboard will operate on a Sunday-start week, identified by a file like `Journals/Weekly/2026-05-31.md`.

On Sunday, May 31, 2026, the daily note will claim it belongs to ISO week 22, while the dashboard will claim it is the first day of week 23. This is not a "broken backlink" (ADR D2); it is a fundamental schism in the vault's temporal organization. It guarantees that any future vault-wide, cross-note analysis will have to constantly disambiguate which "week" definition to use, increasing complexity and the likelihood of error. A truly decoupled system would either eradicate the old system or find a way to make them coexist harmoniously (e.g., by adding a `shosho_week: "[[2026-W23-Sun]]"` link to daily notes). The current proposal ignores this conceptual debt.

### Section 2 — DIFFERENT PRIOR (where your training prior diverges from Claude/GPT-5)

My training data and reasoning model lead to different foundational priors than those implicitly held by Claude and Codex.

**Prior 1: A "Journal" is a chronological, immutable-by-machine record of human experience, not a cache for derived data.**
Codex correctly flags the risk of persisting `actual_pomodoros` as a cache (Codex §2). I elevate this from a technical concern to a philosophical one. The `Journals/` folder, by its very name and function in GTD/Bullet Journal systems, is for logging events and reflections as they happen. It is a primary source.

The ADR proposes writing machine-computed aggregates (`planned_pomodoros`, `actual_pomodoros`) into the frontmatter of `Journals/Weekly/{date}.md`. This fundamentally changes the nature of the document from a journal *entry* to a database *view*. It pollutes a record of human intention and review with transient, derived state. This violates the principle of separating primary data from its presentation. The weekly file should contain the human's plan (`top3`, etc.) and reflection (the six questions), but the *results* of that plan should be computed on-the-fly from their canonical sources (the session logs). Persisting them in the journal file is redundant and conceptually impure.

**Prior 2: Taiwan's default context is ISO 8601, making the "US/epi" system a deliberate, personal override that requires explicit handling.**
While accommodating 修修's personal habit is the correct product decision, it's crucial to ground this in the local context. Taiwan's national standard for date and time representation is CNS 7648, which directly adopts ISO 8601. This means the "default" or "standard" week in this locale is Monday-start.

Therefore, the system isn't choosing between two equally valid standards; it's implementing a *user-specific deviation* from the local and international standard already used elsewhere in the vault. This strengthens the argument from Section 1: the conflict between the two "week" identities is not a minor inconvenience but a direct contradiction of the default organizational principle. The ADR should acknowledge this and treat the Sunday-start week as a distinct, parallel calendar system that must be carefully insulated or explicitly bridged to the ISO system, not just papered over with a range query.

**Prior 3: The cognitive load of the "split-brain" pomodoro user experience.**
Both the ADR and Codex's audit treat the two pomodoro stores as a technical problem to be solved by a `union` read. They miss the human-facing inconsistency. 修修 will have two different timer UIs (Obsidian plugin vs. Bridge web dock) that save their data to two different, invisible locations (`Journals/Daily/` vs. `TaskNotes/Tasks/`). There is no in-app explanation for this. When a session is logged, where does it go? Why? What are the tradeoffs?

This creates a confusing user experience. A user shouldn't need to remember which timer populates which backend store. The system should either:
a) Unify the write path, so both UIs write to the same location (rejected by D3 for good reasons, i.e., not writing to `Journals/Daily/`).
b) Abstract the storage away and present a single, unified log to the user in the UI, making the backend separation an invisible implementation detail.
c) **(Recommended)** Make the separation explicit and meaningful. For example, frame the Obsidian timer as for "deep work sessions tied to a day" and the Bridge timer as for "quick, ad-hoc task time logging."

The current proposal does none of these, leaving the user to navigate an arbitrary technical divide.

### Section 3 — CLAUDE/CODEX BLIND SPOTS

1.  **The Lifecycle of a Paused/Abandoned Pomodoro:** Both documents focus on *completed* pomodoros. What happens to a session that is started but not completed? The Obsidian `pomodoros[]` schema has a `completed: true` flag. The Bridge `timeEntries[]` schema does not; it only has `startTime` and `endTime`. The current Bridge implementation (Codex §1) writes a synthetic 25-minute entry upon completion. What if the user starts the timer, works for 15 minutes, and closes the browser tab? That session is lost. The system lacks a robust concept of an in-progress or abandoned session, which will lead to undercounting of actual effort. The "no double-count" claim (D3) is a red herring; the more immediate risk is "no count at all" for interrupted work.

2.  **The "隨手筆記" (Casual Notes) Use Case:** The ADR (D9) scopes this to a section in the weekly file. This misinterprets the typical "fleeting note" or "scratchpad" workflow. A casual note is captured *in the moment*, often without context. Forcing the user to navigate to `/bridge/weekly` and find the correct section adds significant friction. This feature is likely to be ignored in favor of more accessible capture tools (like Obsidian's daily note or a dedicated inbox file). The proposed implementation solves the storage problem but fails the usability test for its intended purpose.

3.  **Timezone Handling at Week Boundaries:** Codex correctly identifies the need to use `endTime` in Asia/Taipei for weekly inclusion (Codex §3). However, neither audit addresses the full implication. A pomodoro session started at 23:50 on a Saturday in Taipei (UTC+8) and ending at 00:15 on Sunday belongs to which week's "actuals"? The ADR is silent. The logic must be explicit: a session is counted in the week (and day) where its `endTime` falls. This needs to be a firm requirement with a corresponding test case, especially for the Saturday-to-Sunday transition that marks the week boundary.

### Section 4 — RED-LINE CARVE-OUT & SCHEDULING SCHEMA DEEPENING

**D5 Provenance Principle:** The principle that "Bridge persisting human-typed input ≠ agent write" is a seductive but ultimately corrosive argument. It creates a slippery slope. Today, it's a weekly review form. Tomorrow, why not a daily review form that writes to `Journals/Daily/`? Or an OKR planning form that writes to `OKRs/`? Each step would be justified by the same principle. Codex's suggestion to narrow the carve-out is correct but insufficient.

A more durable distinction is based on **structure vs. prose**.
*   **Prose (🔒 Human-only):** Long-form, narrative content. The user's own thoughts, reflections, and notes. The "voice" of the vault. Agents should never write this.
*   **Structured Data (🟡 Collab):** Frontmatter, lists, tables, task items. Data that has a defined schema and is often manipulated by tools.

My proposed modification to the principle: **The Bridge may act as a human-input surface to mutate structured data within 🟡 Collab folders. It may only append to, not author, human prose sections within those files.**

This reframing makes the carve-out non-precedent-setting for prose-heavy folders like `Journals/Daily/`. It allows the Bridge to manage the `top3` list in `Journals/Weekly/` frontmatter but forbids it from, for example, using an LLM to summarize the week's accomplishments and writing it into the review body. This is a more robust and auditable line than "provenance."

**D4 `plan[]` vs. `scheduled` Lifecycle:**
Let's model the reschedule scenario Codex mentioned.
*   **Initial State:** Task A has `plan: [{date: "2026-06-02", pomodoros: 2}, {date: "2026-06-09", pomodoros: 3}]`. The Bridge auto-syncs `scheduled: "2026-06-02"`. The TaskNotes calendar shows Task A on June 2nd.
*   **Execution:** 修修 completes the 2 pomodoros on June 2nd. The Bridge needs to mark that `plan` entry as complete. Let's assume a new field: `plan: [{date: "2026-06-02", pomodoros: 2, completed: 2}, ...]`.
*   **Auto-sync:** The Bridge now sees the first *incomplete* plan date is June 9th. It rewrites the task file: `scheduled: "2026-06-09"`.
*   **Surprising Behavior:** In Obsidian, Task A *vanishes* from the June 2nd daily note/calendar view and *magically appears* on June 9th. From the perspective of the TaskNotes UI, the task's date just changed automatically. This is a "hidden tug-of-war" (Codex §5, Alt 1) and creates a confusing user experience where the Bridge's actions have non-obvious side effects in another application.

A cleaner SoT would be to follow Codex's Alternative 1: **`plan[]` is the SoT, and `scheduled` is a user-controlled field.** The Bridge can *suggest* syncing `scheduled` to the next `plan[]` date, but it should not do so automatically. This respects the agency of the user within the Obsidian environment and makes the data flow explicit rather than magical and potentially surprising.

### Section 5 — ARCHITECTURAL CONCERNS

1.  **Optimizing for the Web at the Expense of the Vault:** This ADR continues a pattern started in ADR-031 of privileging the Bridge UI over the native Obsidian experience. By introducing a custom `plan[]` schema invisible to TaskNotes and creating a `scheduled` field that behaves non-intuitively within Obsidian, the proposal degrades the vault's utility as a standalone system. The vault becomes less of a "substrate" and more of a backing store for a web app. This is a significant architectural trade-off that smells of tech debt and lock-in to the Bridge as the primary interaction surface.

2.  **Increased Implementation Complexity vs. User Value:** The machinery required to implement this ADR is substantial: a new indexer, a new aggregator, a new writer, complex union-read logic, delicate `scheduled` field synchronization, and a new vault permission carve-out. This complexity is in service of a single dashboard view. An alternative approach, such as using a simpler tagging system (`#plan/2026-06-02`) and enhancing dataview queries (even if it means running them client-side in Obsidian and having a read-only Bridge view), was not adequately considered. The chosen path is high-cost and high-risk.

3.  **Governance Drift:** Codex's point that `docs/VAULT-LAYOUT.md` was updated before the ADR was approved (Codex §1, §4) is a serious process concern. It suggests a breakdown in governance where implementation or documentation precedes decision-making. This must be corrected immediately to maintain the integrity of the ADR process.

### Section 6 — FINAL VERDICT

**Approve with significant modifications.** The core user need is valid, but the proposed implementation introduces conceptual conflicts, user experience friction, and foundational risks that must be addressed. The "Slice" plan is sound, but Slice 1 and 2 should not proceed without these changes.

**Prioritized Changes:**

1.  **D5: Reframe the "Provenance Principle" to be about Structure vs. Prose.** (Addresses §4 of this audit). Replace the principle with: "The Bridge may act as a human-input surface to mutate *structured data* (frontmatter, lists) within 🟡 Collab folders, but not author or modify long-form *prose*." This creates a more durable and less abusable precedent than the current proposal.

2.  **D4: Decouple `plan[]` and `scheduled`.** (Addresses §4 of this audit, aligns with Codex §5 Alt 1). Remove the automatic synchronization of the `scheduled` field. `plan[]` is the SoT for the Bridge. `scheduled` is a separate field for the benefit of TaskNotes. The Bridge UI can offer a "sync to next planned date" button, but it must be an explicit user action, not a magical background process.

3.  **D2: Address the Conflicting "Week" Identity.** (Addresses §1 of this audit). The ADR must acknowledge the conceptual schism it creates. The recommended fix is for the `weekly_writer` to also add a `shosho_week: "[[2026-05-31]]"` link to the frontmatter of the seven relevant daily notes. This creates an explicit, queryable link between the two systems, healing the conceptual fork and making the relationship between the daily notes and the weekly journal file robust.

4.  **D3: Clarify Pomodoro Semantics and UX.** (Addresses §2, §3 of this audit, builds on Codex §3).
    *   The pomodoro aggregator must define rules for handling sessions that cross the midnight/week-end boundary (based on `endTime`).
    *   The system must have a defined state for in-progress/abandoned sessions from the Bridge timer to prevent data loss.
    *   The UI should provide a subtle explanation or framing for why there are two different timers, reducing user confusion.

5.  **D1/D5: Do Not Persist Derived Aggregates in Journal Frontmatter.** (Addresses §2 of this audit, strengthens Codex §2/§5). Remove `planned_pomodoros` and `actual_pomodoros` from the weekly file's frontmatter. These are computed values. A journal is a primary source, not a cache. Compute them on read, every time. The performance cost is negligible for a single-user system and the conceptual cleanliness is paramount.
