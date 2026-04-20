---
name: Slack Multi-Bot Gateway 架構
description: 每個 agent 一個獨立 Slack app（名字 / avatar / @mention），共用 python process，token 統一 <AGENT>_SLACK_*
type: project
---
# Slack Multi-Bot Architecture（2026-04-20 起）

## 架構事實

- Slack workspace 端：**每個 agent = 一個獨立 Slack app**（不是 persona）
- 後端：單一 python process（`gateway/bot.py`），每個 bot 一條 `SocketModeHandler` daemon thread
- Token 命名：`<AGENT>_SLACK_BOT_TOKEN` + `<AGENT>_SLACK_APP_TOKEN`（包含 Nami，無前綴的舊 `SLACK_BOT_TOKEN` 已廢）
- Mention 路由：訊息到哪個 bot = 該 agent 被 mention（不再 keyword routing 決定 agent），intent 分類仍走 `gateway/router.py` 供 Nami `create_project` 等 flow 使用

## 進度表（2026-04-20）

| Agent | Slack app | .env token | Handler | PR |
|---|---|---|---|---|
| Nami | ✅ | `NAMI_SLACK_*` | ✅ | 已上線 |
| Sanji | ✅ | `SANJI_SLACK_*` | ✅（既有，改 token 鎖 agent） | PR #55 |
| Zoro | ⬜ | ⬜ | ⬜ | 步驟 5 blocker |
| Brook | ⬜ | ⬜ | ⬜ | 之後 |
| Robin | ⬜ | ⬜ | ⬜ | 之後（Robin 本機跑，Slack 端是通知用） |
| Chopper | ⬜ | ⬜ | ⬜ | 平台未定 |

## 關鍵檔案

- **Runbook**：[docs/runbooks/add-agent-slack-bot.md](../../docs/runbooks/add-agent-slack-bot.md) — Phase 1 修修手動（Slack app 註冊）、Phase 2 Claude code、Phase 3 部署
- **實作**：[gateway/bot.py](../../gateway/bot.py) — `_discover_bots()` 掃 env、`_create_bot_app()` 綁 agent
- **關閉決策**：`SLACK_NAMI_SIGNING_SECRET` 是 HTTP webhook 驗證用，Socket Mode 不需要，可刪（code 不讀）
- **保留必要**：`SLACK_USER_ID_SHOSHO=U05F841H127` — bridge.py:68 讀它 scope agent memory 到修修本人

## VPS 部署記憶點（PR #55 merge 後）

1. SSH 上去改 `.env`：
   - `SLACK_BOT_TOKEN` → `NAMI_SLACK_BOT_TOKEN`
   - `SLACK_APP_TOKEN` → `NAMI_SLACK_APP_TOKEN`
   - 加 `SANJI_SLACK_BOT_TOKEN` + `SANJI_SLACK_APP_TOKEN`
2. `git pull && systemctl restart thousand-sunny`
3. `journalctl -u thousand-sunny -n 30`：應看「啟動 2 個 Slack bot：['nami', 'sanji']」
4. Smoke test：Slack workspace 打 `@Nami` 跟 `@Sanji`，各自回應（不同 avatar）
