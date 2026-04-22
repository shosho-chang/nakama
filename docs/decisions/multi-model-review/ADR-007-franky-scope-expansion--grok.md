---
source_adr: ADR-007-franky-scope-expansion
reviewer_model: grok-4
elapsed_seconds: 65
review_date: 2026-04-22
---

# ADR-007-franky-scope-expansion — grok 審查

### 1. 核心假設檢驗
這份 ADR 基於多個未明說的假設，包括：VPS 資源（2vCPU/4GB RAM）足以承載所有新增 cron 與 API 呼叫，無需額外擴容；Google API（GSC/GA4）與 Cloudflare GraphQL 的授權與資料可用性長期穩定，不會因政策變化中斷；修修作為單一用戶會及時回應 Critical 告警，且 Slack bot 是唯一可靠通知通道；state.db 能無痛擴展新表而不影響其他 agent 效能；所有 API 整合不會超過免費配額或產生隱藏成本；Zoro 產出的 target-keywords.yaml 格式一致且及時更新。這些假設容易出錯的包括：API 政策變化（Google 常調整 API 存取規則，可能導致 GSC/GA4 資料延遲或失效）；VPS 資源假設忽略了高峰期負載疊加（如多 agent 同時跑），可能導致 cron 延遲或崩潰；Slack bot 依賴單一用戶 ID，忽略修修可能更換帳號或 Slack 服務中斷。

### 2. 風險分析
- (a) 未被提及但會造成生產問題的風險：API rate limit 被觸發（Cloudflare GraphQL 每 10 分鐘呼叫若無 proper backoff，可能被 throttle）；state.db 作為單一資料庫，新增多表後若無索引或鎖定機制，會導致查詢競爭與資料不一致；資安漏洞，如 R2 credentials 暴露在共享 runbook 而無加密；外部依賴故障（e.g., Google API 全球 downtime）無 fallback 機制；單一 VPS 作為所有 cron 執行點，忽略了 VPS 本身 downtime 導致監控失效。
- (b) 已提及但嚴重度被低估的風險：Franky cron 太多壓到 VPS（ADR 估 <1% CPU，但未考慮累積效應，如每日/每週 cron 疊加高峰期，可能超過 10% CPU 並影響其他 agent）；GA4/GSC 授權流程繁瑣（低估為 30-40 分鐘，但若修修無技術背景，可能需數小時 debug 並導致延遲上線）；Google Signals 延遲 24-48 小時（低估為 Phase 1 空著，但這會讓 Brook 寫作定位功能在初期失效，影響整體系統價值）。
- (c) 已提及但嚴重度被高估的風險：無明顯高估風險；ADR 對風險描述偏保守，但如「緊急事件才被打擾」假設告警門檻嚴格，實際上 Critical 條件（如 disk >95%）可能太寬鬆而非高估。

### 3. 替代方案
有更簡單的替代路徑，如使用 UptimeRobot 或 Pingdom 取代自建 WP health check 和 Cloudflare 監控（這些工具提供免費 tier 與即時告警，無需 cron 自寫）；Google Alerts + SEMrush 免費版可部分取代 GSC/GA4 排名追蹤與 demographics 分析（更 proven，無需 API 整合）。作者沒選可能是想保持 Nakama 內部整合（e.g., 直接餵資料給 Brook），但這增加自建複雜度。更穩的第三方工具包括 Datadog 或 New Relic 取代整個監控系統（有 dashboard 與 alert 預設，支援多 API 整合，免費 tier 足夠小規模 VPS）；Prometheus + Grafana 可取代自建 cron 與 state.db 儲存（開源、proven，在 VPS 上輕量）。這些能減少自建風險，但作者忽略了，可能是過度自信於 Python 自寫的靈活性。

### 4. 實作 pitfalls
工程師照 ADR 寫，最容易踩坑包括：state.db schema 設計問題，如 gsc_ranks 表無 primary key（date + site + query 組合）導致 duplicate entries，或 ga4_audience 表 dimension/value 欄位型別不一致（string vs int）造成查詢錯誤；API 契約如 Cloudflare GraphQL query 未處理 filter 失效（e.g., clientRequestHTTPHost dimension 若 Cloudflare 改版，會拋 invalid query 錯誤無 retry）；檔名如 agents/franky/cloudflare_monitor.py 未實作 exponential backoff，導致 rate limit 直接崩潰 cron；slack_bot.py 只用 chat:write 無 error handling，若 token 失效會 silent fail 而不通知；gsc_tracker.py 的 target-keywords.yaml 解析若 Zoro 格式變更（e.g., 多層 YAML），會 crash 每日 cron；shared/google_api_client.py 共用 client 未隔離 GSC/GA4 credentials，導致一個 API 授權失敗影響另一個。

### 5. 缺失的視角
資安缺失嚴重：未提 API token 儲存安全（e.g., R2/Cloudflare key 明文在 runbook，無 Vault 或 env var 加密）；效能只提 CPU 估算，忽略 I/O 瓶頸（如 state.db 寫入高峰）；運維無 log rotation 或 error aggregation（cron 失敗無中央收集）；法規忽略 GA4 demographics 的 GDPR/台灣個資法合規（aggregate 資料仍可能需 consent）；可觀測性缺失 Franky 自身監控（誰監控監控者？）；成本只估 Claude ~$0.05，忽略 R2 存儲累積費用與潛在 API overage；可測試性無 unit test 建議（e.g., mock API 測試）；可維護性低估模組切分後的依賴管理（shared/ 模組若版本衝突，會影響多 agent）。

### 6. Phase 拆分建議
GSC/GA4 整合應拆成獨立 ADR（太細，涵蓋 API 細節與隱私，可單獨審查）；Cloudflare + R2 驗證也可拆成另一 ADR，聚焦外部 API 風險。延後到 Phase 2+ 的包括 GA4 demographics（依賴 Signals 啟用，Phase 1 先空跑）和 weekly digest 格式（非核心，可後加）；SEO 排名追蹤（target keywords）可延後，若 Zoro 未就緒。必須 Phase 1 完成的包括原有系統維護（CVE/健康檢查）、Critical 告警與 Slack bot（確保基礎穩定），以及 WP health check（直接影響生產）。

### 7. 結論
- (a) 整體可行性評分 5/10（擴展過廣導致自建複雜，忽略資安與可觀測性，VPS 資源邊緣可能崩潰，但核心監控邏輯合理）。
- (b) 建議：修改後通過
- (c) 最 blocking 的 1-2 個問題：資安缺失（API credentials 暴露，需加加密機制才能開工）；state.db 擴展無 schema 遷移計劃（可能導致資料不一致，需先定義 migration 步驟）。