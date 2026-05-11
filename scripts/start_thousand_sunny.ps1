# Start Thousand Sunny FastAPI server (covers Reader, KB UI, Bridge UI).
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
