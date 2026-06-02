Read-only audit completed. Verdict: **rework**, not accept-with-changes.

**1. CODE GROUNDING**

- `_write_calendar_linked_task` does write the existing linkage fields, but not “exactly” only those fields. It writes task metadata plus `"scheduled": _strip_tz(event.start)`, `"scheduled_end": _strip_tz(event.end)`, and `"calendar_event_id": event.id` (`gateway/handlers/nami.py:1443-1455`). `_strip_tz` converts tz-aware values to Asia/Taipei and emits tz-less `YYYY-MM-DDTHH:MM:SS` (`gateway/handlers/nami.py:2423-2439`). ADR-041 D1/D2 is directionally true here.

- `_sync_task_from_calendar_update` overwrites task `scheduled` and `scheduled_end` from the calendar event, optionally renames the task, then writes the task (`gateway/handlers/nami.py:1583-1605`). But it is **not** a general external-calendar reconciler: the only call site is after Nami itself calls `google_calendar.update_event` (`gateway/handlers/nami.py:1563`). ADR-041 D3 overstates this.

- `create_event` does conflict checking, but not literal Google FreeBusy. It calls `find_conflicts` when `check_conflict=True` (`shared/google_calendar.py:102-105`), but `find_conflicts` explicitly says it does **not** use `freebusy.query`; it uses `events.list` and overlap filtering (`shared/google_calendar.py:137`, `shared/google_calendar.py:172-184`). ADR wording should say “calendar event overlap check,” not “freebusy.”

- Nami rollback-on-task-write-failure is real: after calendar creation, it calls `_write_calendar_linked_task`; on failure it logs rollback and calls `google_calendar.delete_event(event.id)` (`gateway/handlers/nami.py:1393-1400`).

- `_as_date` does **not** currently mishandle datetime `scheduled`. It handles `datetime` directly and parses string date portions via `date.fromisoformat(s[:10])` (`shared/weekly_indexer.py:146-154`), and task `scheduled` uses that helper (`shared/weekly_indexer.py:492`). ADR-041 D5 describes a compatibility fix that appears already implemented.

**2. DRIFT / CONTRADICTION**

- D3 claims task-file concurrency via `task_file_token`, but I found no such helper. The existing token is `weekly_file_token` for weekly files (`shared/weekly_writer.py:379`), `_check_token` is wired to `write_weekly` (`shared/weekly_writer.py:386`, `shared/weekly_writer.py:507`), and Bridge plan mutations call `add_plan_entry` without a task token (`thousand_sunny/routers/bridge_weekly.py:173`).

- Existing writer docs say `add_plan_entry` mutates only `plan` and leaves `scheduled` untouched (`shared/weekly_writer.py:190`, `shared/weekly_writer.py:218`). `sync_scheduled_to_next_plan` says it is the “ONLY write path” touching `scheduled` in that writer (`shared/weekly_writer.py:251-254`). ADR-041 needs to explicitly retire or narrow that invariant.

- `plan[]` and datetime `scheduled` can coexist syntactically, but the indexer treats `plan[]` as dominant: if `self.plan` exists, planned counts/dates come from `plan`, not `scheduled` (`shared/weekly_indexer.py:236-245`, `shared/weekly_indexer.py:251-268`). If Nami moves the calendar event, `_sync_task_from_calendar_update` updates `scheduled`, but **not** `plan[]`. The dashboard can then show the old plan date while the calendar says the new date.

- ADR’s “1 pomodoro = 30 min” conflicts with the current system authority: `POMODORO_MINUTES = 25` (`shared/pomodoro_aggregator.py:41`) and writer nominal pomodoro mode is 25 minutes (`shared/weekly_writer.py:35`). Maybe calendar blocks intentionally include buffer, but the ADR must say that; otherwise planned and actual units diverge.

- Bridge cancel semantics conflict with Nami’s existing delete semantics. Nami `delete_calendar_event` deletes the linked task file after deleting the event (`gateway/handlers/nami.py:1608-1627`). ADR-041 D4 proposes keeping the task and clearing fields/removing plan. That divergence needs to be explicit.

**3. THE SOURCE-OF-TRUTH RULE**

D3 is not coherent yet. “Calendar is source of truth” can work only if task fields are treated as a cache and every derived representation is reconciled. Today, `plan[]` is not reconciled.

Concrete failure modes:

- Bridge creates event, then a Nami-side sync “fires”: there is no automatic sync. If a Nami update happens before Bridge writes `calendar_event_id`, `_find_task_by_calendar_id` finds nothing (`gateway/handlers/nami.py:1269-1278`). If it happens after, it overwrites `scheduled`/`scheduled_end`, but still leaves `plan[]` untouched.

- Bridge reschedules while Nami also updates the event: Calendar API last write wins, but task-file writes are unguarded by any task token. The task can land with calendar time from one actor and `plan[]` from the other.

- Double-submit on Bridge create can produce duplicate calendar events. Existing Nami create protects by task title before event creation; scheduling an existing Bridge task needs an idempotency key or in-flight guard.

- Cancel has two orphan cases: delete succeeds but task write fails leaves a stale `calendar_event_id`; task clear succeeds but delete fails leaves an unlinked calendar event.

**4. THE OPEN QUESTIONS**

1. **Calendar-write failure policy:** fail the timed scheduling action. Optionally offer an explicit “plan-only” fallback, but do not silently write `scheduled`/`scheduled_end` without a calendar event. That creates a fake appointment and violates D1/D3.

2. **D3 ownership:** calendar should own event time, but Bridge edits should “win” only by editing the calendar first. Task fields should be cache. To make that real, add task-file tokens, idempotency, and a reconciliation path for `plan[]`.

3. **Cancel semantics:** do not silently remove `plan[]` by default. Canceling a timed appointment and deallocating planned work are different actions. If you do remove the plan entry, only do it when it was created by this calendar block and `done == 0`; otherwise preserve or ask. Existing `plan[].done` is parsed and preserved on same-date updates (`shared/weekly_writer.py:211`, `shared/weekly_indexer.py:467`).

**5. ALTERNATIVES / MISSING DECISIONS**

- Timezone source: browser local time vs fixed Asia/Taipei. Current calendar helper appends `+08:00` to naive inputs (`shared/google_calendar.py:213-223`) and Nami strips back to Asia/Taipei local time.

- All-day events: `CalendarEvent` can parse date-only events (`shared/google_calendar.py:206-207`), but overlap compares parsed datetimes directly (`shared/google_calendar.py:232-242`), which risks aware/naive comparison problems.

- Idempotency and duplicate prevention: needs operation id, event extendedProperties, or pre-existing-link guard.

- Multiple blocks per task/day: current `plan[]` upserts by date only, and the task has one `calendar_event_id`. ADR must decide whether v1 allows only one scheduled block per task.

- Reschedule conflict policy: `update_event` itself has no conflict checking (`shared/google_calendar.py:141-160`); Nami does a wrapper precheck (`gateway/handlers/nami.py:1525-1531`). Bridge must too.

- `plan[].done` and logged `timeEntries[]`: moving/canceling a block after partial work needs explicit preservation rules.

**6. VERDICT**

**Rework.** The reuse of `scheduled`/`scheduled_end`/`calendar_event_id` is a good instinct, but the ADR currently underestimates the second source of truth it creates through `plan[]`.

Top required changes before build:

1. Define whether `plan[]` is an effort allocation or a derived calendar-block cache, then specify how it updates on calendar reschedule/cancel.
2. Add real task-level concurrency and idempotency; `weekly_file_token` does not protect task files.
3. Correct the ADR’s code-grounding claims: no true FreeBusy API, no `task_file_token`, no external-calendar reconciler, D5 already works.
4. Resolve the 25-minute vs 30-minute pomodoro mismatch.
5. Specify rollback/repair behavior for create, reschedule, cancel, and double-submit.
