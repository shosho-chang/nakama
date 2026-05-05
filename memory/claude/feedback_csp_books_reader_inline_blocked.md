---
name: CSP `script-src 'self'` on /books/* blocks inline scripts AND inline event handlers
description: thousand_sunny/middleware/csp.py 對 /books 與 /api/books 強制 script-src 'self'；inline <script> 與 onerror=/onclick= 屬性都會被瀏覽器靜默拒絕，加 UI 行為時必須走 served-from-origin /static/*.js。撞過 PR #427 → #428 修
type: feedback
created: 2026-05-05
---

寫 reader 系列頁面（books_library / book_reader / book_upload）任何新 JS 行為時，**禁止 inline `<script>` 區塊或 inline event handler**（`onclick=`、`onerror=`、`oninput=` 等）。`thousand_sunny/middleware/csp.py` 對 `/books*` + `/api/books*` 注 `Content-Security-Policy: script-src 'self'`，瀏覽器會在 console 噴 `Refused to execute inline event handler` 但**不會**阻止頁面渲染 — UI 看起來正常，行為靜默死。

**Why**：PR #427 的書架 cancel-X 按鈕 + cover img onerror fallback 都是 inline，merge 後實機點擊毫無反應，user 才反映「按下去沒反應」。修補成 PR #428 — 把 X 拿掉、cover fallback 改用獨立 `/static/books_library.js` 加 error event listener。Defense-in-depth 設計刻意：sanitizer 漏掉 EPUB 注入時 CSP 是第二道牆，所以這個限制不會放寬。

**How to apply**：
- 任何 `/books*` 或 `/api/books*` 新增 UI 互動 → 永遠寫到 `thousand_sunny/static/<page>.js`，HTML 用 `<script type="module" src="/static/<page>.js"></script>`
- img fallback 不能用 `onerror=`，要寫 JS 註冊 `error` 事件並加 class 切換顯示（例如 PR #428 的 `.cover img.broken { display: none }` + 同層 placeholder）
- 改現有頁面前先 grep `onerror|onclick|oninput|<script>` 在 templates/robin/ 看有沒有遺留 inline 死碼
- E2E 驗證：開瀏覽器 DevTools console 看 CSP violation 訊息；route handler 200 + UI 行為失靈時這是第一個假設

不影響範圍：Robin inbox `/`、Bridge `/bridge*`、其他非 reader surface 沒掛這個 CSP，inline `<script>` 仍合法。CSP 只 guard `/books` 與 `/api/books`（見 csp.py `_GUARDED_PREFIXES`）。
