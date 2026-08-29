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
  Reject feedback 也會觸發本 skill：desktop packaging worker 會建立 immutable
  revision request，交給獨立 Agent 重做後回到 Packaging re-review。
---

# thumbnail-brainstorm — 封面 brainstorm 手冊（v3.2）

**版本：v3.2（2026-08-27，封面 cutout 排除 boom arm；
v3.1 = N2 橫框可延伸到人物後方，人物重疊不是失敗；
v3.0 = 人物 cutout 雙肩完整且前景不可挖洞；
v2.9 = 人物 cutout 必須保留完整麥克風；
v2.8 = Reject feedback → desktop revision agent；
v2.7 = 作者訪談的暗色書封中景；
v2.6 = 鄭國威集——內側 fade 吃臉事故 + gate 變體板；
v2.5 = 安吉集三輪事故定版——scale 每角色鎖定、
地標只准 face_measure 程式量、渲染成品 QA 是交付 gate；
v2.4 = 表情同調規則 + 表情版 scale 繼承；
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
7. **人物輪廓優先，雙肩不可裁斷，boom arm 不進封面**：定稿 cutout 必須保留
   頭部、兩側完整肩線與可見上臂；肩膀不可碰到左右裁切界。優先換用 boom arm
   沒有侵入人物 silhouette 的 source frame。若合格表情只存在於 boom arm 入鏡的
   frame，可用 deterministic alpha-mask 或傳統 non-generative clone／heal／inpaint
   retouch，且只准處理 boom arm 區域；禁止生成式影像、禁止重畫整個人物，boom arm 區域
   以外的像素與人物 identity／肩膀／衣服／姿勢必須保持不變。麥克風與線材允許保留；
   若保留，輪廓必須完整、不懸空。肩膀被直切、胸前／肩上透明挖洞或只剩半支
   麥克風，全部視為素材損壞。

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
   vision 回報還要明列：兩側肩線與可見上臂是否完整、boom arm 是否侵入人物
   silhouette、麥克風／線材若保留是否完整。先淘汰有 boom arm 的 frame；只有該格
   符合表情時，才可走紅線 7 的局部 deterministic retouch。不把「表情好」當成
   接受 boom arm 或破損輪廓的理由。
4. 去背落檔（BiRefNet + 統一調色內建）：

```bash
python .claude/skills/thumbnail-brainstorm/scripts/guest_cutout.py finalize \
  --frame <picked.png> --emotion <表情> --ep-slug <ascii-slug> --index <i> \
  --episode-dir "<episode>" --role <host|guest> [--crop x0 y0 x1 y1] [--flip]
```

- **雙落點（修修 2026-08-06 裁決）**：canonical 在 vault
  `Attachments/cutouts/podcast/<ep_slug>/`（Bridge 漏斗、cutout_library、
  frontmatter 都讀它），finalize 會自動鏡射整組到 `<episode>/packaging/cutouts/`
  ——素材要在 project 資料夾一眼看得到。歷史集數或 manifest 更新後補同步：
  `guest_cutout.py mirror --ep-slug <slug> --episode-dir "<episode>"`。
- `--crop`：比例框。**內側界（朝畫面中央那一側）必須落在自然物件的邊緣，
  不可切過身體** — 切過肩膀/手臂會在合成後留下一條懸空直線。決定方式：
  對整張 frame 去背 → 讀 alpha 欄剖面找「身體／麥克風／前景物」的分界 →
  界線放在麥克風等物件外緣（謝伯讓集：0.545 → **0.49**，肩線問題消失）。
  **不要目測猜**（2026-07-29 血淚：目測誤判成「怎麼切都會切到身體」）。
- **雙肩安全距離**：alpha bbox 的左右肩線外必須各留透明 padding；不能為了移除
  boom arm 直接水平裁掉一側肩膀。boom arm 和衣服重疊時，只能在其覆蓋區域用
  deterministic alpha-mask／傳統 non-generative retouch 修補，不可生成或重畫人物，
  也不能把整段 x 範圍切走。灰底驗收時兩側肩線都必須連續、沒有直切面或透明缺口。
- **去背後逐像素重看麥克風**：在灰底與深色底各開一次透明 PNG，沿麥頭、
  防噴罩、麥克風／線材外緣檢查 alpha。來源畫面有完整麥克風而成品缺一段時，先放寬
  crop；仍被 BiRefNet 漏掉就改用保留麥克風的遮罩／換格重做。禁止用殘缺結果
  繼續 render，因為縮圖下會直接看成「麥克風破掉」。
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
- **cutout 定稿即量測（v2.5 改版）**：每顆 finalize 完立刻跑
  `face_measure.py cutouts --write`（mediapipe iris/chin + alpha crown 35% 規則
  + head_cols + IOD，一鍵回寫 manifest）。**禁止 agent 目視量地標**——目視/
  啟發式已兩次釀禍（教訓 20）。IOD 離散 >4% 的格子 = 該格明顯前傾/後仰，
  可用但渲染 QA 會盯著看。

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
- **N1 作者／新書訪談**：有可驗證的實際書封時，以
  `book_cover_data_url` 置中作低亮度中景，書封必須完整可辨識但不得壓過標題與人臉；
  預設 `book_cover_opacity: 0.38`、`book_cover_brightness: 0.52`、
  `book_cover_height_pct: 94`。主持人／來賓依然在左右邊緣，不把書封放成取代中央圖的 N2；
  只能用出版社、書店或使用者提供的書封，在 run log 記錄來源與 SHA-256。
  若設計是「獨立書本置於背景」，必須把書本外部的白底／掃描留白完整去除並檢查
  alpha 邊緣；不能把帶白色矩形底的原始 JPG 直接調暗後當完成。保留書封本身的白色
  設計，去除的是書本外部背景；交付前在深色底重開 PNG 做視覺 QA。
- **N2 精華長片**：雙人夾中央實拍 prop 卡；`prop_position: center`、
  `prop_width_pct: 53`、`prop_height_px: 455`，卡片必須是橫向長方形並延伸到
  兩位 cutout 後方；零文字、`frame_style: skew`、logo bottom-left 92px、
  accent `#F37425`。人物在卡片前方的重疊是景深語彙，不得為了讓 bbox 不重疊
  而縮窄中央卡或裁掉肩膀。
- 大字 = **≤6 字/行 × 2 行**、**恰好一個** highlight 詞
- render 失敗（ThumbnailRenderError）→ 看 variables JSON 與 stderr 修完重跑；
  連續失敗 2 次停下報修修，不降級成無封面。

### Step 4.4 — 中央卡候選池（修修 2026-08-29：「來源的圖要多一點」）

在此之前 gate 只能挑臉、挑標題、打大字——中央圖是 hidden field，換不掉。現在
gate 上有一排圖庫縮圖可以點，池子由這一步填。

1. 用 Elements MCP `search_photos` 對這條標題的畫面概念搜，**務必帶
   `orientation: landscape`、`number_of_people` 依概念設**。一條概念搜 2–3 個
   不同說法，湊到二三十張才夠挑。
2. 逐筆抄成 `results.json`：`preview_url` / `item_url` / `title` / `author` /
   `query`（`query` 就是你當下用的搜尋詞——它會變成 receipt 裡的來歷）。
3. 下載預覽並落地候選池：

```bash
python .claude/skills/thumbnail-brainstorm/scripts/stage_center_candidates.py   --packaging-dir "<ep>/packaging" --cut-id <cut> --episode-slug <slug>   --results results.json
```

下的是**浮水印預覽**，不是授權檔。修修在 gate 上挑定、存配方之後，桌機端才依
`center_visual_asset` 對應的 `source` 走既有的 Elements 下載流程取正式檔並重出
（見 `brook-director/SKILL.md` 的下載程序）。挑十張下十張授權檔，九張是白下的。

直式素材會在這一步就被丟掉，不進 gate——它在 Step 4.8 那關本來就會被擋。

回填時 spec 帶 `center_candidate`（候選池那筆的 supply/source/query）＋
`center_why`，`center_provenance` 就會自動組好；來歷可以繼承，**配對理由不行**。

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

## Step 4.6 — 變體板（修修 2026-08-14 裁決：臉與大字要能在 gate 上挑）

一張定稿不夠——**臉的表情與封面大字都是品味量**，端一張上去等於替修修決定。
每條標題 render **一組變體**（建議 3 個表情對 × 2 個大字 = 6 張），連同定稿一起
`attach_packages.py` 回填（spec 加 `variants` 欄位），gate 上就是勾選題。

```
變體命名：var-r{rank}-{pair}-{bigtext}.png（ASCII，variant_id 同名去掉 var-/.png）
幾何：scale 已鎖定，表情對的 y/x 沿用 solver 解；只有大字換行寬度變 → 逐張跑 occlusion_check
```

- **gate 端零 render、零 LLM**（ADR-054 D11 不變）：PNG 桌機先做完，Bridge 只勾。
- 大字都不滿意 → 修修在 gate 打 `第一行／第二行[橘框詞]` 進 `bigtext_request`，
  **桌機端下次跑本 skill 時讀它重出一張新變體**（不是即時，UI 已標示）。
- 變體不必每張都過 Step 4.5 全套；**但被勾選的那張進交付前一定要跑**
  （`face_measure render` + `occlusion_check`）。

## Step 4.7 — 修修在 gate 組配方 → **render 一次**（修修 2026-08-14 定案流程）

他要的流程不是「先窮舉變體再挑」，是「**先把標題、大字、臉選定，再 render 一次**」。
gate 的〈組封面〉區寫 `approval.json` 的 `render_request`（title_rank／host_cutout／
guest_cutout／big_text／highlight_text），桌機端跑：

```bash
python .claude/skills/thumbnail-brainstorm/scripts/render_request.py   --episode-slug <slug> --packaging-dir "<ep>/packaging" --cut-id <cut>
```

script 內建：scale 鎖定（基準 cutout 解一次）→ 只解 y（眼線對齊來賓）與 x →
render → `occlusion_check` 兩輪內插收斂 → `face_measure render` gate → 回填
`rendered_png` 與該 rank 的 package 縮圖。**強表情素材要先備好**（見下節），
否則他在 gate 上只能從弱表情裡挑。

### Step 4.8 — 長 highlight composition receipt（沒有就不能 Approve）

每一張長 highlight 必須用 `thumbnail_reaction` render；composition 會透過 loopback callback
回傳同一次 render 的 DOM `getBoundingClientRect()`，並與 PNG 原子寫出
`<thumbnail>.png.composition.json`。`attach_packages.py` 的每筆 spec 必須提供
`render_spec`（該次 render_still JSON）；attach 會重驗 renderer/composition、variables、素材與
PNG hash，通過後自動把中央圖、measurement sidecar 與 receipt 寫到 vault：
`Attachments/packaging/<episode-slug>/composition_receipts/<cut-id>-r<rank>.json`。
中央必須是圖像素材，不能用文字代替。`center_visual_asset` 要先複製到同一集 packaging
目錄，使用 vault-relative 路徑；receipt 不能指到 episode 外或不存在的檔案。

**每個 spec 必須帶 `center_provenance`（v3 起強制）**——中央卡的來歷寫進 receipt，
不是寫在誰的記憶裡。2026-08-29 修修看到 20260805 punch-L04 rank 1 的中央卡是一隻
鸚鵡問「為什麼」，整條線翻完只查得到幾何與 SHA-256，配對理由沒有任何地方記過；
推得回去不等於交代過。`supply` 是封閉集合 `envato` / `public_domain` / `redrawn`
（真人一律不准 AI 生成，紅線 5）。

**中央卡素材本身必須是橫式，且長寬比要接近卡片**。卡片是 `object-fit: cover`，
比例不合就從短邊硬裁：同一張 rank 1 的素材是 1080×1920 直式，卡片 678×455
（1.49:1），只有 38% 的原圖進得了畫面，棲架與飼料碗全被切在框外。attach 現在會擋
直式素材，以及裁掉超過一半的極端比例（含過寬的全景）。先前只驗卡片 bbox 是橫的，
沒有人驗餵進去的素材。

2026-08-29 之前的 v2 receipt 仍然讀得進 gate（不追溯作廢已核准的成品），
但新產的一律是 v3。

```json
{
  "schema": "nakama.long_thumbnail_composition.v3",
  "episode": "<packages.json episode exact value>",
  "cut_id": "<cut-id>",
  "package_rank": 1,
  "thumbnail_png": "<packages.json selected thumbnail_png exact value>",
  "canvas_width": 1280,
  "canvas_height": 720,
  "center_visual_asset": "Attachments/packaging/<slug>/center-<cut-id>-r1.png",
  "center_provenance": {
    "supply": "envato",
    "source": "<Envato 品項 URL／id；公版寫來源；重繪寫依據的資料出處>",
    "query": "<找到它的搜尋詞；重繪寫重繪依據>",
    "why": "<這張圖扣回哪一個 beat／quote——至少 12 字，不准寫「配合主題」>"
  },
  "thumbnail_sha256": "<64 lowercase hex>",
  "center_visual_sha256": "<64 lowercase hex>",
  "measurement_sidecar": "Attachments/packaging/<slug>/<thumbnail>.composition.json",
  "measurement_sidecar_sha256": "<64 lowercase hex>",
  "renderer_identity": "hyperframes@<version>",
  "protected_center_bbox": {"x": 420, "y": 100, "width": 440, "height": 520},
  "host_bbox": {"x": 0, "y": 40, "width": 380, "height": 680},
  "guest_bbox": {"x": 900, "y": 40, "width": 380, "height": 680},
  "title_bbox": null,
  "max_protected_overlap_ratio": 1.0
}
```

所有 bbox 都是 1280×720 成品 DOM 實測值，不准手填或拿 spec/CSS 預估值代替。Bridge 會
重新 hash PNG、中央圖與 sidecar，並核對 sidecar identity/bbox；舊 v1、任一檔缺失或漂移、
中央卡不是至少 50% 畫布寬的橫向卡，都會 `COMPOSITION BLOCKED`。人物元素可出血並壓在
中央卡前方；這是 N2 版式的一部分。短片不走此 gate。

### Step 4.9 — Reject feedback → desktop revision agent（v2.8）

Packaging gate 的 Reject 不再只留 note。Bridge 只寫 `approval.json` 的
`revision_job`（`packaging-revision-job-v1`），封存 feedback、Reject 當下的
`packages.json` SHA-256 與每張封面 SHA-256，狀態從 `queued` 開始。Bridge 本身仍零 LLM。

桌機 `scripts/render_watcher.py` 認領後必須依序：

1. 驗證 source hashes；任一漂移即 `failed`，不可把 feedback 套到錯版。
2. 備份至 `<episode>/packaging/revisions/<request_id>/before/`。
3. 啟動 bounded Codex Agent；只可修改該集 working/vault packaging 與該集 cutouts，
   禁止碰 code、approval.json、Resolve、YouTube 或發布狀態。
4. 重新跑本 skill 的素材選擇、去背、render 與 QA；不得只改 JSON 宣稱完成。
5. worker 重驗 PackagesFileV1、working/vault bytes、1280×720 PNG 與 before/after
   fingerprint。通過才寫 `packaging-revision-result-v1` 並標 `ready_for_review`。

Agent **永遠不得自動 Approve**。失敗顯示 error 且不自動重試；只有修修在 gate 按
`Retry revision` 才把同一 request 重新排回 `queued`。新的 Reject 會建立新的 request，
舊 revision 目錄保持可回復。

### 強表情素材怎麼找（不要只抽你想得到的那幾段）

鄭國威集教訓：只抽 9 個窗（106 分鐘裡的 9 分鐘）→ vision agent 回報「全部候選
沒有一格眼睛睜大」，修修回「表情都差強人意」。正確做法是**用資料找段落**：

1. 兩人各自的 mic 軌能量 → 逐 20 秒窗取 95th percentile → 每人 top 12 高能量窗
   （笑聲與激動段自然落在這裡；guest 另外要求該窗自己詞占比 ≥0.6 才過機位驗證）
2. 這些窗抽格（每窗 ~10 格）
3. **mediapipe blendshapes 量表情強度**（`mouthSmile` / `jawOpen` / `eyeWide` /
   `browInnerUp`，`eyeBlink>0.5` 淘汰）→ 每個類別取 top N 給人眼複驗
4. 定稿進 `cutouts_manifest.json` — gate 的臉挑選器只列 validated 清單

實測差異：舊法 host 最強 smile 0.37；新法 smile 0.88–0.93 + jawOpen 0.5+（真的大笑）。

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

### 目錄分層（修修 2026-08-15：「package 那個資料夾裡面太亂了」）

```
<episode>/
├── final/                    ← 上架就看這裡：cover-<cut_id>.png ＋ title-<cut_id>.txt
│                               每次 render 覆蓋同名檔，永遠只有現在這一版
└── packaging/
    ├── packages.json geometry.json keywords.json title_trace.json
    ├── composition_receipts/    ← 長 highlight 每個 package 的中央主圖／bbox 驗證
    ├── manifest.json specs.json  pkg-<cut_id>-<rank>.png（packages.json 引用的）
    ├── briefs/ cutouts/ review_sheets/
    └── _work/                ← spec_*、_textonly-*、抽格 frames、比較板變體
```

`render_request.py` 的中間產物一律寫進 `_work/`；`_transparent1px.png` 缺了會自動
補（以前靠人手放，新集數會 render 失敗）。根目錄只留定稿與資料檔。

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
# 每集一次：cutout 定稿後程式量地標（mediapipe iris/chin + alpha crown 35% 規則）
python .claude/skills/thumbnail-brainstorm/scripts/face_measure.py cutouts \
  --manifest <cutouts_manifest.json> --write
# 每包：--host/--guest 一律傳基準對（兩人 serious 定稿），表情用 --*-expr
python .claude/skills/thumbnail-brainstorm/scripts/layout_solve.py solve-duo \
  --manifest <m.json> --host <host_v1.png> --guest <guest_v1.png> \
  --host-expr <host_表情.png> --guest-expr <guest_表情.png> [--guest-face-boost 1.0]
# → 六參數進 spec（含 _solve 中繼資料，boost 一併記入）；render 前 verify
python .claude/skills/thumbnail-brainstorm/scripts/layout_solve.py verify \
  --manifest <m.json> --spec <spec.json>
# render 後交付 gate（量成品像素，該集所有包一起比）：
python .claude/skills/thumbnail-brainstorm/scripts/face_measure.py render \
  --host-ratio-target <boost> --png pkg1.png --png pkg2.png --png pkg3.png
```

版式規則（solver v3.1 內建，跨集不變）：

| 規則 | 值 | 為什麼 |
|---|---|---|
| **尺寸** | 臉高的**畫布絕對目標** `--face-target-frac`（0.347 = 謝伯讓定案實測 248–262px@720 的中值） | 舊「crown 到圖底填滿畫布」隨裁切框留多少身體而變——安吉集因此比定案小 20%（教訓 24）|
| **量尺鎖定** | 每角色 scale 由**基準對解一次**，該集全部表情版共用；表情版只解 y/x | 單張照片任何標量（臉高/頭高/IOD）被姿勢真實改變 ±5–10%，per-photo 重解＝把姿勢噪音放大成成品噪音（教訓 19）；同人同機位同裁切框物理上同 scale |
| 等大 | s_h = s_g0 × **geomean(臉高比, 頭高比)**（基準對量） | 臉與頭**同時**逼近等大；好地標下兩指標差 <2%，差 >6% 時 solver 警告、需人工確認 |
| guest 感知校準 | `--guest-face-boost` per-pairing 修修拍板一次（謝伯讓 1.05、安吉 1.0；預設 1.0） | 指標等大≠感知等大；但校準值**不可跨 pairing 搬**（教訓 22）|
| **垂直錨定（v3.2）** | host：自己的頭頂釘頂緣。guest：**眼線齊 host 為主，≤12px 眼差（`EYE_SOFT_PX`）買回等量 headroom**；衝突 ≤12px 退化成頂天（微笑對不變）；前傾反向照齊（裁到頭頂，TF 允許）。下緣搆不到畫布底＝裁切框太短，fail loud | 純頂天＝仰頭照臉浮太高（「頭太上面、不平衡」）；純眼線鎖＝headroom。兩個單邊都被修修打槍；12px 是他在五檔變體板上點的 D（教訓 25/26）|
| 眼線 QA | render QA 逐張驗「實測 vs solver 預測 ≤10px」；跨張隨姿勢漂＝合法資訊輸出 | 參數自洽 ≠ 看起來對（教訓 21）|
| 外側出血 | 各切**頭寬 8%** 再**外移 5% canvas**（`--outward-shift-pct`，總裁切 ~20%） | TF「側邊切一點點」+ 修修定案：外移讓中央卡空間變大；出血對「頭」不對圖檔——兩顆 cutout 裡頭的位置不同 |

**中央卡定案規格（修修 2026-08-04 skew 定版）**：

- `frame_style: "skew"`（純斜切＋細橘框），**無碎片**——碎片在寬卡+雙臉下只露
  出零星角料反而像 artifact（`shard_edges:"topbottom"` 模式保留在 composition
  可隨時啟用，但 TF-duo 定案不用）
- `prop_width_pct: 53`、`prop_height_px: 455`——寬幅卡、兩人壓住卡緣（景深）
- prop 圖 = **實拍情境照 cover 塞滿**（該精華核心情境；可從本集 stock 素材
  抽靜幀）；**禁灰底攝影棚小物照**——留白會讓主體縮成一角
- 背景 = 修修正版 bg、logo bottom-left 92px、零文字

⚠️ 量測紀律（兩次事故各一課，方向相反、都要守）：
(1) 2026-08-04——**排版參數不許用眼睛讀格線定**：目測 ±5% + 確認偏誤曾把
數學正確的版「修」壞。地標與求解全程式。
(2) 2026-08-05——**驗收不許拿參數自洽充當**：verify 全 PASS 的三張成品，
實測眼線漂 63px、host 臉跨張縮 16%。交付判準 = `face_measure.py render`
量成品像素 + 親眼看全圖。**程式管幾何，眼睛管感知，兩者不可互替。**

**表情規則（v2.4，修修 2026-08-04：「這很重要」）**：

1. **包內同調**：同一張封面兩人情緒必須一致——話題嚴肅/警示 → 兩人
   serious/neutral；話題輕鬆/有趣 → 兩人可同笑。一人大笑一人肅穆 =
   不協調，直接重配。**表情從標題語氣推**（先定 pair 情緒、再挑 cutout），
   不是各自挑好看的格。
2. **包間拉開**：diversity 軸只作用在「包與包之間」（pkg1 嚴肅組/pkg2 笑組），
   **不是包內**——舊版手冊「三包表情拉開」被誤讀成包內混搭，正是 2026-08-04
   笑臉配肅臉事故的來源。
3. **表情版幾何鎖 scale**（solver v3）：`solve-duo --host-expr/--guest-expr`
   ——scale 從基準對解一次後鎖定，表情版**不重解尺寸**（張嘴、仰頭、前傾
   都會真實改變單張地標 ±5–10%；重解＝把姿勢噪音變成跨包尺寸噪音）。
   表情版只解 y（眼釘 eye_lock、防露底縫）與 x（自己的 head_cols）。
   spec 寫入 `_solve`（**含 guest_face_boost**）後 verify 重算比對六參數。

**一次到位交付檢查（v2.5）**——給修修看之前，五項全過，缺一不交付：

- [ ] `verify` PASS（spec 參數 vs solver 重算自洽——render 前的 sanity）
- [ ] **`face_measure.py render` QA PASS**——render 出的 PNG 上直接量兩張臉
      （跨包 IOD/臉高離散、眼線漂移、包內比例）。**這才是 gate**：verify
      PASS 擋不住 63px 眼線漂移（教訓 21）
- [ ] 親眼看全圖（人物大小/位置/與中央卡的關係）＋ 320×180 小圖可讀
- [ ] cutout 頭部、雙肩、可見上臂完整；肩線不碰左右界、無直切或透明挖洞
- [ ] boom arm 未進人物 silhouette；若局部移除，只有 boom arm 區域像素可改，
      其餘人物像素／identity／肩膀／衣服／姿勢不變；麥克風與線材可保留但不可殘缺
- [ ] 表情同調自檢（兩人情緒 × 標題語氣，逐包過）
- [ ] prop 幀乾淨（無動態模糊/殘影；抽幀要挑）

**全自動做不到、要人的部分（v2.5 誠實邊界）**：

- `guest_face_boost` 感知微調：每個**新 pairing** 由修修看渲染成品拍板一次
  （預設 1.0 起手——等大指標已收在 ±1%；某側看起來偏小就 ±0.03–0.05 重出），
  拍板後記進 spec `_solve`、整集鎖定。感知等大是品味量，沒有客觀正解。
- 表情選格是品味（開心程度 × 話題重量）：vision agent 提名、修修有否決權。
- QA 綠燈內的 ±4% 呼吸是姿勢物理（大笑前傾臉會長一點），不是 bug；
  「三張像素級全同」做不到也不該追求——那等於同一格照片用三次。

**感知量收斂 = 變體板，不是多輪迭代（修修 2026-08-05 定案）**：
凡遇「兩個約束數學上互斥、取捨是感知判斷」（例：仰頭大笑照的頂天 vs
眼線齊，差 30–50px 無普適解）或任何拿不準的感知量（boost、外移量、
headroom 容忍度）——**同一張封面 render 4–6 個變體（只動那一個變數、
等距取樣兩極之間）＋ 拼對比板附量化標籤，讓修修一次挑**。單版改一輪
給一輪是效率違規。挑完把選擇編成常數寫回 solver／spec（`_solve` 記錄），
**同一類情境從此不再問**——變體板是校準步驟，收斂後消失。

## 每集教訓寫回手冊

E2E 每跑完一集（gate approve 過），可固化的教訓 **append 進本節並 bump
版本號**（經 PR）。

### 教訓紀錄

**v3.1（2026-08-27，林之晨 Long 1——驗收規則反向逼出直立窄框與裁肩）**

31. **N2 的人物與中央卡本來就要重疊**：舊 composition receipt 把人物元素 bbox
    與中央卡重疊 >5% 視為失敗；因為 bbox 包含透明畫布，執行者只能把 53% 橫卡縮成
    25% 直立卡，再把完整肩膀 cutout 裁成窄頭像才能通過。這與 house style 相反。
    從此 deterministic gate 只確認中央圖存在、卡片覆蓋中心且為至少 50% 畫布寬的
    橫向卡；人物可在 z-order 上壓住卡緣。肩膀是否完整由交付前實際看成品判斷，
    不用透明 canvas bbox 代替視覺判斷。

**v3.0（2026-08-21，林之晨集——為了去支架而裁掉來賓肩膀）**

30. **去掉支架不等於裁掉支架所在的整段畫面**：第一次修正 `guest_v6_laughing`
    用水平 crop 拿掉白色懸臂，也把來賓左肩一起切掉；這違反已定義的雙肩完整
    silhouette。正確處理是保留完整人物與麥克風本體，單獨移除長支架，並重建其
    後方的條紋襯衫；最後在灰底確認兩側肩線連續且都有透明 padding。

**v2.9（2026-08-21，林之晨集——大笑 cutout 的麥克風被去背遮罩切壞）**

29. **表情好不能抵銷前景物損壞**：`guest_v6_laughing` 的笑臉成立，但麥克風
    被遮罩切成殘缺形狀；小圖上比表情更先被看成瑕疵。從此來源 frame 只要有
    麥克風，就把「人物＋手＋麥克風」當成同一前景組合驗收；crop 留足外緣，
    去背後在灰／深雙底檢查 alpha，任何麥頭懸空、支架中斷或遮罩挖洞都退回
    finalize／換格，不得進 package。

**v2.6（2026-08-14，鄭國威集——內側 fade 把主持人的臉吃掉）**

27. **`inner_edge_fade_pct` 吃的是「元素內側 9% 寬」，不管那裡是不是臉**。本集把
    host 裁切框右界切在 0.505（＝他身體輪廓邊緣），而他側身朝向畫面中央 →
    **他的臉就是剪影最內側的東西**，9%（66px）整條壓在額頭與髮際線上；橘色
    glow 沿剪影畫，也跟著被衰減（實測剪影外圈 R−B：host 42.1 vs guest 60.9）。
    來賓那側同一條規則什麼事都沒有，因為他的臉離內側界還有 120px 肩膀。
    **裁切框的內側界不只要落在自然物件邊緣，還要留至少 fade 寬度（9% 元素寬）
    的非臉部素材給它吃**——本集改成含整支麥克風（x 0.21–0.62）後，兩人 glow
    回到 63.6 / 61.1。
28. **「臉不見了」不會只有一個原因，要逐項量**：修修回報「肩膀、麥克風、臉、
    光暈全被遮住」，實際是兩件事疊加——fade 吃臉（量得出：fade on/off 逐像素
    diff 只落在 x 358–735）＋ **我把麥克風與下半身直接裁掉了**（crop y 0–0.62
    切掉杯子與手時連麥克風一起切）。第一次回答只講了 fade，被打回。**回報視覺
    問題時，把「我改了什麼」逐項列出來對照，不要只解釋最先想到的那一個。**

**v2.5（2026-08-05，安吉集封面三輪同類投訴——scale 鎖定 + 渲染 QA 定版）**

19. **單張照片的任何標量都被姿勢真實改變 ±5–10%**（張嘴讓眼-下巴 +9%、仰頭
    讓髮頂投影 -10%、前傾讓 IOD +7%——mediapipe 實測）。「每張表情照各自量、
    各自解等大」＝把姿勢噪音放大成成品噪音：實測跨三包 host 臉高漂 16%、
    guest 頭 384→445px、眼線漂 63px，修修三連投訴（尺寸不一、臉偏小、位置漂）
    全部源於此。同人同機位同裁切框 → pixel scale 物理上相同（IOD 跨表情
    離散 ≤1% 佐證）——**scale 每角色解一次後鎖定**。
20. **地標只准程式量**（`face_measure.py cutouts --write`）。agent 目視／
    啟發式量地標兩次釀禍：耳機頭帶被當頭頂（頭高比誤讀 1.036，實際 1.14）、
    下巴梯度誤讀 ±6px。目視只做最後 sanity check，不進數字迴路。
21. **QA 要量交付物，不是量計畫**：verify PASS 只證明 spec 參數與 solver
    自洽——眼線漂 63px 的三張成品 verify 全 PASS 過。交付 gate 必須直接
    偵測 render PNG 上的臉（`face_measure.py render`）。**參數自洽 ≠ 看起來
    對**；把 verify 當視覺驗收 = 本次「QA 寫了卻攔不住」的直接原因。
22. **感知校準值不可跨 pairing 搬**：guest_face_boost 1.05 是謝伯讓
    （眼鏡正面臉）的 A/B 拍板值，慣性搬到安吉集＝修修兩輪「我偏小」的
    成因之一。新 pairing 一律 1.0 起手，要調由修修看成品拍板，記進
    `_solve` 鎖定。
23. **連續同類投訴時，先質疑量測與求解結構，不要 metric ping-pong**：
    臉高等大↔頭高等大來回換量尺，兩輪都錯——錯的不是量尺選擇，是
    「壞地標 + per-photo 重解」。換指標再賭一次只是把同一批噪音換個
    投影方向。
24. **尺寸目標錨定在「被核准的絕對量」，不是裁切框相對量**：「crown 到
    圖底填滿畫布」跟裁切框留多少身體綁死——安吉集裁切留身較多，同一條
    規則下人比謝伯讓定案小 20%（修修：「cutout 太小了，跟 TF／謝伯讓成品
    比就知道」）。修法＝直接量核准成品（謝伯讓三張臉高 248–262px@720）
    → 常數 0.347 進 solver。**有「多大才對」的疑問，先量修修核准過的
    成品與 TF 原圖，不要從幾何規則推。**
25. **眼線硬鎖是 headroom 的根源**：眼線常數來自 serious 基準照的幾何；
    仰頭大笑照的 crown→eye 投影短 30px+，把眼睛釘在常數上＝頭頂掉下來
    （SL7 曾出現 39px headroom）。TF 規格的硬約束是**頂天蓋地**，眼線是
    軟的（TF 原版實測自己就漂 ~10% canvas）。錨定各照片**自己的 crown**
    到頂緣後，headroom 結構性不可能出現——不是「調到 0」，是「錨在 0」。
26. **頂天與眼線齊互斥時是感知量，走變體板**：修修對 SL7 純頂天版的
    回饋是「來賓的頭太上面、不平衡」——25 的 crown 錨定消滅了 headroom
    卻讓仰頭照的**臉**浮太高；反向（眼線齊）又生 49px headroom。兩極
    之間哪一點「看起來平衡」沒有數學解——render 五檔等距變體讓修修挑
    （修修原話：「render 五張不同大小位置讓我挑，比改好幾輪有效率」），
    選點編成常數。**演算法負責把選項空間縮到一維，人負責在一維上點一下。**
    收斂結果：修修點 D、E 次之 → 規則＝眼線齊為主、`EYE_SOFT_PX = 12px`
    眼差買 headroom（solver v3.2 規則 3b）；微笑對自動退化成頂天、不受
    影響。變體板從出板到定案一輪完成，驗證了這個工作法。

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
