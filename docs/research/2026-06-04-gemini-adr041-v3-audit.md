# Gemini audit — ADR-041 v3 (multi-block)

Excellent. As Gemini, my role is to provide a distinct, skeptical engineering perspective, focusing on systemic risks, user-experience traps, and long-term maintainability that a pure code or product-focused review might miss.

Here is my audit of the `## v3 — multi-block projection` section of ADR-041.

---

### 1. Code Grounding

Based on the provided grounding facts, v3's claims about the existing v2 system are accurate.

-   **Idempotency Key:** The claim that the key `{slug}@{date}` is "already date-scoped" and supports multi-day projection is correct. The grounding facts confirm `idem = f"{task_slug}@{start.date().isoformat()}"`, which is unique per task per day.
-   **Plan Entry Uniqueness:** The claim that `plan[]` upserts ensure at most one block per task per day is correct. The facts state that `weekly_writer` will "replace the entry whose date matches, else append," guaranteeing `(slug, date)` uniqueness within a task's `plan[]`.
-   **Single-Block Legacy:** The motivation correctly identifies that the v2 system is single-block due to the task-level `scheduled`/`calendar_event_id` fields, as confirmed by the grounding facts.

**Conclusion:** Checked, no issue. The v3 proposal is built on a correct understanding of the current implementation.

### 2. Drift / Contradiction with D1–D10 & 41d Notes

The proposal introduces several significant shifts in behavior, some of which create tension with the spirit or letter of previous decisions.

1.  **Drift on D9 "Cancel" (Major Risk):** The v2 decisions (D9) and 41d notes created a careful distinction between unlinking from the calendar and removing a plan entry. v3 V3 proposes: "the per-row ✕ chip already removes a plan entry → it must also delete that entry's event." This contradicts the established safe-by-default UX. The `✕` chip was previously a low-stakes "remove from this week's plan" action. v3 elevates it to a destructive, cross-system "delete my Google Calendar event" action, likely without a confirmation step. This violates the principle of least surprise and merges two distinct user intents (planning vs. appointment management) into one ambiguous control.
2.  **Drift on D8 "Reschedule":** The 41d notes describe a `reschedule` that always performs an `update_event` on the *same* Google event to preserve its identity. v3 V3 proposes a split: `update_event` for same-day changes but `delete+recreate` for date changes. While technically "cleaner" for the idempotency key, this is a material change to the operation's side effects (see section 4a). The ADR should acknowledge this is a behavioral change, not just a technical fix.
3.  **Contradiction with 41d Orphan Guard:** The shipped `41d` orphan guard rejects scheduling if a task-level `calendar_event_id` exists. This guard is now obsolete. The new logic must be per-`plan[]`-entry: reject if the entry for the target `(slug, date)` *already has* an `event_id`. The ADR implies this change but doesn't explicitly state the need to update the guard, which is a critical detail to prevent orphaned events during the transition.
4.  **Implicit Overturn of D9 "never auto-drop plan[]":** The previous design principle was that the calendar is a downstream projection, and calendar actions should not automatically delete planning entries. v3's proposal for the `✕` chip (item #1 above) reverses this, making a UI action that *looks* like a plan removal also trigger a calendar delete. This is a subtle but important philosophical shift away from the "vault-first" principle of D1.

### 3. Numerical / Correctness

1.  **Block Calculation:** `block = pomodoros × 30 minutes`. Checked, no issue. The math is trivial and consistent.
2.  **Idempotency Key Uniqueness:** The `{slug}@{date}` key remains unique per task per day, which correctly supports the new model. Checked, no issue.
3.  **Derived `scheduled` Mirror (V4/V5):** The proposal for a derived `scheduled` field (mirroring the "earliest future timed entry") for Nami's benefit is a classic transitional strategy. However, it's brittle. What if there are no *future* entries but there is a *past* one? Does `scheduled` become `null`, breaking Nami's expectation that a linked task has a schedule? What happens if the earliest entry is deleted? The derivation logic must be specified precisely (e.g., "the `start` of the chronologically first `plan[]` entry with a non-null `start`, or `null` if none exist"). The potential for divergence between the mirror and the source of truth (`plan[]`) is high.
4.  **Lazy Migration Correctness (V4):** The migration logic seems sound: on first write, if legacy fields exist, fold them into a `plan[]` entry. The risk is that a task with a legacy event that is *never again touched by the Bridge* will permanently exist in the old format. The dual-read indexer must be maintained indefinitely, or a backfill script must be planned to fully retire the legacy fields. The ADR should acknowledge this long-term cost.

### 4. Assumption Push-back (Highest-Value Section)

This is where the design is riskiest and most under-specified.

**(a) Date-change Reschedule as Delete+Recreate:**
The ADR claims this is "cleaner," but for whom? For the database, maybe. For the user, it is data loss. A Google Calendar event is more than a time block; it has a unique ID, a Meet link, attendees, RSVPs, notifications, and potentially attachments. **A delete+recreate action destroys all of this metadata.** If a user invited someone to the event, their invitation is gone. The Meet link changes. Their "Yes" RSVP is deleted. This is a severe regression from the v2 `update_event` behavior. The ADR incorrectly equates `update_event` with "re-keying," which is not how it works; `update_event` modifies the existing resource in-place. The stale idempotency key from v2 was a benign server-side issue; the proposed "fix" introduces a destructive, user-facing one.

**(b) Lazy On-Write Migration & Dual-Read Indexer:**
This is a high-risk pattern. A "dual-read indexer" that combines legacy fields and new `plan[]` entries to present a unified view is complex and prone to bugs. For example, if a read happens mid-write (after the `plan[]` entry is written but before the legacy fields are cleared), could the indexer momentarily show two events for the same task? The transactionality is unclear. Furthermore, as noted in 3.4, this creates a permanent class of "unmigrated" tasks if they are never written to again, forcing the complex dual-read logic to be maintained forever. A one-off batch migration script is almost always safer and cleaner than indefinite lazy migration.

**(c) The Per-Chip ✕ Deleting a Google Event:**
This is the most dangerous UX decision in the proposal. It conflates a minor UI tidy-up ("remove from this view") with a permanent, external data deletion. There is no mention of a confirmation dialog (`"Are you sure you want to delete the Google Calendar event for 'ADR-041 Review' at 3pm?"`). Without one, accidental deletions are guaranteed. This silent, destructive behavior is a significant violation of user trust.

**(d) `find_conflicts` Self-Exclusion:**
The v2 41d notes were explicit: `find_conflicts` must exclude the *event being moved*. In a multi-block world, this becomes more complex. If I'm moving the Monday block of `task-A` to a new time on Monday, the exclusion logic is simple (exclude `event_id: "abc"`). But if I'm moving it to Wednesday, where `task-A` *already has another block*, will `find_conflicts` correctly identify the Wednesday block as a conflict? The logic must not exclude all events for `{slug}`, only the specific `event_id` of the block being actively manipulated. The ADR is silent on this crucial implementation detail.

**(e) `#task-slug` Re-open UX:**
The POST→303 redirect to `…#task-{slug}` is a standard pattern, but the claim that "JS re-opens + scrolls" glosses over complexity. What if the user had scrolled down 3 pages and had 5 other `<details>` sections open? Does the JS clobber this state, closing everything else to open just that one task? This can be jarring. A less disruptive approach, like Turbolinks/Hotwire or targeted AJAX, was dismissed too quickly for a UI that aims to be a fluid dashboard.

**(f) Atomicity of "one 排入 = vault + Google":**
The design correctly prioritizes the vault write (D1/D2). However, V2 describes the action as "atomically" writing to the vault and projecting to Google. This is not atomic. It is a two-phase commit where the second phase is "best-effort." The term "atomic" is misleading and should be replaced with "transactional, with vault-first authority." The failure mode (plan entry exists, but calendar event does not) is a known state that the UI must render clearly.

**(g) Timezone / Data Loss:**
The V1 proposal states the new `start`/`end` fields are "naive Asia/Taipei clock time." This is a ticking time bomb. If a user ever travels or daylight saving time rules change, storing naive local times is a classic source of off-by-one-hour errors. While full multi-tz is deferred, the schema should store UTC timestamps (`YYYY-MM-DDTHH:mm:ssZ`) or a datetime with an explicit offset (`+08:00`). The current v2 decision (D4) to *send* the `timeZone` field to Google is correct, but storing the source-of-truth time naively in the vault is a data-integrity risk.

### 5. Alternatives

1.  **Riskiest Decision:** Rescheduling a block to a new date via `delete+recreate`.
    *   **Alternative:** Use `update_event` for all reschedules and fix the idempotency key separately. After a successful `update_event` that changes the date, perform a second, best-effort `patch_event` call that *only* updates the `extendedProperties.private.lifeos_task` value to the new date-scoped key.
    *   **When it's better:** This is **always** better. It preserves the Google event's identity, attendees, Meet link, and history, which is what the user expects. The "stale key" is a backend implementation detail that should never justify user-facing data loss.

2.  **Riskiest Decision:** The per-chip `✕` silently deleting a Google Calendar event.
    *   **Alternative:** Keep the `✕` as a plan-only removal. Add an explicit, clearly-labeled control within the scheduled block's chip, like a trash can icon (🗑️) or a "Cancel" link. Clicking this would trigger a JS confirmation dialog (`confirm()`) before posting to a dedicated `cancel_schedule` endpoint.
    *   **When it's better:** This is **always** better for any non-expert user. It separates the two distinct intents (un-planning vs. canceling an appointment) and prevents accidental, irreversible data loss with a simple, standard confirmation step.

### 6. Verdict: REWORK

The proposal correctly identifies the core limitation of v2 and moves in the right architectural direction (per-entry projection). However, it contains significant, user-hostile regressions in its current form and glosses over critical implementation complexities. It is not ready to ship.

**Top 5 Concrete Changes Required Before Slicing:**

1.  **Redesign Reschedule:** The `delete+recreate` logic for date changes (v3 V3) **must be replaced**. Use `update_event` to preserve the event's identity and metadata, and patch the idempotency key in a separate, non-critical step as outlined in Alternative #1.
2.  **Redesign Cancel UX:** The per-chip `✕` **must not** delete the Google event. Revert it to its plan-only function. A separate, explicit, and `confirm()`-gated control must be introduced for deleting the plan entry *and* its associated event, as outlined in Alternative #2.
3.  **Specify `find_conflicts` Logic:** The ADR must explicitly state that when rescheduling, `find_conflicts` will exclude the single Google `event_id` of the block being moved, not all events associated with the task's slug.
4.  **Clarify Migration Strategy:** Commit to a concrete plan for the legacy fields. Either (a) scope a mandatory, one-off backfill script to run after v3-C ships to eliminate the dual-read complexity, or (b) fully specify the edge-case behavior of the derived `scheduled` mirror and accept the permanent maintenance cost of the dual-read indexer. "Lazy migration" is too vague.
5.  **Strengthen Vault Schema:** The new `start`/`end` fields in `plan[]` must not be stored as "naive" local times. They should be stored as full ISO 8601 timestamps with either a `Z` (UTC) or a timezone offset (`+08:00`) to prevent future timezone and DST-related bugs.
