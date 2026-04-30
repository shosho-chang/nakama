---
name: gh pr merge / gh issue close 在 nakama .claude/settings.json deny list — auto-flow 必有 host 端手動環節
description: 兩個 gh 寫操作被 deny；自動 review→merge 流程到 merge 那步必停下，sandcastle container 內也擋；用 PR body `closes #N` auto-close 替代 issue close
type: feedback
created: 2026-04-30
---

`Bash(gh pr merge *)` 和 `Bash(gh issue close *)` 顯式 deny 在 nakama `.claude/settings.json`。意思：

- 我跑 `/review PR#N` 結尾打 `gh pr merge` 會被擋 → 必停下、報告使用者手動 `! gh pr merge ...`
- Sandcastle container 內 agent 跑 `gh issue close N` 也擋（CLI 受同 settings 影響）→ agent 留 PR body `closes #N`，靠 GitHub auto-close

**Why:** 修修保留 merge / close 為手動 gate — review/merge/close 是 high-blast-radius 操作，不要 auto。歷史上 review skill 結尾會自動 merge，因此 deny 是必要 stopper。

**How to apply:**

1. 看到 `Bash(gh pr merge *)` 或 `Bash(gh issue close *)` 拒絕 → **不要重試、不要繞道**（用 `gh api` 寫 issue close 也不行 — same intent）
2. 直接告訴使用者「sandbox 擋我，請你 `! gh pr merge <N> --squash --delete-branch`」
3. 對 sandcastle / 任何 AFK runner：prompt 預先教 agent 用 PR body `closes #N` auto-close，不要試 `gh issue close`（節省 retry 浪費）
4. memory 提到 `feedback_pr_review_merge_flow`「自動 review → 自動 squash merge」是 idealized；nakama 實際是「自動 review → **使用者手動** squash merge」

**相關記憶：**
- [feedback_pr_review_merge_flow.md](feedback_pr_review_merge_flow.md) — 完整 PR flow（含 idealized 步驟）
- [reference_sandcastle.md](reference_sandcastle.md) — 試水時踩到 4 號坑
- [docs/runbooks/sandcastle.md](../../docs/runbooks/sandcastle.md) — 寫進 SOP
