---
name: PR 開完後的標準 review + merge 流程
description: 開完 PR 後主動跑 code-review skill，依 PR 類型分級：低風險自動 merge，高風險給報告等授權
type: feedback
originSessionId: b5636c88-2145-418f-b98f-ef364ae150df
---
**規則：PR 開完 → 自動跑 code-review skill → 依 PR 類型分級決定是否自動 merge**

**不要再問「要不要跑 review」** — 2026-04-20 修修再次確認 review 授權給我自己跑，不用先問。只有 merge 這一步才要停下等授權（依分級）。

**Why:** 修修想減少重複指令，但 main 幾乎等同 pre-prod（VPS 從 main pull 部署）。PR #13 是前車之鑑：review 沒擋下 auth 被鎖在 Robin router 裡，merge 後 VPS restart 直接弄壞 Brook。所以用**分級**而非二元：低風險完全自動，高風險保留人工 checkpoint。

**How to apply:**

### 分級規則

| PR 類型 | 自動 merge | 說明 |
|---|---|---|
| 純文件（.md/docs） | ✅ 直接 | 文件錯可 follow-up 修 |
| 純測試（只改 tests/） | ✅ 直接 | 不影響 runtime |
| 單檔 bug fix + 對應測試，且**不碰** auth / router / schema / `.env` / VPS 部署路徑 | ✅ 直接 | 小範圍 + 測試覆蓋 |
| 小 refactor（行為不變、有測試綠） | ✅ 直接 | 無行為變更 |
| 動 auth / router / schema / 新依賴 | ❌ 給報告等授權 | 風險 blast radius 大 |
| 跨 agent / 動 VPS 部署相關 | ❌ 給報告等授權 | 影響共享狀態 |
| > 300 行或 > 5 檔 | ❌ 給報告等授權 | 大 PR 認知成本高 |
| Review skill 找到任何 confidence ≥ 80 的 blocker | ❌ 停下來討論 | 不管類型一律停 |

### 共通流程

1. **Review 嚴格度**：只有 blocker（bug / 安全漏洞 / 破壞現有行為）才停下；nit 和 style suggestion 不打斷修修
2. **Merge 策略**：squash（符合 repo 現有 history 風格）
3. **Merge 後動作**：
   - `git checkout main && git pull origin main`
   - 刪除 feature branch（local + remote）
4. **PR 後續**：把待測試事項更新到 `project_pending_tasks.md`
5. **自動 merge 後**：在對話中明確告知「已 auto-merge，類型：<X>」讓修修知道發生了什麼

### 例外情況需暫停問修修

- Review 發現 blocker → 問要在 PR 內修還是另開
- Reviewer 提出架構級建議 → 屬於設計討論，不是該 PR 的 scope
- PR 類型模稜兩可（例如 refactor 但改到 router 邊界）→ 保守走人工授權
