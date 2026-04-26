---
name: 同機雙視窗開發必用 git worktree
description: 同台機器開兩個 Claude Code 視窗並行時必須 `git worktree add` 開獨立 working tree，純切 branch 會跨 branch 互相覆蓋未 commit 改動
type: feedback
originSessionId: 441cbaea-dbe6-4280-98c8-956dd98584cb
---
兩個視窗在同一個 working tree 各自 `git checkout -b feat/X`、`feat/Y` 時，**未 commit 的改動會跟著切 branch 一起跑**——A 改 file1.py 沒 commit、B 切 branch、file1.py 顯示在 B 的 `git status` 裡，再 commit 進 B 的 PR。事故跟「branch 隔離」的直覺剛好相反。

**正確流程**（同機雙視窗）：

```bash
# 主 tree（Window A）留在原地
# Window B 開獨立 worktree
git worktree add ../nakama-window-b feat/window-b-branch
# Window B 在 IDE / terminal cd 到 ../nakama-window-b 工作
```

兩個 working tree 共用 `.git`、PR 推同一個 remote，但檔案、未 commit 改動、checkout branch 完全分離。結束時 `git worktree remove ../nakama-window-b`（或留著重用）。

**Why**：2026-04-25 兩視窗在 nakama 同 dir 各自 checkout、commit 跨 branch 互踩；解套要手動 stash + 開 branch + cherry-pick + 重切 branch 才把 SEO Slice C 跟 Bridge mutations 兩堆改動分到對的 PR（#139 / #140）。

**How to apply**：
- 同機開第二個視窗前，**第一件事就是 `git worktree add`**
- 跨機器（Mac vs 桌機）走 [feedback_multi_machine_parallel.md](feedback_multi_machine_parallel.md) — 各自 clone、用對方 open PR file list 挑零重疊任務
- 寫雙視窗交接 prompt 時，§開工 checklist 必含「`pwd` 確認在獨立 worktree」「`git status` clean 才動手」「commit 前 `git diff main --stat` 對齊範圍」
- 範例：[docs/task-prompts/2026-04-25-dual-window-allocation.md](../../docs/task-prompts/2026-04-25-dual-window-allocation.md)、[docs/task-prompts/2026-04-25-window-b-kb-search.md](../../docs/task-prompts/2026-04-25-window-b-kb-search.md)

**相關但不同 root cause**：[feedback_shared_tree_devserver_collision.md](feedback_shared_tree_devserver_collision.md) — 同 working tree 跑 dev server 時，我做 git checkout 會 mtime 觸發 uvicorn reload 看到「不對 branch 的程式碼」。同樣用 worktree 解。

**跨 Claude session 也適用（2026-04-26 踩到）**：兩個 Claude session 同時開在同一 repo working tree，A session 已 `git add -A` staged 一批改動準備 commit，期間 B session 在不同 prompt 跑 `git add -A` + `git commit` — **A 的 staged 改動會被吃進 B 的 commit**（git index 共享）。事故點：B 的 commit message 跟 scope 完全錯（B 只想 commit 自己的改動，但 commit stat 包含 A 的 8 個 SEO 檔）。解套：B push 上 origin 後不可 force-rewrite；A 要在新 branch 重做 PR 並標明 race condition，留給 future review/merge 自然處理（內容 identical 時 git auto-merge 不衝突）。**正確預防**：開第二個 Claude session 前先 `git worktree add` 隔離 — 跟人類雙視窗同樣對策。
