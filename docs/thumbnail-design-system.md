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

- **臉**：頭高 60–80%、頭頂出血（height_pct 118–126）、左右貼緣可出血側緣；
  mic／耳機保留（podcast 識別）
- **大字**：中央兩行、白 EB ~96px、line-height 1.28；**payoff 詞 = 橘底圓角框
  ＋白字**（不是橘色字）— house style 核心元素
- **標籤**：左下 `EP<N>`、右下來賓名 — 橘底白字圓角框 44px
- **背景**：炭灰 radial（中央 `#414141` → 邊緣 `#1F1F1F`），非純黑非暖黑
- **調色**：punchy — 提亮 1.14＋對比 1.07，**不壓飽和**

## 硬紀律（工藝底線，rubric 同款）

1. **一張圖一個 idea**：N2 的敘事在 prop 卡（不加字）；N1/N3 的敘事在大字（不加 prop）。
2. **色彩角色鎖定**：每張 ≤4 色系；橘 = highlight 框／標籤／prop 卡框，白 = 字，
   其餘中性 — 不出現第二個彩色。
3. **臉**：頭高 ≥60%；視線朝畫面內（cutout 可 `--flip`，衣字入鏡禁用）；貼緣不漂浮。
4. **文字**：LINE Seed TW EB（系統需裝 TTF）；**恰好一個**橘框 payoff 詞；無描邊，
   soft shadow。
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
- **頭為主裁框**：兩個 cutout 的「整顆頭」都要佔 cutout 高的 ~50% — 這是兩顆頭
  等大的前提（同 height_pct 下，body-dominant cutout 的頭必然縮小。血淚：謝伯讓
  頭曾只佔 35% → render 出來是修修的 0.6 倍）
- 裁框座標從**量測**來，不從目測猜：grid 疊圖讀 頭頂/下巴/臉緣 px，解
  scale = 目標頭高(≈0.50×720px) ÷ 頭在 cutout 的 px
- 眼線對齊:兩人眼睛 display y 都落 ~33%
- 出血:後腦/耳機可出血側緣（guest_x 可到 -13%），五官不可
- 方向:視線必朝內 — **先驗原始畫面的實際視線再決定要不要 flip**（vision agent
  的視線回報要抽查，2026-07-28 誤報導致整輪翻錯邊）
- 亮度:基準 = 攝影機原色不動;暗機位 `--brightness` 微抬(gamma 曲線,1.12 上限左右)。**線性乘法禁用**(爆高光+膚色發灰,2026-07-28 教訓);兩人調法必須幾乎一致,色調不一致比暗更醒目
- 謝伯讓集定案參數:host crop (0.30,0,0.60,0.72) 不翻轉/guest (0.545,0.02,0.82,0.68)
  brightness 1.12(gamma);兩者 height_pct 112,host_x -5,guest_x -13

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
