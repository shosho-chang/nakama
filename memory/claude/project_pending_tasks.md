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

**Transcriber 升級（feat/transcriber-upgrade branch，3 commits）：**
- ✅ shared/auphonic.py — Auphonic REST API 客戶端（多帳號輪詢 + jingle 裁切）
- ✅ shared/transcriber.py — openlrc/Whisper → FunASR Paraformer-zh
- ✅ SRT 斷行 ≤20 字 + 標點改空格 + 英文不切斷
- ✅ E2E 測試通過（20 min Podcast，GPU 10 秒）
- ⬜ 辨識度改善 — 開 LLM 校正（use_llm_correction=True）
- ⬜ Auphonic E2E — 完整 pipeline 含 normalization
- ⬜ merge PR 回 main
- ⬜ 後續：CLI 命令 → Skill 化

**VPS 已部署完成（2026-04-15）：**
- Thousand Sunny web server 已上線
- Zoro Keyword Research 端到端測試通過

**待測試（部署後）：**
- Robin Reader：metadata 卡片顯示 + 貼上圖片顯示
- Brook 聊天頁面端到端測試
- KB Research UI 呈現方式（修修想再改）

**待進行（下一步）：**
- Agent 功能 → Skill 改寫 **Phase 2**：morning-brief (Nami)、kb-search (Robin)
- Agent 功能 → Skill 改寫 **Phase 3**：keyword-research (Zoro)、weekly-report (Franky)、style-extractor

**待開發（agent 功能）：**
- Nami（航海士）— 消費 Robin/Franky 事件，產出 Morning Brief
- Zoro 其餘功能 — PubMed / KOL 追蹤
- Brook Phase 2 — SSE streaming、風格參考庫、Prompt Caching、匯出到 Vault
- PubMed 整合 — 修修有 n8n RSS 工作流，預計用於其他功能，不是 Zoro

**基礎建設 — 補測試覆蓋率：**
- Robin 核心流程（ingest、kb_search）
- Brook compose.py
- Thousand Sunny routers smoke test

**已完成（2026-04-15）：**
- Transcriber 升級：FunASR + Auphonic（feat branch，待 merge）
- Thousand Sunny web server 重構
- Zoro Keyword Research 雙語版 + 直寫 markdown
