---
name: codebase-minimalism-first-principle
description: 整個程式庫精簡是最高指導原則；舊架構或討論不符合現況直接砍，不為保留歷史包袱拖累現在的設計
type: feedback
tags: [refactor, minimalism, technical-debt, adr-027]
created: 2026-05-17
originSessionId: adr-027-panel-grill
---

**Rule:** 程式庫精簡是最高指導原則。早期的架構或討論，只要已經不符合現況，可以直接砍除，不需要繞道保留歷史包袱。

**Why:** 2026-05-17 ADR-027 grill 收尾，修修明確說：
> 「有關整個架構的重整，我這裡沒有特別的意見，讓整個程式庫精簡是最高的指導原則。因為這個專案持續演化中，所以太早以前的架構或是討論，只要已經不符合現況的，都可以直接砍掉，沒關係。」

context：當時討論 ADR-005a / PR #78 compose pipeline 砍除範圍，我提了 Q7a「schema 整套 supersede 還是部分留」。修修不糾結保留歷史，授權直接根據現況需要決定。

**How to apply:**
- 遇到 superseded ADR / 既有但不再用的模組 / 早期 grill 留下的暫時方案 — 評估「現在還在用嗎」而不是「砍會不會破壞向後相容」
- 預設動作：砍。**例外**：明確會被未來 PR 復用的 schema / 工具層（如 ADR-005a 的 `DraftV1` schema 留給 Slice 10 repurpose→Usopp）
- 文件 / memory 同樣適用：superseded ADR 標 status；過期 project memory 標 superseded 或直接 archive
- **不**要為了「萬一以後想找原因」保留 dead code — git history 已經是 audit trail

對應 [[feedback_pipeline_anchored_planning]]：新功能必須 anchor 在 stage；舊架構若不在任何 stage 上就是 dead weight，可砍。

對應 Nakama 持續演化的本質 — 不要對任何架構決定產生 sunk-cost attachment。
