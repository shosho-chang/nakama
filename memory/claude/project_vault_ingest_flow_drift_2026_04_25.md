---
name: 2026-04-25 vault ingest 流程盤點：想像 vs 實作差異
description: 修修腦海中的 5 條 ingest 流程 vs 現有實作落差盤點，含三條 frontmatter schema drift；下一波 ingest 工程的 baseline
type: project
originSessionId: c6399fca-d109-4f35-807f-e564c7010f0c
---
2026-04-25 對話盤點修修腦海工作流 vs nakama 實作現狀。

## 5 條 ingest 流程對齊度

| # | 流程 | 對齊度 | 主要差異 |
|---|------|--------|---------|
| 1 | Robin Daily Digest → 點全文閱讀 | 80% | 非 OA paper 沒全文路徑（Lancet 等付費期刊本來就抓不到） |
| 2 | Reader 翻譯功能 | 40% | 修修以為 Reader 內有「按下去翻譯」按鈕；實作是 ingest 時預翻譯 + Reader toggle 雙語顯示。Engine 是 **Claude Sonnet** 不是 Opus 4.7 |
| 3 | 自製 Web Scraper 抓網頁 | 70% | 後端 Trafilatura + Firecrawl 都有；**缺 Chrome plugin 一鍵剪** |
| 4 | EPUB / Word 電子書 | 10% | **完全沒實作** |
| 5 | 整本教科書 ingest | 0% | **完全沒實作**（見 [project_textbook_ingest_design_gap.md](project_textbook_ingest_design_gap.md)）|

## Schema drift（三條 frontmatter 對不齊）

| 來源 | 路徑 | Frontmatter 形狀 |
|------|------|-----------------|
| Obsidian Web Clipper | `Inbox/kb/*.md` | `title / source / author（亂） / tags=[clippings]` |
| Robin Daily Digest | `KB/Wiki/Sources/pubmed-{pmid}.md` | `pmid / journal / quartile / sjr / scores / type=paper_digest`，**沒有** content_nature / lang / doi |
| scrape-translate（雙語） | reader pipeline 寫的 | 另一種 schema |

**LifeOS CLAUDE.md §4 規範的「Source Summary」schema 跟以上三條都對不齊**。

## ingest 觸發時機（修修問過）

| 流程 | 觸發 | 是否翻譯 |
|------|------|---------|
| Daily digest cron | 自動（UTC 21:30 / 台北 05:30）| ❌ 只寫 metadata |
| `/scrape-translate?url=...` | user 主動貼 URL | ✅ on-demand |
| `/pubmed-to-reader?pmid=...` | user 主動點 daily digest 連結 | ✅ on-demand（只 OA paper） |

**修正錯誤直覺**：早上看 Robin pick 不會自動有雙語版，要自己點才跑翻譯（30-60 秒等待）。

## 下一波 ingest 工程缺口（按優先序）

| 缺口 | 大小 | 規劃狀態 |
|------|------|---------|
| Daily digest 補 `content_nature` / `lang` / `doi` 欄位 | 小 | 應該收，schema drift 治本 |
| Reader 內按需翻譯按鈕（vs 預翻譯設計分歧）| 中 | 待設計討論 |
| Translator A/B 試 Opus 4.7（cost vs quality）| 小 | 一篇就能驗 |
| Annotation → Source page append「我的筆記」 | 中 | [project_bilingual_reader_design.md](project_bilingual_reader_design.md) P3 |
| Chrome Extension 一鍵剪 → thousand_sunny | 中-大 | 後端 API 都有，缺 plugin shell |
| EPUB parser（中文帶圖）| 中 | 完全缺 |
| Word (.docx) parser | 小-中 | 完全缺 |
| 整本書 ingest workflow | 大 | [project_textbook_ingest_design_gap.md](project_textbook_ingest_design_gap.md) |
| frontmatter schema 統一 | 中 | 跨三條 ingest 對齊 |
| Inbox/kb 自動 → Robin pipeline | 小 | 沒實作（Web Clipper 進來後不自動處理）|

## 桌機 / VPS 分工

按 [feedback_compute_tier_split.md](feedback_compute_tier_split.md)：
- **桌機本地**：Docling / EPUB parser / Word parser / 整本書 ingest / 本地 LLM batch
- **VPS**：daily digest cron / scrape-translate Trafilatura 輕量 / agent 對話 / Bridge UI

## 視覺化

修修打算把整個 ingest 流程畫成 IA diagram：
- 第一段 mermaid（驗資訊）—— 對話內已給範本
- 第二段 Claude Design handoff（美學迭代）—— 對話內已給 prompt 範本
- 落地：`docs/diagrams/vault-ingest-flow.md`（待修修確認資訊正確再 commit）

## 此份 memory 的角色

下一波 ingest 工程的 baseline。新功能設計時讀這份確認：
1. 沒有重複造現有的（schema / pipeline / endpoint）
2. 沒有踩既有的 drift
3. 桌機 / VPS 落點正確
