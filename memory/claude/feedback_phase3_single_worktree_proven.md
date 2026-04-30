---
name: Phase 3 single-worktree sequential AFK 實證可行（不需 sandcastle）
description: 2026-04-29 SEO Web UI 完善 4 slices in 1 worktree sequential 2h50m 全綠交付，nakama 規模不需 sandcastle 多 worktree 並行
type: feedback
created: 2026-04-29
---

不裝 sandcastle、用單一 worktree 序列跑 4 vertical slice 是 nakama 規模 Phase 3 AFK 的合適模式。

**Why:** 2026-04-29 跑 PR #260 (SEO Web UI 完善 A′+B′+E) 4 slices in single worktree `F:/nakama-afk-seo-web-ui`，2h50m 完成、28 new tests / 155 全綠 / 0 worktree leak / 0 regression。Matt 的 sandcastle 是 multi-worktree 並行 + Docker isolated + Opus reviewer，對 nakama 規模 setup overhead > 收益（見 [reference_sandcastle.md](reference_sandcastle.md)）。

**How to apply:**
- Phase 3 默認用「單 worktree + feature branch + 序列 slice + 1 PR」，不要急著架 sandcastle
- AFK 前提：grill→PRD→to-issues 走完，issue 規格穩定（Slice 0-3 各 acceptance 清楚）
- 序列依 dependency chain（PR #260 是 0→1→2→3，沒實際並行；Slice 2/3 雖可並行但 conflict 風險高於收益）
- 上限 ~3h；超過要重看 issue 規格是不是太厚
- Sandcastle 真要架時機 = 「issue queue 累積 10+ AFK-ready 我來不及解 + 反正都要 review」
- 安全網仍要：worktree 隔離 + `feedback_worktree_absolute_path_leak.md` 防線 + settings.json hard guardrails（`ssh *` / `gh pr merge *` / `git push origin main*` 全 deny）
