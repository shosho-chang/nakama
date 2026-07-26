---
name: Envato Elements 取得素材實況（2026-07 更新）
description: 修修有 Envato Elements 年度會員；官方無下載 API，但新版 app.envato.com + Claude in Chrome 瀏覽器自動化可全自動搜尋+授權+下載（2026-07-26 實測成功）；含 ToS 風險註記
type: project
tags: [envato, stock-photo, stock-video, image-sourcing, brook, broll]
created: 2026-04-22
updated: 2026-07-26
---

## 事實（2026-07-26 實測更新）

- **Envato Elements（訂閱制）和 Envato Market（build.envato.com/api/，單買制）是兩個不同產品**；Market API 只給交易紀錄，拿不到 Elements 素材
- **Envato Elements 官方無下載 API**；官方 MCP server（mcp.envato.com/mcp，claude.ai 有 connector）**只能搜尋不能下載**（FAQ 明講）
- **新版 app.envato.com（2026 改版）大幅簡化下載**：商品頁一顆「Download 4K」按鈕，點下去**自動授權**（toast「Automatically licensed」）+ 直接下載，無 license modal
- **Claude in Chrome 全自動流程實測成功（2026-07-26）**：搜尋 URL `app.envato.com/search?itemType=stock-video&term=<en+terms>&orientation=vertical` → 點結果 → 點 Download → 檔案落在**瀏覽器預設下載目錄（修修的是 `E:\` 根目錄，不是 Downloads！）**
  - 檔名格式：`<slug>-<date>-utc.mov`（stock video = ProRes 4K，11s 約 880MB）
  - 重複點 Download 會產生 `(1) (2)` 重複檔，但**不會重複授權**
  - extension 會把 JS 回傳裡的簽名 URL/JWT 遮蔽（`[BLOCKED]`），但不影響下載本身落地
- **ToS 風險（2026-04 查證，未重查）**：2026-03 ToS 禁止「scraping / bots / scripts / 自動化下載工具」。低量（每集 2–3 支、人速點擊）風險低，但技術上屬自動化；修修知情並裁決使用（先前也用 Codex Computer Use 做過同樣的事）

## How to apply

短影片 B-roll 管線：agent 標註 `<id>_broll.json`（區間+英文關鍵字）→ Claude in Chrome 搜尋+下載 → 從瀏覽器下載目錄搬到 episode `assets/broll/` → script 插入 Resolve。
Brook 圖片管線同理可用（stock photo 改 `itemType`）。
免費備援：Pexels / Pixabay / Unsplash API（key 免費）。AI 生圖：Flux（fal.ai）或本地 FLUX.1-dev。
