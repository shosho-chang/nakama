# Publish Outcome Reconciler 操作手冊

## 目的與安全邊界

Outcome Reconciler 只確認已 Native Arm、Campaign Anchor 已到點的 YouTube／Facebook
Release Target 結果。它不排程、不核准、不發布、不重傳、不重試、不建立內容，也不處理
Instagram 或 Carousel。預設是 one-shot dry-run；真實 `--execute` 必須由操作者在桌機前景
監督，不可安裝成 Task Scheduler、service 或 unattended autostart。

平台證據採 fail closed：YouTube 必須明確 public；Facebook 必須同時有 top-level
`published=true` 與安全 HTTP(S) permalink。processing、scheduled、private、phase complete、
not-found、auth／transport error 或 contradictory response 都不會被改成 published。

YouTube access token 本來就是短效 credential。Execute 初始化 observer 時會與 Uploader
共用受控 loader：token 尚有效時直接使用；access token 已到期且 refresh credential 仍有效時，
只 refresh 一次並 atomic 保存後繼續，不需要人工重新 OAuth。這次 auth exchange 不是發布
操作，也不增加每個候選的一次 outcome GET。只有下列情況才重跑
`python scripts/youtube_auth.py`：token malformed、缺 refresh credential，或 Google 明確拒絕
refresh（`invalid_grant`，例如授權遭撤銷）。短暫 refresh transport error 或 token persistence
error 先保留最後可解析 token，稍後重跑 worker；不要因此撤銷或重建 OAuth consent。

## 1. 先跑本機安全測試

下列測試使用 temporary SQLite 與 fake observer；不讀 production state、不載入真實 client、
不呼叫 YouTube／Meta：

```powershell
Set-Location E:\nakama
E:\nakama\.venv-v2\Scripts\python.exe -m pytest tests\scripts\test_publish_reconcile.py -q
```

確認所有測試為 green。`test_execute_no_work_records_success_without_initializing_observers`
驗證 isolated no-work execute；dry-run regression 會用 raising builders 證明 network、heartbeat、
Target mutation 都是 0。

## 2. 只讀 dry-run

在 repo 根目錄執行：

```powershell
Set-Location E:\nakama
E:\nakama\.venv-v2\Scripts\python.exe scripts\publish_reconcile.py --once --dry-run
```

預設即 dry-run，`--dry-run` 保留是為了讓意圖清楚。輸出只包含 episode、cut、platform、
Target ID、Campaign Anchor、local status 與 evidence category；不包含 video ID、caption、
checkpoint、signed URL 或 credential。dry-run 不初始化 observer、不寫 heartbeat、不改 Target。

若只檢查一支 Release，episode 與 cut 必須一起提供，而且是 exact match：

```powershell
Set-Location E:\nakama
E:\nakama\.venv-v2\Scripts\python.exe scripts\publish_reconcile.py --once --dry-run --episode "20260721 鄭國威" --cut "partner-haoge-S01"
```

不存在、只給一個參數或空字串都會 fail closed。

## 3. 判讀 dry-run

- `candidates`：已到 Campaign Anchor、仍是 `uploaded`、具唯一 platform identity，執行時會各做一次 GET。
- `future_anchor`：尚未到點，本輪不觀察。
- `missing_campaign_anchor`／`divergent_campaign_anchor`：不猜日期、不呼叫平台。
- `missing_video_identity`：沒有穩定 receipt，禁止觀察或 replacement upload。
- `duplicate_platform_identity`：同一平台 identity 綁到多個 Target，需先人工釐清。

Calendar 的「等待公開確認」只是 DB projection，不是 published。Outcome Reconciler health 與
Instagram Due Dispatcher health 是兩個獨立 worker，不能互相替代。預設 watch cadence 為
5 分鐘；Calendar 以 15 分鐘為 stale threshold，容許兩次短暫 jitter，第三個 interval 仍未見
heartbeat 才警告。

## 4. 受監督的精確 execute

只有在 dry-run 的 exact scope 正確、操作者留在桌機前景、並已確認 credential 指向正式的
Podcast YouTube／Facebook Page 身分時，才執行：

```powershell
Set-Location E:\nakama
E:\nakama\.venv-v2\Scripts\python.exe scripts\publish_reconcile.py --once --execute --episode "20260721 鄭國威" --cut "partner-haoge-S01"
```

Execute 只初始化候選所需的平台 observer；每個候選只做一次 GET。明確結果用
`uploaded + same video_id + same updated_at` CAS 寫回。另一個流程已先更新時會顯示
`stale_snapshot` 並保留對方結果。任何 observer／DB uncertainty 都讓本輪回傳 nonzero 與
code-only diagnostic；全域 run 另寫 failing heartbeat，exact-scope 則不寫 global heartbeat。
Target 保持原狀，siblings 仍繼續；不會觸發 retry 或 upload。

若 YouTube observer setup 失敗，輸出只會顯示 `youtube_observer_setup_failed`，不會包含
token、client secret、token path 或 provider raw error。先依上方 credential 分類處理；不要
清除既有 Release Target 的 video ID，也不要用 `--force` 建立 replacement upload。

Exact-scope execute／watch 不寫 global heartbeat，避免只監看一支 Release 卻讓 Calendar 誤以為
所有 overdue Targets 都受監控；只有不帶 `--episode`／`--cut` 的全域 execute 能更新 Calendar
所讀的 `usopp-release-outcome-reconciler` health。

## 5. 前景 watch（只限有人監督）

需要短暫連續觀察時可在有人看守的 terminal 使用：

```powershell
Set-Location E:\nakama
E:\nakama\.venv-v2\Scripts\python.exe scripts\publish_reconcile.py --watch --execute --poll-seconds 300 --episode "20260721 鄭國威" --cut "partner-haoge-S01"
```

以 `Ctrl+C` 停止。離開桌機前必須停止；不要把這條命令加入永久服務或自動啟動。

## 6. 收尾檢查

1. 重新執行相同 exact dry-run，確認已終止的 Target 不再出現在 candidates。
2. 開啟 Publish Calendar，確認 Target 狀態與平台證據一致。
3. 查看 Outcome Reconciler 的 LAST RUN／LAST SUCCESS／FAILURE STREAK；不要用 Due Dispatcher
   的 heartbeat 代替。
4. `observation_error`、`unsafe_permalink`、`unknown`、`cas_error` 只做人工診斷；不得直接
   retry、reupload 或清除既有 video ID/checkpoint。
