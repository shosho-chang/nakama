---
name: Nami Agent Loop 後續藍圖
description: Phase 0 agent loop 已完成並 merge；記錄 Phase 1-3 後續方向（Calendar / 多通道 / 主動性）
type: project
tags: [nami, agent-loop, roadmap]
created: 2026-04-19
updated: 2026-04-21
confidence: high
ttl: 60d
originSessionId: cbf94814-ac39-48c7-af66-32e399edf699
---
## Phase 0（已完成）：Tool-Based Agent Loop

合併於 2026-04-19 前（commits 63383f7 / a2aa272 / 45dbf38）。
現況見 `project_nami_project_bootstrap.md`（工具清單、設計決策、VPS 狀態）。

## Phase 1（已完成）：Deep Research — web_search + fetch_url

PR #58 merged 2026-04-21，VPS deployed。
2026-04-21 大幅除錯（PR #59/#61/#63/#64），最終驗收通過。

### 已知限制與設計決策
- `call_claude_with_tools` max_tokens=8192（PR #64）
- `fetch_url` 截斷 5000 chars（PR #63）
- 預算：web_search 最多 2 次、fetch_url 最多 3 次
- 報告 800 字以內存到 `Nami/Notes/Research/`
- **定位**：Quick Lookup，不是完整 Deep Research。完整研究請用 Gemini/ChatGPT

### 3 個已修 deferred bug（PR #59）
1. `tests/test_gateway_handlers.py` — dead code 移除
2. `shared/firecrawl_search.py` — `lang=` 移除（SDK 不支援直接 kwarg）
3. `gateway/handlers/nami.py` — 截斷長度改報原始值

## Phase 2（已完成 Phase 1-3 基礎）：Persistent Memory

自建 SQLite agent_memory 已上線（見 `project_agent_memory_design.md`）。
剩下 Phase 4 Bridge UI（編輯/刪除記憶 + cost tracking dashboard）。

## Phase 3（候選）：多通道 + 主動性

- Morning brief（Nami 主動推送；Slack thread 機制已就緒）
- Natural language cron scheduling
- PubMed Quick Lookup（調研中）
