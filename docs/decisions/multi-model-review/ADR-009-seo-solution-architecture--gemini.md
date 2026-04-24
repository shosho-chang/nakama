---
source_adr: ADR-009-seo-solution-architecture
reviewer_model: gemini-2.5-pro
elapsed_seconds: 51
review_date: 2026-04-24
---

# ADR-009-seo-solution-architecture — gemini 審查

好的，審查開始。

---

### 1. 核心假設檢驗

這份 ADR 基於以下未明說的假設，其中一些具備高風險：

1.  **假設 VPS 資源足夠**：文件假定在 2vCPU / 4GB RAM 的 VPS 上，同時運行多個 agent、爬蟲（firecrawl）、多個外部 API 呼叫（GSC, DataForSEO, PageSpeed）是可行的。`firecrawl` 爬取和後續的 LLM 摘要是記憶體和 CPU 密集型操作，在資源受限的環境中，同時觸發多個 skill 可能導致 OOM killer 介入或嚴重的請求壅塞。
2.  **假設外部 API 的延遲是可接受的**：`seo-keyword-enrich` skill 串聯了 GSC、DataForSEO、firecrawl 等多個網路請求。假設這些請求能在一個合理的互動時間內（例如 < 30-60 秒）完成。GSC API 的延遲並不穩定，firecrawl 爬取三個頁面也可能耗時甚久，這會嚴重影響使用者體驗。
3.  **假設 HITL 流程對延遲不敏感**：文件提到所有內容發佈前需經 HITL 審核，但沒有討論 `seo-keyword-enrich` 這個中介步驟本身的耗時。如果修修的 workflow 是「研究 -> enrich -> 寫作」一氣呵成，那麼 enrich 步驟的延遲就會成為瓶頸。
4.  **假設 `keyword-research` 的產出品質穩定且語義明確**：ADR 將 `keyword-research` 的輸出視為一個凍結的、可靠的輸入。但 LLM 產出的 `core_keywords` 和 `title_seeds` 可能存在語義模糊或品質波動，下游的 `seo-keyword-enrich` skill 並沒有對此進行驗證或清洗的機制，這會導致垃圾進、垃圾出。
5.  **假設 Health vertical 的 SEO 規則主要是數據驅動**：文件專注於從 GSC 和競品 SERP 提取數據，但台灣健康領域的 SEO 深受藥事法規影響。許多看似有流量的關鍵字是內容禁區。ADR 假設 LLM（Claude Sonnet）能夠在 `seo-audit-post` 中處理此合規性問題，這是一個非常強的假設，通常需要更明確的規則或專家知識庫。

### 2. 風險分析

**(a) 未被提及但會造成生產問題的風險**

1.  **外部 API Quota / Rate Limit 管理**：文件只提到了 GSC API 的 quota，但 PageSpeed Insights API（免費版有每日/每分鐘限制）、DataForSEO（預算消耗）和 firecrawl（免費額度）都有速率限制。缺乏一個集中的 client 或 middleware 來管理這些 API 的速率和併發請求，很容易在短時間內觸發多個 skill 時撞到上限，導致 skill 執行失敗。
2.  **狀態管理與重試的複雜性**：`seo-keyword-enrich` 是一個多步驟、多外部依賴的流程。如果其中一個 API（例如 DataForSEO）失敗，目前的 fallback 策略是「省略此欄位」。但如果 GSC API 暫時性失敗，整個 skill 的核心價值就喪失了。文件沒有定義一個可恢復的任務機制，失敗了就是失敗了，使用者需要手動重跑，體驗不佳。
3.  **LLM Prompt Injection**：`seo-audit-post` 會爬取外部 URL，`seo-keyword-enrich` 也會爬取競品 URL。這些外部 HTML 內容未經處理就可能被送入 LLM prompt（例如 `competitor_serp_summary`），存在 prompt injection 的風險，可能導致 LLM 輸出非預期內容或執行惡意指令。
4.  **成本失控**：雖然 LLM 模型選擇有成本考量，但 DataForSEO API 是按次計費。如果 agent 觸發邏輯有 bug 或被濫用，可能在短時間內燒光儲值金額。缺乏預算告警和熔斷機制。

**(b) 已提及但嚴重度被低估的風險**

1.  **跨 skill schema drift 風險**：文件提到用 `schema_version` 來緩解，但嚴重低估了維護成本。當 `SEOContextV1` 升級到 `V2`，`seo-keyword-enrich` 需要雙寫，`Brook compose` 和 `seo-optimize-draft` 需要支持 N-1 版本。在一個小團隊和快速迭代的環境中，這種跨越多個模組的同步需求極易出錯，很容易導致某個 skill 還在產生舊版 schema，而消費端只支持新版。
2.  **Phase 1 GSC client 與 ADR-008 Phase 2 可能相互等待**：這不僅是等待問題，而是所有權和實作細節的衝突。ADR-008 的批次需求（高吞吐、錯誤處理）和 ADR-009 的互動式需求（低延遲、單次查詢）對 client 的設計要求不同。把兩者綁在一個 `shared/gsc_client.py` 中，卻沒有明確定義其內部契約，會導致兩邊的工程師互相 block 或寫出一個四不像的 client。

**(c) 已提及但嚴重度被高估的風險**

1.  **3 個 skill 觸發詞需維護**：這個風險被高估了。在 Claude-based skill 系統中，frontmatter `description` 的 prompt engineering 是常態。只要描述寫得夠精確，LLM agent router 的分派能力通常足夠應付。`Do NOT trigger for` 的設計是有效的緩解措施，實際衝突的機率不高。

### 3. 替代方案

1.  **單一、非同步的 `enrichment_job` skill**：
    -   **方案**：將 `seo-keyword-enrich` 重構成一個異步任務。使用者觸發後，skill 立即返回一個 `job_id`，並在背景執行所有 API 呼叫。完成後透過 WebSocket 或 Slack 通知使用者。
    -   **優點**：解決了同步 API 呼叫的延遲問題，改善使用者體驗。可以更好地管理資源，例如使用一個背景 worker queue 來序列化執行，避免同時爬取多個網站導致 VPS 崩潰。也為重試和狀態管理提供了基礎。
    -   **作者為何沒選**：可能追求架構簡單性，避免引入任務隊列（如 Celery/RQ）的複雜度。但對於超過 5-10 秒的任務，同步執行是不可行的。

2.  **使用 `SerpAPI` 或 `ValueSERP` 取代自建 `firecrawl` + LLM 摘要**：
    -   **方案**：付費使用結構化的 SERP API，它們會返回 JSON 格式的搜尋結果，包含標題、摘要、排名等。
    -   **優點**：比 `firecrawl` 更穩定、更快速，且不易被搜尋引擎封鎖。直接獲得結構化數據，省去了爬取 HTML 並用 LLM 摘要的步驟和成本，結果更確定。
    -   **作者為何沒選**：可能是出於成本考量（`firecrawl` 有免費額度）。但自建方案的穩定性和 LLM 摘要的成本（token + 延遲）被低估了。

3.  **Pydantic schema 集中管理，而非分散在 `publishing.py`**：
    -   **方案**：建立一個獨立的 `nakama_schemas` Python package，所有 ADR 定義的 schema（不論是 publishing, gsc, 還是 seo context）都放在裡面，並進行版本控制。
    -   **優點**：從根本上解決 schema drift 問題。所有 agent/skill 都依賴同一個版本的 schema package。升級 schema 變成一個有意識的、統一的動作（升級 package 版本），而不是在多個地方手動同步。
    -   **作者為何沒選**：可能認為目前專案規模小，單一 `shared` 資料夾足夠。但隨著 agent 增多，這個問題會迅速惡化。

### 4. 實作 pitfalls

如果工程師照此 ADR 實作，最可能踩到以下幾個坑：

1.  **`SEOContextV1.site` 的 mapping 邏輯** (`D3`)：
    -   **問題點**：`host_to_target_site("shosho.tw") → "wp_shosho"` 這個 mapping 被放在 `shared/gsc_client.py` 中。這是一個錯誤的關注點分離。`gsc_client` 的職責是與 GSC API 交互，它不應該知道上層應用（"wp_shosho"）的內部命名。
    -   **結果**：這個 mapping 邏輯會洩漏到 GSC client 層，使其與 `Brook` agent 的內部實現耦合。未來如果新增一個站點，不僅要改 `config`，還可能要改 `gsc_client.py`，違反了單一責任原則。這個 mapping 應該在 `seo-keyword-enrich` skill 的業務邏輯層完成。
2.  **`KeywordMetricV1.sources` 的不一致性** (`D3`)：
    -   **問題點**：schema 定義了 `sources: list[Literal[...]]` 來追蹤數據來源，但沒有強制要求填寫它的邏輯。例如，當 `search_volume` 來自 DataForSEO 時，工程師是否記得要把 `"dataforseo"` 加入 `sources` 列表？
    -   **結果**：這個欄位極易被遺忘或填寫不一致，導致其失去 debug 價值。應該在 `seo-keyword-enrich` 的程式碼中設計一個 builder pattern，每次從某個來源填充數據時，自動將 source 加入列表，而不是依賴工程師手動記憶。
3.  **`compose_and_enqueue` 的 `seo_context` 參數** (`D5`)：
    -   **問題點**：將 `SEOContextV1` 物件直接作為函式參數傳遞。這個物件可能很大（`competitor_serp_summary` 可達 3000 字元）。
    -   **結果**：如果系統的內部呼叫是基於 RPC 或序列化（例如，未來 skill 可能會被拆分成獨立的 microservice），傳遞大型物件會有效能問題。更穩健的模式是傳遞一個 context 的引用（例如 ID），讓 `compose` 函式自己去指定的存儲（如 Redis 或檔案系統）中讀取，儘管在目前架構下這可能過度設計，但應意識到此風險。
4.  **`CannibalizationWarningV1` 的實作** (`D3`)：
    -   **問題點**：文件輕描淡寫地說「~50 行 Python」，但從 GSC API 返回的 query × URL 數據中準確偵測 cannibalization 相當微妙。如何定義「競爭」？是同一個 query 有多個 URL 進入前 50 名就算嗎？`recommended_primary_url` 的推薦邏輯是什麼？是點擊最高的那個嗎？
    -   **結果**：工程師可能會寫出一個過於簡陋的偵測邏輯，產生大量誤報（false positives），導致這個功能產生的警告對修修來說是噪音而非信號。ADR 應提供更明確的業務規則。

### 5. 缺失的視角

1.  **可觀測性 (Observability)**：嚴重不足。文件提到了結構化日誌，但完全沒有討論如何監控這套複雜的 SEO skill 家族。
    -   **儀表板**：沒有定義關鍵指標（KPIs）。例如：各 skill 的成功/失敗率、平均執行時間、各外部 API 的延遲與錯誤率、DataForSEO 的費用消耗。沒有這些，運維將是盲人摸象。
    -   **追蹤 (Tracing)**：`seo-keyword-enrich` 呼叫了多個下游服務，這是一個典型的分散式追蹤場景。缺乏 OpenTelemetry 之類的追蹤機制，一旦 enrich 變慢或失敗，很難定位是哪個環節（GSC? firecrawl? DataForSEO?）出了問題。
2.  **可測試性 (Testability)**：講得太輕。
    -   **Mocking 策略**：文件沒有說明如何測試依賴眾多外部 API 的 skill。需要一個清晰的 Mocking/VCR 策略，否則 CI/CD 會非常緩慢且不穩定。`shared/*_client.py` 的設計必須考慮到這一點，例如通過依賴注入來替換 client 實例。
    -   **端到端測試**：只提到了單元測試和 snapshot test。但 `keyword-research` -> `seo-keyword-enrich` -> `Brook compose` 這個鏈條的整合測試完全沒提。如何確保 `SEOContextV1` 在真實的跨 skill 傳遞中不會出錯？
3.  **運維 (Operations)**：幾乎為零。
    -   **失敗處理與告警**：當 GSC Auth 失效、DataForSEO 餘額用盡、firecrawl 被 ban 時，系統如何通知維護者？目前看來只會在 skill 執行時報錯，沒有主動的健康檢查或告警機制。
    -   **配置管理**：`config/target-keywords.yaml` 的 ownership 歸 ADR-008，但 ADR-009 的 skill 依賴它。如果修修改了這個 YAML，如何觸發更新？是否有 hot-reload 機制？
4.  **資安 (Security)**：除了 secrets 管理，其他方面考慮不足。
    -   **依賴安全**：提到了 `firecrawl`，但沒有討論其 npm 依賴樹的安全性。對於一個會爬取外部內容的工具，其安全性至關重要。
    -   **輸出消毒**：`seo-audit-post` 產生的 markdown report 和 `seo-keyword-enrich` 的 `competitor_serp_summary`，如果直接在前端或 Slack 中渲染，而內容又來自被爬取的惡意網站，可能存在 XSS 風險。所有來自外部源的內容在輸出前都需要消毒。

### 6. Phase 拆分建議

目前的 Phase 1 過於龐大且風險集中，應重新拆分。

**必須 Phase 1 完成 (核心價值 MVP)**：

1.  **Slice A：基礎建設與契約**
    -   `SEOContextV1` schema 定義與 `nakama_schemas` package 的建立。
    -   `shared/gsc_client.py` 的**互動式查詢**部分，包含健壯的 OAuth 處理和 runbook。
    -   所有外部 API client (`dataforseo`, `pagespeed`) 的骨架，包含 Mocking 接口。
2.  **Slice B：單一、高價值 skill `seo-keyword-enrich`**
    -   只整合 GSC (striking distance, cannibalization) 和 `keyword-research` 輸入。這是最有價值的部分。
    -   **暫不整合 DataForSEO 和 firecrawl**，先用假數據或 `None` 填充。
    -   輸出 `SEOContextV1` 到檔案。
3.  **Slice C：Brook compose 整合**
    -   實作 `compose_and_enqueue` 的 `seo_context` 參數和 `_build_seo_block`。
    -   建立一個手動工作流程：修修執行 `seo-keyword-enrich`，拿到 `SEOContextV1` 檔案，再手動餵給 Brook。

**可延後到 Phase 2+ (漸進增強)**：

1.  **`seo-audit-post` skill**：這是一個獨立的功能，與核心的「研究->寫作」流程解耦，完全可以延後。其複雜性（25+10條規則、PageSpeed API）使其成為一個獨立的 epic。
2.  **DataForSEO 整合**：在驗證完 GSC 數據的價值後，再引入 DataForSEO 作為補充數據源。這也延後了 $50 的 sunk cost。
3.  **firecrawl SERP 摘要整合**：這是最高風險、最低確定性的部分（資源消耗、LLM 摘要品質不穩）。應延後到核心流程穩定後再考慮。
4.  **`seo-optimize-draft` skill**：這是對既有流程的優化，明確屬於 Phase 2。
5.  **異步化改造**：當 `seo-keyword-enrich` 的同步執行時間被證實為痛點時，再啟動異步任務隊列的改造。

### 7. 結論

**(a) 整體可行性評分：4/10**

理由：方向正確，解決了真實問題。但 ADR 對於系統在資源受限環境下的複雜性、風險和運維現實過於樂觀。目前的設計像是在一台擁有無限資源和穩定網路的開發機上構思的，而非在一個真實的、脆弱的 VPS 上。Phase 1 的範圍過於龐大，試圖一次性解決太多問題，會導致交付延期和低品質的結果。

**(b) 建議：退回重寫**

需要重新審視 Phase 1 的範疇，並在可觀測性、可測試性和運維方面進行大量補充。ADR 應更關注如何交付一個雖小但穩定、可監控的核心價值，而不是一個功能全面但脆弱的系統。

**(c) 最 blocking 的 1-2 個問題**

1.  **資源與延遲的現實評估**：必須在目標 VPS 環境上對 `firecrawl` 和多個 API 同時調用的資源消耗（RAM/CPU）和總延遲進行基準測試（benchmark）。如果一個核心 skill `seo-keyword-enrich` 的 P95 延遲超過 60 秒，整個同步執行的架構就需要被推翻，這是最根本的架構決策點。
2.  **Phase 1 範疇過大**：必須將 Phase 1 縮減至僅包含 GSC 數據的 `seo-keyword-enrich` 和 Brook 的整合。`seo-audit-post` 和其他數據源（DataForSEO, firecrawl）必須移出，否則 Phase 1 註定無法在「2-3 週」內完成。先證明核心的 GSC -> `SEOContextV1` -> Brook 寫作流程能穩定運作，才是最重要的。