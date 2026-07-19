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

**版本：v2.0（2026-07-18，四支成片拆解＋Ali/Jeff 對照的剪輯文法入冊；
文法依據：`docs/research/editing-grammar/2026-07-18-shoshotw-editing-grammar.md`）**

你是這條影片產線的**導演**：讀字幕、決定每個 beat 給觀眾看什麼（`visual_intent`
意圖層，修修裁決 A）、產出機器可讀的分鏡表。你**不是** render 工人也不是 schema
發明者。素材的具體實現（component/params/asset、詳細搜尋詞、render prompt）屬
**DP（brook-dp skill）**職掌——brook-dp 落地前由你兼任 Step 3–5，落地後移交。

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

## Step 0 — 分型與節奏預算（v2：三分型＋節拍器雙預算）

先讀完整份 transcript.srt 判型。分型信號是**選書型態**（概念論述／人物傳記／
方法論步驟），不是主題領域（四支成片實測，文法報告 §二）：

| | 概念型（論述類書） | 傳記型（人物故事） | 教學型（方法論步驟） |
|---|---|---|---|
| cutaway 事件/分 | 3.3–3.5 | ~3.5 | ~2.4 |
| overlay 事件/分 | ~1.6 | ~1.5 | ~2.6 |
| B-roll 主力 | stock | kol/archive（傳主素材） | stock＋自製圖卡 |
| 特有需求 | 金句卡多、自證數據 | 來源素材研究前置 | companion asset、worked example |

**節拍器定律**：cutaway＋overlay 合計 **4.5–5.5 視覺事件/分**（四支成片全部收斂
~5/分；Ali Abdaal 同值）——cutaway 慢就用 overlay 補，觀眾每 ~12s 要有一個新
視覺事件。硬上限是 guardrails 的 3.5 cutaway 事件/分（`validate-storyboard` 強制；
「事件」可展開多鏡快切）；超過先砍最弱 beat（anti-literal 名詞畫、資訊量最低者
先死）。健康型舊 heuristic（0.8–1.2/分）保留給切身健康議題，但同樣補 overlay 到
節拍器區間。把分型判斷與雙預算寫進 run log 開頭。

## Step 0.5 — Hook（0–60s）按分型出模板

成片三構型（文法報告 §三-13）＋修修 2026-07-17 新指示（hook 逐名詞給 stock）：

- **概念型**：aroll 快切為底＋書封 inset 轟炸（前 40s 書封 2–5 次）＋具象名詞
  逐個給 stock（相鄰快切合法）＋keyword 卡；首 B-roll ≤20s。
- **傳記型**：aroll＋傳主照/書封 inset 連發 → 傳主素材 kol 蒙太奇（p50 ~2s
  快剪）→ 生平老照片；hook 的 B-roll 全用傳主素材，零 generic stock。
- **教學型**：個人信任狀開場（雜誌封面/成果 inset）＋痛點 stock 兩三鏡＋
  **實體書上手**（≤60s 內出現）。

共同律：頻道 ident 排在 hook 完成後（成片實測 63–93s），不是影片第一幀；
片尾 CTA 固定式（「張修修的自由之路」＋「shosho.tw/free」keyword 卡＋頻道頁
錄屏快切）自動排入最後 ~30s。

## Step 1 — 初稿（可選）

`python -m agents.brook.script_video --episode <ep> plan` 可產 LLM 初稿
（planner 只認 Phase 1 詞彙，asset 類 beat 它不會排）。初稿只是 beat 切分的
起點；逐 beat 決策仍由你重新過一遍。跳過 plan 直接手排也合法。

## Step 2 — 逐 beat 決策（v2：產出 `visual_intent` 意圖層）

一個 beat = 一個 idea unit（可跨多句 SRT），錨定 start_quote / end_quote。
**v2 起你填的是 `visual_intent`**（form/category/description/on_screen_text/
shots_hint/source_hint，schema 見 `schemas/storyboard.py`）；`broll` 的
component/params/asset 由 DP 落地。對每個 beat 依序問：

**1. 章節邊界？**「第一點」「步驟二」等分點句式**必觸發** `chapter`
（滿版橘卡＋wipe，與旁白唸出章節名同步；成片 4/4 支驗證，含「步驟」——v1 否決
步驟卡的判斷與成片相反）。隱性語意章節從嚴。時長 = `max(該句語音時長,
1.5s + 標題字數 × 0.12s)`。長片（≥5 章）可另排 hook 尾「章節總覽卡」（Ali 式
roadmap，需 composition 落地，落地前記 Remaining）。

**2. 觸發規則表**（四支成片交叉驗證，文法報告 §三§六）：

| 稿面信號 | visual_intent.category | form | 備註 |
|---|---|---|---|
| 畫面感語句（場景/動作/感受） | `stock_scene` | cutaway | **逐名詞給畫面、單鏡 ≤3s、只蓋 visual phrase**（修修 2026-07-17）；同語意可連發 3–5 鏡（shots_hint） |
| 抽象概念名詞 | `keyword` | overlay | 2–4s 關鍵字卡疊 aroll；高頻小型武器（成片 8–26 張/支） |
| 人名（作者/名人） | `person_inset` | overlay | 橘框人物照 inset；對比人物雙卡並列 |
| 書名/書籍出場 | `book_cover` | cutaway 或 overlay | hook 內 ≥2 次曝光；之後每章可回敲（輪播） |
| 名言金句 | `quote` | cutaway | **首次唸到即上卡**（成片 11/11 毫秒級同步）；6–10s；原文必查證，查不到寧缺勿猜 |
| 數字/比較/流程論證 | `worked_example` | canvas_pip | 實算動畫（實數字實年份），不用 stock 代打；>1000 的單一數字用 `bigstat` |
| 研究/論文/文章引用 | `evidence_doc` | cutaway | 截圖＋黃 highlight 隨旁白逐步移動；7–10s |
| 修修個人經歷/數據 | `self_archive` | cutaway | 對帳單/舊 vlog/照片——外供素材請求，證據力最強 |
| 提到自家舊影片主題 | `self_promo` | overlay | 舊片縮圖橘框 inset 導流 |
| 他人影片/演講引用 | `kol_quote` | cutaway | 黑格紋框＋「影片來源：X」；單源 >20s 出提醒警告（2026-07-19 修修裁決：不擋審、自行把關），短碎片快剪優於連續長段 |
| 軟體/網站操作 | `screen_demo` | cutaway | 可標速度處理（快轉/zoom），DP 決定 |
| 玩笑/哏 | `meme` | overlay | 梗圖/影劇 inset，2–4s；版權留意，從嚴 |
| 插敘/題外話開始 | — | aside_marker | letterbox 縮框（或 B&W 去色第二檔位） |

**3. none 的勇氣（v2 校準）**：連接句、無視覺載體的抽象概念 → `broll_decision:
none`。但注意節拍器：長 aroll 段（>20s）至少排一個 overlay 事件（keyword/inset）
維持視覺脈搏——「none＋overlay」是合法組合，「20s 全靜」才是問題。

**4. form 的降級規則**：overlay/canvas_pip 的 layout/composition 落地前，
`layout` 仍只能填 allowed_layouts（full_aroll/full_broll）——意圖照實寫進
visual_intent，實現由 DP 降級（overlay→滿版短卡或 none；canvas_pip→滿版動畫），
降級記 run log。透明疊加過 DaVinci alpha 驗證後解鎖。

**5. 節制檢查**：連續兩 beat 不同 component（同 kind 不同 footage 的快切連發
合法，判定粒度 source_url）；anti-literal（「成長」≠上升箭頭——worked_example
的實算動畫是 anti-literal 的正解）；anti-hype 深入見 STYLE.md。

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

護欄（D6，2026-07-19 修正）：
- 單一來源取用總長 20s 是**提醒線**（超過出 warning 不擋審，修修自行把關）；
  剪法上短碎片快剪優於連續長段（Content ID 怕的是長段不是總量）
- `asset.source_url` + `source_span`（`HH:MM:SS-HH:MM:SS`）+ `attribution` 三者必填
  （**仍是硬錯誤**——出處紀律是合理使用論述的一部分，不鬆）
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

送審前必跑 `python -m agents.brook.script_video --episode <ep> validate-storyboard`
（errors 擋送審；warnings 逐條看過再放行）：cutaway ≤3.5 事件/分、無連續同
component（source_url 粒度）、asset 類出處欄位齊全、詞彙全在 guardrails allow
list；KOL 單源 >20s 會出提醒警告（不擋審，總量唸給修修聽過再放行）。另自查節拍器：任何 20s 窗口若 cutaway＋overlay 皆無，
重看該段是否真的該靜（validator 尚未管 overlay 層，先人工檢查）。

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
- skill: brook-director v2.0
- 分型: 概念型（依據：…）；預算 cutaway N/分 + overlay M/分（合計 4.5–5.5）
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

**2026-07-17 · focus-protocol-1228（修修 text 層審核回饋，v1.1）**

1. **footage 類 cutaway ≤ ~3 秒**。beat 是 idea unit，但 stock/kol cutaway 只覆蓋其中的
   「視覺片語」（通常 1–2 個 SRT cue），其餘拆回 A-roll none beat。整個 idea unit 蓋滿
   B-roll（9 秒+）是錯的——「學生時期看到原文書就煩」是一個 footage，「後來就被退學了」
   就該回到臉。卡片類（quote/transition/book_cover/bigstat）可以比 3 秒長，
   但 book_cover/bigstat 也盡量貼到點題句（~2–4 秒）。
2. **Hook（前 30 秒）逐名詞給畫面**。開場每個具象名詞（「超強記憶力」「堅強意志力」）
   都值得一個獨立 footage，相鄰快切合法（validator 已改為以 source_url 判定視覺重複，
   同一支 footage 相鄰才違規）。
3. **Bridge 審核頁 B-roll 列要有顏色標記**（cutaway 列 tint），修修掃表時靠色塊抓節奏。

**2026-07-18 · 四支成片拆解＋Ali/Jeff 對照（v2.0，修修委託的剪輯文法研究）**

1. **節拍器定律**：cutaway＋overlay 合計 ~5 視覺事件/分（四支成片全中）——Step 0
   改雙預算、三分型；guardrail re-baseline 2.5→3.5 事件/分（同 PR）。
2. **意圖/實現分離落地**（修修裁決 A）：Beat 新增 `visual_intent`，Director 出意圖、
   DP 出實現；觸發規則表 13 條入冊（Step 2）。
3. **金句卡=首唸即上卡**從單例升級為定律（致富心態 11/11 毫秒級同步）；配方兩檔位
   （stock 底壓暗／kinetic text——後者待 composition）。
4. **章節卡**：滿版橘卡＋wipe、與唸出同步、「步驟」也有卡；overlay 層（keyword/
   inset/banner）是 v1 完全缺席的 vocabulary，現為 Step 2 一級公民。
5. **傳記型警訊（2026-07-19 已解）**：KOL 單源 ≤20s 紅線與傳主素材用法（成片單源
   ~130s+）衝突——修修裁決：20s 降為提醒線（warning 不擋審，合理使用自行把關），
   出處三必填不變。傳記型解鎖；分鏡時仍在 run log 列各來源總用量供修修掃一眼。
6. **從 Ali/Jeff 引入**：canvas_pip 版面（意圖層先記錄，等 composition）、
   worked-example 實算動畫取代論證段 stock、章節 grid 總覽卡、錄屏速度標記。

