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
`storyboard.yaml` — standalone script-driven video 一集全部 beats 的機器可讀分鏡表；是 `/brook/video`
review／render／emit 的唯一事實來源，不是 Podcast derivative finished-cut 的 authority。
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

**Finished Cut Production**:
把 Podcast derivative 的已核准 cut 推進為可 review 成品的唯一 Stage 5 production module；Long/Short 以
不同 policy 使用同一套 ordering、acceptance authority、Resolve transaction 與 release publication。
_Avoid_: Long orchestrator（只描述 Long semantic run）、legacy pipeline（語意不精確）

**AcceptedStage**:
Finished Cut Production 內部唯一的 Director／DP／visual stage authority；由 current request proposal 通過
aggregate 驗收後產生，不能由 worker、CLI、format policy 或 `state.json` 建立。
_Avoid_: approved response、approved state（兩者只是 proposal／view，不是 authority）

**Semantic Evidence Range**:
Director 以 current ADR-064 cue IDs 選出的完整語意證據範圍；core 從 Editorial Cut Context 推導文字、
hash、section 與語意 t0/t1。它解釋「這個視覺決策根據哪段論述」，不是素材或字卡實際停留時間。
_Avoid_: display window、clip duration

**Visual Placement**:
DP 從單一事件的 Semantic Evidence Range 中選出的實際上畫 cue subset；core 驗證 ordered、contiguous、
same-section、subset 後唯一 mint placement t0/t1。Intentional A-roll 沒有 Visual Placement；Big Title
Transition 只能用 canonical `transition_before` section 的第一個 current cue 作語意證據，實際上畫固定從
section t0 開始 3 秒（並受 cut duration 上限約束），worker 不能改時間。
_Avoid_: Director anchor（是語意證據）、worker timing（worker 不能 mint）

**Pre-release Event Correction**:
fresh `ProductionRun` 在每個 Director／DP／visual checkpoint 後、`MaterializationPlan` 產生前的單事件
修正。operator 只能提供 command、stage、event 與 feedback；aggregate 從 exact current
`AcceptedStage` mint `event_retry`，保留其他事件、使該 stage 之後的 current authority 失效但不刪歷史。
尚未執行的下游可首次 full-stage，已執行的下游只能同一事件 cascade；不能先發佈壞 Release。
_Avoid_: full-stage rerun、手改 state、Targeted Revision（後者從 exact current Release 開新 command）

**Finished Cut Release**:
一個已核准 cut 經 Director、DP、visual review（問題事件才 targeted retry）、Resolve materialization 與 preview probe 後，可進 finished-cut review 的唯一 production snapshot。
_Avoid_: CURRENT（ADR-065 pointer）、review manifest（多支 Release 的 index）

**Active Asset Store**:
目前 Finished Cut Releases 可解析與重建的 episode-level content-addressed 衍生視覺素材 store；
**Authoritative Episode Source** 僅指 ADR-064 Editorial Master media／SRT／contract，由 Release 另行綁定；
camera correction 另屬明示的 **Video Correction Source**，只能 video-only 且禁止 raw audio；raw camera／
audio 不是 Authoritative Episode Source，也不是 fallback。
_Avoid_: assets cache（它是 production truth，不是可任意清除的 cache）

**Compact Asset Receipt**:
Active Asset Store 每個 object 的精簡 provenance。neutral acquisition 只保留 media digest／bytes、經清理的
source／license facts 與 opaque Forensic Archive object ref；current recipe render 明示為
`current_generated`，不偽造 license。舊 receipt 的 path／cut／revision／job／semantic rows 不進 active index。
_Avoid_: acquisition receipt copy（舊 receipt 原文只屬 Forensic Archive）

**Forensic Archive**:
位於 runtime root 外、不會被 production discovery 掃描的歷史 evidence store；只能 data-only 還原到隔離
staging root，不能產 acceptance／Release、寫 current 或呼叫 Bridge／watcher／Resolve。
_Avoid_: legacy folder（沒有說清楚它是否仍可執行）

**Legacy Creative DAG**:
ADR-065 的 revision-scoped Director／DP／semantic-audit executable pipeline；ADR-066 cutover 後只保留歷史文件與 Forensic Archive，不是 production fallback。
_Avoid_: 舊版（可能混指歷史檔、Short CLI 或相容 reader）

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
複審都是**開發期回饋**，不是穩態 gate。
_Avoid_: review point（太泛）

**archetype / playbook**:
從 140 列參考語料蒸餾出的 10 個標題原型（T-A1…T-A10）＋10 個封面原型（T-V1…T-V10）
＋8 組聯合配對（JP-1…JP-8），每個帶 S/A/B/C/D/F brand-fit 評級；D/F 級禁用。
_Avoid_: 範本、template（那是 composition）

## Relationships

- 一個 **Beat** 至多一個 **B-roll**；`asset` 類 beat 可帶多個**候選**
- **Director** 產 **Storyboard**；**Bridge UI** 兩層審核（text / visual）後才 render/emit
- **asset_requests** 由 **Director** 產出、下載方履約回 **asset_manifest**、Director 驗收後 storyboard 才算素材就緒
- 已核准 cut 經 **Finished Cut Production** 產一個 **Finished Cut Release**；Bridge 只讀 Release index，不讀 route-specific state
- worker response 必須先成為 current **AcceptedStage** chain，才能產 materialization plan／Release；`state.json` 不是 authority
- Director 的 **Semantic Evidence Range** 與 DP 的 **Visual Placement** 是兩個 authority：Event／Release 保留前者，component／derived asset／timeline 使用後者；Visual reviewer 同時看到 final asset ref 與 core-minted placement window
- fresh run 每個 current **AcceptedStage** 都是 operator checkpoint；**Pre-release Event Correction** 只替換一個 event 的 current authority，歷史 AcceptedStage append-only
- **Finished Cut Release** 的衍生視覺媒體只從 **Active Asset Store** 解析；source media/SRT 只從 ADR-064 Editorial Master contract 解析；preview/subtitle/projection/recipes 從 Release-bound artifacts 解析；camera correction 僅可用明示的 video-only source；**Forensic Archive** 不可被 production discovery
- **Legacy Creative DAG** 在 ADR-066 cutover 後沒有 production caller，也不能產生 **Finished Cut Release**

## Example dialogue

> **Dev:** 「這個 beat 的 stock 影片是誰去 render 的？」
> **修修:** 「`asset` 類不 render — **Director** 搜 Envato 挑好候選寫進 **storyboard**，下載走 **asset_requests** 交接、**asset_manifest** 回報，驗收就緒才進 emit。會 render 的只有 `hyperframes` 類。」

> **Dev:** 「舊 revision 還留在硬碟，可以讓新 cut 沿用嗎？」
> **修修:** 「不行。它只能在 **Forensic Archive** 明確還原供稽核；新的 **Finished Cut Release** 必須從目前 run、ADR-064 Editorial Master contract 與 **Active Asset Store** 產生。」

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
- 「舊版」曾混指可執行 ADR-065 流程、歷史 evidence、Short CLI 與相容 reader — 已解：可執行流程＝**Legacy Creative DAG**；歷史 evidence＝**Forensic Archive**；兩者都不是新 production adapter。
