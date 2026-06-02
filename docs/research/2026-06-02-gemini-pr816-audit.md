# Gemini audit — PR #816 (ADR-041 41c backlog calendar scheduler)

_Verbatim Gemini 2.5 Pro audit, multi-agent-panel 2026-06-02. Verdict: BLOCK (see integration notes — title false-positive rejected, token deferred)._

Alright, team. I've completed my independent audit of PR #816. My perspective is focused on finding failure modes under real-world usage, particularly around the vault/calendar contract and internationalization. This is not a rubber-stamp; my job is to push back and harden the system.

Here is my file-by-file analysis.

### 1. Code Grounding (Route logic, Datetime, API calls)

This section covers the core implementation of the `POST /weekly/schedule` route and its supporting logic.

-   **`thousand_sunny/routers/bridge_weekly.py`, line 241:** The composition `start = datetime.combine(ed, et)` is correct for creating a naive datetime as specified in D4. Since the backend `calendar_scheduler` is expected to handle the Asia/Taipei timezone context, this handoff is clean.

-   **`thousand_sunny/routers/bridge_weekly.py`, lines 122-126 & 265-267:** The banner mapping from `ScheduleOutcome` is mostly correct, but has a subtle bug. The generic `cal_conflict` message (`_PLAN_ERRORS`, line 80) is unreachable. If a conflict occurs (`n > 0`), the code *always* constructs the f-string version. If `n` were 0 (which shouldn't happen for a `CONFLICT` status), the original message would be used, but this seems unlikely. This isn't a critical failure, but it's dead code.

-   **`thousand_sunny/routers/bridge_weekly.py`, lines 243-254:** The call to `calendar_scheduler.schedule_block` correctly passes `force=bool(force)`. However, it's missing the `title` parameter from the form post. The form has `<input type="hidden" name="title" value="{{ t.title }}">` but the route signature on line 223 has `title: str = Form("")`. This means if a user has a task with frontmatter `title: My User-Facing Title`, the code will pass an empty string to the scheduler, which then defaults to using the `task_slug`. **This is a bug.** The correct value from the form needs to be passed.

### 2. Contract/Drift (Vault vs Calendar SoT)

This section evaluates adherence to the architectural decisions (ADR-041 D1/D2).

-   **`tests/test_bridge_weekly.py`, lines 653-667 & 669-684:** The tests for calendar conflicts and outages correctly assert that the plan is written to the vault (`scheduled` frontmatter is set) even when the calendar event is not created. This explicitly validates D1 (Vault is SoT) and D2 (Calendar is best-effort). This is well-implemented.

-   **`shared/weekly_indexer.py`, lines 689-695:** The introduction of `backlog_by_project` creates a new data view. This is distinct from the week-scoped `incomplete` list. The logic `backlog = [t for t in all_tasks if not t.done]` is correct. There is no risk of double-writes as the backlog is a read-only view for populating a form; the write path is a separate POST handler. This section is clean — checked for potential confusion between `incomplete` and `backlog`.

-   **Idempotency Key (CJK Handling):** The backend relies on an idempotency key `{slug}@{YYYY-MM-DD}` (D7). The test `test_schedule_creates_event_and_links` (`tests/test_bridge_weekly.py`, line 646) correctly mocks and asserts this with a CJK slug: `assert seen["idempotency_key"] == "測試任務@2026-06-03"`. This confirms that non-ASCII slugs are being handled correctly in the key generation, which is a critical detail for our user base. This is clean.

### 3. Numerical/Logic

-   **`thousand_sunny/templates/bridge/weekly.html`, line 431-437:** The JS duration calculation `(pom * 30)` is correct per D2 (1 🍅 = 30 min). The midnight wrap logic (`mins = ((mins % 1440) + 1440) % 1440;`) is a robust way to handle scheduling past 23:59. This is clean.

-   **`thousand_sunny/routers/bridge_weekly.py`, lines 266-267:** The conflict count `n` is passed correctly to the redirect URL (`&n=len(outcome.conflicts)`), and the `weekly_landing` function correctly uses it. This is clean.

-   **`shared/weekly_indexer.py`, lines 692-693:** The backlog sorting `key=lambda t: (t.scheduled is not None, t.scheduled or date.max, t.name)` is thoughtful. It correctly prioritizes unscheduled tasks (`is not None` evaluates to `False` first) and then sorts scheduled tasks by date. This is good UX. This section is clean.

### 4. Assumption Push-back (Real Usage Failure Modes)

Here's where the implementation shows cracks under real-world pressure.

-   **Race Conditions / Stale Data:** The `POST /weekly/schedule` route does not accept or validate an `expected_token` for the *task file*. The `schedule_block` function it calls *does* support this parameter. This creates a classic race condition:
    1.  User opens the weekly view. A task "測試任務" has `est_pomodoros: 2`.
    2.  User switches to Obsidian and changes the estimate to `est_pomodoros: 5`.
    3.  User returns to the browser and schedules the task using the now-stale form, which still shows 2 pomodoros.
    4.  The server accepts the POST and schedules a 2-pomodoro block, overwriting the user's recent change in the vault.
    This is a data-loss scenario. The form should include a hidden input with the task's last-read token, and the route must pass it to the backend writer.

-   **Double-Submit:** There is no client-side or server-side guard against double-submitting the schedule form. A user double-clicking "排到行事曆" would fire two POST requests. The second request would likely fail the vault write due to the first request changing the file token, but this isn't guaranteed. The idempotency key in the calendar API might prevent a duplicate event, but the user experience will be confusing. Some simple JS to disable the button on submit is a baseline requirement.

-   **Scheduling in the Past:** The UI allows selecting any date, including past dates. The `calendar_scheduler` might handle this, but the UI itself doesn't guide the user. Permitting this could lead to confusion or failed calendar events if the provider rejects them. For v1, the date picker should probably be constrained to `min="{{ view.today_iso }}"`.

-   **CJK Slug Handling in Forms/URLs:** While the idempotency key handles CJK characters well, the task slug is also used in the form value `<input type="hidden" name="task_slug" value="{{ t.slug }}">`. When this form is submitted, the browser URL-encodes the body. FastAPI correctly decodes this. However, this relies on every layer (browser, server framework) handling UTF-8 correctly, which is true here but is a known point of failure in other stacks. It's worth noting this dependency. The current implementation is correct but fragile in a wider context.

### 5. Alternatives

-   Instead of rendering dozens of open forms in the DOM for a large backlog, which can degrade performance, an alternative would be a single modal form. Clicking "Schedule" on any task would populate this shared modal with the task's details (`slug`, `title`, `est_pomodoros`). This simplifies the DOM, reduces JS listeners, and makes it easier to implement robust features like submit-button-disabling in one place.

-   For the stale data problem, a simpler (but less robust) alternative to per-task ETag/tokens would be to pass the `weekly_token` from the main page. The backend write could re-validate that the weekly file hasn't changed. This is less precise than a per-task token but better than nothing.

### 6. Verdict: **BLOCK**

I cannot recommend shipping this PR as-is. The data loss potential from the race condition is a critical flaw that violates user trust in their own data vault.

**Top 3 Must-Fix Items:**

1.  **Stale Data Overwrites (Data Loss):** The `POST /weekly/schedule` route **must** be updated to handle concurrency. The template (`weekly.html`, `backlog_row` macro) needs to include a hidden field for the task's ETag/token, and the route must pass this `expected_token` to `calendar_scheduler.schedule_block` to prevent overwriting recent changes made outside the browser.
2.  **Missing `title` Parameter:** The `weekly_schedule` route (`thousand_sunny/routers/bridge_weekly.py`, line 249) must pass the `title` from the form data to the `schedule_block` call. Failing to do so ignores user-facing titles in favor of slugs, which is a functional bug.
3.  **Double-Submit Risk:** Add simple client-side JavaScript (`thousand_sunny/templates/bridge/weekly.html`, script block) to disable the "排到行事曆" button on form submission to prevent duplicate requests and user confusion.