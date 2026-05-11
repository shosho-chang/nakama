---
name: 用 Playwright MCP 跑 UI verify，不要丟給修修
description: Reader UI / Web UI 驗收的「機械步驟」（navigate / select text / click button / 看 network response）走 mcp__plugin_playwright_playwright__*，只把「視覺判讀」留給修修
type: feedback
---

修修明確點出（2026-05-07 #453 Reader UI 驗收）：「像這個步驟你不能幫我做嗎？隔壁的 Codex 都已經可以直接操作這些電腦程式了」。我手上有 Playwright MCP 整套（navigate / click / snapshot / evaluate / network_requests / press_key）但沒拿出來，反射動作還停在「列步驟給 user 跑」。

**Why**：
- HITL 「視覺驗收」≠「全部都要 user 動手」
- 機械步驟（navigate URL / 找元素 / click button / 看 POST response status / accessibility snapshot）browser MCP 全做得到
- 真正不能取代 user 的只有「**視覺判讀**」 — 高亮位置對不對、sidebar 視覺感受、UI 看起來對不對
- 把機械步驟代跑掉，user 從「整套手動」剩「眼睛確認」 — 大幅降低 verify cost

**How to apply**：
1. **UI verify 任務 default ON Playwright MCP**：先 `ToolSearch select:browser_navigate,browser_snapshot,browser_click,browser_evaluate,browser_press_key,browser_console_messages,browser_network_requests` 載入工具
2. **代跑流程**：navigate → snapshot 看可互動元素 → click / select text / type → 看 console + network → snapshot 比對
3. **回報給 user 看**：把 snapshot / network response 摘要給 user，問「這個截圖/結果看起來對嗎」 — 而不是「請你 click XX 按鈕」
4. **真留給 user 的判讀**：
   - 視覺位置 / 字型 / 配色感受
   - 多步驟流程的整體感
   - aesthetic judgment（是否 AI slop default）
5. **Server 起不起得來不用 user 跑**：能 `python -m thousand_sunny.app` 走 background bash 就自己 spawn（per `feedback_local_shell_ops_just_do_it.md`）

**邊界**：
- Browser MCP 跑在修修本機 Chrome（localhost:8000 可直連）
- 跨機器（VPS）/ 需要修修登入帳號 / 抓 cookie 的場景仍是 HITL
