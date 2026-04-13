---
name: 待辦任務追蹤
description: 當前已知的待辦項目，下次對話時提醒修修
type: project
tags: [todo, pending]
created: 2026-04-11
updated: 2026-04-14
confidence: high
ttl: 90d
originSessionId: ecac2e9b-d409-4922-b30f-4270e46d6df0
---
**待部署（VPS）— 明天第一件事：**
- `git pull` + `pip install -r requirements.txt`（新增 google-api-python-client、pytrends）
- 設定 `YOUTUBE_API_KEY` 到 `.env`
- `sudo systemctl restart robin`
- 需要一次部署的 commits：
  - MemPalace 監控 Zoro→Franky（commit 86cd42e）
  - Robin Reader metadata 卡片 + 圖片修復（commit 70f6c11）
  - Zoro Keyword Research（commit 0e4c866）
  - ADR-003 Telegram Bot（commit 1253abe, b077c71）
  - **Brook 文章助手 Phase 1（commit 370bc22）**

**待測試（部署後）：**
- Robin Reader：metadata 卡片顯示 + 貼上圖片顯示
- Robin KB Research：`/kb/research` endpoint（上次 404 已修，但只在本地驗證邏輯）
- Zoro Keyword Research：Obsidian 按鈕 → `/zoro/keyword-research` 端到端測試
- **Brook 聊天頁面：`http://VPS:8000/brook/chat` 端到端測試**
  - 開新對話 → 確認大綱產出
  - 來回 5+ 回合 → 確認對話連貫
  - 重新整理頁面 → 確認對話恢復
  - 匯出文章 → 確認 Export 功能
  - 查 SQLite `api_calls` → 確認 agent="brook" 有成本記錄

**待調整：**
- KB Research 結果的 UI 呈現方式（修修想再改，具體需求待定）

**待開發（下一個 agent）：**
- Nami（航海士）— 最自然的下一步，消費 Robin/Franky 事件，產出 Morning Brief
- Zoro 其餘功能 — PubMed / KOL 追蹤（keyword research 已完成）
- Brook Phase 2 — SSE streaming、風格參考庫、Prompt Caching、匯出到 Vault

**待評估：**
- MCP 整合方向 — Agent 能力層改為 MCP-compatible（2026-04-11 討論，尚未正式列入規劃）

**基礎建設 — 第三批（開源/商業化前）：**
- CONTRIBUTING.md
- Issue / PR Template

**基礎建設 — 補測試覆蓋率：**
- Robin 核心流程（ingest、web、kb_search）目前完全沒測試，只有 5 個 utility test
- Brook compose.py 也需要測試

**已完成（2026-04-14）：**
- Brook 文章助手 Phase 1 MVP（6 files, 1247 insertions）
  - ask_claude_multi() 多回合 API 支援
  - compose.py 對話管理 + SQLite 儲存 + sliding window
  - brook_chat.html 聊天 UI（dark mode、對話歷史、Export modal）
  - 6 個 web endpoint（/brook/chat, start, message, conversations, conversation/{id}, export/{id}）

**已完成（2026-04-13）：**
- Robin KB Research endpoint 測試通過（修了 nakama-config 尾部斜線 + DataviewJS 自毀 bug）
- Robin Reader：metadata 卡片 + vault 根目錄圖片 fallback
- MemPalace 監控職責 Zoro → Franky（10 個檔案）
- Zoro Keyword Research & Title Generator（YouTube API + Trends + Autocomplete + Claude 合成）
- Obsidian 模板更新（KB Research DOM 渲染版 + Keyword Research 按鈕 + 番茄統計 Number() fix）
- .claude/settings.json 補齊常用 Bash allow 規則
