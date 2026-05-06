---
name: 寫新 ADR 時必須 cross-check 既有 ADR 的 P-level 原則 — 沉默 contract violation 是設計級 bug 源頭
description: ADR-016 違反 ADR-011 P1 但沒人 re-read P1 的教訓 — 任何 ADR 動既有流程前必須列出受影響的 P-level invariants 並 explicit 證明新設計仍滿足；不能假設讀者記得舊 ADR
type: feedback
created: 2026-05-06
---

寫新 ADR / 改既有 pipeline / 重構 module 前，**必須 explicit cross-check 受影響的既有 P-level 原則**。沉默 contract violation 是設計級 bug 源頭，比寫錯 code 嚴重 — code bug 會在 test 抓到，contract 違反會 silent 通過 review 然後在 production 累積數百個壞 artifact。

**Why**: 2026-05-06 發現 KB Concept corpus 87.5% (544/622) 是 Phase B reconciliation 創的空殼。trace 回去：

- ADR-011 P1: 「concept page = cross-source aggregator with merged body」(2026-04-26 凍結)
- ADR-016 §2.1 row B: 「創 stub Concept pages」(2026-05-03 寫)
- 兩者語意根本衝突 — 但 ADR-016 寫的時候沒人重讀 ADR-011 P1
- 結果：Phase B prompt template 寫死 stub creation，Sport Nutrition 4E ingest 一次產出 ~500 個空殼，每個 body 都寫「Will be enriched later」但沒 mechanism 觸發 enrichment

詳見 [project_kb_corpus_stub_crisis_2026_05_06.md](project_kb_corpus_stub_crisis_2026_05_06.md)。

**How to apply:**

寫任何 ADR / pipeline 改動前，prompt 必須包含 **「Affected Principles & Conflict Check」** 段落：

1. **列出新設計動到的既有 ADR** — 不只引用，要 explicit 寫出哪幾條 P-level / D-level / 哪個 §
2. **引用原文 verbatim**（不是改寫，原文一句一句貼）
3. **逐條證明新設計仍滿足** — 不是「應該 OK」，是「具體怎麼滿足」
4. **如果有衝突，必須 explicit 標出並選擇**：
   - (a) supersede 舊原則（顯式寫 `Supersedes ADR-X §Y P-Z`）
   - (b) 修改新設計直到不衝突
   - (c) 新增 exception 並寫進 risk table
   - 不可以是「沒注意到」「忘了」「以為還相容」

5. **review 時優先看 Conflict Check 段落** — 比看新設計本身更優先；找不到這段或這段空白 = 自動 reject

**Anti-pattern signals** (一看到立刻警覺):

- 新 ADR 引用舊 ADR 但只 reference link，沒 quote 原文
- 「per ADR-X」「對齊 ADR-Y」沒具體指哪條
- 新 prompt template 內容跟舊 prompt 規範的 invariant 看似不同但沒解釋
- 「will be X later」「應該不影響 Y」這類字串 — deferred TODO 沒 trigger / owner / deadline = silent dropped
- ADR §metrics 表格只有 wall time / cost / token，**沒有 quality column** — 沒 measure 的會 silent drop

**Subagent prompt 特別重要**:

Subagent 從零起跳沒有 memory 也沒有 ADR context。Dispatch 前必須把所有相關 P-level invariant 直接 inline 進 prompt — 不能假設 subagent 會自己讀 ADR。

ADR-016 phase-b-reconciliation.md 的 bug 就是這個：prompt 引用 ADR-010 但沒 inline ADR-011 P1 原文，subagent 沒看到 aggregator 哲學就執行了 stub creation。

**Memory cross-reference 也適用**:

- [feedback_quality_over_speed_cost.md](feedback_quality_over_speed_cost.md) — 修修最高指導原則「品質 > 速度 > 成本」必須 inline 進任何涉及 ingest / compose / extract 的 subagent prompt，不能依賴 memory 自己 surface
- [feedback_kb_concept_aggregator_principle.md](feedback_kb_concept_aggregator_principle.md) — KB 寫入路徑（Robin / textbook-ingest / kb-ingest）任何改動前必引此原則做 conflict check
