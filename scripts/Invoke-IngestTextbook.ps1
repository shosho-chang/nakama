<#
.SYNOPSIS
    Run the ADR-020 S8 textbook ingest pipeline using Max Plan quota (zero API billing).

.DESCRIPTION
    Reads the OAuth access token from ~\.claude\.credentials.json, injects it as
    CLAUDE_CODE_OAUTH_TOKEN, strips ANTHROPIC_API_KEY from this process's env so
    the SDK can never accidentally bill the API, sets NAKAMA_REQUIRE_MAX_PLAN=1
    as a defense-in-depth lock (refused at get_client() if anything goes wrong),
    then dispatches the chosen book to scripts/run_s8_batch.py.

    The wrapper only mutates env vars in *this PowerShell process*; nothing
    persists to the user-level environment. Other shells continue to use
    ANTHROPIC_API_KEY for Robin / Franky / etc.

.PARAMETER Book
    Which book to ingest:
      - bse   -- Biochemistry for Sport and Exercise (MacLaren), 11 chapters
      - sn    -- Sport Nutrition (Jeukendrup) 4E, 17 chapters
      - acsm  -- ACSM's Guidelines for Exercise Testing and Prescription (12e), 12 chapters
      - all   -- bse,sn,acsm (default)

.PARAMETER DryRun
    Run walker + classifier only, no LLM calls, no vault writes.

.PARAMETER MaxChapters
    Cap chapters per book (for smoke testing). Default: unlimited.

.PARAMETER VaultPath
    Override vault root. Default: E:\Shosho LifeOS.

.EXAMPLE
    .\scripts\Invoke-IngestTextbook.ps1 -DryRun
    Smoke-test the walker on both books without spending Max Plan tokens.

.EXAMPLE
    .\scripts\Invoke-IngestTextbook.ps1 -Book bse -MaxChapters 2
    Re-ingest the first 2 BSE chapters into KB\Wiki.staging using Max Plan.

.EXAMPLE
    .\scripts\Invoke-IngestTextbook.ps1
    Full re-ingest of all books (BSE 11ch + SN 17ch + ACSM 12ch).

.NOTES
    OAuth handling: this wrapper validates ~\.claude\.credentials.json (subscription
    tier + expiry) but does NOT pin CLAUDE_CODE_OAUTH_TOKEN into the subprocess env.
    Long batches (>=1h) can outlive a single token; the subprocess would otherwise
    keep using the stale value and silently 401 mid-batch. By leaving the env unset,
    the claude CLI subprocess reads credentials.json fresh per call and picks up the
    refreshToken-driven rotation automatically. See
    memory/claude/feedback_oauth_env_pinning_long_batch.md for the 2026-05-16
    incident that motivated this.
#>

[CmdletBinding()]
param(
    [ValidateSet('bse', 'sn', 'acsm', 'all')]
    [string]$Book = 'all',

    [switch]$DryRun,

    [int]$MaxChapters = 0,

    [string]$VaultPath = 'E:\Shosho LifeOS'
)

$ErrorActionPreference = 'Stop'

# --- 1. Resolve repo root from script location ----------------------------
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Write-Host "Repo root:  $RepoRoot" -ForegroundColor DarkGray

# --- 2. Read OAuth token from Claude Code credentials ---------------------
$CredsPath = Join-Path $HOME '.claude\.credentials.json'
if (-not (Test-Path $CredsPath)) {
    throw "Claude Code credentials not found at $CredsPath. Run 'claude' once to authenticate, then retry."
}
$Creds = Get-Content $CredsPath -Raw | ConvertFrom-Json
$OAuth = $Creds.claudeAiOauth.accessToken
if (-not $OAuth) {
    throw "claudeAiOauth.accessToken missing from $CredsPath. Re-authenticate with 'claude /login'."
}

$ExpiresAt = $Creds.claudeAiOauth.expiresAt
if ($ExpiresAt) {
    # JS epoch in ms.
    $ExpiryDate = (Get-Date '1970-01-01').AddMilliseconds([double]$ExpiresAt)
    $HoursLeft = ($ExpiryDate - (Get-Date)).TotalHours
    if ($HoursLeft -lt 0) {
        throw "OAuth token already expired (at $($ExpiryDate.ToString('u'))). Refresh with 'claude /login' before running."
    }
    if ($HoursLeft -lt 1) {
        Write-Host ("WARNING: OAuth token expires in {0:N1} hour(s) -- refresh soon with 'claude /login'." -f $HoursLeft) -ForegroundColor Yellow
    }
    Write-Host ("OAuth token: ...{0} (expires {1:yyyy-MM-dd HH:mm}, {2:N1} hours left)" -f $OAuth.Substring($OAuth.Length - 8), $ExpiryDate, $HoursLeft) -ForegroundColor DarkGray
}

$SubType = $Creds.claudeAiOauth.subscriptionType
$RateLimit = $Creds.claudeAiOauth.rateLimitTier
Write-Host "Subscription: $SubType  / Rate tier: $RateLimit" -ForegroundColor DarkGray

# --- 3. Configure env for this process only -------------------------------
# Process-scope env vars do NOT persist after the script exits.
#
# IMPORTANT: do NOT set $env:CLAUDE_CODE_OAUTH_TOKEN. The claude CLI subprocess
# inherits parent env, and pinning the OAuth token here would keep an expired
# value live across the batch even after credentials.json gets refreshed by
# another foreground session. Leaving it unset forces the subprocess to read
# credentials.json fresh per invocation. See the NOTES block above.
if ($env:CLAUDE_CODE_OAUTH_TOKEN) {
    Write-Host "Clearing CLAUDE_CODE_OAUTH_TOKEN from this process (subprocess reads credentials.json fresh)." -ForegroundColor Yellow
    Remove-Item Env:\CLAUDE_CODE_OAUTH_TOKEN
}
$env:NAKAMA_REQUIRE_MAX_PLAN = '1'           # Hard lock in anthropic_client
$env:VAULT_PATH = $VaultPath

# Strip API key so even if it leaked into this shell it can't be used.
if ($env:ANTHROPIC_API_KEY) {
    Write-Host "Clearing ANTHROPIC_API_KEY from this process (Max Plan lock active)." -ForegroundColor Yellow
    Remove-Item Env:\ANTHROPIC_API_KEY
}

Write-Host "Vault:       $env:VAULT_PATH"  -ForegroundColor DarkGray
Write-Host "Max Plan:    locked (NAKAMA_REQUIRE_MAX_PLAN=1)" -ForegroundColor Green

# --- 4. Locate python ------------------------------------------------------
$VenvPython = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    throw "Venv python not found at $VenvPython. Activate setup: 'python -m venv .venv && .venv\Scripts\pip install -r requirements.txt'."
}

# --- 5. Build python args --------------------------------------------------
$BooksArg = switch ($Book) {
    'all' { 'bse,sn,acsm' }
    default { $Book }
}

$pyArgs = @('-m', 'scripts.run_s8_batch', '--books', $BooksArg)
if ($DryRun)            { $pyArgs += '--dry-run' }
if ($MaxChapters -gt 0) { $pyArgs += @('--max-chapters', $MaxChapters) }

Write-Host ""
Write-Host "Launching: python $($pyArgs -join ' ')" -ForegroundColor Cyan
Write-Host "----------------------------------------------------------------"

Push-Location $RepoRoot
try {
    & $VenvPython @pyArgs
    $exit = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host "----------------------------------------------------------------"
if ($exit -eq 0) {
    Write-Host "Done. Review staging: $env:VAULT_PATH\KB\Wiki.staging\Sources\Books\" -ForegroundColor Green
} else {
    Write-Host "Exit code $exit -- see logs above and docs\runs\ for the report." -ForegroundColor Red
}
exit $exit
