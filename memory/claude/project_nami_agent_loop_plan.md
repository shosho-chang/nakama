---
name: Nami Agent Loop 後續藍圖
description: Phase 0 agent loop 已完成並 merge；記錄 Phase 1-3 後續方向（Calendar / 多通道 / 主動性）
type: project
tags: [nami, agent-loop, roadmap]
created: 2026-04-19
updated: 2026-04-19
confidence: high
ttl: 60d
originSessionId: 387704f9-a851-4156-893b-7b0b74f69276
---
## Phase 0（已完成）：Tool-Based Agent Loop

合併於 2026-04-19 前（commits 63383f7 / a2aa272 / 45dbf38）。
現況見 `project_nami_project_bootstrap.md`（工具清單、設計決策、VPS 狀態）。

## Phase 1（候選）：Calendar + Task 獨立化

- Google Calendar API（OAuth flow）— `create_calendar_event` tool
- Obsidian Daily Notes 備援
- Task 可獨立存在（不屬於 project）— 已部分支援（create_task 不 require project）

## Phase 2（已完成 Phase 1-3 基礎）：Persistent Memory

自建 SQLite agent_memory 已上線（見 `project_agent_memory_design.md`）。
剩下 Phase 4 Bridge UI（編輯/刪除記憶 + cost tracking dashboard）。

## Phase 3（候選）：多通道 + 主動性

- Morning brief（Nami 主動推送；Slack thread 機制已就緒）
- Telegram / WhatsApp bot（同一個 agent loop）
- Natural language cron scheduling（「每天早上 9 點 morning brief」）
