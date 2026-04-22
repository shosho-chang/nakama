# Nakama Observability 原則

當事情出問題時，能在 5 分鐘內知道「發生了什麼、在哪裡、影響多大」。這份原則定義 agent、gateway、cron 該記錄什麼、怎麼記、向誰告警。**新 ADR 直接援引**，不用每份重新辯論 log format 或要不要加 metric。

---

## 1. 三層觀察：Log / Metric / Trace

### Log — 發生了什麼

**用途**：debug、事故調查、稽核

**格式**：structured JSON log，每筆至少含：

```python
{
    "ts": "2026-04-22T14:00:00+08:00",
    "level": "INFO",
    "logger": "nakama.brook.compose",
    "msg": "draft created",
    "agent": "brook",
    "operation": "compose_book_review",
    "operation_id": "op_a3f2e9",
    "extra": {
        "draft_id": "draft_20260422T140000_a3f2e9",
        "category": "book-review",
        "word_count": 8421,
        "llm_model": "claude-sonnet-4-6",
        "llm_tokens_in": 12453,
        "llm_tokens_out": 8672,
        "duration_ms": 24500
    }
}
```

**禁止**：
- `print("ok")` 這種無 structure log
- 把 secrets、API key、full content 寫進 log（用 `***` 或摘要替代）
- Multiline exception 沒帶 stack trace

**Log level 準則**：
- `DEBUG`：流程細節，預設關閉
- `INFO`：每個操作的開始 / 結束，含時間與結果摘要
- `WARNING`：降級情境（fallback、retry 成功、DLQ 入隊）
- `ERROR`：失敗但系統繼續跑（個別操作失敗）
- `CRITICAL`：系統級故障（DB 掛、整個 agent 停）

### Metric — 發生多少

**用途**：趨勢觀察、SLO 追蹤、告警

**Phase 1 用簡易方式**：寫進 `state.db` 的 `metrics_timeseries` 表，Bridge 直接查 + 繪圖。

**必備 metric**（先做這批，其他照需求加）：

| Metric | Type | Labels | 來源 |
|---|---|---|---|
| `operations_total` | counter | agent, op, status | 每次 agent 操作 |
| `operation_duration_ms` | histogram | agent, op | 操作耗時 |
| `llm_tokens_total` | counter | agent, model, direction | LLM call 累計 |
| `llm_cost_usd` | counter | agent, model | 每次 LLM call 換算後累加 |
| `queue_depth` | gauge | queue_name, status | 每分鐘 snapshot |
| `external_api_errors_total` | counter | api, status_code | 外部 API 失敗 |
| `health_check_status` | gauge | target | WP / Nakama service ping 結果 |

**Phase 2**：考慮上 Prometheus + Grafana（但 VPS 資源有限時先用 SQLite + Bridge 就夠）。

### Trace — 在哪裡

**用途**：複雜跨服務操作的 end-to-end 追蹤

**Phase 1 簡易版**：每個跨 agent 操作帶 `operation_id`，所有 log 都有這個 ID，用 `grep operation_id=op_a3f2e9` 就能串起來。

**Phase 2**：如果操作複雜度增加，考慮 OpenTelemetry。Phase 1 不用。

## 2. Operation ID 是硬規則

每個對外可見的操作（Brook 寫一篇文、Usopp 發一篇、Franky 一次 cron、Bridge 處理一個 request）都必須生成一個 `operation_id`，並傳遞給所有下游呼叫。

```python
import uuid

def operation_id() -> str:
    return f"op_{uuid.uuid4().hex[:8]}"

def brook_compose(...) -> Draft:
    op_id = operation_id()
    logger = get_logger().bind(operation_id=op_id, agent="brook", op="compose")
    logger.info("compose started", extra={"category": ...})
    # 下游呼叫都傳 op_id
    research = robin_research(topic, operation_id=op_id)
    draft = llm.ask(prompt, operation_id=op_id)
    ...
```

**為什麼**：事故調查時能一次把「這筆文章從 prompt 到發布」的所有 log 串起來。

## 3. 外部 Uptime Probe（non-negotiable for Franky）

**規則**：不能讓一個 agent 監控自己所在的系統。Franky 在 VPS 內部，VPS 掛時 Franky 一起掛，不會發告警。

**解法**：用**外部服務** ping VPS 的 health endpoint：

| 選項 | 免費額度 | 推薦度 |
|---|---|---|
| UptimeRobot | 50 monitor / 5-min interval | ⭐⭐⭐⭐⭐ Phase 1 用這個 |
| Better Uptime | 10 monitor | ⭐⭐⭐⭐ 介面好看 |
| Cloudflare Health Checks | Pro plan 才有 | ⭐⭐ 需付費 |
| 自建 on 第二個 VPS | 貴 | ⭐ 不值 |

**監控目標**：
- `https://nakama.shosho.tw/healthz`（Nakama gateway）
- `https://shosho.tw/` （WP blog）
- `https://fleet.shosho.tw/` （WP community）

**告警通道**：email + SMS（**不能**走 Slack / Franky，因為那兩個都依賴 VPS）。

## 4. Health Endpoint 契約

每個對外 service 必須暴露 `/healthz`：

```python
# Response 200 當且僅當該 service 能處理新請求
{
    "status": "ok",
    "service": "nakama-gateway",
    "version": "0.4.2",
    "checks": {
        "db": "ok",
        "llm_api": "ok",
        "wp_connection": "ok"
    },
    "uptime_seconds": 3600
}

# Response 503 當有 dependency 壞
{
    "status": "degraded",
    "checks": {
        "db": "ok",
        "llm_api": "timeout",
        "wp_connection": "ok"
    }
}
```

`/healthz` **不能**需要 auth（外部 probe 要能訪問）。**不能** 做重操作（要在 50ms 內回覆）。

## 5. SLO（Service Level Objective）

每個 agent 要在其 ADR 裡列 SLO。**不是 SLA**（沒有對外承諾），是 **self-imposed quality bar**：

| Agent | SLO |
|---|---|
| Brook 寫單篇書評 | p95 < 90 秒、成功率 > 95% |
| Usopp 發布單篇 | p95 < 10 秒、成功率 > 98% |
| Bridge UI 載入 | p95 < 500ms |
| Franky cron（5-min health check） | 每次跑 < 30 秒、週失敗率 < 1% |
| Approval queue 入隊到審核通知 Slack | p95 < 60 秒 |
| 全站事故 MTTR | < 15 分鐘（修修上線介入） |

達不到 SLO → 修、不是喊口號。三週連續違反 → 重新設計。

## 6. Alert 三層原則（與 [ADR-007](../decisions/ADR-007-franky-scope-expansion.md) 一致）

- 🚨 **Critical**：立即 DM 修修（Franky 或外部 probe）。**必須**有 dedup 機制（同一事件 15 分鐘內只 DM 一次，除非 escalate）。
- 🟡 **Warning**：Bridge dashboard + weekly digest。
- 🟢 **Info**：Bridge dashboard only。

**Alert Fatigue 防線**：
- 任何 alert rule 上線前，先查過去 30 天 log，模擬會觸發幾次。> 10 次/週的 rule 要調 threshold
- 每月一次 alert review：哪些 rule 沒觸發過（刪）？哪些觸發太多（調）？

## 7. Dashboard 原則

Bridge `/bridge/health` 或類似頁面，**預設顯示今天 + 過去 7 天**：

- 系統：VPS CPU/RAM/disk/load、各 service uptime、各 cron 上次執行時間 + 成功率
- 操作：每個 agent 當日操作數、失敗率、平均耗時
- 隊列：approval queue 當前深度、DLQ 深度
- 成本：當日 / 當月 LLM 花費、token 用量 by model

**禁止**：dashboard 顯示 > 7 天資料（會自動 aggregate 失去細節）；實際歷史要走 `/bridge/metrics` 詳細查詢。

## 8. Log 存放與輪替

| 類型 | 位置 | 輪替 |
|---|---|---|
| Agent log | `/var/log/nakama/{agent}.log` | daily rotate，保留 30 天 |
| Audit log（approval 動作、秘密存取） | `/var/log/nakama/audit.log` | 永不刪，每月壓縮 |
| DB operation log | state.db 內部表 | 保留 90 天，auto-archive 到 R2 |
| Access log（Bridge HTTP） | `/var/log/nakama/access.log` | daily rotate，保留 14 天 |

磁碟用量監控在 Franky 的 5-min cron 裡。

## 9. Secrets 不得出現在 Observability 通道

**不能**出現在 log / metric label / dashboard / alert message / error trace 的項目：

- API key、token、password
- 用戶 email / 電話（除了 identifier 欄位例外）
- Slack token、WP password
- GCP service account JSON
- Draft 全文內容（寫到 log 會被 indexing）

**替代**：log 記 `key_id`（對照表另存安全位置），dashboard 顯示 `****9A2F` 尾 4 碼。

## 10. LLM 成本觀察

每次 LLM call 強制記錄：

```python
{
    "ts": ...,
    "operation_id": ...,
    "agent": "brook",
    "model": "claude-sonnet-4-6",
    "tokens_in": 12453,
    "tokens_out": 8672,
    "tokens_thinking": 2100,
    "cost_usd": 0.0342,
    "duration_ms": 24500,
    "result_status": "success"
}
```

這是 `data/usage_log.jsonl` 的記錄（已有 infra，見 `project_agent_cost_tracking.md`）。

Bridge `/bridge/cost` 顯示每日/週/月 aggregation，Franky weekly digest 提及「本週 LLM 花費 $X，vs 上週 +Y%」。

## 11. 事故事後檢討（Post-mortem）

任何 Critical alert 觸發後 24 小時內寫事故紀錄：

```markdown
# Incident 2026-04-22 Brook compose 雙發

**觸發**：2026-04-22 14:05 Critical alert
**影響**：同一篇文章發了兩次到 shosho.tw
**原因**：`peek_approved` race condition（ADR-006 已列，未處理）
**修復**：手動刪重複文章（14:08）
**根因解法**：改 atomic claim（PR #89）
**學到**：Phase 1 必 done 的 blocker 不能延期
```

Post-mortem 存 `docs/incidents/YYYY-MM-DD-{slug}.md`。**Blameless**：寫原因不寫責任。

## 12. 不做的事

- **不做 hyperscale observability**。Datadog / New Relic 一個月幾百美元不適合個人專案。先用 SQLite + Bridge dashboard
- **不在 log 裡做 analytics**。log 給事故調查，analytics 走 metric 表
- **不做即時 streaming 告警**。5 分鐘 cron 粒度對修修的反應速度已經夠快（人類沒辦法更快）
- **不記錄所有 HTTP request body**。太吃空間且有隱私風險，只在 ERROR 時記

---

## 13. 與其他原則的關係

- **Schema 原則**（[schemas.md](schemas.md)）：log / metric 的 payload 也要有 schema（尤其是 structured log 的 extra fields）
- **Reliability 原則**（[reliability.md](reliability.md)）：observability 驗證 reliability 生效（retry 有成功嗎？DLQ 有東西嗎？）
- **Observability 原則**（本檔）：定義事情出問題時怎麼被看見
