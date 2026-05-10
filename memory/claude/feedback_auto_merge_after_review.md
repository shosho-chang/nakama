---
type: feedback
visibility: claude
agent: claude
confidence: high
created: 2026-05-10
expires: permanent
name: PR review pass 後 auto-merge，不要再問
description: User explicit standing authorization：跑 review skill → 修 bug → 沒問題就直接 merge，不要再問「要不要代 merge」
---

User 明確 standing authorization 2026-05-10：

> 「以後不要問我要不要 merge 了。只要呼叫 review 的 skill，把 bug 修好之後，沒問題你就自己 merge 吧，不用再問我了，拜託。」

**Why**: 反覆問「要不要 merge」對 user 是 friction，特別在 docs/chore PR 走 paths-ignore 無風險時。User 期望 review pass 後一路自動到底。先前 PR #532 + PR #539 都被我多問一次，user 第二次就受不了。

**How to apply**:

- 任何 PR 我或 sandcastle 寫的 → 跑 `code-review` skill（或同等深度 review）→ 過 ≥80 score 的 issue 全修 → 再 review 確認 → CI 全綠 → **直接 `gh pr merge --squash --delete-branch`**，inline 報告即可，不要問
- 例外（仍要問）：
  - PR 觸碰 production data / shared infra / 外部使用者可見功能 / 跨 agent 資料 schema 破壞性變更
  - Review 發現 ≥80 score 的 issue **無法自動修**（需人類判斷或設計討論）
  - PR 來自外部 contributor（非 user / 非 sandcastle / 非我）
  - User 在 PR 開出後明確要求「先不要 merge」
- Memory commit 走自己的 worktree + 直 push main（paths-ignore），不需 PR；不適用此規則
- 報告格式：merge 完一句話 link 到 commit hash + 一句「下一步」即可，不冗
