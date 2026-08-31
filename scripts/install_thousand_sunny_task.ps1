# 建立／更新 Windows 工作排程「Nakama-ThousandSunny」——登入時自動起本機 Bridge。
#
#   powershell -ExecutionPolicy Bypass -File E:\nakama\scripts\install_thousand_sunny_task.ps1
#
# 為什麼要有這支：`start_thousand_sunny.ps1` 的註解從一開始就寫著「Used by Windows
# Task Scheduler "Nakama-ThousandSunny" on logon」，但那個工作**是被停用的**——
# 2026-08-31 實查 State=Disabled、最後一次執行停在 08/27。排程在某個人的機器上手動
# 點出來，被停用也沒有任何紀錄說明為什麼。把建立步驟寫成腳本，它才可稽核、可重建。
#
# （更正：本檔第一版宣稱「那個工作根本不存在」。那是查錯了——在 Git Bash 裡執行
# `schtasks /query` 會被 MSYS 路徑轉換成 `C:/Program Files/Git/query`，指令根本沒跑，
# 把「沒有輸出」當成「沒有這個工作」。查 Windows 排程要用 PowerShell 的
# `Get-ScheduledTask`。）
#
# 這支不需要系統管理員權限：工作註冊在目前使用者底下，登入後才跑。這是刻意的——
# Bridge 要讀 E:\ 與 G:\ 的檔案，跑成 SYSTEM 服務反而看不到使用者的磁碟機。
#
# 移除：Unregister-ScheduledTask -TaskName 'Nakama-ThousandSunny' -Confirm:$false

$ErrorActionPreference = 'Stop'

$taskName = 'Nakama-ThousandSunny'
$repo = 'E:\nakama'
$launcher = Join-Path $repo 'scripts\start_thousand_sunny.ps1'

if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "找不到啟動腳本: $launcher"
}

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`"" `
    -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

# 開機當下磁碟機與網路都還在初始化，延遲一分鐘再起，省掉一輪必然的失敗重試。
$trigger.Delay = 'PT1M'

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description 'Nakama Thousand Sunny（Bridge / Reader / KB UI）＋ packaging watcher，登入時自動啟動於 127.0.0.1:8000' `
    -Force | Out-Null

# 既有的工作可能是停用狀態（實測就是 Disabled），-Force 重註冊未必會把它打開。
# 明確啟用並驗證——installer 跑完卻留下一個 disabled 的工作，是最難察覺的失敗。
Enable-ScheduledTask -TaskName $taskName | Out-Null
$state = (Get-ScheduledTask -TaskName $taskName).State
if ($state -eq 'Disabled') {
    throw "工作註冊完仍是 Disabled：$taskName（手動啟用：Enable-ScheduledTask -TaskName '$taskName')"
}

Write-Output "已註冊工作：$taskName（State: $state）"
Write-Output "  動作：powershell -File $launcher"
Write-Output "  觸發：登入後 1 分鐘"
Write-Output ""
Write-Output "現在就跑一次：Start-ScheduledTask -TaskName '$taskName'"
Write-Output "查狀態：      Get-ScheduledTaskInfo -TaskName '$taskName'"
Write-Output "看日誌：      Get-Content E:\nakama\logs\thousand-sunny.err.log -Tail 30"
