---
name: Multi-agent review 用 3-lens（correctness / test-gap / API-design）並行替代 ultrareview
description: PR review 走 3 parallel general-purpose agent，分工 correctness / test-gap / API-design，isolation worktree 防 leak；PR #320 實證找到 2 blocker + 6 major
type: feedback
created: 2026-05-02
updated: 2026-05-02
---

PR review 走 multi-agent 替代 ultrareview（per `feedback_no_handoff_to_user_mid_work` 不 pause-for-approval）— 3 個 parallel general-purpose agent，每個負責不同 lens。

**Why:** ultrareview 是付費 + 整 session friction；單 reviewer 的 finding 容易缺一面；3-lens 切角加上 isolation worktree 跑 zero leak 風險。實證：PR #320 抓到 2 blocker + 6 major（其中 fps cross-lang drift / mistake_removal silent loss / stub style 三個 finding 是 2 reviewer 獨立 convergence，high signal）。

**3 lens 分工**：

| Lens | Focus | Skip |
|---|---|---|
| Correctness | bugs / spec compliance / cross-language schema consistency / race-state | style / test gap / API design |
| Test-gap | acceptance vs test coverage / edge cases / mock realism / stub testing | production correctness / API design |
| API-design | extensibility for downstream slices / premature concretization / public surface naming / aesthetic-of-code | bug correctness / test gap / TS/Python style nits |

**How to apply:**

1. PR diff 大、跨 stack（Python + TS）、新 module 或第一個 slice 的 PR 適用；focused PR <100 LOC 直接 /review 就好
2. Spawn 3 個 `Agent(subagent_type="general-purpose", isolation="worktree")` 同 message 並行（單 message 多 tool call）
3. 每個 prompt 給：
   - PR # + 必讀 docs（PRD / ADR / plan / 相關 acceptance bullet）
   - 該 lens 的 focus list（幾個 numbered 問題）
   - 該 lens 的 skip list（避免 reviewer 重疊）
   - Output format：severity (blocker / major / minor / nit) + file:line + issue + suggested fix
   - 「If clean, say 'no issues found' explicitly per CLAUDE.md 窮盡一切」
   - 「pwd first to confirm worktree isolation」
   - 「Don't write files」
4. 整合 findings 找 convergence（多 reviewer 抓同 issue = high signal）
5. 修 blocker + major 在 commit 內，minor 看 risk 決定 inline 修還 follow-up
6. PR comment post review summary（lens 表 + 解決對映 + deferred follow-up）

**Worktree cleanup pitfall**：reviewer 即使 prompt 寫「don't write files」，agent 仍可能 `git checkout pr ref` 觸發 worktree retain → locked 狀態。`git worktree remove --force` / `unlock` 都可能被 deny rule 擋（destructive ops）。預防：reviewer prompt 強調「pure read-only via gh pr diff / gh pr view，no git checkout」。

**戰績**：PR #320 — 25 → 35 tests + 8 findings resolved in 1 commit + 1 follow-up list；耗時 ~3-5 min/agent parallel + ~30 min implement。
