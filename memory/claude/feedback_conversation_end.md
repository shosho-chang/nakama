---
name: 對話結束時自動存記憶
description: 使用者說「對話結束」或「清對話」時，主動將重要資訊寫入記憶並 commit & push
type: feedback
---

使用者說「對話結束」、「清對話」、或類似結束訊號時，主動將對話中重要的資訊寫入記憶，並 commit & push。不需要等使用者再提醒。

**Why:** 記憶是跨平台共用的，不存就丟失；使用者不想每次手動提醒。

**How to apply:**
1. 收到「清對話」三個字當作 trigger
2. 回顧對話，挑出值得保留的 feedback/project/reference（判斷標準見 auto-memory 系統 prompt：不存 code patterns / git 可查的東西 / 暫時 state）
3. 寫入 `memory/claude/`，更新 `MEMORY.md`
4. `git commit` + `git push`
5. 簡短回覆「記好了，可以安心清」之類，讓使用者放心清對話
