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

## 進度表（2026-04-20 更新）

| Agent | Slack app | .env token | Handler | 狀態 |
|---|---|---|---|---|
| Nami | ✅ | `NAMI_SLACK_*` | ✅ | ✅ 已上線 |
| Sanji | ✅ | `SANJI_SLACK_*` | ✅ + memory 注入 | ✅ 已上線（2026-04-20 修好） |
| Zoro | ⬜ | ⬜ | ⬜ | 步驟 5 blocker |
| Brook | ⬜ | ⬜ | ⬜ | 之後 |
| Robin | ⬜ | ⬜ | ⬜ | 之後（Robin 本機跑，Slack 端是通知用） |
| Chopper | ⬜ | ⬜ | ⬜ | 平台未定 |

## 關鍵檔案

- **Runbook**：[docs/runbooks/add-agent-slack-bot.md](../../docs/runbooks/add-agent-slack-bot.md) — Phase 1 修修手動（Slack app 註冊）、Phase 2 Claude code、Phase 3 部署
- **實作**：[gateway/bot.py](../../gateway/bot.py) — `_discover_bots()` 掃 env、`_create_bot_app()` 綁 agent
- **關閉決策**：`SLACK_NAMI_SIGNING_SECRET` 是 HTTP webhook 驗證用，Socket Mode 不需要，可刪（code 不讀）
- **保留必要**：`SLACK_USER_ID_SHOSHO=U05F841H127` — bridge.py:68 讀它 scope agent memory 到修修本人

## VPS 部署記憶點（已完成）

- Web server：`thousand-sunny.service`
- Slack gateway：`nakama-gateway.service`（**獨立 service，要分開 restart！**）
- 重啟指令：`systemctl restart nakama-gateway`
- 確認 log：`journalctl -u nakama-gateway -n 10`，看「啟動 N 個 Slack bot：[...]」
