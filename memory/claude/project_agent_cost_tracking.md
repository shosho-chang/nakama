---
name: Agent Cost Tracking — 待實作功能
description: 記錄每個 agent 每次呼叫的 token 用量與費用，供 Bridge 儀表板顯示
type: project
tags: [bridge, telemetry, cost, usage]
created: 2026-04-19
originSessionId: 6dc774b2-8ee7-4655-b691-eebe19832245
---
## 需求

修修想在 Bridge（甲板儀表板）看到每個 agent 執行了哪些任務、花了多少錢。

## 建議設計

- **資料來源**：Claude API response 已有 `usage`（input_tokens / output_tokens / cache_read_input_tokens / cache_creation_input_tokens）
- **寫入位置**：`data/usage_log.jsonl`（append-only）
- **記錄格式**：
  ```json
  {"ts": "2026-04-19T10:00:00Z", "agent": "nami", "intent": "create_task", "model": "claude-sonnet-4-6",
   "input_tokens": 1234, "output_tokens": 56, "cache_read_tokens": 800, "cost_usd": 0.0012}
  ```
- **實作點**：`shared/anthropic_client.py` → `call_claude_with_tools()` 回傳後 append log

## Why: JSONL vs SQLite
- JSONL：簡單、零依賴、git-friendly，Bridge 讀起來容易
- SQLite：有索引，日/週聚合快；資料量大再遷移
- 現階段 JSONL 夠用

## 狀態：待實作
