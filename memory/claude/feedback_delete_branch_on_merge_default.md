---
name: feedback-delete-branch-on-merge-default
description: 新 GitHub repo bootstrap 時必開 deleteBranchOnMerge；否則每次 PR merge 後 remote branch 殘留
metadata:
  type: feedback
---

新 GitHub repo bootstrap 或接手既有 repo 時，**必先確認** `deleteBranchOnMerge: true`：

```bash
gh repo view --json deleteBranchOnMerge
# 若 false：
gh repo edit <owner>/<repo> --delete-branch-on-merge
```

**Why:** 2026-05-12 在 shosho-chang/nakama 發現 default `false`，導致 #563–#569 七條 merged PR 的 head branch 全部殘留在 origin。每次 PR squash-merge 完都需要手動 `git push origin --delete`，多人多 worktree 工作流下很容易忘掉、累積 stale branch 污染 `git branch -r` 與 `--prune` 噪音。

**How to apply:**
- 任何新 repo 第一次接觸時，跑 `gh repo view --json deleteBranchOnMerge` 確認，false 就立刻 edit
- 接手既有 repo 看到 `git branch -r` 有一票對應 merged PR 的殘 branch，先檢查這個設定，再清理
- 清理殘 branch：`git push origin --delete <b1> <b2> ...` + `git fetch --prune`
- 設定打開後 local 端 fetch 會自動 prune 跟著清，不必再手動刪
