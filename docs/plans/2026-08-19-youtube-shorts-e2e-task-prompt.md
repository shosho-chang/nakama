# YouTube Shorts E2E + 外部合作夥伴成品匯入 — P9 Task Prompt

- Date: 2026-08-19
- Stage anchor: Stage 6 Publishing
- Mode: P9（跨 importer / preflight / Bridge / uploader / reconciliation / tests）
- Branch: `codex/social-upload-inventory`
- Upstream decision: ADR-055

## 1. 目標

讓一支已由外部合作夥伴剪好的 vertical Short，不經 Resolve，也能安全地完成：

```text
partner MP4
  -> deterministic preflight
  -> canonical episode/highlights/exports import
  -> Release(format=short) + YouTube target(draft)
  -> Bridge title/description/schedule review
  -> private resumable upload
  -> optional native publishAt
  -> videos.list reconciliation
  -> release target published
```

Definition of Done 不是「API 回 200」，而是一支真實 partner asset 經 private dry run 後，
再以明確排程完成一次公開 probe，DB 狀態由 `draft → approved → uploading → uploaded →
published`，YouTube Studio 能確認該片被分類為 Short，且重跑不會重複上傳。

## 2. 範圍

### 新增

- `agents/usopp/youtube_short_preflight.py`
  - Usopp-owned domain logic；不要放 `shared/`，因目前只有 Usopp publishing 使用。
  - 用 ffprobe JSON 讀 container、video/audio streams、duration、width、height、SAR/DAR、
    codec、fps。
  - 回傳 structured `ShortPreflightResult`，區分 hard errors 與 warnings。
- `scripts/publish_register_external_short.py`
  - 外部成品的唯一 CLI ingress。
  - 驗證 → copy 到 canonical exports → source/destination SHA-256 比對 →
    `register_release(..., format="short")` → `ensure_target(..., "youtube")`。
  - 不依賴 `candidates.json`、`winners.json`、Resolve 或 packaging files。
- `tests/agents/usopp/test_youtube_short_preflight.py`
- `tests/scripts/test_publish_register_external_short.py`

### 修改

- `scripts/publish_upload.py`
  - short 不再呼叫 `captions.insert`；long 維持 tight SRT CC。
  - 新增 `--reconcile`，以 `videos.list(part="status,processingDetails")` 對已有
    `video_id` 的 target 同步 processing / privacy 結果；public 才轉 `published`。
  - reconciliation 必須可重跑，不得建立新影片。
- `thousand_sunny/routers/publish_review.py`
  - Short review 不尋找／宣稱 tight SRT 會上傳成 CC。
  - 傳給 template 明確的 `cc_policy`；long=`sidecar_required`、short=`burned_only`。
- `thousand_sunny/templates/bridge/publish_cut.html`
  - long 才 render default `<track>` 與 CC 缺失警告。
  - short 顯示「字幕已燒入畫面；此流程不另上 CC」，避免 double-subtitle 誤導。
- `tests/test_publish_upload.py`
  - 補 format-based caption routing 與 reconciliation tests。
- `tests/test_publish_review_subs.py`
  - long 保留 default track；short 不 render track、不顯示缺 CC 警告。

### 不需要修改

- `shared/release_store.py` 與 DB schema：現有 `format=short`、file path、duration、bytes、
  YouTube target 欄位足以完成 tracer bullet。
- `scripts/publish_prep.py`：內製 Resolve Shorts 繼續走原路；外部成品走獨立 importer。

## 3. 輸入

### 外部檔案

- 一支公開權利無虞、允許測試發布的 partner MP4。
- 必須已燒字幕；CLI 要求明確的 `--captions-burned` acknowledgment，因程式無法可靠
  自動畫面辨識字幕是否存在。
- CLI 同時要求 `--rights-cleared`，由操作人確認音樂、影像與測試發布權利；這不是 Content ID 預檢或法律審核。
- 建議首支 fixture：1080×1920、H.264、AAC、30 fps、3–180 秒、無未授權音樂。

### CLI contract

```powershell
python scripts/publish_register_external_short.py `
  --episode-dir "G:\footages\20260819 partner-shorts-e2e" `
  --file "D:\partner-delivery\short-01.mp4" `
  --cut-id "partner-S01" `
  --work-title "合作夥伴 Short E2E 01" `
  --captions-burned `
  --rights-cleared
```

### 既有上游依賴

- `shared.release_store.register_release / ensure_target / update_target`
- `thousand_sunny/routers/publish_review.py` 的既有 manual title/description/schedule 表單
- `scripts/publish_upload.py` 的 OAuth、resumable upload、video_id duplicate guard
- `data/youtube_token.json`
- ffmpeg / ffprobe on PATH

### Preflight policy

Hard fail：

- 檔案不存在、不是 regular file、空檔或 ffprobe 失敗；
- 沒有 video stream 或沒有 audio stream；
- duration 不在 3–180 秒；
- width/height 無法解析；
- 是 landscape（`width > height`）；YouTube Short 接受 vertical 或 square；
- `--captions-burned` 未提供；
- `--rights-cleared` 未提供；
- canonical destination 已存在且 bytes/hash 不同；
- 同 `(episode, cut_id)` 已有 `video_id`。

Warning but allow：

- 不是 1080×1920；
- display aspect ratio 不是常用的 9:16 或 1:1，但仍是 vertical/square；
- codec 不是 H.264 / AAC；
- fps 不在 23–60；
- duration >120 秒但 <=180 秒（平台可接受，偏離目前內部 60–120 秒內容規格）。

另外，超過 60 秒的 Short 若含任何有效第三方 Content ID claim，YouTube 會將影片全球封鎖；合作夥伴交付物必須特別確認配樂、素材與授權範圍。

所有 ratio 判斷使用 display dimensions（含 SAR），不能只比較 coded width/height。

## 4. 輸出

### Importer output

- Canonical media：`<episode>/highlights/exports/<cut_id>.mp4`
- 一筆 `releases`：
  - `episode = episode_dir.name`
  - `cut_id = CLI cut-id`
  - `format = short`
  - `file_path = canonical absolute path`
  - `duration_sec / file_bytes = preflight result`
- 一筆 `release_targets(platform=youtube, status=draft)`
- stdout JSON：preflight facts、warnings、canonical path、release ID、target ID、SHA-256。

Importer **不生成** title/description，不偽造 packaging approval；使用者在既有 Bridge surface
手動填寫並核准。

### Reconciliation output

```powershell
python scripts/publish_upload.py --reconcile `
  --episode "20260819 partner-shorts-e2e" `
  --cut "partner-S01"
```

- private / scheduled 未到點：保持 `uploaded`；回報 privacy、processing 與 publishAt。
- public：原子更新 `status=published`、保留 video_id/url。
- processing failed / rejected：轉 `failed` 並寫明 platform reason；不得重傳。
- video not found：fail loud；不得清除既有 video_id，也不得自動建立 replacement。

## 5. 驗收

### Automated tests

1. ffprobe happy path：1080×1920 / H.264 / AAC / 30fps / 60s 通過。
2. 1920×1080、181s、無 audio、壞 JSON、ffprobe non-zero 都 hard fail。
3. 1080×1080 可通過；非標準 codec/fps 只 warning。
4. Importer copy 後 source/destination hash 相同，Release/target 欄位正確。
5. 重跑相同 source/cut idempotent；destination 不同 hash fail closed。
6. 已有 `video_id` 的 target 不可被 importer 改檔或重設狀態。
7. `_upload_one()`：long 會呼叫 caption uploader；short 完全不呼叫。
8. reconciliation：private 保持 uploaded、public → published、processing failure → failed、
   404 不清 video_id。
9. Bridge：long 有 default track；short 無 track，顯示 burned-only policy。
10. Targeted tests 全綠；執行環境先依 `requirements.txt` 補齊 `anthropic`，避免重現本次
    autouse fixture setup blocker。

建議指令：

```powershell
python -m pytest `
  tests/agents/usopp/test_youtube_short_preflight.py `
  tests/scripts/test_publish_register_external_short.py `
  tests/test_publish_upload.py `
  tests/test_publish_review_subs.py `
  tests/shared/test_release_store.py -q
```

### Supervised production E2E

分兩段，避免第一次就公開：

#### Pass 1 — private upload

1. 執行 importer。
2. 開 `/bridge/publish/{episode}/{cut_id}`，看完整影片，確認直式構圖、音訊與燒字幕。
3. 填 `[E2E PRIVATE] ...` title、description；publish time 留空。
4. Approve + Upload。
5. 確認 target=`uploaded`、有 video_id/url，YouTube Studio 顯示影片且未公開。
6. 重按／重跑 uploader，確認因既有 video_id 而 skip，YouTube 沒有第二支。

#### Pass 2 — scheduled public probe

1. 選一支確定可公開、無 NDA／授權／音樂問題的 partner Short。
2. 重新匯入為新的 cut id；在 Bridge 排 15–30 分鐘後發布。
3. 到點後執行 `--reconcile`。
4. DB target 必須為 `published`，public permalink 可開，YouTube Studio 明確認為 Short，且沒有
   Content ID 封鎖或其他版權限制。
5. 記錄實際 publish timestamp 與 video ID；不自動 delete。若要刪除，使用者在 Studio
   手動處理，避免 E2E script 具有 destructive delete 能力。

## 6. 邊界

- 不碰 Meta、Instagram、Facebook、Threads。
- 不新增 DB migration；這個 tracer bullet 不儲存完整 preflight JSON。
- 不修改 Resolve 內製 Shorts pipeline。
- 不讓 external importer 自動 publish、approve、填文案或設定 schedule。
- 不接受 browser 直接上傳 partner file 到 VPS；本 slice 是桌機 CLI ingress，維持 ADR-055
  「桌機 uploader、VPS/control plane」拓撲。
- 不自動 normalize/transcode partner delivery；preflight 不合格就退件給合作夥伴或另開
  media-normalization slice，不能在 importer 裡偷偷重壓畫質。
- 不自動覆寫 canonical MP4；同路徑不同 hash 一律 fail closed。
- 不自動刪除 YouTube 測試影片。
- `--rights-cleared` 只記錄操作人的權利確認；不可宣稱這等同 Content ID 預檢或法律審核。
- 不把 private upload 稱為完整 E2E；只有 scheduled public + reconciliation 到
  `published` 才達 Definition of Done。
- 不用 `--force` 規避 duplicate guard。

## 實作者三問自審

- 方案正確：partner asset 是否真的繞過 Resolve，但仍進同一個 Release Target / Bridge /
  uploader state machine？
- 影響全面：long CC、short burned captions、existing uploaded targets、Bridge preview、
  crash/retry/reconcile 是否都有測？
- 回歸風險：是否保證 long path 不變、short 不 double-caption、重跑不重複上傳、
  importer 不覆寫已發布成品？
