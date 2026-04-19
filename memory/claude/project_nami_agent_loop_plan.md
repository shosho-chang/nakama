---
name: Nami Agent Loop 重構計畫（取代硬編碼 state machine）
description: 借鏡 Hermes + OpenClaw 改成 LLM 驅動的 tool-use agent loop
type: project
tags: [nami, agent-loop, tool-use, claude-api]
created: 2026-04-19
updated: 2026-04-19
confidence: high
ttl: 60d
originSessionId: 387704f9-a851-4156-893b-7b0b74f69276
---
## 背景

修修 2026-04-19 指出現有 Nami flow 太笨：
- 第一條訊息沒提供主題就觸發 flow，結果 title 變「建立專案」
- 硬編碼 state machine 無法處理「幫我 create YouTube 專案，超加工食品，很緊急」這種自然語言
- 無法跨 intent（create_task + create_calendar_event 混在一句）

## 參考

借鏡 Hermes（Nous Research）+ OpenClaw（Peter Steinberger）兩個通用 agent：
- **LLM 作為主循環**，不是 state machine
- **Tool use（function calling）** — 所有動作都是 LLM 呼叫的 function
- **Persistent memory** — context 跨對話持續
- **Skill 作為可組合 tool**

## Phase 0（正在做）：Tool-Based Agent Loop

改 `gateway/handlers/nami.py` 為：
```python
# 定義 Nami 的 tools（JSON schema）
- create_project(topic, content_type, priority, area)
- create_task(title, project_link?, due_date?, estimated_pomodoro?)
- create_calendar_event(title, datetime, duration_minutes, location?)
- ask_user(question, options?)

# Agent loop
while not done:
    response = claude.messages.create(tools=NAMI_TOOLS, messages=...)
    if response.stop_reason == "end_turn":
        return response.content
    if response.stop_reason == "tool_use":
        tool_result = execute_tool(response.content[-1])
        messages += [assistant, tool_result]
```

`ask_user` tool 特殊處理：LLM 呼叫時，把問題丟回 Slack thread，等使用者回覆後把答案當成下一條 user message 繼續 loop。

## Phase 1（3-5 天後）：Calendar + Task 獨立化

- Google Calendar API（OAuth flow）
- Obsidian Daily Notes 備援
- Task 可獨立存在（不屬於 project）

## Phase 2（1-2 週後）：Persistent Memory

- 記 `last_active_project`, `recent_tasks`, `user_preferences`
- System prompt 注入 context

## Phase 3（2-3 週後）：多通道 + 主動性

- Telegram / WhatsApp bot（同一個 agent loop）
- Natural language cron scheduling（「每天早上 9 點 morning brief」）

## 當前進度

Phase 0 branch: `feat/nami-agent-loop`（待建立）
