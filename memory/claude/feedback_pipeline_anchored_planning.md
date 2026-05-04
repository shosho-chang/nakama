---
name: 規劃功能必 anchor 在七層架構
description: 任何「開發 X / 下個 task 做什麼」對話前必先 anchor 在 CONTENT-PIPELINE.md 七層之一 + 對照 Lines × Stages + Agents × Stages 矩陣；無法 anchor 要 flag
type: feedback
created: 2026-05-04
---

任何「我們來開發 X / 下個 task 做什麼 / 下一步做什麼」對話前，必先回答三個 anchor 問題：

1. **這個 feature 屬於哪個 stage？**（CONTENT-PIPELINE.md 七層之一：1 收集 / 2 閱讀註記 / 3 整合 / 4 輸出 / 5 製作 / 6 發布 / 7 監控）
2. **哪條 line / 哪個 agent 受益？**（對照 Lines × Stages + Agents × Stages 矩陣）
3. **跟現有 stage gap 的順序合不合？**（vs CONTENT-PIPELINE.md 4 個結構性觀察 + 優先序建議）

無法 anchor 在七層內 → 屬於 infrastructure / tech debt 另一條 lens，需明確標示「這不是內容流程功能、屬於 X」讓修修確認是否真要排。

**Why**：修修 2026-05-04 自陳「之前開發的順序太 spontaneous，沒有節奏，這裡弄一個那邊弄一個，現在還串不起來，系統越來越大但連不上 = 不好的現象」。沒 anchor 的開發會持續累積無法串接的 feature，最終堆出系統但跑不出 end-to-end 流程。

**How to apply**：
- 修修問「我們來做什麼 / 接下來開發什麼 / 有什麼可以做」 → 主動把候選對應到 stage，不接受「散著挑」
- 我自己提建議 → 必標 stage 編號 + 對應 Lines/Agents 矩陣 cell
- 修修點某 feature 想做 → 反問「這在 stage X，會 unblock Line Y / Agent Z，但現在 stage A 缺口更大，順序對嗎？」必要時 push back
- CONTENT-PIPELINE.md 4 個結構性觀察優先級高於零散 feature；推非優先項要明確說「我知道這不在 top 3 但...」
- 矩陣有 cell 變化（新模組 ship / 缺口補上）→ 同步更新 CONTENT-PIPELINE.md 對應 cell，避免文件腐爛

**禁止**：
- 列「下一步可以做的事」清單卻不分 stage
- 順著修修的 spontaneous 提議直接動工不檢視架構迴路
- 只看「這個 feature 好不好」不看「這個 feature 有沒有把架構接起來」
