This is a thorough audit of ADR-041 slice 41d.

### 1. Code grounding — does the diff do what the description claims?

Yes, the implementation closely matches the design description.

*   **Reschedule (D8):** The route `weekly_task_reschedule` in `bridge_weekly.py` calls `calendar_scheduler.reschedule_block`. This orchestrator first calls `weekly_writer.reschedule_task_block` to perform the authoritative vault write. This writer function correctly relocates the `plan[]` entry (preserving `done` work), updates `scheduled`/`scheduled_end`, and keeps the `calendar_event_id`. The scheduler then pre-checks for conflicts using `google_calendar.find_conflicts` with the self-exclusion filter `if c.id != event_id`. Finally, it calls `google_calendar.update_event` to PATCH the existing event. This all aligns with the spec.
*   **Two cancel actions (D9):**
    *   The "移出行事曆" route (`/unlink`) calls `calendar_scheduler.unlink_calendar`, which uses `weekly_writer.unlink_calendar_block`. This writer function correctly pops `scheduled`, `scheduled_end`, and `calendar_event_id` while leaving `plan[]` untouched.
    *   The "取消排程" route (`/unschedule`) calls `calendar_scheduler.cancel_schedule`, which uses `weekly_writer.unschedule_task_block`. This writer function correctly removes the relevant `plan[]` entry (again, sparing entries with `done` work) in addition to clearing the projection fields.
    *   Both paths then call `_delete_best_effort` to remove the Google Calendar event.
*   **Concurrency:** All three new routes in `bridge_weekly.py` (`reschedule`, `unlink`, `unschedule`) accept and pass an `expected_token` down to the `weekly_writer` functions, which use `_check_token` for an `If-Match` style optimistic lock.
*   **Best-effort Google calls:** All Google API interactions in `calendar_scheduler.py` (`find_conflicts`, `update_event`, `delete_event`) are wrapped in broad `except Exception` blocks that log the error and return a status (`UNAVAILABLE`, `CANCEL_CAL_FAILED`) rather than raising an exception. This correctly implements the specified contract.

### 2. Correctness/bug hunt — concurrency, filters, logic, degradation

This section contains one high-priority bug and several smaller observations.

*   **BUG: `reschedule_block` fallback performs a redundant double-write.**
    The fallback logic for an unlinked task in `calendar_scheduler.reschedule_block` is flawed:
    ```python
    # in calendar_scheduler.reschedule_block
    scheduled, scheduled_end, token, event_id = reschedule_task_block(...)
    if not event_id:
        # ... Degrade to a fresh create+link ...
        return schedule_block(
            vault_root,
            task_slug,
            # ...
            expected_token=token, # <-- token from the FIRST write
            # ...
        )
    ```
    The problem is that `reschedule_task_block` *has already successfully written to the vault* to relocate the `plan[]` entry and update `scheduled`. The call to `schedule_block` then immediately triggers *another* vault write via `weekly_writer.schedule_task_block`. While the upsert logic in `plan[]` handling prevents data corruption (the second write just overwrites the first), this is incorrect, inefficient, and confusing. It performs two file I/O operations and token generations where one is sufficient. The fallback should only perform the *Google Calendar creation and linking* part of `schedule_block`, not the vault write part.

*   **Minor Issue: Stale idempotency key claim needs scrutiny.**
    The comment in `reschedule_block` notes that the idempotency key (based on the original creation date) becomes stale after a reschedule. It claims this is "benign in v1 because a linked task never re-enters the create path". This holds only as long as the UI flow is the sole guardrail. If an API-based workflow were introduced, or if a bug allowed a linked task to be scheduled again, a new event would be created instead of de-duping against the existing one, because the key would not match. This isn't a bug now, but it's technical debt and a system fragility that relies on client-side behavior for correctness.

*   **Clean — checked self-excluding conflict filter:** The logic `[c for c in ... if c.id != event_id]` in `reschedule_block` is simple and correct. It correctly prevents a block from conflicting with its own previous timeslot during a reschedule.

*   **Clean — checked done-work preservation:** The list comprehensions in `reschedule_task_block` and `unschedule_task_block` are correct and robust:
    ```python
    # in weekly_writer.py
    entries = [e for e in entries if not (_entry_date(e) == old_day and not e.get("done"))]
    ```
    This logic correctly preserves historical `plan[]` entries that have `done` pomodoros, satisfying the requirement not to delete work history.

### 3. Best-effort/degradation — are Google failures handled gracefully?

Yes, this is implemented very well.

*   In `reschedule_block`, both the `find_conflicts` call and the `update_event` call are in `try...except` blocks. An exception correctly results in an `UNAVAILABLE` status, leaving the vault changes intact but informing the user that the calendar is out of sync.
*   Similarly, `_delete_best_effort` wraps `delete_event` and returns `CANCEL_CAL_FAILED` on failure.
*   The router layer in `bridge_weekly.py` correctly interprets these statuses (`cal_conflict`, `cal_unavailable`, `cancel_cal_failed`) and presents clear, actionable error messages to the user. The system gracefully degrades, prioritizing the source-of-truth vault write over the downstream projection.

### 4. The deferred uniform-token decision — is it defensible?

No, this is a significant and unnecessary risk. The justification that "the vault is a pure substrate mutated only via Web UI / Nami (no hand-edited frontmatter)" is insufficient and ignores common user behavior.

A concrete race condition exists on the older dashboard-level planning routes (`add_plan_entry`, `remove_plan_entry`) which lack token validation:

1.  **Setup:** A user has the weekly dashboard open in two browser tabs (Tab A and Tab B).
2.  **Load:** Both tabs load the same state for a task, which has a `plan[]` entry for Tuesday with 2 pomodoros.
3.  **Action 1 (Tab A):** The user uses the UI in Tab A to change Tuesday's plan to 4 pomodoros. The `add_plan_entry` route is called. It reads the file, upserts the new value for Tuesday, and writes the file back. The vault now correctly reflects a 4-pomodoro plan for Tuesday.
4.  **Action 2 (Tab B):** The user, working from the stale state in Tab B, decides to change Tuesday's plan to 1 pomodoro. The `add_plan_entry` route is called again. It reads the file (which contains the 4-pomodoro plan from Tab A's write), upserts its own value of 1 pomodoro for Tuesday, and writes the file back.
5.  **Result:** The work from Tab A is silently lost. The final state is 1 pomodoro, which is a data corruption scenario caused by a last-write-wins race.

The `If-Match` protection added in this slice is effective and should be applied uniformly to *all* mutating vault operations originating from the web UI to prevent this exact class of bug. Deferring this creates a known integrity gap.

### 5. Alternatives — anything materially simpler or safer?

*   **Reschedule:** The current approach of `PATCH`ing the existing event via `update_event` is superior to a `DELETE` then `CREATE` alternative. The latter would generate a new `event_id`, breaking idempotency and risking orphan events if the final link-back write failed. The current method is the right one.
*   **Cancel:** The two distinct cancel actions (`unlink` vs. `unschedule`) provide clear user choice and are well-implemented. Combining them would reduce user control. No changes recommended here.
*   **Fixing the `reschedule_block` fallback:** Instead of calling the entire `schedule_block` function, the Google Calendar interaction logic could be factored out into a private helper function, `_create_and_link_calendar_event(...)`. `schedule_block` would become a simple composition of `schedule_task_block` and this new helper. The fallback in `reschedule_block` could then safely call `_create_and_link_calendar_event(...)` without performing a redundant vault write.

### 6. Verdict — SHIP / SHIP-WITH-FIXES / BLOCK

**SHIP-WITH-FIXES.**

The slice is well-architected, particularly in its handling of the vault-vs-calendar state and error conditions. However, the `reschedule_block` bug and the known race condition on older routes must be addressed.

**Must-Fix List:**

1.  **(Correctness Bug)** Refactor `calendar_scheduler.reschedule_block`'s fallback for unlinked tasks to prevent the redundant double-write to the vault file. It should only execute the Google Calendar creation and linking logic.
2.  **(Data Integrity)** Implement `If-Match` token checking on the older dashboard planning routes (`add_plan_entry`, `remove_plan_entry`, `sync_scheduled_to_next_plan`). The risk of the last-write-wins race condition is concrete and the fix is already implemented elsewhere in the codebase. Deferring this is a poor trade-off.