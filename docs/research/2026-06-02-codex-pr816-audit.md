# Codex audit — PR #816 (ADR-041 41c backlog calendar scheduler)

_Verbatim Codex (GPT-5) audit, multi-agent-panel 2026-06-02. Verdict: SHIP-WITH-FIXES._

**1. Code Grounding**

- `thousand_sunny/routers/bridge_weekly.py:287-294`: `schedule_block(...)` is called without `expected_token`. The backend contract supports it, and this UI is writing the vault source of truth. A stale weekly page can silently overwrite Obsidian/Syncthing/other-tab task frontmatter.

- `thousand_sunny/routers/bridge_weekly.py:246` + `:285`: `_parse_entry_time()` uses `time.fromisoformat()`, which is broader than native `HH:MM`. It can admit non-UI shapes like seconds or tz-aware times, then `datetime.combine()` may no longer be the intended naive Asia/Taipei datetime. Reject anything except strict `HH:MM` and `tzinfo is None`.

- Outcome banner mapping is mostly correct: `created/conflict/unavailable/failed` map at `bridge_weekly.py:304-311`, and conflict count rendering is handled at `:129-134`. But `n: int | None = Query(None)` at `:110` can make `/bridge/weekly?...&n=bad` a 422 for the whole dashboard. Parse defensively instead.

**2. Contract / Drift**

- D1/D2 are honored on the happy path: vault write first, calendar degraded into status. But missing `expected_token` weakens D1 in exactly the Obsidian/Syncthing concurrency case this project already guards elsewhere.

- `shared/weekly_indexer.py:693` makes backlog “every not-done task,” not “unscheduled backlog.” Then `weekly.html:402-405` renders scheduler forms for all of them. Current-week scheduled tasks already appear in the normal week/project tabs, so the same task now has multiple plan-writing affordances on one page.

- Bigger drift: this UI exposes already scheduled tasks to a “create calendar block” flow. If a task already has a calendar event, conflict/unavailable/retry/reschedule semantics are not clear in the UI. For v1, either filter this zone to unscheduled tasks or build an explicit reschedule/update path.

**3. Numerical / Logic**

- Pomodoro duration is clean server-side: 1 🍅 = 30 min, and the route passes `pomodoros` through correctly.

- `weekly.html:83`: defaulting the picker to `t.est_pomodoros` is wrong for partially completed tasks. It should default to remaining work, e.g. `max(est - actual, 1)`, and clamp to `20`. As written, a task with `6 / 8🍅` defaults to scheduling 8 more.

- Ordering is clean within each project — checked `shared/weekly_indexer.py:693-697`: unscheduled first, then scheduled date, then name. Project group order is incidental insertion order, but that was not specified.

- `weekly.html:474-482`: the duration hint wraps past midnight but hides the date rollover. `23:30 + 2🍅` displays `→ 00:30（60 分）` without `+1d`, which is misleading.

**4. Assumption Push-Back**

- Past scheduling is allowed: no `min` on `weekly.html:80`, no server guard at `bridge_weekly.py:274-285`. If backfill is intended, label it. If not, block dates before `today_iso`.

- D8 force retry is technically present, but bad UX: conflict redirects only with `err/n` at `bridge_weekly.py:308`, and `_sched_back()` at `:314-321` drops task/date/time/pomodoros. The form resets to defaults at `weekly.html:80-83`, so “勾選強制後重送” can easily force the wrong slot.

- `weekly.html:405` renders one full form per backlog task, and JS attaches listeners to every form at `:496-498`. Fine for 12 items, questionable for a real vault backlog. Lazy-open rows or limit to unscheduled/visible items.

**5. Alternatives**

- Safer v1: make “待排程” only unscheduled open tasks. Keep rescheduling behind the task detail page or a separate explicit flow.

- Add a task frontmatter token to each schedule form and pass it as `expected_token`.

- Preserve conflict retry state in the redirect, or return to the same row with the submitted values prefilled and force highlighted.

- Strictly parse `HH:MM`, clamp/default remaining pomodoros, and show `+1d` in the JS duration hint.

**6. Verdict**

**BLOCK** until the top 3 are fixed:

1. Wire `expected_token` through the schedule form and route.
2. Do not expose already scheduled tasks to this create-style scheduler without safe reschedule semantics.
3. Make conflict retry sticky enough that `force=True` resubmits the same slot, not reset defaults.
rect/session, or return the same row open with the attempted date/time/pomodoros and force enabled.
- Add `min="{{ view.today_iso }}"` or explicit week bounds if past/out-of-week scheduling is not intended.

**6. Verdict**
SHIP-WITH-FIXES.

Top 3 must-fix before merge:
1. Strictly parse `entry_time` as naive `HH:MM` only.
2. Add task-level optimistic concurrency via `expected_token`, and stop trusting hidden `title`.
3. Fix the reschedule/conflict retry story: do not expose already scheduled tasks to a create-only flow, and preserve the attempted slot when asking users to retry with `force=True`.
