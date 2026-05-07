# Nakama Design System

> 所有 Nakama UI surface 的 source of truth：Bridge、Thousand Sunny 甲板儀表板、Chopper 社群、
> Brook 對外 template、任何 landing / marketing。
>
> **Workflow：** Claude Design（視覺探索 + iteration）→ 匯出 → 貼進這份 → Claude Code 照這份落地。
>
> 美學是 first-class requirement，不是 nice-to-have。見 [feedback_aesthetic_first_class.md](../memory/claude/feedback_aesthetic_first_class.md)。

---

## Status

- **版本**：v1（首批 tokens 來自 `/projects/{slug}` review-mode 落地，issue #458）
- **最近更新**：2026-05-07
- **Tokens canonical 來源**：`thousand_sunny/static/projects/tokens.css`（CSS custom properties）

---

## Aesthetic Direction

Two-pane editorial review surface — paper-and-ink restraint, type-driven density.
Body baseline 是 17px **Source Serif Pro / Noto Serif TC** 給長段中英文閱讀，UI label
與 metadata 用 **Manrope** 的暖幾何 sans，slug / id / count 走 **JetBrains Mono** 的
tabular-nums。整套色板只有一個 chromatic accent —— 一抹 deep restrained terracotta
（`#9d4a2c`）—— 拒絕 orange-bright、拒絕 blood-red，只在 active section、focus ring、
destructive confirm 時亮相。Light = 暖紙質 `#f6f1e7`（lower-blue than pure cream）；
Dark = warm near-black `#1a1611`（不是 `#000`，致敬舊圖書館的 felt）。圓角不超過 4px，
hairline rule 是 `1px solid var(--brk-rule)`，沒有彩色漸層、沒有陰影。

### One memorable element

**Two-step terracotta focus ring + serif italic numerals as structural elements。**
焦點環是 `0 0 0 2px var(--brk-bg), 0 0 0 4px var(--brk-mark)` —— 內層襯紙底，
外層 terracotta，像是書頁邊上的標記。Outline section 編號用 serif italic（I / II / III…），
是 structural decoration，不是普通的 badge。

---

## Typography

### Display / Body serif

- family: `Source Serif Pro`, `Noto Serif TC`, `Source Han Serif TC`, `Songti TC`, Georgia, serif
- 用於：page topic（H1）、evidence card title、excerpt blockquote、empty/error 文案
- weights: 400, 500（display variants 用 italic 強調 numerals）

### zh-Hant body

- family: `Noto Serif TC`, `Source Han Serif TC`, `PingFang TC`, `Songti TC`, serif
- 用於：outline section heading、evidence heading-within-paper、長段中文 body
- 中文 line-height 嚴格 ≥ 1.6（`--brk-lh-read: 1.7`）

### UI sans

- family: `Manrope`, `Noto Sans TC`, `PingFang TC`, -apple-system, sans-serif
- **不是 Inter / Roboto** — Manrope 是 Söhne calm feel 的 open match
- weights: 400, 500, 600

### Mono

- family: `JetBrains Mono`, `IBM Plex Mono`, ui-monospace, monospace
- `font-variant-numeric: tabular-nums`
- 用於：slug / id / count / timestamp / caps-style labels

### Scale（CSS variables — see `tokens.css`）

| Variable | Size | Line-height | 用途 |
|----------|------|-------------|------|
| `--brk-t-micro` | 11px | 1.4 | mono caps label（`paper://…` / 大綱· outline 等） |
| `--brk-t-xs` | 12px | 1.4 | secondary metadata |
| `--brk-t-sm` | 14px | `--brk-lh-base` (1.5) | sans body |
| `--brk-t-base` | 15px | 1.5 | UI default |
| `--brk-t-read` | 17px | `--brk-lh-read` (1.7) | serif evidence body |
| `--brk-t-read-2` | 19px | 1.3 | evidence card title |
| `--brk-t-md` | 20px | 1.4 | outline gist |
| `--brk-t-lg` | 24px | 1.25 | outline section heading |
| `--brk-t-xl` | 32px | `--brk-lh-snug` (1.35) | page topic / sticky section heading |
| `--brk-t-2xl` | 44px | 1.18 | hero number, rare |

Tracking: `--brk-track-caps: 0.16em`（mono caps labels）；`--brk-track-tight: -0.012em`（display）。

---

## Color Tokens

> 一個 dominant warm-paper neutral + 一個 restrained terracotta accent。
> 完整定義在 `thousand_sunny/static/projects/tokens.css`，下表是摘要。

### Light（`.brk-light`，default）

| Token | Value | 用途 |
|-------|-------|------|
| `--brk-bg` | `#f6f1e7` | page bg — warm paper |
| `--brk-bg-2` | `#efe9dc` | recessed: outline rail, gutters |
| `--brk-panel` | `#fbf8f1` | evidence card face |
| `--brk-panel-2` | `#f1ecdf` | hovered card |
| `--brk-rule` | `#d9d1bf` | 1px hairline |
| `--brk-rule-2` | `#e8e1cd` | dotted divider |
| `--brk-ink` | `#1c1812` | body text — 16.4:1 AAA |
| `--brk-ink-2` | `#4a4337` | secondary — 8.2:1 AA |
| `--brk-ink-3` | `#6e6553` | tertiary, captions |
| `--brk-muted` | `#9c9078` | metadata, tick units |
| `--brk-mark` | `#9d4a2c` | the only chromatic accent |
| `--brk-mark-soft` | `rgba(157,74,44,0.08)` | active section bg |
| `--brk-mark-2` | `#7d3a23` | hover/pressed |
| `--brk-rule-active` | `#9d4a2c` | margin rule for active outline |
| `--brk-mark-bg` | `rgba(157,74,44,0.14)` | excerpt highlight `<mark>` |
| `--brk-warn` | `#8a6d20` | citation-incomplete amber |
| `--brk-danger` | `#8a2a1c` | destructive confirm |
| `--brk-good` | `#3a5d3e` | finalized |

### Dark（`.brk-dark`）

| Token | Value | 對 light 的關係 |
|-------|-------|----------------|
| `--brk-bg` | `#1a1611` | warm near-black (never `#000`) |
| `--brk-bg-2` | `#221d16` | recessed |
| `--brk-panel` | `#1f1a14` | card face |
| `--brk-panel-2` | `#2a241b` | hover |
| `--brk-ink` | `#ece4d2` | cream — 14.6:1 AAA |
| `--brk-ink-2` | `#b8af9b` | 7.8:1 AA |
| `--brk-mark` | `#d97a52` | terracotta brightened for dark |
| `--brk-danger` | `#d96a4f` | destructive |
| `--brk-good` | `#7ca97f` | finalized |

### Contrast 要求（已驗證）

- body text on bg：≥ 16.4:1（AAA），dark mode 14.6:1（AAA）
- secondary on bg：≥ 8.2:1（AA），dark 7.8:1（AA）
- accent (mark) on bg：≥ 4.5:1，足夠承擔 active state + focus ring

---

## Spacing

- base unit: 4px
- scale: `--brk-s-1` … `--brk-s-11` = `4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 56 / 72 / 96` px
- 實作：CSS custom properties（不寫 Tailwind preset），class 直接消費

---

## Radius

- `--brk-r-1: 1px`、`--brk-r-2: 2px`、`--brk-r-3: 4px`
- **沒有任何元件圓角超過 4px。** 這不是「現代 SaaS 大圓角」風格 —— 是 editorial。

---

## Motion

- `--brk-dur-fast: 90ms`（hover、outline item bg swap）
- `--brk-dur-base: 160ms`（card bg、button border）
- `--brk-dur-slow: 280ms`（dialog open）
- `--brk-ease: cubic-bezier(0.2, 0.7, 0.2, 1)`（default — calm out-curve）
- `--brk-ease-in: cubic-bezier(0.4, 0, 1, 1)`（exit / dismiss）
- `prefers-reduced-motion: reduce` → tokens.css 將三個 dur 全降為 0ms（已驗證）

---

## Layout

- 主 surface：`grid-template-columns: 440px 1fr`（outline + evidence）
- container max-width：evidence body 限制 920px，outline panel 固定 440px
- **不是** 1200px centered column —— 兩 pane 各自滾動、各自 sticky header
- 邊距：page padding `20px 40px 18px`，evidence body `28px 56px 80px`

---

## Component Patterns

### Button

四型 + reject 變體：

| 型 | class | 用途 |
|----|-------|------|
| Primary | `.btn.btn-primary` | finalize CTA、empty-state primary action |
| Ghost | `.btn.btn-ghost` | 暫存草稿、cancel |
| Danger | `.btn.btn-danger` | 確認下架 |
| Disabled | `.btn[disabled]` / `.btn-disabled` | 已 finalized |
| Reject | `.reject-btn` / `.reject-btn--global` | 從段落 / 整條下架 |

字眼禁用：「Get Started」「Learn More」 —— 全用具體動詞（「定稿這份綜合 · finalize」、
「整條不要」）。每個型實作 default / hover / focus（two-step ring）/ active / disabled，
focus ring 走 `var(--brk-focus)`。

### Outline item（list nav）

- structural numeral：serif italic，active 時轉 terracotta
- 3px 左 border 標記 active；hover 換 panel-2 bg
- keyboard：tab + ArrowUp/ArrowDown 切換 + 自動觸發 selection

### Evidence card

- 一張 `<article>` 一條證據；header（meta + relevance）/ blockquote（excerpt with `<mark>` highlight）/ footer（pulled reason + reject buttons）
- 沒有 `grid-cols-3` —— 證據卡是 vertical stack，每張卡 max-width 920px（evidence body width）
- 真實 chunks 沒有 authors / journal / year，這些欄位於 production gracefully omit（不偽造）

### Dialog（reject confirm）

- 用原生 `<dialog>` element + `showModal()` —— 自帶 focus trap、ESC dismiss、backdrop
- backdrop blur 2px + `rgba(28,24,18,0.5)` 的 felt overlay
- header（kind caps）/ body（evidence meta + 「會發生什麼事」effects list）/ foot（cancel + danger primary）

---

## States Checklist（每個 component 都要處理）

| State | 處理 |
|-------|------|
| **default** | server-rendered，所有 token 預設 light theme |
| **loading** | skeleton blocks（`<div>` rectangles 用 `--brk-rule-2` bg + 1.6s ease pulse）；reduced-motion 時退化為靜態灰塊 |
| **empty** | serif italic 28px 標題 + serif 15px body + primary/ghost CTA 雙鈕 |
| **error** | mono caps 紅標題（`--brk-danger`）+ serif 24px 主訊息 + mono 12px 錯誤代號 + retry CTA |
| **hover** | card → `panel-2` bg；outline item → `panel-2` bg；button → border-color shift（160ms） |
| **focus** | `box-shadow: var(--brk-focus)` 兩段環，所有 interactive 元件統一 |
| **active** | outline item: 3px terracotta border-left + `mark-soft` bg；button:pressed → `mark-2` |
| **disabled** | opacity 0.55、`cursor: not-allowed`、`aria-disabled="true"` |
| **finalized** | top bar caption 轉 `--brk-good` + 「✓ locked at HH:MM」；CTA disabled |

---

## AI Slop 禁用清單

未來的 `design-quality.js` hook 會偵測以下 pattern 並警告（**警告不阻擋**，保留迭代速度）：

| Pattern | 為什麼禁 |
|---------|----------|
| Inter / Roboto / Arial 字體（except invisible typography 場景） | AI default 最明顯訊號；本 system 用 Manrope + Source Serif Pro |
| `bg-gradient-to-*` with purple | 最被濫用的 AI 配色；本 system 是 paper + 單 terracotta |
| `grid-cols-3` / `grid-cols-4` 均勻 card grid | 本 system 是 vertical stack of `<article>` |
| 「Get Started」/「Learn More」CTA | 沒意圖、懶惰文案 |
| 寫死在 class 裡的色碼 / 字型，不在 tokens 中 | 繞過 design system；本 system 全 tokens 化 |
| 所有東西置中在 1200px column | 本 system 是 440 + 1fr 兩 pane |
| 圓角 ≥ 8px | 本 system 上限 4px（editorial restraint） |

---

## 使用流程

1. **新 UI 出手前**：讀這份文件（Aesthetic Direction + 相關 component）
2. **有新視覺需求**：在 Claude Design（claude.ai/design）迭代 → 匯出 → 更新這份
3. **實作時**：
   - tokens 寫進 CSS custom properties（範本見 `thousand_sunny/static/projects/tokens.css`）
   - **不要** 硬寫 `#9d4a2c` / `font-family: Manrope` 在 class 裡
   - 每個 state 都實作，不是 afterthought
4. **完工時**：`[P7-COMPLETION]` 的 Aesthetic direction 段要說明在系統內做了什麼選擇、為什麼

---

## 適用範圍

### ✅ 走這份 design system

- Bridge UI（`bridge/templates/`、`bridge/static/`）
- Thousand Sunny `/projects/{slug}` review mode（`thousand_sunny/templates/projects/`、`thousand_sunny/static/projects/`） —— **首批落地** ✓
- Thousand Sunny 甲板儀表板（規劃中）
- Chopper 社群 UI（規劃中）
- Brook 對外 template（landing、PPTX 簡報、部落格 post template）
- 任何未來的 marketing / landing page

### ❌ 不走這份（用其他機制）

- Obsidian vault 內頁呈現 — 用 CSS snippet 獨立處理
- Agent 的 markdown 輸出本身 — 內容不算 UI，content guideline 歸各 agent prompt
- Slack 訊息格式 — 走 Slack block kit，不是 web design

---

## Handoff 自 Claude Design

從 Claude Design 匯出「交付套件」後：

1. HTML reference → 解析後的 source-of-truth 落到 `thousand_sunny/static/<surface>/tokens.css`（不是 `docs/design-ref/`）
2. Design tokens（色、字、spacing） → 同時更新本文件 + tokens.css
3. Component patterns → 更新本文件的 Component Patterns 章節
4. 一頁 changelog 寫在下方 Revision Log

---

## Revision Log

| 日期 | 版本 | 變更 | 來源 |
|------|------|------|------|
| 2026-04-20 | v0 | 骨架建立，待 Claude Design 首輪迭代 | 手動 |
| 2026-05-07 | v1 | 從 `/projects/{slug}` review-mode 落地批量填入：Aesthetic direction、typography、colour tokens（light + dark）、spacing、radius、motion、layout、component patterns、states checklist。Tokens canonical 在 `thousand_sunny/static/projects/tokens.css`。 | Claude Design handoff `N458-brook-review-mode/project/brook/` → issue #458 落地 |
