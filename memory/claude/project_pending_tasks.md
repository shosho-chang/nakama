---
name: 待辦任務追蹤
description: 當前已知的待辦項目，下次對話時提醒修修
type: project
tags: [todo, pending]
created: 2026-04-11
updated: 2026-04-13
confidence: high
ttl: 90d
originSessionId: ecac2e9b-d409-4922-b30f-4270e46d6df0
---
**待部署（VPS）：**
- Zoro Keyword Research（commit 0e4c866）— `git pull` + `pip install -r requirements.txt` + 設定 `YOUTUBE_API_KEY` + `systemctl restart robin`
- Robin Reader metadata 卡片 + 圖片修復（commit 70f6c11）— 同上次 pull 一起更新
- MemPalace 監控 Zoro→Franky（commit 86cd42e）— 同上

**待調整：**
- KB Research 結果的 UI 呈現方式（修修想再改，具體需求待定）

**待開發（下一個 agent）：**
- Nami（航海士）— 最自然的下一步，消費 Robin/Franky 事件，產出 Morning Brief
- Zoro 其餘功能 — PubMed / KOL 追蹤（keyword research 已完成）

**待評估：**
- MCP 整合方向 — Agent 能力層改為 MCP-compatible（2026-04-11 討論，尚未正式列入規劃）

**基礎建設 — 第三批（開源/商業化前）：**
- CONTRIBUTING.md
- Issue / PR Template

**基礎建設 — 補測試覆蓋率：**
- Robin 核心流程（ingest、web、kb_search）目前完全沒測試，只有 5 個 utility test

**已完成（2026-04-13）：**
- Robin KB Research endpoint 測試通過（修了 nakama-config 尾部斜線 + DataviewJS 自毀 bug）
- Robin Reader：metadata 卡片 + vault 根目錄圖片 fallback
- MemPalace 監控職責 Zoro → Franky（10 個檔案）
- Zoro Keyword Research & Title Generator（YouTube API + Trends + Autocomplete + Claude 合成）
- Obsidian 模板更新（KB Research DOM 渲染版 + Keyword Research 按鈕 + 番茄統計 Number() fix）
- .claude/settings.json 補齊常用 Bash allow 規則
