**1. Code Grounding**

Mostly implements the described slice: writer APIs relocate/clear plan state ([weekly_writer.py](E:/nakama-adr041-41d/shared/weekly_writer.py:420), [weekly_writer.py](E:/nakama-adr041-41d/shared/weekly_writer.py:489), [weekly_writer.py](E:/nakama-adr041-41d/shared/weekly_writer.py:513)); scheduler patches the same event and catches calendar failures ([calendar_scheduler.py](E:/nakama-adr041-41d/shared/calendar_scheduler.py:164), [calendar_scheduler.py](E:/nakama-adr041-41d/shared/calendar_scheduler.py:202)); task page adds the panel/forms ([task.html](E:/nakama-adr041-41d/thousand_sunny/templates/bridge/task.html:75)).

Two claims are not fully true:

- “All three destructive routes carry If-Match”: the forms include `expected_token`, but the routes default it to `""` and pass `expected_token or None`, which disables `_check_token` entirely ([bridge_weekly.py](E:/nakama-adr041-41d/thousand_sunny/routers/bridge_weekly.py:769), [bridge_weekly.py](E:/nakama-adr041-41d/thousand_sunny/routers/bridge_weekly.py:801), [weekly_writer.py](E:/nakama-adr041-41d/shared/weekly_writer.py:713)).
- “Linked task never re-enters create path”: true in the rendered backlog UI, but not server-enforced. `/weekly/schedule` still accepts any slug and calls `schedule_block` without rejecting `task.calendar_event_id` ([weekly.html](E:/nakama-adr041-41d/thousand_sunny/templates/bridge/weekly.html:75), [bridge_weekly.py](E:/nakama-adr041-41d/thousand_sunny/routers/bridge_weekly.py:297), [bridge_weekly.py](E:/nakama-adr041-41d/thousand_sunny/routers/bridge_weekly.py:302)).

**2. Correctness / Bug Hunt**

Must-fix: stale dashboard/direct POST can orphan calendar events. If a task is already linked, `/weekly/schedule` can still create a new event. `schedule_task_block(... calendar_event_id=None)` preserves the old `calendar_event_id` on the first write ([weekly_writer.py](E:/nakama-adr041-41d/shared/weekly_writer.py:380)), then `schedule_block` creates a new event and writes the new ID back ([calendar_scheduler.py](E:/nakama-adr041-41d/shared/calendar_scheduler.py:85), [calendar_scheduler.py](E:/nakama-adr041-41d/shared/calendar_scheduler.py:103)). The old Google event is never deleted. The template comment explicitly says this path would orphan an existing event, but the guard is UI-only ([weekly.html](E:/nakama-adr041-41d/thousand_sunny/templates/bridge/weekly.html:75)).

Must-fix: omitted token disables locking on the new destructive routes. This is not just theoretical: the route tests post without `expected_token` and still mutate. Server should require a non-empty token or pass `""` through so `_check_token` conflicts against existing files.

Clean — checked self-excluding conflict filter: `find_conflicts` excludes `event_id` before returning `CONFLICT` ([calendar_scheduler.py](E:/nakama-adr041-41d/shared/calendar_scheduler.py:187)).

Clean with caveat — checked done-work preservation: reschedule/unschedule preserve entries with truthy `done` and drop absent/zero done entries ([weekly_writer.py](E:/nakama-adr041-41d/shared/weekly_writer.py:463), [weekly_writer.py](E:/nakama-adr041-41d/shared/weekly_writer.py:533)). This is fine if `done` is machine-written numeric. A quoted `"0"` would be truthy and preserved, but your “no hand-edited frontmatter” premise makes that low-risk.

Unlinked fallback double-write is mechanically safe: `reschedule_task_block` writes once, then `schedule_block` re-upserts the same target date using the fresh token ([calendar_scheduler.py](E:/nakama-adr041-41d/shared/calendar_scheduler.py:172)). But if fallback returns `FAILED`, the task route collapses it into `cal_unavailable` instead of distinguishing rollback failure ([bridge_weekly.py](E:/nakama-adr041-41d/thousand_sunny/routers/bridge_weekly.py:814)).

Date/time parsing is clean: date parse is date-only, time parse rejects seconds/offsets, and `datetime.combine` stays naive Asia/Taipei ([bridge_weekly.py](E:/nakama-adr041-41d/thousand_sunny/routers/bridge_weekly.py:171), [bridge_weekly.py](E:/nakama-adr041-41d/thousand_sunny/routers/bridge_weekly.py:248), [bridge_weekly.py](E:/nakama-adr041-41d/thousand_sunny/routers/bridge_weekly.py:792)).

Stale idempotency key is only benign under the UI invariant. Because the server does not enforce “linked tasks cannot use create,” a stale/direct `/weekly/schedule` POST after reschedule can miss the old event’s original `slug@date` key and create another event ([calendar_scheduler.py](E:/nakama-adr041-41d/shared/calendar_scheduler.py:161), [google_calendar.py](E:/nakama-adr041-41d/shared/google_calendar.py:136)).

**3. Best-Effort / Degradation**

Clean — checked update outage/conflict/delete fail: vault writes happen before calendar calls; calendar exceptions are converted into status data for reschedule and cancel ([calendar_scheduler.py](E:/nakama-adr041-41d/shared/calendar_scheduler.py:164), [calendar_scheduler.py](E:/nakama-adr041-41d/shared/calendar_scheduler.py:193), [calendar_scheduler.py](E:/nakama-adr041-41d/shared/calendar_scheduler.py:215)).

One degradation nit: `delete_event` 404/already-gone will currently show `cancel_cal_failed`, although the desired end state is already true ([google_calendar.py](E:/nakama-adr041-41d/shared/google_calendar.py:209)). Treating Google 404 as `CANCELLED` would make cancel idempotent.

**4. Deferred Uniform-Token Decision**

Defensible for plain add/remove/sync only if you accept UI-only Web mutations and low concurrency. Not defensible for `/weekly/schedule` because it is not just a vault edit; it can create a second downstream event. The concrete race is stale dashboard page → task linked/rescheduled elsewhere → stale `/weekly/schedule` POST → new event created → old event orphaned.

**5. Alternatives**

- Add a server-side guard in `/weekly/schedule`: after `find_task`, if `task.calendar_event_id`, do not call `schedule_block`; redirect to the task page or return an “already linked, use reschedule” banner.
- Make `expected_token` required on the three 41d POSTs and pass it through unchanged.
- Optional: update the Google extended-property idempotency key when rescheduling, or stop relying on it after link creation and enforce event-id-only flows.

**6. Verdict**

**BLOCK** until the first two fixes land.

Must-fix list:

1. Server-enforce “linked task cannot enter `/weekly/schedule` create path.”
2. Do not allow omitted/empty `expected_token` to disable locking on reschedule/unlink/unschedule.
3. Add tests for both: stale linked schedule POST does not create a new event, and missing token is rejected/conflicts.

I could not run the focused pytest files because the environment policy rejected `python`/`pytest`; this is a code-read audit.
