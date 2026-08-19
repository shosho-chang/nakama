# Publish Calendar tracer bullet — P9 task prompt

## 1. 目標

在 Bridge 的 Content Pipeline Stage 6（發布）新增唯讀 Publish Calendar，讓使用者能以台北時區的月曆，一眼查看全部或單集 Podcast 的長影片、Short、Carousel 在 YouTube／Instagram／Facebook 各平台的排程或實際發布狀態，並能回到既有發布詳情頁處理項目。

受益 line 是 Podcast Line 1；這個 tracer bullet 是 Stage 6 operational visibility，不是 Stage 7 analytics，也不是新的 scheduling truth source。

## 2. 範圍

預期新增：

- `shared/publish_calendar.py`：純讀取 projection、時區／月份／去重／backlog domain logic。
- `thousand_sunny/routers/publish_calendar.py`：登入保護、query 驗證、載入 projection、render page。
- `thousand_sunny/templates/bridge/publish_calendar.html`：desktop 月格與 mobile agenda 的 semantic HTML。
- `thousand_sunny/static/shosho/publish-calendar.css`：只使用既有 `--sho-*` tokens 的專屬樣式。
- `thousand_sunny/static/shosho/publish-calendar.js`：只有在 progressive enhancement 必要時新增；無 JS 仍須可導覽與篩選。
- `tests/shared/test_publish_calendar.py`：projection unit tests。
- `tests/thousand_sunny/test_publish_calendar.py`：route／auth／degraded render tests。
- `tests/thousand_sunny/test_publish_calendar_ui.py`：UI contract／responsive／accessibility assertions。

預期修改：

- `thousand_sunny/app.py`：註冊新 router。
- `thousand_sunny/templates/bridge/publish_list.html`：新增 Publish Calendar 導覽入口／active state。

開始前必須完整讀取：

- `docs/design-system.md`
- `CONTENT-PIPELINE.md`
- `shared/release_store.py`
- `scripts/podcast_carousel_publish_job.py`
- `shared/schemas/carousel_publish.py`
- `thousand_sunny/routers/publish_review.py`
- `thousand_sunny/routers/carousel_review.py`
- `thousand_sunny/templates/bridge/publish_list.html`

若實際 repo 結構要求增加一個很小的 fixture/helper 檔，可以新增，但須在交付中說明原因；不可藉此擴大功能。

## 3. 輸入

### Video／Short truth

- 只透過 `shared.release_store` 的 public `list_releases()` 與 `get_release()` 讀取，不直接複製 SQL 或修改 store。
- `CalendarItem` grain 是一個 release platform target／result，不是把三平台壓成一個 bundle。
- 有 `publish_at` 的 target／release 才進 dated calendar，轉為 `ZoneInfo("Asia/Taipei")`，`date_basis="scheduled"`。
- 沒有可信 `publish_at` 的項目進 backlog。
- 絕不可用 `updated_at`、檔案 mtime 或頁面讀取時間冒充 actual publish time。
- 詳情連結維持 `/bridge/publish/{episode}/{cut_id}`。

### Carousel truth

- 掃描 episode-local `ig-carousel/publish_jobs/pj-*.json`，以現有 `CarouselPublishJobV1` parser／schema 讀取。
- 每個 platform target state 產生一個 calendar item。
- 只有成功 published checkpoint 的 `completed_at` 可作 actual publish date，轉為台北時區，`date_basis="published"`。
- queued／pending／in-progress／failed 且沒有成功 checkpoint 的 target 進 backlog；failed result 的 `completed_at` 不是 publish date。
- retry 去重鍵為 `(episode, request_fingerprint, platform)`：採最新 job 的目前狀態，同時保留 retry 已 carry-forward 的成功 published checkpoint，避免同內容重複列出。
- 單一 malformed／unreadable job JSON 必須 fail soft：記錄 warning／diagnostic，頁面仍渲染其餘有效資料。
- 詳情連結維持 `/bridge/ig-cards/{episode}/publish`。

### Channel identity

- Podcast 長影片、Short、Carousel 的 YouTube 目標在 UI 必須明確顯示為 Podcast YouTube，而非 55 萬訂閱主頻道。
- Podcast 頻道為《張修修的不正常人類研究所》`@abnormal-human-research`，channel ID `UCvipegP35x3-OcAs--PgAig`。
- 不可把主頻道 `@shoshotw`／`UC7_BNdimJrNLPDeectTg6Ig` 當預設 Podcast target。

## 4. 輸出

- `GET /bridge/publish/calendar?month=YYYY-MM&episode=all|<episode-slug>`。
- 無 `month` 時顯示台北時區當月；非法月份回 400 或依 Bridge 既有 query 錯誤慣例 fail closed，不可 silently 顯示錯月。
- desktop 使用週日為第一欄的完整月格；每個 item 顯示時間／日期、內容標題、內容型別、平台、狀態、scheduled 或 published basis。
- date basis 必須使用不與 status 重複的可讀標籤：`scheduled` 顯示「排程時間」、`published` 顯示「實際發布時間」、無可信日期顯示「日期未定」。
- viewport 約 390px 時改成 agenda list，不要求橫向捲動。
- 頂部 episode 下拉包含「全部內容」與所有有 dated item 或 backlog 的 episode；選擇要保留 month query。
- 提供上／下月導覽，query 可分享、refresh 後狀態一致。
- backlog 是 domain projection 的明確區域，不把無日期項目硬塞到今天；UI 必須以「未列入月曆／日期未定」等中性語意呈現，不得把已 published／uploaded 但缺少可信日期的項目誤稱為「待排程」。
- empty、partial warning、error、loading/progressive-enhancement、hover、focus、active、disabled／unavailable states 都有設計；interactive controls 可鍵盤操作。
- 使用 Bridge 既有登入保護與 chassis；不新增 public JSON API。
- 頁面載入不得呼叫 YouTube、Meta、R2 或任何外部 API。

## 5. 驗收

自動測試至少覆蓋：

1. Asia/Taipei UTC 跨日與跨月轉換正確。
2. `calendar.Calendar(firstweekday=6)` 或等價邏輯產生週日起始月格。
3. release 有 `publish_at` 時按 platform 建 scheduled item；無可信日期時進 backlog；不使用 `updated_at`。
4. Carousel 只有成功 checkpoint `completed_at` 成為 published item；failed completion 留 backlog。
5. Carousel retry 按 `(episode, request_fingerprint, platform)` 去重，且 carry-forward 成功 checkpoint 不丟失、不重複。
6. malformed Carousel JSON 不使整頁失敗，UI 顯示可理解的 partial-data warning。
7. episode filter 同時作用於 dated items 與 backlog；backlog-only episode 仍在下拉。
8. route 需要登入；month／episode query 驗證正確。
9. 內容連結分別回既有 video/short 與 Carousel 發布頁。
10. UI 明確區分 long／short／carousel、platform、status、date basis，並顯示 Podcast YouTube identity。
11. 390px 無橫向 overflow，agenda DOM order 可讀；desktop 月格有 semantic weekday／date labels。
12. focus-visible、reduced motion、empty／warning states 符合 `docs/design-system.md`。
13. 測試證明 render path 不呼叫外部平台 API。

完成前執行：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe -m pytest -q tests/shared/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar_ui.py
E:\nakama\.venv-v2\Scripts\python.exe -m pytest -q tests/thousand_sunny/test_publish_review.py tests/thousand_sunny/test_carousel_review.py
E:\nakama\.venv-v2\Scripts\python.exe -m ruff check shared/publish_calendar.py thousand_sunny/routers/publish_calendar.py tests/shared/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar_ui.py
E:\nakama\.venv-v2\Scripts\python.exe -m ruff format --check shared/publish_calendar.py thousand_sunny/routers/publish_calendar.py tests/shared/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar.py tests/thousand_sunny/test_publish_calendar_ui.py
git diff --check
```

若現有 test filename 不同，先用 `rg --files tests/thousand_sunny` 找到正確 regression surface，不可靜默略過。回報每組測試的 passed／skipped 數量。

## 6. 邊界

- 只能在 `E:\nakama\worktrees\publish-calendar` 工作；不得修改 root worktree 或其他 worktree。
- 不得 commit、push、建立 PR；由主 agent 統一 review 與交付。
- 不修改 `shared/release_store.py` 的 schema／persistence semantics。
- 不修改 `CarouselPublishJobV1` schema、publish job state machine、Meta／YouTube adapter、R2 staging、credential 或實際發布行為。
- 不新增 migration，不新增資料庫／第二個 truth source，不新增 ADR。
- 不新增拖拉排程、直接編輯排程、批次發布、analytics、平台 polling 或 background scheduler。
- 不修改 `_chassis_nav.html`、ADR-055、ADR-064、ADR-065。
- 不把 YouTube Community 宣稱為 Data API 自動發布；只呈現既有 handoff／checkpoint truth。
- 不使用 Inter／Roboto、紫色漸層、均勻 SaaS card grid、硬寫色碼或新的 token namespace。
- 保留 repo 中所有不相關變更；如果發現邊界外問題，只在 Remaining work 回報。

## 交付格式

以 `[P7-COMPLETION]` 回報：逐檔說明、受影響 callers、所有測試結果、三問自審、UI aesthetic direction、remaining work。另附 `git status --short` 與 `git diff --stat`，但不要提交。
