---
source_adr: ADR-005-publishing-infrastructure
reviewer_model: gemini-2.5-pro
elapsed_seconds: 43
review_date: 2026-04-22
---

# ADR-005-publishing-infrastructure — gemini 審查

好的，審查開始。

---

### 1. 核心假設檢驗

這份 ADR 基於以下未明說的假設，其中一些風險較高：

1.  **WordPress REST API 穩定性與效能假設**：文件假設 WP REST API 在現有 2vCPU/4GB RAM 的 VPS 上，面對 Agent 自動化的高頻次呼叫（建立文章、上傳媒體、更新 SEO meta）時，依然能穩定回應。這個假設在尖峰時段或 Brook 產生大量草稿時可能失效，導致 API timeout 或 MySQL 崩潰。
2.  **外掛 API 契約不變假設**：文件假設 SEOPress Pro 的 `seopress/v1` endpoint 在未來版本中會保持向後相容。外掛更新頻繁，其非官方核心的 API 契約是脆弱的，一次更新就可能破壞 Usopp 的發布流程。
3.  **Gutenberg Block HTML 純淨度假設**：文件假設 Brook 產出的 Gutenberg HTML 是「乾淨」且永遠相容的。但 LLM 產出的 HTML 可能夾帶不可見的字元、未閉合的標籤、或不符合 Gutenberg 規範的註解格式，導致前端渲染錯位或編輯器載入失敗。
4.  **HITL 流程效率假設**：文件假設「修修」有足夠的時間和意願在 Bridge 介面中完成審批、選擇 slug、手動上傳並關聯特色圖片。如果草稿產出速度遠大於人工審批速度，這個 HITL 流程會成為瓶頸，而非品管機制。
5.  **內容模型單一性假設**：文件假設所有文章都適用於單一的 Gutenberg 內容模型。但對於需要複雜排版（如：多欄位比較表、互動式圖表）的科普文章，純 Gutenberg blocks 可能表達能力不足，導致人工返工。

### 2. 風險分析

**(a) 未被提及但會造成生產問題的風險**

1.  **原子性與狀態管理失敗**：Usopp 的發布流程（`建 post` -> `寫 SEO` -> `publish`）並非原子操作。如果在 `建 post` 之後、`寫 SEO` 之前失敗（例如 SEOPress API 暫時失效），系統會留下一篇沒有 SEO meta 的草稿。文件沒有定義重試邏輯、狀態回滾或錯誤補償機制，這會產生大量「半成品」垃圾資料。
2.  **API Rate Limiting 與安全性**：文件未提及對進來的 REST API 請求做速率限制。自動化 Agent 可能因 bug 或重試風暴產生大量請求，耗盡伺服器資源或觸發主機商的 DoS 保護。此外，依賴 Application Password 進行認證，若密碼洩漏，整個網站內容的控制權將暴露。
3.  **快取失效（Cache Invalidation）的複雜性**：天真地認為「讓 LiteSpeed plugin 自己偵測」是不可靠的。透過 REST API 更新文章，特別是分步更新（先建 post，再更新 meta），不一定能 100% 觸發所有相關快取（頁面快取、對象快取、CDN 快取）的精準清除。這會導致用戶看到舊版內容或不完整的 SEO 資訊。
4.  **環境不一致性**：ADR 描述的是生產環境的盤點結果，但完全沒有提及開發或預備環境（Staging）。在一個沒有隔離環境的系統上直接開發和部署 Agent，極易因一次錯誤的 API 呼叫而污染或損毀生產資料（192 篇既有文章）。

**(b) 已提及但嚴重度被低估的風險**

1.  **資源競爭**：`兩個 site 共用一個 MySQL instance + 4 GB RAM` 的風險被嚴重低估。這不僅是「大量 publish 時可能觸發 RAM 警戒」。一個資源密集型的 Agent 操作（如 Robin 的知識庫 research）就可能鎖住資料庫，導致兩個網站同時癱瘓。這是一個單點故障（SPOF）架構，風險極高。
2.  **Tag 品質**：`497 個 tag 未整理會影響 Brook 的 tag 選擇品質`，這個問題比聽起來嚴重。這不只是「選擇品質」問題，而是「模型污染」問題。如果讓 Brook 從混亂的標籤池中學習或選擇，它會產生更多無意義、重複或錯誤的標籤，加速內容熵增，最終需要代價高昂的人工清理。

**(c) 已提及但嚴重度被高估的風險**

無。此份 ADR 中提及的風險都實際存在且評估合理，沒有被高估的項目。

### 3. 替代方案

1.  **Headless CMS + 靜態網站產生器**：與其直接操作一個裝滿外掛、狀態複雜的 WordPress，更穩健的作法是將 WordPress 作為 Headless CMS 使用。Brook 將內容寫入 WP，但前端由 Astro 或 Next.js 這類靜態網站產生器（SSG）生成。
    *   **優點**：前端速度極快、安全性高（無動態執行環境）、伺服器負載極低、內容與表現完全分離。
    *   **作者為何沒選**：可能是為了保留「修修」熟悉的 WordPress 後台編輯體驗以及 Bricks Theme 帶來的視覺化編輯能力。但這份 ADR 的核心是自動化，對人工編輯的依賴應逐漸降低。
2.  **專業級第三方 Headless CMS**：Contentful、Sanity、Storyblok 等專業 Headless CMS 提供了更穩定、功能更強大的 API、版本控制、內容模組化能力。
    *   **優點**：免去自架設 WordPress 的維運負擔，API 文件和 SDK 完善，系統穩定性由專業廠商保證。
    *   **作者為何沒選**：成本考量，且需要遷移現有的 192 篇文章。但考慮到維運一個脆弱的 VPS 的隱形成本，長期來看未必更貴。
3.  **使用 WordPress XML-RPC API**：雖然 REST API 是趨勢，但古老的 XML-RPC API 在某些情況下更適合腳本操作，因為它通常是單一 endpoint 且功能集中。
    *   **優點**：可能在某些批量操作上更簡單。
    *   **作者為何沒選**：REST API 更現代、更標準化，且 SEOPress 等新外掛只為 REST API 提供支援。這個選擇是合理的。
4.  **第三方自動化工具取代自建 Usopp**：Zapier 或 Make.com (Integromat) 已經有成熟的 WordPress 整合模組。
    *   **優點**：免去開發和維護 `wordpress_client.py` 的工作，錯誤處理和重試機制相對成熟。
    *   **作者為何沒選**：可能是為了保持技術棧的統一性（純 Python）、避免對第三方平台付費和產生依賴、以及追求更客製化的流程。

### 4. 實作 pitfalls

如果工程師照這個 ADR 寫，最容易踩到以下坑：

1.  **`shared/wordpress_client.py` 的認證與錯誤處理**：
    *   **Pitfall**: 文件只提到 Application Password，但沒說如何安全地存儲和注入。工程師很可能將其硬編碼在 config 檔或直接寫在 `os.environ`，這在共享伺服器環境中極不安全。
    *   **Pitfall**: `retry` 邏輯如果寫得太簡單（如 `for i in range(3): ...`），將無法應對不同類型的 HTTP 錯誤。例如，`400 Bad Request` 不應該重試，`429 Too Many Requests` 需要帶有 `Retry-After` header 的指數退避，`503 Service Unavailable` 則需要立即重試。一個通用的 `retry` 是不夠的。
2.  **Usopp 的發布流程**：
    *   **Pitfall**: `POST /wp/v2/posts` 創建草稿後，本地會得到一個 post ID。如果後續的 `POST /seopress/v1/posts/{id}` 失敗且重試也用盡，這個 ID 就成了孤兒。ADR 沒有定義一個持久化的任務佇列或狀態機來追蹤每個發布任務的進度。工程師最終會寫出一堆分散的 `try...except`，而無法保證流程的最終一致性。
3.  **Brook 的 `content_html` 產出**：
    *   **Pitfall**: 文件中的 `content_html` 範例過於簡單。當內容包含列表、引言、圖片、程式碼塊等複雜區塊時，LLM 輸出的 HTML 註解 (`<!-- wp:xxx -->`) 很可能格式錯誤或巢狀結構不對，導致 Gutenberg 編輯器直接報錯「This block contains unexpected or invalid content」。必須要有嚴格的 HTML 清理和驗證層。
4.  **`config/style-profiles/{category}.yaml`**：
    *   **Pitfall**: 文件定義了這個路徑，但沒有說明其 schema。工程師無從得知這個 YAML 該包含哪些鍵值（是 prompt 的一部分？還是結構化參數？），會導致每個 Agent 的實作出現偏差。

### 5. 缺失的視角

1.  **可觀測性 (Observability)**：完全缺失。
    *   **日誌 (Logging)**：哪個 Agent、何時、對哪個 post ID、執行了什麼操作、結果如何？沒有結構化日誌，一旦出問題（如某篇文章的 SEO meta 被錯誤覆蓋），根本無法追蹤。
    *   **監控 (Monitoring)**：除了提到 Franky 會監控 RAM，但監控什麼？API 平均延遲、錯誤率（HTTP 4xx/5xx）、任務佇列深度、MySQL 連線數等關鍵指標都未被定義。
    *   **追蹤 (Tracing)**：一個發布請求從 `Bridge` 到 `Usopp` 再到 WordPress 的完整生命週期是個黑盒子。
2.  **可測試性 (Testability)**：幾乎為零。
    *   `shared/wordpress_client.py` 如何進行單元測試？需要 mock 掉整個 WordPress REST API。
    *   Usopp 的發布流程如何進行整合測試？需要一個可拋棄的 WordPress 實例（例如用 Docker 啟動）。如前所述，沒有 Staging 環境，直接在生產環境測試是災難。
3.  **維運 (Operations)**：考慮不足。
    *   **部署**：Agent 的程式碼如何部署到 VPS？是手動 `git pull` 還是有 CI/CD 流程？
    *   **備份與還原**：除了 `wpvivid-backup-pro` 備份網站檔案和資料庫，Agent 系統本身的狀態（如任務佇列、設定檔）如何備份？如果 VPS 毀損，如何快速還原整個 Nakama 系統？
    *   **秘密管理 (Secret Management)**：API 金鑰、Application Password 這些敏感資訊如何管理？直接寫在環境變數中是常見但不安全的做法。應使用 Vault 或類似工具。
4.  **成本**：只考慮了 VPS 硬體成本，忽略了：
    *   **LLM API 成本**：Brook, Zoro, Robin 等 Agent 的運作需要大量呼叫 LLM API，這部分成本可能遠超硬體費用。
    *   **人力維運成本**：一個脆弱、缺乏可觀測性的系統，將會耗費大量時間進行手動 Debug 和修復。

### 6. Phase 拆分建議

當前 Phase 1 的範圍過於龐大且耦合。

**必須在 Phase 1 完成的：**

1.  **核心發布流程的穩定化**：Brook 產生草稿 -> Usopp 發布到 WordPress（`status=draft`）。這一步必須包含完整的狀態管理、重試邏輯和可觀測性（日誌）。
2.  **建立隔離的 Staging 環境**：這是所有開發工作的先決條件，不可妥協。
3.  **`shared/wordpress_client.py` 的基礎建設**：包含認證、基礎的 post/media 操作，以及可測試的 mock 介面。
4.  **HITL 核心功能**：修修能在 Bridge 看到草稿列表並批准。

**可以延後到 Phase 2+ 的：**

1.  **SEOPress 整合**：可以先手動設定。在核心發布流程穩定前，自動化 SEO 是次要的。應拆分為獨立 ADR，專門討論其 API 的脆弱性和錯誤處理。
2.  **自動化 Tagging**：Phase 1 應完全禁止 Brook 新增或關聯 Tag。讓修修手動加。在 Tag 整理策略（另一個 ADR）出來前，自動化只會製造混亂。
3.  **Featured Image 自動化**：目前的 Phase 1 方案（人工上傳並關聯 ID）已經是個合理的 MVP。Phase 2/3 的自動搜圖/產圖可以延後。
4.  **Style Profile**：Phase 1 可以先用一個通用的 `default.yaml`。精細化的風格調整是優化，不是核心功能。
5.  **`shared/fluent_client.py`**：完全不應該出現在這個 ADR 的範疇內，應在 ADR-008 中再討論。

**應拆分成獨立 ADR 的：**

1.  **ADR-XXX: Nakama Observability Strategy**：定義日誌格式、監控指標、報警規則。
2.  **ADR-XXX: Nakama Testing and Deployment Strategy**：定義 Staging 環境規格、測試流程、CI/CD 管線。
3.  **ADR-XXX: Tag Governance and Cleanup**：定義如何處理現有的 497 個標籤，以及未來的管理策略。

### 7. 結論

**(a) 整體可行性評分：3/10**

理由：該方案在理想情況下能運作，但嚴重缺乏對現實世界中系統故障、外部依賴變更和維運複雜性的考慮。它構建了一個看似美好但極其脆弱的「玻璃大砲」，在生產環境中稍有風吹草動就會崩潰，且屆時將難以除錯和修復。

**(b) 建議：退回重寫 (Reject and Rewrite)**

這份 ADR 必須被駁回。作者需要重新思考整個系統的穩定性、可觀測性和可維護性。直接照此實作將會導致一個無法維護的技術債集合體。

**(c) 最 blocking 的 1-2 個問題（必須先解決才能開工）**

1.  **缺乏穩健的錯誤處理與狀態管理**：Usopp 的發布流程必須被重新設計為一個具備持久化、可重試、冪等性的狀態機或任務佇列。必須明確定義在每一步失敗後的行為（回滾？告警？手動介入？）。
2.  **完全沒有可觀測性與測試策略**：在沒有建立 Staging 環境、定義結構化日誌和核心監控指標之前，任何程式碼都不應該被部署。否則，團隊將會「盲飛」，系統上線即失控。