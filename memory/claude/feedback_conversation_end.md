---
name: 對話結束 / 清對話 — 雙層記憶處理
description: 「清對話」「對話結束」等訊號 trigger 短命 handoff 檔，不再 trigger durable memory 自動 commit/push。Durable memory 走獨立判斷路徑。
type: feedback
---

使用者說「對話結束」「清對話」「收工」或類似 session 邊界訊號時，**不再**自動寫入 durable memory + commit + push。改成兩層處理。

**Why:** 過去設計把 session 收工 trigger 跟 durable memory 寫入綁在一起，導致每次清對話 → 1 個 PR → 1 次 CI → 燒 GHA quota。實際上 90%+ 「session 收工值得記的事」是短命 handoff context（下個 session bootstrap 用），過 24h 就沒價值。把它跟「跨 session 不變的真理」分開可以解兩個痛點：CI noise + memory pollution。設計依據見 `docs/research/2026-05-08-memory-system-redesign-v2.md`。

## How to apply

### Tier 1：Durable memory（跨 session 不變的事）

不由「清對話」trigger，由 conversation 中的明確訊號觸發：

- **L1 明確 user trigger**：使用者說「記下這個 / save / remember X / update memory」 → 立即寫入相關記憶檔，inline 確認寫了什麼
- **L2 強訊號 auto trigger**：
  - 使用者明確 correction（「不要 X，要 Y」「我說過了」）→ 寫 feedback 類記憶
  - 使用者強烈 validation（「對，繼續這方向」「就這樣繼續」）→ 寫 feedback 類記憶
  - 對話中發現新的 stable user/project 事實（不是 session 細節） → 寫 user/project 類記憶

寫 durable memory 時：
- 寫到 `memory/claude/`（過渡期，未來移到 `memory/{shared,claude}/`）
- 走 commit + push（背後 PR review 不需要因為 memory 走 `paths-ignore` 不跑 CI）
- 不需要等對話結束才寫 — 任何時候有 L1/L2 訊號就立即寫

### Tier 2：Ephemeral session handoff（短命交接 context）

「清對話」/「對話結束」/「收工」**只**寫到 `.nakama/session_handoff_{timestamp}.md`，不進 git：

- **路徑**：`.nakama/session_handoff_{ISO8601_timestamp}.md`（例：`.nakama/session_handoff_2026-05-08T16-42-15Z.md`）
- **格式**：自由 markdown，無 frontmatter
- **內容**：「下個 session 開始時你會想知道的東西」 — in-progress task、debug context、剛跑到一半的 hypothesis
- **生命週期**：寫入 → 下個 session 開機時讀 → 讀完即刪
- **多視窗安全**：timestamp 命名讓多視窗不會互蓋
- **不進 git**：`.nakama/` 目錄整個 gitignored
- **跨機限制**：Tier 2 是本機檔，Win → Mac 不同步。跨機共用知識請存 Tier 1 durable

### Session 啟動 SOP

新 session 開始時：

1. Glob `.nakama/session_handoff_*.md` 看有沒有未消化的 handoff
2. 有的話按時間排序，由舊到新讀完
3. 讀完每個檔就刪掉那個檔
4. 如果有檔案 timestamp 超過 24h 前（agent crash 等異常情況）→ log warning 但仍讀，並標註 stale

### 收到「清對話」訊號時的具體流程

```
1. 確認 L1/L2 觸發點（這個 session 有沒有產生 durable 知識）
   - 有 → 已經在對話過程中寫過了（不需要 batch 寫）
   - 沒有 → 跳過 Tier 1
2. 寫 Tier 2 handoff 檔
   - 路徑：.nakama/session_handoff_{timestamp}.md
   - 內容：3-5 行摘要：當前 task、進度、open questions
3. 簡短回覆「handoff 寫到 .nakama/，可以安心清」
4. 不執行 git commit/push（因為 Tier 2 不進 git）
```

## 跟 v1 行為的差異

| 行為 | v1（舊） | v2（這個版本） |
|------|---------|--------------|
| 「清對話」trigger durable memory 寫入 | ✅ | ❌ |
| 「清對話」trigger commit + push | ✅ | ❌ |
| 對話中明確 trigger 寫 durable memory | 較不主動 | 明確 L1/L2 規則 |
| 跨 session 短命 context 處理 | 走 durable memory（污染）| 走 `.nakama/` gitignored |
| 每天平均 memory commit | ~9 個 | 預計降到 1-3 個（只有真正 durable） |

## 失敗模式 + 緩解

1. **agent 仍然 git push 在 Tier 2 路徑**：應該擋。緩解：CI / pre-commit hook 檢查 `.nakama/` 不在 git 內（`.gitignore` 強制）。
2. **stale handoff 累積**：agent crash 不刪。緩解：每天 cron `find .nakama/ -mtime +1 -delete` 或 startup-time 警告。
3. **L2 過度敏感**：每次 user 說「對」就寫 memory。緩解：嚴格門檻 — 只在「具體技術選擇被 validate」時寫，不是所有正向回饋。
