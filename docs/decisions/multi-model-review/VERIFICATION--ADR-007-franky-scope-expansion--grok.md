---
source_adr: ADR-007-franky-scope-expansion
verification_round: 2
reviewer_model: grok-4
elapsed_seconds: 65
review_date: 2026-04-22
---

# ADR-007-franky-scope-expansion — 修訂驗證（grok）

### 1. Blocker 逐項檢核

針對 Consolidated Review §5「最 blocking 的問題」每一項，逐一檢核修訂版 ADR 的回應。

- **原 blocker 一句話描述**：Franky SPOF / 監控物理分離 — VPS 內部 cron 不能是唯一告警路徑。動筆前必決策：至少接一個外部 uptime 服務。  
  **修訂版回應**：✅ 完整解。  
  **證據**：§2「SPOF 緩解 — 外部 uptime probe」，選用 UptimeRobot 免費方案，定義三個監控 targets、告警通道（email + SMS），並附 setup 流程最小步驟；§3 定義 /healthz 契約作為 heartbeat endpoint；Consequences 的 SPOF 表進一步緩解規劃。

- **原 blocker 一句話描述**：Alert deduplication 架構 — `alert_state` schema + suppress window 要在 ADR 敲定，不能留到實作。  
  **修訂版回應**：✅ 完整解。  
  **證據**：§4「Alert dedup」，定義 AlertEventV1 schema（含 dedup_window_seconds）、alert_state DB 表、發送邏輯（含 suppress until 與 fire_count）、冪等性處理；連續 N 次 fail 計數移到 health_probe_state 表，讓 router 無狀態。

- **原 blocker 一句話描述**：Cron job 管理策略 — 選 APScheduler 或 `flock`-wrapped cron，明寫進 ADR。  
  **修訂版回應**：✅ 完整解。  
  **證據**：§7「Cron 管理策略」，明確選擇 system cron + flock wrapper，列理由（Phase 1 job 少、無需 APScheduler overhead）、wrapper 模板 bash 範例、透過 shared/cron_wrapper.py 處理 operation_id 與 cron_runs 寫入。

- **原 blocker 一句話描述**：state.db schema 與 migration — 新增 `gsc_ranks`、`ga4_audience`、`alert_state`、`cron_runs` 等表的 PK、型別、index、migration SQL 要在 ADR 附錄定義。  
  **修訂版回應**：✅ 完整解（Phase 1 範圍內；gsc_ranks/ga4_audience 等拆出）。  
  **證據**：§5「state.db migration 路徑」，現狀盤點後定義 Phase 1 五張新表（alert_state、health_probe_state、cron_runs、vps_metrics、r2_backup_checks）的完整 SQL（含 PK、型別、index）、migration 執行方式（手寫 SQL file + backup + dry run + rollback）、確認 WAL 已開。

- **原 blocker 一句話描述**：VPS 資源 baseline 實測 — 在高峰 Brook 寫作時跑一輪所有 cron，量 CPU/RAM。若 headroom 不足，先決策擴容。  
  **修訂版回應**：✅ 完整解。  
  **證據**：§6「VPS baseline 壓測」，定義方法（vmstat/iostat 採樣、同時負載情境、24 小時持續）、接受標準表（e.g., RAM headroom ≥ 500 MB）、不通過則不開工並決策擴容或調整 cron 頻率；視為開工前交付物。

- **原 blocker 一句話描述**：API credentials 管理 — Service Account JSON 位置、權限（chmod 600）、rotation 策略、R2 最小權限（只 GetObject/ListBucket）。  
  **修訂版回應**：🔄 拆到別處（指明去向：ADR-008 External API Integration Strategy）。  
  **證據**：Context 明列拆出 GSC/GA4/Cloudflare 到 ADR-008；Phase 1 範圍表 (§1) 排除相關檢查項；Notes 提及與 ADR-008 處理 target-keywords.yaml 共用 schema。

### 2. 新發現的問題

修訂版引入或暴露的**新問題**（原 review 沒抓到的），按嚴重度排序。僅列有實質影響者，無 Critical 級。

- **問題描述**：Phase 1 包含 R2 備份驗證 (§1 #4、§5 r2_backup_checks 表、§11 r2_backup_verify.py)，需 R2 credentials，但未定義其管理（如位置、權限、rotation、最小權限），與原 blocker #6 類似；拆到 ADR-008 但 Phase 1 已需使用，易成資安漏洞。**嚴重度**：High。**建議修法**：補入 §6 或新節，定義 R2 key 存放（e.g., env var + chmod 600）、最小權限（GetObject/ListBucket），並援引 reliability.md §4 SPOF 表。

- **問題描述**：§4 AlertEventV1 schema 含 dedup_window_seconds（預設 900），但 alert_state DB 表無此欄位；若 per-rule 覆寫，需額外 config 或 rule 表，否則邏輯不完整，易導致 dedup 失效。**嚴重度**：Medium。**建議修法**：在 §4 schema 補註明 dedup_window_seconds 來源（e.g., 從 config 載入），或加到 alert_state 表為可選欄位。

- **問題描述**：SLO (§ SLO) 中「Critical 事件 → DM 送達 p95 < 3 分鐘」量測需 Slack ts，但未定義如何記錄/聚合（e.g., 無 central log 收集 Slack 送達時間）；外部 probe 偵測延遲可能超 3 分鐘，SLO 易違反。**嚴重度**：Medium。**建議修法**：在 §4 發送邏輯補 alert_router 記錄 send_at / confirmed_at 到 alert_state 表，供 SLO 查詢；調整 SLO 為「內部事件 → DM < 1 分鐘，外部 probe 單獨 SLO」。

- **問題描述**：§2 UptimeRobot setup 提及「Maintenance window：排除 VPS 每週二 03:00-03:30」，但未解釋為何此時段（OS patch），若未文件化，易誤設或忘記更新。**嚴重度**：Low。**建議修法**：在 runbook 連結或 §2 補一註解，援引 reliability.md §10 crash safety。

### 3. 修訂品質評估

| 維度 | 1-10 分 | 一句話評語 |
|---|---|---|
| Schema / 契約完整度 | 9 | 嚴格遵守 schemas.md（e.g., extra=forbid、schema_version），新 schema 如 AlertEventV1 / HealthzResponseV1 定義詳盡，唯 dedup_window_seconds 欄位整合略缺。 |
| Reliability 機制（idempotency / atomic / SPOF）| 9 | 援引 reliability.md 硬規則佳（e.g., 冪等 UPSERT、WAL mode、SPOF 表），migration rollback 安全，但 R2 credentials 管理遺漏成潛在 SPOF。 |
| Observability（log / metric / SLO / probe）| 8 | 涵蓋 observability.md 多項（e.g., cron_runs 表、外部 probe、SLO 指標），但 SLO 量測機制（如 Slack ts）未全實作路徑。 |
| 可實作性（工程師照寫能不能動） | 9 | 模組切分 (§11)、migration SQL、cron wrapper 模板具體可直接抄，開工 Checklist 順序清晰，唯新問題 #1 (R2 credentials) 可能卡住 r2_backup_verify.py。 |
| 範圍聚焦度（沒再 scope creep） | 10 | 嚴格收斂 Phase 1 六項 (§1 表)，明確拆出 GSC/GA4/Cloudflare 到 ADR-008，無原版 scope 過寬問題。 |

### 4. 最終判定

- **go / no-go**：go（工程師可以開始寫 code 了）。  
- **如果 go**：Phase 1 實作過程中要特別盯的 1-2 個風險：(1) R2 credentials 管理（新問題 #1），確保在寫 r2_backup_verify.py 前補定義，否則成資安 SPOF；(2) Alert dedup 邏輯測試（§4），特別驗證 dedup_window_seconds 覆寫與連續 fail state machine，避免生產環境告警風暴。