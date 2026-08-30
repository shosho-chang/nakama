# Start Thousand Sunny FastAPI server (covers Reader, KB UI, Bridge UI)
# 以及 packaging 封面 render watcher（修修 2026-08-14：gate 存配方後自動出圖）。
# Used by Windows Task Scheduler "Nakama-ThousandSunny" on logon.
# 建立排程（一次就好）：scripts\install_thousand_sunny_task.ps1
# Logs to E:\nakama\logs\thousand-sunny.log (append).
#
# Manual run: powershell -ExecutionPolicy Bypass -File E:\nakama\scripts\start_thousand_sunny.ps1
# Stop:      Stop-ScheduledTask -TaskName 'Nakama-ThousandSunny'  (or taskkill /F /IM python.exe)

$ErrorActionPreference = 'Stop'

$repo = 'E:\nakama'
# app 直譯器：**不要指 .venv-v2**。2026-08-31 實查它沒有 pyvenv.cfg（壞掉的 venv），
# 而上一次真的跑起來的 .venv 是 Python 3.10——程式現在用 datetime.UTC，要 3.11+。
# 兩個都起不來；舊版腳本的 Start-Process 失敗又不會讓工作變成失敗狀態，
# 於是「開機自動啟動」看起來有做、實際上 8000 一直是空的。
$appPy = if ($env:NAKAMA_APP_PYTHON) { $env:NAKAMA_APP_PYTHON } else { 'C:\Python314\python.exe' }
# finished-review watcher 必須留在 Resolve 的 Python 3.10（fusionscript ABI），不可換。
$resolvePy = 'C:\Users\Shosho\AppData\Local\Programs\Python\Python310\python.exe'
$logDir = Join-Path $repo 'logs'
$logFile = Join-Path $logDir 'thousand-sunny.log'

if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }

Set-Location $repo

# 起之前先證明這個直譯器真的載得動 app——壞掉的 venv 要在這裡大聲失敗，
# 不要等到使用者發現 8000 沒東西。
& $appPy -c "import sys; sys.path.insert(0, r'$repo'); import thousand_sunny.app" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "app 直譯器載不動 thousand_sunny.app: $appPy（設 NAKAMA_APP_PYTHON 可覆寫）"
}

# 已經有人在 8000 就不要再起一份（開機自動啟動＋手動執行都會走到這裡）。
$listening = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    Write-Output "Thousand Sunny 已在 127.0.0.1:8000 執行（PID $($listening[0].OwningProcess)）"
    exit 0
}

# Use Start-Process so uvicorn stderr is written to file directly, not interpreted as
# PowerShell error (which would abort the wrapper). RedirectStandardOutput + RedirectStandardError
# require separate paths, so use stdout for both via cmd-style merge.
$args = @(
    '-m', 'uvicorn',
    'thousand_sunny.app:app',
    '--host', '127.0.0.1',
    '--port', '8000'
)
Start-Process -FilePath $appPy `
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
Start-Process -FilePath $appPy `
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
