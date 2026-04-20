# Nakama Design System

> 所有 Nakama UI surface 的 source of truth：Bridge、Thousand Sunny 甲板儀表板、Chopper 社群、
> Brook 對外 template、任何 landing / marketing。
>
> **Workflow：** Claude Design（視覺探索 + iteration）→ 匯出 → 貼進這份 → Claude Code 照這份落地。
>
> 美學是 first-class requirement，不是 nice-to-have。見 [feedback_aesthetic_first_class.md](../memory/claude/feedback_aesthetic_first_class.md)。

---

## Status

- **版本**：v0（骨架 — 待 Claude Design 首輪迭代後填入）
- **最近更新**：2026-04-20

---

## Aesthetic Direction

> 一段話，具體到兩個設計師看了能做出相似作品。
>
> 反例（太模糊）：「modern, clean, professional」
> 正例：「Editorial magazine meets Swiss poster. Heavy use of a single display serif, oversized
> numerals as structural elements, a single accent of radioactive yellow against deep forest green.
> Memorable element: hero headline 用 text-balance 壓縮成 monolithic block。」

_待 Claude Design 首輪迭代後填入。_

### One memorable element
<!-- 使用者一眼看完能說出「這個介面跟別的不一樣」的那一件事 -->

_待定。_

---

## Typography

### Display font
- family: _待定_
- source: _待定_（Google Fonts / Fontshare / self-hosted via `next/font` or `@font-face`）
- weights: _待定_
- 用於：landing H1、hero、dashboard page title、甲板儀表板的 agent 名字

### Body font
- family: _待定_（**不是 Inter / Roboto / Arial default**）
- weights: _待定_

### Mono font
- family: _待定_
- 用於：cost 數字、token count、code block、timestamp

### Scale
| Level | Size | Line-height | Weight |
|-------|------|-------------|--------|
| display | _待定_ | _待定_ | _待定_ |
| h1 | _待定_ | _待定_ | _待定_ |
| h2 | _待定_ | _待定_ | _待定_ |
| h3 | _待定_ | _待定_ | _待定_ |
| body | _待定_ | _待定_ | _待定_ |
| caption | _待定_ | _待定_ | _待定_ |

---

## Color Tokens

> 一個 dominant + 一個 sharp accent。不是六色中性調。

### Background
- `--color-bg-primary`: _待定_
- `--color-bg-surface`: _待定_
- `--color-bg-elevated`: _待定_

### Text
- `--color-text-primary`: _待定_
- `--color-text-secondary`: _待定_
- `--color-text-muted`: _待定_

### Accent（sharp，用來創造記憶點）
- `--color-accent`: _待定_
- `--color-accent-hover`: _待定_
- `--color-accent-active`: _待定_

### Semantic
- `--color-success`: _待定_
- `--color-warning`: _待定_
- `--color-danger`: _待定_
- `--color-info`: _待定_

### Contrast 要求
- body text on bg-primary：≥ 7:1（AAA）
- secondary text on bg-primary：≥ 4.5:1（AA）
- accent on bg-primary：≥ 3:1

---

## Spacing

> 基於 base unit 的 scale，不要散亂。

- base unit: _待定_（e.g. 4px）
- scale: _待定_（e.g. `0, 1, 2, 3, 4, 6, 8, 12, 16, 24, 32`）
- 實作：Tailwind 預設 scale / CSS custom properties / 其他

---

## Motion

> 有 choreography 不是隨機 micro-interaction。一個整頁 reveal 勝過十個隨機 hover 效果。

- default-duration: _待定_
- default-easing: _待定_（cubic-bezier 值）
- page-enter 策略: _待定_
- scroll-reveal 策略: _待定_
- hover 策略: _待定_
- reduced-motion: 必須支援 `prefers-reduced-motion`

---

## Layout

- container max-width: _待定_
- 主要格線策略: _待定_（**不允許永遠 1200px centered column**）
- 非對稱 / overlap / 破格 的使用場合: _待定_

---

## Component Patterns

### Button
- Primary / Secondary / Ghost / Danger 四型
- 字眼禁用：「Get Started」「Learn More」等通用 CTA
- 每個型的 states：default / hover / focus / active / disabled / loading

_實作細節待定。_

### Card
- 不允許 `grid-cols-3` / `grid-cols-4` 千篇一律均勻格。
- 如果用 card grid，至少其中一張要破格（大小或位置差異）。

_實作細節待定。_

### Form
- input states：default / focus / error / disabled / filled
- 錯誤訊息位置、樣式
- label 策略（永遠可見 / float / aria-label）

_實作細節待定。_

### Data display（Bridge cost / memory / agent status 用）
- 數字用 mono font
- 趨勢用顏色 + 箭頭，不只顏色（色盲 safety）

_實作細節待定。_

---

## States Checklist（每個 component 都要處理）

- [ ] default
- [ ] loading
- [ ] empty
- [ ] error
- [ ] hover / focus / active
- [ ] disabled

---

## AI Slop 禁用清單

未來的 `design-quality.js` hook 會偵測以下 pattern 並警告（**警告不阻擋**，保留迭代速度）：

| Pattern | 為什麼禁 |
|---------|----------|
| Inter / Roboto / Arial 字體（except invisible typography 場景） | AI default 最明顯訊號 |
| `bg-gradient-to-*` with purple | 最被濫用的 AI 配色 |
| `grid-cols-3` / `grid-cols-4` 均勻 card grid | 千篇一律沒個性 |
| 「Get Started」/「Learn More」CTA | 沒意圖、懶惰文案 |
| 寫死在 class 裡的色碼 / 字型，不在 tokens 中 | 繞過 design system |
| 所有東西置中在 1200px column | 沒 layout 主張 |

---

## 使用流程

1. **新 UI 出手前**：讀這份文件（Aesthetic Direction + 相關 component）
2. **有新視覺需求**：在 Claude Design（claude.ai/design）迭代 → 匯出 → 更新這份
3. **實作時**：
   - tokens 寫進 CSS custom properties 或 tailwind config
   - **不要** 硬寫 `#7C3AED` / `font-family: Inter` 在 class 裡
   - 每個 state 都實作，不是 afterthought
4. **完工時**：`[P7-COMPLETION]` 的 Aesthetic direction 段要說明在系統內做了什麼選擇、為什麼

---

## 適用範圍

### ✅ 走這份 design system
- Bridge UI（`bridge/templates/`、`bridge/static/`）
- Thousand Sunny 甲板儀表板（規劃中，見 [project_deck_dashboard_idea.md](../memory/claude/project_deck_dashboard_idea.md)）
- Chopper 社群 UI（規劃中，見 [project_chopper_community_qa.md](../memory/claude/project_chopper_community_qa.md)）
- Brook 對外 template（landing、PPTX 簡報、部落格 post template）
- 任何未來的 marketing / landing page

### ❌ 不走這份（用其他機制）
- Obsidian vault 內頁呈現 — 用 CSS snippet 獨立處理
- Agent 的 markdown 輸出本身 — 內容不算 UI，content guideline 歸各 agent prompt
- Slack 訊息格式 — 走 Slack block kit，不是 web design

---

## Handoff 自 Claude Design

從 Claude Design 匯出「交付套件」後：

1. HTML reference page → 放到 `docs/design-ref/<topic>/` 當實作時的視覺 source
2. Design tokens（色、字、spacing）→ 更新本文件 + 同步進 `bridge/static/tokens.css` 或 tailwind config
3. Component patterns → 更新本文件的 Component Patterns 章節
4. 一頁 changelog 寫在下方 Revision Log

---

## Revision Log

| 日期 | 版本 | 變更 | 來源 |
|------|------|------|------|
| 2026-04-20 | v0 | 骨架建立，待 Claude Design 首輪迭代 | 手動 |
