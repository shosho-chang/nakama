# Codex audit — ADR-041 v3 (multi-block)

GPT-5 / Codex, read-only repo access. Verbatim final audit (the run's streaming
tool-call log is omitted; this is Codex's audit message). Verdict: **REWORK**.

**1. Code Grounding**

(a) Idempotency key: checked, but with a caveat. Current code builds `{slug}@{date}` at `shared/calendar_scheduler.py:117` and passes it to Google create at `:124`. Google stores it as `extendedProperties.private.lifeos_task` via `IDEMPOTENCY_PROP = "lifeos_task"` at `shared/google_calendar.py:94`/`:159`. The value is date-scoped, so one event per task-day is compatible. **Caveat: lookup is currently constrained to the proposed event's time window, not the whole date, at `shared/google_calendar.py:138`.** v3 should widen lookup to the full `{date}` day if it relies on the key as a task-day uniqueness guard.

(b) `plan[]` upserts by date: checked, no issue for writer-created data (`add_plan_entry` `:268`, `schedule_task_block` `:387`). But this is **not a hard repair of manually duplicated `plan[]` dates**; migration should dedupe or fail closed.

(c) Nami single-field dependency: checked, with wording correction. Nami does not read `plan[]`. It reads task-level `scheduled` for task listing at `gateway/handlers/nami.py:1134`, writes task-level `scheduled`/`scheduled_end`/`calendar_event_id` at `:1453`, and syncs calendar updates back into task-level `scheduled`/`scheduled_end` at `:1590`. V5 is directionally right but "reads the single `scheduled`" is imprecise: create takes tool `start`/`end` and writes task-level fields.

(d) `task_file_token`: checked, right primitive (`weekly_writer.py:183`, enforced by `_check_token` `:729`). **Risk: current weekly row forms do not carry it; `/weekly/schedule` calls `schedule_block` without `expected_token` at `thousand_sunny/routers/bridge_weekly.py:342`.**

(e) Per-row ✕ chip: checked, no issue. Posts to `/bridge/weekly/plan/remove` (`weekly.html:35`), route calls `remove_plan_entry` (`bridge_weekly.py:234`). Does not touch Google today.

**2. Drift / Contradictions** — v3 says only D6 is overturned, but silently changes more.

- **D3 effectively overturned**: D3 writes task-level `scheduled`/`scheduled_end`/`calendar_event_id`; V4 stops. Mark it an explicit D3 revision, not hidden under D6.
- **D8 / shipped 41d contradicted**: 41d says date reschedule PATCHes the *same* Google event (no new event), `find_conflicts` excluding self — current code does exactly that at `shared/calendar_scheduler.py:226`/`:243`. v3's "date change ⇒ delete old + create new" is a different decision.
- **D7 rollback under-specified**: D7 requires create-order + rollback if the event-id write-back fails (`calendar_scheduler.py:135`/`:152`). v3 must restate the rule per `plan[]` entry.
- **Orphan guard not optional**: `/weekly/schedule` rejects any task with `calendar_event_id` (`bridge_weekly.py:336`). v3 needs a replacement *per `{slug}@{date}` / entry* guard, not just removal.
- **D9 blurred**: D9 = two explicit actions + "never auto-drop `plan[]`". v3 makes the ✕ chip delete the Google event too — turns a low-risk plan-only removal into destructive calendar deletion. Also **current `remove_plan_entry` does not preserve `done`; it removes by date unconditionally at `weekly_writer.py:565`.**

**3. Numerical / Correctness**

- Block length: checked (`CALENDAR_BLOCK_MINUTES_PER_POMODORO = 30` `weekly_writer.py:343`; `end = start + pomodoros*30` `:375`/`:474`).
- `{slug}@{date}` uniqueness valid only if `(slug, date)` is a true invariant; lazy migration must handle duplicate dates + stale orphan events. `find_event_by_idempotency_key` searches only the block window, so it is not yet a full task-day uniqueness lookup.
- Derived `scheduled` mirror necessary until Nami migrated. **`TaskAllocation` has no `start`/`end`/`event_id` fields today (`weekly_indexer.py:212`+), so v3's "indexer reads both shapes" is not true of existing code.**
- Lazy migration not fully specified: folding legacy `scheduled`+`calendar_event_id` must preserve `done`/`reason`/pomodoro count, and define behaviour when `scheduled_end` disagrees with `pomodoros × 30`.

**4. Assumption Push-Back**

- **Highest risk: delete+recreate on date-change reschedule** loses Google event identity, history, notifications, RSVP state, user edits — and has bad failure modes (delete-then-create-fails, etc.). `update_event` can't refresh `extendedProperties` today, but extending it is safer than replacing the event.
- **Lazy-on-write migration** leaves untouched tasks in legacy shape forever; acceptable only if every read AND mutation path is dual-shape. Today neither the indexer model nor the row UI supports timed per-entry fields → reads/writes can diverge unless v3-A includes data model + writer + indexer + compatibility together.
- **✕ becoming calendar-destructive** is not acceptable as written (no confirm, no new label). If the entry has an event, deleting it must be visibly a calendar action.
- **Conflict checking needs per-entry identity**: exclude only the current entry's `event_id`, not every event for the slug. Also check for an existing `{slug}@{date}` before creating on a date-change/retry.
- **`#task-{slug}` re-open under-designed**: rows have no `id` (`weekly.html:8`); a POST→303 reload loses selected tab, other expanded rows, scroll unless JS restores them.
- **Concurrency worse** when one row action does plan-write + calendar-create + event-id write-back. Put `task_file_token` on the row action, enforce it, define retry when plan write succeeds but calendar/event-id write fails.

**5. Alternatives**

- Date-change reschedule: prefer `update_event` in place + extend it to patch `extendedProperties.private.lifeos_task` from `{slug}@{old-date}` → `{slug}@{date}`. Better whenever event identity/history matters (the normal case). Delete+recreate only if Google can't re-key, or product wants a new event.
- Migration / chip: keep lazy migration for display, but run per-task migration before any v3 mutation, and make the chip split explicit — "remove plan only" for unlinked entries, "cancel linked block" (visible linked state) for entries with `event_id`.

**6. Verdict: REWORK before slicing.**

Top 5 changes required:
1. Mark **D3, D8, D9, and the 41d orphan-guard** revised where v3 changes them; don't claim only D6 is overturned.
2. Replace delete+recreate date-change reschedule with **`update_event` + `{slug}@{date}` key refresh**, or document why event-identity loss is acceptable.
3. Specify a **per-entry rollback/idempotency contract**: full-day lookup for `find_event_by_idempotency_key`, write-back-failure rollback, per-entry orphan guard.
4. Add **`task_file_token` to merged row actions** and enforce it for `/weekly/plan`, `/weekly/plan/remove`, `/weekly/schedule` replacements.
5. Make **linked ✕ destructive behaviour explicit and `done`-safe**; current `remove_plan_entry` is insufficient for v3 calendar-linked entries.
