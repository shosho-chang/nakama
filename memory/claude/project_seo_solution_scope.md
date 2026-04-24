---
name: SEO Solution 下一個重點（keyword-research skill 之後）
description: SEO 方案範圍三用途：內容建議、現有部落格體檢、Brook compose 整合寫排行潛力草稿
type: project
created: 2026-04-18
confidence: high
originSessionId: 7a9274dd-50e3-4ba4-88fe-50199d5f4333
---
修修 2026-04-18 拍板的下一個開發重點：keyword-research skill 完成後，進入 SEO solution。

**三大用途（全部都要支援）：**

1. **內容創作建議**（當前 keyword-research skill 的主戰場）
   - 找主題 / 關鍵字 / 趨勢 → 寫什麼
   
2. **現有部落格 SEO 體檢**
   - 掃描既有文章，產出每篇的 SEO 改善清單
   - 需要 keyword volume / difficulty / SERP feature 數據 → 指向 **DataForSEO MCP 或類似付費資料源**
   
3. **Brook compose 整合（配合 style-extractor）**
   - 寫草稿時同時吃 voice profile（style-extractor 產出）+ 關鍵字/SEO 資料
   - 目標：寫出來的內容「有排行潛力」而非只是風格對
   - 需要 skill 間可組合：Brook 的 compose skill → 調用 SEO skill → 輸出 SEO-optimized draft

**Why：** 修修的商業模式需要既有部落格變現與流量，內容創作不能只寫「修修喜歡寫的」，還要寫「會被搜尋的」。現在沒人做部落格 SEO 健檢。

**How to apply（設計當下 keyword-research skill 的影響）：**
- 當前 skill 的輸出格式要考慮**下游可組合性**：純 markdown 給人讀 OK，但應同時產結構化資料（frontmatter 或 JSON block）讓 Brook / SEO audit skill 能 parse
- 不要把 DataForSEO 整合擠進當前 skill — 保持最小變動、避免 scope creep
- 下一輪 prior-art-research 要專門針對：SEO audit workflow、DataForSEO MCP、blog crawl + audit 工具

**後續任務序列：**
1. pytrends → trendspyg 遷移 PR（解阻塞）
2. keyword-research skill 化（本輪）
3. 獨立 prior-art-research: SEO audit + MCP 選型
4. SEO skill 家族設計（可能 2-3 個 skill：`seo-audit-post`、`seo-keyword-enrich`、`seo-optimize-draft`）
5. Brook compose 整合 — 寫草稿時吃 style profile + SEO skill 輸出

**狀態**：
- 2026-04-24 — prior-art 完成 ([docs/research/seo-prior-art-2026-04-24.md](../../docs/research/seo-prior-art-2026-04-24.md))
- 2026-04-24 — ADR-009 architecture frozen ([docs/decisions/ADR-009-seo-solution-architecture.md](../../docs/decisions/ADR-009-seo-solution-architecture.md))；8 個 open questions 全收斂（3 skill / GSC 主源 / DataForSEO $50 / Sonnet for audit / SEOContextV1 凍結 / cannibalization 含 phase 1 / seo-optimize-draft 推 phase 2）
- 2026-04-24 — multi-model triangulation 完成（PR #124 桌機跑 Gemini 4/10 退回 / Grok 6/10 通過 / Claude 通過）；6 個共識 blockers 消化：ADR body 改 T5（mapping 搬 `shared/schemas/site_mapping.py`）+ T6（`StrikingDistanceV1` filter 順序契約）；Revised Slice Order `seo-audit-post` 從 Slice C 延到 Phase 1.5，Slice B 縮範只做 GSC；Slice D (Brook 整合) 前移成 Slice C
- 下一步：Slice A PR（`SEOContextV1` schema + `shared/gsc_client.py` + `shared/schemas/site_mapping.py` + GSC OAuth runbook），時程由修修決定
