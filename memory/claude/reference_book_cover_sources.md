---
name: reference_book_cover_sources
description: 影片／封面要用的書封從哪裡拿——先找本機書庫 data/books/<書名>/cover.jpg（高解析正面），再找出版社／書店官方圖；博客來、城邦 CDN、Kobo 擋直接下載
metadata:
  type: reference
---

- **本機書庫**：`E:\nakama\data\books\<中文書名>\cover.jpg`（例：《財富階梯》1545×1999 正面平圖，旁邊還有 `bilingual.epub`）。2026-11-12《財富階梯》YouTube 封面就是用這張（見 `E:\data\AgentOutput\20261112-wealth-ladder-cover\editor-work\spec.json` 的 `book.src`）。
- **該集 YouTube 封面成品與設定**：`E:\data\AgentOutput\<YYYYMMDD>-<slug>-cover\`（`20261112 財富階梯.png`、`editor-work/spec.json`、`frame-styles/` 六種書框比較）。
- **網路下載**（書庫沒有時；書封下載是常設授權，見 [[feedback_book_cover_download_authorized]]）：2026-11-15 實測博客來 `www.books.com.tw/img/...`、城邦 `d4dpzjhk0g50s.cloudfront.net`、Kobo 都回 403；Yahoo 購物城邦讀書花園商品圖可下（但多是帶書背書腰的立體圖，要透視拉平）。不要偽造 Referer 繞過。
