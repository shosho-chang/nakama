---
name: reference_no_jq_use_gh_builtin
description: 這台桌機沒有裝 jq；Monitor 腳本 pipe 給 jq 會靜默空轉，要用 gh 內建的 --jq
metadata:
  type: reference
---

**修修的桌機沒有 `jq`**（`bash: jq: command not found`）。`gh` 有自己內建的 jq
引擎，所以 `gh ... --jq '...'` 可以用，`gh ... | jq '...'` 不行。

**Why**：2026-09-15 盯 PR #1269 的 CI，連續兩個 Monitor 跑滿 30 分鐘**零事件**。
不是 CI 沒動，是腳本裡每一輪的 `jq` 都 command not found → 變數空 → 判斷永遠不成立。
外層又寫了 `2>/dev/null || true` 把錯誤吞掉，於是它看起來像一個正在運作的 monitor，
實際上什麼都沒在看。**沈默跟「還在跑」長得一模一樣**，這正是 Monitor 最危險的失敗
模式。

**How to apply**：
- GitHub 相關的輪詢一律用 `gh <cmd> --json <fields> --jq '<expr>'`，不要 pipe 給 jq
- 另一個坑：**`gh pr checks` 在還有 check 待跑時 exit code 是 8**，所以
  `s=$(gh pr checks ... ) || { sleep; continue; }` 會每一輪都跳過。要 `|| true`
- 寫 Monitor 前先在 Bash 裡單獨跑一次整條 pipeline 確認有輸出，再掛上去
- 這條 CI workflow（lint-and-test）**正常就是 75–78 分鐘**，別把它當成卡住
