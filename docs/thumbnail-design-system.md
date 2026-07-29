# 封面設計系統 v1.1（Thumbnail Design System）

> 訪談/影片封面的 single source of truth。**設計基準 = 修修自家 house style**
> （樣板：`E:\data\podcast thumbnail\EP112/114/117`，2026-07-28 修修裁決）；
> Modern Wisdom 153 張普查（計畫文件 §1）降為工藝參考（臉占比/元素紀律/100px 自檢）。
> 鐵律：**真人畫面不交 AI 合成**（`memory/claude/feedback_no_ai_synthesized_humans.md`）—
> 兩人 cutout 一律從訪談 raw file 抽格；AI 不生成人像；graphics 走 Envato/公版。
> 文字一律 deterministic render（composition 真字型，繁中零錯字）。

## 三配方（compositions 已落地 `video/compositions/`）

| 配方 | composition | 用途 | 文字 |
|---|---|---|---|
| **N1 完整版（house style）** | `thumbnail_full` | 完整集數：兩張超大臉貼緣出血＋中央兩行大字＋橘框 payoff＋EP/人名標籤 | 2 行 × 5–8 字 |
| **N2 反應卡** | `thumbnail_reaction` | clip/主題款：橘框 prop 卡＋反應臉 | **預設零字** |
| **N3 主題卡** | `thumbnail_topic` | 單人主題：左字塊＋右來賓＋實拍棚景壓暗背景 | 2–4 行＋名牌 |

## N1 house style 規格（EP112/114 逆向；composition 已凍結）

- **臉**：**頭高 65%（修修 2026-07-28 裁決：55% 太小、75% 太大）**、左右貼緣
  可出血側緣；mic／耳機保留（podcast 識別）。75% 大臉留給 N2 零文字反應卡
- **大字**：中央兩行、白 EB ~96px、line-height 1.28、title_max_width 460（65% 臉
  下的字塊寬）；**payoff 詞 = 橘底圓角框＋白字**（不是橘色字）— house style 核心元素
- **標籤**：左下 `EP<N>`、右下來賓名 — 橘底白字圓角框 44px
- **背景**：**修修正版背景圖 `E:\data\podcast thumbnail background.png`**（1920×1080
  炭灰 radial vignette），spec `images.bg_image_data_url` 帶入；CSS gradient 僅為
  無圖 fallback
- **調色**：punchy — 提亮 1.14＋對比 1.07，**不壓飽和**

## N2 精華長片卡規格（**2026-07-28 修修定案鎖定** — 精華長片一律用本配方）

CW 式空間邏輯：**人在前、卡在後、卡躲進肩膀後面**；零文字，敘事由 prop 卡承擔。

| 項目 | 定案值 | 備註 |
|---|---|---|
| 來賓頭占比 | **56%** | 頭肩完整（不切頭頂），非 N1 的 65% |
| 貼緣出血 | `guest_x_pct` **-10** | 臉右側輕微出血，與卡分離 |
| prop 卡寬 | `prop_width_pct` **58** | 右緣塞進肩膀後 ~76px = 層次感來源 |
| 人物 glow | 36px/0.5 ＋ 90px/0.3（`person_glow_color` 預設 `#EE8435`） | 大半徑低強度，均勻無硬邊 |
| 內側緣漸隱 | `inner_edge_fade_pct` **9** | 蓋掉裁框硬邊；mask 套在 glow 之後 |
| 頻道 logo | `logo_position: below-card`、`logo_height_px: 82` | 左下暗底（卡自動上移 46%）— **白線 logo 不可壓在 prop 卡上**（淺色卡會糊掉）。資產見下方 |
| 背景 | 修修正版背景圖 | `bg_image_data_url` |

**z 序**：背景 0 → prop 卡 4 → 人物 5 → logo 6。

**頻道 logo 資產**（2026-07-29 修修裁決）：用「張修修的不正常人生」的
`channel_profile_logo_rgb.ai` 轉出的**白線透明底單一臉部圖標**
`E:\data\podcast thumbnail props\channel_logo_face_white.png`；
先前「不正常人類研究所」那版（含麥克風＋#＋@ 符號）在縮圖尺寸下過於雜亂，已停用。
N1 用 92px、N2 用 82px。

## N1 完整訪談卡 — 與 N2 統一處理（**2026-07-29 修修裁決**）

N2 的三項處理反向套回 N1，兩種卡型視覺語言一致：

| 項目 | 定案值 |
|---|---|
| cutout 裁框 | 兩人皆走「內側界落在自然物件邊緣」規則（教授改用 `guest_v9_*`，含完整麥克風） |
| 人物 glow | 36px/0.5 ＋ 90px/0.3 橘 — 同 N2 |
| 內側緣漸隱 | `inner_edge_fade_pct` 9 — 兩人朝中央那側 |
| **分層** | **z：人物 5 > 字塊 4** — 字尾塞進肩／髮／麥克風後方（先前是字壓在人上，才需把字塊縮到 460px） |
| 字塊寬 | `title_max_width` **560–580**（分層後放大；兩側各與人物重疊一點點） |
| 字重 | **Bold(700)**，非 ExtraBold — 2026-07-29 修修：原本太粗 |
| 字陰影 | **無** — text-shadow 已移除（深底本身夠對比） |
| 橘框 padding | `14px 14px 5px` + `line-height 1.02` → 實測四邊視覺留白 上15/下14/左18 |
| 來賓 credit | 大字下方小字 `guest_credit`（如「臺大心理系教授 謝伯讓」）38px Regular；右下橘標籤改留空 |
| 字塊水平中心 | `text_center_pct`（謝伯讓集 pkg1/3 = **50.0**、pkg2 = 48.5）— 見下方遮蔽平衡量法 |
| 謝伯讓集參數 | host h138 x-14 y-14／guest h119 x-27 y0（y+7 會在底部留空白，2026-07-29 退回 0）|

**眼線對齊（2026-07-29 修修驗收標準）**：兩人**眼睛高度差 ≤10px**（720p 畫布）。
臉高相等 ≠ 眼線齊 — cutout 的底部裁切量不同，眼睛在素材裡的相對高度就不同。量法：

1. 兩張 cutout 疊格線讀出 眼線 y／頭頂／下巴（haar 對眼鏡側臉不可靠，用格線目視）
2. 螢幕眼線 y ＝ 元素底邊 −（cutout高 − 眼線y）× scale；scale ＝ 顯示高 ÷ cutout高
3. 解出對齊所需 `guest_height_pct`（放大＝眼線上移），**優先用放大而非 `guest_y` 上移**
   （上移會在底部露出背景 — v15 血淚）
4. render 後用 haar eye cascade 複驗

謝伯讓集實測：v20 差 32px → v22 差 **+2／+9／+5px**；guest h119→**125（pkg2/3）／130（pkg1）**
（pkg1 的「驚訝」格頭在素材中位置較高，需多放大 5%）
⚠️ **放大來賓後必須重跑遮蔽平衡** — 他變大會多壓到字（pkg2 從 +574 漂到 +1985）

**字塊遮蔽平衡量法（2026-07-29 修修驗收標準）**：字被兩人各遮多少要**對稱**，
目測會漏（左 0 右 3974px² 時看起來只是「稍微擋到」）。量法：

1. 同一 spec 再 render 一版**移除兩張 cutout** 的 text-only 圖
2. text-only 圖上取白字＋橘框像素 = 字塊 mask（排除 y>500 的 logo／角標）
3. 與正式 render 逐像素比對，差異 >90 者 = 被遮；以字塊中線分左右計面積
4. 調 `text_center_pct` 使 **|左遮 − 右遮| ≲ 600px²**（線性內插收斂，2 輪即可）

謝伯讓集實測（v22 定案）：pkg1 +343、pkg2 +201、pkg3 +65 ✅
字塊中心：pkg1 50.0／pkg2 47.9／pkg3 49.6

量測驗收（v15 實測）：臉高 49–53%（兩人相當）、中心 x 19–20%／83–84%、
頂 y 9–11%（齊平）、全圖亮度 99–100 — 全部落在樣板目標帶。

## 硬紀律（工藝底線，rubric 同款）

1. **一張圖一個 idea**：N2 的敘事在 prop 卡（不加字）；N1/N3 的敘事在大字（不加 prop）。
2. **色彩角色鎖定**：每張 ≤4 色系；橘 = highlight 框／標籤／prop 卡框，白 = 字，
   其餘中性 — 不出現第二個彩色。
3. **臉**：頭高 ≥60%；視線朝畫面內（cutout 可 `--flip`，衣字入鏡禁用）；貼緣不漂浮。
4. **文字**：LINE Seed TW EB（系統需裝 TTF；**實裝 family name 是
   `LINE Seed TW_TTF ExtraBold` — 寫 `LINE Seed TW` 會靜默 fallback 微軟正黑**，
   2026-07-28 血淚）；**恰好一個**橘框 payoff 詞；無描邊，soft shadow。
5. **裝飾零容忍**：無箭頭、無 emoji、無光暈。
6. **100px 自檢** + **成品必與 house style 樣板並排對照**（EP112/114）— 不對照不交付
   （2026-07-28 血淚：自評不是對照）。

## 素材管線（`.claude/skills/thumbnail-brainstorm/scripts/guest_cutout.py`）

1. `sample --role host|guest`：從機位原檔窗口化抽格（清晰度排序 = 避 motion blur）；
   guest 走機位×說話者交叉驗證；host（反應臉常在來賓說話窗）跳過 dominance 檢查。
2. vision subagent 批量挑格（表情 × 無遮擋 × 視線方向）。
3. `finalize`：BiRefNet 去背（fallback hyperframes u2net）→ `--crop x0 y0 x1 y1`
   （比例框：去麥臂/筆電/衣字，並決定「臉在裁框哪一側」→ 邊緣錨定位置）→
   `--flip`（視線朝內；衣字入鏡禁用）→ 統一調色（提亮 punchy；非 AI relight）。

**裁框規則（2026-07-28 量測迭代定案，取代早期經驗值）**：
- **內側界要落在自然物件邊緣，不可切過身體**：裁框左/右界若切過肩膀或手臂，
  合成後是一條懸空直線（N2 把人疊在卡上時特別明顯）。作法 = 先對整張 frame
  去背、看 alpha 欄剖面找「身體 / 麥克風 / 前景物」的自然分界，把界線放在
  麥克風等物件的外緣 → 輪廓收在圓形物體上讀作遮擋，不是刀切
  （謝伯讓集：左界 0.545 → 0.49，肩線問題消失）
- **頭為主裁框**：兩個 cutout 的「整顆頭」都要佔 cutout 高的 ~50% — 這是兩顆頭
  等大的前提（同 height_pct 下，body-dominant cutout 的頭必然縮小。血淚：謝伯讓
  頭曾只佔 35% → render 出來是修修的 0.6 倍）
- 裁框座標從**量測**來，不從目測猜：grid 疊圖讀 頭頂/下巴/臉緣 px，解
  scale = 目標頭高(≈0.50×720px) ÷ 頭在 cutout 的 px
- 眼線對齊:兩人眼睛 display y 都落 ~33%
- 出血:**臉框離側邊緣 ≤2%（量測 CW R1:1–2%）— 頭後半/耳機被畫框切掉是常態**,人要「擠進畫面」不是「站在畫面裡」;五官不可出血。臉中心目標 x 14–17% / 83–85%
- 方向:視線必朝內 — **先驗原始畫面的實際視線再決定要不要 flip**（vision agent
  的視線回報要抽查，2026-07-28 誤報導致整輪翻錯邊）
- 亮度:基準 = 攝影機原色不動;暗機位 `--brightness` 抬到**臉亮度落 123–130 目標帶**
  (gamma 曲線;謝伯讓集實測要 1.20)。**線性乘法禁用**(爆高光+膚色發灰,2026-07-28
  教訓);兩人調法必須幾乎一致,色調不一致比暗更醒目
- 謝伯讓集定案參數(v10, 頭 65%):host crop (0.30,0,0.60,0.72) 不翻轉/guest
  (0.545,0.02,0.82,0.68) brightness 1.20(gamma)+sharpen;height 138/145,
  x -14/-25,y -14/-18,title_max_width 460
- height_pct 換算:height = 目標頭占比 ÷（頭px ÷ cutout高px）— 謝伯讓集
  host 頭 365/777、guest 頭 320/713,65% → 138/145

## Render（deterministic）

```bash
python .claude/skills/thumbnail-brainstorm/scripts/render_still.py \
  --composition thumbnail_full --spec spec.json --out pkg-1.png
```

spec.json = `{"variables": {...}, "images": {"<var>_data_url": "<path>"}}` —
variables 見各 composition 的 `data-composition-variables`。字型/排版凍結在
composition；改版式 = 改 composition 經 PR，不即席調。

## Prop 卡供給線（N2）

優先序：**Envato stock**（MCP `search_items`）→ 公版藝術品 → 研究圖表重繪
（hyperframes 參數 render）。**不用**：未授權劇照/迷因、AI 生成圖（修修 2026-07-28 裁）。

## Revision Log

| 日期 | 版本 | 變更 |
|---|---|---|
| 2026-07-28 | v1 | 三配方落地＋素材管線（BiRefNet/crop/flip/grade）；M1 謝伯讓集 3 張驗證 |
