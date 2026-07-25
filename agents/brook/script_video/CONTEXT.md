# Video Production Line（Brook）

修修 talking head 影片的製作管線 context：RAW 錄影進、DaVinci 可剪的 timeline 出。
創意分鏡層＝Director skill，機械層＝本目錄 pipeline 程式（ADR-050、ADR-051）。

## Language

**Director**:
分鏡創意層 — Claude 依 skill 手冊讀校正字幕、決定每個 beat 的 B-roll、取得外部素材、產 storyboard。
_Avoid_: planner（那是 `plan` CLI 的單次 LLM 初稿工具）、分鏡師

**Beat**:
storyboard 的最小單位 — 字幕上一段連續引句（start/end quote 錨定）＋一個 B-roll 決策。
_Avoid_: scene、shot、片段

**Storyboard**:
`storyboard.yaml` — 一集全部 beats 的機器可讀分鏡表；Bridge UI 審核與 render/emit 的唯一事實來源。
_Avoid_: 腳本（那是修修寫的逐字稿 script.md）

**Mistake removal / cleanup**:
拍攝失誤移除前置 stage — 單擊掌 marker ＋ WhisperX 對稿回溯產 ripple-delete timeline 與校正字幕（ADR-050 D3、PR #985）。
_Avoid_: 剪輯（DaVinci 裡修修做的才叫剪輯）

**Big Title Transition**:
滿版章節卡 — 「第 N 點」等章節邊界觸發、聲音不斷、時長錨定語音（1.5–3s）。component 名 `transition_title`。
_Avoid_: 轉場（泛稱）、chapter card

**外供素材槽位（external asset slot）**:
Director 不自動產製、由修修丟檔進 `assets/` 的 beat 類型（目前：螢幕錄影）。

**asset_requests / asset_manifest（素材交接雙檔）**:
`asset_requests.yaml`（意圖：搜尋詞、首選 URL、目標路徑、氛圍/負面約束）＋
`asset_manifest.yaml`（履約：落地路徑、license 註記、失敗原因）。Director 寫
requests、下載方（預設 Codex computer use，可換人工）回 manifest、Director 逐項
驗收（存在＋sha256＋幀率 conform）後素材才算就緒。
_Avoid_: assets_queue（panel v2 §2 前的舊單檔名，已拆雙檔）

**候選（primary / alternates）**:
stock 類 beat 帶一個首選＋兩個備選預覽連結，修修審核時圈選。

## B-roll 類型（v1 詞彙，schema `component` / `render_target`）

| 類型 | component | render_target | 來源 |
|---|---|---|---|
| 大數字卡 | `bigstat` | `hyperframes` | 參數 render |
| 章節卡 | `transition_title` | `hyperframes` | 參數 render |
| 概念動畫 | 菜單 component 或即席 composition | `hyperframes` | render（即席必留資產） |
| 書封卡 | `book_cover` | `hyperframes` | 封面圖＋參數 render |
| 金句卡 | `quote_card` | `hyperframes` | 參數 render |
| 文獻 highlight | `doc_highlight` | `hyperframes` | PyMuPDF 定位＋頁面圖 render |
| Stock 實拍 | — | `asset` | Envato（批次交接 Codex） |
| KOL 片段 | — | `asset` | yt-dlp 指定秒數（護欄＋出處） |
| 螢幕錄影 | — | `asset` | 修修外供 |

v1.5 預留：`keypoint_overlay`（透明疊加字卡，卡 alpha 輸出驗證）。

## Relationships

- 一個 **Beat** 至多一個 **B-roll**；`asset` 類 beat 可帶多個**候選**
- **Director** 產 **Storyboard**；**Bridge UI** 兩層審核（text / visual）後才 render/emit
- **asset_requests** 由 **Director** 產出、下載方履約回 **asset_manifest**、Director 驗收後 storyboard 才算素材就緒

## Example dialogue

> **Dev:** 「這個 beat 的 stock 影片是誰去 render 的？」
> **修修:** 「`asset` 類不 render — **Director** 搜 Envato 挑好候選寫進 **storyboard**，下載走 **asset_requests** 交接、**asset_manifest** 回報，驗收就緒才進 emit。會 render 的只有 `hyperframes` 類。」

## Flagged ambiguities

- 「腳本」曾同時指修修的逐字稿與 storyboard — 已解：逐字稿＝`script.md`，分鏡表＝storyboard。
- 「planner」曾泛指分鏡決策者 — 已解：Director 是決策者；planner 專指 `plan` CLI 的一次性 LLM 初稿工具。
