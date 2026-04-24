# Runbook：幫 Agent 開一個獨立 Slack Bot

每個 Nakama agent（Nami / Sanji / Zoro / Brook / Robin / Chopper）在 Slack 是獨立 bot —  有自己的名字、avatar、`@mention`、個性 prompt。這份 runbook 記錄從頭新增一個 bot 的完整流程。

## 背景

- Slack workspace 端每個 agent 都是一個獨立 **Slack app**（不是 persona）
- 所有 bot 共用一個 `gateway/bot.py` python process（每個 bot 跑一條獨立的 Socket Mode connection）
- token 走 `.env`，統一格式 `<AGENT>_SLACK_BOT_TOKEN` + `<AGENT>_SLACK_APP_TOKEN`（Nami 也一樣 `NAMI_SLACK_*`，無前綴的舊 `SLACK_BOT_TOKEN` 已廢）
- 每個 agent 的 slash command 在自己的 Slack app 設（早期全掛 Nami bot 的逐步搬遷）

## Phase 1 — Slack 側（修修手動）

### 1. 到 https://api.slack.com/apps 建 New App

- **From scratch**
- App Name：`Nakama <Agent>`（例如 `Nakama Sanji`）
- Pick workspace：修修的 Slack workspace

### 2. Socket Mode 開啟

- 左側 Settings → **Socket Mode** → Enable
- 產生 App-Level Token：
  - Name: `socket-connection`
  - Scope: `connections:write`
  - Copy：`xapp-...`（**這是 APP_TOKEN**）

### 3. OAuth & Permissions → Bot Token Scopes 加：

| Scope | 用途 |
|---|---|
| `app_mentions:read` | 接 `@mention` 事件 |
| `chat:write` | 發訊息 |
| `im:history` | DM 多輪對話 |
| `im:read` | DM 基本 |
| `im:write` | DM 發訊息 |
| `channels:history` | 讀頻道訊息（brainstorm / event_bridge 用） |
| `channels:read` | 讀頻道列表 |
| `groups:history` | 讀私密頻道（可選） |
| `reactions:read` | brainstorm 停止條件看 `🛑` reaction（之後才用） |

### 4. Event Subscriptions

- Enable Events
- Subscribe to bot events：
  - `app_mention`
  - `message.im`（DM 接續多輪）
  - `message.channels`（brainstorm 時聽 mention 推題）

### 5. Slash Commands（可選）

若該 agent 要有 slash command（如 `/sanji`），在 Slash Commands 頁加：
- Command: `/<agent>`
- Description: 一句話描述
- Usage Hint: 填個範例

### 6. App Display

- Display Information → 填：
  - Name（bot 顯示名）
  - Short description
  - Long description
  - App icon（每個 agent 該有自己的 avatar，最遲 Phase 3 補上）
  - Background color

### 7. Install to Workspace

- Settings → Install App → Install to Workspace → Allow
- 複製 **Bot User OAuth Token**：`xoxb-...`（**這是 BOT_TOKEN**）

### 8. 交付 token 給 Claude

**不要在 Slack chat / Claude 對話框直接貼 token**。改在本機：

```bash
# f:/nakama/.env 加兩行（以 Sanji 為例）
SANJI_SLACK_BOT_TOKEN=xoxb-...
SANJI_SLACK_APP_TOKEN=xapp-...
```

VPS 端也同步加（`ssh nakama-vps` → 編輯 `/home/nakama/.env`）。

## Phase 2 — Code 側（Claude 做）

收到 token 設好 `.env` 後請 Claude：

1. **`gateway/bot.py` multi-bot registry**（已完成架構）
   - `_discover_bots()` 掃 `.env` 找所有有設 token 的 agent
   - 每個 bot 一條 `SocketModeHandler` 在獨立 daemon thread 跑
   - mention 事件到該 bot = 該 agent 被 mention（不再 keyword routing 決定 agent）
   - intent 分類仍走 router（Nami `create_project` 等 flow 需要）

2. **`gateway/router.py` 移除 keyword routing**（by agent name）
   - 改為：每個 bot 的 `app_mention` 直接 dispatch 到對應 handler
   - slash command routing 保留（向後相容）

3. **`gateway/handlers/<agent>.py` 寫 handler**
   - 繼承 `BaseHandler`，設 `agent_name` + `supported_intents`
   - `handle(intent, text, user_id)` 實作
   - 需要 multi-turn → 加 `continue_flow`

4. **測試**
   - `tests/test_gateway_<agent>.py` — mock SocketModeClient 驗 handler 路徑

## Phase 3 — 部署

```bash
# 本機
python -m gateway  # 確認新 bot 有連上（log 會顯示 "<Agent> Socket started"）

# VPS
ssh nakama-vps
cd /home/nakama && git pull
# 確認 .env 有新 agent 的兩組 token
grep "<AGENT>_SLACK" .env
systemctl restart thousand-sunny  # 目前 gateway 跑在這個 service 裡；未來拆獨立 service
systemctl status thousand-sunny --no-pager | head
```

**Smoke test**：
1. Slack workspace 打 `@<Agent> 你好`
2. 該 bot 應該以自己的 avatar / name 回覆
3. `https://nakama.shosho.tw/bridge/cost` 查最新一筆 → `agent=<agent>` 對上

## Troubleshooting — `@<Agent>` 沒反應

### 診斷流程（先看 log 判斷是 Slack 端還是 code 端）

```bash
# 1. Gateway 認得 bot 嗎？
journalctl -u nakama-gateway -n 20 --no-pager | grep "啟動\|Socket"
# 預期：「啟動 N 個 Slack bot：[..., '<agent>', ...]」+ 「[<agent>] Socket Mode connection started」
# 沒看到 agent 名字 → code 端問題（handler 沒註冊 or .env token 沒設）
# 看到 Socket started 但沒 Mention event → 往下

# 2. Slack 有把 mention 送過來嗎？
journalctl -u nakama-gateway --since "10 minutes ago" --no-pager | grep -E "<agent>|Mention"
# 有看到 `[<agent>] Mention in ...` → code 端處理問題，往 handler 查
# 完全沒 Mention event → Slack app 端問題（見下）

# 3. Bot token 有效嗎？
set -a && source .env && set +a
curl -s -H "Authorization: Bearer $<AGENT>_SLACK_BOT_TOKEN" https://slack.com/api/auth.test
# 預期 {"ok":true,"user":"<agent>","user_id":"U...","bot_id":"B..."}
# ok:false 或 missing_scope → 去 OAuth & Permissions 檢查 + Reinstall
```

### 最常見的 Slack 端 gotcha

1. **Event Subscriptions 沒訂閱 `app_mention`**（頭號坑）
   - Slack app settings → Event Subscriptions → Enable Events ON
   - Subscribe to bot events 必須有 `app_mention`、`message.im`、`message.channels`
   - 漏掉 `app_mention` 的症狀：`auth.test` 成功、Socket Mode 連得上、但 gateway log 永遠收不到該 bot 的 Mention event

2. **加新 scope 後忘記 Reinstall to Workspace**
   - OAuth & Permissions 加了 scope，頁頂會出現「You've changed the scopes...」黃色 banner
   - 必須按 Reinstall to Workspace，舊 token 雖然還能 `auth.test` 但不會有新 scope
   - 症狀：部分功能失靈（例如 DM 能回但頻道 mention 沒反應）

3. **Bot 沒被加入頻道**
   - 頻道 `@<agent>` 需要 bot 是該頻道成員（`/invite @<agent>`）
   - DM 不受此限，DM 有 scope 就能用。可用 DM 驗證 bot 本身是否正常

### 確認是 code 端問題後（log 有看到 Mention event 但 bot 沒回覆）

- `gateway/handlers/<agent>.py` 有 `raise` 穿出嗎？`journalctl` 看 exception
- `MODEL_<AGENT>` 設了但 provider 無效？看 `shared/llm.py` fail-fast guard 的 error
- `set_current_agent("<agent>")` 有被呼叫嗎？沒的話 cost DB 會記到別 agent

## 當前進度表

| Agent | Slack app | .env token | Handler | 備註 |
|---|---|---|---|---|
| Nami | ✅ | `NAMI_SLACK_*` | ✅ `gateway/handlers/nami.py` | 11 tools |
| Sanji | ✅ | `SANJI_SLACK_*` | ✅ `gateway/handlers/sanji.py` | P1 brainstorm 上線 |
| Zoro | ✅ | `ZORO_SLACK_*` | ✅ `gateway/handlers/zoro.py` | P2 scout 在 Slice B/C |
| Brook | ⬜ | ⬜ | ⬜ | 之後 |
| Robin | ⬜ | ⬜ | ⬜ | 之後（Robin 本機跑，Slack 端是通知用） |
| Chopper | ⬜ | ⬜ | ⬜ | 平台未定 |

> 每加一個 agent bot，更新這張表。
