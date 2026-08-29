# Start Thousand Sunny FastAPI server (covers Reader, KB UI, Bridge UI)
# 以及 packaging 封面 render watcher（修修 2026-08-14：gate 存配方後自動出圖）。
# Used by Windows Task Scheduler "Nakama-ThousandSunny" on logon.
# Logs to E:\nakama\logs\thousand-sunny.log (append).
#
# Manual run: powershell -ExecutionPolicy Bypass -File E:\nakama\scripts\start_thousand_sunny.ps1
# Stop:      Stop-ScheduledTask -TaskName 'Nakama-ThousandSunny'  (or taskkill /F /IM python.exe)

$repo = 'E:\nakama'
$venvPy = Join-Path $repo '.venv-v2\Scripts\python.exe'
$resolvePy = 'C:\Users\Shosho\AppData\Local\Programs\Python\Python310\python.exe'
$logDir = Join-Path $repo 'logs'
$logFile = Join-Path $logDir 'thousand-sunny.log'

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

Set-Location $repo

# Use Start-Process so uvicorn stderr is written to file directly, not interpreted as
# PowerShell error (which would abort the wrapper). RedirectStandardOutput + RedirectStandardError
# require separate paths, so use stdout for both via cmd-style merge.
$args = @(
    '-m', 'uvicorn',
    'thousand_sunny.app:app',
    '--host', '127.0.0.1',
    '--port', '8000'
)
Start-Process -FilePath $venvPy `
    -ArgumentList $args `
    -WorkingDirectory $repo `
    -RedirectStandardOutput $logFile `
    -RedirectStandardError (Join-Path $logDir 'thousand-sunny.err.log') `
    -WindowStyle Hidden `
    -NoNewWindow:$false

# --- packaging desktop worker ------------------------------------------------
# 兩種不依賴 Resolve ABI 的工作共用 render watcher：
# 1) gate「存配方」→ render_request → render 一次。
# 2) gate Reject + feedback → revision_job → bounded Codex agent 重做 → 回到 re-review。
# 3) Highlight shortlist approve → queued Long Packaging → title + thumbnail → READY。
# Finished-cut revision 由下方 Python 3.10 supervisor 獨佔，避免 fusionscript ABI 錯誤與雙重消費。
$watcherArgs = @('scripts/render_watcher.py', '--interval', '5')
Start-Process -FilePath $venvPy `
    -ArgumentList $watcherArgs `
    -WorkingDirectory $repo `
    -RedirectStandardOutput (Join-Path $logDir 'render-watcher.out.log') `
    -RedirectStandardError (Join-Path $logDir 'render-watcher.err.log') `
    -WindowStyle Hidden `
    -NoNewWindow:$false

# --- finished-cut revision worker (Resolve/Fusion Python 3.10 ABI) -----------
if (-not (Test-Path -LiteralPath $resolvePy -PathType Leaf)) {
    throw "Resolve-compatible Python 3.10 not found: $resolvePy"
}
$finishedWatcherArgs = @('scripts/finished_review_watcher.py', '--interval', '5')
Start-Process -FilePath $resolvePy `
    -ArgumentList $finishedWatcherArgs `
    -WorkingDirectory $repo `
    -RedirectStandardOutput (Join-Path $logDir 'finished-review-watcher.out.log') `
    -RedirectStandardError (Join-Path $logDir 'finished-review-watcher.err.log') `
    -WindowStyle Hidden `
    -NoNewWindow:$false
