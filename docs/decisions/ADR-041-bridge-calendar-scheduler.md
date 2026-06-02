# ADR-041 — Bridge time-block scheduler (task → calendar appointment + pomodoro plan)

**Status:** Proposed (drafted 2026-06-02 after a grill-with-docs design session; awaiting 修修 sign-off + cross-model panel).

**Context owner:** 修修. **Surface:** Bridge weekly dashboard (`/bridge/weekly`) + `shared/weekly_writer.py` + `shared/google_calendar.py`. **Family:** ADR-039 (weekly dashboard) / ADR-040 (execution layer).

---

## Context

修修 wants, from the weekly dashboard: (1) a view of **all incomplete tasks** (not just this-week's), and (2) to **schedule one onto a specific day at a specific clock time** ("幾點"), where the scheduled block becomes a **real timed calendar event**. He picks the start time; one 🍅 = **30 minutes flat**, so an N-🍅 task books an N×30-minute block.

### What already exists (inventory — the reason this is an ADR, not a quick slice)

Six task-creation / scheduling entry points write to `TaskNotes/Tasks/*.md`:

| # | Entry | Writes | Calendar |
|---|---|---|---|
| 1 | Nami `create_task` (`gateway/handlers/nami.py:1087`) | `scheduled` (date) | — |
| 2 | Nami `create_calendar_event`(also_create_task) (`nami.py:1342`) | `calendar_event_id` + `scheduled` + `scheduled_end` | **creates a timed GCal event** (freebusy conflict check + rollback-on-task-write-failure) |
| 3 | Nami `create_project_with_tasks` (`shared/lifeos_writer.py:127`) | project + default tasks | — |
| 4 | `shared/project_writer.create_task` (`project_writer.py:502`) | `scheduled` (date) | — |
| 5 | Bridge weekly (`bridge_weekly.py`) | `plan[]={date,pomodoros}`, `sync_scheduled_to_next_plan`→`scheduled` | — |
| 6 | Manual in Obsidian | frontmatter by hand | — |

**Two scheduling concepts already coexist on a task:**
- **`plan[]`** = `{date, pomodoros, reason?, done?}` — weekly **pomodoro allocation** (ADR-039, Bridge). Date-only, no clock time.
- **`scheduled` + `scheduled_end` + `calendar_event_id`** — **calendar appointment** (Nami). For calendar-linked tasks, `scheduled`/`scheduled_end` are **tz-stripped datetimes** (e.g. `2026-06-03T09:00:00`); `_sync_task_from_calendar_update` (`nami.py:1583`) pushes calendar edits back to the task — i.e. **the calendar is the source of truth** today.

`shared/google_calendar.py` already provides `create_event(title,start,end,check_conflict)` → timed events with **freebusy `find_conflicts`**, plus `update_event` / `delete_event`. **Only Nami creates events; the Bridge creates none. There is no `scheduled→calendar` batch sync — linkage is per-event via `calendar_event_id`.**

### The trap this avoids

The naïve fix — add `plan[].time` — would create a **third** scheduling representation, fragmenting "when is this task" across `plan[].time` (Bridge) and `scheduled_end`/`calendar_event_id` (Nami). That is the same "one event split across unrelated fields" failure mode Gemini flagged when ADR-040 A8 rejected the dual-track habit model. We reuse the existing convention instead.

---

## Decision

### D1 — Reuse the calendar-linked-task convention; bring event *creation* to the Bridge (reverse of Nami's direction)

Scheduling a task on the Bridge writes the **existing** `scheduled` (start datetime) + `scheduled_end` (end datetime) + `calendar_event_id`, and creates the GCal event via the **existing** `shared/google_calendar.create_event`. No new schema key. Nami (calendar→task) and Bridge (task→calendar) now share one model.

### D2 — A scheduled block is *both* a calendar appointment *and* a pomodoro plan entry (one action, two writes)

One "排到行事曆" action coalesces:
1. `scheduled` = chosen `date`T`time` (tz-stripped, Obsidian format); `scheduled_end` = start + **pomodoros × 30 min**; `calendar_event_id` = the created event's id.
2. `plan[]` gains/updates `{date, pomodoros}` for that date — so the weekly dashboard 🍅 goal/actual still counts it.

Pomodoro count defaults to the task's `預估🍅`, editable in the picker. **1 🍅 = 30 min, flat** (no break math) — `scheduled_end = start + pomodoros*30min`.

### D3 — Source-of-truth reconciliation rule (the key cross-system risk)

A task carries `calendar_event_id` that *either* side may now touch. Rule: **the calendar remains source of truth for an existing event's time.** The Bridge action is *create* (no `calendar_event_id` yet) or an *explicit* reschedule/cancel the user initiates on the Bridge. The Bridge never silently re-pushes on read; Nami's `_sync_task_from_calendar_update` stays the reconciler for externally-edited events. Concurrent edits are guarded by the same **If-Match content-hash token** Slice 2 added (`task_file_token`) on the task file, surfaced as a conflict banner — never a silent overwrite.

### D4 — Bridge gains create + reschedule + cancel (full lifecycle, no orphans)

- **Create:** freebusy-check; on conflict, show the clashing events and offer **force** (mirrors Nami's `force=true`). On task-write failure after event creation, **rollback the event** (mirror `nami.py:1391-1416`).
- **Reschedule:** `update_event(calendar_event_id, …)` + rewrite `scheduled`/`scheduled_end`/plan entry.
- **Cancel:** `delete_event` + clear `calendar_event_id`/`scheduled_end` (+ remove the plan entry for that date).

### D5 — `scheduled` is overloaded (date vs datetime) — the indexer must tolerate both

The weekly indexer parses `scheduled` via `_as_date()`. Once the Bridge writes a **datetime** `scheduled` (`2026-06-03T09:00:00`), `_as_date()` must parse the **date portion** (not fail / not drop the task). This compatibility shim is in scope; tasks scheduled by Nami today already carry datetime `scheduled`, so this also fixes a latent read bug.

### D6 — All-incomplete backlog as a collapsible dashboard zone

A `<details>` zone on `/bridge/weekly` — "📥 待排程 · 所有未完成任務" — lists every `status≠done` task (grouped by project), each with an inline **native `<input type=date>` + `<input type=time>`** picker (browser calendar popover; design-system-consistent; mobile-friendly; no custom widget) + editable 🍅 count + 排到行事曆 button. Distinct from ADR-040's week-scoped `incomplete` zone.

### D7 — Weekend reason (D9 of ADR-039) still applies

A weekend block still requires a reason (the `add_plan_entry` weekend guard), for consistency with the existing plan-write rule.

---

## Consequences

- **Good:** one scheduling/calendar model across Nami + Bridge; reuses a battle-tested transactional create-with-rollback + freebusy path; the weekly 🍅 view stays truthful because scheduling also writes `plan[]`.
- **Cost / risk:** Bridge now depends on `shared/google_calendar` (OAuth token must be present on the host; a calendar outage must degrade gracefully — schedule the *plan* write even if the event fails? **open question, see below**). The `scheduled` date/datetime overload (D5) touches read paths used everywhere — needs regression tests. The two-way `calendar_event_id` ownership (D3) is the subtle part a panel should stress.
- **Deferred:** recurring blocks; drag-to-reschedule UI; multi-day blocks; auto-suggesting free slots from freebusy.

## Alternatives rejected

- **`plan[].time` (Bridge-local time field):** fragments scheduling into two sources vs Nami's `scheduled_end`/`calendar_event_id`. Rejected (the A8 split-record trap).
- **Calendar appointment only, no `plan[]`:** the weekly 🍅 dashboard would not reflect scheduled work. Rejected (D2 keeps them coherent).
- **Custom month-grid calendar widget:** heavier to build/maintain; native inputs already give a calendar popover. Rejected for v1.

## Open questions for sign-off / panel

1. **Calendar-write failure policy:** if `create_event` fails (token expired / API down), do we still write `plan[]` + `scheduled` (degrade to a date-plan, no event) and tell 修修 "calendar not linked", or fail the whole action? (Leaning: degrade + banner, so scheduling never hard-blocks on Google.)
2. **D3 ownership:** is "calendar is source of truth, Bridge only creates / explicitly edits" the right rule, or should Bridge edits win?
3. **Cancel semantics:** does cancel remove the `plan[]` entry too, or keep the pomodoro plan and only drop the calendar event?

## Slice plan (proposed)

| Slice | Scope | Notes |
|---|---|---|
| **41a** | `_as_date` datetime-tolerance (D5) + tests | unblocks everything; tiny, no UI |
| **41b** | `schedule_task_block()` writer (scheduled/scheduled_end/calendar_event_id + plan[] + GCal create w/ freebusy + rollback) + tests | no UI yet; the contract |
| **41c** | Backlog `<details>` zone + native date/time/🍅 picker + 排到行事曆 route (create) | browser-UAT |
| **41d** | Reschedule + cancel (update/delete event) | browser-UAT |
