# Publish Calendar Pipeline Scheduling — P9 Task Prompt

**Stage anchor:** Stage 6 — Publishing
**Branch/worktree:** `codex/publish-calendar-pipeline` / `E:\nakama\worktrees\publish-calendar-pipeline`
**Frozen product decision:** one Release or Carousel publish job has one Asia/Taipei **Campaign Anchor**; every selected platform is arranged for that exact same instant in this version. Scheduling never implies approval or publication.

## 1. 目標

Turn the existing read-only Publish Calendar into the first safe read/write Stage 6 planning slice: group all platform targets for one Short/Long/Carousel into one publication card, show its derived Pipeline phase, and let Shosho set, adjust, or remove one shared Campaign Anchor without triggering platform APIs or changing approval/execution status.

The surface must answer two different questions without conflating them:

- Pipeline: 「這個成品目前卡在哪裡？」
- Calendar: 「這個成品安排何時發布？」

## 2. 範圍

Read and modify only the following modules unless a directly related test fixture requires a narrowly justified addition:

- Domain documentation
  - `agents/usopp/CONTEXT.md` — already records the frozen Campaign Anchor language; preserve it.
  - `docs/decisions/ADR-055-video-release-architecture.md` — add a dated amendment/D4 recording one shared Campaign Anchor, target-specific execution state, and scheduling != approval.
  - `CONTENT-PIPELINE.md` — correct only the Stage 6 Line 1a / Usopp readiness statements that are now stale after PRs #1185/#1186; do not rewrite unrelated stages.
- Video/Short release scheduling
  - `shared/release_store.py`
  - `tests/shared/test_release_store.py`
- Carousel planning contract and atomic update
  - `shared/schemas/carousel_publish.py`
  - `scripts/podcast_carousel_publish_job.py`
  - `tests/shared/schemas/test_carousel_publish.py`
  - `tests/scripts/test_podcast_carousel_publish_job.py`
- Calendar projection and Bridge write surface
  - `shared/publish_calendar.py`
  - `thousand_sunny/routers/publish_calendar.py`
  - `thousand_sunny/templates/bridge/publish_calendar.html`
  - `thousand_sunny/static/shosho/publish-calendar.css`
  - `tests/shared/test_publish_calendar.py`
  - `tests/thousand_sunny/test_publish_calendar.py`
  - `tests/thousand_sunny/test_publish_calendar_ui.py`

Do not add a client-side framework. Prefer semantic HTML and server-side POST/Redirect/GET. Add JavaScript only if a progressive enhancement is impossible with HTML forms, and justify it in the completion report.

## 3. 輸入

Authoritative upstream contracts and decisions:

- `agents/usopp/CONTEXT.md`
  - Release contains independent Release Targets.
  - One Campaign Anchor is shared by all selected targets.
  - Calendar is a projection, not a second scheduling SoT.
  - Changing the anchor does not approve or publish.
- `docs/decisions/ADR-055-video-release-architecture.md`
  - DB remains the Release plan/execution SoT.
  - Release Target remains the platform retry/receipt boundary.
  - Do not reuse `approval_queue` components.
- `shared/release_store.py`
  - Existing target fields include `publish_at`.
  - Existing target status domain is `draft | approved | uploading | uploaded | published | failed`.
- `shared/schemas/carousel_publish.py`
  - Carousel job is created only after the Stage 5 approval gate closes.
  - Platform execution state and result checkpoints remain independent.
- `scripts/podcast_carousel_publish_job.py`
  - Reuse its job lock and atomic-write discipline; do not introduce a second ad-hoc JSON writer.
- Existing first-version projection and UI in `shared/publish_calendar.py` and `/bridge/publish/calendar`.
- `docs/design-system.md` and `--sho-*` tokens are mandatory for all UI changes.
- Podcast YouTube identity is frozen:
  - `《張修修的不正常人類研究所》`
  - `@abnormal-human-research`
  - `UCvipegP35x3-OcAs--PgAig`

Storage design for this slice:

1. Do not add a new database table.
2. For a video/Short Release, the shared Campaign Anchor is materialized transactionally into every selected `release_targets.publish_at` value. The public store API must make divergent/missing target timestamps observable and must update the group atomically.
3. For a Carousel publish job, add one optional timezone-aware `campaign_anchor_at` field to `CarouselPublishJobV1`; update it only through a public lock-protected, atomic helper in `scripts/podcast_carousel_publish_job.py`.
4. Existing legacy records without a Campaign Anchor remain readable and appear in the No Date area unless a trustworthy actual published timestamp exists.

## 4. 輸出

Deliver all of the following:

### A. Release-level scheduling command

Add a public deep-module API in `shared/release_store.py` that:

- addresses one Release, not one target;
- accepts either one timezone-aware instant or `None` for unschedule;
- writes the same normalized UTC ISO8601 instant to every target in one DB transaction;
- changes no target status, approval field, title, description, URL, or receipt;
- fails closed and writes nothing if the Release is missing, has no targets, or any target is in `uploading`, `uploaded`, or `published`;
- allows planning edits for `draft`, `approved`, and `failed` targets;
- detects divergent existing target anchors rather than silently selecting one.

### B. Carousel Campaign Anchor

- Add optional timezone-aware `campaign_anchor_at` to `CarouselPublishJobV1` with backward-compatible default `None`.
- Add a public scheduling helper that locks and atomically rewrites the job.
- Only a `queued` job may be scheduled/unscheduled; all other job states fail closed.
- Preserve job identity, approval evidence, target state, progress, results, retries, and timestamps except the anchor and the required monotonic `updated_at` change.

### C. Grouped projection and Pipeline phase

Replace the platform-row mental model in the UI projection with one publication group per:

- Release for Long/Short; or
- deduplicated Carousel request fingerprint/job lineage.

Each group must retain its target list and derive a deterministic phase from target/job state. Use a small explicit domain such as:

- `needs_review`
- `ready_to_schedule`
- `scheduled`
- `in_progress`
- `attention`
- `published`

Exact labels may be sharpened, but tests must freeze the mapping. Do not collapse target-specific states: a group card must expose platform badges/status and partial success (for example `2/3 published`).

Campaign Anchor display rules:

- Prefer the shared planned anchor when present.
- A trustworthy actual published timestamp may place a legacy no-anchor completed item on the calendar, with date basis `published`.
- Divergent Release target `publish_at` values are a diagnostic + No Date/attention condition, never an arbitrary chosen date.
- Failed/malformed Carousel files remain fail-soft diagnostics as in v1.

### D. Authenticated read/write Bridge surface

On `/bridge/publish/calendar`:

- keep All/Episode filtering and month navigation;
- add a compact Pipeline overview/rail with counts by derived phase;
- show one content card with multiple platform badges;
- keep a clear No Date planning area;
- provide accessible HTML controls to set/reschedule/unschedule the Campaign Anchor;
- parse operator input as Asia/Taipei and persist an aware UTC instant;
- use POST/Redirect/GET and preserve `month` + `episode` query context;
- enforce existing auth, safe episode identifiers, safe job/release identity, and fail-loud 4xx responses for stale/illegal scheduling commands;
- scheduling must call no YouTube/Meta/R2/browser API and must not enqueue/claim/publish anything.

Editing boundaries visible in the UI:

- `draft`/`approved`/`failed` Release groups may be planned.
- `queued` Carousel jobs may be planned.
- Once upload/execution has started, the schedule control is disabled with a concrete explanation; later platform-reschedule support is a separate slice.

### E. UI quality

- Follow `docs/design-system.md` exactly: LINE Seed TW, `--sho-*` only, hairline-driven dense ops UI, orange <= 4%, no gradients or large rounded SaaS cards.
- Desktop: Pipeline rail + Sunday-first month grid + No Date area.
- Mobile around 390 px: agenda layout + full-width form controls; no horizontal overflow.
- Design default/loading-equivalent/empty/error/hover/focus/active/disabled states.
- Preserve reduced motion and keyboard access.

## 5. 驗收

The work is complete only when all checks below pass.

### Domain/store tests

- A three-target Release schedule command persists one identical UTC instant to all targets.
- Unschedule clears all target anchors atomically.
- Scheduling never changes target status.
- If one target is `uploading`, `uploaded`, or `published`, the whole command fails and no target timestamp changes.
- Divergent existing target timestamps are surfaced deterministically.
- Carousel schema loads old JSON with no anchor.
- A queued Carousel job can be scheduled and unscheduled through the lock-protected atomic helper.
- A non-queued Carousel job rejects the change without mutation.

### Projection/UI tests

- Three target rows become one publication card with three platform badges.
- Pipeline phase mappings cover draft, approved/unscheduled, scheduled, in-progress, partial failure, and fully published scenarios.
- One Campaign Anchor produces one calendar placement, not three duplicate cards.
- Episode and month filters remain preserved after a scheduling POST.
- Unauthenticated POST redirects safely to login or follows the repository's established mutation-auth convention.
- Unsafe episode/job identities and malformed local datetime values fail closed.
- UI contract tests assert Pipeline overview, No Date area, date-basis language, disabled explanations, and the exact Podcast YouTube identity.

### Commands

Run at minimum:

```powershell
E:\nakama\.venv-v2\Scripts\python.exe -m pytest tests/shared/test_release_store.py tests/shared/schemas/test_carousel_publish.py tests/scripts/test_podcast_carousel_publish_job.py tests/shared/test_publish_calendar.py -q
E:\nakama\.venv-v2\Scripts\python.exe -m pytest tests/thousand_sunny/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar_ui.py -q
E:\nakama\.venv-v2\Scripts\python.exe -m pytest tests/thousand_sunny/test_carousel_publish_ui.py tests/test_publish_review_auth.py tests/test_publish_review_subs.py -q
E:\nakama\.venv-v2\Scripts\python.exe -m ruff check shared/release_store.py shared/publish_calendar.py shared/schemas/carousel_publish.py scripts/podcast_carousel_publish_job.py thousand_sunny/routers/publish_calendar.py tests/shared/test_release_store.py tests/shared/test_publish_calendar.py tests/shared/schemas/test_carousel_publish.py tests/scripts/test_podcast_carousel_publish_job.py tests/thousand_sunny/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar_ui.py
E:\nakama\.venv-v2\Scripts\python.exe -m ruff format --check shared/release_store.py shared/publish_calendar.py shared/schemas/carousel_publish.py scripts/podcast_carousel_publish_job.py thousand_sunny/routers/publish_calendar.py tests/shared/test_release_store.py tests/shared/test_publish_calendar.py tests/shared/schemas/test_carousel_publish.py tests/scripts/test_podcast_carousel_publish_job.py tests/thousand_sunny/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar_ui.py
git diff --check
```

### Manual browser verification

Using real local episode data without invoking external platform APIs:

- All content view and one episode filter.
- Set one previously undated eligible item to a future Campaign Anchor and verify one grouped card appears on that day.
- Adjust it once and verify it moves without changing approval/execution phase.
- Unschedule it and verify it returns to No Date.
- Confirm an uploaded/published/in-progress item cannot be moved.
- Verify desktop and approximately 390 px mobile layouts with no horizontal overflow.

## 6. 邊界

Explicitly out of scope:

- No YouTube, Meta, R2, Buffer, browser-agent, or other external API call.
- No actual publish, upload, claim, retry, approval, or status transition.
- No recurring weekly posting slots, Next Available queue allocator, optimal-time recommendation, drag-and-drop, or per-platform time offsets.
- No platform-native reschedule after a target reaches `uploading`, `uploaded`, or `published`.
- No Stage 5 asset generation/editing and no Stage 7 analytics.
- No new scheduling database/table and no reuse/migration of `approval_queue`.
- No secrets, `.env`, OAuth credentials, channel identity, media files, user footage, or unrelated worktree changes.
- Do not remove target-level execution state or make a group status the retry boundary.
- Do not commit, push, merge, or create a PR; the primary agent owns final review and Git operations.

When finished, report changed files with line references, tests run, assumptions, and any discovered blocker. Do not claim completion if any required behavior or verification remains.
