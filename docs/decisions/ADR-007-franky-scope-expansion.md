# ADR-007: Franky Phase 1 — 基礎設施監控（slim 版）

**Date:** 2026-04-22
**Status:** Proposed
**Supersedes:** ADR-007 (2026-04-22, pre-multi-model-review)

---

## Context

原 ADR-007 將 Franky 職責一次擴張為「系統維護 + 外部監控 + SEO 觀測」三合一。Multi-model review（`multi-model-review/ADR-007--CONSOLIDATED.md`）三家模型一致指出：

1. **Scope 過寬** — infra / SEO / social 攪在一份 ADR 無法逐項收斂
2. **SPOF 未解** — Franky 跑在 VPS，VPS 掛 Franky 一起掛，內部 cron 不能是唯一告警路徑
3. **Alert dedup 架構缺失** — 同一 Critical 條件每個 cron 週期都會重發 DM
4. **state.db schema 未定義 + migration 未規劃**
5. **VPS 資源「<1% CPU」是拍腦袋** — 2vCPU/4GB 與既有負載疊加沒量化依據

本 ADR（slim 版）回應這五項 blocker 並**聚焦 Franky Phase 1 基礎設施監控**：

- 放進來：VPS 資源 / WP health / Nakama service health / R2 備份 / Franky Slack bot / 外部 uptime probe
- 拆出去：GSC / GA4 / Cloudflare 三家外部 API 整合 → ADR-008（Phase 2）
- `config/target-keywords.yaml` 三方共用 schema → 留給 ADR-008 或後續獨立 ADR

Zoro-Franky 分工維持原決策，但具體 keyword 追蹤實作落在 ADR-008。

**援引原則**：
- Schema：`docs/principles/schemas.md` §1（契約先於實作）、§3（schema_version 硬規則）、§5（aware UTC）
- Reliability：`docs/principles/reliability.md` §1（冪等）、§2（atomic claim）、§4（SPOF 表）、§5（retry/backoff）、§10（crash safety）
- Observability：`docs/principles/observability.md` §2（operation_id）、§3（外部 uptime probe）、§4（/healthz 契約）、§5（SLO）、§6（Alert dedup）

---

## Decision

### 1. Phase 1 範圍（六項，其他通通拆出去）

| # | 檢查項 | 頻率 | 告警通道 |
|---|---|---|---|
| 1 | VPS 資源（CPU / RAM / disk / load / swap） | 每 5 分鐘 | Critical → Franky DM；Warning → Bridge + digest |
| 2 | WP 兩站 HTTP health（shosho.tw / fleet.shosho.tw）內部 probe | 每 5 分鐘 | 同上 |
| 3 | Nakama service health（gateway + Slack bot + cron watchdog） | 每 5 分鐘 | 同上 |
| 4 | R2 備份驗證（最新 object 存在且大小合理） | 每日 03:00 | 連 2 日失敗 → Critical |
| 5 | Slack Franky bot（Critical DM + weekly digest） | — | — |
| 6 | **外部 uptime probe（UptimeRobot）** | 每 5 分鐘 | email + SMS（繞過 VPS） |

### 2. SPOF 緩解 — 外部 uptime probe（blocker #1，必做）

**為什麼必做**：`reliability.md` §4 明列 VPS 為首要 SPOF。Franky 本身在 VPS 內，VPS 掛 = Franky 靜默死亡。Phase 1 最後一道防線是外部 probe。

**選型**：UptimeRobot 免費方案（50 monitors / 5-min interval，見 `observability.md` §3 表格）。

**監控 targets（三個 public URL）**：

```
https://nakama.shosho.tw/healthz    # Nakama gateway heartbeat（本 ADR §3 定義契約）
https://shosho.tw/                  # WP blog
https://fleet.shosho.tw/            # WP community
```

**告警通道**（硬規則）：

- **email + SMS**（UptimeRobot 兩者免費組合，見 runbook）
- **絕對不走 Slack / Franky DM**（這兩個依賴 VPS，繞圈告警失效）
- SMS 每月免費額度 20 則，只設給 3 個 target 的 DOWN state（不含 WARNING）

**Setup 流程** — 見 [docs/runbooks/uptimerobot-setup.md](../runbooks/uptimerobot-setup.md)（本 ADR 產出後下一步建立；最小步驟列在本節，不能只寫「見 runbook」）：

1. 申請 UptimeRobot 免費帳號，綁修修主要 email + 一組台灣手機號
2. 新增 3 個 monitor（HTTP(s)，5-min interval，timeout 30s）
3. Alert contact：email always + SMS only on DOWN
4. Maintenance window：排除 VPS 每週二 03:00-03:30（預留 OS patch）
5. 匯出 status page（private link，只給修修）為未來 Chopper 社群信任訊號備用

### 3. `/healthz` 契約（blocker 配套）

Nakama gateway（`thousand_sunny/`）暴露 `/healthz`，遵守 `observability.md` §4：

**行為規範**：

- **不需 auth**（外部 probe 要能打）
- **p95 < 50ms**，不做重操作
- 只回 200 / 503 兩個狀態碼
- 檢查項：
  1. `state.db` connectable（sqlite `SELECT 1`）
  2. LLM API reachable（**cached 30s**，不每次打外部 API）
  3. 最新 cron run 時間 < 15 min（讀 `cron_runs` 表，見 §5）

**Response schema**（照 `observability.md` §4）：

```json
{
  "status": "ok | degraded",
  "service": "nakama-gateway",
  "version": "0.x.y",
  "checks": {
    "db": "ok | error",
    "llm_api": "ok | timeout | error",
    "cron_freshness": "ok | stale"
  },
  "uptime_seconds": 3600,
  "schema_version": 1
}
```

Schema 定義位置：`shared/schemas/monitoring.py` 的 `HealthzResponseV1`，遵守 `schemas.md` §3（`schema_version: Literal[1]`）與 §4（`extra="forbid"`）。

### 4. Alert dedup（blocker #2）

**問題**：cron 每 5 分鐘跑一次，若不做 dedup，disk>95% 會每 5 分鐘 DM 一次 → 24 小時 288 則訊息 → 告警疲勞 → 重要告警被忽略。

**解法**：`alert_state` 表 + per-rule dedup window。

**Schema**（在 `shared/schemas/monitoring.py`）：

```python
class AlertEventV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    rule_id: constr(pattern=r"^[a-z_][a-z0-9_]{2,63}$")  # e.g. "disk_critical"
    severity: Literal["critical", "warning", "info"]
    message: constr(min_length=1, max_length=500)
    fired_at: AwareDatetime
    dedup_key: str                   # 預設 = rule_id；可加維度（如 host）
    dedup_window_seconds: int = 900  # 預設 15 min，可 per-rule 覆寫
```

**DB 表**（§5 migration 一併建立）：

```sql
CREATE TABLE alert_state (
    dedup_key          TEXT PRIMARY KEY,
    rule_id            TEXT NOT NULL,
    last_fired_at      TEXT NOT NULL,   -- ISO 8601 + tz
    suppress_until     TEXT NOT NULL,   -- ISO 8601 + tz
    state              TEXT NOT NULL,   -- 'firing' | 'resolved'
    last_message       TEXT NOT NULL,
    fire_count         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_alert_state_suppress ON alert_state(suppress_until);
```

**發送邏輯**（`agents/franky/alert_router.py`）：

1. Event 進來 → 計算 `dedup_key`
2. 查 `alert_state`：若 `suppress_until > now` → 累加 `fire_count`、更新 `last_fired_at`，**不發** DM
3. 否則 → 發 DM、寫 `last_fired_at = now`、`suppress_until = now + dedup_window_seconds`、state='firing'
4. 條件解除時（下次 cron 驗證健康）→ 更新 state='resolved'，發「已恢復」DM（不 dedup，resolve 只發一次）

**冪等性**（`reliability.md` §1）：同一 `(dedup_key, fired_at)` 重放 UPSERT 安全。

**連續 N 次 fail 計數**（`health_check.py` 內部，不丟給 router）：

WP HTTP health 要求「連 3 次 fail 才 Critical」。這個狀態放在 `health_check.py` 的獨立表 `health_probe_state`，router 只消費「已判定為 firing」的事件：

```sql
CREATE TABLE health_probe_state (
    target             TEXT PRIMARY KEY,  -- 'wp_shosho' | 'wp_fleet' | 'nakama_gateway'
    consecutive_fails  INTEGER NOT NULL DEFAULT 0,
    last_check_at      TEXT NOT NULL,
    last_status        TEXT NOT NULL      -- 'ok' | 'fail'
);
```

Probe 連 3 次 fail → 推 AlertEventV1 進 router。這樣 router 無狀態、好測試。

### 5. state.db migration 路徑（blocker #4）

**現狀盤點**（讀 `shared/state.py` 目前 schema）：

現有表：`files_processed`、`agent_runs`、`api_calls`、`scout_seen`、`community_alerts`、`files_read`、`agent_events`、`event_consumptions`、`memories` + FTS5。無命名衝突。

**Phase 1 新增表**：

```sql
-- migrations/002_franky_monitoring.sql

-- 1. Alert 去重狀態
CREATE TABLE IF NOT EXISTS alert_state (
    dedup_key          TEXT PRIMARY KEY,
    rule_id            TEXT NOT NULL,
    last_fired_at      TEXT NOT NULL,
    suppress_until     TEXT NOT NULL,
    state              TEXT NOT NULL,
    last_message       TEXT NOT NULL,
    fire_count         INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_alert_state_suppress
    ON alert_state(suppress_until);
CREATE INDEX IF NOT EXISTS idx_alert_state_rule
    ON alert_state(rule_id, last_fired_at DESC);

-- 2. 連續失敗計數
CREATE TABLE IF NOT EXISTS health_probe_state (
    target             TEXT PRIMARY KEY,
    consecutive_fails  INTEGER NOT NULL DEFAULT 0,
    last_check_at      TEXT NOT NULL,
    last_status        TEXT NOT NULL
);

-- 3. Cron 執行歷史（observability.md §1 metric 也從這裡推）
CREATE TABLE IF NOT EXISTS cron_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name           TEXT NOT NULL,
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    status             TEXT NOT NULL,           -- 'running' | 'ok' | 'failed' | 'timeout'
    duration_ms        INTEGER,
    error_msg          TEXT,
    operation_id       TEXT NOT NULL            -- observability.md §2
);
CREATE INDEX IF NOT EXISTS idx_cron_runs_job_time
    ON cron_runs(job_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_cron_runs_status
    ON cron_runs(status, started_at DESC);

-- 4. VPS 資源時序（metric 儲存）
CREATE TABLE IF NOT EXISTS vps_metrics (
    sampled_at         TEXT NOT NULL,           -- ISO 8601 + tz
    cpu_pct            REAL NOT NULL,
    ram_used_mb        INTEGER NOT NULL,
    ram_total_mb       INTEGER NOT NULL,
    swap_used_mb       INTEGER NOT NULL,
    disk_used_pct      REAL NOT NULL,
    load_1m            REAL NOT NULL,
    PRIMARY KEY (sampled_at)
);
CREATE INDEX IF NOT EXISTS idx_vps_metrics_time
    ON vps_metrics(sampled_at DESC);

-- 5. R2 備份驗證紀錄
CREATE TABLE IF NOT EXISTS r2_backup_checks (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    checked_at         TEXT NOT NULL,
    latest_object_key  TEXT,
    latest_object_size INTEGER,
    latest_object_mtime TEXT,                   -- ISO 8601 + tz (Asia/Taipei)
    status             TEXT NOT NULL,           -- 'ok' | 'stale' | 'missing' | 'too_small'
    detail             TEXT
);
CREATE INDEX IF NOT EXISTS idx_r2_backup_time
    ON r2_backup_checks(checked_at DESC);
```

**執行方式**：

- 檔案放 `migrations/002_franky_monitoring.sql`（沿用既有 `shared/state.py` 的 `executescript` 風格，Phase 1 不引入 Alembic）
- 上線前：先 `sqlite3 state.db .backup state.db.pre-002.bak`（遵守 `reliability.md` §11 可回滾）
- Dry run：讀 `.sql` 解析但不執行，確認無 `DROP` / `ALTER COLUMN` 等破壞性語句
- Rollback：down migration 寫在 `migrations/002_franky_monitoring.down.sql`（DROP 四張新表）

**WAL 已開**（`shared/state.py` L24 已 `PRAGMA journal_mode=WAL`），符合 `reliability.md` §10。

### 6. VPS baseline 壓測（blocker #5）

**目的**：驗證新增 cron 疊加現有負載後 VPS 仍有 headroom。

**開工前必做**，不是開工後觀察。

**方法**：

1. 在當前 VPS 裝 `vmstat` + `iostat` 採樣腳本，每 30 秒記錄一筆到臨時 log
2. 觸發同時負載情境：
   - Brook 寫一篇長文（LLM 推理高峰）
   - Nami agent loop 執行中
   - 手動觸發所有新 Franky cron（health check + VPS snapshot + R2 verify）
3. 持續 24 小時，包含 daily cron 03:00 R2 備份觸發時刻
4. 匯出報告 → `docs/runbooks/vps-baseline-2026-04.md`

**接受標準**：

| 指標 | 門檻 | 備註 |
|---|---|---|
| CPU p95 | < 60% | 2 vCPU，單核飽和 = 50% |
| RAM used | < 3 GB（預留 ≥ 1 GB headroom） | 含 buffer/cache 扣除 |
| RAM headroom | ≥ 500 MB | Python startup 疊加考量 |
| Disk 成長率 | ≤ 100 MB/day | log/db 寫入可控 |
| Swap 使用 | < 20% | 避免觸發 critical 告警條件 |

**若未通過**：

- RAM headroom 不足 → 先決策擴容到 8GB 或關掉部分 agent（ADR 層級決策，不是實作層）
- CPU p95 > 60% → 考慮將 5-min cron 改為 10-min，或合併多個 cron 成單一 wrapper

**不通過則不開工**。基準報告是 Phase 1 的交付物之一。

### 7. Cron 管理策略

**選擇**：system cron + `flock` wrapper（不引入 APScheduler）。

**理由**：

- Phase 1 job 種類少（5 min × 1、daily × 2、weekly × 1），APScheduler 的 overhead（額外 daemon、persistent store）性價比低
- `flock` 解決 job overlap 問題（`reliability.md` §10 crash safety）
- 與既有 cron（PubMed digest、backup）一致，不增運維成本

**Wrapper 模板**（所有 Franky cron 必經）：

```bash
# /etc/cron.d/nakama-franky
*/5 * * * * nakama flock -n /var/lock/franky_health.lock \
  /home/nakama/.venv/bin/python -m agents.franky.health_check \
  >> /var/log/nakama/franky.log 2>&1
```

- `flock -n` 拿不到鎖直接退出（不排隊）
- Python 這端在 entry point 啟動 `cron_runs` row，結束時 update status（遵守 `reliability.md` §10 transaction 包寫）
- 統一透過 `shared/cron_wrapper.py` 的 context manager 處理 operation_id 生成與 cron_runs 寫入

### 8. 告警三級制（僅列 Phase 1 infra 相關）

| 級別 | 通道 | 條件（任一觸發；配 §4 dedup） |
|---|---|---|
| Critical | Franky DM + 外部 probe email/SMS | • WP HTTP 非 200（連 3 次 fail，§4 state machine）<br>• Nakama service 崩 + 重啟失敗<br>• `/healthz` 503 連 3 次（外部 probe 觸發）<br>• VPS disk > 95% 或 free < 5GB<br>• VPS RAM full + swap > 80%<br>• R2 備份連續 2 日失敗 |
| Warning | Bridge dashboard + weekly digest | • RAM > 85% 超過 10 分鐘<br>• CPU 15-min avg > 90%<br>• disk 週成長 > 500MB<br>• cron 單日失敗率 > 20% |
| Info | Bridge dashboard | • 日常 VPS 指標<br>• cron 成功紀錄<br>• R2 備份成功 |

**SEO / Cloudflare / plugin CVE 等條件移到 ADR-008**。

### 9. Slack Franky bot

- 獨立 Slack app（sanity：bot token 已依 `docs/runbooks/add-agent-slack-bot.md` 完成，見 `project_phase1_infra_checkpoint.md`）
- Scope：`chat:write` + `im:write`（單向 DM）
- 接收 DM target：`SLACK_USER_ID_SHOSHO`
- 發送類型：Critical alert / Weekly digest / 啟動通知
- **不訂閱 events、不提供 interactivity**（降低 attack surface）

### 10. Weekly digest（Phase 1 格式 — 純 infra）

週一 10:00 產出，貼 Bridge + Franky DM。格式（比原 ADR 簡單）：

```markdown
# Franky Weekly Digest — 2026-04-22

## 系統健康
- VPS: CPU avg 14% / p95 41%、RAM avg 62%、disk 26%、load 0.08 — healthy
- Nakama services: uptime 7d 0h（0 次 restart）
- WP × 2: uptime 100%, avg response 340ms

## Cron 執行
- 總執行：2016 次（health × 2016 + daily × 7 + weekly × 1）
- 成功率：99.8%（4 次 timeout，皆 retry 成功）
- 最慢 job：r2_backup_verify p95 18.4s

## 備份
- R2 備份：7/7 天成功，最新物件 142MB（+3MB w/w）

## 告警
- Critical：0 次
- Warning：2 次（RAM > 85% × 2，晚間 LLM 高峰）

## 本週 LLM 花費
- 總計 $X.XX（vs 上週 +Y%）  ← 讀 api_calls 表，`observability.md` §10
```

Phase 1 **不用 LLM 生成** digest，純數字 + 固定 template（Python 字串格式化），避免多花 LLM 成本。

### 11. 模組切分

```
agents/franky/
  __main__.py            # weekly digest entry
  health_check.py        # 5-min cron: VPS + WP + Nakama + /healthz
  vps_monitor.py         # VPS resource snapshot（併到 health_check 或獨立）
  r2_backup_verify.py    # daily 03:00 cron
  alert_router.py        # §4 dedup + DM 發送
  slack_bot.py           # Franky DM 客戶端
shared/
  cron_wrapper.py        # §7 context manager（operation_id + cron_runs 寫入）
  schemas/
    monitoring.py        # AlertEventV1 / HealthzResponseV1 / VPSMetricV1 / R2CheckV1
migrations/
  002_franky_monitoring.sql
  002_franky_monitoring.down.sql
```

---

## Consequences

### 正面
- SPOF 補強（外部 probe），VPS 掛時至少 email/SMS 一條路能通
- Alert dedup 架構前置，避免告警疲勞
- state.db schema 與 migration 顯式化，Phase 2 新增 SEO 表不會撞上
- Phase 1 範圍收斂，2 週內可交付可驗證的 infra 監控 MVP
- cron_runs + vps_metrics 同時成為 Bridge dashboard 的資料源（`observability.md` §1 metric）

### 負面
- UptimeRobot 依賴外部 SaaS；若 UptimeRobot 本身掛，降級為 email-only（Phase 2 可補 Better Uptime 作為 secondary）
- `flock` 策略不追蹤 job metadata 細節（只記 start/end），比 APScheduler 粗
- 沒有 auto-recover（例如 Nakama service 崩掉不會自動重啟；依賴 systemd Restart=on-failure，**必須在 runbook 確認**）

### 風險
- 外部 probe 與本機 probe 不同步判定（e.g., 本機回 200 但 CF → origin 500） → 告警矛盾。由 UptimeRobot 為外部權威、Franky 為內部補充，文件化此優先序
- `/healthz` 暴露在公網可能被掃；mitigation：response 不含敏感資訊、rate limit 10 req/min、non-auth 只回狀態
- 外部 probe SMS 月 20 則免費額度若被 flapping 打爆 → mitigation：UptimeRobot 設「DOWN for 10 min 才 SMS」

### SPOF 表（硬規則，`reliability.md` §4）

| SPOF | 影響 | Phase 1 緩解 | Phase 2 規劃 |
|---|---|---|---|
| VPS | Franky 全停 | 外部 UptimeRobot email+SMS | secondary probe（Better Uptime）|
| state.db 毀損 | alert_state / cron_runs 全失 | 每日 R2 備份 + WAL + `.backup` API | litestream 實時同步 |
| UptimeRobot 掛 | 外部告警失效 | email-only 降級 | 加第二家 probe |
| Slack outage | Franky DM 失敗 | 外部 probe email 繞過 | SMTP/Resend email fallback |
| `SLACK_USER_ID_SHOSHO` 變更 | DM 失敗 | bot 啟動時驗 user id，失敗即崩（非靜默） | — |

---

## SLO（遵守 `observability.md` §5）

| 指標 | 目標 | 量測 |
|---|---|---|
| cron 單次執行時間 | p95 < 30 秒 | `cron_runs.duration_ms` |
| cron 週失敗率 | < 1% | `cron_runs.status='failed'` / total |
| Critical 事件 → DM 送達 | p95 < 3 分鐘（含外部 probe 偵測延遲） | event 時間戳 vs Slack `ts` |
| `/healthz` 回應時間 | p95 < 50 ms | gateway access log |
| `/healthz` 可用性 | > 99%（月） | UptimeRobot report |
| Alert dedup 正確性 | 同 dedup_key 15 分鐘內 DM 1 次 | 手動抽檢 Slack 歷史 |

**違反 SLO → 修、不是喊口號。三週連續違反 → 重新設計。**

---

## 開工 Checklist

### A. ADR 通過 → 修修端準備（30 分鐘）

- [ ] UptimeRobot 帳號建立 + 3 個 monitor + email/SMS alert contact
- [ ] 確認 VPS 台灣手機號接收 SMS OK（試打一次）
- [ ] 讀 `reference_vps_paths.md` 確認 state.db 位置（`/home/nakama/data/state.db`）

### B. Phase 1 開工前（Claude Code 端，1 天）

- [ ] 跑 VPS baseline 壓測（§6）→ 交付 `docs/runbooks/vps-baseline-2026-04.md`
- [ ] baseline 通過 → 寫 migration SQL（`migrations/002_franky_monitoring.sql` + down）
- [ ] 寫 `shared/schemas/monitoring.py`（4 個 V1 schema，`extra="forbid"` + `schema_version`）
- [ ] 寫 `shared/cron_wrapper.py`（context manager，operation_id + cron_runs 寫入）

### C. Phase 1 實作順序（2 週）

- [ ] week 1：migration + `/healthz` endpoint + health_check.py + alert_router.py + Franky Slack bot + flock cron
- [ ] week 1 end：UptimeRobot ↔ `/healthz` 連通測試
- [ ] week 2：vps_monitor + r2_backup_verify + weekly digest template + Bridge dashboard 讀 cron_runs/vps_metrics
- [ ] week 2 end：跑 72 小時 soak test，驗 alert dedup 正確、cron 無 overlap、digest 產出正常

### D. Phase 1 完工驗收

- [ ] 三條 SLO 量測通道全通（cron_runs、access log、Slack ts）
- [ ] 故意關閉 Nakama gateway 30 秒 → 外部 probe 在 10 分鐘內 SMS 送達
- [ ] 故意 `dd if=/dev/zero of=/tmp/fill` 到 disk > 95% → Franky DM 一次、15 分鐘內不重發
- [ ] `docs/incidents/` 首份 post-mortem 模板就緒（`observability.md` §11）

---

## 不做的事（Phase 1 明確排除）

- ❌ GSC / GA4 / Cloudflare 整合（→ ADR-008）
- ❌ Plugin 自動更新（Critical 告警手動處理；避免 auto-update 把站搞壞）
- ❌ LLM 生成 digest（Phase 1 純 template，Phase 2 再評估）
- ❌ APScheduler / Prometheus / Grafana（VPS 資源有限，先用 SQLite + Bridge 就夠）
- ❌ Circuit breaker（`reliability.md` §6 明列 Phase 2）
- ❌ Auto-recover Nakama service（倚賴 systemd Restart=on-failure，手動確認 unit file）

---

## Notes

- 本 ADR 援引 `docs/principles/` 三份（schemas / reliability / observability）所有硬規則，違反任一即 reject
- 與 ADR-001 Franky 定義相容（只做 Phase 1 收斂）
- 與 ADR-006 approval queue 共存於同一 state.db，migration 測試要確認無鎖衝突
- `config/target-keywords.yaml` 三方共用 schema 由 ADR-008 處理（Zoro / Usopp / Franky 共用）
