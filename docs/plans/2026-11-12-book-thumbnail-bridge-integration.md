# 書籍介紹影片封面編輯器整合進 Bridge — 規劃

> **2026-11-12 實作狀態**：S1–S6 已實作（同日、同分支）。
> - 修修裁決：先不接任何上游觸發，接下來幾支 YouTube 影片的標題封面都手動叫 agent 做。
> - 書籍介紹與主題介紹共用同一套版型；專案類別 `youtube-health` 同日改名為 `youtube-explanation`「主題介紹」。
>
> 實作與本文原規劃的差異：
> - **獨立頁** `/bridge/youtube-packaging/<slug>`（`thousand_sunny/routers/youtube_packaging.py`），沒有塞進 podcast 的 packaging board。Podcast board 對 YouTube 集數一律 303 導過去，列表也直接連過去。
> - **版型**放在 `thousand_sunny/static/shosho/youtube-cover.{js,css}`，Bridge 預覽與桌機出圖（`scripts/render_youtube_cover.py`，以 file:// 載入）共用，沒有走 hyperframes composition。
> - **檔名**：`youtube_cover.json`（原規劃的 `book_cover.json`）；`book` 圖層改名為 `feature`（主圖）。
> - **搬遷**：用 `.claude/skills/book-thumbnail/scripts/init_youtube_packaging.py`，本集已搬進 vault 並出圖驗證（跟原型成品平均像素差 0.16）。
> - **S7**（Bridge 顯示成效）未做：7 天檢查排程任務已改寫 vault 的 `performance_checks.json`，Bridge 顯示留待下一步。

> 2026-11-12。修修：「計劃如何把這個頁面整合到我們的 Nakama Bridge 裡面。」
> 本文是 P9 規劃（多模組、跨 VPS／桌機），只寫任務切分，不含實作。

## 0. Pipeline 錨點

- **Line 2**（讀書心得 → 主頻道書籍介紹影片）
- **Stage 5**：影片 channel 的 packaging（標題＋封面）
- **Stage 6**：上架標題 A/B
- **Stage 7**：7 天成效追蹤

`CONTENT-PIPELINE.md` 的 Lines × Stages 矩陣在 Line 2 × 影片 channel 那格，目前沒有 packaging 工具。這次就是補這一格。

## 1. 現況（本機原型，2026-11-12 已實際用來上片 XiuLE6E9VVY）

| 部件 | 位置 | 行為 |
|---|---|---|
| 編輯器頁面 | `.claude/skills/book-thumbnail/editor/editor.html` | 1280×720 舞台；圖層拖曳／滾輪縮放；背景、去背照、書封外框 6 種、大字斜度；〈大字候選〉與〈標題〉分開 |
| 本機 server | `.claude/skills/book-thumbnail/editor/server.py`（port 8765） | `/save` 存 `spec.json`＋headless Chromium 截同一份 DOM 出圖；`/tracking` 存 `title_tracking.json` |
| 發想 | `title-brainstorm`（emit_packages）＋`book-thumbnail/scripts/emit_bigtext.py` | 寫 `packages.json`（`visual_recipe: youtube_book`）與 `<cut>/bigtext.json`，已鏡射到 vault |
| 追蹤 | 排程任務 `wealth-ladder-title-ab-7day-check`（11/19 21:00） | Analytics API＋Studio 讀 A/B 結果，寫 `performance-7day.md` |

**原型的限制**：
- 只在桌機、只在對話開著時可用，資料夾寫死在 `E:\data\AgentOutput\...`。
- 跟 Bridge 的 packaging 資料（vault `Attachments/packaging/<slug>/`）只有發想結果是共用的。
- 封面配方、標題紀錄都在本機工作資料夾，Bridge 看不到。

## 2. 硬約束（沿用既有決策，不重新發明）

1. **VPS 不 render**（ADR-054 D11）：VPS 上的 Bridge 只存配方，出圖一律由桌機 `scripts/render_watcher.py` 接手。這跟 podcast 組封面是同一個模式。
2. **資料 SoT＝vault `Attachments/packaging/<episode_slug>/`**，用 Syncthing 同步。每多一個寫入點就多一個 conflict 面。所以：
   - 封面配方、標題紀錄各自一檔，跟 `packages.json` 分開。
   - 出現 `*.sync-conflict-*` 時 fail loud，沿用 packaging router 現行政策。
3. **舞台與出圖同一份 DOM／CSS**：拖到哪裡、出圖就在哪裡。podcast 版的 board 已經是這個做法，book 版照抄。
4. **UI 零 LLM**：Bridge 上沒有「重新發想」按鈕，發想仍回 Cowork 跑 skill。
5. 新增 vault 檔案，要同一個 PR 更新 `docs/VAULT-LAYOUT.md`。
6. UI 進 `docs/design-system.md` 體系。merge 前要 dev server＋瀏覽器實際走過 golden path 與 edge case。

## 3. 目標架構

```
vault Attachments/packaging/<slug>/
├── packages.json            既有：titles（youtube_book）
├── full/bigtext.json        既有：大字候選
├── book_cover.json          新：封面配方（= 原型 spec.json ＋ requested_at ＋ rendered_png）
├── title_tracking.json      新：上架標題紀錄（微調文字、已上頻道＝A/B、影片網址）
├── performance_checks.json  新：Stage 7 檢查結果（排程任務寫入）
├── assets/                  新：本集背景授權檔、書封（VPS 要讀得到才能在舞台顯示）
└── cover-full.png           出圖（watcher 回填）
```

| 端 | 職責 |
|---|---|
| Bridge（VPS／本機 8000） | `/bridge/packaging/<slug>` 偵測 `visual_recipe == youtube_book` → 顯示書籍版組封面區（舞台＋圖層＋背景／去背照／外框／大字）與〈標題〉紀錄區；存配方寫 `book_cover.json`（帶 `requested_at`）；標題紀錄寫 `title_tracking.json` |
| 桌機 render_watcher | 掃到 `book_cover.json` 的 `requested_at` 比上次新 → 用 headless Chromium 截同一份 composition → 回填 `rendered_png`；Bridge 用既有的 render-status 輪詢機制顯示成品 |
| 排程任務（Stage 7） | 讀 `title_tracking.json` 的影片網址，寫 `performance_checks.json`；Bridge 在標題區顯示最近一次檢查 |

## 4. 任務切分（P9 六要素）

### S1 — Schema ＋ vault 版面

- **目標**：定義 `BookCoverRecipeV1`、`TitleTrackingV1`、`PerformanceCheckV1`，與原型的 `spec.json`、`title_tracking.json` 欄位一一對應。
- **範圍**：`shared/schemas/packaging.py`（或新檔 `shared/schemas/book_packaging.py`）、`docs/VAULT-LAYOUT.md`。
- **輸入**：原型的 `spec.json`、`title_tracking.json`（本集實檔可當 fixture）。
- **輸出**：pydantic 模型＋單元測試（A/B 上限 3、已上頻道＝A/B、空網址不覆蓋既有網址、日期格式）。
- **驗收**：本集兩份原型檔能無損 parse 再序列化；pytest 綠。
- **邊界**：不改 `PackagesFileV1`、`ApprovalFileV1` 既有欄位。

### S2 — 抽出共用 composition ＋ 出圖腳本

- **目標**：把 `editor.html` 的封面 DOM／CSS／`draw()` 抽成 `video/compositions/thumbnail_book/`，讓舞台與出圖共用。
- **範圍**：新 composition 目錄、`.claude/skills/book-thumbnail/scripts/render_book_cover.py`（輸入 `book_cover.json` → PNG＋320 縮圖）。
- **輸入**：S1 schema、現行 `editor.html`。
- **輸出**：composition＋render 腳本＋回歸測試。測試用本集配方 render，跟目前 `cover.png` 做 SSIM 比對，門檻 ≥ 0.99，沿用 Foundry 的判準。
- **驗收**：本集封面重出後跟原型成品視覺一致；LINE Seed 字型與 6 種外框都正確。
- **邊界**：不動 podcast 的 `thumbnail_full`、`thumbnail_reaction`。

### S3 — render_watcher 支援書籍封面

- **目標**：watcher 撿 `book_cover.json` 的新配方並出圖回填。
- **範圍**：`scripts/render_watcher.py`（新增 youtube_book 分支、state key）、對應測試。
- **輸入**：S1、S2。
- **輸出**：分支＋單元測試（同配方只出一次、已有成品不重出、失敗寫 `last_error` 不無限重試）。
- **驗收**：`--once` 對本集跑出 `cover-full.png`，Bridge render-status 讀得到。
- **邊界**：podcast 路徑零行為變更。現有 watcher 測試全綠。

### S4 — Bridge 路由

- **目標**：Bridge 能讀寫書籍封面配方與標題紀錄，也能提供素材。
- **範圍**：`thousand_sunny/routers/packaging.py`。新增：
  - `POST /{slug}/book-cover`：存配方
  - `POST /{slug}/title-tracking`：存標題紀錄
  - `GET /{slug}/book-asset/{name}`：本集素材
  - `GET /cutout-library/{emotion}/{n}`：修修去背照庫
- **輸入**：S1。
- **輸出**：路由＋測試。測試涵蓋：auth、路徑穿越防護（沿用 `_SAFE_RECEIPT_ID` 類 regex）、sync-conflict fail loud、整檔驗證後才落盤。
- **驗收**：pytest 綠；本機 8000 用 curl 走過存取。
- **邊界**：不改 approve、compose 等既有 podcast 路由的行為。

### S5 — Bridge UI：書籍版組封面區＋標題紀錄區

- **目標**：在 packaging board 內，`youtube_book` 的 cut 顯示原型同等功能。
- **範圍**：`thousand_sunny/templates/bridge/packaging_board.html`（或拆 partial `_book_composer.html`）、對應 JS。
- **功能**：
  - 預覽固定、右欄自己捲
  - 標題可編輯、勾「已上頻道」（最多 3）、填影片網址、自動存
  - 大字候選與標題分開
- **輸入**：S2 composition、S4 路由。
- **輸出**：UI＋瀏覽器實測紀錄。驗證 1440 與 390 兩種寬度、淺色深色、拖曳／滾輪、存配方→watcher 出圖→顯示成品。
- **驗收**：修修在 Bridge 上完成一次「調版面 → 存 → 看到成品」與「改標題 → 勾已上頻道」，資料出現在 vault。
- **邊界**：遵守 design-system（底線分頁、無卡片、無藥丸）；podcast 組封面 UI 不變。

### S6 — 本集資料搬遷＋原型去留

- **目標**：把 20261027-wealth-ladder 的資料搬進 vault，讓 Bridge 接手。
- **搬遷內容**：`editor-work/spec.json`、`title_tracking.json`、授權背景 `stock-licensed/`。
- **範圍**：一次性搬遷腳本、`book-thumbnail/SKILL.md` 改指 Bridge。
- **驗收**：Bridge 上本集封面、標題紀錄與原型一致；排程任務改讀 vault 路徑。
- **邊界**：原型 editor 保留為離線 fallback（或刪除，視修修決定），不留兩個都能寫的入口。

### S7 — Stage 7 成效顯示

- **目標**：Bridge 標題區顯示最近一次成效檢查，並標出 A/B 勝出標題。
- **成效檢查內容**：觀看、平均觀看百分比、點閱率、A/B 狀態。
- **範圍**：排程任務的 prompt 改寫 vault 的 `performance_checks.json`；S5 UI 讀取顯示。
- **驗收**：11/19 那次檢查的結果出現在 Bridge。
- **邊界**：只讀 YouTube，不改標題、不動測試。

**依賴**：S1 → S2 → S3。S1 → S4 → S5（S5 同時依賴 S2）。S6、S7 在 S5 之後。S2 與 S4 可並行。

## 5. 風險與已知問題

- **書籍集已經出現在 Bridge packaging 列表**：本集 `packages.json` 已寫進 vault（emit_packages 預設鏡射），而 `packaging_list` 會掃 vault 目錄。在 S5 之前，board 會用 podcast 版面顯示這集，packages 是空的，approve 也湊不滿 3 個 package。S5 之前要嘛讓列表略過 `youtube_book`，要嘛標成「書籍版尚未支援」。
- **Syncthing conflict**：Bridge（VPS）與桌機同時寫 `title_tracking.json`，這次原型就發生過兩邊同時寫的覆蓋問題。S4 寫入前要比對 `updated_at`，不一致就回 409，不做 last-write-wins。
- **桌機沒開就不會出圖**：跟 podcast 一樣。Bridge 上要顯示「等桌機出圖」狀態，不要看起來像壞掉。
- **素材體積**：授權背景原檔 5184×3456（約數 MB）進 vault 會被同步到所有裝置。建議 S2 出圖時另存一份縮到 2560 寬的工作檔，原檔留在桌機。
- **去背照庫品質**：庫裡 17 張原本是 u2net 去背。2026-11-12 全庫稽核（`E:\data\AgentOutput\cutout-audit-20261112\`）發現 16 張有問題：衣服印花被挖洞、手錶與手指被挖掉、椅背殘留。當天已全部換成 BiRefNet 版，u2net 原檔備份在 `original-u2net/`。
  - 庫裡另有兩組重複照片：serious/2＝pointing/3、serious/1＝thoughtful/1，表情分類待修修決定。
  - `scripts/import_shosho_cutouts.py` 仍用 u2net（hyperframes remove-background），以後重拍匯入前要改用 BiRefNet，否則會再出現同樣問題。
