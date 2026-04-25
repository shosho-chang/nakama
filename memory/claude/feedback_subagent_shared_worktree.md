---
name: 並行 sub-agent 必開 git worktree，不可共用主 worktree
description: 並行 review/test sub-agent 在主 worktree 跑會互踩 branch state；必須每個 agent 一個 `git worktree add`
type: feedback
originSessionId: 74954c3c-fdc6-4064-931b-fe3250a7d804
---
並行 sub-agent 跑 `gh pr checkout` 或 `git checkout` 時必須在獨立 worktree；共用主 worktree 會 race。

**Why:** 2026-04-25 review 5 個 quality uplift PR 並行跑 sub-agent，每個各自 `gh pr checkout`，互踩主 worktree branch state。最後主 worktree 停在 `pr157-review` branch、staged 了 #154 的 6 個檔、stash 一堆 WIP；textbook ingest untracked 檔被誤 stash。需要事後手動 reset + drop stash + 移檔還原。`feedback_dual_window_worktree.md` 涵蓋同機雙視窗，但沒明寫 sub-agent 也算「同機多視窗」。

**How to apply:**

- 派並行 sub-agent 跑會切 branch / `gh pr checkout` / `gh pr diff` / `pytest` 的工作前，先 `git worktree add ../nakama-review-pr<N> origin/<branch>`，prompt 內 `cd` 到那個 worktree
- 純 read-only 工作（讀檔、`gh pr view --json`、grep 主 worktree 內容）不需要 worktree
- Sub-agent 跑完，主 process 回收 worktree：`git worktree remove ../nakama-review-pr<N>`
- 沒有 worktree 隔離時，sub-agent 不可被授權做 `git checkout` / `gh pr checkout` / `git stash` / `git reset`；prompt 要明確寫「只 read，不切 branch」
- 同樣適用本機 reviewer 跑 ultrareview 之外的 review：必開 worktree，不靠口頭協議

**踩過的具體症狀（2026-04-25）：**
- `git status` 顯示莫名其妙的 staged 檔（其他 PR 的）
- 主 branch 自己會切到 `pr<N>-review` 一類臨時 branch
- `git stash list` 多出 4 個 sub-agent 留下的 WIP stash
- 原本主 worktree 的 untracked 檔（textbook ingest 那 3 份）被誤掃進 stash
- Sub-agent 報告開頭出現「Branch keeps drifting — there's clearly a parallel worktree or something」這種困惑
