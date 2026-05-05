---
name: 實作型 skill 不要 checkpoint，drive to completion
description: 修修在 /tdd / /to-prd / /to-issues / 多 cycle 實作中明確要求「全部交給你繼續進行，不要再問我了」
type: feedback
created: 2026-05-05
---

實作型 skill 跑起來後（/tdd cycles / 多 slice ship / 多 commit），**不在中段 checkpoint 問「要繼續嗎」/「要 commit 嗎」**。drive 到自然完成點（PR 開好、tests 全綠、issue 更新）再回報。

**Why:** 修修 CEO+PM 角色，能跑就直接跑、不要 friction（[feedback_run_dont_ask.md](feedback_run_dont_ask.md) + [feedback_no_handoff_to_user_mid_work.md](feedback_no_handoff_to_user_mid_work.md) 同精神）。Cycle-by-cycle checkpoint 讓他每幾分鐘要切回來 ack 一次，破壞 flow。2026-05-05 Slice 1 #389 跑 Cycle 1-3 後我問「continue 還是 commit」，修修明確 push back：「全部交給你繼續進行，不要再問我了」。

**How to apply:**

- **Architectural decisions（grill phase）→ 仍逐題 ack**（grill-with-docs skill 的精神，one question at a time，這條不變）
- **實作 phase（tdd / to-prd / to-issues / slice ship）→ batch through，不 checkpoint**：
  - 每 cycle 簡短 RED/GREEN 進度報告 OK
  - **不問**「要繼續嗎」「要 commit 嗎」「要開 PR 嗎」
  - 自然斷點（cycles 全綠 / module 完成 / slice ship-able）才 batch commit + push + PR
- **碰到真 blocker**（test 跑不過想不通 / interface 設計疑問 / scope 大幅偏離 grill 結論）才停下問
- **不可逆動作**（destroy data / force push / 刪 branch）仍照 CLAUDE.md「risky actions」原則停下 ack

## 區分「該停」vs「不該停」

| 情境 | 該停問 | 該繼續 |
|---|---|---|
| Cycle 1-N 連續跑，每 cycle test 都綠 | ❌ | ✅ |
| 寫 commit message + push branch | ❌ | ✅ |
| 開 PR for slice | ❌ | ✅ |
| 更新 issue 進度 comment | ❌ | ✅ |
| 連續 3 cycle 同 test 沒過 | ✅ 要停問 | ❌ |
| Interface 設計遇到 grill 沒拍的歧異 | ✅ 要停問 | ❌ |
| 偏離 grill 結論的 scope 變動 | ✅ 要停問 | ❌ |
| 跨 PR / 跨 branch / 跨 slice 的決定 | ✅ 要停問 | ❌ |
| force push / git reset --hard / 刪 branch | ✅ 強制停問（CLAUDE.md 風險原則） | ❌ |

## 相關
- [feedback_run_dont_ask.md](feedback_run_dont_ask.md) — 能跑就直接跑不問
- [feedback_no_handoff_to_user_mid_work.md](feedback_no_handoff_to_user_mid_work.md) — grill 完→我接手做完→修修最後驗收
- [feedback_minimize_manual_friction.md](feedback_minimize_manual_friction.md) — 每個手動步驟 = 摩擦力
