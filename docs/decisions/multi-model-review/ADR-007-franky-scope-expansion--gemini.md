---
source_adr: ADR-007-franky-scope-expansion
reviewer_model: gemini-2.5-pro
elapsed_seconds: 42
review_date: 2026-04-22
---

# ADR-007-franky-scope-expansion — gemini 審查

好的，審查開始。

---

### 1. 核心假設檢驗

這份 ADR 基於數個未明說的假設，其中有些非常脆弱：

1.  **假設 VPS 資源是靜態且充裕的**：文件預估 Franky 的 cron 負載 <1% CPU，這是在假設其他 agent（尤其是 Brook 的寫作任務，可能涉及 LLM 推理或大量資料處理）處於閒置狀態。現實是，當 Brook 正在生成文章時，CPU/RAM 可能已在高位，此時任何新增的 cron job 都可能成為壓垮駱駝的最後一根稻草。這個假設在高負載時不成立。
2.  **假設所有外部 API 都是可靠且快速的**：文件預設了 Google 和 Cloudflare 的 API 會在短時間內穩定回應。當這些 API 變慢或失敗時，cron job 可能會超時、堆積，或因重試機制而耗盡資源。文件沒有定義超時（timeout）或熔斷（circuit breaker）機制。
3.  **假設 Nakama 本身是無狀態或輕狀態的**：每日備份 `state.db` 到 R2 隱含了這個資料庫相對較小。如果 Nakama 系統持續運行，log、歷史紀錄、agent memory 等資料會讓 `state.db` 膨脹，屆時備份將成為一個耗時耗資源的操作，影響其他服務。
4.  **假設「修修」具備 DevOps 的即時反應能力**：`Critical` 告警直接 DM 修修，這假設他能立即理解告警內容並採取行動。但像「VPS RAM full 且 swap >80%」這類問題，非技術人員可能無法處理，導致告警疲勞，最終忽略真正重要的訊息。
5.  **假設授權流程是一次性的靜態設定**：文件低估了維護外部 API 授權的複雜度。Google 的 OAuth token 會過期，service account 的金鑰需要輪換。這些授權不是設完就忘，而是需要生命週期管理的。

其中，最容易出錯的是 **假設 1 (VPS 資源充裕)** 和 **假設 4 (修修的即時反應能力)**。

### 2. 風險分析

**(a) 未被提及但會造成生產問題的風險**

1.  **Cron Job 堆疊（Job Overlapping）**：5 分鐘的 health check 和 10 分鐘的 Cloudflare monitor 可能會因網路延遲或 API 變慢而執行超過其排程間隔。這會導致同一個腳本的多個實例同時運行，造成資源競爭、資料庫鎖定，甚至重複發送告警。
2.  **API Rate Limiting**：文件對所有外部 API 都假設「免費」且無限制。Google 和 Cloudflare 都有速率限制。在高頻率輪詢下（即使是 10 分鐘），或在系統重啟後短時間內集中執行，極易觸發 Rate Limit，導致監控資料失明數小時。
3.  **單點故障（SPOF）**：整個監控與告警系統（Franky）與它所監控的核心業務系統（Nakama）跑在同一台 VPS 上。如果 VPS 本身宕機（硬體故障、網路中斷），Franky 將無法發出任何告警。告警系統必須與被監控對象在故障域上分離。
4.  **狀態資料庫損毀**：`state.db`（推測是 SQLite）是所有 agent 共享的狀態中心。高頻率的寫入（GSC 每日更新、GA4 每週更新、健康快照等）增加了資料庫損毀的風險。文件只提了備份，但沒有提到損毀檢測、告警及恢復計畫（restore plan）。

**(b) 已提及但嚴重度被低估的風險**

1.  **「Franky cron 太多可能壓到 VPS」**：這被輕描淡寫地估算為 "<1% CPU"。這個估算極度樂觀。Python 啟動本身有開銷，`google-api-python-client` 這種大型 library 的載入也不容小覷。在 2vCPU/4GB RAM 的環境下，一個 I/O-bound 的 Python 程序加上其依賴項，完全可能在啟動和執行時吃掉數百 MB RAM 和顯著的 CPU time slice，尤其是在與其他 agent 競爭時。
2.  **「GA4/GSC 授權流程繁瑣」**：嚴重度遠不止是「修修花 30-40 分鐘手動建」。這類授權失敗的偵錯非常困難，錯誤訊息隱晦。一旦 token 失效或權限變更，整個監控鏈路會靜默失敗（silently fail），直到一週後發現 digest 是空的才知道出事了。這不是一次性成本，而是持續的維運負擔。

**(c) 已提及但嚴重度被高估的風險**

無。此 ADR 中提到的風險都確實存在且嚴重度評估合理或偏低。

### 3. 替代方案

1.  **監控與告警的替代方案**：
    *   **自建 vs. 第三方服務**：與其在本機用 cron 執行大量監控腳本，不如使用外部、更穩定的監控服務。例如：
        *   **UptimeRobot / Better Uptime**：免費方案就能提供比本地 `curl` 更可靠的 HTTP health check、SSL 憑證到期檢查，且告警通道獨立於 VPS。
        *   **Datadog / New Relic / Grafana Cloud**：雖然可能超出預算，但它們的 agent 能提供遠比本地腳本更深入的系統指標（CPU/RAM/Disk/Network），且 UI 和告警規則都已產品化。
    *   **作者為何沒選？** 可能為了節省成本，並追求將所有功能整合在 Nakama 框架內的「純粹性」。然而，這犧牲了系統的穩定性和可靠性。監控系統的可靠度應高於被監控系統，放在一起是架構上的根本錯誤。

2.  **資料抓取與整合的替代方案**：
    *   **自建 vs. ETL/iPaaS 工具**：與其手寫 Python client 去對接 GSC/GA4/Cloudflare，不如使用像 **Supermetrics**、**Fivetran** 或開源的 **Airbyte**。這些工具專門處理 API 認證、分頁、schema 變化和 rate limit，能將資料直接同步到指定的資料庫或資料倉儲。
    *   **作者為何沒選？** 同樣，可能是成本考量和避免引入更多外部依賴。但這意味著團隊需要自己處理所有 API 的細節和未來的變更，維護成本極高。

### 4. 實作 pitfalls

如果工程師直接照此文件實作：

1.  **`state.db` 會成為效能瓶頸與鎖定地獄**：
    *   **Schema 問題**：`gsc_ranks` 的主鍵是什麼？`(date, site, query)`？如果一個 query 在一天內被多次記錄，會發生什麼？`ga4_audience` 的 `week_start` 應該是 `DATE` 型別，`dimension` 和 `value` 應該有長度限制。
    *   **併發寫入**：多個 cron job 可能同時嘗試寫入 `state.db`（SQLite 預設 write-lock）。例如，每日 03:00 的 GSC 任務和 R2 驗證任務，如果 GSC 執行過久，可能會與 04:00 的 `state.db` 備份任務衝突，導致 `database is locked` 錯誤。

2.  **`shared/google_api_client.py` 的設計缺陷**：
    *   **API 契約問題**：GSC 和 GA4 的認證流程、scope 和 client library 不同。把它們包在一個 `google_api_client.py` 裡，很可能導致一個巨大的、難以維護的上帝物件（God object），或者在處理 credential refresh token 時互相干擾。這兩個應該是獨立的 client。

3.  **`alert_router.py` 的告警風暴**：
    *   **邏輯缺陷**：`WP site HTTP 非 200（連 3 次 fail，每次間隔 1 分鐘）`，這個邏輯應該在 `health_check.py` 內部實現狀態管理（例如，用一個小檔案或 `state.db` 的一個表記錄失敗次數），而不是讓 `alert_router` 去判斷。目前的設計會導致每次失敗都觸發 router，router 無法判斷是否「連續」。這會導致第一次失敗就發告警，或根本不發。

4.  **`config/target-keywords.yaml` 的同步問題**：
    *   Zoro 和 Usopp 都會修改這個檔案，這是一個明顯的競爭條件（race condition）。如果沒有檔案鎖定（file locking）機制，一方的寫入可能會覆蓋另一方的修改。

### 5. 缺失的視角

1.  **可觀測性 (Observability)**：ADR 只談了監控「結果」（例如 CPU > 90%），但完全沒提如何觀察 Franky「自身」的健康狀況。
    *   Cron job 的執行日誌（logs）存在哪？如何輪替（rotate）？
    *   Cron job 是否成功完成、執行了多久、消耗了多少資源？這些元數據（metadata）完全沒有被記錄。當一個 job 靜默失敗時，無人知曉。
    *   缺乏 tracing。當 GSC 資料沒進來時，無法追蹤是認證失敗、API 呼叫失敗，還是資料庫寫入失敗。

2.  **可維護性 (Maintainability)**：
    *   **Credential 管理**：所有 API key 和 token 都散落在環境變數中。沒有統一的 secret management 機制，輪換金鑰將是一場災難。
    *   **相依性地獄**：`google-api-python-client` 和 `google-analytics-data` 都是巨大的套件，它們的相依性可能與 Nakama 其他 agent 的相依性衝突。沒有提及如何用 `venv` 或 `Poetry` 等工具隔離環境。

3.  **成本**：
    *   只估算了 Claude API 和 R2 存儲的直接成本，完全忽略了**資料傳輸成本（Data Transfer Cost）**。Cloudflare R2 的 A 類操作（寫入）免費，但 B 類操作（讀取，例如備份驗證）是收費的。Vultr 的流量 egress 超過免費額度後也是收費的。這些成本雖小，但架構師應有此意識。
    *   最重要的是**人力維運成本**被嚴重低估。排查一個失效的 Google API 授權所花費的工程師時間，遠比購買一個現成的監控服務昂貴。

4.  **資安**：
    *   Cloudflare API token 設為 `read-only` 是好的。但 Google Service Account 的 JSON key 檔案權限管理呢？Slack bot token 的權限管理呢？這些敏感文件若權限設置不當（如 `644`），可能被同機上的其他進程讀取。

### 6. Phase 拆分建議

當前的 ADR 範圍過大，試圖一次性解決所有問題。必須拆分。

**Phase 1 (MVP - 核心穩定性)**：
*   **必須完成**：
    1.  **外部 HTTP Health Check**：使用 UptimeRobot 或類似的**外部**服務來監控 WP 站點和 Nakama service 端點。這將告警系統與主機脫鉤。
    2.  **本機資源監控**：實作 VPS (RAM/CPU/Disk) 的基本監控與 `Critical` 級別告警。
    3.  **獨立 Slack Bot**：建立 `Franky` bot，只用於接收來自 Phase 1 監控項的 `Critical` 告警。
    4.  **R2 備份驗證**：這部分相對獨立且重要，可以保留。

*   **獨立 ADR 需要**：
    1.  **ADR-008: External API Integration Strategy (Google & Cloudflare)**：這應該是一個獨立的決策，詳細討論認證、rate limiting、錯誤處理、資料儲存 schema。GSC、GA4、Cloudflare 的整合應放在這個 ADR 下。

**Phase 2 (SEO & 讀者洞察)**：
*   在 ADR-008 通過後，實作 GSC 關鍵字追蹤與告警。
*   實作 GA4 demographics 抓取。
*   實作 Weekly digest 的生成與發布。

**延後到 Phase 2+ 或重新評估**：
*   **Cloudflare 流量與攻擊監控**：這個監控的即時性要求不高，且 Cloudflare 自身儀表板已提供足夠資訊。除非有明確的自動化響應需求，否則可以先手動觀看。

### 7. 結論

**(a) 整體可行性評分：3/10**

理由：此 ADR 在一個資源極度受限的單點故障環境上，試圖用脆弱的自建方案堆疊過多功能。它嚴重低估了資源競爭、外部 API 的不確定性以及長期維運的複雜度。方向正確，但實作路徑充滿風險，很可能導致一個比它所監控的系統更不穩定的「監控系統」。

**(b) 建議：退回重寫 (Reject and Rewrite)**

必須重新思考核心架構。監控系統的根基應是穩定與獨立，目前方案兩者皆無。

**(c) 最 blocking 的 1-2 個問題**

1.  **監控系統與被監控系統的物理分離**：嚴禁在同一台 VPS 上部署監控其自身的 cron job 集合。必須將核心的 uptime/health-check 移至外部服務，以確保在 VPS 完全失聯時還能收到告警。
2.  **資源競爭與 cron 管理**：必須引入一個成熟的 job scheduler（如 `APScheduler` 配合 persistent store，或更重的如 Celery Beat）來取代裸露的系統 cron。這能防止 job 重疊，並提供對 job 執行狀態（成功、失敗、執行時間）的基本可觀測性。在沒有解決這個問題前，每增加一個 cron job 都是在增加系統的熵。