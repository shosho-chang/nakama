---
name: Worktree session 跑 Python tools + 收尾流程
description: worktree 共用 git 不共用 .venv；收尾 merge 後別動主 tree branch state（multi-window 並行考量）
type: feedback
originSessionId: c198744a-b7d8-4cd5-8365-cbc26b1fd742
---
**規則一：worktree session 跑 Python 工具用主 tree venv 絕對路徑。**

例：`/Users/shosho/Documents/nakama/.venv/bin/python -m pytest tests/skills/kb_search/`

**Why:** `git worktree add` 共用 `.git` 但不共用 `.venv`/`node_modules`/`__pycache__` 等不在 git 內的東西。worktree 內裸跑 `python` 會用 `/usr/bin/python3` 拿不到 `httpx` / `pytest` / `pytest-asyncio` 等專案 dep；自己在 worktree 重裝 venv 是浪費。

**How to apply:**

- `pytest` → `/Users/shosho/Documents/nakama/.venv/bin/python -m pytest`
- `ruff` → `/Users/shosho/Documents/nakama/.venv/bin/python -m ruff check`
- 該 worktree 對應 cwd：`cd /Users/shosho/Documents/nakama-window-b && <絕對 venv 路徑> -m ...`
- 不要在 worktree 內 `pip install` 或建獨立 venv

---

**規則二：worktree session 收尾流程不動主 tree 的 branch state。**

PR squash-merged 後（按 `feedback_pr_review_merge_flow`）：

1. `gh pr merge <N> --squash --delete-branch`（remote merge + delete branch；local branch delete 會失敗因 worktree 還持有，正常）
2. **離開 worktree**（cwd 切到主 tree 或別處），跑 `git worktree remove /Users/shosho/Documents/nakama-window-b`
3. `git branch -d feat/<name>`（worktree 釋放後 local branch 才能刪）
4. `git fetch origin main`（**不要** `git checkout main && git pull`）

**Why:** Multi-window 場景下，主 tree 可能正在另一個 branch 上工作（例：Window A 在 `feat/robin-reader-ui-polish`）。`git checkout main` 會把主 tree working tree 強切走、搶掉 Window A 的 in-flight 改動。`git fetch origin main` 只更新 `origin/main` ref，主 tree HEAD 不動。

**How to apply:**

- 永遠先 `git worktree list` 看主 tree 在哪個 branch
- 如果主 tree 在 main：可以直接 checkout + pull（單視窗安全）
- 如果主 tree 在別的 branch：fetch only，留給對方 session 自己 sync main
- worktree session 報告 merge 完成時，明確告訴修修主 tree 沒被動

---

**規則三：shell cwd 在 worktree 不持久。**

Bash tool 每次 call 之間 cwd reset。從 worktree 操作必用 `cd /Users/shosho/Documents/nakama-window-b && <command>` 或全絕對路徑。

**Why:** Claude Code Bash tool 是無狀態的 — `cd /path/to/worktree` 後下一個 Bash call cwd 已經回到 spawn 點。

**How to apply:** 任何 worktree session 內的 bash command，前面都要 `cd <worktree> &&`。
