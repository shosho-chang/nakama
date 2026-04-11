---
name: 待辦任務追蹤
description: 當前已知的待辦項目，下次對話時提醒修修
type: project
tags: [todo, pending]
created: 2026-04-11
updated: 2026-04-12
confidence: high
ttl: 90d
---

**待測試：**
- Robin `/kb/research` endpoint（2026-04-10 新增，commit c49b630，尚未測試）

**待開發（下一個 agent）：**
- Nami（航海士）— 最自然的下一步，消費 Robin/Franky 事件，產出 Morning Brief
- Zoro（劍士）— PubMed / KOL 追蹤、MemPalace 監控

**基礎建設 — 第三批（開源/商業化前）：**
- CONTRIBUTING.md
- Issue / PR Template

**基礎建設 — 補測試覆蓋率：**
- Robin 核心流程（ingest、web、kb_search）目前完全沒測試，只有 5 個 utility test

**已完成：**
- ADR-002 Phase 1-3（PR #4 merged）
- 第一批基礎建設：pyproject.toml + Git tags v0.0.1~v0.4.1（PR #5 merged）
- 第二批基礎建設：CI/CD + Ruff linter + pre-commit hooks（PR #6 merged）
- state.db 路徑修正：/home/agents/ → /home/nakama/data/
- VPS 已同步（2026-04-12）
- gh CLI 已在 Mac 上安裝並授權
