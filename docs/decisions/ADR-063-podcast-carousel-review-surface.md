# ADR-063: Podcast Carousel 使用獨立的 page-based review contract

**Date:** 2026-08-18  
**Status:** Accepted

**Contract update (2026-08-19):** EP120 Review Gate dogfood 已以「非空 feedback 建立修改工作、全空才可整份 Approve」取代早期 per-card radio 決策。下列內容是目前有效契約。

Podcast Carousel 與長 Highlight／短影片共用同一個 Thousand Sunny process、登入與 feedback/revision pattern，但使用獨立的 `/bridge/ig-cards/{episode_slug}` route 與 `nakama.podcast_carousel_review_manifest.v1`。Carousel 的 review 單位是 page、PNG、Display Copy 與 Transcript Evidence；finished-cut 的單位是 cut、timeline component、MP4 與 subtitle。共用 Web App 可避免多一個本機服務，分開 manifest 則避免把 carousel page 偽裝成 cut 或污染既有 timeline domain language。

每頁只有一個簡短 feedback 欄位。非空表示該頁需要修改；空白只表示該頁沒有修改要求，不是單卡 approval。送出任何非空 feedback 時，系統只收集非空項目並建立 revision-bound、agent-neutral correction job。所有欄位皆空時才允許整份 Approve；Approve 不修改 artifact、不建立 correction job，也不發布。

## Considered Options

- 擴充 `finished_cut_review_manifest`：否決；兩種 artifact 的 review 單位與可執行 action 不同。
- 另開一個 Carousel review service：否決；會重複 auth、啟動與 feedback infrastructure。

## Consequences

- `thousand_sunny.app` 掛載 sibling Carousel router，沿用既有 Highlight Review 的 auth 與 episode-root boundary。
- Carousel 可重用既有 Web App shell，但 schema、artifact validation 與 page actions獨立演進。
- Carousel review 以五欄桌機 grid 同時呈現全部卡片；不保留 per-card `approved`／`needs_changes` radio。
- 逐字稿 evidence 不常駐擠壓 grid；點擊卡片後以 detail panel 顯示放大成圖、Display Copy、原文、說話者與時間位置。
- Correction job 保存 source revision、manifest hash、page/artifact identity、claim、progress 與 result revision，使用 `queued → claimed → in_progress → completed|failed` 狀態機。
- 當前 E2E Codex 或 Claude Code agent claim job 並負責完整修訂；IG Audience、Episode Editorial、Brand and Evidence 仍是三個獨立 subagents。沒有相容 executor 在線時，job 保持 `queued`。
- Claim 是有期限的 lease；有效期間內其他 executor 不得搶 job，只有 lease 過期後另一個 Codex 或 Claude Code 才能 reclaim。合法 progress update 同時續租，避免長任務被誤接手。
- Executor 不得使用外部 LLM API 或隱性 provider。Approve 只關閉人類 review gate；Stage 6 發布仍是另一個明確動作。
