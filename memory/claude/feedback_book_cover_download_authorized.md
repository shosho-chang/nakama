---
name: 書封下載不用問
description: 修修 2026-10-09 常設授權——封面要用的書封（出版社／書店官方書封圖）直接下載，不用再徵詢
type: feedback
created: 2026-10-09
---

修修 2026-10-09（20261001 洪瀞集 thumbnail-brainstorm N1 作者訪談版式）：「以後下載書封這個動作就不用問我了，就直接做。」

**Why:** 每集作者訪談的完整節目封面都需要實際書封（thumbnail-brainstorm「N1 作者／新書訪談」），來源固定是出版社或書店的官方書封圖，每次停下來問只是在增加一個沒有資訊量的停點。

**How to apply:**
- 範圍只限**書封圖**：出版社官網（例：天下文化 bookzone `imgs.cwgv.com.tw/books/<系列>/<書號>/cover/<書號>.png`，去掉 `/thumb/` 是原尺寸）或書店商品頁的封面圖。其他下載（Envato 授權素材、影片、任意網頁檔）不在這條授權內。
- 照舊記錄來源頁、圖片 URL、SHA-256（`book-cover-source.json` + run log）。
- Windows 上 Git Bash `curl -o` 寫不進含 CJK 的路徑（error 23）——先下載到 ASCII 路徑再 `cp` 進 episode 資料夾。
- 相關：[[feedback_subtitle_house_style]]（同一集定下的字幕書名號規則）
