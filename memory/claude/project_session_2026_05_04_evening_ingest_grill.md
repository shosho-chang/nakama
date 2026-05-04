---
name: 收工 — 2026-05-04 晚 QA 撞 Stage 1 ingest 結構問題 + 5 grill 待答
description: PR #346/347/348/349 全 merged + QA Phase 0 全綠 / Phase 1.x 撞 /scrape-translate 對學術 JS site 不夠 → 修修要求 review Stage 1 ingest pipeline；不裝 Playwright runtime；5 grill 問題等下個 session 答
type: project
created: 2026-05-04
---

5/4 晚 從 QA 開跑 → 撞學術文章 ingest 兩條 path 分裂的結構問題 → 拍板下一波重設計 Stage 1。**新對話起手**：先答下方第 5 節 5 個 grill 問題凍 scope。

## 1. 今天 4 PR 全 ship

- PR #346 (5/4 下午收工 memo) merged
- PR #347 (PowerShell 回收桶 allow prefix 嚴格對齊規則) merged
- PR #348 (Line 2 annotation QA acceptance plan) merged
- PR #349 (Firecrawl SDK v2 migrate `scrape_url`→`scrape` + `only_main_content=True`) merged

main HEAD 對齊；無 in-flight branch；無 background task。

## 2. QA 跑到哪

✅ Phase 0 全綠：env 對 / imports OK / 116 tests pass / VAULT_PATH=E:/Shosho LifeOS / KB/Annotations/ clean baseline / uvicorn 可起
🔄 Phase 1.x 中斷：撞 /scrape-translate 對學術 JS site 不夠

實測證據：
- Lancet eClinicalMedicine `PIIS2589-5370(25)00676-5/fulltext` → Firecrawl `only_main_content=True` 回 958 字 chrome（4 種參數組合都同樣 958 字）
- BMJ Medicine `bmjmedicine.bmj.com/content/5/1/e001513` → 抓到半身 content + 圖片 0
- Playwright Claude Code plugin empirical：Lancet article title 抓得到（partial — MCP backend 卡住沒撈 body，但 title 證明 full browser context 看得到 article 主體）

## 3. 戰略 reframe

問題不是 scraper bug，是**結構性兩條 ingest path 分裂**：

| | Robin PubMed digest | /scrape-translate |
|---|---|---|
| 入口 | PMID（cron / 修修指定） | 任意 URL |
| Fallback | **5 層** OA：efetch → PMC NCBI → **Europe PMC**（VPS 友善）→ Unpaywall → publisher HTML（PR #94, BMJ/PLOS/eLife 友善） | **3 層** 笨：Trafilatura → Readability → Firecrawl 整頁 |
| DOI-aware | ✅ | ❌ |
| 圖片 | ✅ pymupdf4llm + `download_markdown_images` | ❌ 完全沒抽 |
| 翻譯 input | OA PDF → pymupdf4llm 解析 | 前端 chrome / 半身 HTML |
| BMJ Medicine 該走哪 | DOI `10.1136/bmjmed-...` → Europe PMC 應該有 | 走 scrape，回半身 |

修修的 BMJ Medicine 文章本來就 OA 在 PMC，因為入口是 URL 不是 PMID 跑到笨 path。

## 4. 拍板決策

- **不裝 Playwright runtime**：Lancet 走 Europe PMC 應該夠；agent web automation（YouTube 留言）等 Chopper 真開發再綁 slice 裝（[feedback_avoid_one_shot_summit](feedback_avoid_one_shot_summit.md) + [feedback_todo_needs_use_case](feedback_todo_needs_use_case.md)）
- **Stage 1 ingest engine unify**：把 5 層 OA fallback engine（已在 `agents/robin/pubmed_fulltext.py`）抽成 URL-first 通用 ingest engine，/scrape-translate 退位 last resort
- **圖片 first-class**：pymupdf4llm（PubMed digest 已用）+ `download_markdown_images`（PR #94）同套用到 URL ingest
- **Status 透明**：reader header 顯示「OA from Europe PMC ✓」/「publisher HTML ⚠️」/「scrape last resort ❌」修修一眼判斷品質
- **out of scope**：Zotero（訂閱內容下一階段，跟 OA path 並行）/ Playwright runtime / 付費 scrape service（ScrapingBee 等）

## 5. 5 個 grill 問題等修修答（**新對話起手第一件事**）

凍 scope 用，答完我寫 PRD doc 仿 PRD #337 結構：

**Q1（用量）**：每週 ingest 幾篇？10 / 30 / 100+？（決定要不要做 batch、cache、image lazy load）
**Q2（source 比例）**：PubMed-indexed % vs 直接 URL % vs 訂閱 %（粗估即可）
**Q3（圖片必要性）**：學術圖（forest plot / Kaplan-Meier 曲線）對心得是 nice-to-have 還是 critical？決定圖抽要不要 first class
**Q4（失敗 tolerance）**：5 層全失敗時可接受手動下載 PDF 丟 Inbox/kb/？還是 next priority 必須再加層（e.g. ScrapingBee 付費）
**Q5（既有 PubMed digest 路徑要不要受影響）**：unify 後 PubMed digest 也走新 engine 嗎？還是 PubMed 維持現狀、URL 走新的（兩條 code path）

## 6. 接續流程（新對話）

1. 修修答上面 5 題 → 凍 scope
2. 我寫 PRD doc 仿 PRD #337 結構（user stories + 實作決策 + slice 拆法）
3. `/to-issues` 拆 slice 開 GH issue + 用 `/to-prd` 上 GitHub
4. ship 3-4 個 PR

## 7. 今日 incidental learning

- ANTHROPIC_API_KEY leak via `grep ^ANTHROPIC_API_KEY .env` → 修修 rotate；feedback memory 補 agent grep/cat 端禁忌 + 4 條 ✅/❌ 範本（[feedback_no_secrets_in_chat](feedback_no_secrets_in_chat.md)）
- Firecrawl SDK 4.x API churn — v1 `FirecrawlApp.scrape_url(params=...)` 砍了，v2 `Firecrawl.scrape(formats=...)`；test mock 加 `spec=Firecrawl(api_key="dummy-spec")`（spec=instance 因為 class delegation pattern）防下次 SDK churn silent leak
- PowerShell allow prefix 嚴格對齊（[feedback_powershell_allow_exact_prefix](feedback_powershell_allow_exact_prefix.md)）— `-NoProfile` / `foreach` / Python `send2trash` 都會破 match
- MCP playwright backend 卡住現象（first navigate 成功、後續 retry 全 closed）— 已記、未開 issue（不影響工作流程）

## Reference

- 早上的 7 層架構凍結：[project_session_2026_05_04_pipeline_arch](project_session_2026_05_04_pipeline_arch.md)
- 下午 PRD #337 三 slice ship：[project_session_2026_05_04_pm_annotation_ship](project_session_2026_05_04_pm_annotation_ship.md)
- Stage 1 既有設計（將 unify 的 base）：[project_robin_pubmed_digest](project_robin_pubmed_digest.md) + [reference_oa_fulltext_apis](reference_oa_fulltext_apis.md) + [project_zotero_integration_plan](project_zotero_integration_plan.md)
- 7 層 anchor：[CONTENT-PIPELINE.md](../../CONTENT-PIPELINE.md) Stage 1
- QA doc：[docs/plans/2026-05-04-line-2-annotation-qa.md](../../docs/plans/2026-05-04-line-2-annotation-qa.md)
