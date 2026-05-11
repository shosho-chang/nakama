---
name: 修修不做 PR review，Claude dispatch review + 過關後自動 merge
description: PR ship 完 Claude 主動 dispatch review skill 跑 review；no blocking issue 就直接 squash-merge，不等修修。Blocking issue / CI 紅 / conflict 才 halt 報修修
type: feedback
---

修修不做 code review，也不做 merge 決策。PR ship 完 Claude 走完整 review-and-merge loop：dispatch review → 看結論 → 過關自動 merge → 報修修一句話結果。

**Why**：修修明確說過「我不做 review」+ 2026-05-11 加碼「review 完不用等我決定就可以 merge」。等修修 = PR 卡死 + 修修做 bottleneck。Repo 有現成 review skill（`code-review:code-review` Claude 可叫；`/ultrareview` user-triggered + billed Claude 不可叫）+ Agent 工具能 spawn 多模型 panel。

**Auto-merge gate**（全部 ✅ 才 merge）：
- `code-review:code-review` skill 跑完，**no blocking issue**（confidence ≥ 80 的 bug / security / 行為破壞 才算 blocker；nit / style / docstring drift 不停下）
- `gh pr checks` 全綠（或 paths-ignore 無 required check）
- 無 merge conflict（`mergeable: MERGEABLE`）
- 非 stacked PR base 怪異（base 必為 main 或明確 review 過的 feature stack）

**Halt 條件**（任一即停 + 一句話報修修，不 merge）：
- review 出 ≥1 blocking issue
- CI 紅
- merge conflict
- 戰略級變動（ADR / 跨 agent contract / 改 CLAUDE.md 規則）— 即使 review 過，建議跑 multi-agent panel 或 `/ultrareview`，**等修修點頭**

**How to apply**：
- 任何 PR ship 完默認下一步是 dispatch `code-review:code-review`，不是「等修修看」
- 過關直接 `gh pr merge --squash --delete-branch`，一句話報結果（PR # + merge SHA + 一句 review 結論）
- 多 PR 批次處理時 review 平行 dispatch，merge 串行（避免衝突）
- Memory-only PR（`memory/**` 全包）可 skip review 直 merge — review 對 markdown frontmatter 沒 added value
- 戰略級 PR 或 CLAUDE.md 改動，review 過也建議 surface 給修修點頭再 merge
