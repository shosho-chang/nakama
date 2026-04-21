---
name: 待辦任務追蹤
description: 當前已知的待辦項目，下次對話時提醒修修
type: project
tags: [todo, pending]
created: 2026-04-11
updated: 2026-04-22
confidence: high
ttl: 90d
originSessionId: cbf94814-ac39-48c7-af66-32e399edf699
---
**Nami 收尾（本次對話完成大部分，剩兩項）：**
- ✅ PR #59：Deep Research deferred bug 3 個 + lint 修
- ✅ PR #60：移除 Slack 回應的 `:tangerine: Nami` header
- ✅ PR #61：Firecrawl `lang=` kwarg revert（search 壞掉的根本原因）
- ✅ PR #62：Gmail `list_messages` 空結果 ThreadPoolExecutor crash
- ✅ PR #63：Deep Research max_tokens — fetch 截斷 20k→5k + 預算 2+3
- ✅ PR #64：`call_claude_with_tools` max_tokens 2048→8192
- ✅ Deep Research 功能驗收通過（Vault 有報告輸出）
- ✅ Gmail 大量搜尋策略加入 system prompt（分批 5 封 + ask_user）
- ⬜ **project-bootstrap template 同步**（`tpl-project.md` + `tpl-action.md` 脫節）
- ⬜ **Slack thread 續問實機測試**（多輪對話未驗）

**Robin（今晚要做）：**
- ⬜ `/kb/research` E2E 未測（skill 化前先驗）
- ⬜ Robin Reader：metadata 卡片顯示 + 貼上圖片顯示（本機測試）
- ⬜ KB Research UI 呈現方式（修修想再改）

**Phase 4 Bridge UI：**
- ✅ PR-A backend merged（#41）
- ✅ PR-B Memory UI（#42）、PR-C Cost UI（#44）、Bridge Hub（#45）
- ✅ Direction B Instrument Panel 重設計 + VPS 部署（PR #65，2026-04-21）
- ⬜ Tech debt：`agent_memory.update` rollback / `MemoryUpdate.type` Literal / docstring
- ⬜ 細節 UI polish（修修說「還有很多細節要改，但先這樣」）

**Zoro：**
- ⬜ Zoro bot Slack app 上線（Phase 2 brainstorm blocker）
- ⬜ keyword-research backlog 6 項 GH issues（術語表 / normalize / {today} / reddit_zh / twitter / CLI cost）

**Skill 化工程：**
- ⬜ `kb-search` (Robin) — E2E 未測，skill 化前先驗
- 🚧 `style-extractor` — PRD v4 草稿完成，等 3 個 StyleSamples 資料夾備齊
- ⬜ `weekly-report` (Franky)
- ⬜ `morning-brief` (Nami)
- ⬜ `interview-to-article`、`kb-synthesize-article`、`book-reflection-compose`（需 PRD）

**SEO Solution（下一個重點）：**
- ⬜ prior-art-research（DataForSEO MCP / Ahrefs MCP / 部落格 audit workflow）
- ⬜ skill 家族設計（`seo-audit-post` / `seo-keyword-enrich` / `seo-optimize-draft`）

**雙語閱讀 Pipeline：**
- ⬜ P2B：BabelDOC 整合（需 Immersive Translate API key）
- ⬜ P3：Annotation → Ingest 整合

**PubMed 整合：**
- ✅ Robin PubMed 每日 digest 上線 VPS（PR #66/#67/#68，首次 cron 2026-04-22 05:30 台北）
- ⬜ 調研 PubMed NCBI Entrez API（Nami Quick Lookup 替代 Deep Research，獨立於 Robin digest）

**基礎建設：**
- ⬜ Robin 核心流程（ingest、kb_search）補測試覆蓋率
- ⬜ Brook compose.py 補測試覆蓋率
- ⬜ Thousand Sunny routers smoke test
