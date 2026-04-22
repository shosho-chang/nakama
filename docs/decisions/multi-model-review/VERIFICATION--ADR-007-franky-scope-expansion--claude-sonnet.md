---
source_adr: ADR-007-franky-scope-expansion
verification_round: 2
reviewer_model: claude-sonnet-4-6
elapsed_seconds: 108
review_date: 2026-04-22
---

# ADR-007-franky-scope-expansion — 修訂驗證（claude-sonnet）

# ADR-007 修訂版驗證報告

---

## 1. Blocker 逐項檢核

### Blocker 1：Franky SPOF / 監控物理分離

**原 blocker**：Franky cron 與被監控對象同在 VPS，VPS 掛 → 告警靜默死亡，無外部看門狗。

**修訂版回應**：✅ 完整解

**證據**：
- §1 表格第 6 項明列 UptimeRobot 外部 probe，頻率 5 分鐘，告警走 email + SMS
- §2 完整定義選型（UptimeRobot 免費方案）、三個監控 target、告警通道硬規則（**絕對不走 Slack / Franky DM**，理由明確）
- §2 Setup 步驟 1–5 直接寫在 ADR 本文（沒有只丟「見 runbook」）
- `Consequences → SPOF 表` 把 VPS 掛的影響、Phase 1 緩解、Phase 2 規劃三欄全列
- Franky heartbeat endpoint `/healthz` 在 §3 有完整契約定義，是外部 probe 的偵測目標

**細節確認**：`/healthz` 的 `cron_freshness` 檢查（最新 cron run < 15 min）讓外部 probe 能間接偵測 Franky cron 靜默死亡，這條邏輯鏈完整。

---

### Blocker 2：Alert deduplication 架構缺失

**原 blocker**：`alert_router.py` 無 alert state 儲存，Critical 條件持續成立時每個 cron 週期重發 DM；連續 N 次 fail 計數位置不對。

**修訂版回應**：✅ 完整解

**證據**：
- §4 定義 `AlertEventV1` schema（`dedup_key`、`dedup_window_seconds`、`fire_count`）
- §4 `alert_state` 表 DDL 完整，含 `suppress_until`、`state`（firing/resolved）、`fire_count`
- §4 發送邏輯四步驟明確，包含 suppress 邏輯與 resolve 通知（只發一次）
- 連續 N 次 fail 計數拆到 `health_probe_state` 表，由 `health_check.py` 管理，router 無狀態——這個架構切割是原 review 要求的，修訂版正確實作
- 冪等性聲明（`(dedup_key, fired_at)` 重放 UPSERT 安全）對應 `reliability.md §1`

**一個輕微觀察**（不影響 go/no-go）：`dedup_window_seconds` 預設 15 分鐘，但 §8 表格 Critical 條件「WP HTTP 非 200 連 3 次」的 3 次 × 5 分鐘 = 15 分鐘，suppress window 與偵測延遲等長，第一次告警發出後剛好壓到下一輪偵測視窗。**建議 Critical 規則的 dedup window 改為 30 分鐘**，避免 resolved 後立刻 re-fire 觸發 suppress 視窗空洞。但這屬於實作層調參，不是架構問題。

---

### Blocker 3：Cron job 管理策略未決

**原 blocker**：裸 system cron 無 overlap 保護、無 retry、無 job metadata，ADR 未明選 APScheduler 或 flock。

**修訂版回應**：✅ 完整解

**證據**：
- §7 明確選擇「system cron + `flock` wrapper」，理由列出（job 種類少、APScheduler overhead 不划算、與既有 cron 一致）
- 提供完整 cron wrapper bash 模板，`flock -n` 拿不到鎖直接退出（正確選擇，不排隊）
- `shared/cron_wrapper.py` context manager 統一處理 operation_id + `cron_runs` 寫入
- job metadata 由 `cron_runs` 表（§5 定義）覆蓋，status 含 `running/ok/failed/timeout`

**細節確認**：`flock -n` 策略意味著上一個 job 還在跑時本次直接 skip，這對 5-min health check 是合理的；但需要在 `cron_runs` 的 status 中有 `'skipped'` 或類似記錄，否則 `cron_freshness` 若連續 skip 會誤報 stale。**這是實作時要注意的，建議在 cron_runs 的 status enum 加 `'skipped'`，並在 `/healthz` 的 freshness 計算中排除 skipped。**

---

### Blocker 4：state.db schema 未定義 + migration 未規劃

**原 blocker**：新增表無 PK / 型別 / index 定義，schema migration 計劃缺失，多 writer 並發鎖問題。

**修訂版回應**：✅ 完整解

**證據**：
- §5 列出現有表盤點（無命名衝突確認）
- 五張新增表（`alert_state`、`health_probe_state`、`cron_runs`、`vps_metrics`、`r2_backup_checks`）全有完整 DDL，包含 PK、欄位型別、NOT NULL 約束、index
- Migration 執行方式明確：`migrations/002_franky_monitoring.sql`、備份用 `.backup` API（不用檔案 copy）、dry run 說明、down migration
- WAL 模式確認已在 `shared/state.py` L24 開啟
- 不引入 Alembic（Phase 1 決策，理由合理）

**唯一輕微缺口**：`vps_metrics` 的 `sampled_at TEXT PRIMARY KEY` 採用 ISO 8601 字串作為 PK，若同一秒有兩次採樣（理論上 5 分鐘間隔下不太可能）會衝突並靜默失敗。建議加 `INSERT OR IGNORE` 或改 `INTEGER PRIMARY KEY AUTOINCREMENT` + `CREATE UNIQUE INDEX`。**不是 blocker，實作層決策。**

---

### Blocker 5：VPS 資源 baseline 未實測

**原 blocker**：「<1% CPU」無量化依據，2vCPU/4GB 多 agent 疊加沒有測過。

**修訂版回應**：✅ 完整解（ADR 層面）

**證據**：
- §6 定義完整壓測方法（觸發情境、採樣頻率、持續 24 小時含 03:00 daily cron）
- 接受標準五項全有門檻數字（CPU p95 < 60%、RAM headroom ≥ 500 MB 等）
- 明確「不通過則不開工」
- 壓測報告列為 Phase 1 交付物（`docs/runbooks/vps-baseline-2026-04.md`）
- 未通過時的決策路徑（擴容 8GB / 關部分 agent / 降低 cron 頻率）在 ADR 層級寫了，不是丟給實作者猜

**注意**：baseline 是「開工前必做」，但目前還沒跑。這是設計上正確的——ADR 定義標準，執行在 checklist。這個 blocker 在 ADR 層面算解決，**實際解鎖 Phase 1 實作的前提是 baseline report 通過**。

---

### Blocker 6：API credentials 管理（Grok Blocking + 佐證）

**原 blocker**：Service Account JSON 存放位置、權限、scope checklist、R2 最小權限未定義。

**修訂版回應**：🔄 部分拆出去，Phase 1 範圍內有輕微缺口

**說明**：
- GSC / GA4 credentials 正確拆到 ADR-008（Phase 2）
- 但 Phase 1 仍有 **R2 credentials**（`r2_backup_verify.py` 需要）和 **Slack bot token** 兩組 credentials，修訂版沒有明確說明存放位置、chmod 600、env var 管理
- §9 只說「sanity：bot token 已依 runbook 完成」，R2 credentials 完全沒提
- Consolidated Review §5 Blocker 6 中「R2 最小權限（只 GetObject/ListBucket）」這條針對 Phase 1 的 R2 功能，修訂版未回應

**這是修訂版僅存的一個輕微缺口**，但因為 Phase 1 確實有 R2 操作，這條需要補。

---

## 2. 新發現的問題

### P1 — `/healthz` 公開暴露 + rate limit 執行機制未定義

**嚴重度**：High

**問題描述**：
§3 提到「rate limit 10 req/min」作為 mitigation，但：
1. 這個 rate limit 由誰執行？Nakama gateway 自己？Cloudflare？nginx？完全沒說。
2. 若是 Nakama gateway 自己實作，需要 in-memory counter 或 redis，但 Phase 1 沒有這個基礎設施。
3. 若由 Cloudflare WAF 規則執行，需要有對應的 WAF 設定步驟，ADR 沒有。
4. UptimeRobot 每 5 分鐘打一次 = 0.003 req/min，加上正常的外部掃描，10 req/min 門檻設定是否合理也沒有評估依據。

**建議修法**：明確指定 rate limit 實作層（首選：nginx `limit_req_zone`，最簡單且不依賴 Python 層），或承認 Phase 1 先不做 rate limit，改為「response 不含敏感資訊」作為唯一 mitigation，Phase 2 再補 WAF 規則。模糊的安全承諾比明確不做更危險。

---

### P2 — `cron_runs` 的 `started_at` 在 flock skip 情境下永遠不會寫入

**嚴重度**：High

**問題描述**：
§7 的 flock wrapper 在拿不到鎖時「直接退出」，但 Python 這端的 `cron_wrapper.py` 是靠 context manager 在 entry point 寫入 `cron_runs` row。問題是：flock skip 發生在 bash 層，Python process 根本不啟動，context manager 永遠不執行，`cron_runs` 沒有任何記錄。

後果：
- `/healthz` 的 `cron_freshness` 檢查「最新 cron run < 15 min」在 flock 連續 skip 時會誤判 stale，觸發假告警
- cron 週失敗率 SLO（`cron_runs.status='failed'` / total）分母失真
- 連鎖 watchdog 邏輯也會誤觸發

**建議修法**：在 bash wrapper 加一行：拿到鎖成功時才執行 Python，拿不到鎖時寫一筆 skip log 到 `franky.log`（不寫 DB，因為 Python 沒起來），同時在 `/healthz` 的 freshness 計算中改為「最新非 skipped 的 ok/failed run < 30 min（兩倍 interval）」，並在 `shared/cron_wrapper.py` 的 context manager 開頭立即寫入 `status='running'`，結束時 UPDATE，避免 Python crash 留下懸空 `running` 紀錄。

---

### P3 — Weekly digest 的 LLM 花費計算依賴 `api_calls` 表，但該表不在本 ADR 的 migration 範疇

**嚴重度**：Medium

**問題描述**：
§10 weekly digest 格式中有「本週 LLM 花費 → 讀 api_calls 表」，且引用 `observability.md §10`。`api_calls` 是現有表（§5 盤點中列出），但 Phase 1 的 Franky cron 本身不呼叫 LLM（digest 用 template），digest 裡的 LLM 花費數字是讀其他 agent（Brook、Nami）的 `api_calls`，不是 Franky 自己產生的。

問題不在資料存在與否，而在：Franky 讀其他 agent 的 `api_calls` 是否有定義好的 ownership？若 Brook 改變 `api_calls` 的 schema，Franky 的 digest 會靜默讀到錯誤數字（`extra="forbid"` 保護的是 write path，read path 沒有 schema guard）。

**建議修法**：在 digest template 的對應程式碼加 `try/except`，讀取失敗時 digest 那行改顯示「資料不可用」，不能讓 digest 因跨表讀取失敗而整個炸掉。或明確聲明 Franky 只讀 `api_calls` 的 subset（指定欄位），不依賴完整 schema。

---

### P4 — `systemd Restart=on-failure` 在「必須確認」的說法沒有確認機制

**嚴重度**：Medium

**問題描述**：
`Consequences → 負面` 寫「倚賴 systemd Restart=on-failure，**必須在 runbook 確認**」，但 Checklist A/B/C/D 都沒有這一步。若 systemd unit file 沒有 `Restart=on-failure`，Nakama service 崩掉後不會自動重啟，外部 probe 會在下一個 5 分鐘偵測到 503，但 Franky 的 WP health check 的「連 3 次 fail」狀態機也可能在 Nakama 自己掛掉時一起失效（因為 Nakama gateway 掛了，`health_check.py` 執行環境也可能有問題）。

**建議修法**：在 Checklist B 加一條 `[ ] 確認 nakama-gateway.service 與 nakama-bot.service 的 systemd unit file 含 Restart=on-failure Restart-Sec=10s`，並在 D 驗收中加故意 `kill -9` nakama-gateway 的測試案例。

---

### P5 — R2 備份驗證的「大小合理」門檻未定義

**嚴重度**：Low

**問題描述**：
§1 表格 item 4 寫「最新 object 存在且**大小合理**」，`r2_backup_checks` 表有 `latest_object_size` 欄位，§8 Critical 條件有「R2 備份連續 2 日失敗」，但「too_small 的門檻是多少 bytes？」在整份 ADR 找不到。

目前 weekly digest 顯示「最新物件 142MB」，但這是範例數字，不是門檻定義。

**建議修法**：在 §5 或 `r2_backup_verify.py` 模組說明中定義 `MIN_BACKUP_SIZE_BYTES = X`（e.g., 50 MB），並在 ADR 說明這個數字如何得到（例如：現有備份的 p10 大小 × 0.5，作為「備份嚴重截斷」的保護）。

---

## 3. 修訂品質評估

| 維度 | 分數 | 一句話評語 |
|---|---|---|
| Schema / 契約完整度 | 9 | 五張表 DDL 全有、Pydantic V1 schema 完整、`extra="forbid"` + `schema_version` 全到位；唯 R2 credentials scope 缺一筆 |
| Reliability 機制（idempotency / atomic / SPOF）| 8 | SPOF 表、dedup UPSERT 冪等、flock overlap 保護、WAL + `.backup` API 都到位；flock skip 不寫 cron_runs 是一個執行路徑缺口 |
| Observability（log / metric / SLO / probe）| 9 | SLO 六條全有量測通道、`cron_runs` + `vps_metrics` 雙資料源、外部 probe 契約完整、operation_id 整合；heartbeat 的 freshness 計算在 flock 情境有邏輯漏洞 |
| 可實作性（工程師照寫能不能動） | 8 | 模組切分清楚、Checklist A/B/C/D 步驟具體；`/healthz` rate limit 實作層未定、systemd 確認步驟漏掉，會讓工程師卡在「這誰負責？」 |
| 範圍聚焦度（沒再 scope creep） | 10 | 「不做的事」明確列了 6 項，GSC/GA4/Cloudflare 全部拆出，Phase 1 邊界清晰，沒有再偷偷放東西進來 |

---

## 4. 最終判定

### **✅ GO — 可以開始寫 code**

五個原始 blocker 在 ADR 架構層面全數解決，修訂版的架構決策品質顯著高於前版。以下兩件事要在**第一天實作前**補齊，不需要修改 ADR 主體，可以在 checklist 層級解決：

---

**實作前必做（2 項）**

**① R2 credentials 管理補一段**（對應原 Blocker 6 Phase 1 缺口）

在 Checklist B 加：
```
[ ] 確認 ~/.env 或 /etc/nakama/env 含 R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
[ ] chmod 600 對應 env 檔案
[ ] R2 API token 權限：Object Read Only（只 GetObject + ListBucket，不給 Delete/Write）
[ ] Slack bot token 存放位置確認同上，不能硬寫在程式碼
```

這四行不需要修 ADR 主文，加在 Checklist B 即可。

**② flock skip → cron_runs 記錄缺口**（P2 問題）

在 `shared/cron_wrapper.py` 的設計說明加一句：Python process 啟動後的第一行必須是寫入 `status='running'`，同時在 `/healthz` 的 `cron_freshness` 邏輯中把門檻從 `< 15 min` 改為 `< 30 min`（兩倍 interval，容忍一次正常的 flock skip），並確認 `status` enum 文件化包含 `skipped` 的處理方式（即使 skipped 不寫 DB，文件要說清楚）。

---

**Phase 1 實作過程中特別盯的風險（2 項）**

1. **`/healthz` rate limit 實作層懸空**：Week 1 上線 `/healthz` 時，工程師必須在當天決定 rate limit 由 nginx 還是 Python 層執行，不能到上線時才發現沒人做。建議 Week 1 task list 第一項就是「確認 nginx config 加 `limit_req_zone`」。

2. **VPS baseline 壓測是 Phase 1 的硬性前置條件**：§6 寫「不通過則不開工」，但 Checklist B 的 baseline 壓測和後續的 migration SQL、schema 寫作是並列的。若 baseline 不通過（RAM headroom < 500 MB），整個 Phase 1 要重新決策擴容，這個風險要在 Week 0 第一天就排掉，不能跟 code 工作平行進行。