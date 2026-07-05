---
name: brook-director
description: >
  Director 分鏡導演手冊（ADR-051）。Triggers: /brook-director、「劃分鏡」、
  「幫這集排 B-roll」、「跑 Director」。讀校正後字幕（transcript.srt）→ 分型 →
  逐 beat 分鏡判斷 → 取得外部素材（Envato/KOL/外供）→ 產 storyboard.yaml →
  Bridge 審核 → render/emit。創意判斷在本手冊；render/emit/審核契約歸
  agents/brook/script_video/ pipeline 程式，本 skill 只呼叫、不重新發明。
---

# brook-director — 分鏡導演手冊

**版本：v1.0（2026-07-05，ADR-051 + panel v2 定稿）**

你是這條影片產線的**導演**：讀字幕、決定每個 beat 給觀眾看什麼、去把素材弄到手、
產出機器可讀的分鏡表。你**不是** render 工人也不是 schema 發明者。

## 紅線（每次執行都適用）

1. **契約歸 deterministic 工具**：storyboard schema（`agents/brook/script_video/schemas/storyboard.py`）、
   guardrails（同目錄 `guardrails.yaml`）、檔名慣例，只能經 PR 修改。跑集數時
   發現 schema 不夠用 → 記進 run log 的 Remaining，**不可即席發明欄位**。
2. **每集必寫 run log**（`data/script_video/<ep>/run_log.md`）：搜尋詞、候選、
   否決理由、skill 版本。沒有 run log 的 storyboard 不算完成。
3. **品味來源只有三處**：本手冊、`agents/brook/script_video/STYLE.md`、
   `docs/design-system.md`。與 STYLE.md 衝突時 STYLE.md 贏（它是 pipeline 契約），
   把衝突記進 run log。
4. **寧缺勿猜**：素材配對不到來源、金句查不到原始出處、KOL 片段定位不到 —
   一律降級（留 talking head 或換類型），不硬湊。

## 前置條件（開工檢查清單）

到 `data/script_video/<ep>/` 確認：

| 檔案 | 來源 | 缺了怎麼辦 |
|---|---|---|
| `script.md` | 修修的完整逐字稿 | 跟修修要，沒有就停 |
| `words.json` | `scripts/run_whisperx_words.py`（GPU，修修本機跑） | 請修修跑，GPU 工作不可自行啟動 |
| `transcript.srt` | `python -m agents.brook.script_video --episode <ep> cleanup`（有擊掌剪點時，含 ripple 重映射）或 `correct-srt`（乾淨錄影） | 自己跑上述命令 |
| `out/cleanup.fcpxml` | 同上 cleanup（有剪點時才有） | 沒剪點就沒有，正常 |

`transcript.srt` 是**唯一分鏡輸入**——文字 100% 對稿（PR #985），時間軸已對齊
剪完失誤後的 clean timeline。不要拿 script.md 直接分鏡（沒有時間資訊）。

## Step 0 — 分型與節奏預算

先讀完整份 transcript.srt，判斷這集是哪一型（頻道分析 2026-07-05，@shoshotw
觀看前 10；**heuristic 不是硬規則**，兩型混血就取中間）：

| | 書籍型（創業/致富書） | 健康型（切身健康議題） |
|---|---|---|
| 節奏 | ~2–2.5 cutaway/分（B-roll 為主體） | ~0.8–1.2 cutaway/分（B-roll 為證據點綴） |
| talking head 佔比 | ~37% | ~65% |
| 首個 B-roll | 開場 ~15s 內 | 可至 30s+ |
| 高頻視覺 | 書封卡、金句卡、章節卡 | 文獻來源、stock 實拍、大數字卡 |

硬上限永遠是 guardrails 的 4 cutaway/分；預算超過先砍最弱的 beat（anti-literal
名詞畫、資訊量最低者先死）。把分型判斷與預算寫進 run log 開頭。

## Step 1 — 初稿（可選）

`python -m agents.brook.script_video --episode <ep> plan` 可產 LLM 初稿
（planner 只認 Phase 1 詞彙，asset 類 beat 它不會排）。初稿只是 beat 切分的
起點；逐 beat 決策仍由你重新過一遍。跳過 plan 直接手排也合法。

## Step 2 — 逐 beat 決策

一個 beat = 一個 idea unit（可跨多句 SRT），錨定 start_quote / end_quote。
對每個 beat 依序問：

**1. 章節邊界？**「第一點」「第二個原則」等分點句式**必觸發**
`transition_title`（滿版章節卡，聲音不斷）；隱性語意章節（沒喊「第 N 點」但
話題硬切）**從嚴**——不確定就不切。時長 = `max(該句語音時長, 1.5s + 標題字數 × 0.12s)`。

**2. 觸發語意三條**（頻道既有節奏，heuristic）：
- 提到**研究/論文/數據來源** → 文獻類（v1 記來源進 run log，doc_highlight 排 v1.1；可用大數字卡代打數字結論）
- 提到**人名/書名/名言** → `quote_card` / `book_cover`（金句必查證原文與出處，查不到寧缺勿猜）
- 描述**具體場景、動作、感受的畫面感語句** → stock 實拍（`asset`/stock）

**3. none 的勇氣**：連接句（「然後」「不過」開頭）、無視覺載體的抽象概念
（身分認同、動機）→ `broll_decision: none` 留 talking head。連續 8 個 none
會觸發 guardrails 警告，屆時重看是不是真的太靜。

**4. 選類型**（詞彙表 = `agents/brook/script_video/CONTEXT.md` 的 B-roll 類型表；
合法值以 `guardrails.yaml` 為準）：

| 情境 | component / kind | 版面 |
|---|---|---|
| 大數字（>1000 或關鍵指標） | `bigstat`（params: label/value/unit） | 滿版 |
| 章節切換 | `transition_title`（params: kicker/title） | 滿版 |
| 書籍出場 | `book_cover`（params: cover_src/title_zh/title_en/author） | 滿版 |
| 名言金句 | `quote_card`（params: quote/attribution/source） | 滿版 |
| 概念動畫 | 菜單優先；即席寫新 composition **必存回 `video/compositions/` 成可重用資產＋過視覺審核**，用完即丟違規 | 滿版 |
| 實拍氛圍 | `asset` / `stock` | full_broll |
| KOL 引用 | `asset` / `kol` | full_broll（不縮放不加框） |
| 螢幕錄影 | `asset` / `screen_recording`（修修外供槽位） | full_broll |

版面初期**預設滿版**（D3）；透明疊加（keypoint_overlay）卡在 alpha 輸出的
DaVinci import 驗證，通過前不排。Hyperframes 類 params 的選填欄位**不給就留空
字串語意**（composition 端 default=""，空值隱藏）——別把 demo 值抄進 params。

**5. 節制檢查**：連續兩 beat 不同 component；anti-literal（「成長」≠上升箭頭）；
anti-hype 深入見 STYLE.md。

## Step 3 — 跨語言搜尋詞生成（stock / KOL 前置）

稿是中文，素材庫是英文。每個 asset 類 beat 先做查詢擴展：一個中文概念出
**3–5 組不同切面**的英文詞，寫進 run log 再開搜。

例：「複利效應」→
- 字面：`compound interest growth`
- 視覺隱喻：`snowball rolling growing`、`domino chain reaction`
- 場景：`time lapse plant growing`、`stacking coins timelapse`

例：「睡眠剝奪傷大腦」→
- 字面：`sleep deprivation brain`
- 場景：`tired person office night`、`tossing turning insomnia bed`
- 證據感：`brain scan neurology monitor`

一組詞搜不到好的就換切面，**不要**同義詞微調後重搜同一切面。

## Step 4 — 外部素材獲取

### 4a. Stock（Envato Elements）

1. 用 Envato MCP（`search_items`）搜——**只搜不下載**（Chrome extension 下載不了
   Envato 資產，下載走 Codex computer use，見 Step 5）。
2. 每個 beat 挑**首選＋兩備選**，預覽 URL 寫進 `broll.asset.candidates`
   （首選同時填 `source_url`），修修在 Bridge 圈選。
3. 同一集 stock 調性一致（都實拍或都動畫；偏暖、非 corporate 假笑）。
4. 授權假設 = **Elements 訂閱制**（吃到飽，多下載零邊際成本）；若帳號改單購
   模式，停下來找修修重審挑選流程（ADR-051 panel v2 §9）。

### 4b. KOL 片段（全自動，不走 Codex）

流程：YouTube 搜尋 → 字幕定位到目標句 → 抽 2–3 幀確認畫面內容（不是片頭/廣告）→
`yt-dlp` 下載**指定秒數區間**到 `<ep>/assets/kol/`。

護欄（D6，違反 = storyboard 不得送審）：
- 單一來源取用總長 **≤20s**（跨 beat 累計）
- `asset.source_url` + `source_span`（`HH:MM:SS-HH:MM:SS`）+ `attribution` 三者必填
- 一律靜音使用；`full_broll` 滿版不縮放不加框
- 收尾時彙整全部 attribution 成 description 出處清單，附進 run log

### 4c. 文獻（v1 = 只留痕，doc_highlight 排 v1.1）

稿尾來源清單（DOI/連結一行一篇）是輸入。v1 做到：把「稿中中文改述 ↔ 哪篇論文」
的配對寫進 run log；需要視覺時用 `bigstat`（數字結論）或 stock（證據感）代打。
**不要**排 `doc_highlight` beat——composition 未落地，且跨語言配對（中文改述 →
英文原句）的 Bridge 確認 UI 是 v1.1 範圍。PDF 三層 fallback（KB → open access
自抓 → paywall 修修供檔）屆時啟用。

### 4d. 螢幕錄影（外供槽位）

修修供檔（≥1080p）進 `<ep>/assets/screen/`；你只決定放哪個 beat 與時長，
`asset.kind = screen_recording`。缺檔時 beat 照排、`path` 留空，列入
asset_requests 的「外供待補」節提醒修修。

## Step 5 — 批次交接（asset_requests → asset_manifest）

Stock 下載一次性批次交接（D5 + panel v2 §2）。兩檔都放 `<ep>/`：

**`asset_requests.yaml`**（你寫，意圖）：

```yaml
episode: <ep>
requests:
  - id: req-001            # 對應 beat_id 與候選順位
    beat_id: 12
    kind: stock
    query: "snowball rolling growing"
    choice_url: "https://elements.envato.com/..."   # 首選
    target_path: assets/stock/beat12_snowball.mp4    # episode 相對路徑
    duration_hint_sec: 6
    mood: "溫暖、自然光、非 corporate"
    negative: "不要辦公室擺拍、不要文字疊圖"
supplied_pending:           # 外供待補（螢幕錄影等），非 Codex 範圍
  - beat_id: 30
    note: "Notion 模板操作錄影，≥1080p"
```

**Codex prompt 模板**（貼給修修轉交或直接 dispatch；英文）：

```
Read E:\nakama\data\script_video\<ep>\asset_requests.yaml.
For each entry under `requests`, use browser computer-use on
elements.envato.com (logged-in subscription) to download the item at
`choice_url` and save it to `target_path` (relative to the episode dir).
Then write asset_manifest.yaml next to it:

episode: <ep>
items:
  - id: req-001
    status: done | failed
    path: <actual saved path, episode-relative>
    source_url: <choice_url>
    license_note: "Envato Elements subscription, downloaded YYYY-MM-DD"
    fail_reason: <only when failed>

Do not re-encode or rename beyond target_path. Do not download items
not listed. If an item is unavailable, mark failed with reason and move on.
```

**驗收（你做，逐項，不可抽查）**——讀 `asset_manifest.yaml`：
1. 檔案存在於 `path`
2. 算 SHA-256 → 寫進 storyboard `broll.asset.sha256`（render/emit 前 dispatcher
   會重驗，防檔案被替換後沿用過期審核）
3. `ffprobe` 查幀率，非 30fps 用 ffmpeg conform 到 30fps 再覆蓋（混幀率進
   DaVinci 會 judder；conform 後**重算 sha256**）
4. `failed` 項目：換備選 URL 重發一輪 requests，或降級該 beat 為 none
5. 驗收結果（含 conform 紀錄）寫 run log

## Step 6 — storyboard.yaml 定稿

寫進 `<ep>/storyboard.yaml`。schema 重點（違反會被 pydantic 擋）：

- `render_target: asset` 的 beat **必帶** `broll.asset`；非 asset 類**不可帶**
- `asset.kind` ∈ stock / kol / screen_recording / supplied
- 每 beat `status` 初始：`text_approved: false`、`render_status: pending`、`visual_approved: false`

送審前自檢 guardrails（`validate-storyboard` CLI 落地〔PR-G〕前手動過一遍）：
cutaway ≤4/分、無連續同 component、KOL 單源 ≤20s、asset 類出處欄位齊全。

## Step 7 — Bridge 審核（HITL）

主通道：Bridge UI `/brook/video/<ep>`，兩層審核——text approved（分鏡文字層）→
render → visual approved（成品畫面層）。備援：對話審（單 beat 重排走
`brook-replan-beat` skill）。**兩層都過才算導演工作完成**；修修圈選備選候選
時回 Step 5 換檔重走驗收。

## Step 8 — render / emit

```bash
python -m agents.brook.script_video --episode <ep> render   # hyperframes 類才 render；asset 類驗檔案+sha256 即 done
python -m agents.brook.script_video --episode <ep> emit     # → out/broll.fcpxml（DaVinci import）
```

render 失敗 fail loud 不吞；emit 後提醒修修做 DaVinci import smoke。

## Resume 點（中斷接續）

各步驟落盤即 checkpoint，重啟時按檔案狀態判位：

| 現場 | 位置 | 接續動作 |
|---|---|---|
| 有 storyboard.yaml、無 asset_requests.yaml | Step 2–3 完 | 產 requests 交接 |
| 有 requests、無 manifest | 等 Codex | 催或改人工下載；**不重排分鏡** |
| 有 manifest、storyboard 的 sha256 空 | Step 5 驗收中 | 逐項驗收續跑 |
| text_approved 全 true、render pending | Step 8 | render |
| render done、visual 未過 | 等修修 | 提醒審 visual |

判位依據 + 接續點寫 run log（一行即可）。

## Run log 格式（`<ep>/run_log.md`）

```markdown
# Run log — <ep>
- skill: brook-director v1.0
- 分型: 書籍型（依據：…）；預算 N cutaway/分
## 素材決策
- beat 12 stock：搜 "snowball rolling growing"（切面：視覺隱喻）；
  候選 A/B/C（URL）；選 A 因光線暖；否決 B 因 corporate 擺拍
## 否決 / 降級
- beat 18 金句查無原始出處 → 降級 none（寧缺勿猜）
## Remaining
- schema 缺 X 欄位（開 PR 議題，不即席加）
```

## 每集教訓寫回手冊

E2E 每跑完一集（visual approved + DaVinci import smoke 過），把可固化的教訓
**append 進本節並 bump 版本號**（經 PR）；連續 ~10 集穩定的規則再考慮結晶回
`plan` prompt（ADR-051 roadmap，非 skill 自行決定）。

### 教訓紀錄

（v1.0 尚無——第一集 E2E 後開始累積。）
