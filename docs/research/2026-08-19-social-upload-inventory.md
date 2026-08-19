# Stage 6 社群素材發布現況盤點

- Date: 2026-08-19
- Stage anchor: Stage 6 Publishing
- Worktree: `E:\nakama\worktrees\social-upload-inventory`
- Branch: `codex/social-upload-inventory`
- Baseline: `37cb4b4c`
- 參考中的既有分支: `codex/social-carousel-publishing` at `479c9ac5`

## 結論

1. **YouTube 長 Highlight 已有可用的 production path，而且本機 state 顯示真的上傳過。**
   2026-08-19 對 `E:\nakama\data\state.db` 做唯讀查詢，得到 7 筆 long Release：
   4 筆 `uploaded` 且有 `video_id`、3 筆 `draft`；`data/youtube_token.json` 也存在。
2. **YouTube Shorts 不是 0 實作。** 現有程式已能 render short、燒字幕、登錄
   `format=short` 的 YouTube Release Target，再交給同一個 `videos.insert` uploader。
   但 state 裡目前 **0 筆 short Release / 0 筆 short upload**，所以應定義為
   「底層共用能力已存在，Shorts production E2E 尚未驗收」，不能寫成 fully shipped。
3. **Podcast Carousel 的 Stage 5 與 Stage 6 contract 已在另一條乾淨分支上做得很深，
   但仍未真正發布。** `codex/social-carousel-publishing` 有 render、Review Gate、Publish
   page、idempotent job、lease、per-platform checkpoint；ADR-065 明寫該 slice 不呼叫
   外部平台、不儲存 secrets。
4. **Meta 官方 API 足以支援自有 Web App。** Instagram Professional Carousel / Reels、
   Facebook Page Reels、Threads Carousel 都有官方發布 flow。建議讓 Thousand Sunny
   Bridge 當控制面，不讓瀏覽器或 VPS 無條件代理所有影片 bytes。

## Repo 現況矩陣

| Channel → Platform | 現況 | 證據 | 真正缺口 |
|---|---|---|---|
| 長 Highlight → YouTube | **已跑過** | `scripts/publish_upload.py:182-247` 做 resumable upload、縮圖、CC；`thousand_sunny/routers/publish_review.py:240-325` 做審核、排程與啟動 worker；本機 state 4 筆 uploaded | 平台到點後沒有 reconciliation 把 `uploaded` 更新成 `published`；跨 process resumable resume 註解仍說 v2 再接 |
| Short → YouTube Shorts | **底層可走，未 production 驗收** | `scripts/publish_prep.py:240-258` 依 long/short 分流，short 用 ffmpeg 燒 tight SRT；`scripts/publish_prep.py:302-313` 兩種 format 都建立 YouTube target；uploader 不限制 format | 0 筆 short runtime record；沒有 9:16、解析度、codec、duration preflight；短片已燒字幕但 uploader 又無條件呼叫 `captions.insert`；沒有 Shorts 專屬 E2E/UAT |
| Short → Instagram Reels | **0 live adapter** | repo base 沒有 Graph API publisher；ADR-055 只把它列 v2 | Meta OAuth、media transport、container poll/publish、permalink checkpoint、app-owned scheduling |
| Short → Facebook Page Reels | **0 live adapter** | 同上 | Facebook Page token、Reels start/upload/finish、時長規格 probe、schedule payload、checkpoint |
| Carousel → Instagram | **Stage 5 + job contract；0 live publish** | `codex/social-carousel-publishing` 的 ADR-065、`shared/schemas/carousel_publish.py`、`scripts/podcast_carousel_publish_job.py` | Meta credentials、暫存素材 URL/transport、children containers、parent carousel、status poll、`media_publish` |
| Carousel → Facebook Page | **job target；0 live publish** | `CarouselPublishPlatform` 有 `facebook_page` | 必須另做 Page multi-photo adapter；不能假設它和 IG carousel 是相同 payload/呈現 |
| Carousel → Threads | **官方可做；repo job 未建模** | 官方 Threads API 支援 2–20 張 carousel；現有 schema 只有 `instagram / facebook_page / youtube_community` | 先裁決 scope，再加 Threads OAuth 與 adapter；不可暗中塞進 `facebook_page` |
| Carousel → YouTube Community | **只能 agent/browser handoff** | ADR-065 與 schema 明確禁止把它宣稱成 Data API publish | 官方沒有 Community post insert；若保留，只能 browser/manual 且需人工確認 |

## YouTube 既有路徑：實際形狀

```text
Resolve timeline
  -> publish_prep.py
     -> long: 關字幕軌，render clean MP4
     -> short: render clean MP4 + ffmpeg burn tight SRT
  -> releases + release_targets(platform=youtube)
  -> publish_description.py 回填 title / description / thumbnail
  -> Bridge /bridge/publish 審核與 publish_at
  -> publish_upload.py
     -> videos.insert(resumable, private + optional publishAt)
     -> thumbnails.set (有 thumbnail 才做)
     -> captions.insert
     -> status=uploaded + video_id/url
```

現有設計與 YouTube 官方 flow 一致：`videos.insert` 支援 resumable media upload，
`status.publishAt` 必須搭配 private 且影片未曾發布；caption 走 `captions.insert`。
YouTube 沒有另一套「Shorts upload endpoint」，Shorts 仍走 `videos.insert`，平台依素材
規格分類。

### YouTube closeout 缺口

1. **Short preflight 缺失**：`_probe()` 只記 duration 與 file size，沒有讀 width / height /
   codec / fps；`release_store` 只驗 `format in (long, short)`。應在 approve 前 fail closed，
   否則「上傳成功」不等於「被辨識為 Short」。
2. **Short CC policy 自相矛盾**：`publish_prep.py:248-258` 已燒字幕；
   `publish_upload.py:229-241` 卻對所有 format 都嘗試上 CC。若觀眾手動開 CC，會看到
   double subtitles。應明確裁決 short 是否還上 CC，再按 `release.format` 分流。
3. **published reconciliation 缺失**：`VALID_STATUS` 有 `published`，但全 repo 找不到把
   video Release Target 從 `uploaded` 更新到 `published` 的 caller。應以 `videos.list`
   reconcile privacy/publish/processing 狀態，而不是把 `uploaded` 當終態。
4. **跨 process resume 尚未完成**：程式會保存 `upload_session_uri`，但註解明寫目前只保證
   同 process 續傳，跨 process resume 是後續工作。
5. **測試環境 caveat**：盤點時嘗試執行 30 個 publishing targeted tests；collection
   成功，但全部在 autouse fixture setup 被環境缺少 `anthropic` 擋住，沒有進入 test body。
   這不是 publishing assertion failure，但本次不能拿測試綠燈當證據。

## Carousel 分支：已完成與未完成的邊界

`codex/social-carousel-publishing` at `479c9ac5` 是乾淨 worktree。它已完成：

- episode-first square PNG render 與 page-based Review Gate；
- approval 與 publish action 分離；
- immutable release bundle、artifact receipt 與 manifest hash；
- idempotency fingerprint、retry lineage、lease、reclaim；
- per-platform `start-target` / checkpoint / complete；
- Publish page capability/strategy 顯示。

但 `docs/decisions/ADR-065-podcast-carousel-stage6-publish-jobs.md:61` 明確說：
**這個 slice 不實際對外發布，也不儲存平台 secrets**；
`scripts/podcast_carousel_publish_job.py:3` 也明確說不接觸社群平台。
因此這條分支應被視為 live adapter 的可靠上游，不應重做 job state machine，也不應把
job `completed` 誤當平台已發布。

另有一個 scope drift 要先裁決：早期影片發布設計把 Meta 三面定義為 IG / FB / Threads，
但 Carousel job schema 現在是 IG / Facebook Page / YouTube Community，沒有 Threads。

## Meta 官方機制

### Instagram authentication

Meta 目前提供兩種 Instagram Professional login topology：

1. **Instagram API with Facebook Login**
   - IG 必須是 Business 或 Creator，並連到 Facebook Page。
   - 使用 Facebook Page access token。
   - publishing 主要權限：`pages_show_list`、`instagram_basic`、
     `instagram_content_publish`、`pages_read_engagement`。
2. **Instagram API with Instagram Login**
   - 不要求連 Facebook Page。
   - 使用 Instagram user access token。
   - publishing scope：`instagram_business_basic`、
     `instagram_business_content_publish`。

本專案同時想發 IG 與 Facebook Page，因此建議 v1 選 **Facebook Login for Business +
Page access token**，一套 owner consent 對齊兩個目的地。只管理自己擁有／管理且已加到 App
Dashboard 的 Instagram Professional 帳號時，官方文件允許 Standard Access；若未來變成
替其他人的帳號服務，才需要 Advanced Access / App Review。

### Instagram Reels

官方 server-side flow：

1. `POST /{ig-user-id}/media`，`media_type=REELS`，帶 `video_url`、caption；
2. poll container 的 `status_code`；
3. FINISHED 後 `POST /{ig-user-id}/media_publish?creation_id=...`。

Meta 的官方 Postman collection 目前列出的 Reel 規格包含 MP4/MOV、H.264/HEVC、
23–60 FPS、建議 9:16、3 秒至 15 分鐘、最大 1 GB。`video_url` 必須能讓 Meta server
公開抓取；大檔也有 resumable upload 建立方式。

### Instagram Carousel

官方 flow：

1. 每張圖片各自 `POST /{ig-user-id}/media`，帶 public `image_url` 並標示
   `is_carousel_item=true`；
2. 以 child container IDs 建 parent：`media_type=CAROUSEL&children=...`；
3. publish parent container。

現有 Podcast Carousel 有 10 張邊界與 immutable receipts，適合直接接此 flow。但 child
container 是外部 side effect；必須每張建立後立即 checkpoint，crash/retry 時先 reconcile，
不能整組從頭無腦重建。

### Facebook Page Reels

官方 flow 是 `video_reels` 的 start → upload → finish：start 回 `video_id` 與 upload URL，
finish 支援 `DRAFT / SCHEDULED / PUBLISHED` state。Meta 目前官方 Postman collection 列的
Facebook Reel 上限是 60 秒；而本專案 short 規格是 60–120 秒，這是 **Slice 0 必測 blocker**。
在探針通過前，不可假設同一支 90–120 秒 cut 能無修改投放 YouTube Shorts、IG Reels、FB Reels。

### Threads Carousel

官方 Threads API 已支援 2–20 個 image/video child containers：
`/{threads-user-id}/threads` 建 item 與 parent carousel，再用
`/{threads-user-id}/threads_publish` 發布。它需要獨立 Threads OAuth scopes，不能只靠
Facebook Page token 推論會自動有權限。

### 排程語意

- YouTube 已有原生 `publishAt`，平台持鐘。
- Facebook Reels finish 有 `SCHEDULED` 狀態，但 exact schedule payload 仍應用真帳號 probe。
- 本次在 Instagram 官方 current collection 找到的是 create/poll/publish，沒有對等的
  `publishAt`。在 probe 證明前，應把 Instagram / Threads 視為 **Nakama 持鐘**：排程到點
  才建立／發布 container，而不是提早數天建 container。

## 自有 Web App 建議架構

可以做在 Thousand Sunny，但 Web App 應是 **control plane**，不是所有 media bytes 的 proxy。

```text
Bridge Publish surface
  -> approve / caption / platform set / schedule
  -> Stage 6 job (DB or episode-local contract)
  -> platform worker claims target
     -> YouTube video: desktop -> YouTube resumable upload
     -> IG/FB video: desktop worker -> Meta resumable，或先放短效 object URL
     -> IG/FB/Threads cards: stage PNG to R2/S3 signed URL -> Meta fetches
  -> poll/reconcile platform state
  -> checkpoint platform media ID + permalink
  -> UI shows per-target success/failure
```

### Security boundary

- OAuth redirect 可以從 Web App 開始，但 access token、refresh/long-lived token、app secret
  只留 server/worker；不可落在 browser localStorage 或送給前端 JavaScript。
- object URL 只給單一 job、短 TTL、unguessable key；Meta 完成抓取後刪除 staged media。
- UI approve 不直接等於平台 publish。沿用 ADR-065 的 claim/checkpoint boundary，避免使用者
  refresh 或 double click 造成重複貼文。
- 每個 platform target 獨立 state；IG 成功、FB 失敗時只重試 FB。

### 為什麼不讓 VPS 代理影片

ADR-055 已凍結「原檔與 Resolve 在桌機、桌機當 uploader、VPS 當控制面」。如果 Web App
先收 1 GB 影片到 VPS 再傳 Meta，會多一次上傳、增加 VPS disk 與 timeout 風險，也破壞既有
operational topology。Carousel PNG 小，才適合由 Web App/backend stage 到 object storage。

## 建議執行順序

### Slice A — 關閉 YouTube Shorts 的現有缺口

Definition of Done：

- 以一支真實 `punch-S*` 完成 render → Release → review → upload → scheduled publish；
- ffprobe preflight 驗 width/height/aspect/duration/codec/audio；
- 裁決並實作 short CC policy；
- `videos.list` reconcile 後 state 真的到 `published`；
- 測試環境依賴補齊，targeted tests 綠。

這一刀不需要新 uploader，只是把既有 generic video path 從「能跑 short」收斂成「Shorts 已驗收」。

### Slice B — Meta Slice 0 probes

使用 Meta test asset / test account，先不寫 production adapter：

1. Facebook Login for Business 取得 Page token + IG user ID；
2. 發一支 IG Reel，再查 media/permalink；
3. 發一組 IG Carousel，再查 children / parent / permalink；
4. 發 60 秒與 90 秒 Facebook Reel，確認真實時長邊界；
5. 驗 Facebook Page multi-photo post 的 payload 與呈現；
6. 驗 token refresh/expiry、Standard Access、失敗後可否 reconcile；
7. 驗 Instagram/Threads scheduling 是否必須由 Nakama 持鐘。

### Slice C — Instagram live adapter

先接價值最高且官方 flow 最清楚的兩條：

- short video → Instagram Reels；
- Podcast Carousel → Instagram Carousel。

重用 ADR-065 job/checkpoint，不重做 queue；video target 則沿用 ADR-055 Release Target model。

### Slice D — Facebook Page + Threads

Facebook Page Reels 的時長 probe 通過後再接；Facebook multi-photo 與 Threads Carousel 各自
adapter，不把三個 Meta surface 偽裝成同一 payload。完成後才補 Stage 7 insights 回收。

## 需要先做的產品裁決

1. Carousel 的目的地到底是 IG only、IG + FB，還是早期規劃的 IG + FB + Threads？
2. YouTube Community 是否保留 browser/manual handoff，或從 v1 scope 移除？
3. 60–120 秒 short 是否願意為 Facebook 產一個 <=60 秒變體？答案會影響 Stage 5 cut model，
   不能在 Stage 6 adapter 裡偷偷截斷。
4. Instagram / Threads 若無原生排程，Nakama 持鐘是否接受「到點 Web App/worker 必須在線」；
   若不接受，需要外部 scheduler/queue 的 SLO。

## 官方參考

- [Google — Upload a Video](https://developers.google.com/youtube/v3/guides/uploading_a_video)
- [Google — Resumable Uploads](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)
- [Google — Video resource / publishAt](https://developers.google.com/youtube/v3/docs/videos)
- [Google — Captions insert](https://developers.google.com/youtube/v3/docs/captions/insert)
- [Meta official Postman — Instagram API](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api)
- [Meta official Postman — Instagram image/container publishing](https://www.postman.com/meta/instagram/request/23987686-f4b5a72d-a125-4080-8968-93de1a549e68)
- [Meta official Postman — Facebook API / Reels publishing](https://www.postman.com/meta/facebook/documentation/r56bjfd/facebook-api)
- [Meta official Postman — Threads API](https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api)
- [Meta official sample — Reels Publishing APIs](https://github.com/fbsamples/reels_publishing_apis)
