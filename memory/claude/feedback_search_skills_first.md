---
name: 開發前必須搜尋現有 Skills
description: 新功能開發前先用 find-skills 搜尋社群有沒有現成方案，不要重複造輪子
type: feedback
created: 2026-04-15
updated: 2026-04-15
confidence: high
---

開發新的 shared module 或 agent 功能前，**必須先搜尋現有的 skills 和 MCP tools**。

**Why:** Robin Ingest 大文件升級時，直接手寫了 pdf_parser.py、local_llm.py、chunker.py，沒有先確認社群是否已有類似方案。修修明確指出這違反了「不要重複造輪子」原則。

**How to apply:** 
- 在 Phase 2（Plan）之前，用 find-skills 或搜尋 Claude Code plugins / MCP tools
- 特別留意：PDF 解析、LLM wrapper、RAG chunking、文件處理 等常見功能
- 即使最終決定自己寫，也要在 plan 中記錄「已調研過 X、Y、Z，因為 N 原因決定自己實作」
