---
name: badge / count UI 不要用 len(list_api(...))，要走專屬 COUNT(*) 函式
description: list API 通常有 LIMIT 預設值（避免 UI 渲染 500 row）；拿它的 len() 算 badge/stat 會在量大時 silently 封頂在 limit，且每 hit 都撈完整 row payload 浪費 IO
type: feedback
originSessionId: 788acb56-5d6f-452b-b1eb-20fdc8a14057
---
**規則：UI badge / stat / counter 算數字時，呼叫 `count_*` 函式（走 `SELECT COUNT(*)`），不要 `len(list_*())`。**

**Why:** PR #136 Bridge `/bridge/drafts` 初版 hub badge 寫 `len(approval_queue.list_by_status("pending"))` — `list_by_status` 預設 `LIMIT 50`，pending > 50 時 badge 永遠顯 50，user 看不到隊伍真實長度，且每次 hit 都從 SQLite 撈 50 row 完整 payload TEXT 只為算 `len()`。Reviewer sub-agent 抓到，PR #137 修：加 `count_by_status()` 走 `SELECT COUNT(*) WHERE status=?`，list 介面留給「真要 render row」的場景。

**How to apply:**

- 寫新的 `list_*(status, *, limit=50)` 介面時，平行加一個 `count_*(status)` — caller 看意圖選擇
- Code review 看到 `len(some_list_func(...))` 在 template/UI/badge context → 信號彈，問「這個 list 有 LIMIT 嗎？」
- 已存在的 list API 在 caller 改 count 用法時，順手補 count helper（不擴散這條 anti-pattern）
- truncate hint 配套：list 真被截斷時（`len(rows) == limit` 且 `count > limit`）UI 要顯示「顯示前 N 筆 / 共 X」banner，別讓 reviewer 以為隊伍只有 50

**踩過的範例**：`shared/approval_queue.py:list_by_status` LIMIT 50 + `bridge.py:bridge_index` `len(...)` badge → PR #137 加 `count_by_status` 修
