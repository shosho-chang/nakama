---
name: 同 working tree 開 dev server + 我做 git 動作會踩到
description: 修修跑 uvicorn --reload 在主 tree 時，我做 git checkout / force-push 後切 branch / stash pop 會 mtime 觸發 reload，dev server load 到「不對的版本」，瀏覽器顯示跟程式邏輯不同步
type: feedback
---

修修在主 tree 跑 `uvicorn thousand_sunny.app:app --reload` 等 dev server 時，我（claude）做 git checkout / `git checkout main -- file` / force-push 後切 branch / `git stash pop` 等動作 → file mtime 變動 → uvicorn auto-reload → server 跑「不對 branch 的程式碼」→ 瀏覽器看到的跟我程式邏輯期望不同步。

修修可能誤以為「我寫的 feature 沒實作」，實際是 working tree 切到別的 branch 了。

**Why**：2026-04-25 PR #141 拆 paste-image。我 force-push 後 `git checkout main` 把主 tree reader.html 重置為 main 版。修修 uvicorn `--reload` 偵測 mtime 變動 reload main 版 template → 瀏覽器 refresh 看到 meta-pills 沒顯示，以為 PR feature 壞掉。實際我 push 的 PR HEAD 是對的、只是修修的 dev server 看到的不是 PR HEAD 的 working tree。

跟 [feedback_dual_window_worktree.md](feedback_dual_window_worktree.md) 同根（working tree 是共享資源，多動作來源會互踩），但不同 root cause：dual-window 是 git 行為（未 commit 改動跨 branch 跑），這條是 uvicorn 行為（mtime watch + auto-reload）。解法也不同。

**How to apply**：

我做 git 動作前的 checklist：

1. **問或檢查 dev server 在不在跑**：
   ```bash
   ps aux | grep -E "uvicorn|fastapi|next dev|vite" | grep -v grep
   ```
   或直接問修修「你 uvicorn 還在跑嗎？」

2. **有 dev server 跑 → 走 worktree 操作**（推薦）：
   ```bash
   git worktree add /tmp/nakama-tmp-2026-04-25 <branch>
   # 在 /tmp/... 做 git 動作 + Edit / Write 用絕對路徑
   git -C /tmp/nakama-tmp-2026-04-25 add ... && git -C ... commit ... && git -C ... push
   git worktree remove /tmp/nakama-tmp-2026-04-25
   ```
   主 tree 完全沒動 → uvicorn 不會 reload。

3. **沒辦法 worktree（緊急 hotfix）→ 跟修修同步**：
   - 動之前說「我要動 working tree，先停 uvicorn 或保留現狀」
   - 動完明確說「OK 主 tree 在 X branch，可以 reload」

4. **`uvicorn --reload` 警覺事項**：
   - mtime 變動 = reload，不是 file 內容變才 reload
   - `git checkout` 動到 file → reload 觸發
   - `git stash pop` 帶回改動 → reload 觸發
   - `git worktree add` 不影響原 tree → 不 reload（這是用 worktree 的關鍵）

5. **下次 PR review/merge flow 走 worktree**：force-push / amend / rebase 動作多時，預設用 `git worktree add /tmp/...` 隔離操作，不要直接在主 tree 動。
