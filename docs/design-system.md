# Nakama Design System

> 所有 Nakama UI surface 的 single source of truth：public（`/progress`、`/architecture`）、
> Bridge ops 中控台、Robin reader / books / promotion / writing-assist、Brook handoff / projects、
> login、未來的 marketing landing。
>
> **Workflow：** Claude Design（視覺探索 + iteration）→ 匯出 handoff bundle → 貼進這份 →
> Claude Code 照這份落地。
>
> 美學是 first-class requirement，不是 nice-to-have。見 [feedback_aesthetic_first_class.md](../memory/claude/feedback_aesthetic_first_class.md)。

---

## Status

- **版本**：v3 — single design system（`--sho-*`）
- **最近更新**：2026-05-21
- **Canonical tokens**：`thousand_sunny/static/shosho/tokens.css`
- **來源**：Claude Design handoff bundle `shosho-website-new-design`（Design System v0.1）

**一個 design system，一組 token namespace。** `--sho-*` 是 Nakama 唯一的設計語言。
過去並存的 `--brk-*`（Brook editorial paper）與 `--nk-*`（Bridge workshop floor）兩個
namespace 已**全面退役** — 它們是 AI-slop default，不是刻意設計的氣質。所有 surface
現已統一到 `--sho-*`（PR #631、#642–#651）。

**Opt-in 機制**：surface 的 root element（通常 `<body>`）加 `class="sho"`，`--sho-*`
token 即 cascade。Dark mode 走 `<html>` 上的 `data-theme` attribute（或
`prefers-color-scheme` auto）— 見 [Theme switching](#theme-switching)。

---

## Aesthetic Direction

Engineering ops density，從 paper-and-ink editorial 翻譯而來。PANTONE 165 PC 橘
`#e98965`（≈ `oklch(0.71 0.135 41)`）是唯一 chromatic accent，全頁佔比 ≤ 4%。Neutral
scale 走暖灰 oklch hue 80°，light 是接近紙白 `oklch(0.988 0.003 80)`、dark 是 warm
near-black `oklch(0.145 0.005 80)`（不是 `#000`）。單一字族 **LINE Seed TW**（sans / zh /
mono 共用一支字）。圓角 4 / 8 / 12px 三階 + `--sho-r-pill: 999px` 給 switch / chip。

### One memorable element

**閃電 bolt 作為 99/1 stamp + decorative wordmark 作 signage。** Bolt 是 viewBox
`0 0 24 14` 的 polyline zigzag，每頁最多 1×、通常在 footer「研究編號 N°XXXX」前作為
watermark；header logo 內含其字面化版本。Bolt rotated -25° 表示「進入」、靜置時為 logo
一部分；hover logo 會觸發 320ms 的 `sho-bolt-jiggle` keyframe。

### 五個翻譯策略（Claude Design strategy.jsx）

1. **Orange = line / dot / char**，不是 block fill — 橘色只出現在 1px border、focus ring、單字、active text，從不大面積填色
2. **Bolt = stamp**，不是 wallpaper — 99/1 規則，整個頁面看到最多一次
3. **Decorative wordmark = signage**，不是 chrome — logo 是品牌印記，不每頁佔位
4. **Engineer density**，不是 YouTuber whitespace — ops 頁走 information-dense layout
5. **Motion ≤ 200ms** — `--sho-t-fast: 120ms`（hover）/ `--sho-t-base: 180ms`（state change）/ `--sho-t-slow: 280ms`（dialog only）

---

## Typography

**單一字族：LINE Seed TW。** sans、zh-Hant、mono role 全部共用一支字 — mono 的「氣味」
靠 uppercase + letter-spacing + colour 區分，不靠等寬字體。

### 字族

- family（`--sho-font-sans` / `--sho-font-zh` / `--sho-font-mono` 三者皆同）：
  `"LINE Seed TW", "Noto Sans TC", "PingFang TC", ui-sans-serif, system-ui, sans-serif`
- 四個字重 cut（Th / Rg / Bd / Eb），在 tokens.css 用 `@font-face` 以 weight range 綁定：
  `100–300 → Th`、`400–500 → Rg`、`600–700 → Bd`、`800–900 → Eb` — 讓設計裡的 500 / 600
  自動對到實體 cut，避免瀏覽器 faux-bold
- 字型檔 bundle 在 `thousand_sunny/static/shosho/fonts/`（woff2 + woff），**不**走 Google
  Fonts CDN — 自帶 = CSP-safe + 無外部依賴

### Mono role

mono 不是另一支字 — 是同一支 LINE Seed TW 加 `font-variant-numeric: tabular-nums` +
uppercase + `0.06em` tracking。用於研究編號 / id / slug /「indoor voice」metadata。

### Scale

Shosho 用 typography scale 但**不**把每個 size 包成 `--sho-t-*` token。原因：size 多半
貼合 component（footer mono 11.5px、kicker 11px、tag 11.5px、body 不固定），抽 token 會讓
每個 component 自找對應，比直接寫 `font-size` 更難讀。**只有** mono carrier
`.sho-mono { font-size: 12.5px }` 有預設值。

中文 line-height：建議 ≥ 1.6（per component 設定，無 token）。

### Common classes

| Class | 用途 |
|-------|------|
| `.sho-mono` | "indoor voice" — 12.5px mono, muted color, tabular-nums |
| `.sho-mono-up` | mono + uppercase + 0.06em tracking, weight 500 |
| `.sho-lk` | inline link — currentColor + 1px underline → orange + orange underline on hover |
| `.sho-kicker` | 「[ 001 ] section name」structural label |
| `.sho-tag` / `.sho-tag--accent` | bordered pill chip, neutral or accent variant |

---

## Color Tokens

> oklch 色彩空間，hue 鎖 80°（neutral warm）+ 41° / 48°（accent orange）。完整定義在
> `thousand_sunny/static/shosho/tokens.css`。

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

軟填色（soft fill）統一用 `color-mix(in oklch, var(--sho-<sem>) <pct>%, transparent)`，
不另開 token。

### Dark（auto via `prefers-color-scheme` OR explicit `html[data-theme="dark"]`）

Brightness flip + accent shift toward `oklch(0.78 0.130 48)`（從 41° hue 推到 48° 補
warm dark mode 偏冷的視感）。完整 dark block 見 tokens.css。

### Theme switching

三模式，state 寫在 `<html>` 的 `data-theme` attribute 上：
- **auto**（預設）：無 attribute，跟系統 `prefers-color-scheme`
- **顯式 light**：`<html data-theme="light">`
- **顯式 dark**：`<html data-theme="dark">`

token override 選擇器是 `html[data-theme="dark"] .sho`（dark）與 media query
`html:not([data-theme="light"]) .sho`（auto）。attribute 放 `<html>` 而非 `<body>`，
因為它由 `<head>` 內的 script set，那時 `<body>` 還不存在。

**統一 toggle**：`static/shosho/theme.js` 是所有 app surface 的單一 toggle 來源。
它以 render-blocking `<script>` 載入 `<head>`（不可加 `defer`/`async`，否則 paint
前主題還沒套好會 flash），在 `<body>` paint 前套好主題，並於 `DOMContentLoaded`
注入右下角固定 toggle（`.sho-theme-toggle`，auto → light → dark 循環）。偏好持久化
在 `localStorage['sho-theme']`。`/architecture`、`/progress` 兩個對外靜態頁保留各自
的 binary theme-switch slider（見下方 Theme switch 節），但同樣寫 `html[data-theme]`
+ 共用 `sho-theme` key，state 跨 surface 一致。

### Contrast 要求

- body text on bg：oklch L=0.20 on L=0.988 ≈ 16:1（AAA）
- secondary on bg：oklch L=0.36 on L=0.988 ≈ 8:1（AA）
- accent on bg：oklch L=0.71 chroma 0.135 on L=0.988 ≈ 3.2:1 — **僅用於 border / focus / 單字，不承擔 body text**

---

## Spacing & Radius

### Spacing scale（4px grid）

`--sho-s-1` … `--sho-s-10` = `4 / 8 / 12 / 16 / 24 / 32 / 48 / 64 / 96 / 128` px。
注意 `--sho-s-4` → `--sho-s-5` 從 16 跳 24（略過 20）。

### Radius

| Token | Value | 用途 |
|-------|-------|------|
| `--sho-r-1` | 4px | tag, kicker number |
| `--sho-r-2` | 8px | card, button |
| `--sho-r-3` | 12px | dialog, large panel |
| `--sho-r-pill` | 999px | switch track, chip variant |

上限 12px — 不進入「現代 SaaS 大圓角」氛圍。

---

## Motion

- `--sho-t-fast: 120ms`（hover）
- `--sho-t-base: 180ms`（theme switch thumb slide、state change）
- `--sho-t-slow: 280ms`（dialog open）
- `--sho-ease: cubic-bezier(0.22, 1, 0.36, 1)`（calm out-curve）
- `prefers-reduced-motion: reduce` → 三個 dur 全降為 0ms（已實作 tokens.css）

---

## Focus ring

```css
--sho-focus: 0 0 0 2px var(--sho-bg), 0 0 0 4px var(--sho-accent);
```

Two-step ring — 內 2px page-bg、外 2px accent orange。任何 interactive 元件
focus-visible 時統一套：

```css
.sho-foo:focus-visible { outline: none; box-shadow: var(--sho-focus); }
```

---

## Component Patterns

### Logo mark

- `.sho-logo-mark` — 含品牌 PNG（`/static/shosho/brand/logo_1.png`），dark mode 套
  `filter: var(--sho-logo-filter)` = `invert(1) brightness(1.1)`
- hover 觸發 `sho-bolt-jiggle` 320ms keyframe，bolt 上下抖 1px ± rotate 3°

### Bolt mark — 99/1 stamp

- SVG polyline zigzag，viewBox `0 0 24 14`
- 預設大小：`.sho-bolt svg { width: 1em; height: 1em }`
- **每頁最多 1×**（footer 99/1 stamp、或 hero watermark 二選一）

### Theme switch（binary）

- 結構：`<button class="theme-switch" role="switch" aria-checked="...">` containing
  `.theme-switch-track > [.theme-switch-label-light, .theme-switch-label-dark, .theme-switch-thumb]`
- Geometry：track 44×22px、thumb 16px、padding 3px
- Vertical centering：thumb 用 `top: 50%; transform: translate(0, -50%)`
- 持久化：`localStorage` + matchMedia fallback；try/catch 包 storage 存取以防 Safari private mode
- 持久化 + 初始套用：inline `<script>` set `document.documentElement` 的
  `data-theme`（與共用 `theme.js` 同一個 `sho-theme` localStorage key）

### Bridge ops chassis（dense dashboard surfaces）

Bridge 系列（dashboard + SEO + 子頁）共用 `static/shosho/bridge.css` 的 ops 詞彙：

- `.chassis` + `.chassis-*` — dark ops header band（`_chassis_nav.html` 發出）
- `.sho-chip` + `.sho-chip--active|online|idle|hold|offline` — 5-state status pill
- `.sho-readout` / `.sho-gauge` — metric readout + hairline gauge
- `.sho-panel` — hairline card
- `.sho-grid-strip` / `.sho-grid-cell` — dense cell-grid（compact tables / tile grids）
- `.sho-btn` / `.sho-btn--ghost` — primary + ghost ops buttons

### Tag / chip

- `.sho-tag` 預設 neutral：transparent bg + `--sho-line-2` border + mono 11.5px
- `.sho-tag--accent` 變 accent：color + border 換 `--sho-accent` / `--sho-accent-line`

### Kicker（structural label）

- `[ 001 ] SECTION NAME` 風格
- `.sho-kicker` 包 `.sho-kicker-n`（編號）+ uppercase mono text

### Mono carrier

- `.sho-mono` — 12.5px mono、muted color、tabular-nums，承載 metadata / timestamp / id
- 加 `.sho-mono-up` 變 caps style（uppercase + 0.06em tracking）

### Hairline rule

- `.sho-rule` — `height: 1px; background: var(--sho-line); border: 0` — sections 間 divider

### Inline link

- `.sho-lk` — currentColor + 1px underline（`background-image` gradient 1px @ bottom）
- hover → 同時換 color 跟 underline 到 `--sho-accent`
- focus-visible → `box-shadow: var(--sho-focus)`

---

## Asset versioning

外部 surface 走 Cloudflare Tunnel，`/static/*` 在 CF 邊緣有 4h cache。Hard refresh
**不**會 bypass CF cache — 改了 CSS 卻看到舊樣式幾乎都是這個原因。

機制：每個 render dynamic 頁面的 router 有一個 `_shosho_asset_version()` helper，boot 時
sha1 hash 該頁 link 的 CSS 檔案、取 8 字元當 version slug，注入模板 `?v=<slug>` query。
任一 CSS 改動 → service restart → 新 hash → 新 URL → CF cache miss → fresh fetch。

此 pattern 散落在 `routers/{progress,architecture,robin,bridge,brook,projects,
promotion_review,writing_assist,auth,franky}.py`。**第三個以上 surface 出現時可考慮抽成
共用 helper**（目前各 router 各自一份）。

---

## Reduced motion

```css
@media (prefers-reduced-motion: reduce) {
  .sho { --sho-t-fast: 0ms; --sho-t-base: 0ms; --sho-t-slow: 0ms; }
  .sho .sho-bolt, .sho .sho-logo-mark:hover .sho-bolt { animation: none; }
}
```

已實作 tokens.css。任何新 keyframe animation **必須**在這個 media query 內被 disable。

---

## AI Slop 禁用清單

| Pattern | 為什麼禁 |
|---------|----------|
| 大塊橘色 fill（>4% 面積） | 違反 strategy 1 — orange 是 line/dot/char 不是 block |
| Bolt 同頁出現 ≥ 2 次 | 違反 99/1 strategy 2 |
| 把 `--sho-accent` 用在 body text | 對比 3.2:1，不合 AA |
| 套 Inter / Roboto / system-ui default 取代 LINE Seed TW | 失去 brand voice |
| 紫色漸層、teal accent、任何非 PANTONE 165 PC 的彩色 | 單一 accent 原則 |
| 圓角 ≥ 16px | 進入「現代 SaaS 大圓角」氛圍，上限 12px |
| 重 box-shadow 當分層手段 | Shosho 走 1px hairline-driven，不靠陰影 |
| hardcode 色碼 / 字型在 class / inline style | 一律走 `--sho-*` token |

---

## 適用範圍

### ✅ 走這份 design system（`--sho-*`）

**所有 Nakama web UI surface** — public（`/progress`、`/architecture`）、Bridge ops
中控台（dashboard + SEO + 子頁）、Robin（reader、books、promotion、writing-assist）、
Brook（handoff、projects review）、login、未來的 marketing landing。

### ❌ 不走（用其他機制）

- Obsidian vault 內頁 — 獨立 CSS snippet 處理
- Agent markdown 輸出本身 — 不算 UI
- foliate-js epub reader iframe 內的書本內文 — sandboxed document，`--sho-*` 無法
  cascade，由 `book_reader.js` 的 `pushReaderStyles()` 另行注入

---

## Revision Log

| 日期 | 版本 | 變更 | 來源 |
|------|------|------|------|
| 2026-04-20 | v0 | 骨架建立，待 Claude Design 首輪迭代 | 手動 |
| 2026-05-07 | v1 | 從 `/projects/{slug}` review-mode 落地批量填入 `--brk-*` Brook editorial tokens。 | Claude Design handoff `N458-brook-review-mode` → issue #458 |
| 2026-05-19 | v2 | 加入 `--sho-*` Shosho CI namespace（三 namespace 並存章節）。 | Claude Design handoff `shosho-website-new-design` → PR #595–#608 |
| 2026-05-21 | v3 | **單一 design system 重構**：`--brk-*`（Brook editorial）與 `--nk-*`（Bridge workshop）兩個 namespace 全面退役，整個 Nakama web UI 統一到 `--sho-*`。字族換成單一 LINE Seed TW（取代 Geist + Noto Sans TC + Geist Mono）。13 個 surface migration 完成（PR #631、#642–#651）。文件刪除 obsolete 的 `--brk-*` 章節。 | Claude Design handoff `shosho-website-new-design` Design System v0.1 |
| 2026-05-21 | v3.1 | **統一 theme toggle**：新增 `static/shosho/theme.js` — 單一 light/dark/auto toggle 鋪到全部 36 個 web surface（34 個 app template 注入右下角浮動 pill；`/architecture`、`/progress` 保留 bespoke slider）。`data-theme` 從 `<body>` 遷到 `<html>`，token 選擇器改 `html[data-theme]`。退役 reader / book_reader / projects 三套 ad-hoc per-page toggle。 | 對話 UI 稽核 — 全站 L/D toggle 缺口 |
