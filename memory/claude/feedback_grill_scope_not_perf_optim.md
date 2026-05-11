---
name: scope freeze grill 不該問 perf optimization 問題
description: scope freeze grill（凍結 feature 範圍）不該問「每週用幾次」這類 perf optimization 問題；on-demand async 個人工具數量不影響架構，這類題目該砍
type: feedback
created: 2026-05-04
---

scope freeze grill 跟 perf optimization grill 是兩回事，不能 stack。on-demand async 個人工具（修修 paste URL、按按鈕 trigger）的「每週用量」對架構無影響：

- 單篇處理（一次 paste 一個）
- 翻譯成本 cache 不解決重複問題（同一篇修修不會再 paste）
- batch 沒用（不會一次 paste 10 個）

只有 cron / 大量 batch / cache 設計 / SLO 才需要估數量級。

**Why:** 2026-05-04 Stage 1 ingest grill Q1 我問「每週 ingest 幾篇？10/30/100+？」修修 push back「我不懂這個數字為什麼是重要的？這個的使用情境是 paste URL → 抓 + 翻 → 存清單 → 有空讀 → annotation → ingest，我不懂這樣子的流程，為什麼要先估數量？」— 我把 perf optimization 反射性套到 scope freeze；違反 [feedback_minimize_manual_friction](feedback_minimize_manual_friction.md)（不必要的 grill 是 friction）

**How to apply:**

- scope freeze grill 題目 default 是「**使用者行為的分歧點**」、「**設計選擇要凍住的判斷**」、「**invariant 要驗證的 assumption**」
- 「每週幾次 / 多大 / 多快」這類數量題只在 cron / batch / cache / SLO design 場景才問
- 寫 grill 題目前先問自己：「**這個答案會改變我的 design 嗎？**」如果 default 答案不影響 design → 砍題
- 對 on-demand async 個人工具特別敏感 — 這類使用 default 是 single-user / single-action / 數量級不影響
