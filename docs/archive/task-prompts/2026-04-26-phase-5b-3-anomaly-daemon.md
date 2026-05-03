# Phase 5B-3 — Anomaly daemon（cost / error / latency 3σ）

**Framework:** P9 六要素（CLAUDE.md §工作方法論）
**Status:** 草稿，**待修修凍結後動手**（前提：Phase 5C PR #182 merged）
**Source plan:** [`docs/plans/quality-bar-uplift-2026-04-25.md`](../plans/quality-bar-uplift-2026-04-25.md) §Phase 5
**Pickup memo:** [`memory/claude/project_quality_uplift_next_2026_04_27.md`](../../memory/claude/project_quality_uplift_next_2026_04_27.md)
**Reference impls:**
- [`shared/heartbeat.py`](../../shared/heartbeat.py) — heartbeat staleness probe (5B-1) 樣板
- [`shared/alerts.py`](../../shared/alerts.py) — alert + dedup 樣板（沿用）
- [`agents/franky/health_check.py`](../../agents/franky/health_check.py) — probe registry pattern（5B-1 / 5D）

---

## §1. 目標（一句話）

每 15 分鐘掃過去 1h vs 過去 7d 的 cost / latency / error rate baseline，3σ 偏離自動發 Slack 警告，讓修修在 LLM 帳單炸前 / agent 集體變慢前 / cron 集體 fail 前看到。

## §2. 範圍（精確檔案路徑）

| 檔案 | 動作 | 理由 |
|---|---|---|
| `agents/franky/anomaly_daemon.py` | **新建** | 主迴圈：跑 4 類 anomaly check，每個 check 獨立 module function；reuse `shared.alerts.alert()` 發 Slack |
| `shared/anomaly.py` | **新建** | 純函式：rolling baseline 計算（mean / stddev / sample size gate）、3σ 判定、`AnomalyV1` schema |
| `shared/schemas/franky.py` | **改** | 加 `AnomalyV1` Pydantic schema（mirror `HealthProbeV1` shape） |
| `agents/franky/__main__.py` | **改** | 加 `anomaly` subcommand（mirror `health`） |
| `tests/agents/franky/test_anomaly_daemon.py` | **新建** | 8+ tests 覆蓋 4 類 check + dedup + sample-size gate |
| `tests/shared/test_anomaly.py` | **新建** | 純函式 unit test（baseline math + 3σ gate） |
| `agents/franky/health_check.py` | **改** | 註冊 `nakama-anomaly-daemon` 進 `CRON_SCHEDULES`（15min interval, 5min grace） |
| Crontab on VPS | **改**（manual） | `*/15 * * * * cd /home/nakama && /usr/bin/python3 -m agents.franky anomaly` |

預估 line delta：~700 added / ~10 modified。

## §3. 輸入（依賴）

- ✅ `state.db` `api_calls` table — agent / model / input/output/cache tokens / latency_ms / called_at（5A 已落地）
- ✅ `data/logs.db` (Phase 5C — 待 PR #182 merge) — level / logger / msg / ts；error rate 由 `WHERE level IN ('ERROR','CRITICAL')` 計
- ✅ `state.db` `heartbeat` table — cron consecutive_failures（5B-1 已落地）
- ✅ `shared.alerts.alert()` — Slack DM + dedup 既有契約（不改）
- ✅ `shared.heartbeat.record_success/failure` — daemon 自身 heartbeat（mirror franky-health）
- ⚠️ Pricing data — `shared/pricing.py` 既有 `calc_cost(model, input_tokens, output_tokens)` 把 token 換 USD，cost anomaly 用得到

## §4. 輸出（交付物）

### 4.1 Anomaly types

每個 anomaly 對應一個 `check_*()` function in `agents/franky/anomaly_daemon.py`：

| # | name | 算式 | 觀測窗 | baseline 窗 | sample-size gate |
|:---:|---|---|---|---|---|
| 1 | **cost spike** | `sum(input+output+cache_write tokens) × 模型單價` (USD) GROUP BY agent | 過去 60 min | 過去 7 天（同 weekday hour）| 至少 24 個 baseline 點 |
| 2 | **latency p95 spike** | `percentile_cont(0.95) over latency_ms` GROUP BY agent | 過去 60 min | 過去 7 天 | 至少 50 個 api_calls 在 baseline 窗 |
| 3 | **error rate spike** | `count(*) FROM logs WHERE level IN ('ERROR','CRITICAL')` | 過去 60 min | 過去 7 天 | 至少 24 個小時 baseline |
| 4 | **cron failure cluster** | `count(*) FROM heartbeat WHERE consecutive_failures >= 3` | 當下 snapshot | 上次 daemon tick（state-based） | 不需 — single-shot transition |

### 4.2 `shared/anomaly.py` 公開 API（純函式）

```python
@dataclass
class BaselineStats:
    mean: float
    stddev: float
    n: int  # sample count

def rolling_baseline(values: list[float]) -> BaselineStats: ...
def is_3sigma_anomaly(current: float, baseline: BaselineStats, *, min_n: int = 24) -> bool: ...
```

`is_3sigma_anomaly` 規則：
- baseline.n < min_n → False（樣本不夠不報警）
- stddev == 0 → 用 `(current > mean * 1.5)` 替代（防止 baseline flat 時除零 + spurious alert）
- 否則 `(current - mean) / stddev > 3.0` → True

### 4.3 `AnomalyV1` schema（mirror `HealthProbeV1`）

```python
class AnomalyV1(BaseModel):
    schema_version: int = 1
    metric: Literal["cost_spike", "latency_p95_spike", "error_rate_spike", "cron_failure_cluster"]
    target: str  # agent name OR "_global"
    current: float
    baseline_mean: float
    baseline_stddev: float
    sample_size: int
    detail: dict
    detected_at: datetime
```

### 4.4 Daemon entry point

```python
# agents/franky/anomaly_daemon.py
def run_once() -> list[AnomalyV1]:
    """每 15 min cron 跑一次，每個 check 跑完後送 alert（dedup'd）。
    回傳清單便於 dashboard 顯示 + 測試 introspect。"""
    anomalies = []
    anomalies.extend(check_cost_spike())
    anomalies.extend(check_latency_p95_spike())
    anomalies.extend(check_error_rate_spike())
    anomalies.extend(check_cron_failure_cluster())
    for a in anomalies:
        alert(
            category="anomaly",
            message=_format_alert(a),
            dedupe_key=f"anomaly:{a.metric}:{a.target}",
            dedupe_minutes=60,
        )
    record_success("nakama-anomaly-daemon")
    return anomalies
```

### 4.5 Cron + heartbeat

- crontab：`*/15 * * * * cd /home/nakama && /usr/bin/python3 -m agents.franky anomaly >> /var/log/nakama/anomaly-daemon.log 2>&1`
- heartbeat key：`nakama-anomaly-daemon`
- `CRON_SCHEDULES`：`(15, 5)` — 15min interval, 5min grace（嚴一點，因為這是 high-frequency entry）

## §5. 驗收（Definition of Done）

| # | 條件 | 驗證方式 |
|:---:|---|---|
| 1 | 4 類 check 都有 unit test，正例（觸發 3σ）/ 負例（在 baseline 內）/ 樣本不足都覆蓋 | `pytest tests/agents/franky/test_anomaly_daemon.py` |
| 2 | `shared.anomaly.is_3sigma_anomaly` stddev=0 不除零，flat baseline 時用 1.5x 規則 | unit |
| 3 | `run_once()` empty `api_calls` / 空 logs / 空 heartbeat 全 graceful（不 raise，回 []）| unit |
| 4 | dedup 60 min — 同 metric+target 第二次不再發 Slack | unit + 觀察 sentinel record |
| 5 | full suite pytest 0 fail | CI |
| 6 | VPS deploy + cron 跑過 1 個 tick 後 `heartbeat.last_status == 'success'` for `nakama-anomaly-daemon` | ssh sqlite3 query |
| 7 | 故意製造 1h 大量 ERROR log（dev tool）→ daemon 該 tick 發 Slack | manual |

## §6. 邊界（明確不能碰）

**不可碰**：
- ❌ `shared/alerts.py` 不改（用既有 `alert()` 介面，dedup 走既有 sentinel table）
- ❌ `state.db` 不加 schema — 所有計算 read-only
- ❌ 不做 ML / forecast — 純 3σ 統計，文件化「first iteration」
- ❌ 不在 daemon 內做修復動作（auto-recover）— 只警告，人類介入
- ❌ 不開新 dashboard — 結果先進 Slack DM；下一輪（5B-3+1）再考慮 `/bridge/anomaly` 頁面

**可選 / 留下次**：
- p99 latency vs p95 — 第一輪只 p95；夠戲劇性的 spike 都會抓到
- per-model（不只 per-agent）— 第一輪 by agent；分 model 留 follow-up
- weekday-aware baseline（週末 vs 平日 cost pattern 不同）— 第一輪走 trailing 7d；如果誤報多再加

## §7. 推薦執行序

1. `shared/schemas/franky.py` 加 `AnomalyV1` schema
2. `shared/anomaly.py` baseline 純函式 + tests（不 touch db，純 list[float] math）
3. `agents/franky/anomaly_daemon.py` `check_cost_spike` + tests（read-only api_calls query）
4. 同檔 `check_latency_p95_spike` + tests
5. 同檔 `check_error_rate_spike` + tests（依賴 5C `data/logs.db` schema）
6. 同檔 `check_cron_failure_cluster` + tests
7. `run_once()` orchestrator + integration test
8. `agents/franky/__main__.py` `anomaly` subcommand
9. `agents/franky/health_check.py` CRON_SCHEDULES 註冊
10. Ruff + 全 suite + PR + ultrareview

## §8. 風險

- **Baseline cold start**：頭 7 天無 baseline data → sample-size gate 防止誤報，但 daemon 跑空 7 天才有用。文件化 expected。
- **Sleep mode false positive**：如果某 agent 整週沒跑，今天突然跑 → 看似 spike，其實是 first-after-idle。緩解：sample-size gate（n < 24 不報警）。
- **Slack DM noise**：4 類 anomaly × N agents 可能一次發多條。dedup 60min + per-(metric, target) dedup_key 控制。
- **`api_calls.called_at` timezone drift**：既有 schema 是 ISO8601 string，UTC 寫入。query window 用 `datetime.now(timezone.utc) - timedelta(hours=1)` ISO 比較。已有 5A pattern 可借鑒。
- **Phase 5C 依賴**：error_rate_spike 讀 `data/logs.db`。**5B-3 必須在 5C merge + VPS deploy 後才能啟用**，否則 `data/logs.db` 不存在 / 是空的，sample-size gate 會 always-skip → 該類 check 自動 dormant 直到 5C live。架構上沒問題，只是時序。

## §9. 待決定（凍結前 user 拍板）

| # | 題目 | 預設 | 替代 |
|:---:|---|---|---|
| Q1 | 觀測窗 | **過去 60 min** | 30 / 120 |
| Q2 | baseline 窗 | **過去 7 天**（trailing） | 14 / 30 |
| Q3 | 3σ 還是 2σ 還是兩段（warn 2σ / page 3σ） | **3σ 單級**（先簡單） | 兩段 |
| Q4 | dedup 視窗 | **60 min** | 30 / 120 |
| Q5 | 第一輪要不要 p99（除了 p95）| **不要**（只 p95） | 加 p99 |
| Q6 | per-agent 還是 per-(agent, model) | **per-agent**（第一輪簡單） | per-(agent, model) |
| Q7 | 是否寫 ADR | **不寫**（task prompt + plan 已足） | 寫 ADR-014 |
| Q8 | 是否上 ultrareview | **是**（多 metric + statistics + alert path = 高 leverage） | 跳過 |

修修凍結後直接動手；不夠決定就在 chat 問。

## §10. 完工後 follow-up

- 更新 pickup memo（5B-3 → ✅，下一個 Phase 6 / Phase 7）
- 寫 short feedback memo `feedback_anomaly_3sigma_pattern.md` 記第一輪結果（誤報多寡 / 真實抓到 issue 機率），給未來統計類 detector 借鑒
- 看 1-2 週運作後是否要加 `/bridge/anomaly` 歷史頁面（5B-3+1 issue）
