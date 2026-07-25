# foundry editorial rubric

Single source for broll-pipeline editorial taste. Loaded by `planner.py` into every prompt. Grows over time as `edit_log/*.jsonl` entries are reviewed and promoted into examples; *固化*的規則沉澱回這份。

Not a brand document — brand tokens live in `docs/design-system.md` (canonical). Not a behavioral feedback document — that's `memory/claude/feedback_*.md`. This file is **pipeline-specific editorial judgment**.

---

## Hard rules（必遵守）

- **B-roll service 的是 idea，不是 sentence**：一個 beat 是一個 idea unit，可跨多句 SRT。配合 §6 LLM 自然分群。
- **Restraint budget**：10 分鐘影片預期 ~25-35 個 cutaway 事件（成片實測 3.26-3.52 事件/分，見 `docs/research/editing-grammar/2026-07-18-shoshotw-editing-grammar.md` §一；一事件可展開多鏡快切）。連續兩 beat 不用同一個 component（避免視覺重複；同 kind 不同 footage 的快切連發合法，判定粒度為 source_url）。
- **Anti-literal**：B-roll 添加資訊或情緒，不要畫名詞（「成長」→ 上升箭頭是 lazy slop）。
- **Anti-hype**：拒用 money-count 巨大數字效果（除 BigStat fork）、neon caption、glitch RGB、TikTok/IG follow overlay。Brook editorial tone 是 anti-hype, engineering-grounded（見 `docs/design-system.md`）。

## Soft rules（默認，可破除但要寫進 edit_log）

- **數字 > 1000**：傾向用 BigStat（除非該 beat 已有其他視覺強調）
- **長定義 / 列舉句**：傾向 `side_overlay_left` + caption-* component
- **抽象概念**（如「身分認同」「動機」）：留 talking head，**不要強塞 B-roll**
- **章節切換**：用 `TransitionTitle` （Phase 1.5 加 component）

## Phase 1 vocabulary（縮限）

- **layouts available**：`full_aroll` / `full_broll` 兩種
  - Phase 1.5 開放：side_overlay_left/right、pip_corner_br
- **render_target available**：僅 `hyperframes`
  - Phase 1.5 開放：reader-playwright、web-playwright
- **components available**：僅 `bigstat`
  - Phase 1.5 / Phase 2 開放：TransitionTitle、DataChart、Map、Caption 各式

Planner prompt 必須 enforce 這個 vocabulary subset（產出 layout / component / render_target 不在此清單時 hard-fail）。

---

## Video visual grammar（ADR-051 panel v2 §5；PR-C 前置，2026-07-05）

七種 B-roll 來自四種視覺來源（Hyperframes 品牌動畫 / stock 實拍 / KOL 截取 /
螢幕錄影）— 沒有統一規則會剪成拼裝怪。以下對 compositions 與 Director 皆有效：

### 版面

- **字幕禁飛區**：常駐繁中字幕佔畫面底部 ~200px（1080p）。所有 composition 的
  關鍵內容（文字、highlight、數字）不得進入此區；`.stage` 一律 `bottom: 200px`。
- **章節卡時長**：`duration = max(該句語音時長, 最低可讀時間)`；最低可讀時間
  ≈ 1.5s + 標題每字 0.12s。標題 >9 字自動降字級（168 → 128px）。

### Hyperframes compositions

- tokens 同 `docs/design-system.md`（暖灰底 oklch(0.988 0.003 80)、PANTONE 165
  accent、LINE Seed TW）；**不硬編其他色碼字型**。
- 動效節奏：進場 ≤0.65s、power2/3/4.out 家族；accent 元素（bar / tick /
  underline）wipe-in 0.35–0.45s；章節卡結尾**硬切**不 fade（頻道既有節奏）。
- **中英混排（quote_card / book_cover）**：出處/原文字級 = 中文本文的 ~60–80%、
  mute 色、Rg/Bd 字重；中文 Eb 大字當視覺主角。
- **variables 資料衛生**：選填欄位宣告 `default: ""` — demo 預設值會滲進正式
  render（planner 漏給 source 時畫面出現錯的書名，2026-07-05 實測）。空值時
  對應元素整個隱藏，不留孤懸裝飾（dash、書名號）。

### 外部素材（asset 類）

- **stock**：優先選色溫偏暖、非 corporate 假笑的素材；同一集內 stock 調性一致
  （都實拍或都動畫，不混）。
- **KOL 截取**：一律 `full_broll` 滿版（不縮放不加框 — 縮放需 transform，
  Phase 1.5 blocked）；單一來源總長 ≤20s；出處必記（`asset.attribution`）。
- **幀率**：一律 conform 到 30fps 再進 timeline（manifest 驗收步驟 ffprobe 檢
  查；非 30fps 用 ffmpeg 轉）— 混幀率在 DaVinci 會 judder。
- **螢幕錄影（修修外供）**：錄製解析度 ≥1080p，系統 UI 語言與內容一致。

## Long-form 編輯哲學（隨 edit_log 累積長大）

（此節由 edit_log 提煉的固化教訓填入。Phase 1 留空作 placeholder。）
