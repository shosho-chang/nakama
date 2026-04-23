---
name: Stacked PR squash merge 後底層 PR 繼承的 unmergeable 陷阱
description: 基層 PR squash-merged 後，堆疊在它之上的子 PR 繼承 pre-squash 的 parent commits，GitHub 視為 unmergeable；非 force-push 解法是 merge-main-into-branch + checkout --ours
type: feedback
tags: [git, pr-workflow, stacked-pr, squash-merge]
originSessionId: d6248f62-150b-43c6-ac05-98394223e172
---
堆疊 PR（PR B 從 PR A 的 branch 分出）+ PR A 用 **squash merge** 進 main → PR B 立刻變 `UNMERGEABLE`，因為 PR B 的 commits 還帶著 pre-squash 的 PR A commits 作 parent，和 main 上的 squash commit SHA 不一致。

**Why:** squash 合併只重現 diff，不保留 commit 身份。PR B 的歷史裡有 A1/A2/A3 commits，main 的歷史只有一個 squash commit。GitHub 對 PR B 重跑 merge 時看到衝突（文件在兩邊都被加過）。

**How to apply:**

- 最乾淨：`git rebase --onto main <last-slice-A-commit> <slice-B-branch>` + `git push --force-with-lease`。但在沒 force-push 權限時走不了。
- 沒 force-push 權限的退路：**`git merge main --no-edit`** 進子分支，衝突用 `git checkout --ours <file>` 解（子分支永遠是 main 的 superset），commit merge，普通 push。最後 `gh pr merge --squash` 的 squash commit 會把雜亂歷史壓平，main 歷史仍乾淨。
- 驗證策略選對：如果子分支的所有衝突檔案「our size > their size」就放心用 ours；不對勁才一個檔案看 conflict marker 手動挑。
- 順序很重要：每次 merge 完 main，下一個堆疊 PR 才開始跑 merge 解衝突 → 才有新 base 可用。
- 案例：PR #74/#75/#76（Franky Phase 1 三 slice）2026-04-23 就是踩這個，第二招解掉沒 force-push。
