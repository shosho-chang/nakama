---
name: 並行 sub-agent 優先（時間 > token 成本）
description: 獨立任務盡量並行跑 sub-agent 節省時間；worktree 要注意 main checkout 污染
type: feedback
---
**規則：獨立任務優先用並行 sub-agent 跑，不要序列跑。修修重視時間勝於 token 成本。**

**Why:** 修修 2026-04-17 明確說「最缺的就是時間，花的 Token 倒是還好；如果能夠讓工作在短時間內一次搞定，我寧願花比較多的錢來達成這件事」。當天並行跑 PR #15（unlink 修復）+ PR #16（cookie 改名）兩個 tech debt，比序列快 ~2x，體感差很多。

**How to apply:**

1. **獨立任務優先並行**：若 N 個任務互相獨立（不同 scope/檔案、沒依賴），用 N 個 `Agent` 呼叫放在**同一個 message** 並行跑，各自 `isolation: "worktree"`。
2. **Code review 流程也是**：5 個 Sonnet reviewer + 多個 Haiku eligibility/summary 都該同 message 並行。
3. **Worktree 陷阱（2026-04-17 此次學到）**：兩個 agent 同時開 worktree 時，agent 的 edit 可能意外落在主 checkout (`f:\nakama`) 而非自己的 worktree。兩者會互相蓋到。下次 SOP：
   - 開並行 agent 前先確認主 checkout 工作目錄乾淨（`git status` 無 modified file）
   - 在 prompt 明確告訴 agent「operate inside your worktree path, not the main repo」
   - 若預期衝突風險高，考慮讓其中一個先完成再開另一個
4. **不適用場景**：任務間有資料依賴、需要中途修修決策、或只是硬把一個任務拆成假平行 → 序列就好，不要 premature parallelism。
5. **成本心態**：不用省 token，多跑幾個 Haiku/Sonnet agent 換時間划算。但也不要無意義擴編（例如 3 個 reviewer 就夠的場景不要開 10 個）。
