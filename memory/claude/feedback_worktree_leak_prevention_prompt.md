---
name: Worktree-isolated agent prompt 防 leak 兩條防線
description: 派 worktree-isolated agent 時 prompt 加「第一步必跑 pwd + 完工前主 tree git status self-check」，2026-04-29 連續 5 agent 零 leak 驗證實效
type: feedback
created: 2026-04-29
confidence: high
originSessionId: 2026-04-29-seo-中控台-完工
---

派 worktree-isolated agent (`Agent` tool with `isolation: "worktree"`) 時，**只警告「不要用絕對路徑」是不夠的** — 兩天內三個 agent 連環踩同一坑（PR #237 / #238 / #243），最嚴重的一次 reset --hard 清 leak 連帶吃掉 ch2 cache reindex 永久遺失。

**Why:** Agent 在 worktree 內習慣性會用 `F:/nakama/...` 絕對路徑做 Edit/Write，即使 prompt 警告也會犯。光寫「不要絕對路徑」太抽象，agent 不會主動驗證。

**How to apply:** 在 worktree-isolated agent 的 prompt **最前面**加這兩條剛性防線：

1. **第一步必跑 `pwd` + 把輸出當所有 Edit/Write 的 root**，prompt 強調「沒 `.claude/worktrees/...` 的絕對路徑會繞過隔離」
2. **完工 single-message report 前必跑 `cd F:/nakama && git status --short`**，要求 agent 自我斷言「除了 `?? .claude/worktrees/` 之外無輸出」 — 有 leak 自己用 `git checkout -- <file>` surgical 清，**永遠不准 `git reset --hard`**

實證效果（2026-04-29 SEO 中控台 v1 session）：

| Agent | Issue | Leak |
|---|---|---|
| `a2ae9b02` | #230 target keywords | 0 ✅ |
| `a7dabb42` | #232 audit pipeline | 0 ✅ |
| `a9976924` | #233 rank change | 0 ✅ |
| `abcd20d2` | #234 review UI | 0 ✅ |
| `a40fd611` | #235 export queue | 0 ✅ |

**5 連勝零 leak**，前提是同 prompt template。若不加這兩條防線 baseline 是 ~50% 機率 leak（先前 #237/#238/#243 都中）。

詳細範本見 `feedback_worktree_absolute_path_leak.md` + `feedback_no_reset_hard_in_worktree_cleanup.md`（前置教訓）。
