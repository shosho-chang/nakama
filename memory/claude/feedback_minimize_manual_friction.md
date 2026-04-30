---
name: 最高指導原則 — 盡量減少修修手動操作
description: 修修最高指導原則：每多一個手動步驟就是多一個摩擦力，設計時 default 自動化；手動是退路不是預設
type: feedback
created: 2026-05-01
---

修修 2026-05-01 Line 1 grill Q3（diarization）時 explicit 講出：「我的最高指導原則是最好盡量讓我手動操作越少越好，多一個手動操作就是多一個摩擦力。」

**Why:** 修修是 CEO + PM，時間是稀缺資源；他選 WhisperX 內建 diarization 重啟（選項 d）而不是「修修人工手標 SRT」（選項 a）就是這個原則的具體應用。手動雖然 100% 準，但 5-15 分鐘 / 集 × 每集 podcast = 累積摩擦。

**How to apply:**

1. **設計新 flow / pipeline 時 default 自動化**，不要把人類設成預設步驟。例外要論證（accuracy critical / 創意創作 / 主觀判斷）。
2. **「scope 砍掉」決策要看下游 ripple** — PR #273 砍 diarization 當時對的（transcribe scope）但 Line 1 反過來需要 → 結果反悔重做。下次砍 scope 前要列下游 use case 清單再砍。
3. **手動步驟不可避免時，batch / 合併** — 例：SRT 校正本來要做，diarization tag 順手批次標 vs 分開兩道工序。能合併就合併。
4. **不要為了 robustness 加 confirmation step** — 「跑前先 confirm」「每階段都 approve」是反 pattern；用回滾 / dry-run / preview 取代。
5. **Approval gate 設計時，並行 > 階段** — 三 channel 同送審 vs 「blog 先審→過了才生 FB+IG」前者一個 gate、後者兩個 gate。
6. **重要例外 — 創作 voice 不可全自動**：style profile / tone / hook 候選等需要修修品味的東西，AI 提候選讓修修選 / 改是必要的人類介入，不是「摩擦力」。區分清楚。

**對應修修角色其他 feedback：**
- [feedback_run_dont_ask.md](feedback_run_dont_ask.md) — 能跑就跑不問
- [feedback_avoid_one_shot_summit.md](feedback_avoid_one_shot_summit.md) — 採購 / phase / scope 決策前先 reframe 成 incremental
- [feedback_no_premature_execution.md](feedback_no_premature_execution.md) — 但「幫我看一下」≠「幫我做」，六 Phase 交接點要嚴守

三條合起來：**自動化執行 + incremental scope + 守 phase boundary**。
