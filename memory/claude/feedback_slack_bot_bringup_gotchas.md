---
name: Slack bot bring-up 診斷順序與常見坑
description: 新 agent Slack bot 沒反應時的診斷路徑 — 先分「Slack 端 vs code 端」再下手，避免亂改
type: feedback
originSessionId: 08f9ecf1-0d35-4311-a34c-34bca66b0731
---
新 agent 的 Slack bot 上線後「`@<agent>` 沒反應」時，按這個順序診斷，不要猜。

**Why**：Zoro bring-up 踩過一整串 —— VPS 沒 pull code / VPS 有 divergent local commit / `.env` token 缺 / Slack app Event Subscription 沒訂 `app_mention`；這些症狀都長得很像，但根因在不同層。沒先分層會改錯地方。

**How to apply**：新 agent Slack bot 第一次上線，或已知能跑的 bot 突然失靈時，按以下三層順序查，每層有明確 GO/NO 判準。

### Layer 1 — Gateway 端有認得 bot 嗎？

```bash
journalctl -u nakama-gateway -n 20 --no-pager | grep "啟動\|Socket"
```

- 看到 `啟動 N 個 Slack bot：[..., '<agent>', ...]` + `[<agent>] Socket Mode connection started` → Layer 1 PASS，往 Layer 2
- 沒看到 agent 名 → **code / 部署端問題**：
  - `git log --oneline -3` 確認 VPS 在最新 commit（新 handler 已 pulled）
  - `grep "^<AGENT>_SLACK_" .env` 確認兩個 token 都有
  - `gateway/handlers/__init__.py` registry 有註冊該 agent handler
  - `_discover_bots()` 的 log 會 warning「token 齊了但沒註冊 handler，skip」/「BOT_TOKEN 有但 APP_TOKEN 缺，skip」

### Layer 2 — Slack 有把 mention 送過來嗎？

```bash
journalctl -u nakama-gateway --since "10 minutes ago" --no-pager | grep -E "<agent>|Mention"
```

- 有 `[<agent>] Mention in ...` → Layer 2 PASS，往 Layer 3
- 完全沒 Mention event → **Slack app 端問題**（最常見三個）：
  1. **Event Subscriptions 沒訂 `app_mention`**（頭號坑 — Zoro 踩過）：Slack app settings → Event Subscriptions → Enable ON + Subscribe to bot events 加 `app_mention` / `message.im` / `message.channels`
  2. **加 scope 後沒 Reinstall to Workspace**：OAuth & Permissions 頁頂有黃色 banner 就是要按
  3. **Bot 沒加進頻道**：`/invite @<agent>` 到該頻道（DM 不受此限，可以先用 DM 驗 bot 本身）

  診斷輔助：`curl -H "Authorization: Bearer $<AGENT>_SLACK_BOT_TOKEN" https://slack.com/api/auth.test` 回 `ok:true` 只證明 token 有效，**不代表 event subscription / scope 齊**。

### Layer 3 — Handler 有回應嗎？

Mention event 進來了但用戶沒收到回覆。看 exception：

```bash
journalctl -u nakama-gateway --since "10 minutes ago" --no-pager | grep -iE "error|exception|traceback"
```

常見原因：
- `MODEL_<AGENT>` env 設了但 provider 無效（`shared/llm.py` fail-fast guard 會 raise）
- handler 某個路徑 raise 穿出（沒 catch → 訊息掉）
- cost DB 寫失敗拖慢回應

### 反例（不要先動 code）

- 「code 沒變，`@<agent>` 不回」幾乎都是 Layer 2（Slack app 設定）或 Layer 1（部署狀態），不要先去看 `gateway/handlers/<agent>.py`
- 「已知能跑的 Nami/Sanji 都能回，只有新 agent 不能」= **幾乎 100% 是 Slack app 端**，因為 gateway code 是共用的

### Runbook

完整上線流程見 [docs/runbooks/add-agent-slack-bot.md](../../docs/runbooks/add-agent-slack-bot.md)，troubleshooting section 同步有。
