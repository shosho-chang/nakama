param(
    [int]$Port = 8128
)

$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$nakamaRoot = (Resolve-Path (Join-Path $repo '..\..')).Path
$python = Join-Path $nakamaRoot '.venv-v2\Scripts\python.exe'
$envFile = Join-Path $nakamaRoot '.env'
$logDir = Join-Path $repo '.cache\bridge-server'
$stdoutLog = Join-Path $logDir "bridge-$Port.out.log"
$stderrLog = Join-Path $logDir "bridge-$Port.err.log"
$bridgeUrl = "http://127.0.0.1:$Port"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Bridge Python runtime not found: $python"
}
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw "Bridge environment file not found: $envFile"
}
if (-not (Test-Path -LiteralPath $logDir -PathType Container)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

function Test-BridgeServer {
    param([string]$BaseUrl)

    try {
        $handler = [System.Net.Http.HttpClientHandler]::new()
        $handler.AllowAutoRedirect = $false
        $client = [System.Net.Http.HttpClient]::new($handler)
        $client.Timeout = [TimeSpan]::FromSeconds(2)
        $response = $client.GetAsync("$BaseUrl/").GetAwaiter().GetResult()
        $location = $response.Headers.Location
        return (
            [int]$response.StatusCode -eq 302 -and
            $null -ne $location -and
            $location.OriginalString -eq '/bridge/weekly'
        )
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $client) { $client.Dispose() }
        if ($null -ne $handler) { $handler.Dispose() }
    }
}

if (Test-BridgeServer -BaseUrl $bridgeUrl) {
    Write-Output "Bridge is already running: $bridgeUrl"
    exit 0
}

$arguments = @(
    '-m', 'uvicorn',
    'thousand_sunny.app:app',
    '--host', '127.0.0.1',
    '--port', $Port.ToString(),
    '--env-file', $envFile
)

$env:PYTHONUTF8 = '1'
$process = Start-Process -FilePath $python `
    -ArgumentList $arguments `
    -WorkingDirectory $repo `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -WindowStyle Hidden `
    -PassThru

for ($attempt = 0; $attempt -lt 40; $attempt++) {
    if ($process.HasExited) {
        $tail = if (Test-Path -LiteralPath $stderrLog) {
            (Get-Content -LiteralPath $stderrLog -Tail 20) -join [Environment]::NewLine
        } else {
            'No stderr log was created.'
        }
        throw "Bridge exited during startup (code $($process.ExitCode)).$([Environment]::NewLine)$tail"
    }
    if (Test-BridgeServer -BaseUrl $bridgeUrl) {
        Write-Output "Bridge started: $bridgeUrl (PID $($process.Id))"
        Write-Output "Logs: $stdoutLog ; $stderrLog"
        exit 0
    }
    Start-Sleep -Milliseconds 250
}

throw "Bridge did not become ready within 10 seconds. Check: $stderrLog"
