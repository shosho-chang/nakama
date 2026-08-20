# Publish Short Due Dispatcher — P9 Task Prompt

**Stage anchor:** Stage 6 — Publishing
**Branch/worktree:** `codex/publish-dispatcher` / `E:\nakama\worktrees\publish-dispatcher`
**Frozen product decision:** one Short Release has one Asia/Taipei **Campaign Anchor** shared by YouTube Shorts, Instagram Reels, and Facebook Reels. Platform execution and receipts remain independent.

## 1. 目標

Close the first scheduled Short publishing loop without rebuilding the already-working platform adapters:

- YouTube Shorts is uploaded ahead of time and uses YouTube-native `publishAt`.
- Facebook Reels is uploaded ahead of time and uses Meta-native `video_state=SCHEDULED` plus `scheduled_publish_time`.
- Instagram Reels remains approved but uncalled until a desktop due worker reaches the Campaign Anchor, then publishes through the existing container + `media_publish` flow.
- Bridge makes the operational truth visible: a future Short can be “已排程” while YouTube/Facebook are armed and Instagram is waiting; it must also show whether the live due worker is online, stale, failed, or has never run.

The system must preserve independent Release Target retry boundaries and must prevent two worker/manual processes from dispatching the same target concurrently.

This slice is Short-only. Carousel scheduling remains planning-only and is not executed by this worker.

## 2. 範圍

Read and modify only the following modules unless a directly related fixture requires a narrowly justified addition:

- Domain and runbook
  - `agents/usopp/CONTEXT.md`
  - `docs/decisions/ADR-055-video-release-architecture.md` — add D5 for the per-platform execution policy and due-worker safety.
  - `CONTENT-PIPELINE.md` — update only Stage 6 statements made stale by this completed slice.
  - add `docs/runbooks/publish-short-due-dispatcher.md`
- Release Target claim/state
  - `shared/release_store.py`
  - `tests/shared/test_release_store.py`
- Platform-neutral dispatch and concrete adapters
  - `agents/usopp/social_publish.py`
  - `agents/usopp/meta_graph.py`
  - `scripts/publish_dispatch.py`
  - `tests/agents/usopp/test_social_publish.py`
  - `tests/agents/usopp/test_meta_graph.py`
  - `tests/scripts/test_publish_dispatch.py`
- New due scanner/worker
  - add `scripts/publish_due.py`
  - add `tests/scripts/test_publish_due.py`
- Bridge approval flow, detail disclosure, Calendar projection, and worker health
  - `thousand_sunny/routers/publish_review.py`
  - `thousand_sunny/templates/bridge/publish_cut.html`
  - `shared/publish_calendar.py`
  - `thousand_sunny/routers/publish_calendar.py`
  - `thousand_sunny/templates/bridge/publish_calendar.html`
  - `thousand_sunny/static/shosho/publish-calendar.css`
  - `tests/test_publish_review_subs.py`
  - `tests/shared/test_publish_calendar.py`
  - `tests/thousand_sunny/test_publish_calendar.py`
  - `tests/thousand_sunny/test_publish_calendar_ui.py`
- Existing heartbeat API
  - consume `shared/heartbeat.py`; change it only if a deterministic clock seam is impossible otherwise, and cover any change in `tests/shared/test_heartbeat.py`.

Do not add a client-side framework. Follow the existing server-rendered Bridge patterns. UI changes must obey `docs/design-system.md` and the `--sho-*` tokens.

## 3. 輸入

Authoritative local contracts:

- `CONTENT-PIPELINE.md`: this feature belongs to Stage 6, after approved Stage 5 Short assets exist.
- `agents/usopp/CONTEXT.md`: Release, Release Target, Campaign Anchor, and Calendar Projection are the canonical terms.
- `docs/decisions/ADR-055-video-release-architecture.md`:
  - desktop owns upload bytes;
  - DB is the Release plan/execution source of truth;
  - one Campaign Anchor is materialized to every Release Target;
  - scheduling is not approval and target outcomes never collapse into one group retry state.
- `shared/release_store.py`: target statuses are `draft | approved | uploading | uploaded | published | failed | ineligible`; `publish_at`, checkpoint, adapter, idempotency key, and `updated_at` already exist. The `(status, publish_at)` index already supports due scans. Prefer no schema migration.
- `agents/usopp/social_publish.py`: `approve_short_targets` already fans reviewed copy/anchor to all three platforms; `dispatch_release` already isolates outcomes and checkpoints, but its current read-then-write `uploading` transition is not an atomic claim and currently accepts any `uploading` target immediately.
- `scripts/publish_dispatch.py`:
  - dry-run is the default; external writes require `--execute`;
  - YouTube already sends native `publishAt` and returns local status `uploaded`;
  - Instagram already stages to R2 and publishes through Meta;
  - Facebook currently finishes every Reel as `PUBLISHED` and must gain scheduled mode.
- `thousand_sunny/routers/publish_review.py`: the Short approve-upload route currently starts all three platforms immediately. Preserve immediate behavior when there is no future Campaign Anchor; change only future-anchor behavior.
- `shared/heartbeat.py`: reuse its durable success/failure/read API with job name `usopp-short-due-dispatcher`.
- `shared/publish_calendar.py`: a future Release with native-armed `uploaded` targets must still project as `scheduled`, not `in_progress`, while the Campaign Anchor remains in the future and no target has failed.

Platform evidence and deliberately narrow interpretation:

- Meta’s official Facebook Reels sample/Postman collection supports `video_state` values including `SCHEDULED` and a `scheduled_publish_time` finish parameter: <https://github.com/fbsamples/Facebook-Reels-Publishing-API-Postman-Collection>.
- Meta’s official Instagram content-publishing flow is container creation followed by `media_publish`: <https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/content-publishing/>.
- Treat “Instagram has no native scheduling parameter in this inspected flow” as an implementation inference, not a universal platform claim. Keep it behind the adapter/worker boundary so a future native capability can replace the clock owner.

Execution policy for one Short:

1. **No future Campaign Anchor** (missing or due now): preserve the existing supervised approve-and-dispatch behavior for all eligible platforms.
2. **Future Campaign Anchor**: approve all eligible targets, then pre-dispatch only `youtube` and `facebook_reels`. Leave `instagram_reels` as `approved` without calling Meta or R2.
3. **At/after Campaign Anchor**: the live due worker may atomically claim and dispatch the unfinished Instagram target. It must not automatically retry a terminal `failed` target every poll; operator retry remains explicit.
4. **Native scheduled outcome**: YouTube and scheduled Facebook are `uploaded` locally (armed on-platform, not yet proven public). Never mark a target `published` merely because local time passed.

## 4. 輸出

Deliver all of the following.

### A. Atomic Release Target claim with stale recovery

Add a public deep-module claim API in `shared/release_store.py` and make `dispatch_release` use it before every adapter call.

- The claim must be one conditional SQL mutation, not read-then-write.
- A normal claim may transition `approved` (and an explicitly requested manual retry state, if retained) to `uploading` only when the row still matches.
- A second concurrent claimant must receive a non-claim result and must not call the adapter.
- An `uploading` target may be reclaimed only after a documented lease/staleness threshold using `updated_at`; checkpoint data must remain intact so adapter resume logic can continue.
- Provide deterministic `now`/stale-cutoff seams for tests. Do not add owner/lease columns unless the existing status + `updated_at` contract is proven insufficient.
- Checkpoint updates during an active adapter call continue to refresh `updated_at`.
- `published`, `uploaded`, `draft`, and `ineligible` targets are never claimable for a new publish call.
- A due worker must not auto-claim `failed` on every polling cycle. Explicit Bridge retry may reset `failed -> approved` before dispatch, as it does today.

### B. Facebook native scheduling

Extend `MetaGraphClient.publish_facebook_reel` and `FacebookReelAdapter`:

- Accept an optional timezone-aware scheduled instant derived from the target’s Campaign Anchor.
- Future anchor finish payload: `video_state=SCHEDULED` and `scheduled_publish_time` as the platform-required Unix timestamp.
- Missing/due anchor finish payload: preserve `video_state=PUBLISHED` and do not send a schedule field.
- Persist enough checkpoint data to prove which finish mode and instant were accepted. Re-entry after a persisted `finished` checkpoint must not call the finish endpoint again.
- A scheduled Facebook acceptance returns `AdapterResult(status="uploaded")`; an immediate publication retains `published`.
- Never infer public status solely from processing completion or wall-clock passage.
- Validate timezone awareness and fail closed on malformed anchors. Let Meta return authoritative platform-range errors rather than inventing undocumented limits.

### C. Approval routing policy and operator disclosure

Change the Short approve-upload route and detail page:

- Future Campaign Anchor: approve all three targets, spawn one dispatcher process selecting only YouTube and eligible Facebook, and leave Instagram approved.
- No anchor or anchor due now: dispatch all eligible platforms as today.
- Compare using an aware UTC clock with a deterministic seam in tests.
- Do not duplicate subprocesses. Preserve the existing progress-log behavior and per-platform retry endpoint.
- Detail UI must state the truth in plain Traditional Chinese:
  - YouTube/Facebook will be armed ahead of time with native scheduling.
  - Instagram will be sent by the desktop worker at the Campaign Anchor and may complete slightly later while Meta processes the container.
  - Scheduling does not itself prove publication.

### D. Due scanner and worker CLI

Add `scripts/publish_due.py` as a small orchestration layer over the existing store and dispatcher.

- Scan only `format=short` Releases whose target anchors form one shared Campaign Anchor.
- A target is due only at or after that aware UTC anchor.
- In this slice, automatic due dispatch selects only `instagram_reels` targets that are still `approved`, plus stale `uploading` targets eligible for checkpoint resume.
- `failed` requires an explicit operator retry and is reported, not repeatedly invoked.
- Ignore Long and Carousel completely.
- Default is `--once` dry-run: emit portable JSON plan, make no external call, claim nothing, and write no heartbeat.
- `--once --execute`: run one live cycle.
- `--watch --execute --poll-seconds N`: repeat live cycles with a bounded positive interval and graceful `KeyboardInterrupt` handling. A dry-run watch may be supported, but must never advertise a live heartbeat.
- One Release/target failure must not prevent other due targets from being processed.
- Record `shared.heartbeat.record_success("usopp-short-due-dispatcher")` after a healthy live cycle, including a no-due-work cycle. Record failure with a concise, secret-free summary when the live cycle cannot complete safely or any due target ends failed. Never include tokens, signed R2 URLs, captions, or local media bytes in heartbeat/log output.
- Return a nonzero one-shot exit code when a due target failed or the scan itself failed.
- Do not install or start a permanent service in this task.

### E. Calendar truth and worker readiness

Extend the existing Calendar projection/UI without creating a second scheduling source of truth.

- Derive worker health from the heartbeat: `never_seen | online | stale | failing`. Make the stale threshold an explicit constant and test it with injected `now`.
- Show a compact Short due-worker status in the Calendar ops surface, including last run/last success and failure streak when available.
- If at least one future Short depends on Instagram due dispatch and worker health is not online, show an actionable warning. Do not alter the Campaign Anchor or target state.
- Future shared-anchor Short with statuses drawn from `approved`/`uploaded` and no failure remains phase `scheduled`, even after YouTube/Facebook have been armed.
- At/after anchor, unfinished execution may become `in_progress`/`attention` according to explicit tested mappings; any `failed`/`ineligible` target remains visible as attention under the existing product semantics.
- Do not claim that `uploaded` means public. Keep target badges and partial outcomes visible.
- UI follows the existing dense engineering-ops aesthetic: LINE Seed TW, `--sho-*` tokens, hairline hierarchy, orange <= 4%, semantic status colors, keyboard focus, disabled states, reduced motion, and no horizontal overflow around 390 px.

### F. Runbook and domain record

The runbook must give copy/pasteable Windows PowerShell steps for:

- a dry-run one-shot;
- a supervised live one-shot against isolated test state;
- starting/stopping `--watch --execute` in the foreground;
- confirming the Calendar heartbeat changes from never-seen/stale to online;
- diagnosing failed/stale `uploading` targets and using the existing explicit retry control;
- verifying that no real platform calls occur in dry-run;
- the later supervised real probe using a newly unpublished Short.

Do not include secrets or real tokens. Record the new `Due Dispatcher`/native-arm execution language in Usopp context and ADR D5.

## 5. 驗收

The work is complete only when every applicable check below passes.

### Atomicity and state tests

- Two concurrent claim attempts against one approved target yield exactly one winner; the adapter is called exactly once.
- A fresh `uploading` target is not reclaimed.
- A stale `uploading` target is reclaimed once and resumes with its existing checkpoint.
- A due worker does not auto-retry `failed` on the next poll.
- Explicit retry resets/claims only the failed platform; successful siblings are never called again.
- Adapter exceptions persist `failed` independently and do not block other due Releases.

### Platform policy tests

- Future Facebook Reel sends `SCHEDULED` plus the correct UTC Unix timestamp, checkpoints the schedule mode, and returns local `uploaded`.
- Immediate Facebook Reel sends `PUBLISHED`, omits `scheduled_publish_time`, and returns `published`.
- Re-entry with a finished checkpoint does not finish/upload twice.
- Naive/malformed scheduled datetime fails before transport mutation.
- Future Short approval spawns only `youtube` + `facebook_reels`; Instagram remains `approved` and no R2/Instagram call occurs.
- Due-now/no-anchor Short approval preserves three-platform dispatch.

### Due worker tests

- Before the anchor, dry-run and execute cycles make zero adapter calls.
- At the anchor and after it, an approved Instagram target is called exactly once.
- Long Releases, divergent anchors, missing anchors, ineligible targets, and Carousel jobs are excluded deterministically and surfaced in JSON counts/diagnostics where useful.
- Default invocation is dry-run and writes no heartbeat.
- Live no-work/success cycle records success; live target/scan failure records failure and returns nonzero for `--once`.
- Watch mode rejects non-positive poll intervals and exits cleanly on `KeyboardInterrupt`.
- Tests use fake adapters/transports, isolated SQLite state, and fake clocks only: no YouTube, Meta, R2, browser, or media upload call.

### Calendar/UI tests

- A future Short with YouTube/Facebook `uploaded` and Instagram `approved` remains `scheduled`, not `in_progress`.
- Worker health mapping covers never-seen, online, stale, and failing with deterministic time.
- A future Instagram-dependent Short plus missing/stale/failing worker shows a warning; an online worker clears it.
- UI contract tests assert the native-arm vs due-time disclosure, last-run/failure metadata, accessible status semantics, and Podcast YouTube identity:
  - `《張修修的不正常人類研究所》`
  - `@abnormal-human-research`
  - `UCvipegP35x3-OcAs--PgAig`
- Existing All/Episode/month filters, Campaign Anchor editing, mobile agenda, and auth behavior remain intact.

### Required commands

Run at minimum:

```powershell
E:\nakama\.venv-v2\Scripts\python.exe -m pytest tests/shared/test_release_store.py tests/agents/usopp/test_social_publish.py tests/agents/usopp/test_meta_graph.py tests/scripts/test_publish_dispatch.py tests/scripts/test_publish_due.py -q
E:\nakama\.venv-v2\Scripts\python.exe -m pytest tests/test_publish_review_subs.py tests/shared/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar_ui.py -q
E:\nakama\.venv-v2\Scripts\python.exe -m pytest tests/test_publish_review_auth.py tests/thousand_sunny/test_carousel_publish_ui.py tests/scripts/test_podcast_carousel_publish_job.py -q
E:\nakama\.venv-v2\Scripts\python.exe -m ruff check shared/release_store.py agents/usopp/social_publish.py agents/usopp/meta_graph.py scripts/publish_dispatch.py scripts/publish_due.py shared/publish_calendar.py thousand_sunny/routers/publish_review.py thousand_sunny/routers/publish_calendar.py tests/shared/test_release_store.py tests/agents/usopp/test_social_publish.py tests/agents/usopp/test_meta_graph.py tests/scripts/test_publish_dispatch.py tests/scripts/test_publish_due.py tests/test_publish_review_subs.py tests/shared/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar_ui.py
E:\nakama\.venv-v2\Scripts\python.exe -m ruff format --check shared/release_store.py agents/usopp/social_publish.py agents/usopp/meta_graph.py scripts/publish_dispatch.py scripts/publish_due.py shared/publish_calendar.py thousand_sunny/routers/publish_review.py thousand_sunny/routers/publish_calendar.py tests/shared/test_release_store.py tests/agents/usopp/test_social_publish.py tests/agents/usopp/test_meta_graph.py tests/scripts/test_publish_dispatch.py tests/scripts/test_publish_due.py tests/test_publish_review_subs.py tests/shared/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar_ui.py
git diff --check
```

Also run the directly affected existing Short/Calendar suite necessary to prove no regression. If a required test cannot run, report the exact command/error and do not claim completion.

### Manual verification

Use isolated fake/local state only:

- Open a future scheduled Short detail and confirm the copy says YouTube/Facebook native scheduling and Instagram due worker.
- Approve it with subprocess/adapters faked and verify only the two native-scheduled targets are selected.
- Open Calendar at desktop and approximately 390 px; verify grouped target states, worker warning/readout, focus states, and no horizontal overflow.
- Run one dry-run due scan and verify state DB/heartbeat do not change.
- Run one fake live due cycle at the anchor and verify Instagram transitions independently and heartbeat becomes online.

## 6. 邊界

Explicitly out of scope:

- No Carousel execution, YouTube Community automation, or changes to Carousel Publish Job scheduling semantics.
- No real YouTube, Meta, R2, Cloudflare, browser, or external network mutation during implementation or verification.
- No service installation, Task Scheduler registration, daemon autostart, or unsupervised production worker activation. That happens only after a separate supervised real probe with a newly unpublished Short.
- No Stage 5 asset generation/editing, video transcoding, caption generation, or title/description rewriting.
- No Stage 7 analytics, recurring posting slots, optimal-time recommendations, drag-and-drop, or per-platform time offsets.
- No new scheduling table and no reuse of the WordPress `approval_queue`.
- No false publication inference from elapsed time, processing completion, an upload receipt, or an accepted native schedule.
- No automatic endless retry of failed targets.
- No secrets, `.env` edits, real media files, tokens, signed URLs, or unrelated worktree changes.
- Do not commit, push, merge, or create a PR; the primary agent owns review and Git operations.

When finished, report changed files with line references, tests run and exact counts, assumptions, any remaining gap, and the P7 completion self-review. Do not claim completion if any required behavior or verification remains.
