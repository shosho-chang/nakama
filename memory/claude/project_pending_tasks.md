---
name: 待辦任務追蹤
description: 當前已知的待辦項目，下次對話時提醒修修
type: project
tags: [todo, pending]
created: 2026-04-11
updated: 2026-04-15
confidence: high
ttl: 90d
---
**VPS 已部署完成（2026-04-15）：**
- Thousand Sunny web server 已上線（取代舊的 robin service）
- `WEB_SECRET=s581020` 已設定
- Zoro Keyword Research 端到端測試通過

**待測試（部署後）：**
- Robin Reader：metadata 卡片顯示 + 貼上圖片顯示
- Robin KB Research：`/kb/research` endpoint（VPS curl 測試回 0 results，可能是 vault 沒有 KB 內容）
- **Brook 聊天頁面：`http://VPS:8000/brook/chat` 端到端測試**
  - 開新對話 → 確認大綱產出
  - 來回 5+ 回合 → 確認對話連貫
  - 匯出文章 → 確認 Export 功能

**待調整：**
- KB Research 結果的 UI 呈現方式（修修想再改，具體需求待定）

**待進行（下一步）：**
- Agent 功能 → Skill 改寫 **Phase 2**：morning-brief (Nami)、kb-search (Robin)
- Agent 功能 → Skill 改寫 **Phase 3**：keyword-research (Zoro)、weekly-report (Franky)、style-extractor

**待開發（agent 功能）：**
- Nami（航海士）— 最自然的下一步，消費 Robin/Franky 事件，產出 Morning Brief
- Zoro 其餘功能 — PubMed / KOL 追蹤（keyword research 雙語版已完成）
- Brook Phase 2 — SSE streaming、風格參考庫、Prompt Caching、匯出到 Vault
- PubMed 整合 — 修修有 n8n RSS 工作流（GPT 分析摘要→Google Sheets），預計用於其他功能，不是 Zoro

**開發流程變更（2026-04-14）：**
- 多視窗開發時用 feature branch + PR（不直接在 main 上改）
- 開發前先讓 prior-art-research skill 跑完再動手
- 用 /skill-creator 建新 skill（含 eval 迭代循環）
- commit 前必須跑 `ruff check` + `ruff format`（不只 format）

**待評估：**
- MCP 整合方向 — Agent 能力層改為 MCP-compatible（2026-04-11 討論，尚未正式列入規劃）

**基礎建設 — 第三批（開源/商業化前）：**
- CONTRIBUTING.md
- Issue / PR Template

**基礎建設 — 補測試覆蓋率：**
- Robin 核心流程（ingest、kb_search）目前完全沒測試
- Brook compose.py 也需要測試
- Thousand Sunny routers 需要基礎 smoke test

**已完成（2026-04-15）：**
- Thousand Sunny web server 重構（commit d3f7c7c）
  - `agents/robin/web.py`（775 行）拆成 `thousand_sunny/` 獨立模組
  - auth.py 共用認證、helpers.py 共用工具
  - routers/robin.py（16 routes）、zoro.py（1 route）、brook.py（6 routes）
  - templates 搬到 thousand_sunny/templates/{robin,brook}/
  - systemd service: robin → thousand-sunny
  - VPS 部署測試通過
- Zoro Keyword Research 改寫為直接寫入 markdown（不再用 DataviewJS 渲染）
  - 解決 Obsidian DataviewJS 300+ DOM 元素造成 hang 的問題
  - 結果用 %%KW-START%% / %%KW-END%% 標記直接寫入 .md 檔
  - 新增 search_topic frontmatter 欄位控制搜尋關鍵字
  - 影片分 Shorts / 長影片兩個表格，只顯示英文（國外趨勢）
  - YouTube 搜尋量增至 50 筆確保長影片足夠

**已完成（2026-04-14）：**
- Zoro Keyword Research 雙語升級（3 commits: e34ed2c, be4647b, b8682d5）
- PR #8 merged：Agent→Skill Phase 1
- Agent→Skill 改寫 Phase 1：kb-ingest + article-compose + obsidian-markdown
- Brook 文章助手 Phase 1 MVP

**已完成（2026-04-13）：**
- Robin KB Research endpoint 測試通過
- Robin Reader：metadata 卡片 + vault 根目錄圖片 fallback
- MemPalace 監控職責 Zoro → Franky
- Zoro Keyword Research v1
- Obsidian 模板更新
