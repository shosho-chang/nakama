---
name: sandcastle (worktree isolation) by default
description: 凡是能在 isolated worktree 並行的 implementation slice，必須走 Agent isolation:worktree（sandcastle）— 不要在主 working tree 序列做
type: feedback
---

修修明確要求：「能進 sandcastle 的就一定要進」（2026-05-07 ADR-021/022 implementation 開工時）。

**Why**：
- 多個 issue 沒互相阻擋時，序列執行白白浪費時間
- 主 working tree 一次跑多條 implementation 易污染（亂跨 branch、未 commit 的檔互踩）
- Worktree 自動 cleanup 機制讓「無改動 → 無痕」、有改動則自動回報 path + branch，整潔
- ADR-021 v2 + ADR-022 是同 session 內 11-issue 大批 implementation，sandcastle 並行 = 直接的 wall-clock win

**How to apply**：
1. **Default ON**：dispatch implementation agent 時，永遠加 `isolation: "worktree"`，除非明確「需要先看主 tree 既有 uncommitted state」
2. **Wave-aware**：每 wave 內所有 unblocked issue 同訊息一次派出去，不要序列 — **但見下方並行失效坑**
3. **Briefing 要含**：分支命名規則（建議 `impl/N{issue#}-{slug}`）、issue 號、ADR 路徑、AC 清單、實作邊界
4. **不適用情境**：grill / spike / 單檔 quick fix / docs-only edit / 需要本地 venv 立即跑且 worktree setup 成本不划算的情況

**⚠️ 已知 isolation 失效模式（2026-05-07 三次踩：N456 / N452-fixups / N457）**：

`isolation: "worktree"` 在**並行 dispatch 多個** sandcastle 時，第二（含以後）個 agent 常常 fall back 到主 E:/nakama tree 而非自己的 isolated worktree（task notification 沒回 `worktreePath` 欄位是訊號）。後果：
- 多 agent 互踩主 tree branch（一個 commit 跑進另一個 agent 應該獨立的 branch）
- 主 tree HEAD 被切到不該的 branch（影響 user 跟其他 worktree 的同名 branch 衝突）
- Agent 必須 stash + re-checkout 才能 commit 乾淨

**Workaround（修正前）**：
- **改用 sequential dispatch**：前一個 agent 通知到再派下一個，不要 parallel batch
- 或 agent prompt 開頭明確 inline：「first verify pwd is your isolated worktree (`pwd` should be under `.claude/worktrees/agent-*`); if not, `git stash -u && git fetch origin && git checkout -B <target-branch> origin/<base>` to recover before any work.」
- 即使序列也要在 prompt 強調 `git checkout -B impl/...-rebased origin/<base>` 不依賴 cwd state

