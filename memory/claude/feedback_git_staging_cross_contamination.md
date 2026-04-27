---
name: git add specific-paths 不 isolate staging；多視窗開發 commit 前必 git status 確認 staging clean
description: 多 worktree / IDE 並行時 staging area 可能已有別處 stage 的 file；`git add file1 file2` 不會清掉它們，commit 會把它們一起帶走
type: feedback
originSessionId: f576b513-b012-4b87-a689-d42f09413728
---
**規則**：多 worktree / 多視窗 / 多 IDE 並行開發時，commit 前必先 `git status --short` 確認 staging area 乾淨；如果有 unrelated file，先 `git restore --staged <unrelated>` 清掉再 `git add` 自己的 file。

**Why:** 2026-04-27 V1 acceptance commit (PR #189) 預期 stage 兩個 V1 file，commit 結果含 **12 file**：兩個 V1 file + 10 個無關 SEO file（修修在另一視窗 IDE 已 stage 的 in-progress work）。發現後做 `git reset HEAD~1` + `git restore --staged .` + `git add` 只兩個 V1 file + force-push-with-lease 才修好。force push 雖然 feature branch low-risk，但仍是 destructive、可避免。

根因：`git add path1 path2` 只**新增** path1/path2 進 staging，**不會清掉** staging 已有的 entries。多 IDE 並行時，VS Code / Obsidian 等可能有 auto-stage 行為（或 user 在另一視窗 stage 了東西沒 commit）。staging area 是 repo-global，不是 cwd-local。

**How to apply:**

- **Commit 流程**：
  1. `git status --short` → 看 staging area（前一個字元 = staging，後一個 = working tree）
  2. 如果 staging 有 unrelated file：`git restore --staged <unrelated>` 或 `git restore --staged .`（清全部）
  3. `git add <my-files>` 只 add 自己這次要 commit 的 file
  4. `git commit -m "..."`
  5. `git status --short` 再看一次確認 working tree state（修修 in-progress work 仍應 unstaged dirty 留著）
- **避免**：跑 `git add .` 或 `git add -A` 在 main worktree（會帶進別人 dirty work）
- **適用範圍**：所有 multi-worktree / multi-IDE 開發場景；尤其當 commit 涉及 main worktree（不只是當前 worktree 的 isolated branch）。
