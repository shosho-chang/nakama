# Short Due Dispatcher Runbook

Stage 6 only. The worker reads the existing Release/Release Target state; it does not create a second schedule. Commands below use the repository virtualenv and never require secrets for dry-run or isolated no-work verification.

## Read-only one-shot (default)

```powershell
Set-Location E:\nakama
E:\nakama\.venv-v2\Scripts\python.exe scripts\publish_due.py --once
```

The JSON must contain `"dry_run": true`. This path does not claim a Target, call YouTube/Meta/R2, or write the heartbeat.

To prove state and heartbeat do not change, inspect them before and after the same dry-run:

```powershell
E:\nakama\.venv-v2\Scripts\python.exe -c "from shared.heartbeat import get_heartbeat; from shared.release_store import list_releases; print(get_heartbeat('usopp-short-due-dispatcher')); print(list_releases())"
E:\nakama\.venv-v2\Scripts\python.exe scripts\publish_due.py --once
E:\nakama\.venv-v2\Scripts\python.exe -c "from shared.heartbeat import get_heartbeat; from shared.release_store import list_releases; print(get_heartbeat('usopp-short-due-dispatcher')); print(list_releases())"
```

## Supervised live one-shot against isolated test state

Use a unique empty SQLite path. With no Releases, `--execute` records a healthy no-work heartbeat and cannot call a platform adapter.

```powershell
Set-Location E:\nakama
$testState = Join-Path $env:TEMP ("nakama-publish-due-" + [guid]::NewGuid().ToString("N") + ".db")
$env:DB_PATH = $testState
E:\nakama\.venv-v2\Scripts\python.exe scripts\publish_due.py --once --execute
E:\nakama\.venv-v2\Scripts\python.exe -c "from shared.heartbeat import get_heartbeat; print(get_heartbeat('usopp-short-due-dispatcher'))"
```

Keep the printed `$testState` path for diagnosis. Do not point this check at production state when testing worker mechanics.

## Foreground watch: start and stop

Start against the isolated state from the prior section:

```powershell
$env:DB_PATH = $testState
E:\nakama\.venv-v2\Scripts\python.exe scripts\publish_due.py --watch --execute --poll-seconds 60
```

Stop with `Ctrl+C`. The process handles `KeyboardInterrupt` and exits cleanly. This task does not install Task Scheduler entries, services, autostart, or a background daemon.

## Confirm Calendar readiness

The Bridge process and worker must read the same `DB_PATH`. Start/restart Bridge in the same PowerShell environment, sign in, then open `/bridge/publish/calendar`.

1. Before the first live cycle, the Short Due Dispatcher readout is `尚未執行` (or `逾時` for an old heartbeat).
2. Run the isolated live one-shot or one foreground watch cycle.
3. Refresh Calendar. The readout must become `在線`, with LAST RUN, LAST SUCCESS, and FAILURE STREAK visible.
4. If a future Short has Instagram `approved` and health is not online, Calendar shows an actionable warning. The warning never edits the Campaign Anchor or Target.

## Diagnose failed or stale uploading Targets

Print only operational fields (no captions, tokens, signed URLs, or media):

```powershell
E:\nakama\.venv-v2\Scripts\python.exe -c "from shared.release_store import get_release; r=get_release('EPISODE','CUT'); print([(t['platform'],t['status'],t['updated_at'],bool(t['checkpoint_json']),t['error']) for t in r['targets']])"
```

- `failed`: the worker reports it but will not auto-retry. Open `/bridge/publish/EPISODE/CUT` and use **只重試此平台**. That control resets only the selected failed Target to `approved` and starts its single-platform dispatcher; successful siblings stay closed.
- fresh `uploading`: leave it alone; checkpoint writes refresh the lease.
- stale `uploading`: the next live due cycle may atomically reclaim it and resume from its existing checkpoint. First confirm no supervised dispatcher is still actively uploading.
- repeated failure: inspect the per-Release progress log under `data/upload_progress/` and the Calendar failure streak. Never paste tokens or signed URLs into tickets/log summaries.

## Later supervised real probe (separate operation)

Do not reuse published media. Prepare a newly unpublished Short, confirm all Stage 5 assets/copy, and first run:

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\publish_due.py --once
```

Review the JSON identity, shared Campaign Anchor, and selected Instagram Target. In Bridge, approve the future Short and verify YouTube/Facebook become native-armed while Instagram remains `approved`. Only with the operator present, correct credentials loaded, rollback expectations agreed, and the new Short still unpublished should a separate supervised session run:

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\publish_due.py --once --execute
```

Verify the Instagram receipt independently. An `uploaded` YouTube/Facebook Target or accepted native schedule is not proof of public publication.
