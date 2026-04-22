---
source_adr: ADR-007-franky-scope-expansion
reviewer_model: claude-sonnet-4-6
elapsed_seconds: 146
review_date: 2026-04-22
---

# ADR-007-franky-scope-expansion — claude-sonnet 審查

# ADR-007 架構審查報告

---

## 1. 核心假設檢驗

**假設 A：單一 VPS 同時跑 cron scheduler + 多個 agent + WordPress — 不會互搶資源**
這是整份 ADR 最危險的隱性假設。2vCPU / 4GB RAM 的機器上，如果 WordPress + MySQL + Nakama 主服務 + Franky cron（每 5 分鐘、每 10 分鐘、每小時）同時活著，尖峰時的記憶體壓力完全沒有量化依據。「<1% CPU」這個估算沒有基準數字支撐，是拍腦袋。

**假設 B：Cloudflare GraphQL API 在每 10 分鐘的頻率下不會被 rate limit**
Cloudflare GraphQL Analytics API 有 per-minute 和 per-day query budget，ADR 完全沒有提及。`httpArchive` 類 query 每分鐘上限約 5 requests，`ztFirewallEventsAdaptive` 更嚴。10 分鐘跑一次看起來保守，但若 query 複雜（多個 dataset join），單次可能消耗多個 cost unit。

**假設 C：GSC API 的資料延遲是可接受的**
Google Search Console API 的資料延遲是 2–4 天，不是「前一日」。ADR 第 4 節寫「每日抓：近 7 天 + 前一日」，這是錯的。對 target keyword 排名告警而言，你在 4/22 看到的最新資料可能是 4/18 的。若以此觸發 Warning（「單週掉 >10 名」），時間窗口計算會系統性偏移。

**假設 D：GA4 demographics 的 aggregate 資料在小流量站台上會有意義**
Google 對低流量屬性會套用「門檻值」（thresholding），當某個維度組合的樣本不足時直接省略資料或回傳 null。修修的站台是否達到 GA4 demographics 所需的最低流量門檻，ADR 沒有任何評估。如果流量不足，整個 GA4 audience pipeline 產出的是空資料或不完整資料，卻還要「餵給 Brook 寫作定位」。

**假設 E：`SLACK_SHOSHO_USER_ID` 是穩定的識別符**
Slack User ID 本身是穩定的，但 Workspace 換方案、用戶被 deactivate / re-invite 都可能讓 IM channel ID 失效。ADR 沒有任何 DM 失敗的 fallback。

**假設 F：xCloud 的 R2 備份 created_at timestamp 與 Franky 的時區一致**
R2 object metadata 的 `LastModified` 是 UTC。Franky cron 在 `Asia/Taipei`（UTC+8）跑，但檢查邏輯是「created_at > 昨天 00:00」。若沒有明確的時區轉換，當 xCloud 在台灣時間 23:50 完成備份，而 Franky 在 03:00 檢查，UTC 換算後可能誤判為「昨天」的備份不存在。

**假設 G：`config/target-keywords.yaml` 在 Franky 需要讀取時總是存在且格式正確**
ADR 把這個檔案設計為 Zoro 和 Usopp 都會寫入，Franky 讀取，但沒有定義 schema、沒有 validation、沒有 file lock 機制。多個 agent concurrent write 是 race condition 的經典場景。

---

## 2. 風險分析

### (a) 未被提及但會造成生產問題的風險

**R1：Google OAuth token 過期與 refresh 機制缺失**
GSC 和 GA4 都用 `google-api-python-client`，Service Account JSON 的 token 有效期是 1 小時，client library 理論上會自動 refresh，但在 cron 環境（每個 invocation 是獨立 process）下，若沒有正確處理 credentials 物件的序列化，可能每次都重新走完整 OAuth flow，或者在 token refresh 失敗時靜默跳過（不告警）。ADR 沒有任何 credentials refresh 失敗的告警設計。

**R2：`state.db` 的 concurrent write 問題**
Franky 的多個 cron（5min health check、10min cloudflare、每小時 VPS、每日 GSC/R2）都寫同一個 `state.db`（SQLite）。SQLite 的 WAL mode 可以處理一寫多讀，但多個 writer 同時寫不同 table 仍然會序列化，若某個 cron job 持鎖超時，下一個 cron 會被 block。ADR 沒有提及 SQLite WAL 設定，也沒有 cron job timeout 設計。

**R3：Franky cron 失敗不告警的靜默死亡問題**
如果 `gsc_tracker.py` 的 daily cron 因為 API 異常靜默退出，Franky 不會知道，修修也不會知道。告警系統本身沒有「watchdog」——誰來監控 Franky？這是 self-monitoring 的根本矛盾，ADR 完全沒有討論。

**R4：Slack DM 的 rate limit 在 Critical 告警連發時**
WP health check 每 5 分鐘跑，連 3 次 fail 觸發 Critical。但如果觸發後 Franky 繼續跑 health check，每隔 15 分鐘就可能再次觸發，無限 DM 修修。ADR 沒有 alert deduplication / suppression 邏輯。Slack Tier 1 API rate limit 是 1 req/min per method，批量 DM 可能觸發 `ratelimited`。

**R5：Weekly digest 的 Claude Sonnet 呼叫沒有 fallback**
每週一 10:00 的 digest 估算 $0.05，但如果 Claude API 在那個時間點 unavailable，digest 就靜默失敗。這個 cron 是修修最主要的資訊入口，失敗應該要有告警或 retry。

**R6：GA4 Data API 的 quota 耗盡**
GA4 Data API 每個 property 每天有 200,000 token 的 quota，聽起來很多，但若 `interestCategory` + `demographic_age_range` + `demographic_gender` + `landing_page` 分開跑 4 個 request，加上 batch 邏輯不好，在多個 site 情況下 quota 消耗速度難以預估。ADR 完全沒提 quota management。

### (b) 已提及但嚴重度被低估的風險

**R7：「GA4/GSC 授權流程繁瑣，修修需花 30-40 分鐘」**
這是嚴重低估。實際風險是：
- GSC Service Account 需要 property owner 在 Google Search Console UI 手動加 service account email 為 user
- GA4 需要在 Google Analytics Admin 加 service account 為 Viewer
- 若 GA4 property 是 Universal Analytics（ADR Open Question 已提及此疑慮），整個 `google-analytics-data` SDK 就廢了，因為 UA 用的是 Reporting API v4，不是 GA4 Data API
- Google Signals 啟用不只是「30 分鐘」，還需要在 EU 地區遵守 DPA 條款，且啟用後的 demographics 只對啟用後的新資料有效，歷史資料不會補填

**R8：「Cloudflare threat rate > baseline 10x」的 baseline 定義**
ADR 提到 DDoS 觸發條件是「threat rate > baseline 10x」，但 baseline 怎麼算？是 rolling 7 天平均？是靜態數字？是從哪個 table 查？如果站台剛上線流量本來就很低，一個正常的爬蟲 spike 就可能觸發 10x。這個告警條件在生產環境會產生大量誤報，嚴重度遠高於 ADR 暗示的程度。

### (c) 已提及但嚴重度被高估的風險

**R9：「Franky cron 太多可能壓到 VPS」**
ADR 自己說「估算 <1% CPU」然後說是「風險」，這個措辭製造了不必要的焦慮。每 5 分鐘的 HTTP health check（`curl`）和每 10 分鐘的 Cloudflare API call 在 I/O 層面確實幾乎不可見。真正的資源壓力是 SQLite write + Python 進程啟動開銷，但在這個頻率下仍然是可忽略的。這個風險被錯誤地放進 Consequences 區，分散了讀者對真正風險（R1–R8）的注意力。

---

## 3. 替代方案

### Franky 的整體監控職責應該部分外包給 UptimeRobot / Better Uptime

ADR 花了相當篇幅設計「WP × 2 HTTP health check 每 5 分鐘 / 連 3 次 fail 觸發 Critical DM」——這是在自建一個 UptimeRobot 的功能子集。UptimeRobot 免費方案提供 50 monitors、5 分鐘 check interval、email + Slack 告警、response time 歷史，而且它是從外部網路發起 check（能偵測到 VPS 本身掛掉的情況，Franky 自身 cron 在 VPS 上跑則無法）。

作者可能沒選的原因：想把所有觀測統一在 Franky 一個地方。但這個設計決策的代價是 Franky 成為單點失敗——如果 VPS 掛了，Franky 也掛了，WordPress 掛了也沒有人 DM 修修。**這是架構上的根本缺陷，不是邊緣案例。**

### GSC 排名追蹤應該考慮 Semrush / Ahrefs API 或 SERP API

GSC 只有自己 property 的資料（clicks, impressions），沒有競爭對手資料，資料延遲 2–4 天。如果目標是「Zoro 選 keyword，Franky 觀察排名變化」，更精準的工具是 SERP API（如 SerpAPI、DataForSEO），可以即時查詢特定關鍵字在 Google 的實際排名，不依賴 GSC 的間接 impression 資料。ADR 完全沒有討論 GSC 資料的局限性對「排名追蹤」使用案例是否足夠。

### Cron 管理應該用 APScheduler 或 Celery Beat 而非 system cron

目前設計是多個獨立 Python script 被 system crontab 呼叫。這個方案的問題：
- 每次執行都是獨立 Python 進程，startup overhead 累積
- Cron job 失敗不會自動 retry
- 沒有 job overlap 保護（上一次 cron 還沒跑完，下一次又觸發）
- Log 分散在不同地方

APScheduler 在同一個 process 裡管理所有 schedule，有 job coalescing（防重複觸發）和 misfire 處理。若不想額外依賴，至少 system cron 每個 job 應該有 `flock` 保護。ADR 對這個問題完全沉默。

---

## 4. 實作 Pitfalls

### P1：`gsc_tracker.py` 的日期窗口邏輯

```python
# 開發者很可能寫成
end_date = date.today() - timedelta(days=1)  # 昨天
start_date = end_date - timedelta(days=7)
```

但 GSC API 的實際可用最新日期是 `today - 3` 到 `today - 4`。用 `today - 1` 當 end_date 會取到部分資料或空資料，但 API 不一定回傳錯誤，只是安靜地給你不完整的 impressions 數字。正確做法是動態查詢 `dataAvailability`，或者直接用 `today - 4` 作為 end_date。這個 bug 會讓排名告警計算的「上週 vs 本週」比較基準完全錯誤。

### P2：`config/target-keywords.yaml` 的 schema 沒有定義

ADR 說 Usopp publish 後「自動 append focus_keyword」，但沒有定義 YAML 格式。最可能產生的 schema：

```yaml
# Zoro 可能寫成
keywords:
  - 肌酸 功效

# Usopp 可能 append 成
keywords:
  - 肌酸 功效
  - creatine women  # 英文？繁中？
```

Franky 的 `gsc_tracker.py` 拿到這個 list 去 GSC API 查，但 GSC query 是 case-sensitive 且語言敏感的。若 Usopp append 的是 post slug 或英文 keyword，而 GSC 裡的 query 是繁中，匹配會靜默失敗（找不到 keyword，不告警，不報錯）。**這個 YAML 的 schema、encoding、validation 必須在 ADR 裡明確定義。**

### P3：`alert_router.py` 的三級制在 Critical 後缺乏狀態追蹤

```python
# 最天真的實作
if disk_usage > 95:
    slack_dm(CRITICAL_MESSAGE)
```

這個邏輯每次 cron 跑都會重新 evaluate，只要磁碟持續 >95%，每小時就 DM 一次。`alert_router.py` 需要有 alert state 儲存（「這個告警已在 X 時間發出，suppress 到 Y 時間」），但 ADR 沒有設計這個 table，也沒有定義 suppress window。

### P4：`cloudflare_monitor.py` 的 GraphQL query 結構

Cloudflare GraphQL Analytics API 的 `httpRequests1mGroups` dataset（1 分鐘粒度）只保留最近 72 小時的資料，且 `clientRequestHTTPHost` dimension 在某些方案下不可用或需要特定欄位組合。ADR 說用 `clientRequestHTTPHost` 分開兩個 hostname，但沒有確認修修的 Cloudflare 方案是否支援這個 dimension，也沒有提供範例 query。開發者會在這裡踩到「query syntax 正確但 field 不存在」的執行期錯誤。

### P5：`r2_backup_verify.py` 的大小下限沒有定義

ADR 說「檔案大小 > 下限（避免空備份）」，但下限是多少？這個數字需要先跑一次正常備份、量測正常大小、設定合理門檻（例如正常大小的 50%）。如果工程師把這個值 hardcode 成 `1024`（bytes）或 `0`，檢查就沒有意義。這個值應該是 config，且 ADR 應該定義它的來源和更新程序。

### P6：`shared/google_api_client.py` 的 credentials 管理

GSC 和 GA4 共用一個 client module，但兩個服務需要不同的 API scope：
- GSC：`https://www.googleapis.com/auth/webmasters.readonly`
- GA4：`https://www.googleapis.com/auth/analytics.readonly`

如果使用同一個 Service Account，需要在建立時把兩個 scope 都加進去，且 Service Account 要分別在 GSC property 和 GA4 property 被授權。若工程師只看 ADR 然後建一個 Service Account 只給 GSC 授權，GA4 call 會在 runtime 才報 403，不是在 setup 階段就被 catch。**Credentials setup checklist 必須在 ADR 裡明確列出，不能只說「見 runbook」。**

---

## 5. 缺失的視角

### 可觀測性（嚴重缺失）

整份 ADR 設計了 Franky 監控其他東西，但完全沒有設計如何監控 Franky 自己。具體缺失：
- Franky 每個 cron job 的執行結果（成功/失敗/duration）應該寫到哪裡？
- 如果 `gsc_tracker.py` 連續 3 天 silent crash，誰告警？
- 沒有 structured log format 定義，未來查問題靠 `grep`

最低要求：每個 cron job 應該有 heartbeat 記錄（`cron_runs(job_name, run_at, status, duration_ms, error_msg)`），且應該有 watchdog 機制（例如：若 `gsc_tracker` 3 天沒有成功記錄，發 Warning）。

### 資安（部分缺失）

ADR 提到 Cloudflare token 是 read-only，這是對的。但：
- GA4 / GSC Service Account JSON 存放位置沒有定義（是 `.env`？是 Vault？是 `~/credentials/`？）
- R2 access key 的最小權限範圍沒有定義（應該只有 `GetObject` + `ListBucket`，不能有 `DeleteObject`）
- Slack bot token 的存放和 rotation 策略沒有提及
- `state.db` 裡的 `ga4_audience` 表雖然說「不存個別 user ID」，但 `interestCategory` + `demographic_age_range` + `demographic_gender` 的組合，在小站台情況下仍然具有個人識別風險（GDPR 的 k-anonymity 問題）

### 法規（嚴重缺失）

GA4 demographics 和 Google Signals 涉及 GDPR（即使是台灣站台，EU 用戶訪問也受約束）和台灣個資法。ADR 只說「不存個別 user ID」，但：
- 修修的隱私政策是否揭露使用 Google Signals 蒐集 demographics？
- 讀者有沒有 cookie consent 流程？
- 將 GA4 aggregate audience 資料傳給 Brook（AI agent）是否合規？

這塊若未處理，在歐洲用戶訪問站台的情況下是實際的法律風險，不是假設場景。

### 可測試性（完全缺失）

ADR 沒有任何測試策略：
- Cloudflare / GSC / GA4 都是外部 API，unit test 怎麼 mock？
- 告警觸發條件的 integration test 如何在沒有真實 DDoS 的情況下驗證？
- Weekly digest 的 Claude 呼叫怎麼在 CI 中測試而不燒錢？

沒有 test strategy 的監控系統，上線後的 debug 成本極高，因為你不知道是資料錯了、API 錯了、還是邏輯錯了。

### 成本（計算不完整）

ADR 提到 Claude Sonnet ~$0.05/週，但沒有計算：
- GA4 Data API：免費，但超過 quota 後有無付費選項？
- GSC API：完全免費，但 quota 上限是什麼？
- R2 存儲成本：每天一個 `state.db` 備份，一年下來多大？R2 的 $0.015/GB/月，但要知道 DB 增長速度
- Cloudflare GraphQL：免費方案有 query budget，是否足夠？

### 運維（缺失 runbook）

- 如果 `state.db` 損壞（sqlite corruption），Franky 怎麼 recover？
- 如果 R2 credentials 輪換，哪些地方需要更新？
- Franky 重新部署後，歷史 GSC 資料是否需要補跑？
- Weekly digest 如果週一早上失敗，有沒有手動觸發的機制？

---

## 6. Phase 拆分建議

### 必須 Phase 1 完成（核心功能，不做就沒意義）

- **WP HTTP health check + Critical DM**（但必須搭配外部監控，見 R3）
- **VPS disk/RAM 告警**
- **Nakama service health check**
- **`alert_router.py` 含 deduplication 邏輯**（不做就是 DM 轟炸）
- **Franky 自身的 cron heartbeat 監控**（不做就是瞎眼監控）

### 應拆成獨立 ADR

- **ADR-007a：外部 uptime 監控策略**
  WP health check 從 VPS 外部發起 vs 從 VPS 內部發起是架構選擇，影響到 VPS 掛掉時的告警能力，值得獨立討論。

- **ADR-007b：GSC + GA4 資料 pipeline**
  這兩個 Google API 整合有獨立的 credentials、schema、資料延遲、quota 問題，且與「Zoro/Brook 協作流程」強耦合，不應該塞在同一份 ADR 裡。

- **ADR-007c：target-keywords.yaml 協作 schema**
  Zoro、Usopp、Franky 三個 agent 共享一個 config file 是跨 ADR 的關鍵依賴，需要獨立定義 schema、ownership、validation、conflict resolution。

### 可以延後到 Phase 2+

- **GA4 demographics → Brook pipeline**
  前提是 GA4 流量門檻達到（見假設 D），且 Google Signals 啟用 48 小時後才有資料。Phase 1 先讓 `ga4_audience` table 存在但為空，Brook 端的整合等 Phase 2。

- **Weekly digest 的 Claude Sonnet 生成**
  Phase 1 可以先做純數字的 Markdown 報表（不需要 LLM），確認資料管道正確後 Phase 2 再加 LLM 潤飾。這樣可以大幅降低 Phase 1 的複雜度和 debug 難度。

- **Cloudflare baseline 動態計算**
  Phase 1 先用靜態 threshold，Phase 2 再實作 rolling average baseline，避免 P2（Cloudflare baseline）問題在 Phase 1 就爆。

---

## 7. 結論

### (a) 整體可行性評分：4 / 10

範圍定義清楚，三級告警架構思路正確，模組切分也合理。但核心缺陷太多：自我監控盲點（VPS 掛了 Franky 也掛了）、GSC 資料延遲被誤解、alert deduplication 完全缺失、GA4 demographics 在小站台的實際可用性沒有驗證。這些不是細節問題，是會讓系統在生產環境失去可信度的根本設計錯誤。

### (b) 建議：退回修改，重寫關鍵章節

不是退回重寫整份 ADR，但以下章節必須大幅修改才能開工：
- Section 2（告警條件需加入 deduplication 設計）
- Section 4（GSC 資料延遲必須修正）
- Section 6（Cloudflare baseline 定義方式）
- 必須新增 Section：Franky 自監控機制
- 必須新增 Section：外部 uptime 監控（非 VPS 本機 cron）

### (c) 最 Blocking 的問題

**Blocking 1：Franky 自身是單點失敗**
整個系統的告警依賴 Franky cron 在 VPS 上跑，但若 VPS 本身崩潰、網路中斷、或 Franky process 靜默死亡，修修不會收到任何通知。這個問題在動筆寫任何 code 之前必須決策：至少需要一個 VPS 外部的 heartbeat check（UptimeRobot 免費方案即可解決），監控 Franky 的 watchdog endpoint。沒有這個設計，整個 ADR-007 的告警系統是紙老虎。

**Blocking 2：Alert deduplication 架構缺失**
在任何告警邏輯開始實作之前，必須先定義 `alert_state` 的 schema 和 suppress 邏輯。若先實作告警 trigger，後補 deduplication，幾乎必然需要重寫 `alert_router.py`。這個設計應該在 ADR 層面就敲定，不能留到實作時決定。