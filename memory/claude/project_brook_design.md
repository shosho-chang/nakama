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

## Phase 2 功能（尚未實作）

| 功能 | 說明 |
|------|------|
| SSE Streaming | 逐字顯示回應（需新增 `ask_claude_multi_stream()`） |
| 風格參考庫 | Vault 建 `Content/Published/` 存過去文章，Brook 啟動時載入 2-3 篇（修修目前還沒建立此資料夾） |
| Prompt Caching | system prompt + 風格範本快取，省 30-40% 成本 |
| Obsidian 整合按鈕 | Project 頁面加按鈕跳轉 `/brook/chat?topic=...` |
| 匯出到 Vault | 完成的文章直接寫入 Obsidian vault |

**How to apply:** 開發 Phase 2 時參考此記憶，不需重新評估架構方向。
