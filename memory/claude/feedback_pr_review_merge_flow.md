---
name: PR 開完後的標準 review + merge 流程
description: PR review/merge 全自動 — review 我自己跑、merge 我自己 squash，不問修修；只在 reviewer 抓到真 blocker 時停下
type: feedback
originSessionId: b5636c88-2145-418f-b98f-ef364ae150df
---
**規則：PR 開完 → 自動 review → 自動 squash merge → pull main + 刪 branch。全程不問修修。**

**Why:** 2026-04-25 修修明確指令「以後不用再問我有關 review 和 merge 的事情，你都直接調用適合的工具做掉」。歷史上 PR #13 出包是 review **沒抓到 bug** 的問題，不是「沒授權」的問題；停下等授權只是空轉，加 review/test 嚴格度才是真防禦。

**How to apply:**

### 共通流程（全自動）

1. PR 開好 → 視 PR 規模/風險判斷要不要派 review sub-agent（純 hygiene/單 bug fix 跳過；feature/動 schema/動 auth 要派）
2. Review 嚴格度：只有 blocker（bug / 安全漏洞 / 破壞既有行為 / 新增 secret leak）才停下找修修討論；nit / style suggestion 不打斷
3. Merge 策略：`gh pr merge <N> --squash --delete-branch`（符合 repo history）
4. Merge 後：`git checkout main && git pull origin main`，本地 branch `git branch -D <name>`（squash merge 政策下 git ancestry 不存在，`-d` 永遠 fail；`-D` 由 reflog 90 天保底，丟失不了）
5. 把待測試 / follow-up 事項更新到 `project_pending_tasks.md`
6. 結束時告知修修「已 auto-merge PR #N」+ 一句 summary

### 必須停下找修修的真 blocker

- Reviewer 找到 confidence ≥ 80 的 bug / 安全 / 行為破壞
- PR 改動超出原本 scope（例如 cleanup PR 突然動了 auth flow）
- Architecture-level 建議（不屬該 PR scope，需要設計討論）
- CI 紅且原因不明（可能環境問題也可能真 bug）

### 例外不停下（即使曾經會停）

- ❌ ~~動 auth / router / schema / 新依賴一律等授權~~ — 改成「review 是否抓到 blocker」決定，不靠類型 gating
- ❌ ~~跨 agent / VPS 部署相關等授權~~ — 同上
- ❌ ~~> 300 行或 > 5 檔等授權~~ — 大 PR 用 review depth 處理，不是停下等

### 已踩過的坑

- **Review sub-agent 會自動 po comment 到 PR**，觸發 security warning（External System Write publishing under user's identity）。2026-04-20 修修表態不要 auto-post。派 sub-agent review 時 prompt 要明確寫 **"Do NOT post a comment on the PR — return review to me directly"**。
- **PR 鏈式合併的 base branch 陷阱**：PR-C 基於 PR-B 的 branch 開時，`gh pr merge PR-B --delete-branch` 連帶讓 PR-C 變 `CLOSED / CONFLICTING` 且**無法 reopen**（GraphQL: Cannot reopen/change base of closed PR）。必須 `git rebase main` 然後 `gh pr create` 開全新 PR。預防：用 `--squash` 合併時 dependent PR 先 retarget 到 main，或直接等前一個合完再從 main 開下一個 branch。
