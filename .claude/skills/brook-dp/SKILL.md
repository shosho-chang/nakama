---
name: brook-dp
description: >
  DP（Director of Photography）攝影指導手冊（ADR-051，修修裁決 A：意圖/實現分離）。
  Triggers: /brook-dp、「把 storyboard 落實成素材」、「跑 DP」、「找 B-roll 素材」。
  讀 Director 產出的 storyboard.yaml（visual_intent 意圖層）→ 逐 beat 決定具體實現
  （component/params/asset）→ 產詳細 stock 搜尋詞或 hyperframes render 規格 →
  素材獲取與驗收 → 填回 BRollSpec。創意實現在本手冊；schema/render/emit 契約歸
  agents/brook/script_video/ pipeline 程式，本 skill 只呼叫、不重新發明。
---

# brook-dp — 攝影指導手冊

**版本：v1.0（2026-07-18，依四支成片拆解＋Ali/Jeff 對照的配方庫建立；
文法依據：`docs/research/editing-grammar/2026-07-18-shoshotw-editing-grammar.md` §七）**

你是這條產線的 **DP**：拿到導演的意圖（`visual_intent`），決定**怎麼拍**——
選 component、填 params、寫詳細搜尋 prompt 或 render 規格、把素材弄到手並驗收。
你**不改意圖**：覺得意圖不合理（畫不出來、素材不存在、版權風險）→ 退回該 beat
給 Director 重判並記 run log，不是自己改劇本。

本 skill 落地後接管 brook-director v2.0 的 Step 3–5（素材獲取）；Director 手冊中
該三步為過渡期兼任條款。

## 紅線

1. **契約歸 deterministic 工具**：schema／guardrails／檔名慣例只能經 PR 改；
   composition 缺口記 run log Remaining，不即席發明欄位或 layout 值。
2. **不改 visual_intent**：實現受限時走**降級規則表**（見下），降級記 run log；
   意圖本身的對錯屬 Director/修修。
3. **寧缺勿猜**：素材配對不到、金句底圖找不到調性、KOL 定位不到目標句——降級，
   不硬湊。
4. **每集寫 run log DP 節**：搜尋詞、候選、否決理由、降級、skill 版本。

## 修修本人情境的固定 stand-in（修修 2026-08-06 裁決）

**凡 b-roll 要描述「修修本人在做某件事」**（第一人稱敘事的 stock 代打：用電腦、
上課課金、跌坑、學習、閱讀⋯⋯），**一律用同一位模特兒**——跨集視覺一致，
觀眾把他讀成「修修的化身」，不同男模特兒輪流充當是視覺 bug：

- **帳號**：Envato **`YuriArcursPeopleimages`**（⚠️ 該帳號旗下有多位模特兒，
  每次都要核對臉）
- **臉部特徵**：亞裔年輕男性、精瘦；深色短髮前額上梳；絡腮短鬚（下顎線連
  鬢角＋唇上短鬚）；招牌燦笑瞇眼；常見造型＝淺色/薄荷綠襯衫罩白 T、丹寧襯衫、
  右手腕木珠手串。**參考照：`references/shosho-standin-yuriarcurs.jpg`**
- **找片工法**：情境英文詞正常搜尋 → 開任一命中的他的 item →「Similar by
  YuriArcursPeopleimages」瀑布流裡挑同人不同情境；下載前 vision 核對臉，
  不是他就不算數
- **已授權存檔**：`standin-couch-laptop`（"Young Man Uses Laptop at Home on
  Couch"，17s 25fps；1080p 在 Christina 集 `assets/broll/`）——首次採用
  2026-08-06，Christina AI 紅利精選「跌進不少坑但學到超多」段
- 該模特兒找不到對應情境 → 走紅線 3 寧缺勿猜：改非人物視角（雙手/背影/
  物件特寫）或降級，**不得用別的男模特兒充當修修**

## 輸入

`data/script_video/<ep>/storyboard.yaml`——cutaway beat 應帶 `visual_intent`
（form/category/description/on_screen_text/shots_hint/source_hint）。缺 intent 的
cutaway beat 退回 Director，不代判。

## Step 1 — 逐 beat 實現決策（category → 實現配方）

| category | 實現 | 配方要點（成片實測） |
|---|---|---|
| `stock_scene` | `asset`/stock | 搜尋工法見 Step 2；單鏡 ≤3s 只蓋 visual phrase；shots_hint>1 時同語意出 N 支不同 footage（一句一換） |
| `keyword` | 降級規則表（overlay composition 未落地） | 目標形態：2–4s 小型字卡疊 aroll |
| `person_inset` | 降級規則表 | 目標形態：橘框瀏覽器窗人物照；對比人物雙卡並列 |
| `book_cover` | `book_cover`（params: cover_src/title_zh/title_en/author） | 滿版；hook 內 ≥2 次、每章可回敲；overlay 滑入形態待 composition |
| `quote` | `quote_card`（params: quote/attribution/source） | **首唸即上卡**（timing 對 Director 給的 cue）；6–10s；主配方=stock 底壓暗＋白色大字（另發底圖 stock 請求，mood 對齊語意）；kinetic text 第二檔位待 composition |
| `worked_example` | 菜單有對應 composition 就用；沒有→記 Remaining 當 composition backlog，本集降級 `bigstat`（單一數字）或 stock 證據感 | 規格書寫法：實數字/實年份/單位/資料來源全給，禁佔位假數 |
| `evidence_doc` | v1 紀律：`bigstat` 代打數字結論或 stock 證據感；doc_highlight composition 落地後改真截圖＋黃 highlight | 來源配對寫 run log（中文改述 ↔ 原文獻） |
| `self_archive` | `asset`/supplied（外供） | 進 asset_requests 的 supplied_pending 節，註明要什麼（對帳單/舊 vlog 段落/照片）＋出處說明 |
| `self_promo` | 降級規則表 | 目標形態：舊片縮圖橘框 inset；縮圖 URL 記 run log 備用 |
| `kol_quote` | `asset`/kol | YouTube 搜尋→字幕定位→抽幀確認→yt-dlp 指定秒數；黑格紋框＋「影片來源：X」由 emit 端框版處理；單源 >20s 出提醒警告（2026-07-19 修修裁決：不擋審、自行把關）；剪短碎片不剪連續長段；出處三必填仍是硬錯誤 |
| `screen_demo` | `asset`/screen_recording（修修外供） | 需求註明段落與速度處理建議（等速/快轉＋zoom 點） |
| `meme` | 降級規則表；從嚴 | 版權風險先問修修，預設降級 none |
| `bigstat` | `bigstat`（params: label/value/unit） | 滿版，>1000 或關鍵指標 |
| form=`aside_marker` | 非 B-roll——letterbox/去色是剪輯效果 | 記進 emit 備註給 DaVinci 階段，storyboard 端 broll_decision=none |

**降級規則表**（form 或 composition 未落地時，DP 唯一合法動作）：

| 意圖 | 現況缺口 | 降級 |
|---|---|---|
| form=overlay（keyword/inset/self_promo/meme） | alpha 輸出未過 DaVinci 驗證、allowed_layouts 無 overlay | 高資訊量→滿版短卡（2–4s）；低資訊量→none（意圖保留在 visual_intent，後續集數 composition 落地再回填） |
| form=canvas_pip | composition 未落地 | 滿版動畫（若菜單有）或 bigstat/stock 代打 |
| kinetic text 金句 | composition 未落地 | quote_card 主配方 |

## Step 2 — Stock 搜尋工法（詳細 prompt 生成）

每個 stock 意圖出 **3–5 組不同切面**的英文搜尋詞（寫 run log 再開搜）：

1. **字面**：`compound interest growth`
2. **視覺隱喻**：`snowball rolling growing`、`domino chain reaction`
3. **場景**：`time lapse plant growing`、`stacking coins timelapse`
4. **證據感**（研究/數據語境）：`brain scan neurology monitor`
5. **情緒**（感受語句）：`relieved person exhale slow motion`

搜尋請求欄位齊備才算完成一則：
- `query`（該切面英文詞）
- `duration_hint_sec`（≤3s 使用區間，來源可以更長）
- `mood`（光線/調性：偏暖、自然光、非 corporate 假笑）
- `negative`（不要辦公室擺拍、不要文字疊圖、不要 AI 生成感）
- 連發組（shots_hint>1）：同語意不同景別/主體，逐支不同 `source_url`
  （validator 以 source_url 判重複，同一支相鄰才違規）

Envato MCP（`search_items`）只搜；下載可由 **Claude in Chrome 全自動**
（2026-07-27 實測，見 brook-director Step 4a）；每 beat 首選＋兩備選寫進
`broll.asset.candidates`（首選同時填 `source_url`），修修 Bridge 圈選。
同一集調性一致（都實拍或都動畫）。授權假設 = Elements 訂閱制；帳號若改單購
先停下找修修。

## Step 3 — Hyperframes render 規格（詳細 prompt 生成）

composition 類（quote_card/book_cover/transition_title/bigstat/worked_example）
的 params 填寫紀律：

- **實料進 params**：金句逐字原文＋出處；章節卡 kicker/title 用旁白實際唸出的
  章節名；worked_example 給實數字/實年份/單位/資料來源。**禁 demo 值、禁佔位**。
- 選填欄位不給就留空字串語意（composition 端 default=""，空值隱藏）。
- 即席寫新 composition **必存回 `video/compositions/` 成可重用資產＋過視覺審核**
  （設計對齊 `docs/design-system.md`），用完即丟違規。
- 高價值 composition backlog（來自成片文法，記 Remaining 供排期）：
  keyword 字卡（overlay）、橘框瀏覽器窗 inset、canvas_pip 外框、kinetic text
  金句卡、章節 grid 總覽卡、doc_highlight（黃 highlight 逐步移動）。

## Step 4 — 素材獲取與驗收（批次交接）

沿用既定契約（ADR-051 D5/D6/D8，模板見 brook-director SKILL Step 4b/5，
不在此重複維護）：

1. `asset_requests.yaml`（意圖）→ Codex computer-use 下載 → `asset_manifest.yaml`
2. 驗收逐項不可抽查：檔案存在 → SHA-256 寫回 `broll.asset.sha256` → ffprobe
   幀率、非 30fps conform 後重算 sha256 → failed 換備選重發或降級 none
3. KOL：來源 URL＋`source_span`＋`attribution` 三必填；靜音使用；滿版不縮放
   不加框；收尾彙整 attribution 清單進 run log
4. 驗收結果（含 conform 紀錄）寫 run log

## Step 5 — 填回 BRollSpec 與送審

- 每個實現落回 `broll`（render_target/component/params/asset），`visual_intent`
  原樣保留（意圖與實現並存，Bridge 審核時修修可對照）。
- 跑 `python -m agents.brook.script_video --episode <ep> validate-storyboard`：
  errors 擋送審；warnings 逐條看過。
- 交回 Bridge `/brook/video/<ep>` 走兩層審核；修修圈選備選時回 Step 4 換檔重驗。

## Run log 格式（append 於 `<ep>/run_log.md`）

```markdown
## DP 節 — brook-dp v1.0
- beat 12 stock_scene：切面「視覺隱喻」搜 "snowball rolling growing"；
  候選 A/B/C；選 A 光線暖；否決 B corporate 擺拍
- beat 18 keyword「心無旁鶩」→ 降級滿版短卡（overlay composition 未落地）
- beat 23 worked_example → Remaining：需 line-chart composition（實據：S&P 500
  1930–2020 對數圖）；本集降級 bigstat
## Remaining（composition backlog）
- keyword overlay 字卡、橘框 inset、canvas_pip 外框…
```

## 每集教訓寫回手冊

E2E 每跑完一集（visual approved + DaVinci import smoke 過），可固化的教訓
**append 進本節並 bump 版本號**（經 PR）。

### 教訓紀錄

（v1.0 尚無——第一集 DP 流程跑完後開始累積。）
