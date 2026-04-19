---
name: Nami Agent Loop — feat/nami-agent-loop 進度
description: LLM tool-use agent loop 重構完成，VPS 已部署實測，待 PR merge
type: project
tags: [nami, agent-loop, tool-use, vps-deployed]
created: 2026-04-19
updated: 2026-04-19
originSessionId: 387704f9-a851-4156-893b-7b0b74f69276
---
## 狀態：VPS 實測通過，待 PR review + merge

**Branch**: `feat/nami-agent-loop`（已推 origin，最新 commit: `231d3b0`）
**VPS**: `nakama-gateway.service` 已設定為 systemd service，正在運行

## 已完成（2026-04-19）

**架構重構**：
- `gateway/handlers/nami.py` — 完整 LLM agent loop（取代舊 state machine）
  - 4 個 tools：`create_project` / `create_task` / `list_tasks` / `ask_user`
  - `ask_user` 特殊處理：pause loop → Slack thread → continue_flow()
  - thread 持續對話：end_turn 後不清掉 conversation，整個 thread 可繼續問問題
  - `_MAX_ITERS = 6` 安全上限
- `shared/anthropic_client.py` — 新增 `call_claude_with_tools()`（prompt caching）
- `prompts/nami/agent_system.md` — 決策規則 + 日期判斷 + Nami 角色個性（few-shot）
- `gateway/conversation_state.py` — 新增 `get_latest_for_user_and_agent()`（DM fallback）
- `gateway/bot.py` — DM thread_ts 缺失的 fallback 邏輯 + debug 日誌

**修復清單**：
- DM 中 thread reply 沒反應（`thread_ts` 缺失，現用 user+agent fallback 查詢）
- 移除 intent debug context block（用戶不應看到）
- 注入今日日期 + 未來 14 天對照表（Sonnet 直接查表，不再自行推算）
- 模型升級：Haiku 4.5 → Sonnet 4.6（Haiku 日期推算不可靠）
- `scheduled` 支援 datetime 格式（`2026-04-23T15:00:00`）
- Nami 角色個性 prompt：正面描述 + 4 個 few-shot 範例，修修 = 船長

**測試**：26 tests pass，336 total pass，ruff clean

## VPS 部署

```bash
systemctl status nakama-gateway  # 確認運行中
journalctl -u nakama-gateway -f  # 看即時日誌
```

## 下一步

- ⬜ PR review + squash merge（照 feedback_pr_review_merge_flow.md 流程）
- ⬜ Slack manifest 實際確認訂閱了 message.channels + message.im（已確認有加）
- ⬜ morning-brief 功能（Nami 主動推送）

## 已知限制

- ConversationStore 是 in-memory，重啟後狀態清掉，用戶需重新開始對話
- DM fallback 策略（user+agent 查最新 conversation）在多對話同時進行時可能混淆
