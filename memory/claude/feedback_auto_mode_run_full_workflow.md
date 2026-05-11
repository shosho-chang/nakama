---
name: Auto mode = orchestrate the FULL Matt workflow, don't park at handoffs
description: 在 auto mode 跑 Matt Pocock workflow（grill → to-prd → to-issues → tdd → sandcastle → review → merge）時，每個 phase 完成後自動接下一個；不要停在「請你執行 X」的 handoff，自己跑。
type: feedback
---

在 auto mode（system-reminder 寫「Execute autonomously, minimize interruptions, prefer action over planning」）下跑 Matt Pocock workflow 時，**每個 phase 完成後自動接下一個 phase**。

具體：

| Phase 完成 | 自動下一步 |
|---|---|
| `/grill-with-docs` 凍結 9 題 | 接 `/to-prd` 寫 PRD |
| `/to-prd` 開 PRD issue | 接 `/to-issues` 拆 slice |
| `/to-issues` 開 slice issues | 接 `/tdd` HITL |
| `/tdd` HITL 完成 commit failing tests | 接 sandcastle dispatch（add label + 跑 CLI）|
| sandcastle 收完 PR | 接 review + squash merge |
| 一個 slice 全 PR merged | 接下一個 slice 的 `/tdd` |
| 最後 slice merged | report done |

**Why**：修修在 auto mode 期望「按 Matt 流程自動跑下去」，不是每個 handoff 都要他動手。2026-05-05 跑 Slice 1 `/tdd #379` 時，HITL 階段做完 + 開 sandcastle sub-issues 後我就停在「修修下一步：跑 `gh issue edit … --add-label sandcastle`」，被糾正「為什麼一直要我動手」。

**How to apply**：

1. **Sandcastle dispatch 我自己做**：
   - 先 sync `.env`（per [feedback_sandcastle_env_drift_check.md](feedback_sandcastle_env_drift_check.md)）
   - `gh issue edit <N> --add-label sandcastle`
   - `cd E:/sandcastle-test && MSYS_NO_PATHCONV=1 npx tsx --env-file=.sandcastle/.env .sandcastle/main.mts`（背景跑）
   - 等通知完成 → `gh pr view` → review → squash merge → fire 下一個 step

2. **PR review + merge 我自己做**：
   - 看 sandcastle 開的 PR diff
   - 跑 tests 驗 GREEN
   - 沒大問題 squash merge；有問題開 review comment
   - 大型 / 跨領域 / 美學敏感 PR 才停下來請修修判斷

3. **Step 之間要 user-confirm 的時機**：
   - sandcastle 連跑兩次都失敗 → 停下來 diagnose
   - PR diff 含 surprising change 超出 issue scope → 停下來
   - 美學 / UX 抉擇（顏色、layout、文案）→ 停下來
   - 跨 slice 邊界（即將進入下一 slice）→ 提醒「即將開始 Slice N+1」並等預設 OK（可短暫等，不要長時間 hold）
   - 涉及刪除 / 不可逆操作 → 停下來

4. **不要做的事**：
   - 寫「請你執行：`gh issue edit ...`」的指令給修修讓他複製貼上
   - 在 phase 結束時用「下一步可以 X」當收尾
   - 等待沒人會給的 approval

**Edge case**：如果 phase chain 很長（5+ slice），每個 slice merged 後可以 status update 一行「Slice N 收尾，進 Slice N+1」就繼續跑，不要 verbose 報告。
