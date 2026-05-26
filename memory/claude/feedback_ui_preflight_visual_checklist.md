---
name: feedback_ui_preflight_visual_checklist
description: UI surface 完工前的 self-check — gutter / overflow / focus / hover / theme — 不要讓修修挑 trivial 視覺 bug
metadata:
  type: feedback
---

UI surface 在「跑 Playwright 截圖丟給修修看」之前，自己先走一輪視覺基本檢查。「這種很 trivial 的問題就不應該讓我來挑」(2026-05-26 digest landing edge-to-edge cards 教訓)。

**Why**：修修把美學列 first-class requirement（CLAUDE.md「美學要求」節 + [[feedback_aesthetic_first_class]]）。Playwright 我已經能用了，screenshot 我也已經拍了 — 拍完不檢查直接 ship 給修修等於把品控外包。AI slop default（edge-to-edge、無 hover、無 gutter）是禁用清單，但我自己常常先做出 default 才被抓。

**How to apply**：宣告 UI 任務 done 前必跑一輪：

1. **Page gutter**：內容區左右有沒有合理間距（≥16px 行動、≥40px 桌機）？參考 Franky probe-zone pattern `margin: 20px 40px 0` 或新的 `.digest-page` wrapper `padding: var(--sho-s-5) 40px var(--sho-s-7); max-width: 1280px; margin: 0 auto`
2. **水平溢出**：`document.documentElement.scrollWidth <= window.innerWidth`？grid item / flex item 是否漏設 `min-width: 0`？nowrap span + ellipsis 的父層有沒有 `overflow: hidden`？
3. **Card hover**：滑過去有沒有反饋（border-color → accent / bg shift）？Zoro hover 橘色塊 vs Robin 沒反饋這種不一致是禁忌（[[feedback_aesthetic_first_class]]）
4. **Empty state**：if no data, 是不是真有「無 X」訊息而不是空白卡片？
5. **Theme**：light + dark 都看一次（Playwright evaluate 切 `data-theme` 或 prefers-color-scheme）。tokens 走得對的話應該都 OK，但 hardcoded 色碼會在某 theme 露餡
6. **Long-text overflow**：標題很長 / 摘要很長 / 標籤超多時排版會不會爆？拿 vault 最長的那筆來測
7. **無 raw markdown dump**：結構化資料（score、journal、domain）有沒有獨立 chip / meta row？if 看到「片段被 markdown 整段 dump」就是失敗模式（[[feedback_digest_card_redesign]] / digest detail v1 教訓）
8. **Sho tokens only**：grep 自己寫的 CSS 有沒有 invented tokens（`--sho-space-*`、`--sho-border`、`--sho-surface`、`--sho-fg`、`--sho-muted-strong`、`--sho-link`、`--sho-surface-sunken`）— 這些 token 都不在 `tokens.css`，全是要被替換的（→ `--sho-s-*` / `--sho-line` / `--sho-bg-2` / `--sho-text` / `--sho-text-2` / `--sho-accent` / `--sho-bg-3`）

跑完 1-8 才 surface 給修修看；不要拿一張「乍看 OK」的 screenshot 當完成證明。

**範圍**：所有 Bridge UI surface（projects / digests / drafts / SEO / ask / Franky probe / Robin reader），不適用 Obsidian vault snippet 與 agent markdown 輸出。

**相關**：[[feedback_aesthetic_first_class]]、[[feedback_use_mcp_browser_for_ui_verify]]
