---
name: Nami Slack Bot 已部署到 VPS 並成功測試
description: feat/nami-project-bootstrap 在 VPS 跑著，Nami Slack bot (Socket Mode) 可用
type: project
tags: [nami, slack, vps, deployed]
created: 2026-04-19
updated: 2026-04-19
confidence: high
ttl: 30d
originSessionId: 387704f9-a851-4156-893b-7b0b74f69276
---
## 狀態：VPS 已部署 + Slack Bot 可用

**2026-04-19 完成**：
- ✅ VPS 拉 `feat/nami-project-bootstrap` 分支
- ✅ 建立 Slack App「Nami」+ 走 Socket Mode（不走 HTTP Request URL）
- ✅ `.env` 加 Slack token（**2026-04-20 改名** → `NAMI_SLACK_BOT_TOKEN` / `NAMI_SLACK_APP_TOKEN`，見 PR #55。`SLACK_SIGNING_SECRET` Socket Mode 用不到，可刪）
- ✅ 手動啟動 `python3 -m gateway`（尚未設 systemd service）
- ✅ Slack DM 測通：「建立專案」可觸發 create_project flow，選 content_type 後寫 LifeOS

**踩到的坑**：
- 代碼用 Socket Mode，不是 HTTP mode — Slack App 的 Event Subscriptions 的 Request URL 要**完全清空**，否則 Slack 會一直驗證失敗
- Socket Mode 開關要 On
- `.env` 的 Signing Secret 複製錯一次（以為是 `sig_` 開頭，實際不是）

## Slack App 設定（Nami）

- **Name**: Nami
- **Mode**: Socket Mode
- **Bot Token Scopes**: `chat:write`, `channels:history`, `im:history`, `im:read`, `app_mentions:read`
- **Bot Events**: `message.channels`, `message.im`, `app_mention`
- **App-Level Token**: 有 `connections:write` scope

## 未完成

- ⬜ systemd service（`/etc/systemd/system/nakama-gateway.service`）
- ⬜ Nami agent loop 重構（Phase 0，見 [project_nami_agent_loop_plan.md](project_nami_agent_loop_plan.md)）
- ⬜ PR → code-review → merge 回 main

## 其他 Bots 未建立

只有 Nami 建了 Slack App。Robin / Zoro / Franky / Brook 的 Slack App 還沒做。
若要建，命名照 agent 名，token 放 `.env` 分開命名（`SLACK_ROBIN_BOT_TOKEN` 等）。
