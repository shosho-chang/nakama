# Start Thousand Sunny FastAPI server (covers Reader, KB UI, Bridge UI)
# 以及 packaging 封面 render watcher（修修 2026-08-14：gate 存配方後自動出圖）。
# Used by Windows Task Scheduler "Nakama-ThousandSunny" on logon.
# Logs to E:\nakama\logs\thousand-sunny.log (append).
#
# Manual run: powershell -ExecutionPolicy Bypass -File E:\nakama\scripts\start_thousand_sunny.ps1
# Stop:      Stop-ScheduledTask -TaskName 'Nakama-ThousandSunny'  (or taskkill /F /IM python.exe)

$repo = 'E:\nakama'
$venvPy = Join-Path $repo '.venv\Scripts\python.exe'
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

# --- packaging render watcher -------------------------------------------------
# 修修在 gate 上按「存配方」→ approval.json 多一份 render_request；render 需要
# Chrome/hyperframes/字型，只能在桌機跑（ADR-054 D11），所以這支跟 Bridge 一起
# 開機起來盯著。同一份配方只出一次圖（時間戳比對），失敗寫 log 不重試。
$watcherArgs = @('scripts/render_watcher.py', '--interval', '5')
Start-Process -FilePath $venvPy `
    -ArgumentList $watcherArgs `
    -WorkingDirectory $repo `
    -RedirectStandardOutput (Join-Path $logDir 'render-watcher.out.log') `
    -RedirectStandardError (Join-Path $logDir 'render-watcher.err.log') `
    -WindowStyle Hidden `
    -NoNewWindow:$false
