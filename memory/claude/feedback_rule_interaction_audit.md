---
name: 加 deny rule / 「不要 X」guidance 前必審互動
description: 安全規則 + 既有政策疊加會產生 silent dead-end；squash merge × `-d` × `-D deny` 是 6 天 stale branch 累積的原型案例
type: feedback
created: 2026-05-01
---

加任何 deny rule 或「不要 X」型 guidance 前，必先審它跟既有政策的互動 — 三條獨立 reasonable 的規則疊起來常會堵死合法工作流程，且 fail silent（沒 error、沒警告，只是任務悄悄推不下去）。

**Why:** 2026-05-01 發現本機累積 7 個 stale branch 6 天沒清，根因是三條規則疊加：
1. **Squash merge 政策**（`feedback_pr_review_merge_flow.md` step 3）— PR merge 後 ancestry 在 main 不存在
2. **`feedback_pr_review_merge_flow.md` step 4 寫「用 `-d` 不要 `-D`」** — `-d` 只在有 ancestry 時 work，squash 後永遠 fail
3. **`Bash(git branch -D *)` 在 deny list** — 唯一 fallback 被擋

每條獨立看都合理：squash merge 乾淨 history、`-d` 比 `-D` 安全、`-D` 跟 `reset --hard` 同一破壞性等級。但**疊起來 = 沒任何路徑能清 stale branch**。發現是因為視覺噪音累積到 7 個才被注意到，不是 error log。

**How to apply:**

加 deny rule 或「不要 X」guidance 前跑 3 題 audit：

1. **誰需要 X？** Grep 既有 memory + runbook 找呼叫 X 的工作流程。如果有，問 (2)。
2. **替代路徑全條件 work 嗎？** 不只看 happy path — 列舉所有觸發條件確認替代仍有效。範例：`-d` 在 squash merge 條件下失效，這條件是 repo 預設政策。
3. **錯了的 recovery cost 多少？** 如果 deny rule 太嚴，從失效到發現要多久？視覺噪音（如 stale branch）vs 工作流程卡住（如部署失敗）成本天差地別。

**對應的 fail mode 警訊**（看到下列徵兆時懷疑規則互鎖）：
- 同類東西累積但沒 error（stale branch、untouched TODO、未 review PR、過期 memory）
- 修修偶發手動清理但沒寫進 hook / runbook（暗示沒人記得它存在）
- 文件條文邏輯上互斥（如 step 4「用 -d 不要 -D」+ step 3「用 squash merge」）

**呼應原則：**
- [feedback_permission_setup.md](feedback_permission_setup.md) — deny 永遠蓋過 allow，加 deny 先查 allow 子集會不會被誤殺
- [feedback_pr_review_merge_flow.md](feedback_pr_review_merge_flow.md) — squash merge 政策的 cleanup 配套
