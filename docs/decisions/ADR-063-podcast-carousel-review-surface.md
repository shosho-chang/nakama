# ADR-063: Podcast Carousel 使用獨立的 page-based review contract

**Date:** 2026-08-18  
**Status:** Accepted

Podcast Carousel 與長 Highlight／短影片共用同一個 Thousand Sunny process、登入與 feedback/revision pattern，但使用獨立的 `/bridge/ig-cards/{episode_slug}` route 與 `nakama.podcast_carousel_review_manifest.v1`。Carousel 的 review 單位是 page、PNG、Display Copy 與 Transcript Evidence；finished-cut 的單位是 cut、timeline component、MP4 與 subtitle。共用 Web App 可避免多一個本機服務，分開 manifest 則避免把 carousel page 偽裝成 cut 或污染既有 timeline domain language。

## Considered Options

- 擴充 `finished_cut_review_manifest`：否決；兩種 artifact 的 review 單位與可執行 action 不同。
- 另開一個 Carousel review service：否決；會重複 auth、啟動與 feedback infrastructure。

## Consequences

- `thousand_sunny.app` 掛載 sibling Carousel router，沿用既有 Highlight Review 的 auth 與 episode-root boundary。
- Carousel 可重用既有 Web App shell，但 schema、artifact validation 與 page actions獨立演進。
- Carousel review 以五欄桌機 grid 同時呈現全部卡片；每頁有 `approved`／`needs_changes` 與簡短 feedback，全部同 revision 卡片核准後整份才通過。
- 逐字稿 evidence 不常駐擠壓 grid；點擊卡片後以 detail panel 顯示放大成圖、Display Copy、原文、說話者與時間位置。
