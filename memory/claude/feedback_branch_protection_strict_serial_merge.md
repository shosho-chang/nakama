---
name: GH branch protection strict mode 序列 merge cycle 痛點
description: nakama main 設 strict + enforce_admins + auto-merge disabled，每 squash merge 一條後其他 PR base 變 outdated 必須 cycle update-branch + wait CI + merge；--admin 也擋
type: feedback
created: 2026-05-04
---

修修升 GitHub Pro + main branch protection 開了：

```json
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["lint-and-test", "lint-pr-title"]
  },
  "enforce_admins": {"enabled": true},
  "required_linear_history": {"enabled": true}
}
```

加上 repo 層級 **Auto merge: disabled**（`gh pr merge --auto` 回 `enablePullRequestAutoMerge` 拒絕）。

**Why strict + serial merge 是痛點**：
- `strict: true` 表示 base branch 必須 up-to-date with main 才能 merge
- 每 squash merge 一條 PR → main advance → 其他 PR 的 base ref outdated
- 下一條 squash 嘗試會 fail：`GraphQL: 2 of 2 required status checks are expected. (mergePullRequest)`
- `gh pr update-branch <num>` 把 PR base 拉到最新 main，**但會 trigger CI re-run**（lint-and-test ~60-90s）
- N 條獨立 PR 序列 merge = N×（update + wait + merge）cycle
- `--admin` flag 沒用（`enforce_admins: true`）

**How to apply**：

對 ≥2 條獨立 PR 一起 ship 時：

1. **第一條直接 squash merge**（base 還對齊）
2. **其他全部 batch `gh pr update-branch`**（並行，不 wait 個別 CI）
3. **用 background until-loop poll 全部 CLEAN**：
   ```bash
   until [ "$(gh pr view N --json mergeStateStatus -q .mergeStateStatus)" = "CLEAN" ] && \
         [ "$(gh pr view M --json mergeStateStatus -q .mergeStateStatus)" = "CLEAN" ]; do
     sleep 15
   done
   ```
   配 Bash `run_in_background: true`，等 callback 後一次 squash merge 一條
4. **squash merge 後下一條 base 又 outdated** → 對下一條 update-branch + poll + merge，重複

5 PR session 實證 cycle：merge #334 → merge #331 + #332 + #333 + #335 序列（每條 update + wait CI + squash），三輪 cycle 才全 merge。每輪 wait CI ~60-90s。

**避免方法**：
- 修修可以 **disable strict mode**（保留其他 protection）— 但 strict mode 防 untested-against-latest-main 是有 value
- 或 **enable auto-merge at repo level**（GH settings → Pull Requests → Allow auto-merge）— 不 disable strict 仍可 batch 設 auto-merge 然後不管
- 或 **不開 PR 直接 commit main**（chore memory hygiene 類），但這違反 [feedback_branch_workflow.md]

**錯誤訊息對照**：

| 訊息 | 真因 |
|---|---|
| `GraphQL: 2 of 2 required status checks are expected. (mergePullRequest)` | strict mode + base outdated；run `gh pr update-branch` |
| `GraphQL: Auto merge is not allowed for this repository (enablePullRequestAutoMerge)` | repo settings → Pull Requests → "Allow auto-merge" 沒開 |
| `mergeStateStatus: BLOCKED` | required check 未過 / required review 未到 / strict outdated 多種可能；看 contexts 細節 |

**相關**：
- [reference_github_plan_branch_protection.md](reference_github_plan_branch_protection.md) — GitHub plan & branch protection 限制
- [feedback_pr_review_merge_flow.md](feedback_pr_review_merge_flow.md) — focused PR 自開直接 squash merge 流程
- [feedback_minimize_manual_friction.md](feedback_minimize_manual_friction.md) — 摩擦力最小原則（這個 cycle 是 5 PR 一次需要 cycle，每條 1-2 min waste）
