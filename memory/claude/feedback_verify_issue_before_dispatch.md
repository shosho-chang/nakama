---
name: feedback-verify-issue-before-dispatch
description: 派 agent 實作 issue 前先驗 code 是否其實已 ship — issue tracker admin 收尾常落後 code merge
metadata:
  type: feedback
---

開工 issue（特別 needs-triage / 久未動的 open issue）前，先快速驗 code 是否其實已經 ship 完、只是 admin 沒關 issue。同一天 2026-05-24 連抓兩個：

- **#358 Slice 1 follow-up**：5 個 micro-fix 全指向 PR #573 (2026-05-14) 砍掉的 URLDispatcher / `/scrape-translate` / InboxWriter。News Coo Clipper 取代了那條路徑。
- **#540 ADR-024 S10 N518**：整套 6 個 adapter + lifespan wiring + smoke test 已在 #541/#543/#545/#546/#559/#568 全 merged。QA verdict 14/14 pass。

**Why:** issue tracker 的「open」狀態不等於「work not done」。Admin housekeeping（關 issue、發 cross-reference comment）常常拖在 PR merge 之後。盲派 agent 會浪費 token + agent 可能會 fabricate 「對不存在 code 的改動」（#358 micro-PR 那次 agent 抓住沒做）。

**How to apply:**
- needs-triage / 久未動的 open issue 派工前先 30-second verify：
  - `gh issue view <n>` 抓 issue body 內 mentioned 的具體檔案 / function name
  - Grep 該 symbol：production 仍存在 → 真的要做；只剩 doc / memory / historical 引用 → 八成已 ship
  - 對應的 follow-up PR 在 issue 評論裡常有線索 (`#XXX merged`)，或用 `gh pr list --search "fixes #<n>"` 找
- 派 agent 時把這個驗證步驟寫進 prompt 的「Read first」section（讓 agent 自己 fail-fast）
- Agent 報「work already complete on main」是好結果 — 收 admin（close issue + cross-ref comment），不要強推「再做一次」
