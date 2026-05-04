---
name: API key 不在對話框輸入
description: API key 等敏感資訊不要在 Claude Code 對話框裡輸入，改用 .env 或 terminal 設定
type: feedback
originSessionId: ecac2e9b-d409-4922-b30f-4270e46d6df0
---
API key、密碼等敏感資訊不要在 Claude Code 對話框裡輸入或印出，因為會留在對話紀錄中。

**Why:** 對話歷史可能被記錄或同步，敏感資訊外洩風險高。

**How to apply:**
1. **使用者輸入端** — 引導修修透過 `.env` / terminal 環境變數設定，不用 `/firecrawl:setup` 之類在對話中輸入 key 的 flow
2. **Agent grep / cat 端**（我自己最容易踩的）— 任何 secret-bearing 檔案（`.env`、`*.token`、`credentials.json`）**禁止 raw `grep` / `cat` 把 value 印到 stdout**：
   - ❌ `grep -E "^VAULT_PATH|^ANTHROPIC_API_KEY" .env`（會把 key 整段 plaintext leak 到 transcript — 2026-05-04 踩過一次，那把 key rotate 掉）
   - ❌ `cat .env`
   - ✅ Presence-only check：`grep -q "^KEY=." .env && echo "KEY: set" || echo "MISSING"`
   - ✅ 路徑類非 secret 值才直接印：`grep ^VAULT_PATH .env | cut -d= -f2`
   - ✅ 只想看 key list（不要 value）：`cut -d= -f1 .env | grep -v '^#'`
3. **後果不只 transcript** — Claude Code 對話記錄可能 sync 到雲端、被 indexing、cache 數天；leak = 必 rotate，不能假裝沒事
4. **Trigger 條件** — 看到 `.env` / `secrets.*` / `*.token` 路徑要動，起手就走 presence-only pattern；不要憑印象寫「先 grep 一下看看」
