---
name: 5/7 FrankyEvolveV1 — session 2 handoff（review + fix loop 完成，等 merge + 後續 slice）
description: 4 個 PR (#479/#480/#481/#483) review 已 post + fix 已 push；S2b/S3/S4 等 merge 後派工，必走 Sandcastle
type: project
created: 2026-05-07
---

## TL;DR

5/7 session 2：跑 4 個 PR 的 code-review skill → 提 issue → 修 → push fix。S1 (#472) 也 ship 完（從 cd-quirk 污染中救回）。下一步：等 merge + 派 S2b/S3/S4，**全走 Sandcastle**。

## 4 個 PR 當前狀態

| PR | branch | latest commit | review issues | fix commit |
|----|--------|---------------|----------------|------------|
| #479 | feat/franky-evolve-v1-spec | a0a7aea | 1: 缺 feedback_sandcastle_default.md 檔 | a0a7aea（ship 該 file） |
| #480 | feat/franky-s2a-context-snapshot | d7a327b | 2: subprocess 沒 timeout / 3 處 stale ADR-022 | d7a327b（timeout=30s + ADR sweep） |
| #481 | feat/franky-s5-proposal-metrics | b22d0ea | 2: yaml.YAMLError 沒包 / mark_wontfix 覆蓋 baseline_source | b22d0ea（FrontmatterParseError + guard 只在 NULL 時寫） |
| #483 | feat/franky-s1-source-expansion | 395f44b | 2: 沒 GITHUB_TOKEN auth / 6 處 stale ADR-022 | 395f44b（_github_api_headers helper + ADR sweep） |

review comment 連結（給人看用）：
- #479#issuecomment-4395612780
- #480#issuecomment-4395644104
- #481#issuecomment-4395665180
- #483#issuecomment-4395699904

## 5/7 兩支 cd quirk 事故

S1 + S5 sub-agent dispatched via local Agent tool `isolation:worktree`，兩支都中相同的 cd quirk — sub-agent Bash cwd 不真釘 worktree，commit 落主 repo working tree（S5 自爆，S1 直接把 E:/nakama 從 main 切到 feat/franky-s1-source-expansion）。

別兩個視窗（E:/nakama-stage4 = docs/kb-stub-crisis-memory；E:/nakama-adr021 = rebase-N456）有獨立 worktree，**沒被污染**（已驗 working tree clean）。

教訓寫進：
- `CLAUDE.md` 新加 §AFK / 並行 dispatch 派工規則（commit 84c7d0d on PR #479）
- `memory/claude/feedback_sandcastle_default.md`（commit a0a7aea on PR #479）
- `memory/claude/feedback_dual_window_worktree.md` 加 5/7 quirk 註記

## ADR 編號改動

原 ADR-022 撞 main 既有 ADR-022-multilingual-embedding-default.md → rename ADR-023-franky-evolution-loop.md（PR #479 commit 437bbfa）。後續各 PR 的 stale ref sweep 完成。

## 下一步（fresh session 起手）

### 1. 等 4 個 PR review pass + merge

修修不 self-review。review skill 已跑完並 post 結論到 PR comment。如果還要再跑一輪 review（針對 fix commit），重派 `code-review:code-review` skill。

### 2. Merge 順序

`#479 → #480 (deps 無但要先 merge ADR) → #483 (S1 無 deps) → #481 (S5 無 deps)`。S2a/S5 ship 後 unblock S2b/S3/S4。

### 3. 派 S2b (#475) / S3 (#476) / S4 (#477) — **必走 Sandcastle**

S2b 等 S2a (#473/#480) merge 後可派。S3 等 S2b。S4 等 S3 + S5 (#474/#481)。

派工 prompt 範本（hard rule：sandcastle，不要寫成「sandcastle 或 local agent worktree」）：

```
Sandcastle template B (OAuth flow)
- 本 handoff 路徑 + ADR-023 路徑 + issue body 完整 acceptance criteria
- 完工 PR 自動跑 review skill
```

### 4. VPS deploy 前置

- `GITHUB_TOKEN` env var 必須設（PR #483 fix 假設）
- shared/cron_wrapper.py 仍未 wire — Franky 既有 cron job 都沒走 wrapper，新加的 context_snapshot 也跟現有 pattern。out of scope until 修修拍板要不要回頭做

## 這條線剩下沒做的（next-session 待辦）

1. **`settings.json` deny rule** — 在 E:/nakama 主 repo 上擋 `git checkout` / `git commit` 寫入。CLAUDE.md 已加文字規則，但沒 hard guard。建議加 deny entry：
   ```
   "deny": ["Bash(cd E:/nakama && git checkout *)", "Bash(cd E:/nakama && git commit *)"]
   ```
2. **`memory/claude/MEMORY.md` index 更新** — 加 `feedback_sandcastle_default.md` + `project_franky_evolve_v1_2026_05_07_session2_handoff.md` entry。沒做因為 MEMORY.md 在別視窗 modified 不能動 — fresh session 等別視窗 commit / push 後再動
3. **Gemini panel audit 補跑** — `GEMINI_API_KEY` 沒設，deferred
4. **`E:/nakama/.claude/worktrees/franky-s1-fix` 清理** — S1 PR merge 後 `git worktree remove`
5. **6 個 issue 的 sandcastle 派工** — S2b/S3/S4，按上面 §3 順序

## 主 repo 當前狀態（fresh session 接手要知道）

- `E:/nakama` 上次 active branch = `feat/franky-s1-source-expansion`（雖然我中段 checkout main 過一次但被切回；fresh session 應 `git checkout main` 確認，但這操作可能會被 deny — 看 settings.json 設沒設 hard guard）
- `git stash list` 還有 4 個歷史 stash，跟本 session 無關，不要 drop
- 4 個 worktree 都 locked + alive：franky-evolve-spec / agent-ae36f718bd9c563bf / agent-ad8152471cd8ac88a / franky-s1-fix

## References

- 前一個 handoff：[project_franky_evolve_v1_2026_05_07_handoff.md](project_franky_evolve_v1_2026_05_07_handoff.md)（session 1 — ADR draft + 6 issue 開好 + 派工）
- ADR-023：`docs/decisions/ADR-023-franky-evolution-loop.md`（PR #479 only，未 merge main）
- 5/7 兩支 cd quirk 事故記錄：上面 §「5/7 兩支 cd quirk 事故」+ `feedback_sandcastle_default.md` + `feedback_dual_window_worktree.md`
