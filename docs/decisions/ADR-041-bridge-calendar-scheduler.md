# ADR-041 — Bridge time-block scheduler (task → calendar projection of a plan entry)

**Status:** Proposed **v3** — 41a–41d shipped under v2 (single-block). v3 overturns **D6** (one block per task → **one block per `plan[]` entry**, i.e. multi-block) after 修修 UAT: a task legitimately spans multiple days (`plan[]` was always multi-day), so its calendar projection must be multi-day too. v3 design is below (`## v3`); awaiting the 2026-06-04 panel + 修修's go before slicing. v1/v2 are in git history.

**Context owner:** 修修. **Surface:** Bridge weekly dashboard + `shared/weekly_writer.py` + `shared/google_calendar.py`. **Family:** ADR-039 / ADR-040. **Depends on:** PR #812 (`task_file_token` / byte-splice task writes).

## Panel audit trail (v1 → v2)
- **Inverted the source of truth** (Codex §3 + Gemini §2, 修修 confirmed): the **Obsidian vault** — concretely the task's `plan[]` — is the source of truth; the Google Calendar event is a **downstream representation**. v1 had it backwards (calendar-as-truth), which left `plan[]` un-reconciled and could drift.
- **Corrected v1's code-grounding errors** (Codex §1): `find_conflicts` is an *event-overlap check* (`events.list`), **not** the Google FreeBusy API; `_as_date` **already** parses a datetime `scheduled` (so D5 is a *semantic* concern, not a parsing fix); `_sync_task_from_calendar_update` only runs after Nami's *own* update (there is **no** external-calendar reconciler daemon); `task_file_token` does not exist on `main` — it lands with **#812**.
- **Timezone** (Gemini §1, 修修 confirmed): use Google's `start.timeZone` field with an IANA id instead of strip-and-hard-`+08:00`. v1 is **Asia/Taipei-only**; multi-tz/DST deferred.
- **Adopted** the panel's answers to all three v1 open questions (see D2/D7/D9).

---

## v3 — multi-block projection (one calendar event per `plan[]` entry)

**Status of this section:** integrates the 2026-06-04 3-way panel (Codex + Gemini, both *REWORK*; audits in `docs/research/2026-06-04-{codex,gemini}-adr041-v3-audit.md`). The draft below is **v3-final** — every panel item adopted is marked `[panel]`.

### Decisions this revision overturns (explicit — Codex §2)
v3 changes **more than D6**; each is restated here so nothing is silently contradicted:
- **D6** (one block per task) → **one block per `plan[]` entry** (the headline).
- **D3** (projection writes task-level `scheduled`/`scheduled_end`/`calendar_event_id`) → those task-level fields become **legacy/derived**; the projection cache moves onto each `plan[]` entry (V4).
- **D8** (reschedule) → still `update_event` on the **same** event, **incl. a date change** — *not* delete+recreate (V3a). `find_conflicts` self-exclusion is **by `event_id`** (V3c).
- **D9** (two cancel actions, never auto-drop `plan[]`) → kept and **strengthened**: the plain ✕ chip stays **plan-only and never deletes a Google event**; calendar deletion is a separate, labelled, `confirm()`-gated control (V3d).
- **41d orphan guard** (reject if task has any `calendar_event_id`) → **per-entry guard**: reject a *create* on a `(slug, date)` whose entry already has an `event_id` (V3b).

### Motivation (修修 UAT, 2026-06-04)
The weekly-row scheduler split into **two** forms — 「排入」(writes `plan[]`, date-only, no calendar) and 「排到 Google 行事曆」(the single timed block). 修修: that split is confusing; pressing **排入** should write the vault `plan[]` entry **and** project it to Google for **any** date at a chosen time, and a task on 6/3 **and** 6/5 must show **two** Google events. v2's D6 makes that impossible (scheduling 6/5 *moves* the 6/3 event). v3 lifts the projection from task-level to **per-`plan[]`-entry**.

### V1 — a `plan[]` entry is the unit of projection
Each `plan[]` entry MAY carry a clock time and project to its **own** Google event.

```yaml
plan:
  - date: 2026-06-03                 # existing (date-only key for the entry)
    pomodoros: 4                     # existing → block = 4 × 30 = 120 min
    start: "2026-06-03T09:00:00+08:00"  # NEW optional — ISO-8601 WITH offset [panel: Gemini #5, no naive]
    end:   "2026-06-03T11:00:00+08:00"  # NEW derived (start + pomodoros×30); stored for the view
    event_id: "abc123"                  # NEW optional Google event id (also locatable via the key)
    reason: "..."                    # existing (weekend gate, D9)
    done: 0                          # existing
```
- No `start` ⇒ **plan-only** (no calendar), exactly today's 排入. With `start` ⇒ a timed, projected block.
- One entry per `(slug, date)` (plan upserts by date) ⇒ **≤1 block per task per day**, keeping `{slug}@{date}` unique. **Migration must dedupe / fail-closed on hand-duplicated dates** [panel: Codex §3].
- `start`/`end` stored **with the `+08:00` offset**, never naive [panel: Gemini #5 — naive local is a DST/travel time-bomb]. D4's "send Google a `timeZone` field" is unchanged; this only hardens the vault storage.

### V2 — one 排入 = `plan[]` write + best-effort projection (transactional, vault-first — **not** "atomic" [panel: Gemini 4f])
The row's single 「排入」 form takes **date (full picker, any week) + time + 🍅 + reason + force**, and **carries `task_file_token`** [panel: Codex §1d/§4 — today's `/weekly/schedule` omits it]. On submit:
1. **Authoritative**: upsert the `plan[]` entry `{date, pomodoros, start, end, reason?}` under `task_file_token` (If-Match; stale ⇒ conflict banner). Must succeed.
2. **Best-effort**: find-or-create the Google event keyed `{slug}@{date}`; write `event_id` back to that entry. **Per-entry D7 rollback** [panel: Codex §2/§5]: if the `event_id` write-back fails after create, delete the event; the entry stands (D1).
3. Calendar failure ⇒ entry stands + non-fatal banner. **Time omitted ⇒ plan-only, no calendar step.**

Removed: the separate 「排到 Google 行事曆」 section **and** the 「scheduled → 最近排程日」 sync button (both existed only because plan and calendar were decoupled). Post-submit returns to `…#task-{slug}`; JS re-opens that row, **restores the active tab + other open rows + scroll**, and scrolls it into view [panel: both — current rows have no `id`, and a naïve reload clobbers state]. (修修 chose this redirect+reopen over AJAX to keep the POST→303 architecture; the state-preservation is the acceptance bar.)

### V3 — idempotency, reschedule, cancel, guard (per-entry)
**(V3a) Reschedule = `update_event` in place, even on a date change [panel: UNANIMOUS — Codex #2 + Gemini #1].** Delete+recreate is rejected: it destroys the Google event's identity, Meet link, attendees, RSVPs, notifications. After an `update_event` that changes the date, a **second best-effort patch** rewrites `extendedProperties.private.lifeos_task` from `{slug}@{old-date}` → `{slug}@{new-date}`. This requires **extending `update_event` to set `extendedProperties`** (it can't today — Codex §1/§4). Same-date time change = a plain `update_event`.

**(V3b) Idempotency + per-entry orphan guard.** The key stays `{slug}@{date}`. **Widen `find_event_by_idempotency_key` to look up the whole `{date}` day, not just the proposed time window** [panel: Codex §1a/§3 — today it searches only the block window, so it isn't yet a task-day uniqueness check]. The orphan guard becomes per-entry: a *create* is rejected if that `(slug, date)` entry already carries an `event_id` (reschedule/cancel are the only paths for a linked entry).

**(V3c) Conflict pre-check excludes by `event_id`, not by slug** [panel: both]. `find_conflicts` must exclude only the specific event being moved; with multiple blocks of the same task, excluding "all events for the slug" would wrongly ignore a real clash with the task's *other* block.

**(V3d) Cancel — kept as two explicit, `done`-safe actions; the plain ✕ never deletes a Google event [panel: UNANIMOUS].**
- An **unlinked** entry's ✕ = today's plan-only `remove_plan_entry` (no calendar).
- A **linked** entry shows a distinct, labelled control (e.g. 🗑 取消這天的安排) that is **`confirm()`-gated** and deletes the entry **and** its event.
- 「移出行事曆」 (clear `start`/`end`/`event_id`, keep entry) stays available per-entry.
- **`remove_plan_entry` must become `done`-safe** [panel: Codex §2 — today it removes by date unconditionally]; a linked/`done` entry must not be silently dropped.

### V4 — task-level `scheduled`/`calendar_event_id` → legacy; migration is concrete [panel: both #4]
v3 stops writing the task-level cache. To avoid an indefinite dual-read tax:
- **Indexer dual-read for display** during the transition (`TaskAllocation` gains `start`/`end`/`event_id`; **today it has none** — Codex §3 — so the model + indexer change ships **inside v3-A**, not assumed).
- **Per-task fold before any v3 mutation**: on the first v3 write to a task, fold legacy `scheduled`+`calendar_event_id` into the `plan[]` entry on `scheduled`'s date, **preserving `done`/`reason`/`pomodoros`** and resolving a `scheduled_end` that disagrees with `pomodoros×30` (trust `pomodoros`; log the mismatch).
- **One-off backfill script in v3-A** to migrate the rest, so the dual-read path can be retired (not maintained forever).
- **Derived `scheduled` mirror** (for Nami until V5) defined precisely: `start` of the **chronologically-earliest `plan[]` entry that has a `start`** (past or future), else absent. [panel: Gemini §3 — pin the rule so the mirror can't diverge.]

### V5 — Nami calendar sync iterates timed `plan[]` (its own slice)
Wording corrected [panel: Codex §1c]: Nami doesn't read `plan[]` today — it reads task-level `scheduled` for task listing (`nami.py:1134`), writes task-level `scheduled`/`scheduled_end`/`calendar_event_id` on create (`nami.py:1453`), and syncs calendar updates back into task-level `scheduled`/`scheduled_end` (`nami.py:1590`). v3-D makes those iterate the timed `plan[]` entries; until then Nami uses the derived mirror (V4).

### v3 slice plan
| Slice | Scope | Type |
|---|---|---|
| **v3-0** | This ADR revision + 3-way panel + 修修 sign-off | HITL |
| **v3-A** | **Bundled so reads/writes can't diverge** [panel]: `TaskAllocation`+indexer dual-read; `weekly_writer` per-entry `start`/`end`/`event_id` + `done`-safe remove + per-task fold; `calendar_scheduler` schedule/reschedule(`update_event`+key-patch)/cancel one entry + per-entry rollback + per-entry orphan guard; `google_calendar` widen idempotency lookup to the day + `update_event` sets `extendedProperties`; one-off backfill script; tests (mocked GCal) | AFK |
| **v3-B** | Merge the two row forms → one 「排入」 (date any-week + time + 🍅) = vault + best-effort Google; carry+enforce `task_file_token`; remove the separate section + sync button; `#task-{slug}` re-open **preserving tab/open-rows/scroll**; chip shows time + linked colour; ✕ stays plan-only, linked entry gets a `confirm()`-gated 取消; **browser UAT** | HITL |
| **v3-C** | Task page (41d) reschedule/cancel become per-entry | HITL |
| **v3-D** | Nami sync iterates timed `plan[]` entries (retire the derived `scheduled` mirror) | AFK |

**v3-A must merge before v3-B** (shared writer/scheduler/indexer). Every UI slice's acceptance includes desktop real-vault browser UAT.

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
| **41c** | Backlog `<details>` zone + native date/time/🍅 picker + 排到行事曆 route (create, best-effort calendar, banner on failure) | browser-UAT ✅ |
| **41d** | Reschedule + the two cancel actions (移出行事曆 / 取消排程) | browser-UAT |

### 41d implementation notes (shipped)
The reschedule/cancel surface lives on the **task detail page** (not the backlog row): a 41c linked-task row shows a locked note pointing here, and the page already carries the task's If-Match `task_file_token`, so the destructive forms reuse it for free.

- **Reschedule (D8)** — `weekly_writer.reschedule_task_block` relocates the `plan[]` entry (drops the old scheduled date's entry unless it carries done work — its 🍅 history survives) and rewrites `scheduled`/`scheduled_end`, leaving `calendar_event_id` untouched. `calendar_scheduler.reschedule_block` then PATCHes the **same** Google event (`update_event`) — no new event, no orphan. `update_event` has no built-in conflict check, so a non-`force` reschedule pre-checks overlap with `find_conflicts` **excluding the event being moved** (else a sub-block shift would clash with its own old slot).
- **Cancel (D9)** — `unlink_calendar` (移出行事曆: clear projection fields, keep `plan[]`) and `cancel_schedule` (取消排程: also drop the scheduled-date `plan[]` entry unless `done`). Both delete the Google event best-effort; a delete failure degrades to a `cancel_cal_failed` banner with the vault already authoritatively cleared.
- **Per-task optimistic lock (deferred item H) — scoped to the new routes; uniform rollout escalated.** The three new destructive routes (reschedule/unlink/unschedule) **hard-enforce** `expected_token` (the page carries it; an omitted/empty token now *conflicts* rather than silently opting out — fixed after the panel). The broader retrofit of the older dashboard plan routes (add/remove/sync) is still **deferred but flagged**: 修修's original sign-off (vault = pure substrate, no hand-edited frontmatter) covered Obsidian/external writers, but the panel surfaced a *new* scenario it didn't — a **two-Bridge-tab last-write-wins race** on `add_plan_entry` (both tabs read, both upsert the same day, the first write is silently lost). Single-user, so rare; the fix needs the dashboard to render a per-task token (a SHA-1 read on the all-tasks hot path). Pending 修修's call (see PR).
- **Server-side orphan guard (panel fix).** `/weekly/schedule` now rejects a task that already carries `calendar_event_id` (`err=already_linked`) — previously only the UI hid the picker, so a stale dashboard / direct POST could create a *second* event and orphan the first. Reschedule/cancel are the only paths for a linked task.
- **Idempotent cancel (panel fix).** `delete_event` now treats a Google 404/410 (event already gone) as success, so a re-sent cancel doesn't spuriously report `cancel_cal_failed`.
- **Known v1 limitation — stale idempotency key after reschedule.** `update_event` keeps the event's `extendedProperties.lifeos_task = {slug}@{original-date}` key, so after a date change the key no longer matches the new date. Benign in v1: the server orphan guard above means a linked task never re-enters the create path, and reschedule/rollback locate by event **id**, not key. A multi-block (D6-deferred) future would need the key refreshed.
