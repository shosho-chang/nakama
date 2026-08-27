---
name: brook-dp
description: >
  DP（Director of Photography）攝影指導手冊（ADR-051，修修裁決 A：意圖/實現分離）。
  Triggers: /brook-dp、「把 storyboard 落實成素材」、「跑 DP」、「找 B-roll 素材」。
  讀 Director 產出的 visual_intent（standalone storyboard.yaml；Podcast Highlight
  DIRECTOR-PLAN.json）→ 逐 beat 決定具體實現
  （component/params/asset）→ 產詳細 stock 搜尋詞或 hyperframes render 規格 →
  素材獲取與驗收 → 填回 BRollSpec。創意實現在本手冊；schema/render/emit 契約歸
  agents/brook/script_video/ pipeline 程式，本 skill 只呼叫、不重新發明。
---

# brook-dp — 攝影指導手冊

**版本：v1.1（2026-08-06，Christina 集 44 位 stock 整版打槍後固化〈選片鐵則〉；
v1.0 2026-07-18 依四支成片拆解＋Ali/Jeff 對照的配方庫建立；
文法依據：`docs/research/editing-grammar/2026-07-18-shoshotw-editing-grammar.md` §七）**

你是這條產線的 **DP**：拿到導演的意圖（`visual_intent`），決定**怎麼拍**——
選 component、填 params、寫詳細搜尋 prompt 或 render 規格、把素材弄到手並驗收。
你**不改意圖**：覺得意圖不合理（畫不出來、素材不存在、版權風險）→ 退回該 beat
給 Director 重判並記 run log，不是自己改劇本。

本 skill 落地後接管 brook-director v2.0 的 Step 3–5（素材獲取）；Director 手冊中
該三步為過渡期兼任條款。

## Model routing

DP 不負責重新理解整支影片的論述結構；它只在 Director 已定義的 exact event 內做素材搜尋、選片與
render 規格，因此使用平衡型 model：

- Codex：`gpt-5.6-terra`，reasoning `medium`。
- Claude Code：最新 Opus；runtime 有 `claude-opus-5` 時優先使用。
- 真正的下載、ffprobe、Hyperframes／Resolve render 與 materialization 全走 deterministic 工具，不用 LLM。

若 Director intent 本身含糊，DP 不升級 model 代猜；退回 Director。只有搜尋結果在兩個語意切面間難以
判斷時，才把該 event 的 reasoning 提高一級，不把整輪 DP 升到 frontier model。

## Podcast Highlight production adapter（ADR-065；優先於下方 standalone 步驟）

Podcast episode + cut ID 不讀 standalone `data/script_video/<ep>/storyboard.yaml`。唯一 truth是
revision-aware DAG：

```text
<episode>/highlights/visual-pipeline/<cut-id>/
  PENDING.json
  CURRENT.json
  revisions/<revision-id>/
    DIRECTOR-WORK.json       podcast-highlight-visual-work-packet-v1
    DIRECTOR-PLAN.json       podcast-highlight-director-plan-v1
    DP-FULFILLMENT.json      podcast-highlight-dp-fulfillment-v1
    SEMANTIC-AUDIT.json      podcast-highlight-visual-semantic-audit-v1
```

Production預設呼叫一次 trusted orchestrator；finished review revision必須傳 exact immutable request snapshot：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_orchestrator.py "<episode>" `
  --cut-id <cut-id> [--revision-request "<episode-local immutable request.json>"]
```

Claude Code手動/subagent route不得省略 accept順序。DP proposal只能寫 phase-local output；trusted host提供實際
DP execution/session identity，不能從 proposal抄 worker欄位：

```powershell
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py accept-dp "<episode>" --cut-id <cut-id> --revision-id <revision-id> --proposal "<dp-proposal.json>" --worker-id <trusted-dp-worker-id> --execution-id <trusted-dp-execution-id> --session-id <trusted-dp-session-id>
# Resume original Director worker/session, never DP, for the semantic-audit proposal:
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py accept-audit "<episode>" --cut-id <cut-id> --revision-id <revision-id> --proposal "<semantic-audit-proposal.json>" --worker-id <same-trusted-director-worker-id> --execution-id <new-trusted-director-audit-execution-id> --session-id <same-trusted-director-session-id>
E:\nakama\.venv-v2\Scripts\python.exe scripts\podcast_highlight_visual_pipeline.py verify "<episode>" --cut-id <cut-id> --revision-id <revision-id>
```

1. Deterministic status/validator必須 fresh驗出 `awaiting_dp`；work packet、Director plan、Editorial
   Master或 work packet綁定的 exact tight SRT任一 missing/stale/invalid就停，不得自行找 latest或修 receipt。
2. Exact覆蓋 `DIRECTOR-PLAN.json` 的所有 events。每個 fulfillment保存 mode、`target_lane`、2–5 個不同
   語意切面的 search queries、候選、selected candidate、選擇理由、negative checks及 source/license/hash；找不到就
   明示 none/合法降級，不拿同主題 generic footage湊數。
3. 畫面必須對應 event的 exact transcript quote，不只對應廣泛主題。來源可信、ffprobe/hash通過與至少
   三支 Stock Video都是機械條件，不能替代語意證明。
4. 產出 phase-local `podcast-highlight-dp-fulfillment-v1` proposal，只有 trusted `accept-dp`可寫
   `revisions/<revision-id>/DP-FULFILLMENT.json`；不可直接手寫 canonical receipt或 production `_broll.json`。
5. Fresh status必須前進到 `awaiting_semantic_audit`。交回 orchestrator，讓**原本同一個 Director
   worker identity**做 semantic audit；該 identity必須不同於本 DP。DP不得自審或寫
   `SEMANTIC-AUDIT.json`。

Auditor exact覆蓋每個 selected materialization且全數 semantic match後，`accept-audit`才 pointer-last更新
`CURRENT.json`，status成 `ready_to_materialize`。失敗的 PENDING不得破壞前一個 CURRENT。
`scripts/run_short_broll.py`只是一個 **materializer**；它的成功不代表本 skill執行過，也不授權 DP
在 renderer內臨時選片。普通 pending是 agent-owned next work，只有 authority、license或主觀語意真的
ambiguity才是 HITL。Bridge只 read-only展示 fresh receipt與 audit，不新增正常人類 gate。

DP履約涵蓋所有 content visuals：Stock／Hero／keyword／quote／chapter／card，並同時產出 **B-roll 與 title implementations**。
結構性 badge／camera correction／guest namecard維持各自 deterministic contract。
`scripts/run_short_titles.py`也只是 materializer；不得在 receipt chain外另選 Hero text或落點。

下方 storyboard、asset_requests/manifest與 `/brook/video/<ep>` 路徑仍服務 ADR-051 standalone route；
Podcast Highlight不得 silent fallback。

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
- **進階（2026-08-06 實測）**：可直接在他的作品集內做關鍵詞搜尋——
  `app.envato.com/search?itemType=stock-video&itemReference=f3b54f2f-3eec-4558-8106-aeaf39a72109&filter.portfolio=YuriArcursPeopleimages&term=<情境詞>&filter.orientation=Horizontal`
  （itemReference 錨定結果排序，換 term 換情境）；帳號模特兒眾多，
  縮圖階段就要 zoom 核臉再點進去
- **已授權存檔**：`standin-couch-laptop`（"Young Man Uses Laptop at Home on
  Couch"，17s 25fps；1080p 在 Christina 集 `assets/broll/`）——首次採用
  2026-08-06，Christina AI 紅利精選「跌進不少坑但學到超多」段
- 該模特兒找不到對應情境 → 走紅線 3 寧缺勿猜：改非人物視角（雙手/背影/
  物件特寫）或降級，**不得用別的男模特兒充當修修**

## 選片鐵則（修修 2026-08-06 Christina 集 review 裁決）

適用於**一切 b-roll 選片**——包括不走 storyboard pipeline、直接在 Resolve
timeline 放 stock 的手剪情境。Christina 集 44 個位就是跳過本手冊 ad hoc
掃字幕配對，被整版打槍（見教訓紀錄）。

1. **全片一片一用**：每支 footage 整部影片**只准出現一次**，重複使用是大忌。
   素材不夠＝回 Envato 再抓或留白，不是複用庫存。（2026-07-17「同一支相鄰
   才違規」的判定就此升級為**全片唯一**；storyboard validator 目前僅查相鄰
   ——升級記 Remaining。）
2. **進出點切齊被強調的那句話**：B-roll 的功能是強調「正在陳述的那句話」，
   in/out 對齊該句 cue 起訖（同一語意可跨多 cue），不是固定秒數、不是落在
   鄰句上。實測打槍：跑步鏡頭落在「判斷力不是原地踏步」句上，要強調的
   其實是下一句「判斷力是做出來的」——放錯句＝邏輯斷裂。
3. **畫面＝語意，不是主題相近**：驗收標準＝「不看字幕也能從畫面讀出這句
   話的意思」。實測打槍案例（同一輪 review）：
   - 「從失敗中學習」放小孩拿放大鏡（那是好奇心）→ 應為跌倒後爬起來
   - 「那些你永遠不會採用的瘋狂點子」放男人望窗外（讀不出）→ 應為
     靈光乍現、忽然想到什麼的樣子
   - 「新創老闆進步快」放霓虹跑車（無關）→ 應為創業者很有自信的樣子
   - 「判斷力是做出來的」放跑步 → 應為認真努力工作的樣子
   - 「駕馭 AI 浪潮勝出」放晨跑（讀不出意思）
4. **抽象專有名詞沒有對應畫面 → 字卡＋音效**：「半人馬工作流」放騎馬＝
   兩回事，硬湊隱喻比留白更糟。課程名/概念名這類重點直接上 keyword 字卡
   加音效（Resolve 手剪情境用 Text+；pipeline 情境走降級規則表）。
5. **負面意象禁用清單**（隨集數累積）：
   - 小孩用平板/3C——修修視為相當負面的現象，禁止當「工具隨手可得」類
     正面例證
6. **書封/靜態圖必須去背＋動畫＋位置設計**：不透明原圖整張放上去、還壓在
   說話者臉上＝打槍。正確：去背成 alpha PNG → 進場動畫（滑入/彈出）→
   擺在不遮臉的負空間。

## 輸入（Standalone route）

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
  （validator 以 source_url 判重複；2026-08-06 起同支 footage **全片唯一**
  ——見〈選片鐵則〉1，validator 僅查相鄰為待升級 gap）

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

## Step 4 — 素材獲取與驗收（Standalone批次交接）

沿用既定契約（ADR-051 D5/D6/D8，模板見 brook-director SKILL Step 4b/5，
不在此重複維護）：

1. `asset_requests.yaml`（意圖）→ Codex computer-use 下載 → `asset_manifest.yaml`
2. 驗收逐項不可抽查：檔案存在 → SHA-256 寫回 `broll.asset.sha256` → ffprobe
   幀率、非 30fps conform 後重算 sha256 → failed 換備選重發或降級 none
3. KOL：來源 URL＋`source_span`＋`attribution` 三必填；靜音使用；滿版不縮放
   不加框；收尾彙整 attribution 清單進 run log
4. 驗收結果（含 conform 紀錄）寫 run log

## Step 5 — 填回 BRollSpec 與送審（Standalone only）

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

- **2026-08-06 Christina「AI 紅利精選（緊）」**：跳過本手冊，ad hoc 掃字幕
  在 28 支庫存裡填 44 個位、一律固定 4.0s——修修 review 全面打槍：12 支
  素材被重複使用佔 16 個位（大忌）、多處放錯句/畫面讀不出語意、kid-tablet
  負面意象、書封不透明 jpg 直接壓臉；重複的他自己動手刪。根因＝沒走逐
  beat「意圖→實現」流程、沒有一片一用檢查、沒做畫面＝語意驗收。固化為
  〈選片鐵則〉節（v1.1）。Remaining：storyboard validator 重複判定從
  「相鄰」升級「全片唯一」。
