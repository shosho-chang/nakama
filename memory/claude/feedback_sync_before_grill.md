---
name: 跨 session 工作前必先 sync git + GH state
description: 修修可能在另一個視窗 / 前一晚已動工同一個 task；新 session 起手必查 git log main + gh issue list，避免重複 grill 重複開 issue
type: feedback
created: 2026-05-01
---

修修 2026-05-01 Line 1 podcast repurpose grill session：我跑完整輪 grill + /to-prd + /to-issues 後才發現修修在前一晚（同日 01:14-01:19 Taipei）已經跑過同一輪 grill、merged PRD via PR #281、開了 master issue #283、Slice 1 也 ship 了 PR #280。我重複開了 master #282 + Slice 1 #284，又用 `Write` 蓋掉 main 既有 PRD doc（內容相同，git 視為 clean，但操作上是 redundant）。

**Why:** 修修是內容創作者 + CEO，可能多視窗 / 跨日 work on 同個 task；他自己也可能忘了昨晚跑過 grill 又起一輪（這次正是這個 case）。Cleanup 雖然成本可控（5 min 關 issue / re-parent），但浪費一輪完整 grill = ~30 min context window + 修修 attention。

**How to apply:**

新 session 起手做任何 grill / PRD / 重構 / 設計類工作前，**先跑這 3 條 sync 檢查**：

1. `git -C <repo> log --oneline main | head -10` — 看主幹最近 commits，找跟 task 相關的 PR / commit
2. `git -C <repo> reflog --date=iso | head -10` — 看修修最近的 branch 活動
3. `gh issue list --search "<task keyword>" --state all --limit 20` — 看是否已有 issue / PR 在 track 同個 task

**特別 trigger：**
- 修修說「我們做 X」/「開始 X」/「help me start X」— 不要假設這是 fresh start，檢查是否已動工
- Branch 名跟 task 對得上（如 `feat/transcribe-diarization-restore` 跟 diarization grill）— 是強訊號該 task 已動工
- Memory 裡提到該 task 是 in-flight / unblocked → 一定已有相關 PR

**Sync 失敗的成本對照表：**

| 失敗類型 | 觀察到的成本 |
|---------|---------|
| 重複 grill | 浪費 1 整輪 chat（~30 min context） |
| 重複開 master issue | cleanup 1-2 min + 兩個 issue 視覺污染 |
| 重複開 slice issues | 每個 cleanup 1 min + re-parent 5-10 min |
| Write 蓋掉 main 既有 doc | 如內容相同無損；如不同則需 git restore + 重做 |

**呼應原則：**
- [feedback_run_dont_ask.md](feedback_run_dont_ask.md)：「能跑就跑不問」是針對**已知 scope** 的執行；scope 確認前必 sync
- [feedback_avoid_one_shot_summit.md](feedback_avoid_one_shot_summit.md)：incremental scope 第一步必須是「現狀 audit」，不是直接拍板新方案
