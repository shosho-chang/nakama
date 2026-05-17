---
name: Brook 設計決策與 Phase 2 規劃
description: Brook 文章助手的架構選擇、成本分析、Phase 2 功能清單
type: project
tags: [brook, design, cost]
created: 2026-04-14
updated: 2026-04-14
confidence: high
ttl: permanent
---
## 架構選擇

- **對話介面**：Robin Web UI 聊天頁（`/brook/chat`）— 修修選擇此方案而非 Obsidian 內嵌或 Context Package 方式
- **多回合支援**：新增 `ask_claude_multi(messages)` 而非修改 `ask_claude()`，避免影響其他 agent
- **對話儲存**：SQLite（brook_conversations + brook_messages），非 in-memory dict — 對話可跨 server restart 存活
- **上下文管理**：Sliding window（前 2 則 anchor + 最近 40 則 recent），中間截斷

**Why:** Brook 是 Nakama 第一個需要多回合對話的 agent，其餘都是 one-shot。選擇 Web UI 是因為 Obsidian DataviewJS 不適合即時對話 UX。

## 成本分析

- 使用 Sonnet 4，單篇文章（15-20 回合）約 $1.00-1.50
- 啟用 Prompt Caching 後可降至 $0.50-0.80
- 10-20 篇/月：$5-30，與 ChatGPT Plus $20/月 相當或更便宜

