---
source_adr: ADR-007-franky-scope-expansion
verification_round: 2
reviewer_model: gemini-2.5-pro
elapsed_seconds: 23
review_date: 2026-04-22
---

# ADR-007-franky-scope-expansion — 修訂驗證（gemini）

好的，身為資深軟體架構師，我已審閱完 ADR-007 修訂版，以下是針對上一輪 multi-model review blocker 的驗證報告。

---

### 1. Blocker 逐項檢核

#### Blocker 1: Franky SPOF / 監控物理分離
- **原 blocker 一句話描述**：監控系統 (Franky) 與被監控對象 (VPS) 在同一個故障域，VPS 掛掉會導致告警靜默。
- **修訂版回應**：✅ 完整解
- **證據**：
    - §1 Phase 1 範圍，明確列入「#6 外部 uptime probe」。
    - §2 SPOF 緩解，完整定義了 **UptimeRobot** 作為外部探針，並明列監控 targets (`/healthz`, WP×2)。
    - §2 告警通道，硬性規定走 **email+SMS，絕對不走 Slack**，成功繞開了 VPS 故障域。
    - SPOF 表格明確列出 VPS 為 SPOF，並以 UptimeRobot 作為緩解措施。

#### Blocker 2: Alert deduplication 架構
- **原 blocker 一句話描述**：缺乏告警狀態儲存，會造成告警風暴，且「連續 N 次失敗」的邏輯無法實作。
- **修訂版回應**：✅ 完整解
- **證據**：
    - §4 Alert dedup，提出了完整的架構：
        - `AlertEventV1` Pydantic schema
        - `alert_state` SQL table schema，包含 `suppress_until` 和 `state` 欄位
        - 明確的發送邏輯，處理了 `suppress_until > now` 的情況
    - 針對「連續 N 次 fail」，修訂版在 §4 末尾設計了獨立的 `health_probe_state` 表，將此邏輯從 `alert_router` 抽離，讓 router 職責單一，設計優良。

#### Blocker 3: Cron job 管理策略
- **原 blocker 一句話描述**：未選擇成熟的 job scheduler 或 job overlap 保護機制，裸 system cron 風險高。
- **修訂版回應**：✅ 完整解
- **證據**：
    - §7 Cron 管理策略，明確做出決策：「**system cron + `flock` wrapper**」。
    - 提供了 Wrapper 模板，展示了 `flock -n` 如何防止 job 重疊。
    - 搭配 `shared/cron_wrapper.py` 與 `cron_runs` 表，補足了 system cron 缺乏的 metadata 記錄與可觀測性。

#### Blocker 4: state.db schema 與 migration
- **原 blocker 一句話描述**：新功能所需的資料表 schema 未定義，且沒有資料庫遷移計畫。
- **修訂版回應**：✅ 完整解
- **證據**：
    - §5 state.db migration 路徑，提供了完整的解決方案：
        - 盤點了現有表，確認無命名衝突。
        - 提供了所有 **5 張新表** (`alert_state`, `health_probe_state`, `cron_runs`, `vps_metrics`, `r2_backup_checks`) 的完整 `CREATE TABLE` DDL，包含 PK、index。
        - 定義了 migration 執行方式（SQL file + `executescript`），並納入備份與 rollback 步驟，符合可靠性原則。

#### Blocker 5: VPS 資源 baseline 實測
- **原 blocker 一句話描述**：「<1% CPU」的估算缺乏數據支撐，是拍腦袋決策。
- **修訂版回應**：✅ 完整解
- **證據**：
    - §6 VPS baseline 壓測，將此項從「假設」變為「**開工前必做**」的硬性要求。
    - 定義了清晰的壓測方法（觸發同時負載情境）、可量化的接受標準（CPU p95 < 60%、RAM headroom ≥ 500 MB 等）。
    - 最關鍵的是，定義了「**若未通過**」的決策路徑（擴容或修改 ADR）以及「**不通過則不開工**」的閘門，徹底解決了原問題。

#### Blocker 6: API credentials 管理
- **原 blocker 一句話描述**：低估了 Google OAuth 授權的複雜度，且 ADR 中未提供具體的 credentials setup checklist。
- **修訂版回應**：🔄 拆到別處
- **證據**：
    - 本修訂版透過大幅收斂 Phase 1 範圍，將所有 Google API (GSC/GA4) 與 Cloudflare API 的整合都移除了。
    - Context 章節明確指出：「拆出去：GSC / GA4 / Cloudflare 三家外部 API 整合 → **ADR-008 (Phase 2)**」。
    - 這個 blocker 在本 ADR 的範疇內已不適用，問題被合理地延後到專門的 ADR-008 處理。

---

### 2. 新發現的問題

修訂版品質很高，幾乎沒有引入新問題，但暴露了一個執行層面的風險。

- **問題描述**：ADR §2 中提到 Setup 流程：「見 [docs/runbooks/uptimerobot-setup.md](../runbooks/uptimerobot-setup.md)（本 ADR 產出後下一步建立）」。雖然 ADR 內文列出了最小步驟，但實際執行時，若 runbook 沒有即時建立或品質不佳，UptimeRobot 的 maintenance window、SMS 告警條件（DOWN for N minutes）等細節可能被遺漏，導致外部探針配置錯誤。
- **嚴重度**：Medium
- **建議修法**：在 ADR 的「開工 Checklist」中，將「**產出 `uptimerobot-setup.md` 並由另一位團隊成員 review 通過**」列為一個獨立、必須勾選的任務項，確保 runbook 的品質與及時性。

---

### 3. 修訂品質評估

| 維度 | 1-10 分 | 一句話評語 |
|---|---|---|
| Schema / 契約完整度 | 10 | 從 `/healthz` response 到 `alert_state` DDL，所有契約都已定義，且援引了通用原則。 |
| Reliability 機制（idempotency / atomic / SPOF）| 10 | 外部 probe 完美解決 SPOF，`flock` 解決 cron overlap，`alert_state` 處理冪等，無可挑剔。 |
| Observability（log / metric / SLO / probe）| 9 | `cron_runs` 和 `vps_metrics` 提供了扎實的數據基礎，SLO 清晰可量測；唯一扣分點是還需手動兜 Bridge。 |
| 可實作性（工程師照寫能不能動） | 10 | 極高。從模組切分、SQL schema 到 checklist，幾乎是 pseudo-code 等級的規格，可直接轉為 tickets。 |
| 範圍聚焦度（沒再 scope creep） | 10 | 堪稱典範。果斷將 GSC/GA4/Cloudflare 移出，讓 Phase 1 成為一個穩固、可兩週內交付的 MVP。 |

---

### 4. 最終判定

- **go / no-go**：**Go**。這份修訂版 ADR 品質極高，不僅完整解決了上一輪的所有核心 blocker，且在範圍、契約、可靠性上都做出了遠超預期的改進。工程師可以立即開工。

- **如果 no-go**：(不適用)

- **如果 go**：Phase 1 實作過程中要特別盯的風險點：
    1.  **VPS Baseline 實測**：這是唯一的「未知數」。必須嚴格執行 §6 的壓測，如果結果不符預期（例如 RAM headroom 不足），團隊必須有停下來重新決策的勇氣，而不是硬著頭皮做下去。
    2.  **`healthz` endpoint 的 LLM API check**：§3 中提到對 LLM API 的檢查是 `cached 30s`。要確保這個快取機制實作正確，避免 `/healthz` 被頻繁呼叫時意外產生大量 LLM API call，導致成本暴增或被 rate limit。