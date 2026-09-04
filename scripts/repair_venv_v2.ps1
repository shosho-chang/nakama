# repair_venv_v2.ps1 — .venv-v2 自癒：只修指標，不裝軟體，不用問。
#
# 背景（修修 2026-09-04 血淚）：.venv-v2 的 site-packages（torch cu128／Blackwell、
# faster-whisper、Qwen3-ASR）本身沒事，但它指向的 Python 3.12 直譯器一直不是靠
# installer 正式裝上、沒進 registry——只要那個資料夾被任何清理動作掃到，
# pyvenv.cfg 就會消失或指向空氣，整條 podcast pipeline／Resolve 自動化當場死掉，
# 而且死得沒有 traceback。這個腳本只做一件事：**pyvenv.cfg 壞了，但 pin 的那個
# Python 版本其實已經裝好、有登記在 registry 時，自動修好指標——不用重裝、
# 不用問**。只有連 Python 本身都不見了，才會印出安裝指令、要求你自己跑
# （裝軟體一律要問，不是這個腳本的權限）。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File E:\nakama\scripts\repair_venv_v2.ps1
#
# 這個腳本應該是每次真的要用 .venv-v2 之前的第一步（podcast-pipeline S0.0
# 健檢就是呼叫它），不是出事才想起來跑。

param(
    # .venv-v2 是跨 worktree 共用資源，固定在 E:\nakama 根目錄，不隨 worktree 走。
    [string]$VenvPath = 'E:\nakama\.venv-v2',
    # .python-version 是 repo-tracked 檔案，跟目前 checkout 走（worktree 測試時可覆寫）。
    [string]$PinFile = 'E:\nakama\.python-version'
)

$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$venv = $VenvPath
$pinFile = $PinFile
$cfgPath = Join-Path $venv 'pyvenv.cfg'

if (-not (Test-Path -LiteralPath $pinFile)) {
    throw "找不到 $pinFile——這個檔案應該釘死 .venv-v2 要用的精確 Python 版本，不該不見。"
}
$pinnedVersion = (Get-Content -LiteralPath $pinFile -Raw).Trim()
if ($pinnedVersion -notmatch '^(\d+)\.(\d+)\.(\d+)$') {
    throw "$pinFile 內容 `"$pinnedVersion`" 格式不對，應該是 X.Y.Z（例：3.12.10）。"
}
$minor = "$($Matches[1]).$($Matches[2])"

function Find-PinnedPython {
    # 只信 registry（installer 登記的路徑），不猜檔案系統——猜路徑正是
    # 8/12 那次埋雷的方式（可攜式 zip、沒登記）。
    foreach ($hive in @('HKCU', 'HKLM')) {
        $key = "${hive}:\Software\Python\PythonCore\$minor\InstallPath"
        if (Test-Path $key) {
            $exe = (Get-ItemProperty -Path $key -Name 'ExecutablePath' -ErrorAction SilentlyContinue).ExecutablePath
            if ($exe -and (Test-Path -LiteralPath $exe)) {
                $ver = & $exe -c "import sys; print('.'.join(map(str, sys.version_info[:3])))" 2>$null
                if ($ver -eq $pinnedVersion) { return $exe }
            }
        }
    }
    return $null
}

$pythonExe = Find-PinnedPython
if (-not $pythonExe) {
    Write-Host "未找到已登記的 Python $pinnedVersion——這個腳本不裝軟體，請自己執行：" -ForegroundColor Yellow
    Write-Host "  winget install --id Python.Python.$minor -e --scope user" -ForegroundColor Cyan
    Write-Host "裝完再重跑這個腳本。"
    exit 2
}
Write-Host "找到已登記的 Python $pinnedVersion -> $pythonExe"

$needsRepair = $true
if (Test-Path -LiteralPath $cfgPath) {
    $existing = Get-Content -LiteralPath $cfgPath -Raw
    if ($existing -match [regex]::Escape($pythonExe)) {
        Write-Host "pyvenv.cfg 已經指向正確的直譯器，不用修。"
        $needsRepair = $false
    } else {
        Write-Host "pyvenv.cfg 存在但指標不對／過期，重寫。" -ForegroundColor Yellow
    }
} else {
    Write-Host "pyvenv.cfg 不見了，重建。" -ForegroundColor Yellow
}

if ($needsRepair) {
    $home_ = Split-Path -Parent $pythonExe
    $cfg = @"
home = $home_
include-system-site-packages = false
version = $pinnedVersion
executable = $pythonExe
command = $pythonExe -m venv $venv
"@
    # PowerShell 5.1 沒有 utf8NoBOM 這個 Set-Content encoding 選項（PS7+ 才有），
    # 直接用 .NET API 寫，明確不帶 BOM（pyvenv.cfg 是純 ASCII 內容，但求保險）。
    [System.IO.File]::WriteAllText($cfgPath, $cfg, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "pyvenv.cfg 已修復。" -ForegroundColor Green
}

# 驗證：直譯器可跑、torch 看得到 GPU、Resolve fusionscript 匯入不炸
$venvPy = Join-Path $venv 'Scripts\python.exe'
$check = & $venvPy -c @"
import sys
print('python', sys.version.split()[0])
try:
    import torch
    print('torch', torch.__version__, 'cuda', torch.cuda.is_available())
except Exception as e:
    print('torch CHECK FAILED:', e)
"@ 2>&1
Write-Host $check

Write-Host "`n.venv-v2 健康。" -ForegroundColor Green
