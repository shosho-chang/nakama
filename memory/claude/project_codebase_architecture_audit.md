---
name: 2026-04-27 codebase architecture audit
description: 12 deepening 候選 + ROI 排序 + 哪些 done 哪些 pending；下次想做架構改善先看這份再決定要不要重跑 skill
type: project
originSessionId: 3d901e7b-183e-450a-a0bf-2f06311b6452
---
2026-04-27 第一次跑 `/improve-codebase-architecture`（mattpocock skill）對 nakama repo 完整掃過，產出 12 個 deepening 候選。

**完整清單 + 細節**：[docs/research/2026-04-27-codebase-architecture-audit.md](../../docs/research/2026-04-27-codebase-architecture-audit.md)

**已完成**：① LLM facade shallow + ⑤ anthropic/gemini 樣板重複 — PR #208 merged 2026-04-27 為 `e043e2a`（infra split + facade 擴大）。

**Pending ROI 排序**：
- **④ sanitizer 收編** — Chopper / Sanji 加平台前最值得做
- **② approval FSM + schema 鎖死** — Chopper Phase 2 blocker
- **⑨ doc_index 蒸發** — 順手修 pre-existing `test_doc_index.py::test_stats_returns_per_category_counts`
- **⑪ seo modules re-export** — `__init__.py` 一行 fix
- **③ KB writer interface 收緊** / **⑥ memory tier interface** / **⑦ prompt loader 顯式 dep** / **⑩ anomaly 抽得不夠廣** — 中優先
- **⑧ Usopp publisher 600 行 monolith** — 等真要做 Chopper 留言 publisher 才動

**Why**：audit 的價值不在「找一次」，在「baseline 對照下次掃」。下次跑 skill 時拿這份比，看新候選浮現 / 老候選是否還在。

**How to apply**：要動架構任務前先讀這份。已 done 的不重新分析；pending 的按 ROI 排。決定動哪一個之後，再跑 grilling loop（skill step 3）。重跑整個 audit 的觸發條件：sprint boundary、加新 agent、完成 backlog ≥3 項。
