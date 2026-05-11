---
name: 最高指導原則 — 一切可 dispatch 的工作都必 dispatch（不接手）
description: 修修 2026-05-04 凌晨明文 declared 最高指導原則。所有可委派的工作（code edit / review fix / conflict resolve / refactor / rebase）一律 dispatch sub-agent 或 sandcastle，main context 嚴禁親手做。違反 = session token 失控 (520k breach 教訓)。
type: feedback
created: 2026-05-04
---

修修 explicit declaration 2026-05-04 凌晨：「所有能 dispatch 的都一定要 dispatch 出去給 sandcastle 做。這是之後開發的最高指導原則。」

**Why**：2026-05-04 Stage 1 ingest session 燒到 520k tokens（修修 budget 上限 250k 的 2x+）。token 主要燒在「我接手」反射 — Slice 1 review fix 親手做 (~80-100k)、Slice 2 rebase + URLDispatcherConfig slot refactor 親手做 (~80-100k)、Slice 4 agent killed 我接手 finish (~30k)、5 PR CI watch + merge cycle (~30k)。如果嚴守 dispatch 原則可降到 ~150k。

修修觀察：「採用 sandcastle 就是為了避免這種情況」。這個原則存在的原因就是 main context 是稀缺資源，必須只用在「決策 / orchestration / final approval」上。

**How to apply（dispatch 觸發點 — 反射動作改成 dispatch，不是接手）**：

| 情境 | ✗ 燒 main context | ✓ Dispatch |
|---|---|---|
| Sub-agent killed / partial work | 我讀檔 + finish + commit + push + open PR | dispatch 新 agent w/ P9 prompt「從 worktree 現狀接手，commit/push/PR」 |
| Reviewer 找出 blockers / majors | 我讀 reviewer report + read 涉及檔案 + edit + test + push | dispatch fix agent w/ reviewer findings 當 input + acceptance「修齊 push」 |
| Rebase 衝突 / merge conflict | 我 git merge + read conflict + edit + add + continue | dispatch agent w/ 兩 branch + 衝突解決規則 + tests-pass acceptance |
| Refactor / rename slot 跨多檔 | 我 grep + edit 多檔 + 跑 pytest | dispatch agent w/ rename rule 描述 + 涉及 file list + tests-pass acceptance |
| 多 reviewer report 彙整 | 我讀 3 個 1500-token reports + summarize | reviewer 用 `gh pr review --comment` 寫到 PR + 只 return summary「N blockers」 |
| CI watch + serial merge cycle | 我 watch + manual merge + verify + repeat ×5 | 全 `gh pr merge --auto`，CI 綠 GH 自動 merge |

**Main context 唯一合法用途**：

1. 決策（要不要 dispatch / 要不要 merge / 走 plan A vs B）
2. Dispatch agent prompt 寫作（P9 六要素 task prompt）
3. 看 final PR diff + reviewer summary 決定 merge
4. 跟修修溝通（status report / 開放問題 / 拍板）
5. Memory / PRD / ADR doc 寫作（這類 main context 必須的）

**反向觸發 trigger**：每當我 reflex「我接手 / 我手動 / 我親手」時 STOP + reframe 成 dispatch。

**例外（真的只能 main context 做）**：

- 寫 dispatch agent 的 P9 prompt 本身（決策性質）
- Memory file 寫作（必須讀 session context 才能寫）
- gh pr merge / gh pr update-branch 等單命令 git 操作（dispatch overhead 大於命令本身）
- Quick grep / sanity check（< 5 個 tool calls 內完成）

**自我 audit checkpoint**：

- ❌ 「我讀 X 檔來決定怎麼修」→ ✓ dispatch agent 讀 + 修
- ❌ 「我跑 pytest 看哪 fail」→ ✓ dispatch agent 跑 + 修
- ❌ 「我 resolve 這個 merge conflict」→ ✓ dispatch agent
- ❌ 「我 grep 確認 caller」→ 可短期主 context（< 5 calls），長期 dispatch
- ❌ 「我 commit / push / PR open」→ 可主 context（單命令）；如果是 long sequence (commit + push + open PR + comment) → dispatch

**關連 memory**：

- [feedback_partial_agent_recovery](feedback_partial_agent_recovery.md) — agent killed 先 inspect 再決定。本原則 supersede：inspect 後仍 dispatch，不接手
- [feedback_no_handoff_to_user_mid_work](feedback_no_handoff_to_user_mid_work.md) — 不 handoff 給 user。本原則補充：也不 handoff 給自己（main context）
- [feedback_minimize_manual_friction](feedback_minimize_manual_friction.md) — 減少修修手動。本原則補充：也減少 main context 手動
- [feedback_quality_over_speed_cost](feedback_quality_over_speed_cost.md) — 品質 > 速度 > 省錢。Dispatch 原則跟品質不矛盾：sandcastle agent 品質 = main context 品質（同 model），但 token 成本低 50%+
- [reference_sandcastle](reference_sandcastle.md) — sandcastle 試水 3/3 通過。本原則 promote 它從「適合的 issue 才用」到「能 dispatch 都 dispatch」
