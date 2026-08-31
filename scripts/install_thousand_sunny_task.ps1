# 建立／更新 Windows 工作排程「Nakama-ThousandSunny」——登入時自動起本機 Bridge。
#
#   powershell -ExecutionPolicy Bypass -File E:\nakama\scripts\install_thousand_sunny_task.ps1
#
# 為什麼要有這支：`start_thousand_sunny.ps1` 的註解從一開始就寫著「Used by Windows
# Task Scheduler "Nakama-ThousandSunny" on logon」，但 2026-08-31 實查那個工作**根本
# 不存在**。腳本在 repo 裡、排程在某個人的機器上手動點出來——只要沒點，或點過又被
# 清掉，就沒有人會發現。把建立步驟寫成腳本，它才可稽核、可重建。
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

Write-Output "已註冊工作：$taskName"
Write-Output "  動作：powershell -File $launcher"
Write-Output "  觸發：登入後 1 分鐘"
Write-Output ""
Write-Output "現在就跑一次：Start-ScheduledTask -TaskName '$taskName'"
Write-Output "查狀態：      Get-ScheduledTaskInfo -TaskName '$taskName'"
Write-Output "看日誌：      Get-Content E:\nakama\logs\thousand-sunny.err.log -Tail 30"
