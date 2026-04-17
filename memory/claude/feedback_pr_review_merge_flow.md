---
name: PR 開完後的標準 review + merge 流程
description: 開完 PR 後主動跑 code-review skill，給摘要報告，等修修授權再 squash merge
type: feedback
originSessionId: b5636c88-2145-418f-b98f-ef364ae150df
---
**規則：PR 開完 → 自動跑 code-review skill → 給摘要報告 → 等修修說 merge → squash merge → 切回 main pull + 刪 feature branch**

**Why:** 修修想減少重複指令（每次都要說「跑 review」「幫我 merge」很煩），但 merge 到 main 是破壞性動作，需要保留一個 checkpoint 讓他看 review 結果再決定。

**How to apply:**

1. **Review 嚴格度**：只有 blocker（bug / 安全漏洞 / 破壞現有行為）才停下討論；nit 和 style suggestion 不打斷修修
2. **自動程度**：Review 完**給報告**，等修修說 merge 再執行（不自動 merge）
3. **Merge 策略**：squash（符合 repo 現有 history 風格）
4. **Merge 後動作**：
   - `git checkout main && git pull origin main`
   - 刪除 feature branch（local + remote）
5. **PR 後續**：把待測試事項更新到 `project_pending_tasks.md`

**例外情況需暫停問修修：**
- Review 發現 blocker → 問要在 PR 內修還是另開
- Reviewer 提出架構級建議 → 屬於設計討論，不是該 PR 的 scope
