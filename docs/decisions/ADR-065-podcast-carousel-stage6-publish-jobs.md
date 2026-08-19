# ADR-065: Podcast Carousel 使用獨立 Stage 6 Publish Job

**Date:** 2026-08-19  
**Status:** Accepted  
**Extends:** ADR-063, ADR-064  
**Does not extend:** ADR-006 WordPress approval queue, ADR-055 video release store

## Context

Podcast Carousel 在 Stage 5 產生 episode-local square PNG、經 page-based Review Gate 核准後，仍缺少一個明確的 Stage 6 hand-off。Approve 不能同時代表發布；WordPress `approval_queue` 與 video `release_store` 的 payload、狀態與平台語言也不適合承載 Carousel。

平台能力並不對稱：Instagram professional carousel 可在設定 Meta credentials 與 media transport 後使用 media containers / `media_publish`；Facebook Page 可使用 Meta transport；YouTube Data API 的 Activities 資源只有 list，`activities.insert` 已 obsolete，沒有 Community post publish endpoint。因此 UI 與 job 不得把「可選平台」誤寫成「API 已可用」。

## Decision

核准 current Carousel manifest 後，Review Gate 的 Approve response 回傳 same-origin `publish_url`，前端導向 `/bridge/ig-cards/{episode_slug}/publish`。Approve 仍只寫 approval audit，不建立 publish job、不執行外部 action。

Publish page 重新驗證 current manifest 的 `revision + manifest_sha256` 與最新 `CarouselFeedbackRevision(decision=approved)`，顯示核准的 square cards、caption、平台 capability 與 strategy。提交後在 `<episode>/ig-carousel/publish_jobs/` 建立 `CarouselPublishJobV1`：

- source revision、manifest hash、approval revision 與每張 PNG `ArtifactReceipt`
- 使用者審過的 caption
- 平台集合與建立當下的 capability / strategy snapshot
- `sha256(revision + manifest hash + caption + sorted platform set)` idempotency fingerprint
- `queued → claimed → in_progress → completed | failed` 狀態、lease、progress 與逐平台 result

相同 fingerprint 的重複 submit 原子地回傳同一 job。Codex 或 Claude Code executor claim 時必須聲明 capabilities；job 只在 capability 覆蓋所有 target requirements 時允許 claim。合法 progress 續租，lease 到期後才可 reclaim；新 token 使舊 worker 失效。

平台 strategy：

| Platform | Strategy | Capability boundary |
|---|---|---|
| Instagram | `meta_api`（僅明確設定 transport 時）或 `agent_browser` | 未設定 credentials 時誠實顯示 agent browser，不假裝 API ready |
| Facebook Page | `meta_api`（僅明確設定 transport 時）或 `agent_browser` | 同上 |
| YouTube Community | `agent_browser_manual` | 永遠不宣稱有 Data API insert；需要 browser session 與人工確認 |

`scripts/podcast_carousel_publish_job.py` 只做 episode-local JSON 的 list / claim / progress / complete / fail。它不讀取 secrets、不呼叫 Meta / YouTube / browser、不做 live publish。外部 executor 完成平台操作後，才把每平台 `receipt_id`、`permalink` 或 `error` 回寫 job。

## Consequences

- Stage 5 approval 與 Stage 6 publish action 保持可稽核、可重試且語意分離。
- Publish page refresh 可從 episode-local latest job 或 session state 恢復 polling。
- 沒有相容 executor 時 job 保持 `queued`；UI 不製造假的 progress。
- 本 slice 建立完整 contract 與操作面，但不實際對外發布，也不儲存平台 secrets。
- 未來 live adapter 必須另外驗證 Meta credential/media transport、browser session 與 per-platform confirmation；不得改寫本 ADR 的能力邊界。
