---
name: 不跟 Zotero 在 web capture quality 上重競
description: DIY URL scrape 永遠贏不了 browser-extension snapshot；缺的是橋（Zotero → Obsidian），不是 capture 重做
type: feedback
created: 2026-05-05
---

學術文章 / 訂閱內容 ingest **不要做 DIY URL scrape**，capture quality 永遠贏不了 Zotero browser-extension snapshot。

**Why:** 2026-05-05 早 QA 實測：Stage 1 ingest 五層 OA fallback 全跑通，Nature 文章從 Europe PMC 抓 PDF 解析 112K 字元，pipeline `status=ready` 但**修修讀起來 quality 不滿意**。Zotero browser-extension snapshot 四大根本優勢 DIY 無法複製：

1. 用既有 browser session（cookies）解 paywall，不需自己造 publisher login
2. 全 DOM + CSS + asset，渲染後再存，不靠 PDF 解析失真
3. 1000+ 站台 community-maintained translators，metadata 抽取比正則健壯
4. ToS 風險轉嫁給「使用者主動點存檔」，不是 cron 批次

DIY scrape 三大不可克服劣勢：
- 每家 publisher 不同 → 永遠補不完 edge case
- PDF 解析劣化（公式 / 表格 / 圖 / 排版 / 雙欄）
- HTML scrape 受 paywall / lazy load / JS render 影響

**How to apply:**

- 看到「URL → KB markdown」需求 → 第一反應**走 Zotero**，不要 propose DIY scrape
- 既有 Stage 1 ingest 5 slice（PR #352-356）留作 escape hatch（沒入 Zotero 的內容），不再投資擴充
- 真正要蓋的是 **Zotero → Obsidian sync 這座橋**（既有 plan Phase B/C，2026-05-05 升 primary）
- 翻譯 / annotation / search / 重組 是**橋的下游**，不是 ingest 重做
- 翻譯特例：在 Robin Reader 對 sync 進來的 clean MD 做翻譯產獨立對照頁，**不污染原檔**（解 沉浸式翻譯 inject DOM 污染 Zotero snapshot 的痛點）

**例外 — 仍可走 DIY**：
- 完全無法存 Zotero 的內容（YouTube transcript、podcast、社群 post）
- 已自動化 batch ingest（PubMed digest cron — 已上線 PR #94 publisher HTML fallback Free 標 OA）

**相關**：
- [project_session_2026_05_05_zotero_pivot.md](project_session_2026_05_05_zotero_pivot.md) — pivot 決定來源
- [project_zotero_integration_plan.md](project_zotero_integration_plan.md) — primary path plan
- [feedback_structural_vs_functional_validation.md](feedback_structural_vs_functional_validation.md) — pipeline status=ready ≠ user-acceptable quality 的廣義 lesson
