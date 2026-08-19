# Stage 6：Meta + Carousel 多平台發布 tracer bullet

- Date: 2026-08-19
- Stage anchor: Stage 6 Publishing
- Worktree: `E:\nakama\worktrees\social-upload-inventory`
- Branch: `codex/social-upload-inventory`
- Upstream carousel branch: `codex/social-carousel-publishing` at `479c9ac5`

## 1. 目標

建立「一次核准、展開多個獨立平台 target」的 Stage 6 tracer bullet：Short 同一次操作可投遞 YouTube Shorts、Instagram Reels、Facebook Page Reels；Carousel 同一次操作可投遞 Instagram Carousel、Facebook Page 多圖貼文，並建立 YouTube Community image post 的可追蹤 browser handoff。任何單一平台失敗不得回滾或重做其他已成功平台。

## 2. 範圍

### 2.1 先整合既有成果

- 保留目前 worktree 內 YouTube Shorts E2E 與進度修正，不可 reset、checkout 覆寫或遺失。
- 先把目前 Shorts 相關變更整理成獨立 commit。
- 將 `codex/social-carousel-publishing` 合併到目前 branch；衝突時保留目前分支的 YouTube Short／progress 行為，同時帶入 carousel review、release bundle、job lease、per-platform checkpoint 與 publish UI。
- 不重寫 `scripts/podcast_carousel_publish_job.py` 的 job state machine。

### 2.2 新增平台層

精確落點：

- `agents/usopp/social_publish.py`：平台 adapter protocol、target eligibility、一次 fan-out orchestration；不得放 HTTP 細節。
- `agents/usopp/meta_graph.py`：Meta Graph API anti-corruption layer；Instagram Reels、Instagram Carousel、Facebook Page Reels、Facebook Page multi-photo 的 create/upload/poll/publish/reconcile。
- `agents/usopp/media_staging.py`：Cloudflare R2 短效素材 staging；unguessable job key、presigned GET URL、TTL 與完成後 cleanup。不得重用 backup bucket 名稱。
- `scripts/publish_dispatch.py`：桌面 worker CLI；依 release/carousel job claim 各平台 target，逐一 checkpoint；支援 `--dry-run`，真實外部寫入必須明確 `--execute`。
- `scripts/meta_publish_probe.py`：Meta Slice 0 probe；credentials/capabilities、IG Reel、IG Carousel、FB Reel、FB multi-photo 分開的命令，預設只做 credentials/capability 檢查。
- `shared/release_store.py`、`shared/state.py`、`migrations/`：只在 release target 缺少外部 checkpoint／adapter metadata 時做最小 schema 擴充；不可建立第二套 release queue。
- `thousand_sunny/routers/publish_review.py`、`thousand_sunny/templates/bridge/publish_cut.html`：Short 的「核准並上傳」改成一次建立/核准所有合格 targets，顯示每平台狀態與只重試失敗平台。
- `thousand_sunny/routers/carousel_review.py`、`thousand_sunny/templates/bridge/carousel_publish.html`：沿用 ADR-065 job，接 live adapters；YouTube Community 顯示 browser-handoff capability，不得冒充 API 自動發布。
- `.env.example`：新增 Meta 與 media staging 設定名稱，不放 secret 值。
- `docs/runbooks/meta-publishing-setup.md`：Meta App、Facebook Login for Business、Page token、IG professional account、R2 staging bucket 與 probe SOP。
- 對應 `tests/agents/usopp/`、`tests/scripts/`、`tests/shared/`、`tests/thousand_sunny/` 測試。

### 2.3 平台命名與 eligibility

- Short targets：`youtube`、`instagram_reels`、`facebook_reels`。
- Carousel targets：`instagram`、`facebook_page`、`youtube_community`。
- `facebook_reels` 對 >60 秒素材 fail closed，狀態顯示 `ineligible`／明確原因；不得自動裁切、加速或重壓。其他合格平台仍照常執行。
- YouTube Community 走 `browser_handoff`：輸出 caption、最多 10 張 PNG、目標 URL 與待確認狀態；只有讀到人工／瀏覽器回填 permalink 或 post id 才可 checkpoint `published`。

## 3. 輸入

- ADR-055：`docs/decisions/ADR-055-video-release-architecture.md`，Release Target 是影片執行單位，DB 是 release plan SoT。
- Carousel contract：`codex/social-carousel-publishing` 的 `docs/decisions/ADR-065-podcast-carousel-stage6-publish-jobs.md`、`shared/schemas/carousel_publish.py`、`scripts/podcast_carousel_publish_job.py`。
- YouTube Shorts 實作：目前 worktree 的 `scripts/publish_upload.py`、`scripts/publish_register_external_short.py`、`thousand_sunny/routers/publish_review.py`。
- Meta 官方 flow：
  - IG Reel：create `media_type=REELS` → poll container → `media_publish`。
  - IG Carousel：create each child with `is_carousel_item=true` → parent `CAROUSEL` → poll → publish。
  - FB Reel：`video_reels` start → resumable/local upload → finish → poll/reconcile。
  - FB multi-photo：先建立 unpublished photo IDs，再以 Page feed `attached_media` 建立單一貼文；以真實 probe 驗 payload，不把 IG payload 共用過來。
- YouTube 官方限制：Community image post UI 最多 10 張；Data API 無 Community post insert。
- Design system：`docs/design-system.md`；UI 只能使用既有 `--sho-*` tokens 與 Bridge dense ops patterns。

必要設定名稱：

- `META_GRAPH_API_VERSION`（明確指定，不在程式內猜 current version）
- `META_PAGE_ID`
- `META_IG_USER_ID`
- `META_PAGE_ACCESS_TOKEN`
- `META_MEDIA_R2_ACCOUNT_ID`
- `META_MEDIA_R2_ACCESS_KEY_ID`
- `META_MEDIA_R2_SECRET_ACCESS_KEY`
- `META_MEDIA_R2_BUCKET`
- `META_MEDIA_PUBLIC_BASE_URL`（若採 public custom domain）

## 4. 輸出

1. 一個共用 fan-out orchestration service，但平台 HTTP payload 各自封裝。
2. 四個 Meta adapter 與一個 YouTube Community handoff adapter。
3. Short Bridge 頁的一次多平台核准與 per-target status/retry UI。
4. Carousel publish job 的 live executor 接線與 YouTube Community handoff。
5. R2 短效素材 staging/cleanup。
6. Meta setup/probe runbook。
7. 全部新功能使用 fake transport 的 deterministic tests；測試不得碰真實 Meta、R2 或 YouTube。

## 5. 驗收

### Automated acceptance

- Short 59 秒：一次 dispatch 建立並執行 `youtube`、`instagram_reels`、`facebook_reels` 三 targets。
- Short 74 秒：`youtube`、`instagram_reels` 可執行；`facebook_reels` 明確 ineligible，整體 job 不因而失敗，也不產生 FB API call。
- IG Reel 測試鎖定 create → poll FINISHED → publish → permalink checkpoint 順序。
- IG Carousel 測試鎖定每張 child 立即 checkpoint；crash 後重試不重建已知 child；parent 只建立一次。
- FB Reel 測試鎖定 start/upload/finish/reconcile；失敗重試不重新發布其他平台。
- FB multi-photo 測試鎖定 unpublished photo IDs 與單一 Page post；不得產生多則獨立照片貼文。
- R2 staging key 不含原始檔名／個資；URL 有 TTL；成功或永久失敗後 cleanup；測試確認不碰 backup bucket。
- YouTube Community target 永遠不透過 Data API 偽造成功；只有 handoff receipt 回填後才可 published。
- 重送同一 idempotency fingerprint 不重複建立外部 post。
- IG 成功、FB 失敗後，Retry 只呼叫 FB。
- Bridge 頁 refresh 後能從 persisted state 還原各平台狀態。
- 既有 YouTube long／Short upload、Short burned-caption policy、progress polling 全部回歸綠。
- Ruff、相關 pytest、`git diff --check` 全綠。

### Supervised live gates（沒有 credentials 時不得假裝完成）

1. Meta credential probe 能取得 Page 與 IG professional identity。
2. 用測試素材發布一支 IG Reel，取得 media id/permalink。
3. 用 2 張測試卡發布 IG Carousel，取得 parent id/permalink。
4. 分別 probe 59 秒與 74 秒 FB Reel，記錄 Meta 真實回應；在官方上限或 probe 結果改變前仍維持 >60 秒 fail closed。
5. 發一組 FB Page multi-photo 測試貼文並人工確認呈現。
6. YouTube Community handoff 由已登入瀏覽器完成人工確認，回填 post URL。

## 6. 邊界

- 不做 Threads；這次平台集合由使用者明確指定為 IG、FB、YouTube。
- 不使用 YouTube Data API 發 Community post；官方沒有 endpoint。
- 不在 browser localStorage、HTML、log、job JSON 或 state.db 明文儲存 access token/app secret。
- 不把 Bridge/VPS 變成長期影片 bytes proxy；影片由桌面 worker直傳，Carousel 圖片才使用短效 R2 staging。
- 不重用 R2 backup bucket；staging bucket 必須獨立、最小權限、可清除。
- 不讓單一平台失敗回滾其他平台；不得用一個共享 `status` 掩蓋 per-target 狀態。
- 不自動裁短 Facebook 版本；需要另開 Stage 5 variant 才能支援 >60 秒素材。
- 不在缺少 credentials 時呼叫外部 side effect；UI/CLI 必須 fail closed 並指出缺哪個設定。
- 不刪除、reset 或覆寫目前 worktree 的未提交變更；需要刪除時遵守回收桶規則。
- 不新增前端 framework；沿用 FastAPI form POST + 303、既有 polling 與 `--sho-*` design system。

## 實作者三問自審

- 方案正確：一次 click 是否只是建立 fan-out group，而不是把多平台做成不可重試的同步串行？
- 影響全面：Short、Carousel、YouTube Community handoff、R2 cleanup、partial failure、refresh recovery 是否都有測？
- 回歸風險：是否完整保留現有 YouTube long/Short、burned captions、progress 與 duplicate guard？
