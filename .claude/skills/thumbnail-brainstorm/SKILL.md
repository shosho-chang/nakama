---
name: thumbnail-brainstorm
description: >
  封面 brainstorm 手冊（ADR-054 D8/D9）。packaging 的 sequential 第二棒：吃
  title-brainstorm 寫進 packages.json 的 Top 標題（各帶 archetype_id）→ playbook
  joint pairing 為前 3 條各配一個封面 idea → 視覺配方 routing（podcast／
  youtube_host／youtube_book）→ 來賓 cutout 走窗口化 funnel + vision 挑表情 →
  render 3 張 16:9 PNG → 回填 packages.json 成 3 個 package。Triggers:
  /thumbnail-brainstorm、「配封面」、「出 package」。創意判斷（配對、表情、
  大字）在本手冊；schema／render／去背／檔名慣例歸 shared/ 與 scripts/，
  本 skill 只呼叫、不重新發明。
---

# thumbnail-brainstorm — 封面 brainstorm 手冊（v2.4）

**版本：v2.4（2026-08-04，表情同調規則 + 表情版 scale 繼承；
v2.3 = TF 式雙臉版式 SOP + layout_solve 確定性求解；
v2.2 = cutout manifest 紀律；
v2.1 = N2 框型接上《張修修品牌識別》— 斜切框＋碎片、
品牌橘 `#F37425`、logo 淨空規範；v2.0 = 謝伯讓集 gate 前收斂；
v1.1 = 封面設計系統 v1 接入；v1.0 = ADR-054 D8/D9 首落地。
規格見 `docs/thumbnail-design-system.md`）**

你是 packaging 的**封面棒**：標題已定（Top 5 進 packages.json），你為前 3 條
各配一個封面、render 成 PNG、綁成 3 個 package。你**不改標題**：覺得某條標題
配不出封面（抽象到無畫面、與所有 S/A 級 thumb archetype 相斥）→ 記 run log
把該條換成 rank 4/5 遞補，不是改寫標題文字。

## 紅線

1. **契約歸 deterministic 工具**：packages.json schema（`shared/schemas/packaging.py`）、
   cutout 檔名（`cutout_filename`）、render（`thumbnail_worker`）、去背
   （hyperframes）只能經 PR 改；缺口記 run log Remaining，不即席發明欄位。
2. **機位驗證 fail 不許繞過**：`guest_cutout.py sample` 報 ValueError（expected
   speaker 窗內占比 < 0.6）時，唯一合法動作是查修 `highlights/tighten/director.json`
   的 `cams` 對應後重跑。**禁止**換 `--expected-speaker` 數字硬過——那正是
   ADR-054 A8③ 要堵的「穩定抽到錯的人且不報錯」。
3. **D/F 級 archetype 禁用**；C 級需在 run log 寫明 hedge 理由。封閉來源：
   playbook compact index，不即席發明 archetype。
4. **檔名 ASCII**：PNG 一律 `pkg-{cut_id}-{n}.png`；guest cutout 一律
   `cutout_filename("guest", i, emotion)` 產（帶 emotion — A8④）。
5. **設計系統紀律**（`docs/thumbnail-design-system.md` 硬紀律節）：一張圖一個
   idea、色彩角色鎖定（橘只當 highlight/框/bolt）、頭高 ≥45%、視線朝內、
   零裝飾、100px 自檢。diversity 軸 = **配方（N1/N2/N3）× 表情 × 大字**。
   真人不 AI（memory 鐵律）；N2 prop 卡供給 = Envato → 公版 → 圖表重繪。
6. **每集寫 run log packaging 節**（配對理由、表情選擇、否決、Remaining）。

## 輸入

- `<packaging_dir>/packages.json` — 目標 cut 的 `titles`（Top 5，各帶
  `archetype_id`）、`visual_recipe`、`cut_id`。packages 未滿 3 的中間態合法
  （本 skill 就是來補滿的）。
- podcast 配方另需：`highlights/winners.json`（段落時間窗）、
  `highlights/tighten/director.json`（機位 `cams` 對應）、episode 資料夾
  （`subs/words.json` + `Audio/` 分軌 + `normalized.wav`）。

缺輸入 → 停下報明缺哪個檔，不腦補。

## Step 0 — 配方檢查（fail loud 先行）

```bash
python -c "import sys; sys.path.insert(0, '.'); \
  from importlib.util import spec_from_file_location, module_from_spec; \
  spec = spec_from_file_location('rs', '.claude/skills/thumbnail-brainstorm/scripts/render_still.py'); \
  m = module_from_spec(spec); spec.loader.exec_module(m); \
  m.ensure_recipe_supported('<visual_recipe>')"
```

`youtube_book` 在此立刻 NotImplementedError（附參考圖庫指引）— 不做半套。

## Step 1 — joint pairing 配對（每條標題一個封面 idea）

注入 compact playbook（~1.5K tokens，勿讀 91KB 原檔）：

```bash
python -c "from shared.thumbnail_playbook import format_playbook_index_for_prompt as f; print(f())"
```

對 titles rank 1–3 逐條：

1. 取該條 `archetype_id`（T-A*）→ 查 joint pairings 有無 `title_archetype_id`
   相符的 JP-*（index 已附 `why_they_pair` 佐證）。有 → 用它的 thumb archetype。
2. 沒有相符 JP → 依 thumb archetype 的 when_to_use/brand-fit 自配一個
   （S/A 優先，D/F 禁用），run log 記「無 JP 佐證，自配理由」。
3. 三個封面在**表情／大字／裝飾**軸上拉開（例：驚訝大特寫 vs 解釋+圖示 vs
   認真+數字大字）。同 archetype 出現兩次即違反 diversity — 換掉一個。
4. 每個 idea 定案三件事：`thumb_archetype_id`、**大字**（3–7 字 hook 短語，
   不是標題全文——標題已在 YouTube 標題欄，封面大字補不同資訊）、
   **表情**（`prompts/thumbnail/emotions.yml` 七值之一，host 與 guest 各一）。

## Step 2 — 視覺配方 routing（修修 2026-07-28 裁：兩人都從 raw file 抽）

| visual_recipe | host | guest |
|---|---|---|
| `podcast` | Step 3 抽格（`--role host`，修修機位） | Step 3 抽格（`--role guest`） |
| `youtube_host` | 預建庫 `pick_youtube_host(表情, vault)`（非訪談影片才用） | 無 |
| `youtube_book` | Step 0 已 fail loud | — |

## Step 3 — cutout 抽格（兩個角色，僅 podcast）

1. 窗口 = `winners.json` 該 cut 的 start/end；機位檔 + `expected_speaker`
   = `director.json` 的 `cams` 對應。host 反應臉常在來賓說話窗 → `--role host`
   會跳過 speaker-dominance 檢查（機位正確性由 cams 設定把關）。
2. 抽格（guest 機位交叉驗證內建 — 見紅線 2）：

```bash
python .claude/skills/thumbnail-brainstorm/scripts/guest_cutout.py sample \
  --episode-dir "<episode>" --cam-video <機位.mp4> --role <host|guest> \
  --window <t0> <t1> [--expected-speaker <n>] \
  --out-dir "<packaging_dir>/<role>_frames/<cut_id>"
```

3. **vision 挑格（subagent，一次批量，兩個角色各一次）**：候選已按清晰度排序
   （= motion blur 淘汰）。任務 =「依 emotions.yml 為 Step 1 定案的表情各挑
   最佳一格；臉被手/麥擋、閉眼、動態模糊、側轉 >45° 淘汰；**回報視線方向**
   （放左緣的人要看畫面右，反之亦然）」。一個 subagent 看完全部候選。
4. 去背落檔（BiRefNet + 統一調色內建）：

```bash
python .claude/skills/thumbnail-brainstorm/scripts/guest_cutout.py finalize \
  --frame <picked.png> --emotion <表情> --ep-slug <ascii-slug> --index <i> \
  --role <host|guest> [--crop x0 y0 x1 y1] [--flip]
```

- `--crop`：比例框。**內側界（朝畫面中央那一側）必須落在自然物件的邊緣，
  不可切過身體** — 切過肩膀/手臂會在合成後留下一條懸空直線。決定方式：
  對整張 frame 去背 → 讀 alpha 欄剖面找「身體／麥克風／前景物」的分界 →
  界線放在麥克風等物件外緣（謝伯讓集：0.545 → **0.49**，肩線問題消失）。
  **不要目測猜**（2026-07-29 血淚：目測誤判成「怎麼切都會切到身體」）。
- 頭為主裁框：整顆頭佔 cutout 高 ~50%（兩顆頭等大的前提）；下緣可再裁胸
  以提高頭佔比（N2 用 0.882 倍高）。
- `--flip`：視線不朝內時翻轉（實拍像素、非 AI；**衣服有字時禁用**，run log 註記
  給修修否決權）。**先驗原始畫面的實際視線再決定**（vision agent 回報要抽查）。
- `--brightness`：gamma 微抬到**臉亮度落 123–130** 目標帶（謝伯讓集來賓需 1.20）；
  線性乘法禁用。`--sharpen`：放大 >1.1× 時補軟化。
- render 後**必看成品**：cutout 裁切/位置不對就調 crop 重出 — 一次迭代是常態。
- **表情庫一次抽齊（v2.4，修修 2026-08-04）**：vision 挑格與 finalize 不要只
  做本輪三個包用到的表情——host 與 guest 各自把 emotions.yml 常用值
  （至少 serious／surprised／excited／laughing 四值）**同一輪、同一個裁切框**
  全部 finalize 出來。理由：(1) 謝伯讓集 host 只落了兩種表情，pkg3 被迫與
  pkg1 同臉；(2) 表情版 scale 繼承（layout_solve 規則 7）要求同尺寸裁切框——
  事後補抽若裁切框不同，scale 就不可繼承，等於重做。
- **cutout 定稿即量測**：每顆 finalize 完立刻建 `cutouts_manifest.json`
  validated 條目＋精測地標（頭頂/眼/下巴 2x 網格精讀 + head_cols alpha bbox）
  ——排版期零手工。

## Step 4 — render 3 張 PNG（設計系統 v1）

依 Step 1 的配對選配方（N1 `thumbnail_full`／N2 `thumbnail_reaction`／N3
`thumbnail_topic` — 選擇邏輯見設計系統），寫 spec JSON 後：

```bash
python .claude/skills/thumbnail-brainstorm/scripts/render_still.py \
  --composition <thumbnail_full|thumbnail_reaction|thumbnail_topic> \
  --spec <spec.json> --out "<packaging_dir>/pkg-<cut_id>-<n>.png"
```

spec 的 variables 見各 composition 檔頭註解。**定案參數表在
`docs/thumbnail-design-system.md`（N1／N2 各一節，2026-07-29 修修鎖定）**，
起手直接照抄，只調每集差異項：

- **N1 完整訪談**：兩人 glow + 內緣 fade 9% + 字塊 z4（在人之下 → 字尾塞肩後）、
  字 Bold 無陰影、橘框 padding 14/14/5、`guest_credit`（頭銜＋姓名）、
  左下頻道 logo 92px、`text_center_pct` 每包微調
- **N2 精華長片**：右來賓 75%→頭56% + 左 Envato prop 卡（`prop_left_pct` 15／
  `prop_width_pct` 52，躲肩後）、零文字、`frame_style: hybrid`（品牌斜切框＋碎片）、
  logo `below-card` 96px、accent `#F37425`
- 大字 = **≤6 字/行 × 2 行**、**恰好一個** highlight 詞
- render 失敗（ThumbnailRenderError）→ 看 variables JSON 與 stderr 修完重跑；
  連續失敗 2 次停下報修修，不降級成無封面。

## Step 4.5 — 量測驗收（**不做不交付**）

目測會漏；三項都要跑（腳本邏輯見設計系統對應節）：

| 檢查 | 門檻 | 失敗時調 |
|---|---|---|
| 兩人**眼線差** | ≤10px @720p | `guest_height_pct`（放大＝眼線上移；**不要用 y 上移**，底部會露背景）|
| **字塊遮蔽平衡** | \|左遮−右遮\| ≤600px² | `text_center_pct`（線性內插 2 輪收斂）|
| 臉高／中心x／頂y／亮度 | 48–52%／14–17%·83–85%／8–12%／89–100 | height／x／brightness |
| **logo 淨空**（N2） | 上／下／左三邊皆 ≥ **0.235 × logo 高**（品牌書 p7） | `logo_height_px`；要更大就得動碎片幾何 |

⚠️ **順序有依賴**：先定眼線（改 height）→ 再校遮蔽平衡（改 text_center）。
放大來賓後遮蔽平衡必然漂掉，一定要重跑（謝伯讓集實測 +574 → +1985）。

## Step 5 — 回填 + 驗證 + 雙落點

寫 `specs.json`（3 筆：title_rank／thumbnail 本地路徑／thumb_archetype_id／
joint_pairing_id／host_cutout／guest_cutout），然後：

```bash
python .claude/skills/thumbnail-brainstorm/scripts/attach_packages.py \
  --packaging-dir "<packaging_dir>" --cut-id <cut_id> \
  --episode-slug <ascii-slug> --specs specs.json
```

script 會：PNG 複製進 vault `Attachments/packaging/<slug>/`、cutout 路徑轉
vault-relative、整檔過 `PackagesFileV1` 驗證（失敗即不落任何一份）、
working set 與 vault 雙寫（ADR-054 D10）。驗證錯誤讀訊息修 specs，不改 schema。

## Run log 格式（append 於 `<ep>/run_log.md`）

```markdown
## Packaging 封面節 — thumbnail-brainstorm v2.1
- L1 rank1「...」T-A8 → JP-7（T-V2 tight face crop）；guest 表情=驚訝
  （frame 0034，淘汰 0021 手擋臉）；大字「大腦會說謊」
- L1 rank3 無 JP 佐證 → 自配 T-V4（解釋語境、A 級）；理由：...
- Remaining：youtube_book 參考圖庫未建
```

## 精華長片 TF 式雙臉版式（v2.3–2.4 SOP — 修修 2026-08-04 定案，跨集可重現）

精華長片封面 = `thumbnail_reaction` + `prop_position:"center"`：兩側頭像＋
中央品牌框 prop 卡（代表精華重點的 stock photo，非文字——文字版是 N1
完整訪談語彙）。**人物幾何全走 `scripts/layout_solve.py`，零目測**：

```bash
# 每集一次：cutout 定稿後精測地標寫進該集 cutouts_manifest.json
#   landmarks_px = { head_top, eye, chin（row px，2x+ 放大 2% 網格精讀）,
#                    cutout_h, cutout_w, head_cols（頭部 alpha bbox 左右界，
#                    程式量：頭頂-下巴 rows 內 alpha>20 的 col min/max）}
python .claude/skills/thumbnail-brainstorm/scripts/layout_solve.py solve-duo \
  --manifest <cutouts_manifest.json> --host <host定稿.png> --guest <guest定稿.png>
# → 六個參數直接進 spec；render 前必跑 verify（PASS 才 render）
python .claude/skills/thumbnail-brainstorm/scripts/layout_solve.py verify \
  --manifest <cutouts_manifest.json> --spec <spec.json>
```

版式規則（solver 內建，跨集不變；謝伯讓集由修修 A/B/C 三版裁決收斂）：

| 規則 | 值 | 為什麼 |
|---|---|---|
| 等大基準 | **臉高（眼–下巴）**，非頭高 | 蓬髮吃頭高額度，等頭高=臉縮水（謝伯讓集 -2.4%）|
| guest 感知校準 | 臉再 **×1.05**（`--guest-face-boost`）| 正面臉＋眼鏡感知上小一號；指標等大≠感知等大，最後一格由修修 A/B 校準 |
| headroom | **0**（guest 頭頂 0px 且下緣貼底的耦合解；host 眼線跟隨）| TF 規格；guest 再上抬會在身下露背景縫 |
| 眼線 | 兩人鎖同一水平（差 ≤10px）| 修修 2026-07-29 驗收標準 |
| 外側出血 | 各切**頭寬 8%** 再**外移 5% canvas**（`--outward-shift-pct`，總裁切 ~20%） | TF「側邊切一點點」+ 修修定案：外移讓中央卡空間變大；出血對「頭」不對圖檔——兩顆 cutout 裡頭的位置不同 |

**中央卡定案規格（修修 2026-08-04 skew 定版）**：

- `frame_style: "skew"`（純斜切＋細橘框），**無碎片**——碎片在寬卡+雙臉下只露
  出零星角料反而像 artifact（`shard_edges:"topbottom"` 模式保留在 composition
  可隨時啟用，但 TF-duo 定案不用）
- `prop_width_pct: 53`、`prop_height_px: 455`——寬幅卡、兩人壓住卡緣（景深）
- prop 圖 = **實拍情境照 cover 塞滿**（該精華核心情境；可從本集 stock 素材
  抽靜幀）；**禁灰底攝影棚小物照**——留白會讓主體縮成一角
- 背景 = 修修正版 bg、logo bottom-left 92px、零文字

⚠️ 量測紀律（2026-08-04 事故的直接教訓）：**驗收用 verify 的數學預測，
不用眼睛讀格線**——目測誤差 ±5% 曾把一版數學正確的排版「修」壞（bottom
錨定負偏移方向感反轉＋確認偏誤）。眼睛只負責最後 sanity check 與感知校準
（臉等大的 ×1.05、外移量這類「感知量」由修修 A/B 收斂）。

**表情規則（v2.4，修修 2026-08-04：「這很重要」）**：

1. **包內同調**：同一張封面兩人情緒必須一致——話題嚴肅/警示 → 兩人
   serious/neutral；話題輕鬆/有趣 → 兩人可同笑。一人大笑一人肅穆 =
   不協調，直接重配。**表情從標題語氣推**（先定 pair 情緒、再挑 cutout），
   不是各自挑好看的格。
2. **包間拉開**：diversity 軸只作用在「包與包之間」（pkg1 嚴肅組/pkg2 笑組），
   **不是包內**——舊版手冊「三包表情拉開」被誤讀成包內混搭，正是 2026-08-04
   笑臉配肅臉事故的來源。
3. **表情版幾何走 scale 繼承**（solver 規則 7）：`solve-duo --host-expr/--guest-expr`
   ——臉高量尺會被張嘴表情撐長（+23% → 人被誤縮 19%），表情版必須繼承同人
   基準 cutout 的 scale，只重解 y/x。spec 寫入 `_solve` 中繼資料後，verify
   直接重算比對六參數（一致性判準，取代對 expr 對無意義的臉高比）。

**一次到位交付檢查（v2.4）**——給修修看之前，四項全過，缺一不交付：

- [ ] `verify` PASS（幾何一致性）
- [ ] 表情同調自檢（兩人情緒 × 標題語氣，逐包過）
- [ ] prop 幀乾淨（無動態模糊/殘影；抽幀要挑）
- [ ] 320×180 小圖可讀（YouTube 格線真實尺寸）

## 每集教訓寫回手冊

E2E 每跑完一集（gate approve 過），可固化的教訓 **append 進本節並 bump
版本號**（經 PR）。

### 教訓紀錄

**v2.0（2026-07-29，謝伯讓集 gate 前收斂）**

1. **對標的是修修自家 house style**（`E:\data\podcast thumbnail\EP112/114/117`），
   不是外部頻道；出手前先問「現有的長什麼樣」。
2. **元素存在 ≠ 位置正確**：自評打分前必須重開圖量測。曾經版式/臉都自評 90 分，
   修修給 0 分。
3. **hyperframes 截圖會丟棄 root 元素自身的 background** → 背景必須放子元素
   （`#bgfill`），否則輸出是 alpha=0 的透明圖（看起來像純黑）。
4. **實裝字型 family name 帶後綴**：`LINE Seed TW_TTF ExtraBold`／`... Bold`。
   寫 `LINE Seed TW` 會**靜默** fallback 微軟正黑。
5. **工具誤差不是保留既有結論的理由**：haar 對眼鏡側臉低估，但它早就顯示兩顆頭
   不等大 — 當時用「工具不準」搪塞 = 確認偏誤。
6. **先修結構再碰顏色**：亮度/色偏的抱怨常常根因在裁框與尺寸。
7. **AI 只做 graphic 與 render，真人一律實拍**（修修原則）；prop 走 Envato，
   授權檔可用 Claude in Chrome 走修修登入態下載（落點 `E:\`）。
8. 交付快照同步 `E:\data\AgentOutput\YYYYMMDD-<topic>\`（每輪都要，不是最後才做）。

**v2.2（2026-08-04，story-L1 TF 式封面爛版事故——三層根因）**

15. **cutout 資料夾是迭代歷史，不是素材庫**：`guest_v1..v8`／`host_v1` 是
    finalize `--crop` 的中間迭代（裁切幾何彼此不同：528×713／528×629／
    634×713／634×628、host_v1 甚至 1075×778 寬幅帶場景），只有最後一輪是
    定稿。把「版號×表情」當可互換素材庫亂抽 = 2026-08-04 v2/v3 爛版根因之一。
    **處置**：定稿寫進 `cutouts_manifest.json`（validated 清單＋各 composition
    已調參基準），中間產物歸檔 `_iterations/`。**排版只准用 manifest 裡的檔**；
    新集 finalize 收斂後立刻建 manifest。
16. **幾何參數 per-cutout、per-composition，皆不可移植**：`height_pct` +
    `object-fit: contain` 下，同一個 138% 套在不同 aspect 的 cutout 上頭的
    大小位置完全不同；同一顆 cutout 換 composition（N1 1280 畫布 vs N2）
    基準也不同。換 cutout 或換 composition = 從 manifest 基準起手重調。
17. **Step 4.5 量測驗收沒有「提案輪豁免」**：「先給修修看方向再驗收」=
    v2/v3 直接把爛版送到修修面前。**任何要給修修看的 render 都要先過
    量測**（skill 本來就寫「不做不交付」——這次是流程違規，不是規則缺口）。
18. **精華長片 TF 式版式 = N2 `prop_position:"center"` 雙臉夾中**，中央是
    品牌框 prop 卡（代表該精華重點的 stock photo），**不是文字**（修修
    2026-08-04 裁決；中央大字是 N1 完整訪談的語彙）。prop 圖要預裁緊
    （主體佔滿卡面）——cover 裁切不會幫你放大主體。

**v2.1（2026-07-29，N2 框型品牌化）**

9. **通用語彙 = 撞臉風險**：8px 圓角矩形橘框「沒有錯」，但那是 CW 也有的東西。
   出手前先問「這個元素在**修修的品牌書**裡對應到什麼」——
   `F:\Project Files\Assets\張修修品牌\張修修品牌識別_0827.pdf`。
   本案的答案早就在 p22（影片引用字卡）：框 ＋ 框背後爆出的鋸齒碎片。
10. **品牌書的內文可能跟稿件不一致**：p10 寫 `#e98965`，但同頁 CMYK／RGB 與
    實際稿件像素都指向 `#F37425`。**量稿件，不要抄內文**。
11. **母題要收斂不要直譯**：p15「自我解讀」的傾斜量等比例搬到卡片是 96px，
    看起來像壞掉；收到 3.5% 才成立。品牌書給的是**方向**不是**數值**。
12. **裝飾有空間成本，要先算再畫**：碎片需要 ~190px；原本卡左緣只剩 77px →
    卡右移並縮窄（6%→15%、58%→52%）。沒有先算就會做出被畫布切平的碎片。
13. **小尺寸驗證是獨立的一關**：320×180（YouTube 格線真實尺寸）另存一張比對。
    謝伯讓集實測——碎片在格線尺寸仍可辨識，斜切幾乎看不出來。
14. **淨空規範是 logo 尺寸的硬上限**（品牌書 p7，X ≈ 0.235 × logo 高）：
    修修說「放大一些些」時，不要憑感覺選一個數字 — 掃尺寸、量三邊、挑
    最大的合規值（本案 96px；108 起上緣就撞碎片）。
