# Feedback — vendor SPA 的 UI 改動必須在真實瀏覽器裡驗證，不可盲寫 selector

**Type:** feedback
**Created:** 2026-08-24
**Confidence:** high（同一天連續三次失敗後才修正做法）

## 事實

2026-08-24 做 FluentCommunity profile 加「航海日誌」tab 時，我連續三輪交付了**沒實測過**的
DOM 操作程式碼，修修連續三次回報「沒反應／行為不對」：

1. 第一輪：猜 `ul.fcom_profile_nav` 的父層是內容卡 → 錯（真實結構是
   `div.object_header` 的**下一個 SECTION 兄弟** `section.fcom_space_container`）
2. 第二輪：腳本只掛 `portal_header`，而站上走的模板 `portal_page.php` 根本不呼叫它 → 腳本沒載入
3. 第三輪：卡片樣式自己刻，與 vendor 的 `.about_wrap`（`var(--fcom-primary-bg)` / 5px / 20px）不一致

改用 **Playwright MCP ＋ magic login** 實際開瀏覽器之後，同一批問題在一輪內全部解決，
並額外抓到兩個盲寫絕對看不到的 vendor router 對抗點（popstate 踢回 base、
push 前的 replaceState 覆寫當前 entry）與一個真 bug（Vue 認為「已在該路由」而不更新網址）。

## Why

vendor 的 Vue SPA 是 minified bundle，DOM 結構、CSS 變數、router 行為**都無法從原始碼推導**。
盲寫 selector ＝把驗證成本轉嫁給修修，一輪來回至少半小時，而且會累積「這東西不可靠」的印象。
這也違反 CLAUDE.md 的「嚴禁幻想」與 [[feedback_ui_browser_verification_before_merge]]。

## How to apply

**動任何 vendor SPA 的前端之前，先開瀏覽器。** 標準流程（2026-08-24 驗證可行）：

1. **拿登入態**：站上有 fluent-security，可程式化生成免密碼登入連結（用測試帳號 user 8，
   不要用修修的帳號）：
   ```
   wp eval '$t = apply_filters("fluent_auth/login_token_by_user_id", "", 8, 30);
            echo add_query_arg(["fls_al" => $t, "force_redirect" => "yes"], site_url("index.php"));'
   ```
   → 30 分鐘有效的 magic link，貼進 Playwright `browser_navigate` 即登入。
2. **解剖 DOM**：`browser_evaluate` 印出真實結構、`getComputedStyle` 撈視覺參數、
   掃 `document.styleSheets` 找 vendor 的原始 CSS 規則（要沿用它的變數，暗色模式才會自動跟隨）。
3. **改完部署到 production plugin 目錄 → 回瀏覽器逐項點擊驗證**（含 back/forward/深連結）。
4. 通過才 commit，commit message 寫「Playwright 實測」與涵蓋的情境。

**Chrome extension（claude-in-chrome）在此機器未連線**，用 `mcp__plugin_playwright_playwright__*`。

**Portal 基底路徑不是站根**：`fleet.shosho.tw/deck/`（`Helper::baseUrl()` 才是真相，別假設）。

Related: [[feedback_ui_browser_verification_before_merge]]、[[reference_fleet_gamification_stack]]
