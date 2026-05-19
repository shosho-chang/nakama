# Nakama Design System

> 所有 Nakama UI surface 的 source of truth：Bridge、Thousand Sunny 甲板儀表板、Chopper 社群、
> Brook 對外 template、任何 landing / marketing。
>
> **Workflow：** Claude Design（視覺探索 + iteration）→ 匯出 → 貼進這份 → Claude Code 照這份落地。
>
> 美學是 first-class requirement，不是 nice-to-have。見 [feedback_aesthetic_first_class.md](../memory/claude/feedback_aesthetic_first_class.md)。

---

## Status

- **版本**：v2 — 三個 token namespace 並存
- **最近更新**：2026-05-19
- **Token namespaces**：
  | Namespace | Scope | Aesthetic | Canonical source |
  |-----------|-------|-----------|------------------|
  | `--brk-*` | Brook editorial review（`/projects/{slug}`、未來的 Reader、書評輸出）| Paper-and-ink, terracotta accent, Source Serif Pro + Manrope, ≤4px radius | `thousand_sunny/static/projects/tokens.css` |
  | `--sho-*` | Shosho web identity（`/progress`、未來 `nakama.shosho.tw` root marketing landing、partner-facing 頁）| Modern engineering ops, PANTONE 165 PC orange `#e98965`, Geist + Noto Sans TC, oklch palette, switchable light/dark | `thousand_sunny/static/shosho/tokens.css` |
  | `--nk-*` | Bridge 中控台（`thousand_sunny/templates/bridge/*.html`）| Workshop bench × dark chassis, signal orange `#ff5a1f`, Space Grotesk + JetBrains Mono, dense ops UI | inline `<style>` 在各 bridge template 頂部（尚未抽出 tokens.css）|

> 三套 token 都是 first-class — **不是**「`--sho-*` 是新版要取代 `--brk-*` / `--nk-*`」。每套服務不同 surface 類型：
> - **Brook** = 內部編輯室、長段閱讀、慢思考、editorial calm
> - **Shosho** = 對外品牌、partner-facing、marketing、modern web identity
> - **Bridge** = 內部 ops 中控台、information dense、workshop floor
>
> Opt-in 機制：surface root element 加 `.brk-light` / `.brk-dark` / `.sho` / Bridge 是 implicit（每個 template 自己 inline）。
>
> **`--nk-*` Bridge tokens 還沒抽到 tokens.css 檔**，目前散落在 20+ 個 template 的 `<style>` 區。屬技術債，待 Phase 3 整理；不是 Phase 2 範圍。

---

> **The sections from here through "Revision Log v1" describe Brook (`--brk-*`) tokens.** Any rule below that reads "system-wide" (e.g. "圓角不超過 4px", "唯一 chromatic accent") applies to Brook surfaces only; Shosho has its own AI Slop list and constraints in the Shosho chapter further down.

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

---

> **Sections above describe Brook (`--brk-*`) — editorial review surface tokens. Sections below describe Shosho (`--sho-*`) — web identity tokens for outward-facing surfaces. Two parallel systems; surfaces opt in via root class.**
>
> Shosho CI 對外 surface — `/progress`、未來 `nakama.shosho.tw` root marketing landing、任何 partner-facing 頁。來自 Claude Design handoff bundle `shosho-website-new-design`（2026-05-19）。Bridge 中控台**不**在 Shosho 範圍內，走自己的 `--nk-*` namespace。

---

## Aesthetic Direction (Shosho)

Engineering ops density translated from paper-and-ink editorial. PANTONE 165 PC 橘 `#e98965`（≈ `oklch(0.71 0.135 41)`）是唯一 chromatic accent，全頁佔比 ≤ 4%。Neutral scale 走暖灰 oklch hue 80°，light 是接近紙白 `oklch(0.988 0.003 80)`、dark 是 warm near-black `oklch(0.145 0.005 80)`（不是 `#000`）。Type stack: Geist + Noto Sans TC + Geist Mono，全 latin 用 Geist 的暖幾何，中文走 Noto Sans TC，數字 / id / count 走 Geist Mono 的 tabular。圓角 4 / 8 / 12px 三階 + `--sho-r-pill: 999px` 給 switch / chip。

### One memorable element

**閃電 bolt 作為 99/1 stamp + decorative wordmark 作 signage。** Bolt 是 viewBox `0 0 24 14` 的 polyline zigzag，每頁最多 1×、通常在 footer 「研究編號 N°XXXX」前作為 watermark；header logo 內含其字面化版本。Bolt rotated -25° 表示「進入」、靜置時為 logo 一部分；hover logo 會觸發 320ms 的 `sho-bolt-jiggle` keyframe。

### 五個翻譯策略（Claude Design strategy.jsx）

1. **Orange = line / dot / char**，不是 block fill — 橘色只出現在 1px border、focus ring、單字、active text，從不大面積填色
2. **Bolt = stamp**，不是 wallpaper — 99/1 規則，整個頁面看到最多一次
3. **Decorative wordmark = signage**，不是 chrome — logo 是品牌印記，不每頁佔位
4. **Engineer density**，不是 YouTuber whitespace — Bridge / 進度頁 走 information-dense layout
5. **Motion ≤ 200ms** — `--sho-t-fast: 120ms` (hover) / `--sho-t-base: 180ms` (state change) / `--sho-t-slow: 280ms` (dialog only)

---

## Typography (Shosho)

### Sans (latin + zh-Hant default)

- family: `Geist`, `Noto Sans TC`, `PingFang TC`, ui-sans-serif, system-ui, sans-serif
- 用於：body、H1-H4、UI label
- weights: 300 / 400 / 500 / 600 / 700
- 載入：`@import` Google Fonts 在 tokens.css 頂部（CSP-safe）

### zh-Hant body fallback chain

- family: `Noto Sans TC`, `PingFang TC`, ui-sans-serif, system-ui, sans-serif
- 中文 line-height：建議 ≥ 1.6（沒有 token，per component 設定）

### Mono

- family: `Geist Mono`, `JetBrains Mono`, ui-monospace, SFMono-Regular, monospace
- `font-variant-numeric: tabular-nums`
- 用於：研究編號 / id / slug / "indoor voice" metadata

### Scale（per-component，無 numeric tokens — 是設計選擇）

Shosho 用 typography scale 但**不**像 Brook 那樣把每個 size 包成 `--sho-t-*` token。原因：Shosho 的 size 多半貼合 component（footer mono 11.5px、kicker 11px、tag 11.5px、body 不固定），抽 token 會讓每個 component 自找對應，比直接寫 `font-size: 11.5px` 更難讀。**只有** mono carrier `.sho-mono { font-size: 12.5px }` 有預設值。

### Common classes

| Class | 用途 |
|-------|------|
| `.sho-mono` | "indoor voice" — 12.5px mono, muted color, tabular-nums |
| `.sho-mono-up` | mono + uppercase + 0.06em tracking, weight 500 |
| `.sho-lk` | inline link — currentColor + 1px underline → orange + orange underline on hover |
| `.sho-kicker` | 「[ 001 ] section name」structural label |
| `.sho-tag` / `.sho-tag--accent` | bordered pill chip, neutral or accent variant |

---

## Color Tokens (Shosho)

> oklch 色彩空間，hue 鎖 80° (neutral warm) + 41° / 48° (accent orange)。完整定義在 `thousand_sunny/static/shosho/tokens.css`。

### Light（`.sho` default）

| Token | Value | 用途 |
|-------|-------|------|
| `--sho-bg` | `oklch(0.988 0.003 80)` | page bg — near-paper white |
| `--sho-bg-2` | `oklch(0.967 0.003 80)` | recessed surface |
| `--sho-bg-3` | `oklch(0.925 0.003 80)` | Cool Gray 1 PC equivalent — brand neutral |
| `--sho-line` | `oklch(0.875 0.003 80)` | 1px hairline |
| `--sho-line-2` | `oklch(0.820 0.003 80)` | stronger divider, tag border |
| `--sho-muted` | `oklch(0.550 0.005 80)` | metadata, mono carrier |
| `--sho-text-2` | `oklch(0.360 0.005 80)` | secondary text |
| `--sho-text` | `oklch(0.200 0.005 80)` | body text |
| `--sho-ink` | `oklch(0.100 0.005 80)` | strongest headline |
| `--sho-accent` | `oklch(0.71 0.135 41)` | the only chromatic accent (PANTONE 165 PC ≈ `#e98965`) |
| `--sho-accent-2` | `oklch(0.66 0.155 38)` | hover / pressed accent |
| `--sho-accent-soft` | `oklch(0.71 0.135 41 / 0.10)` | active-state bg fill |
| `--sho-accent-line` | `oklch(0.71 0.135 41 / 0.45)` | accent border |

### Semantic（共用 light / dark）

| Token | Value | 用途 |
|-------|-------|------|
| `--sho-success` | `oklch(0.58 0.115 150)` | finalized / pass |
| `--sho-warning` | `oklch(0.72 0.140 75)` | citation-incomplete / amber |
| `--sho-error` | `oklch(0.58 0.180 28)` | destructive confirm |
| `--sho-info` | `oklch(0.62 0.090 235)` | informational |

### Dark（auto via `prefers-color-scheme` OR explicit `.sho[data-theme="dark"]`）

Brightness flip + accent shift toward `oklch(0.78 0.130 48)`（從 41° hue 推到 48° 補 warm dark mode 偏冷的視感）。詳見 tokens.css line 75-110。

### Theme switching

Surface 可選三模式：
- **預設**：跟系統（`prefers-color-scheme`）
- **顯式 light**：`<body class="sho" data-theme="light">`
- **顯式 dark**：`<body class="sho" data-theme="dark">`

`/progress` 提供 binary toggle UI（見 Component Patterns · Theme switch），用 localStorage 持久化 `sho-theme: light | dark`。

### Contrast 要求

- body text on bg：oklch L=0.20 on L=0.988 ≈ 16:1（AAA）
- secondary on bg：oklch L=0.36 on L=0.988 ≈ 8:1（AA）
- accent on bg：oklch L=0.71 chroma 0.135 on L=0.988 ≈ 3.2:1 — **僅用於 border / focus / 單字，不承擔 body text**

---

## Spacing & Radius (Shosho)

### Spacing scale（4px grid）

`--sho-s-1` … `--sho-s-10` = `4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96 / 128` px。注意 5 → 24（跳 20），與 Brook 的 11-step 不同；Shosho 更鬆。

### Radius

| Token | Value | 用途 |
|-------|-------|------|
| `--sho-r-1` | 4px | tag, kicker number |
| `--sho-r-2` | 8px | card, button |
| `--sho-r-3` | 12px | dialog, large panel |
| `--sho-r-pill` | 999px | switch track, chip variant |

**Shosho 允許 8 / 12px radius**（不像 Brook 上限 4px）— editorial restraint 不適用 web identity。

---

## Motion (Shosho)

- `--sho-t-fast: 120ms`（hover）
- `--sho-t-base: 180ms`（theme switch thumb slide、state change）
- `--sho-t-slow: 280ms`（dialog open，目前無實作）
- `--sho-ease: cubic-bezier(0.22, 1, 0.36, 1)`（calm out-curve — 同 Brook）
- `prefers-reduced-motion: reduce` → 三個 dur 全降為 0ms（已實作 tokens.css line 214-217）

---

## Focus ring (Shosho)

```css
--sho-focus: 0 0 0 2px var(--sho-bg), 0 0 0 4px var(--sho-accent);
```

Two-step ring — 內 2px page-bg、外 2px accent orange，沿用 Brook 結構但換 color。任何 interactive 元件 focus-visible 時統一套：

```css
.sho-foo:focus-visible { outline: none; box-shadow: var(--sho-focus); }
```

---

## Component Patterns (Shosho)

### Logo mark

- `.sho-logo-mark` — 含品牌 PNG（`/static/shosho/brand/logo_1.png`）+ 暖灰底時不變，dark mode 套 `filter: var(--sho-logo-filter)` = `invert(1) brightness(1.1)`
- hover 觸發 `sho-bolt-jiggle` 320ms keyframe，bolt 上下抖 1px ± rotate 3°

### Bolt mark — 99/1 stamp

- SVG symbol `<symbol id="sho-bolt-shape" viewBox="0 0 24 14">` 一次定義在 page top，後續 `<use href="#sho-bolt-shape"/>` 引用
- 預設大小：`.sho-bolt svg { width: 1em; height: 1em }`
- **每頁最多 1×**（footer 99/1 stamp、或 hero watermark 二選一）

### Theme switch（binary）

- 結構：`<button class="theme-switch" role="switch" aria-checked="...">` containing `.theme-switch-track > [.theme-switch-label-light, .theme-switch-label-dark, .theme-switch-thumb]`
- Geometry：track 44×22px、thumb 16px、padding 3px、L/D label 各 left/right 5px
- Vertical centering：thumb 用 `top: 50%; transform: translate(0, -50%)`（避開 absolute-pos relative to padding-box 的 border 偏移）
- Horizontal slide：dark 模式 override 為 `transform: translate(var(--thumb-travel), -50%)`，`--thumb-travel = switch-w - thumb-d - pad*2 - 2px`（最後 -2px 扣 track border-box 兩側 1px border）
- 持久化：`localStorage.setItem('sho-theme', mode)` + matchMedia fallback；try/catch 包 storage 存取以防 Safari private mode
- 初始 paint：HTML inline `<script>` 跑在 render 前 set `body.dataset.theme`，避免 flash of wrong theme

### Tag / chip

- `.sho-tag` 預設 neutral：transparent bg + `--sho-line-2` border + mono 11.5px
- `.sho-tag--accent` 變 accent：color + border 換 `--sho-accent` / `--sho-accent-line`
- 用於 status pill、line / channel / agent label

### Kicker（structural label）

- `[ 001 ] SECTION NAME` 風格
- `.sho-kicker` 包 `.sho-kicker-n`（編號）+ uppercase mono text
- 編號 box：1px line-2 border + 4px radius + mono 10.5px

### Mono carrier

- `.sho-mono` — 12.5px mono、muted color、tabular-nums，承載 metadata / timestamp / id
- 加 `.sho-mono-up` 變 caps style（uppercase + 0.06em tracking）

### Hairline rule

- `.sho-rule` — `height: 1px; background: var(--sho-line); border: 0` — 在 sections 間做 divider

### Inline link

- `.sho-lk` — currentColor + 1px underline （`background-image: linear-gradient(currentColor, currentColor)` 1px @ bottom）
- hover → 同時換 color 跟 underline 到 `--sho-accent`
- focus-visible → `box-shadow: var(--sho-focus)`

---

## Asset versioning（Shosho-specific infra）

外部 surface 走 Cloudflare Tunnel，static assets 在 CF 邊緣有 4h cache。因此：

- **`/progress` 採用 router-rendered HTMLResponse**（不是 FileResponse），boot 時 sha1 hash `(progress.css + tokens.css + logo_1.png)` 取 8 字元當 version slug，注入 HTML 裡 `?v=<slug>` query
- 任一 asset 改動 → service restart → 新 hash → 新 URL → CF cache miss → fresh fetch
- 這個 pattern 之後其他 sho surface 出現後可抽成共用 helper（目前單一實例 inline 在 `thousand_sunny/routers/progress.py`）

詳見 PR #608 / `feedback_cloudflare_cdn_cache.md`（pending memory write）。

---

## Reduced motion (Shosho)

```css
@media (prefers-reduced-motion: reduce) {
  .sho { --sho-t-fast: 0ms; --sho-t-base: 0ms; --sho-t-slow: 0ms; }
  .sho .sho-bolt, .sho .sho-logo-mark:hover .sho-bolt { animation: none; }
}
```

已實作 tokens.css。任何新 keyframe animation **必須** 在這個 media query 內被 disable。

---

## AI Slop 禁用清單 — Shosho 補充

Shosho 不繼承 Brook AI Slop 規則 — 圓角 ≥ 8px、`grid-cols-3/4`、置中 1200px column 那些是 Brook editorial 特定，Shosho 容許 8/12px radius、grid layouts、marketing-style centered column。下列是 Shosho 特有禁用 + 通用 AI default 防護：

| Pattern | 為什麼禁 |
|---------|----------|
| 大塊橘色 fill（>4% 面積） | 違反 strategy 1 — orange 是 line/dot/char 不是 block |
| Bolt 同頁出現 ≥ 2 次 | 違反 99/1 strategy 2 |
| 把 `--sho-accent` 用在 body text | 對比 3.2:1，不合 AA |
| 套 Inter / Roboto / system-ui default 取代 Geist | 失去 brand voice |
| 圓角 ≥ 16px | 進入「現代 SaaS 大圓角」氛圍，Shosho 上限 12px |

---

## 適用範圍 — Shosho

### ✅ 走 `--sho-*`

- `/progress` 公開儀表板 — **首批落地** ✓（PR #595–#608）
- `nakama.shosho.tw` root marketing landing（待建）
- 任何 partner-facing 或 marketing 頁

### ❌ 不走 `--sho-*`（走 `--brk-*` / `--nk-*` 或自訂）

- `/projects/{slug}` Brook review surface — 走 `--brk-*` 是 editorial setting
- Reader 書本 / annotation surface — 走 `--brk-*`（閱讀體驗 paper-and-ink）
- Bridge 中控台 — 走 `--nk-*` workshop / chassis 語言（與 Shosho 並列、互不取代）
- Obsidian vault 內頁 — CSS snippet 獨立處理
- Agent markdown 輸出本身 — 不算 UI

---

## Revision Log

| 日期 | 版本 | 變更 | 來源 |
|------|------|------|------|
| 2026-04-20 | v0 | 骨架建立，待 Claude Design 首輪迭代 | 手動 |
| 2026-05-07 | v1 | 從 `/projects/{slug}` review-mode 落地批量填入：Aesthetic direction、typography、colour tokens（light + dark）、spacing、radius、motion、layout、component patterns、states checklist。Tokens canonical 在 `thousand_sunny/static/projects/tokens.css`。 | Claude Design handoff `N458-brook-review-mode/project/brook/` → issue #458 落地 |
| 2026-05-19 | v2 | 加入 `--sho-*` Shosho CI namespace 並存章節：aesthetic direction（5 strategies + 99/1 bolt + ≤4% orange）、Geist + Noto Sans TC + Geist Mono、oklch color palette、binary theme switch、asset versioning infra。Tokens canonical 在 `thousand_sunny/static/shosho/tokens.css`。Brook (`--brk-*`) 章節保留不動。 | Claude Design handoff `shosho-website-new-design` → PR #595–#608 落地 |
