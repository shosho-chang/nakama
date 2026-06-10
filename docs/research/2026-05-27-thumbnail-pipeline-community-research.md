# Thumbnail Pipeline — 2026 社群現況研究

**Date**: 2026-05-27
**Triggered by**: 修修 ADR-033 pipeline 渲出來品質遠低於 reference（見 `data/thumbnails/肌酸的妙用/runs/202605272232*/v*.png`）
**Goal**: 決定要 rebuild composition / 接 AI image gen API / 還是換整套 tool

---

## 1. AI image gen 模型分工（2026 共識：混搭三模）

| 模型 | 強項 | 適用 |
|------|------|------|
| **Ideogram v3** | 文字渲染冠軍（中英文可讀） | 整張一次出 |
| **Flux Kontext / Flux 2** | 寫實人臉、保留主體換背景 | 純背景 / cutout 進去改場景 |
| **Midjourney v7** | 電影級打光、藝術指導 | 無 API（不可程式化） |
| **Recraft** | 設計品味（grid / hierarchy） | 品牌一致 marketing |
| **Nano Banana (Gemini 2.5 Flash Image)** | conversational 多圖合成、character consistency、低延遲便宜 | **「保留我的臉 + 換背景 + 加道具」一段 prompt 搞定**（修修最相關）|

**典型 prompt**：subject + emotion + environment + style + lighting + `--ar 16:9`，<60 字。

無 thumbnail-specific 開源 fine-tune；Pikzels / Banana Thumbnail 是通用模型 + prompt-engineering layer + RAG。

## 2. Cutout-based hybrid 是主流

2026：純 AI 全生成人臉退潮（Social Blade 2025-12 survey 47.3% creator 棄用 — uncanny valley）。主流 = **真人 cutout + AI 背景 + 手刻文字**。

工具棧：
- Photoshop 2025 Generative Fill + Generate Background（內建 Firefly + Flux Kontext Pro + Nano Banana 三選）
- SaaS：Pikzels、Banana Thumbnail、1of10、ViewStats、ThumbnailTest
- API：**Photoroom / Bria — production-grade，可程式化，適合修修 backend pipeline**

## 3. Programmatic composition

- Puppeteer/Playwright + HTML/CSS：設計師背景開發者首選（修修目前路線 ✓）
- Pillow / Skia：backend-only，stroke 品質不如 CSS
- **修修目前 hyperframes（Puppeteer + HTML/CSS）路線 OK，問題在 CSS 本身不在工具**

## 4. Design reference 來源

1. **1of10.com** — outlier 偵測 + Chrome extension
2. **ViewStats** — MrBeast 系，trending + 競品追蹤
3. **ThumbnailTest.com** — design guide + A/B
4. **Dribbble**（MrBeast / Ali Abdaal tag）
5. **Jamie Whiffen 案例庫** — 實際幫 Ali Abdaal / Bryan Johnson 設計，**longevity 內容直接相關**
6. Twitter/X：`@parisrouzati`、`@JamieWhiffen`

## 5. Archetype taxonomy（社群常用 7+5 種）

- **Face + huge number**（MrBeast / Bryan Johnson）
- **Before/After split**
- **Versus / X vs Y**
- **Question hook**
- **Stat shock**
- **Reaction face**
- **Whiteboard / Framework**（Ali Abdaal 招牌）
- **Arrow / circle annotation**
- **Verified screenshot**（2026 新趨勢「verifiable」）
- **Phase ladder**
- **Object iso + glow**
- **Calm authority**（Ali Abdaal — low saturation, single accent）

**紀律**：top channel 用 3-5 個 locked template，只換變數。**修修該抄這個 → template registry**。

### MrBeast 文字公式

- 字型：Obelix Pro / Komika Axis / Burbank Big Condensed / Bangers — bold + condensed + 有個性
- **白字 → 黑 stroke 10-20px → 黑 hard drop shadow (80% opacity, 45°)**，no glow no gradient no bevel
- 文字常 arc/bulge warp
- 只在數字 / 金額用大字

### Ali Abdaal 公式（適合修修 longevity 頻道）

- 低飽和：muted green / warm cream / soft charcoal + 單一 accent
- 框架圖佔 2/3 frame，創作者頭像不是主角
- whiteboard / before-after / phase ladder 三種 template

## 6. AI 全生成 vs 半自動

**半自動全勝**。

| 路線 | Failure mode |
|------|--------------|
| 純 AI（Pikzels、Submagic） | uncanny face / 多手指 / 直出文字錯字 / "AI plastic look" → YouTube 2025-06 algorithm 懲罰 |
| **半自動 hybrid** | cutout 邊緣（→ RMBG-2.0 解決）/ 光影不匹配（→ Flux Kontext / Nano Banana 自動對齊）/ template 死板（→ archetype registry） |

Nano Banana Pro 2026 解決 character consistency — hybrid 路線的關鍵 enabler。

## 7. Quality benchmark

- CTR：<3% 待加強 / 4-5% 合格 / >7% 例外。Health/wellness niche 4-6%。
- YouTube 原生 A/B test 用 watch time 而非 CTR（避免 clickbait 漂移）
- 第三方：TubeBuddy / ThumbnailTest（154% 提升案例）/ vidIQ
- 2026 趨勢：「verifiable thumbnail」— mismatch 會被懲罰

## 8. Cutout 模型升級（u2net → ?）

**2026 SOTA 排序**：**RMBG-2.0 > BiRefNet > u2net**
- RMBG-2.0：Bria benchmark 90% vs BiRefNet 85%
- 基於 BiRefNet 架構 + 15,000+ 標註 dataset
- HuggingFace `briaai/RMBG-2.0`，本地 inference 可
- 頭髮、半透明、複雜邊界明顯勝 u2net

---

## 修修升級路徑（優先排序）

1. **Composition CSS 重寫**（無 cost，先做）
   - 字體：Obelix Pro / 源樣黑體 Heavy + `-webkit-text-stroke: 12px black` + 80% drop shadow
   - 標題 180-220px、cutout scale 1.3-1.5x
   - 預設 palette 切「calm authority」(muted green/cream + single accent)
   - 多 archetype layout (root data-archetype class switch)

2. **接 Nano Banana / Flux Kontext API 補 bg**（需 API key）
   - 輸入 `parsed.bg` 描述 + cutout → 對齊光影的背景
   - 不要 Midjourney（無 API）
   - 成本 ~$0.04/張

3. **建 archetype template registry**（4 個先做）
   - `framework`（Ali Abdaal 風，longevity 主力）
   - `face+number`（壽命 / 年齡 / 倍數）
   - `before/after`（生活方式對比）
   - `question`（教學）
   - 每個鎖 layout/color/font，只接內容變數

4. **去背換 RMBG-2.0**（半天工，邊緣 +1 tier）

5. **接 ThumbnailTest**（A/B test 自動化）

純 AI 一鍵（Pikzels）不適合 — backend pipeline 已成型，買 SaaS 等於放棄內容元數據自動化。

---

## 來源（按引用順序）

- https://www.cliprise.app/learn/guides/marketing/best-ai-for-youtube-thumbnails
- https://www.aimagicx.com/blog/midjourney-vs-flux-vs-ideogram-image-comparison-2026
- https://bfl.ai/models/flux-kontext
- https://developers.googleblog.com/en/introducing-gemini-2-5-flash-image/
- https://blog.fal.ai/introducing-gemini-2-5-flash-image-edit-aka-nano-banana/
- https://www.banana-prompts.net/40-ai-prompts-for-youtube-thumbnail-design-in-2026/
- https://www.photoshopessentials.com/photo-editing/using-the-new-generate-background-ai-in-photoshop/
- https://blog.adobe.com/en/publish/2025/09/25/photoshop-beta-expands-generative-fillmore-ai-models-more-possibilities
- https://blog.bananathumbnail.com/ai-youtube-thumbnails-2/
- https://strivingx.com/pikzels-review
- https://docs.photoroom.com/
- https://bria.ai/ai-image-editing
- https://1of10.com/
- https://www.viewstats.com/info
- https://github.com/jordicor/youtube_thumbnail_generator_with_AIs
- https://www.jamiewhiffen.co.uk/start-getting-views/how-i-designed-thumbnails-for-ali-abdaal-bryan-johnson-breakdown-inside
- https://touhfa.art/blog/designtips/mrbeast-thumbnail-article/
- https://touhfa.art/blog/thumbnails/mrbeast-thumbnail-font-guide/
- https://async.com/blog/mrbeast-thumbnails/
- https://blog.bananathumbnail.com/steal-this-how-mrbeast-mkbhd-and-top-creators-desi/
- https://miraflow.ai/blog/how-top-youtubers-design-thumbnails-7-patterns
- https://vidiq.com/blog/post/types-youtube-thumbnails/
- https://thumbnailtest.com/guides/learn-youtube-thumbnail-design/
- https://alici.ai/youtube-thumbnails/aliabdaal
- https://blog.laozhang.ai/en/posts/nano-banana-pro-face-consistency-guide
- https://narkis.ai/blog/why-general-purpose-ai-image-generators-fail-at-professional-headshots
- https://www.tubebuddy.com/tools/youtube-thumbnail-test
- https://thumbnailtest.com/
- https://www.thumbmagic.co/blog/ab-test-youtube-thumbnails
- https://www.analyticsvidhya.com/blog/2025/03/rmgb-v2-0/
- https://blog.bria.ai/benchmarking-blog/brias-new-state-of-the-art-remove-background-2.0-outperforms-the-competition
- https://huggingface.co/briaai/RMBG-2.0
- https://dev.to/om_prakash_3311f8a4576605/birefnet-vs-rembg-vs-u2net-which-background-removal-model-actually-works-in-production-4830
