---
name: Browser smoke 不要拍 screenshot 當斷言
description: Playwright MCP screenshot 一張 PNG base64 進 context ~30-50k token；改用 browser_evaluate + browser_snapshot 文字斷言，只在真要看美學/layout drift 時才拍。2026-05-05 EPUB Reader workflow 燒了 ~200k 在不必要 screenshot 上的教訓。
type: feedback
---

Playwright MCP `browser_take_screenshot` 把 PNG 直接 base64 進 context — 一張就吃 30-50k token。整 session 12 張 = 200-300k 浪費。

**Why**：當下覺得「眼見為憑」最快，但其實絕大多數視覺斷言都能用：

- `browser_evaluate(() => ({ width: el.getBoundingClientRect().width, color: getComputedStyle(el).color }))` — 文字回傳，幾百 token
- `browser_snapshot()` — accessibility tree yaml，每節點 ~10-30 token，整頁通常 < 1k
- `browser_console_messages({level: "error"})` — 只回錯誤 string

**How to apply**：

| 想驗證的事 | 用什麼 |
|---|---|
| 「按鈕在 DOM 嗎」 | `browser_snapshot` 看 yaml |
| 「按鈕點下去 disabled 了嗎」 | `browser_evaluate(() => btn.disabled)` |
| 「dual-page 在 1920px / 單頁在 1280px」 | `browser_evaluate(() => view.renderer.getAttribute("max-column-count"))` |
| 「dark mode 套到 EPUB iframe 沒」 | `browser_evaluate(() => getComputedStyle(document.body).backgroundColor)` |
| 「CSP header 對嗎」 | `curl -sI` 看 header，不用瀏覽器 |
| 「badge 顏色是不是綠色」 | `browser_evaluate(() => getComputedStyle(badge).backgroundColor)` |
| 「Reader 真的渲染出書了」 | **這個拍** — chapter heading 沒在 evaluate 上明顯，screenshot 確認文字出現是 cheap |
| 「Layout drift / 美學是否看起來對」 | **這個拍** — screenshot 唯一價值點 |

**Rule of thumb**：每 slice smoke ≤ 1 張 screenshot（重大視覺檢查）。其他全用 evaluate / snapshot。

**Detection**：每次想 take_screenshot 前先問：「我可以用 evaluate 拿到這個 boolean / 字串嗎？」可以就改 evaluate。
