---
name: GitHub Actions scheduled / probe workflow 陷阱
description: 寫 cron-driven GitHub Actions workflow（uptime probe、定時抓 API 等）踩過的坑
type: reference
originSessionId: 9bfe5264-7fe3-4bcf-8418-f9917c14cced
---
寫 `on: schedule` GitHub Actions workflow 時容易踩到的五個固定坑：

1. **Cron 強制 UTC，沒時區**
   - `cron: '*/5 * * * *'` 不吃 `TZ=...`，想「台北上班時段密集」要自己算 offset。
   - Shell 裡要印台北時間仍可 `TZ=Asia/Taipei date ...`，只有 trigger 本身是 UTC。

2. **Scheduled run 排程延遲 5-15 分鐘是常態**
   - 公有 runner 滿載時 `*/5 * * * *` 實際可能 10-20 分鐘才跑。
   - 不是 SLA。要 ≤1 min 檢查頻率得自建 runner 或換 tool。

3. **60 天不活躍自動停用 scheduled workflows**
   - Repo 沒 commit 60 天，GitHub 靜默停 `on: schedule`，要 `gh workflow enable` 才恢復。
   - 活躍 repo 不會踩，低活躍專案（個人 project、archive 用途）要注意。

4. **`workflow_dispatch` 的 boolean input 在 shell 裡是 string**
   - 宣告 `type: boolean` + `default: false`，`${{ inputs.flag }}` 吃進 env 變成 `"true"` / `"false"`。
   - Shell 比對要 `[ "$FLAG" = "true" ]`，不要用 `if [ "$FLAG" ]` （"false" 也是 truthy）。

5. **Slack bot 不能直接 post 到 user ID**
   - `chat.postMessage` 的 `channel` 欄位吃 DM channel ID（`D...`），不吃 user ID（`U...`）。
   - 要先 `conversations.open` with `users: <U...>`，拿回 `channel.id` 再 post。
   - 也可以 cache channel ID，但每次重開低成本（~50ms），workflow 裡直接開就行。

**workflow_dispatch simulate 按鈕的常見模式:**
加一個 `simulate_down: boolean` input，在 step 開頭判斷 `"$SIMULATE_DOWN" = "true"` 就強制 fail
（`exit 1` + 寫 `reason=...` 到 `$GITHUB_OUTPUT`），讓 failure 後續步驟能拿到假的 http_code / reason
走完真實路徑。不用分兩條 workflow。

**用過的範本:** `.github/workflows/external-probe.yml`（3 URL matrix + Slack DM on failure）
