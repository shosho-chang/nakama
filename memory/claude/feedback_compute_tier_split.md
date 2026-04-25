---
name: 重 ingest 桌機 / 輕 query VPS 的 compute tier 分工
description: 桌機（RTX 5070 Ti + 64GB RAM）負責重 ingest（Docling/EPUB/教科書/本地 LLM batch），VPS（2vCPU/4GB RAM）負責輕 query 對話（agent loop / Slack bot / Bridge UI / cron）。vault 是同步介面：桌機寫入 → Obsidian Sync → VPS 讀取
type: feedback
originSessionId: c6399fca-d109-4f35-807f-e564c7010f0c
---
雙層架構分工：

**桌機**（RTX 5070 Ti 16GB VRAM + 64GB RAM）：
- Docling 書籍 / 掃描 PDF 解析
- EPUB / Word parser
- 整本教科書 ingest workflow
- Qwen 3.6 / 其他本地 LLM 跑 map step（如 Robin keyword extract）
- 未來 BabelDOC 高保真學術 PDF 雙語化
- 大批量 chunking / embedding / 重 LLM batch

**VPS**（2vCPU / 4GB RAM / Asia/Taipei TZ）：
- Robin daily digest cron（每日 05:30）
- agent loop（Nami / Brook / Chopper / Zoro / Sanji 等 Slack handler）
- Bridge UI（thousand_sunny FastAPI service）
- approval queue（Brook → Usopp pipeline）
- cron 輕量觀測（Franky / external probe）
- scrape-translate 輕量 Trafilatura + 段落 Sonnet 翻譯

**Why**：
- VPS 3.8GB RAM 撐不住 torch + transformers（Docling 試過就放棄）
- 整本書 chunking + embedding 需要 RAM，本地跑才合理
- 本地 GPU 對**本地 LLM** batch 比 API call 更省成本

**例外 — Claude Code subscription 走法**（2026-04-25 ADR-010 新增）：

整本教科書 ingest（textbook-ingest skill）**不需要桌機 GPU**，因為：

- Claude Code Opus 4.7 走 Max 200 subscription quota（修修已付）= 零邊際成本
- 1M context 可整章 in-context，不需要本地 embedding pipeline
- 不需要 vector store，retrieval 走 Robin /kb/research 的 LLM ranking + symbolic backlink

所以「桌機 vs Mac」對 textbook-ingest 主要看修修在哪邊舒服 + 哪台 quota 有剩，不是性能 / 成本決策。Phase 1 開發在 Mac，Phase 1.5 MVP 驗收 + 實際 ingest 修修決定走桌機。

**規則修正**：「重 ingest 在桌機」對「需要本地 model 跑（Docling / Qwen / EPUB Word parser）」仍成立；對「Claude Code-driven ingest（textbook 等）」改成「兩邊都行，看 subscription quota + 修修偏好」。

**vault 是中介層**：
- 桌機 ingest 寫進 vault → Obsidian Sync 自動同步到雲 + VPS
- VPS 端 Robin / Chopper KB 查詢直接讀 vault → 不需要桌機在線
- 修修在桌機 / Mac / 手機都看得到一致 vault

**How to apply**：

設計新功能前先問「這條要在桌機還是 VPS 跑？」決策表：

| 屬性 | 桌機 | VPS |
|------|------|-----|
| RAM 需求 > 2GB | ✅ | ❌ |
| 需要 GPU | ✅ | ❌ |
| 需要本地 file 解析（PDF/EPUB/docx）整本 | ✅ | ❌ |
| 需要本地 LLM batch | ✅ | ❌ |
| 純 API endpoint / agent 對話 | ❌ | ✅ |
| cron 輕量觀測 / digest | ❌ | ✅ |
| 24/7 always-on | ❌ | ✅ |
| Claude Code interactive subscription ingest | ✅（Mac 也行）| ❌（VPS 沒 Claude Code）|
| 寫入 vault | ✅ 兩邊都行 | ✅（透過 sync）|
| 修修一鍵互動 | ✅ | ✅（Slack / Bridge）|

寫 ADR / pending todo 時，明確標 `(桌機)` 或 `(VPS)` —— 避免實作時才發現位置選錯（例：把整本書 ingest 寫進 VPS endpoint 會 OOM）。
