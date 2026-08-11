---
name: feedback_hitl_at_cheapest_fork
description: 修修的選擇要放在成本最低的分叉點——選項還沒物化前就給他挑，不要做完才問「這樣可以嗎」
metadata:
  type: feedback
---

**產線任何「機器排序 → 挑幾個 → 花大錢物化」的環節，HITL 一律放在物化之前。**

修修 2026-08-11（安吉三支長片全部剪完、補完 stock、正要上傳時）：

> 「我現在有個疑問，我忘了這三個長片是如何挑出來的？⋯⋯目前看這三個，其中有
> 一兩個主題好像不是特別吸引人。如果從五個裡面挑三個，或許會比較好」

**Why**：機器的排序（persona panel）讀的是**逐字稿**，評的是素材強度——不是成片
吸引力，更不是修修的品味。這個落差永遠存在，所以「自動取 top N 直接進製作」
必然會有做完才發現不對的一天，而那時候製作與 packaging 的成本已經付掉了。

成本結構是不對稱的，這才是判斷依據：

- **排序／評審**＝per episode，一次評完 20 個候選，做 3 支或 5 支都一樣
- **製作＋packaging**＝per cut **100% 線性**，且 packaging（標題 7 步 panel＋
  封面 3 張）是 LLM 用量最大的一塊

所以「多做兩支再讓他挑」要付兩支的完整成本；「排完停下來給他挑」幾乎零成本——
那張表的料在 panel 跑完的當下就已經齊了。

**How to apply**：
- 設計任何 skill 的 HITL gate，先問：**這個決定最早可以在哪一步做？** 把 gate 放在那裡，
  不是放在交付前
- 給他選的時候要**連機器的排序、分數、否決理由一起端出來**，並明說「這是素材強度不是
  你的品味」；落選的同群組 variant 也要列，他可能指名那個切法
- 機器的否決（如 brand-lens veto）**可以被他覆蓋**，但不能靜默照做——要警告
- 實作首例：`scripts/run_cut_shortlist.py` + highlight-cut skill Step 2.4（PR #1157）
- 同源紀律見 [[feedback_minimize_manual_friction]]（減少他的手動操作）與
  [[feedback_hitl_gate_serves_subjective_taste]]（gate 服務的是主觀品味，LLM 變強也不會消失）
