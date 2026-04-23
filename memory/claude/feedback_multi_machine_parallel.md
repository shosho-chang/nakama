---
name: Mac ↔ Windows 桌機零衝突並行開發 workflow
description: 多機器同時開 Nakama 開發時，用 open PR 的 file list 做 conflict 分析，挑 zero-overlap 任務維持兩邊 steady PR stream
type: feedback
created: 2026-04-23
confidence: high
originSessionId: 749ec7ee-40b5-4729-9a94-d76671f993e2
---
**規則**：當 Mac + Windows 桌機（或任兩個環境）同時工作時，先查對方 open PR 的 file list，只挑與之 **file path 無 overlap** 的任務做；不要 waste time rebasing / merging stacked branches。

**Why**：2026-04-23 下午兩邊並行時驗證有效 — 桌機做 PR #77 Usopp Slice B（7 檔新 + state.py / docs），Mac 做 5 個零衝突 PR（#79/#80/#81/#82/#83）都成功 squash merge，零 rebase / 零 conflict resolution。對比 `feedback_stacked_pr_squash_conflict.md` 記的 stacked branch 痛點 — 零衝突分工 > stacked PR 鏈。

**How to apply**：
1. 每次 Mac session 起手先跑 `gh pr list --state open` + `gh pr view <N> --json files` 確認對方動哪些檔
2. 列「Mac 可動」清單，排除對方觸碰的 file path（包括 module + 同檔的 test）
3. 優先做**新檔案**任務（完全 zero conflict）> 小範圍 edit > 大範圍 refactor
4. 若任務得動對方也在動的檔 → 暫不做、放 backlog、等 merge 後再挑
5. 每個 PR 獨立 branch / review / merge / delete — 不堆 stack

**Nakama 2026-04-23 實作**：
- 桌機：`feature/usopp-slice-b`（PR #77）touches `agents/usopp/publisher.py` + `shared/compliance/*` + `shared/seopress_writer.py` + `shared/litespeed_purge.py` + `shared/state.py` + migration
- Mac 挑：`config/style-profiles/` + `shared/gutenberg_validator.py`（新檔）+ `shared/schemas/publishing.py`（非 #77 scope）+ `shared/approval_queue.py`（非 #77 scope）
- 驗：5 PR / 5 merge / 0 conflict

**相關**：
- `feedback_branch_workflow.md` — 每人 feature branch 的核心規則
- `feedback_pr_review_merge_flow.md` — 每 PR 走 review → auth → merge
- `feedback_stacked_pr_squash_conflict.md` — 如果真要 stack，squash 會讓子 PR 變 unmergeable，盡量避免
