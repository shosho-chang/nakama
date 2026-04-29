---
name: worktree absolute-path leak — agent Edit/Write 用絕對路徑會繞過 worktree 隔離
description: 派 worktree-isolated agent 時 agent 若用 `F:/nakama/...` 絕對路徑 Edit/Write 會直接寫進 main tree 不是 worktree；2026-04-29 SEO Slice 1 + Slice 8 兩 agent 都踩到
type: feedback
created: 2026-04-29
confidence: high
originSessionId: 94a66141-8b16-4e9a-9762-b9d9c43f1695
---
派 sub-agent 走 `isolation: "worktree"` 時不要假設 agent 會自動把所有檔案路徑限制在 worktree 內。實測 agent 用 `Edit(file_path="F:/nakama/migrations/005_gsc_rows.sql")` 這類絕對路徑會**直接寫到 main tree 裡同名路徑**，繞過 worktree 隔離。

**Why:** 2026-04-29 派 SEO Slice 1（PR #237）+ Slice 8（PR #238）兩個 worktree-isolated agent 平行做 SEO 中控台。Slice 1 結束 self-report 才發現「我的 Edit 一開始用絕對路徑也寫入了 main tree」；同時 Slice 8 也漏了一份檔案到 main tree。Slice 1 的事後清理用 `git reset --hard HEAD` 把 working-tree 改動全擦掉，**順便吃掉了 ch2 ingest cache 一份未 commit 的 reindex**（與這 PR 完全無關），reflog 救不回（reset --hard 不留 dangling blob 給 working tree）。

**How to apply:**

1. 派 agent prompt 內**明確要求用 worktree 內的相對路徑或 worktree 根目錄起的絕對路徑**，不要從 main tree 起：「You're in a worktree at `F:/nakama/.claude/worktrees/agent-XXX/` — all Edit/Write paths must start there, not `F:/nakama/...`」
2. agent 結束後 main tree `git status` 必看 — 如果有 untracked / modified 檔案，先 stash 看一下是不是 leak
3. 清 leak **永遠不用 `git reset --hard`** — 用 surgical `git checkout -- <leaked-files>` 或 PowerShell 回收桶 untracked file，避免吃到無關的 working-tree 改動
4. agent self-report 提到「path mishap / 絕對路徑 / 主 tree 裡也寫了」要立刻在 main tree 比對 reflog 看有沒有非預期的 reset/checkout 動作
5. 跨 agent 派發前 main tree 工作中改動 commit 或 stash，不要留 working-tree dirty 跑 worktree 平行 — 一旦 agent leak 進來再清就有風險

**相關記憶**：
- [feedback_worktree_session_hygiene.md](feedback_worktree_session_hygiene.md) — worktree 用主 tree venv 絕對路徑 + 收尾 fetch 不 checkout main + bash cwd 不持久（這條的補充：venv 路徑可絕對，但**檔案路徑要相對 worktree**）
- [feedback_dual_window_worktree.md](feedback_dual_window_worktree.md) — 同機雙視窗開發必用 worktree
