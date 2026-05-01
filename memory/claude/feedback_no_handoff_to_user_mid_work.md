---
name: 不經由修修接手 — 最高指導原則（高於品質 > 速度 > 省錢）
description: Grill 完 → 我接手做完 → 修修最後驗收。中間任何要修修手動跑/批准的步驟（ultrareview / pause for approval）都是違規 friction
type: feedback
created: 2026-05-01
---

修修 2026-05-01 對 PR #295 ultrareview 提示拒絕後拍板：**「不用經由我來接手」是比品質更高的指導原則**。

**Why:**
- Grill 階段是修修把需求想清楚的時間（PRD 凍結、acceptance 切細）— 此後不該再被打擾
- ultrareview 要修修手動跑 + 付費 + 等結果，是 friction
- Matt Pocock workflow 也沒有 ultrareview — sandcastle + agent review 就夠
- 修修時間應花在驗收 + 寫內容 + 做生意，不是中間 review gate

**How to apply:**
1. **PR review 走 multi-agent 並行** — 不用 ultrareview。並行 3 sub-agent：behavior/edge case + data/config/schema drift + PRD acceptance 對齊。互補 bias 替代 ultrareview behavioral bias
2. **borderline 別停下問** — 自己判斷重要度修，記錄在 PR description 或 follow-up issue
3. **真 blocker 才停** — 外部依賴 / 設計歧異 / 不可逆操作；用 AskUserQuestion 簡短問
4. **驗收 = 最後一步** — 所有 PR merged + 文件寫完 + 戰況 summary 給修修一次驗收
5. **覆寫品質 > 速度 > 省錢** — 品質仍重要但不拿「我先 ultrareview 確認」當打擾 excuse

**例外**（必須修修做不可避）：
- Production deploy 前 smoke（瀏覽器驗、本機 uvicorn 看 UI）
- 設計決策真有歧異 — 開 `docs/plans/{date}-decisions.md` checkbox 問
- 不可逆操作（force push main / 刪 remote branch / DB migration on prod）

**Cross-ref**：[feedback_minimize_manual_friction.md](feedback_minimize_manual_friction.md) 同源原則升級版；[feedback_quality_over_speed_cost.md](feedback_quality_over_speed_cost.md) 退讓位於本原則之下。
