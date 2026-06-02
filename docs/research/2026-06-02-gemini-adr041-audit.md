Third reviewer, Gemini lens. Audit complete.

My verdict aligns with Codex's: **rework**. The ADR's core instinct to reuse the existing calendar convention is sound, but it fundamentally misinterprets the system's current state and introduces a source-of-truth conflict that will lead to data corruption. My reasoning differs from Codex's by focusing on the internationalization (i18n) and timezone implications of the proposed model, which I believe are critical failure points missed by both the ADR and the prior audit.

---

### 1. The Timezone and Internationalization (i18n) Lens

The ADR's proposal to reuse tz-stripped datetimes is a latent bug waiting to corrupt user schedules across timezones and during Daylight Saving Time (DST) transitions.

*   **The Core Problem:** The system currently operates on a fragile, implicit assumption: that all operations happen in `Asia/Taipei`. The `_strip_tz` function (`gateway/handlers/nami.py:2423-2439`) launders timezone information, converting an aware datetime into a naive `YYYY-MM-DDTHH:MM:SS` string representing the local time in Taipei. This string is then stored in the task file. When creating a Google Calendar event, the `google_calendar` helper (`shared/google_calendar.py:213-223`) re-interprets this naive string by explicitly attaching a fixed `+08:00` offset.

*   **Where This Breaks (ADR-041 D1, D6):**
    1.  **Daylight Saving Time (DST):** `Asia/Taipei` does not observe DST, but many other timezones do. If a user in `America/New_York` (which observes DST) uses the Bridge UI, their browser's native `<input type=time>` will submit local time. If the backend naively strips the timezone and stores it, and then another part of the system re-attaches a fixed `+08:00`, the event will be created at the wrong UTC time. The user's 9 AM appointment becomes an entirely different time on the calendar.
    2.  **The Remote User:** A team member working from `Europe/London` schedules a task for 9:00 their time. The browser sends this local time. The backend strips the timezone, stores `...T09:00:00`, and then creates a Google Calendar event at `...T09:00:00+08:00`. This is 9:00 in Taipei, which is 1:00 or 2:00 AM in London. The event is now completely wrong for the person who scheduled it. The system *must* know the user's IANA timezone at the time of scheduling.
    3.  **Google Calendar's `timeZone` field:** The Google Calendar API's `events` resource includes a `timeZone` field for the start and end times. The current `create_event` helper (`shared/google_calendar.py:102`) does not use this; it only sends a `dateTime` with a fixed offset. This is a misuse of the API. The correct approach is to send the user's local time *and* their IANA timezone identifier (e.g., `dateTime: '2026-06-10T09:00:00'`, `timeZone: 'America/New_York'`). Google Calendar then handles all DST and cross-timezone rendering correctly. The current tz-stripping actively prevents this robust behavior.
    4.  **CJK Event Titles:** The ADR assumes event titles are simple. What if a task title is `【修修】審核ADR-041 & 準備週會`? The existing `google_calendar.create_event` (`shared/google_calendar.py:102`) passes this title directly. While Google Calendar generally handles UTF-8 well, this dependency must be explicitly tested. A failure in the calendar API due to an unexpected character set could trigger the rollback logic (ADR-041 D4), failing the entire operation.

This timezone-stripping pattern is not a feature to be reused; it is a critical bug to be fixed. The ADR must specify that the user's IANA timezone is captured and passed to the Google Calendar API.

### 2. A Different Prior: `plan[]` is the Source of Truth for *Intent*

Codex correctly identifies that `plan[]` and `scheduled` create a dual source of truth. My perspective differs: the ADR is modeling the relationship backwards.

*   **The ADR's Model (D3):** Calendar is truth for time; `plan[]` is a side-effect for pomodoro counting.
*   **My Proposed Model:** The `plan[]` entry is the user's *intent* to allocate effort. The calendar event is a *downstream representation* of that intent, a convenience for blocking time.

Why is this a better model?
1.  **Offline/Degraded Mode:** It directly answers Open Question #1. If the Google Calendar API is down, the core action—allocating N pomodoros to a task on a specific day—succeeds. The system can record the intent in `plan[]` and flag the task as "pending calendar sync." The user's planning is not blocked by a third-party outage.
2.  **Conceptual Integrity:** Users think "I will spend 2 pomodoros on this task Tuesday." They don't think "I will create a 60-minute calendar event which implies a 2-pomodoro allocation." The `plan[]` array is the canonical log of planned work. The calendar is just one possible view.
3.  **Simplifies Reconciliation:** If `plan[]` is truth, then any change to the calendar event (e.g., dragging it in Google Calendar) is an *external change notification*. Nami's `_sync_task_from_calendar_update` should not just update `scheduled`; it should propose an update to the authoritative `plan[]` entry. This resolves the drift Codex noted where `plan[]` and `scheduled` can diverge.

### 3. Claude/Codex Blind Spots

*   **Codex Understated the `plan[]` Drift:** Codex correctly notes that `plan[]` and `scheduled` can drift (`shared/weekly_indexer.py:236-245`). This is not a minor inconsistency; it's a critical flaw. The dashboard will show pomodoros allocated on Wednesday while the user's calendar shows the work block on Thursday. This breaks the primary value proposition of a unified view.
*   **Codex Overstated the D5 Fix:** Codex claims the `_as_date` compatibility fix (ADR-041 D5) is "already implemented" (`shared/weekly_indexer.py:146-154`). While `_as_date` can *parse* a datetime string, the ADR's concern is about the *semantic* overload. The system now has two types of `scheduled` values with different meanings: a date for "due-by" tasks and a datetime for "appointed-at" tasks. The indexer and all downstream consumers must be aware of this distinction, which they currently are not. It's more than a parsing fix; it's a schema-level ambiguity.
*   **What Both Missed:**
    1.  **The Timezone/i18n Bomb:** As detailed in section 1, neither the ADR nor Codex's audit addressed the fundamental flaws in the timezone handling logic, which will cause scheduling errors for any user outside `Asia/Taipei` or during DST transitions.
    2.  **The Pomodoro Mismatch is a Feature, Not a Bug:** Both audits flag the "1 🍅 = 30 min" (ADR-041 D2) vs. `POMODORO_MINUTES = 25` (`shared/pomodoro_aggregator.py:41`) conflict. This isn't necessarily a contradiction. It's common practice to time-block 30 minutes for a 25-minute focus session plus a 5-minute break (the Pomodoro Technique). The ADR fails to *explain this rationale*, making it look like an error. The decision should be to formalize this: `CALENDAR_BLOCK_MINUTES_PER_POMODORO = 30`, and state that this includes buffer/break time.
    3.  **Mobile UX of Native Pickers (D6):** The ADR praises native `<input type=date/time>` as "mobile-friendly." This is naively optimistic. Mobile browser implementations of these pickers vary wildly in quality and usability. Committing to them without prototyping assumes a level of consistency that does not exist.

### 4. My Call on the 3 Open Questions

1.  **Calendar-write failure policy:** **Fail the calendar write, but succeed the plan write.** Following my model from section 2, the user's intent is captured in `plan[]`. The UI should show a non-fatal "Could not create calendar event. We'll retry." banner. The action should not fail entirely, as that blocks the user's planning workflow due to a transient external dependency.
2.  **D3 ownership:** **The task file's `plan[]` array is the source of truth for effort allocation.** The calendar is a mutable, downstream view. Bridge edits `plan[]` first, then attempts to propagate that change to the calendar. External calendar edits trigger a reconciliation flow that proposes changes back to the `plan[]`. This is a more robust and offline-tolerant model.
3.  **Cancel semantics:** **Canceling the calendar event should *not* automatically remove the `plan[]` entry.** These are distinct user actions. "Cancel my 2 PM meeting" is different from "I am no longer planning to work on this task today." The UI should offer two distinct actions: "Remove from calendar" (deletes the event) and "Unplan" (removes the `plan[]` entry and the event).

### 5. Missing Decisions Before Building

The ADR is missing several critical decisions required for a robust implementation:

*   **Idempotency on Double-Submit:** As Codex noted, this is missing. A unique request token or checking for an existing event with a specific `extendedProperty` (e.g., `lifeos_task_id`) is required to prevent duplicate calendar events on a network hiccup/retry.
*   **All-Day vs. Timed Events:** The system must decide how to handle all-day events created in Google Calendar. If Nami syncs an all-day event, what `scheduled` and `scheduled_end` values should it write? This is undefined.
*   **Free/Busy Across Multiple Plan Blocks:** The ADR assumes a task is scheduled once. What if a user schedules 2 pomodoros on Tuesday and 3 on Wednesday for the same task? The task has one `calendar_event_id`. This is a fundamental model limitation. V1 must explicitly state: **"A task can only be linked to a single calendar event at a time."**
*   **`plan[].done` vs. Calendar Event Status:** What happens if a user marks `plan[].done = 1` but the calendar event is still in the future? Or if they respond "No" to the event invitation in Google Calendar? The relationship between task progress and calendar attendance status is undefined.
*   **Multi-Day Scheduling:** What happens if a user schedules a 10-pomodoro task (5 hours) starting at 10 PM? The `scheduled_end` will cross midnight. How is this represented in `plan[]`? Does it create one entry for the start day or two entries for both days? This must be specified.

### 6. Verdict: Rework

I agree with Codex's verdict: **Rework**. The proposal's goal is correct, but its understanding of the existing system and its proposed architecture are flawed. It introduces a critical source-of-truth conflict and doubles down on a fragile, incorrect timezone implementation.

**Prioritized Changes Before Approval:**

1.  **Fix the Timezone Model:** Rip out the tz-stripping logic. Capture the user's IANA timezone on the frontend. Store UTC-aware datetimes or naive local times *with* the IANA timezone in the task file. Use the Google Calendar API's `timeZone` field correctly. This is the highest priority as it prevents data corruption.
2.  **Redefine the Source of Truth (D3):** Explicitly state that `plan[]` is the source of truth for planned effort, and the calendar event is a downstream representation. All write operations must be consistent with this hierarchy.
3.  **Resolve the Multi-Block Ambiguity:** For v1, explicitly state that a task can only have one timed calendar block associated with it. Defer multi-block scheduling. This simplifies the `calendar_event_id` linkage.
4.  **Specify Idempotency and Rollback Logic:** Define the mechanism (e.g., `extendedProperty` on the event) to prevent duplicate event creation and to reliably find the event for rollback if the task-write fails.
5.  **Clarify the Pomodoro-to-Minutes Rationale (D2):** Document that the 30-minute calendar block per 25-minute pomodoro is intentional and includes a break/buffer. This turns a perceived bug into a documented feature.
