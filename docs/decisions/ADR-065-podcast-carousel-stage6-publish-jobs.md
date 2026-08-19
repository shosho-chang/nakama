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

- source revision、manifest hash、`publish_compatibility`、approval revision 與每張 PNG `ArtifactReceipt`
- 使用者審過的 caption
- 平台集合與建立當下的 capability / strategy snapshot
- `sha256(revision + manifest hash + publish compatibility + caption + sorted platform set)` idempotency fingerprint
- `queued → claimed → in_progress → completed | failed | superseded` 狀態、lease、progress 與逐平台 checkpoint

相同 fingerprint 的重複 submit 在最新 matching job 非 `failed` 時原子地回傳同一 job；若最新 matching job 已 `failed`，相同輸入建立新的 queued retry，並以 `retry_of_job_id` 連回失敗 job。任何不同 fingerprint 的新工作若與 queued / claimed / in-progress job 有重疊平台，建立時 fail closed，避免同一 revision/hash 平行發布。成功完成後若要用不同文案或平台集合再次發布，UI 與 server 都要求明確 republish confirmation。

Codex 或 Claude Code executor claim 時必須聲明 capabilities；retry / reclaim 只要求尚未 published targets 的 capabilities，已 checkpoint 的平台不會阻擋後續 worker。合法 progress 續租，lease 到期後才可 reclaim；新 token 使舊 worker 失效。Claim 與 complete 都必須重新計算 job 內每個 asset 的 path existence、bytes 與 SHA-256；任一 drift 都 fail closed，且不得改變 job state。

Release ordering 使用 episode-local OS-backed release lock。建立 job 時會把 manifest、頁面 bytes 與 canonical request 複製為 per-job immutable release bundle；後續 claim / start / checkpoint / complete 只讀 bundle，並重新驗證 bundle receipts、request fingerprint、`current.json` identity、approval revision 與 active correction。新 feedback 在同一把鎖內先處理 publish job：queued job 轉為 `superseded`；claimed / in-progress job 則拒絕 correction。Job mutation lock 使用 OS advisory lock；lock file 僅是 metadata，process crash 後 OS 會釋放 mutex，殘留檔不會永久阻塞。

每個 target 有穩定 `idempotency_key` 與 `pending / in_progress / published / failed` state。Executor 必須先 `start-target` 取得新的 `attempt_id`；checkpoint receipt 必須同時綁定 target key 與該 attempt。Lease 過期後的 in-progress target 標為 `reconcile_required`，不得 restart 或 fail 掉不確定 outcome，只能先 reconciliation。Published checkpoint 在 strategy 變更、lease reclaim、job-level failure 與 retry lineage 中保留；executor 只執行未 published targets。只要任一 target checkpoint 是 `failed`，job-level 狀態就是可重試的 `failed`。這是 crash recovery 與跨 worker 去重 boundary，不宣稱外部平台具備交易式 exactly-once。

平台 strategy：

| Platform | Strategy | Capability boundary |
|---|---|---|
| Instagram | `meta_api`（僅 manifest 為 `api_compatible` 且明確設定 transport 時）或 `agent_browser` | `manual_only` manifest 永遠使用 agent browser，即使 Meta transport 已設定 |
| Facebook Page | `meta_api`（同上）或 `agent_browser` | 同上 |
| YouTube Community | `agent_browser_manual` | 永遠不宣稱有 Data API insert；需要 browser session 與人工確認；最多 10 張圖片，超過時 UI disabled 且 server 拒絕 |

`scripts/podcast_carousel_publish_job.py` 只做 episode-local JSON 的 list / claim / start-target / checkpoint / progress / complete / fail / retire-legacy。它不讀取 secrets、不呼叫 Meta / YouTube / browser、不做 live publish。外部 executor 完成單一平台操作後，立刻把綁定 attempt 的 `receipt_id`、`permalink` 或 `error` checkpoint 回 job；unsafe expired legacy job 只能用不執行外部操作的管理路徑 retire。

Publish Web page 是 handoff surface，不是 agent dispatcher。建立 job 不會呼叫 Anthropic API，也不會自動喚醒 Codex / Claude Code desktop task。Queued 畫面必須顯示 job ID 與依 unfinished targets 產生的兩種本機 claim CLI，讓使用者把工作交給目前已開啟且具有登入狀態的 agent 流程。Desktop wakeup 或 live executor adapter 是後續獨立整合，不得在此 slice 假裝已自動執行。

Approve idempotency 只看 current manifest 的最新 matching feedback revision。檢查 idempotent approval 以前必須先拒絕同一 revision/hash 的 active correction job；歷史 approved revision 不得蓋過較新的 draft 或 active correction。

### v1 backward compatibility

早期 v1 job 沒有 `source_publish_compatibility`，其 fingerprint 也沒有這個欄位。這些 JSON 保持可讀，list、status route 與 Publish page 不做 eager rewrite：欄位缺少時保留為 `null`，執行 boundary 依 asset count 推導有效值（`<=10` 為 `api_compatible`，`>10` 為 `manual_only`）。Legacy `manual_only + meta_api` job 可以被稽核，但 claim 與 complete 都必須 fail closed。

建立 job 時同時計算 current 與 legacy fingerprint，matching 任一值都視為相同 request。沒有 immutable bundle 的 legacy job 不可執行；queued / failed 或 lease 已過期的 unsafe legacy job 可由 `retire-legacy` supersede，再以 `retry_of_job_id` 建立完整 current contract。Superseded matching request 會建立 queued replacement；整條 retry lineage 的 published carry 不會被誤判為新的 republish。如此不改寫既有 audit artifact，也不因 schema 演進製造重複 publish work或死路。

## Consequences

- Stage 5 approval 與 Stage 6 publish action 保持可稽核、可重試且語意分離。
- Publish page refresh 可從 episode-local latest job 或 session state 恢復 polling。
- Publish page 顯示逐平台 checkpoint；390px 窄螢幕不裁切 header、caption counter 或平台 eligibility reason。
- 沒有相容 executor 時 job 保持 `queued`；UI 不製造假的 progress。
- 一份 base caption 目前仍共用於所有平台；Instagram 被選取時，超過 2,200 字會同時在 client 與 server fail closed。UI 另顯示 Facebook 字數提示與 YouTube 共用文案邊界；per-platform 完整格式 preview 與 override 必須在 live adapter 前另做相容性 slice。
- 本 slice 建立完整 contract 與操作面，但不實際對外發布，也不儲存平台 secrets。
- 未來 live adapter 必須另外驗證 Meta credential/media transport、browser session 與 per-platform confirmation；不得改寫本 ADR 的能力邊界。
