---
name: 規劃功能前必走 Grill me
description: 跳過 Grill me 直接 PRD/ADR + ship → 修修 mental model 跟 frozen 設計 mismatch；等 ship 後 QA 才 surface = 三 PR 全部要重新檢討；任何「新功能 / 新 button / 改 user-visible flow」前 grill 一輪非選配
type: feedback
---

任何「新功能 / 新 button / 改 user-visible flow」的設計，**PRD / ADR 凍結前必先走 Grill me**（蘇格拉底式問答 — 我問、修修答、答到他自己 surface 動機 + 邊界 + 替代方案 + 不做的事）。跳過直接寫 PRD / ADR / dispatch implementation 是違規。

**Why**：
2026-05-04 PRD #337（annotation KB integration）三 slice ship 後 QA 才發現修修 mental model 跟 ADR-017 frozen 設計**有本質 gap**：

| 維度 | 修修記憶 | ADR-017 凍結 |
|---|---|---|
| Annotation 主存儲 | 嵌在 source page 內 | 獨立 ``KB/Annotations/{slug}.md`` 解耦 |
| 觸發整合到 KB | 一次性 ingest（連 annotation 一起進 source page） | 「同步到 KB」鍵獨立操作 → push 到 Concept page |

直接後果：
- PRD #337 三 PR (#342/#343/#344) 全部要重新檢討（potential revert）
- P0 hotfix PR #368（修 sync invalid JSON）暫停 merge — 因為連功能本身要不要保留都未定
- ~3-5 hours grill + planning + 三 PR 工 + 我的 P0 hotfix 工 = 全沉沒成本

修修自己定的根因：「之前在規劃功能的時候，沒有用 Grill me 的後果」。

**How to apply**：

1. **Trigger 條件**：任何下列關鍵字出現，停止往「直接寫 PRD / dispatch」方向走，先 ask `要不要先 Grill me 一輪？`：
   - 「我想做 X」「加個 Y 功能」「在 reader / inbox / bridge 加 button」
   - 「重新設計 X」「改 X 的 flow」
   - 「規劃 X」「想想 X 怎麼做」
2. **Grill 出口才能 dispatch**：grill 結束時修修必須能用一句話 say back「為什麼做」+「不做什麼」+「怎麼跟既有 flow 接」。三項缺一不准進 PRD。
3. **PRD / ADR 寫完仍須複誦 grill 結論**：在 PRD §1 Context 段落首句帶 grill 出口的「為什麼做」，避免 implement 時 drift。
4. **Ship 前 acceptance dry-run**：PRD §Acceptance 至少一條人工 walkthrough（修修自己用嘴講「然後我點什麼按鈕、看到什麼、預期什麼結果」），確認 frozen 設計跟修修當下 mental model 對齊。
5. **Exception**：純工程 hotfix（如本次 PR #368 修 invalid JSON contract）不算「新功能」、跳過 grill 沒問題；但**底層功能**的 grill 缺席不能用 hotfix 補。
