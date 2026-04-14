---
name: 多視窗開發用 feature branch + PR
description: 開多視窗平行開發時，各自開 feature branch 再 PR 回 main
type: feedback
created: 2026-04-14
updated: 2026-04-14
confidence: high
ttl: permanent
---

多視窗平行開發時用 feature branch + PR，不直接在 main 上改。

**Why:** 2026-04-14 eval 測試中，子 agent 直接改了生產程式碼（384 行寫進 4 個檔案），汙染了 main。Branch 天然隔離這個風險。修修確認同意這個做法。

**How to apply:**
- 開始新任務：`git checkout -b feat/xxx main`
- 完成後：`git push -u origin feat/xxx` → `gh pr create`
- PR 前跑 `/code-review`
- 純記憶更新或小修改可以直接在 main
