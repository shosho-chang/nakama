# Weekly Dashboard — UI Design Spec (Stage 1: IA / Layout)

**Date:** 2026-05-31
**Status:** IA locked (grill 2026-05-31); awaiting hi-fi mock sign-off
**Builds on:** [ADR-039](../decisions/ADR-039-lifeos-weekly-dashboard.md) (data model + behavior), [`docs/design-system.md`](../design-system.md) (`--sho-*` visual language — **the hard authority; no new tokens**)
**Surface:** `/bridge/weekly` (Bridge, single-user, HMAC cookie)

> This spec captures the **information architecture, layout, interaction states, and component treatment** decided in the 2026-05-31 design grill. It does **not** invent visual language — that is frozen in `design-system.md` (`--sho-*`, LINE Seed TW, PANTONE 165 PC orange ≤4% as line/dot/char, hairline-driven, ops density, 4/8/12px radius, light/dark/auto). The `frontend-design` plugin is **out of scope here** (it is for greenfield aesthetic invention; this surface conforms to an existing system).

## 1. Page structure — single dense scroll + sticky header + sticky dock (Direction A)

A weekly dashboard's job is **at-a-glance situational awareness** (open it Sunday → *see* the week), which favors a single scannable surface over hidden tabs. Top-to-bottom order mirrors 修修's Sunday ritual: numbers → tasks → daily → review → notes.

- **Sticky top:** week header (`◀ W23 · 5/31–6/6 ▶`) + hero metric row.
- **Scroll body (zones):** Task views → Daily grid → Weekly Review → 隨手筆記.
- **Sticky bottom:** Pomodoro dock (reuse the Tier C `_pomodoro_dock.html` pattern — active-task selector + timer + 🍅 rollup).
- Inherits the Bridge `chassis-nav` top band + `_shosho_asset_version()` CSS-busting + `theme.js` toggle, like every Bridge surface.

## 2. Dual-mode — focused-week, status-driven layout

The page renders one **focused week** (default = the week needing attention). Same URL, layout adapts to the focused week's `status`; `◀▶` navigates weeks freely.

### Active-mode (focused week has a file / is in progress)
```
◀  W23 · 5/31–6/6  ▶                       [sticky header]
預計 32🍅   實際 18🍅   ▰▰▰▰▱ 56%          [hero gauge]
─────────────────────────────────────────
[今日] 整週  按專案                         [task views]
 □ 肌酸 Pre-prod      2🍅
 □ 蛋白質 Lit review  3🍅
─────────────────────────────────────────
每日  日  一  二  三  四  五  六            [daily grid, Sun-start]
工作  ·  ▰▰ ▰  ▰▰ ·  ▰  —
運動  ·  ·  30  ·  45  ·  ·
─────────────────────────────────────────
✨ 週回顧 (本週末填)                         [review — pending until week-end]
✎ 隨手筆記 …
─────────────────────────────────────────
🎬 task▾   ▶ 25:00   🍅 18/32              [sticky dock]
```

### Review-mode (Sunday landing; focused week = last week, status≠reviewed)
```
◀  W22 · 5/24–5/30  ▶                  ⚠ 待回顧
─────────────────────────────────────────
執行率  預計 28🍅  實際 22🍅  ▰▰▰▰▱ 79%
─────────────────────────────────────────
未完成 task → 排進 W23                      [carry-forward]
 □ 肌酸 Post-prod   剩 3🍅   [排程…]
 □ 蛋白質 Synthesis 剩 5🍅   [排程…]
─────────────────────────────────────────
✨ Highlight  / 😔 Lowlight / 📚 學到的     [6-Q review form]
🙏 感恩 1·2·3 / ✅ top-3 完成率 2/3 (自動)
🎯 下週 top-3  [____] [____] [____]
─────────────────────────────────────────
        [ 完成回顧 → 建立 W23 ]            [soft gate, overridable]
```
On submit → `W22.status=reviewed`, focus auto-advances to W23 (active-mode). Past weeks via `◀` render their completed review **read-only**. Soft gate per ADR-039 D7 (overridable; honest `created_before_previous_review` state if skipped).

## 3. Component treatment (within `--sho-*`)

- **Hero metric** — existing `.sho-gauge` **hairline horizontal bar**: `預計 N🍅` (muted) · `實際 M🍅` (ink) · gauge filled to M/N with `%` label; orange only on the fill edge (line, ≤4%). One row, `tabular-nums`. **No ring/donut** (too heavy / SaaS; violates hairline-driven).
- **Task views** (tabs in the task zone): **今日** (plan hits today + that day's 🍅 + done progress + status toggle) · **整週** (week plan grouped by day) · **按專案** (per-project allocation + est/actual rollup). Status toggle writes TaskNotes `status ∈ {to-do,doing,done,paused}` (ADR-039 D6).
- **Daily grid** — `.sho-grid-strip`/`.sho-grid-cell` compact grid. **7 columns 日→六 (week is Sun-start, ADR-039 D2).** Rows = 工作 / 運動 / 冥想 / 雜事. 工作·雜事 cells = scheduled 🍅 / task chips (read + navigate; status toggle). 運動·冥想 cells = habit minutes (**read-only**, from daily-note fields, ADR-039 D6). **六日 工作 row dimmed unless a `reason` exists** (then shows with ⚠ marker, ADR-039 D9).
- **Pomodoro dock** — reuse Tier C `_pomodoro_dock.html` (active-task `▾`, `▶` timer, `🍅 actual/est` rollup, Tasks ▾ expand). One timer surface; writes `timeEntries[]` (ADR-039 D3). Sticky across scroll.
- **隨手筆記** — single human-prose textarea section (weekly-scoped reflection; NOT a quick-capture inbox, ADR-039 D9).

## 4. States (CLAUDE.md mandate — every surface)

| State | Treatment |
|---|---|
| **empty week** (no plan) | empty hint: "本週尚無排程 — 從上週未完成 task 帶過來，或 ＋新增" |
| **review-pending** | the review-mode landing (§2) |
| **mid-timer** | dock shows running countdown; active task row highlighted |
| **loading** | hairline skeleton rows (no spinner) |
| **sync-conflict** | top banner (Slice-0 `*.sync-conflict-*` detection, ADR-039) |
| **stale-write** | 409 → toast "本週檔已被別處改動，重新整理後再存" (ADR-039 D5 If-Match) |
| **hover / focus / active / disabled** | per design-system: `--sho-focus` two-step ring; `--sho-t-fast` 120ms; `prefers-reduced-motion` → 0ms |

## 5. Accessibility (non-negotiable, per CLAUDE.md)

- Body text AAA (oklch L=0.20 on 0.988 ≈ 16:1); secondary AA; orange never carries body text (border/focus/single-word only).
- Full keyboard nav (week ◀▶, tab switches, task toggles, timer, form fields); semantic HTML (`<nav>`, `<table>` for daily grid, `<form>` for review).
- `prefers-reduced-motion: reduce` disables all motion (timer tick stays functional, decorative transitions 0ms).

## 6. Deferred to hi-fi mock iteration
Exact cell density / chip styling / gauge proportions / responsive narrow-viewport (mobile deferred per ADR-029) / micro-copy — resolved against the static `--sho-*` mock + Playwright screenshots, then the mock becomes the Slice-0 template skeleton.
