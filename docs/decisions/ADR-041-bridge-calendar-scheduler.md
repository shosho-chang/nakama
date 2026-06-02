# ADR-041 — Bridge time-block scheduler (task → calendar projection of a plan entry)

**Status:** Proposed **v2** — incorporates the 2026-06-02 cross-model panel (Codex + Gemini both returned *rework*; integrated below). Awaiting 修修's final go before slicing. v1 draft is in git history.

**Context owner:** 修修. **Surface:** Bridge weekly dashboard + `shared/weekly_writer.py` + `shared/google_calendar.py`. **Family:** ADR-039 / ADR-040. **Depends on:** PR #812 (`task_file_token` / byte-splice task writes).

## Panel audit trail (v1 → v2)
- **Inverted the source of truth** (Codex §3 + Gemini §2, 修修 confirmed): the **Obsidian vault** — concretely the task's `plan[]` — is the source of truth; the Google Calendar event is a **downstream representation**. v1 had it backwards (calendar-as-truth), which left `plan[]` un-reconciled and could drift.
- **Corrected v1's code-grounding errors** (Codex §1): `find_conflicts` is an *event-overlap check* (`events.list`), **not** the Google FreeBusy API; `_as_date` **already** parses a datetime `scheduled` (so D5 is a *semantic* concern, not a parsing fix); `_sync_task_from_calendar_update` only runs after Nami's *own* update (there is **no** external-calendar reconciler daemon); `task_file_token` does not exist on `main` — it lands with **#812**.
- **Timezone** (Gemini §1, 修修 confirmed): use Google's `start.timeZone` field with an IANA id instead of strip-and-hard-`+08:00`. v1 is **Asia/Taipei-only**; multi-tz/DST deferred.
- **Adopted** the panel's answers to all three v1 open questions (see D2/D7/D9).

---

## Context

修修 wants, from the weekly dashboard: (1) a view of **all incomplete tasks**, and (2) to schedule one onto a specific day at a specific clock time, where the block becomes a **real timed Google Calendar event**. 1 🍅 = 30 minutes of calendar block.

### Inventory (why this is an ADR)
Six task-creation/scheduling entry points already write `TaskNotes/Tasks/*.md` (Nami `create_task`/`create_calendar_event`/`create_project_with_tasks`, `project_writer.create_task`, Bridge `plan[]`, manual Obsidian). Two scheduling concepts already coexist on a task:
- **`plan[]`** `{date, pomodoros, reason?, done?}` — weekly pomodoro **effort allocation** (ADR-039, Bridge). Date-only.
- **`scheduled` + `scheduled_end` + `calendar_event_id`** — a **calendar appointment** (Nami `create_calendar_event` → `_write_calendar_linked_task`, `gateway/handlers/nami.py:1443-1455`). `scheduled`/`scheduled_end` are tz-stripped Asia/Taipei datetimes. `shared/google_calendar.py` provides `create_event`/`update_event`/`delete_event` and `find_conflicts` (an `events.list` overlap check).

The naïve `plan[].time` would be a **third** representation — rejected (the ADR-040 A8 split-record trap).

---

## Decision

### D1 — The vault is the source of truth; the calendar is a downstream representation

**The Obsidian vault is the canonical store for all content; the Bridge UI and Google Calendar are views that orbit it** (修修's stated principle; generalises ADR-039 Tier-B "vault-as-substrate" + ADR-040 A1). Concretely: a task's **`plan[]`** is the authoritative record of *intent to spend N 🍅 on day D*. The calendar event is a **projection** of that intent. Consequences cascade through every decision below:
- A schedule action **always** writes `plan[]` first; the calendar event is best-effort.
- An external calendar edit is an *incoming change proposal* to be reconciled **back into `plan[]`**, never the master copy.

### D2 — One schedule action = authoritative `plan[]` write, then best-effort calendar projection

Scheduling a backlog task (date + start time + 🍅 count, default `預估🍅`):
1. **Write `plan[]`** `{date, pomodoros, reason?}` (the existing `add_plan_entry`; weekend still needs a reason — ADR-039 D9). **This must succeed for the action to succeed.**
2. **Best-effort** create the calendar event and store `scheduled` (start), `scheduled_end` (start + block), `calendar_event_id` on the task.
3. If the calendar step fails (token expired / API down / conflict-not-forced), the **plan stands**; the page shows a non-fatal "未連動行事曆，可稍後重試" banner. (Open-Q1 → *plan succeeds, calendar best-effort*.)

**Block length:** `CALENDAR_BLOCK_MINUTES_PER_POMODORO = 30` = a 25-min focus 🍅 + 5-min buffer (Pomodoro-technique convention; 修修's "一顆番茄半小時"). **Counting/aggregation stays 25** (`POMODORO_MINUTES = 25`) — the 30-min block is *calendar wall-clock only*, documented so planned/actual 🍅 units don't appear to diverge.

### D3 — Reuse the calendar-linked-task fields as the projection's cache

The projection writes the **existing** `scheduled`/`scheduled_end`/`calendar_event_id` (no new keys; same convention Nami uses). Concurrency on the task file uses **#812's `task_file_token`** (If-Match content hash) — `weekly_file_token` is for weekly files only. Conflict detection on create is `find_conflicts` (event-overlap; **not** FreeBusy); on conflict the UI offers **force** (mirrors Nami's `force=true`).

### D4 — Timezone: Asia/Taipei-only v1, but emitted via Google's `timeZone` field

v1 assumes 修修 schedules in **Asia/Taipei** (single user, no DST). But `create_event` is extended to send `start.timeZone`/`end.timeZone = "Asia/Taipei"` with a **naive local** `dateTime` (Google then renders/DST-handles correctly), instead of the current strip-and-hard-`+08:00`. Multi-user / IANA-per-user / DST zones are **deferred** — the `timeZone` field makes that a later additive change, not a rewrite.

### D5 — `scheduled` is semantically overloaded; `plan[]` stays the planning authority

`scheduled` now means either *due-by* (date, legacy/Nami plain tasks) or *appointed-at* (datetime, a calendar block). `_as_date` already parses both for week-placement, so there is **no parsing bug**; but consumers must not infer *planning* from `scheduled`. Because **`plan[]` is the planning authority (D1)**, the overload is low-risk: the dashboard's 🍅 math reads `plan[]`, not `scheduled`.

### D6 — One timed calendar block per task (v1)

`plan[]` upserts by date and a task holds a single `calendar_event_id`, so v1 links a task to **at most one** timed block. Scheduling a second time **replaces** the block (reschedule). Multiple concurrent blocks per task (e.g. 2🍅 Tue + 3🍅 Wed) are **deferred**.

### D7 — Idempotency + rollback

Stamp the event with an `extendedProperties.private.lifeos_task = "{slug}@{date}"` so a double-submit/retry finds the existing event instead of duplicating, and rollback can locate it reliably. Create order: `plan[]` write → event create → store id. If the id-store write fails after event creation, delete the event (mirror Nami `nami.py:1393-1400`); the `plan[]` entry remains (D1).

### D8 — Reschedule

Edit the `plan[]` entry + `update_event` + rewrite `scheduled`/`scheduled_end`. `update_event` has **no** built-in conflict check, so the route pre-checks overlap (like Nami's wrapper `nami.py:1525-1531`) and offers force.

### D9 — Cancel = two distinct actions (never auto-drop `plan[]`)

- **「移出行事曆」** — `delete_event` + clear `scheduled`/`scheduled_end`/`calendar_event_id`; **keep `plan[]`** (you still plan to do the work, just not as a calendar block).
- **「取消排程」** — remove the `plan[]` entry (preserving `plan[].done` semantics — only drop if `done == 0`) **and** delete the event.

This **diverges from Nami's `delete_calendar_event`**, which deletes the whole task file (`nami.py:1608-1627`) — documented divergence; the Bridge never deletes a task.

### D10 — Backlog zone + native pickers

A `<details>` "📥 待排程 · 所有未完成任務" zone on `/bridge/weekly` lists every `status≠done` task (grouped by project) with an inline native `<input type=date>` + `<input type=time>` + 🍅 count + 排到行事曆. Native pickers for v1 (design-system-consistent, no widget); **mobile picker usability is a UAT check** (Gemini caution), not assumed.

---

## Consequences
- **Good:** one coherent model — vault/`plan[]` is truth, calendar is a projection; scheduling never hard-blocks on Google; reuses Nami's field convention + transactional create/rollback; the dashboard 🍅 math stays authoritative.
- **Cost/risk:** Bridge now depends on `shared/google_calendar` (OAuth token on host). The projection can lag the plan if the calendar write fails (banner + retry). External calendar edits via Nami currently update `scheduled` but **not** `plan[]` (see Deferred — reconciliation).

## Deferred (not in v1)
- **Calendar→`plan[]` reconciliation**: making Nami's `_sync_task_from_calendar_update` also propagate to `plan[]` (today it only writes `scheduled`/`scheduled_end`). Until then, *edit blocks on the Bridge, not in Google Calendar*.
- Multi-block per task; multi-day / cross-midnight blocks; all-day-event semantics; `plan[].done` vs Google RSVP status; multi-timezone / DST / per-user IANA tz.

## Alternatives rejected
- **Calendar as source of truth (v1's D3):** leaves `plan[]` un-reconciled → dashboard/calendar drift; calendar outage blocks planning. Rejected by the panel + 修修.
- **`plan[].time` (Bridge-local time field):** a third scheduling representation. Rejected (A8 split-record trap).
- **Custom month-grid widget:** heavier than native inputs for v1. Rejected.

## Slice plan
| Slice | Scope | Notes |
|---|---|---|
| **41a** | `schedule_task_block()` in `weekly_writer` — authoritative `plan[]` write + projection fields (`scheduled`/`scheduled_end`/`calendar_event_id`) under `task_file_token`; pure-data tests (no live GCal) | depends on #812 merged |
| **41b** | `google_calendar` extension: `timeZone` field + `extendedProperties` idempotency + overlap pre-check for `update_event`; create/reschedule/cancel helpers + tests (mocked) | the integration contract |
| **41c** | Backlog `<details>` zone + native date/time/🍅 picker + 排到行事曆 route (create, best-effort calendar, banner on failure) | browser-UAT |
| **41d** | Reschedule + the two cancel actions (移出行事曆 / 取消排程) | browser-UAT |
