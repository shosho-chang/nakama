---
source_adr: ADR-009-seo-solution-architecture
reviewer_model: grok-4
elapsed_seconds: 55
review_date: 2026-04-24
---

# ADR-009-seo-solution-architecture — grok 審查

### 1. 核心假設檢驗
這份ADR基於多個未明說的假設，包括：(1) GSC API的response格式長期穩定（未考慮Google頻繁微調API的歷史）；(2) DataForSEO對health類別的search_volume隱藏限制不會改變（但Google Ads政策可能演變）；(3) VPS資源（2vCPU/4GB）足夠支撐多skills並行執行（未估算LLM調用或API爬取的峰值負荷）；(4) 修修作為單一用戶的行為模式固定（e.g., 僅用Slack觸發，不會批量請求）；(5) 所有skills的觸發詞不會與其他agents（如Zoro）衝突（未驗證全局namespace）。  
容易出錯的假設：(3) 資源假設最脆弱，多agents如Brook+Franky同時跑可能導致OOM或延遲；(2) DataForSEO政策變更會直接廢掉health垂直的enrichment邏輯，導致SEOContextV1多欄位永遠None。

### 2. 風險分析
- (a) 未被提及但會造成生產問題的風險：API quota耗盡（GSC每日限額未提，頻繁enrich會觸發）；firecrawl免費quota超支導致SERP摘要永遠None（無付費fallback）；secrets部署錯誤（如.env權限非600，導致VPS洩漏）；LLM prompt洩漏敏感數據（e.g., competitor_serp_summary含競品機密）；HITL審核延遲造成發布瓶頸（自動化目標但人為審核未優化）。
- (b) 已提及但嚴重度被低估的風險：schema drift（ADR說透過version吸收，但跨skills+Brook的遷移期會破壞生產flow，低估了測試負擔）；GSC OAuth設定（視為一次性，但修修操作錯誤可能永久阻擋Phase 1）；Claude模型成本（估$3/月，但未計入retry失敗或prompt膨脹導致的重試開支）。
- (c) 已提及但嚴重度被高估的風險：DataForSEO $50 sunk cost（ADR強調負面，但credits不朽期且用量低，實際風險微小）；3個skills觸發詞維護（ADR視為負面，但frontmatter自動路由使之易管理，高估了複雜度）。

### 3. 替代方案
更簡單的替代路徑：直接整合現成開源工具如Screaming Frog SEO Spider（免費版足夠單篇audit），取代自建seo-audit-post；或用Google的官方SEO Starter Guide腳本，避開DataForSEO儲值。作者沒選因為強調health垂直客製（但Screaming Frog支援自訂規則，客製成本低）。更穩的第三方工具：Semrush API免費tier（取代DataForSEO，無health隱藏限制）；SurferSEO免費試用整合到Brook prompt（取代seo-optimize-draft，proven在內容優化）。作者reject因成本，但Semrush免費版已proven在小規模blog，且無sunk cost風險。

### 4. 實作 pitfalls
工程師照ADR寫，最易踩坑：(1) shared/schemas/publishing.py中SEOContextV1的striking_distance.current_position confloat(ge=10.0, le=21.0)邊界邏輯—若GSC返回10.0以下但邏輯上應收錄，會被schema reject，導致enrich skill crash（坑在filter logic與schema不一致）；(2) agents/brook/compose.py的_build_seo_block helper—若未嚴格檢查seo_context None欄位，prompt會注入無效建議，破壞DraftV1輸出（e.g., cannibalization_warnings空list誤導LLM）；(3) shared/gsc_client.py的host_to_target_site mapping—無fixture test覆蓋edge case（如新site添加），導致SEOContextV1.site Literal不符，Brook compose路由錯誤；(4) seo-keyword-enrich/SKILL.md frontmatter parse keyword-research輸入—若frontmatter schema微變（未凍結），會報錯但無graceful fallback，阻斷整個flow；(5) API契約如DataForSEO client—health filter內建但未處理rate limit，造成無限retry loop吃光VPS CPU。

### 5. 缺失的視角
資安：secrets管理提了但太輕（未討論加密或rotating keys，VPS暴露風險高）；效能：未估API延遲（e.g., firecrawl爬取+Haiku摘要可能>10s，影響Slack互動）；運維：無部署runbook（e.g., Phase 1上線後如何hotfix GSC client）；法規：台灣藥事法僅在audit semantic check提，無全局compliance檢查（e.g., SEO優化可能生成違規內容）；可觀測性：structured log援引但未指定metrics（如skill執行時間、failure rate）；成本：月估$3但未計VPS電費或Claude意外spike；可測試性：fixture test提但無end-to-end integration test（e.g., skill chain）；可維護性：3 skills增加維護負擔，無deprecation計劃。

### 6. Phase 拆分建議
應拆成獨立ADR：GSC client與OAuth setup（目前混在D9/Open Items，太關鍵應單獨凍結）；cannibalization detection邏輯（~50行但影響schema，值得獨ADR討論演算法）。可延後到Phase 2+：seo-optimize-draft（非核心，Phase 1已足enrich+audit）；cron-driven整站體檢（與ADR-008重疊，延後不擋MVP）。必須Phase 1完成：SEOContextV1 schema凍結（跨skills依賴）；Brook compose整合（否則enrich無消費端）；GSC client實作（OAuth blocker）。

### 7. 結論
- (a) 整體可行性評分 6（架構清晰但假設脆弱、缺失資安/效能視角，易在生產中崩盤）。
- (b) 建議：修改後通過
- (c) 最blocking的1-2個問題：(1) GSC OAuth前置作業未有強制runbook與health check，阻擋Phase 1啟動；(2) schema與filter logic不一致（e.g., striking_distance邊界），會導致頻繁fail fast而不易debug。