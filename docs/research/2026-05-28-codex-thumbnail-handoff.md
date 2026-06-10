---
title: "Codex Handoff — Title × Thumbnail Visual Quality"
date: 2026-05-28
from: Claude (Opus 4.7)
to: Codex
status: open
---

# Codex Handoff — Title × Thumbnail Visual Quality

## TL;DR (read this first)

Pipeline plumbing 全部完工 — title parsing, idea cards, cutout library, bridge UI, AI bg wrapper, hyperframes composition, 5 locked templates, Windows asyncio fix, commit flow。**唯一 unsolved 的是：實際產出的縮圖跟 `E:/Thumbnail-example/` 裡 Alex Hormozi / Ali Abdaal / Cleo Abram / Jeff Su 的水準仍有顯著落差。**

修修對最後一輪 V2 結果（`E:/nakama/data/thumbnails/_creatine_v1/final_v2.png`）的評語：「差太多了」。Claude 經過多輪迭代後無法收斂到 playbook 標準，handoff 給你。

任務不是「再試一輪」— 是 **fundamentally re-evaluate 視覺製作策略**。Claude 試過的方向都列在「Rejected paths」段，不要重複。

---

## The 3-layer architecture（不要動）

修修對「人類做這件事很簡單」的判斷是對的，3-layer 是正確架構，**不要改**：

```
Layer 1 (AI):   背景 plate — 純環境，NO people, NO text
Layer 2 (real): 主人 cutout PNG — 真實照片去背
Layer 3 (CSS):  標題 + decoration — 字型 / 顏色 / 位置
```

各層獨立決策、組合成像。失敗的原版本是「叫 AI 一次生整張圖含人臉」 — gpt-image-1 `images.edit` 不保臉部 identity，產出不是修修。

---

## Playbook 在哪

| 用途 | 路徑 |
|------|------|
| **實際 reference 縮圖**（4 creators × ~140 張）| `E:/Thumbnail-example/{Alex Hormozi,Ali Abdaal,Cleo Abram,Jeff Su}/*.jpg` |
| Playbook 結構化 catalog（10 T-V × 10 T-A × 8 JP）| `E:/nakama/prompts/thumbnail/playbook_data_v1.json` |
| Playbook 人讀文件 | `E:/nakama/prompts/thumbnail/playbook_v1.md` |
| 設計研究背景 | `E:/nakama/docs/research/2026-05-26-thumbnail-playbook-design.md` |
| 社群 SOTA 研究 | `E:/nakama/docs/research/2026-05-27-thumbnail-pipeline-community-research.md` |

**動工前先打開 4-6 張 reference jpg 真的看過。** 不要只 grep description，要 visual ingest 才能 reproduce 那個質感。

---

## Pipeline 程式碼地圖

| 檔案 | 角色 |
|------|------|
| `shared/thumbnail_idea.py` | 解 5-line idea text → `ParsedIdea(hook, emotion, visual, decoration, bg, archetype_tags)` |
| `shared/cutout_library.py` | emotion alias resolver + cutout 路徑解析 |
| `agents/foundry/thumbnail_templates.py` | 5 locked templates registry：T-V1/V2/V3/V8/V10；`get_template()` + `build_prompt()` |
| `agents/foundry/render_workers/ai_image_gen.py` | gpt-image-1 wrapper（`images.generate`，**不是 edit**）；retries + cost tracking |
| `agents/foundry/render_workers/thumbnail_worker.py` | hyperframes (puppeteer + node) 呼叫器；`render_youtube_still()` |
| `video/compositions/thumbnail_youtube/index.html` | CSS composition；5 個 tv* archetype variants |
| `thousand_sunny/routers/bridge_project_thumbnails.py` | Bridge UI 整合；render endpoint 接 AI bg + hyperframes |
| `thousand_sunny/app.py` | Windows ProactorEventLoopPolicy + UTF-8 console |

---

## 哪裡卡關（具體列）

### 1. Cutout 品質

- **`E:/Shosho LifeOS/Attachments/cutouts/shosho/`** 在前一輪 batch crop 被弄壞 — `.png` 變成「保留背景的 RGBA」，alpha bbox 幾乎佔滿全幀，opaque>200 fraction 約 50%（正常 cutout 應該 ~20%）。
- **`E:/Shosho LifeOS/Attachments/cutouts_uncropped_backup/shosho/`** 是真正的 transparent cutout（opaque ~20%），但用 u2net 做的，邊緣會帶書架/桌面碎片。
- **長期解**：換 RMBG-2.0（briaai/RMBG-2.0 on HuggingFace，alpha matting cleaner edges）。
- **短期解**：把 backup 重新 tight-crop 到 alpha>16 bbox + 8px padding，**並且要 head-and-shoulders only**（取 alpha bbox 上 ~48-50% 而不是全 figure）。Claude 試的版本在 `.tmp/compose_creatine.py`，可以參考但 face scale 還是不夠 Hormozi。

### 2. AI 背景跟 Hormozi 質感差

- Claude 現在用 prompt 描述 "dramatic studio + warm amber rim glow + bokeh"，產出是「煙霧感的曖昧暗背景」，沒有 Hormozi 那種**單顆硬光打臉 + 邊緣全黑 + 高反差**的 studio drama。
- gpt-image-1 high quality（$0.167/張）已是 Claude 試過最強選項；不確定是 prompt 不對還是模型上限。
- **可試的替代**：
  - Imagen 4（Google Vertex AI，2026 應該已釋出）
  - FLUX.1 [pro] via fal.ai or Replicate
  - 不用 AI gen，直接用真實 studio bg 照片庫（Unsplash 篩選）+ darkening

### 3. Composition 字型 + stroke + 顏色

- Hormozi 字型像 Inter Black 或類似的 condensed sans，stroke 厚實，黃色精確 `#FFD340` 附近。
- Claude 用 Microsoft JhengHei Bold + 12px black stroke + `#FFD340` — 字型 weight 夠但**字體本身造型不夠 punchy**。
- Noto Sans TC Black 也試過，差不多。
- **可能方向**：找一個專門的繁中 display font（思源黑體 Heavy？或者 Adobe 思源宋體 Heavy？或 Source Han Sans HC Black）。或者乾脆用 SVG path manipulation 加更厚的 stroke + drop shadow。

### 4. 整體 production polish

- Hormozi reference 看起來像「設計師花 30 分鐘在 Photoshop 拉出來的」— hard shadows 在 cutout 邊緣、subtle color grade 統一光色、字跟人有合理的 spatial relationship。
- Claude 直接 PIL paste 沒有這些 polish。Hyperframes (CSS) 應該可以做出來但目前 index.html 還沒有這些細節。
- **建議**：要嘛把 hyperframes index.html 真的弄到位（per-archetype CSS shadow / color grade），要嘛改用一個專業 compositing 框架（Skia-python / Magick / ImageMagick）。

### 5. 5 templates 只驗了 T-V1

T-V2 (Face-Center)、T-V3 (Split-Screen)、T-V8 (Color Pop)、T-V10 (Number Hero) 都沒有 end-to-end 跑過。每個 archetype 的 CSS 在 `index.html` 都有，但 cutout positioning + AI bg prompt 都需要實測。

---

## Rejected paths（不要再試）

- ❌ **gpt-image-1 `images.edit` 餵 cutout 當 identity reference** — 不保臉。
- ❌ **AI 一次生整張含人臉** — 同上。
- ❌ **u2net 不重做就用** — 邊緣帶背景碎片，sharpness 不夠。
- ❌ **Chinese 描述顏色（"飽和黃色"）在 mixed prompt** — gpt-image-1 忽略，要 hardcode 英文+hex。
- ❌ **Claim "1:1 scale match" 不真做 side-by-side 比對** — 修修專門點名過。Codex 出手前**每一輪都要把產出跟至少 3 張 reference 放並排比，誠實寫出落差**，不要自評過寬。

---

## 怎麼跑 pipeline 本機

```bash
# 1. 確保 .env 有 OPENAI_API_KEY
# 2. 啟服務
cd E:/nakama
uvicorn thousand_sunny.app:app --port 8000 --loop asyncio
# 3. 瀏覽器: http://localhost:8000/bridge/projects/<project_id>/thumbnails
# 4. 寫 5-line idea → render
```

或單跑 composer（繞過 hyperframes）：
```bash
python .tmp/compose_creatine.py  # Claude 留的 PIL 版本
```

---

## 成本守則

- gpt-image-1 high = $0.167/image
- 每輪迭代開 5-10 張 candidate 就要 $1-2
- **不要在 polish loop 裡用 high**，先用 low ($0.011) 試 prompt direction，鎖定後再 high

---

## 第一步建議

1. 打開 `E:/Thumbnail-example/Alex Hormozi/How To Actually Get Rich In Your 20s.jpg` + `$100M CEO Explains How to Build A Brand in 2024.jpg` + Ali Abdaal `8 Simple Hacks to Improve Your Health.jpg` — 真的看。
2. 開 `E:/nakama/data/thumbnails/_creatine_v1/final_v2.png` 並排比。
3. 寫一份 **honest gap analysis**（不要客氣），列出 5-10 個具體視覺落差。
4. 從那 5-10 個 gap **挑 1-2 個最 impactful 的**先攻，不要 5 條線並行。
5. 收斂後再展開到 T-V2/V3/V8/V10。

修修不需要 5 個 90 分 templates，他需要 1 個 95 分 template 證明這條路走得通。

— Claude (handing off, 2026-05-28)
