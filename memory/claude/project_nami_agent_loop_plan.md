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

## Phase 1（已完成）：Deep Research — web_search + fetch_url

PR #58 merged 2026-04-21，VPS commit fd02252，nakama-gateway 已重啟。

### 新增能力
- `shared/firecrawl_search.py` — Firecrawl `/search` wrapper，`firecrawl_search(query, num_results)` 回傳 `list[dict]`
- `_tool_web_search` / `_tool_fetch_url` — 兩個新 Nami tool，dispatcher 已接入
- `prompts/nami/agent_system.md` — 深度研究模式 section：trigger、4-step 流程、frontmatter 格式、12 tool call 預算
- `_MAX_ITERS` 從 6 → 15（支援多輪研究循環）
- 62 tests pass（含 5 個 web/research 新測試）

### 已知 deferred bug（下次再修）
1. `tests/test_gateway_handlers.py:1750-1752` — dead code（裸 `_tool_fetch_url` call 在 mock 外）
2. `shared/firecrawl_search.py:29` — `lang` 參數是 dead code，未轉送 Firecrawl SDK
3. `gateway/handlers/nami.py:1757` — truncation 後 `len(content)` 報截斷後長度，非原始長度

### 研究報告輸出路徑
`Nami/Notes/Research/YYYY-MM-DD-{slug}.md`（frontmatter 含 sources/query/date）

## Phase 2（候選）：多通道 + 主動性

- Morning brief（Nami 主動推送；Slack thread 機制已就緒）
- Telegram / WhatsApp bot（同一個 agent loop）
- Natural language cron scheduling（「每天早上 9 點 morning brief」）
