---
name: feedback_adr_signoff_and_toplevel_doc_sync
description: ADR 引入新 agent / 改 ownership 時，(1) 必拿修修 explicit sign-off 才 ship（Draft-shipped 是 drift 溫床）(2) 必同 PR 更新 CONTENT-PIPELINE.md + CONTEXT-MAP.md + ARCHITECTURE.md（修修的 mental model anchor 在頂層文件，不在 ADR）
type: feedback
---

**Why**：2026-07-03 修修：「Foundry 會跑出來我之前就覺得很奇怪，這不是我預設中的」→ 觸發 ADR-050 整場修訂。回溯根因兩條：

1. **ADR-032 Status 停在「Draft v2 待修修最終 sign-off」但 Phase 1 五個 PR + ADR-033 + ADR-038 系列全部 ship 了**（ADR-033 同樣 Draft-shipped）。決策沒被 owner 正式認可就變成既成事實。
2. **CONTENT-PIPELINE.md / CONTEXT-MAP.md 從 ADR-032（2026-05-25）到 2026-07-03 從未更新**，仍寫「Script-Driven Video = `agents/brook/script_video/`」（ADR-015 時代）。repo 內同時存在兩套矛盾 truth，而修修日常 anchor 的是 CONTENT-PIPELINE.md。

**How to apply**：
- 新 ADR 引入 agent / 改 agent map / 改 ownership → **同一個 PR 必含** CONTENT-PIPELINE.md + CONTEXT-MAP.md + ARCHITECTURE.md 對應列更新（比照 VAULT-LAYOUT 的「Reviewer 抓」紀律）
- ADR Status = Draft 時**不 ship 實作 PR**；grill/panel 完成後主動要一次 explicit sign-off 把 Status 翻 Accepted，才開實施
- 另：agent 命名遵守 One Piece 船員慣例 — Foundry 破例也是「不是我預設中的」的成因之一
- 對應既有紀律 [[feedback_adr_principle_conflict_check]]、[[feedback_grill_then_panel_for_big_adr]]
