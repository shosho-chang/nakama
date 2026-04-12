---
name: API key 不在對話框輸入
description: API key 等敏感資訊不要在 Claude Code 對話框裡輸入，改用 .env 或 terminal 設定
type: feedback
originSessionId: ecac2e9b-d409-4922-b30f-4270e46d6df0
---
API key、密碼等敏感資訊不要在 Claude Code 對話框裡輸入，因為會留在對話紀錄中。

**Why:** 對話歷史可能被記錄或同步，敏感資訊外洩風險高。
**How to apply:** 需要設定 API key 時，引導修修透過 `.env` 檔案或 terminal 環境變數設定，不要用 `/firecrawl:setup` 之類需要在對話中輸入 key 的方式。
