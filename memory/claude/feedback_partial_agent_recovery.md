---
name: Killed agent 的 partial work 先 inspect 再決定，常常 near-complete 可直接接手
description: 2026-04-29 SEO #229 agent 被 kill 留下 539+/40-未 commit 工作，inspect 後發現是 near-complete（23 tests 綠 + ruff 綠 + 結構正確），手動 commit + 開 PR 比重派還快
type: feedback
created: 2026-04-29
confidence: high
originSessionId: 2026-04-29-seo-中控台-完工
---

當 background worktree agent 被 kill / stop（status='killed'，summary='Agent was stopped'），不要直接 abandon 重派。**先 inspect partial work 的完成度**，常常已經接近完工。

**Why:** 2026-04-29 SEO 中控台 #229 agent 被 kill（原因不明，可能是修修主動或 system 限制）。worktree 留 539+/40- 未 commit 改動。我去 inspect：

- `shared/wp_post_lister.py` 285 行完整實作（schema / cache / error handling / docstrings 都有）
- `tests/shared/test_wp_post_lister.py` 14 tests
- `tests/test_bridge_seo.py` 9 tests
- 23 tests 全綠 + ruff 全綠

完成度約 95%，只差 commit + push + PR。**手動接手成本 ~5 分鐘，重派 agent 成本 ~10 分鐘 + 浪費 partial work**。

**How to apply:**

1. Agent 被 kill 通知進來時，**先 `git log origin/main..HEAD` 看有無 commit**（若有 commit 就只差 push + PR）
2. **`git status --short`** 看 working tree 改動範圍
3. **`git diff --stat`** 看 lines changed 量級
4. 跑 pytest + ruff 看 partial 是否真的可用
5. 如果 95%+ 完成 + tests 綠：**手動接手** — 加 P7-COMPLETION block 寫進 PR description，commit + push + 開 PR
6. 如果 50-90% 完成：spawn 新 agent，prompt 它「你的 cwd 是 worktree path，那裡有上一個 agent 沒做完的工作，繼續完成」（要謹慎不能用 `isolation: "worktree"` 因為那會建新 worktree）
7. 如果 <50% 完成 / 方向錯：abandon worktree（force remove）+ 重派

**反例**：如果不檢查就 abandon，會浪費 partial work，且 worktree 不清還會占空間 + 留 lock。

**配套**：問使用者為什麼 kill — 可能是看到方向不對。這次修修沒指明原因，inspect 結果方向對就接手。
