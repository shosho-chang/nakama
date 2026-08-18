# Video Production Line（Brook）

修修 talking head 影片的製作管線 context：RAW 錄影進、DaVinci 可剪的 timeline 出。
創意分鏡層＝Director skill，機械層＝本目錄 pipeline 程式（ADR-050、ADR-051）。
字幕 handoff 由 ADR-056 收緊：本 context 只消費 Podcast Subtitle V2 的 Verified Projection ID + manifest，不把裸 SRT 當文字真相。

## Language

**Verified Projection handoff**:
Stage 4 交給本管線的 content-addressed SRT projection 與 lineage manifest；已通過 exact-copy、speaker、duration、semantic split 與 generation gates。
_Avoid_: raw.srt、corrected SRT、只傳 transcript.srt 路徑

**Director**:
分鏡創意層 — Claude 依 skill 手冊讀 Verified Projection、決定每個 beat 的 B-roll、取得外部素材、產 storyboard。
_Avoid_: planner（那是 `plan` CLI 的單次 LLM 初稿工具）、分鏡師

**Beat**:
storyboard 的最小單位 — Verified Projection 上一段 exact-copy 連續引句（start/end quote 錨定）＋一個 B-roll 決策。
_Avoid_: scene、shot、片段

**Storyboard**:
`storyboard.yaml` — 一集全部 beats 的機器可讀分鏡表；Bridge UI 審核與 render/emit 的唯一事實來源。
_Avoid_: 腳本（那是修修寫的逐字稿 script.md）

**Mistake removal / cleanup**:
拍攝失誤移除前置 stage — 單擊掌 marker ＋ WhisperX 對稿回溯產 ripple-delete timeline 與校正字幕（ADR-050 D3、PR #985）。
其字幕輸出是 `legacy_transcript.srt`，不會被 production `plan` / `run` 接受。
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

## Packaging（標題與封面）

點擊率的兩個變數。2026-07-26 grill 凍結兩條**正交**的軸——混用會問出無解的組合。

**視覺配方（visual recipe）**:
封面畫面裡「有誰／有什麼物件」的配方；**`visual_recipe` 新欄位**（ADR-054 D2 — 不動既有
`content_type` enum，那是 Project 的 youtube/blog/research/podcast 四值）。三值：`podcast`
（雙人＝修修＋來賓）／`youtube_host`（只有修修）／`youtube_book`（修修＋書封同框）。
_Avoid_: 內容類型、content_type（另一個既有欄位）、route、A/B 路線

**資訊起點（information origin）**:
發想當下手上有的素材量：`full_text`（逐字稿或文章全文，hook 可 cite 原文）／
`one_liner`（只有一句話，hook 得靠外部搜尋補料）。**與視覺配方正交**——四種組合都真實存在。
_Avoid_: A 路線／B 路線（無語意，記不住哪個是哪個）

**aspect（版面比例）**:
`16:9`／`9:16`；同一個視覺配方可出兩種比例。獨立維度，不併進視覺配方的值。
_Avoid_: 直式/橫式（口語可用，schema 用 aspect）

**cutout（去背人像）**:
透明背景 PNG 的人物素材。`youtube_*` 配方用**預建**的修修表情庫
（`Attachments/cutouts/shosho/{emotion}/{n}.png`）；`podcast` 配方 host 沿用預建庫、
guest **逐集**產生（`Attachments/cutouts/podcast/{ep_slug}/guest_v{n}_{emotion}.png` —
檔名帶 emotion 是 ADR-054 A8④，舊式 `_v{n}.png` 會讓表情匹配永遠 miss 掉入隨機 fallback）。
_Avoid_: 去背圖、頭像（頭像僅指修修，guest 也是 cutout）

**表情（emotion）**:
封閉枚舉的 7 個 zh-Hant 標籤，單一事實來源 `prompts/thumbnail/emotions.yml`（帶
en ↔ zh-Hant ↔ alias 雙向對映）。決定從 cutout 庫挑哪張。
_Avoid_: 情緒（那是標題的 angle，不是臉）

**Package（包裝）**:
一組綁定的 (標題, 封面)；YouTube Test & Compare 的測試單位，一支長片產 **3 個**。
2025-12 起平台支援 3 組 combined package 測試，勝負judged by **watch time per impression**（非 CTR）。
_Avoid_: 標題候選／封面候選（那是 package 成形前的中間產物）

**Packaging gate**:
穩態下唯一的人工介入點 —— 修修看 3 個 package 後 approve。字幕 QC／選段 veto／短片 cuts
複審都不是穩態的人工 gate；Podcast Subtitle V2 的機械 Quality Gates 仍是每個 projection 的 fail-closed 前置條件。
_Avoid_: review point（太泛）

**archetype / playbook**:
從 140 列參考語料蒸餾出的 10 個標題原型（T-A1…T-A10）＋10 個封面原型（T-V1…T-V10）
＋8 組聯合配對（JP-1…JP-8），每個帶 S/A/B/C/D/F brand-fit 評級；D/F 級禁用。
_Avoid_: 範本、template（那是 composition）

## Relationships

- **Verified Projection handoff** 來自 Stage 4 `PodcastSubtitleV2.project`；本管線先驗 manifest / generation hash 才讀 SRT
- **Director** 與 **Beat** 只能 exact-copy Verified Projection；需要改字時退回 Stage 4 `resolve`，不得在 storyboard 或本管線內 correction
- 一個 **Beat** 至多一個 **B-roll**；`asset` 類 beat 可帶多個**候選**
- **Director** 產 **Storyboard**；**Bridge UI** 兩層審核（text / visual）後才 render/emit
- **asset_requests** 由 **Director** 產出、下載方履約回 **asset_manifest**、Director 驗收後 storyboard 才算素材就緒

## Example dialogue

> **Dev:** 「這個 beat 的 stock 影片是誰去 render 的？」
> **修修:** 「`asset` 類不 render — **Director** 搜 Envato 挑好候選寫進 **storyboard**，下載走 **asset_requests** 交接、**asset_manifest** 回報，驗收就緒才進 emit。會 render 的只有 `hyperframes` 類。」

> **Dev:** 「字幕有一個字錯了，可以直接改 storyboard quote 嗎？」
> **修修:** 「不行。本管線只 exact-copy **Verified Projection handoff**；回 Podcast Subtitle V2 resolve 出新 Generation，再 project 一份新的 verified SRT。」

## Flagged ambiguities

- 「腳本」曾同時指修修的逐字稿與 storyboard — 已解：逐字稿＝`script.md`，分鏡表＝storyboard。
- 「A 路線 / B 路線」曾被當成單一 enum，實際是**兩條正交軸**混講 — 已解：資訊起點
  （full_text / one_liner）× 視覺配方（podcast / youtube_host / youtube_book）。壓成一個
  enum 會讓「讀書心得出影片」（full_text × youtube_book）無家可歸。
- 「情緒」曾同時指標題的點擊 angle 與封面的臉部表情 — 已解：標題＝**情緒 angle**
  （好奇缺口/恐懼損失/…），封面＝**表情**（emotions.yml 封閉枚舉 7 值）。
- **Packaging 是否該獨立成 context 未定**：`thumbnail_worker` 與 `video/compositions/thumbnail_*`
  在本 context，但 `shared/cutout_library.py`＋`shared/thumbnail_funnel.py` 依 ADR-052
  邊界規則（`shared/` 只收 2+ agent 共用）其實該住 Brook package。待本 grill 決定 code 落點後回收。
- 「planner」曾泛指分鏡決策者 — 已解：Director 是決策者；planner 專指 `plan` CLI 的一次性 LLM 初稿工具。
- 「校正字幕」曾指任何看起來已修改過的 SRT — 已解：Stage 5 只承認帶 lineage manifest 的 **Verified Projection handoff**；裸檔不是可接受 input。
