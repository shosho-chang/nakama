---
name: 待辦任務追蹤
description: 當前已知的待辦項目，下次對話時提醒修修
type: project
tags: [todo, pending]
created: 2026-04-11
updated: 2026-04-16
confidence: high
ttl: 90d
originSessionId: 8bece3a7-26ae-4215-bade-04d2bca1809b
---
**Transcriber（PR #9 已 merge，2026-04-15）：**
- ✅ FunASR + Auphonic + LLM 校正升級（Pinyin + JSON diff + LifeOS 整合 + Opus）
- ✅ 78 個測試全過，已 merge 到 main
- ⬜ LLM 校正 E2E 實測（`use_llm_correction=True` + 真實音檔）
- ⬜ Auphonic E2E 實測（完整 pipeline 含 normalization）
- ⬜ CLI 命令 → Skill 化

**VPS 已部署完成（2026-04-15）：**
- Thousand Sunny web server 已上線
- Zoro Keyword Research 端到端測試通過

**待測試（部署後）：**
- Robin Reader：metadata 卡片顯示 + 貼上圖片顯示
- Brook 聊天頁面端到端測試
- KB Research UI 呈現方式（修修想再改）

**Robin 大文件 Ingest（PR #11 已 merge，2026-04-15）：**
- ✅ PDF 解析（pymupdf4llm 本地 + Firecrawl 遠端）
- ✅ 本地 LLM 客戶端（OpenAI-compatible，支援 llama.cpp / Ollama）
- ✅ Map-Reduce 大文件摘要（chunker + prompts + fallback）
- ✅ 33 個新測試全過
- ⬜ E2E 實測：Gemma 4 26B 已安裝，丟 PDF 跑完整 pipeline

**Robin 內容性質分類（PR #12 已 merge，2026-04-16）：**
- ✅ 兩層分類架構（source_type + content_nature）
- ✅ 6 類別專屬 prompt（research/textbook/clinical_protocol/narrative/commentary + popular_science default）
- ✅ prompt_loader 支援 categories/ 路由 + fallback
- ✅ Web UI 兩個 dropdown + 取消按鈕
- ✅ 領域擴充：運動科學、營養科學、腦神經科學（睡眠/飲食/運動/情緒四大面向）
- ⬜ Web UI 實測：不同 content_nature 執行 ingest
- ⬜ E2E：大文件 + 本地 LLM + 類別 prompt 驗證

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
