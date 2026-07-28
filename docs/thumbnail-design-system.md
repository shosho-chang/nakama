# 封面設計系統 v1（Thumbnail Design System）

> 訪談/影片封面的 single source of truth。對標基準：Modern Wisdom 153 張全量普查
> （`docs/plans/2026-07-28-thumbnail-quality-upgrade-plan.md` §1）。
> 鐵律：**真人畫面不交 AI 合成**（`memory/claude/feedback_no_ai_synthesized_humans.md`）—
> 兩人 cutout 一律從訪談 raw file 抽格；AI 不生成人像；graphics 走 Envato/公版。
> 文字一律 deterministic render（composition 真字型，繁中零錯字）。

## 三配方（compositions 已落地 `video/compositions/`）

| 配方 | composition | 用途 | 文字 |
|---|---|---|---|
| **N1 完整版** | `thumbnail_full` | 完整集數：修修左緣＋來賓右緣＋中欄大字＋名牌 | 2–6 字 × ≤3 行 |
| **N2 反應卡** | `thumbnail_reaction` | clip/主題款（對標主力 52%）：橘框 prop 卡＋反應臉 | **預設零字** |
| **N3 主題卡** | `thumbnail_topic` | 單人主題：左字塊＋右來賓＋實拍棚景壓暗背景 | 2–4 行＋名牌 |

## 硬紀律（從普查逆向，rubric 同款）

1. **一張圖一個 idea**：N2 的敘事在 prop 卡（不加字）；N1/N3 的敘事在大字（不加 prop）。
2. **色彩角色鎖定**：每張 ≤4 色系。橘 `#e98965` 只出現在 highlight 詞、prop 卡框、
   bolt、名牌分隔線 — **不做大面積填色**。名牌 = 白底 ink 字。背景 = 暖近黑 `#191613`。
3. **臉**：頭高 ≥45%（目標 50–70%）；視線朝畫面內（cutout 可 `--flip`，衣字入鏡禁用）；
   人物貼 frame 邊緣，不漂浮。
4. **文字**：LINE Seed TW EB（系統需裝 TTF）；白 + **恰好一個**橘 highlight 詞；句點
   結尾；名牌「來賓名｜頭銜」。無描邊 — 用 soft shadow。
5. **裝飾零容忍**：無箭頭、無 emoji、無光暈、無漸層裝飾（vignette 是融合手段不是裝飾）。
6. **100px 自檢**：縮到 feed 尺寸仍讀得出 idea 才算過。

## 素材管線（`.claude/skills/thumbnail-brainstorm/scripts/guest_cutout.py`）

1. `sample --role host|guest`：從機位原檔窗口化抽格（清晰度排序 = 避 motion blur）；
   guest 走機位×說話者交叉驗證；host（反應臉常在來賓說話窗）跳過 dominance 檢查。
2. vision subagent 批量挑格（表情 × 無遮擋 × 視線方向）。
3. `finalize`：BiRefNet 去背（fallback hyperframes u2net）→ `--crop x0 y0 x1 y1`
   （比例框：去麥臂/筆電/衣字，並決定「臉在裁框哪一側」→ 邊緣錨定位置）→
   `--flip`（視線朝內；衣字入鏡禁用）→ 統一調色（壓飽和 matte；非 AI relight）。

**裁框經驗值**（謝伯讓集實測）：host bust =（0.24, 0.02, 0.60, 0.58）+ flip；
guest 臉貼右緣 = 裁框右界收到臉右緣（如 0.50–0.82），否則右錨定錨到空肩膀。

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
