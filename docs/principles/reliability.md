# Nakama Reliability 原則

這份文件定義 Nakama 所有 agent、gateway、cron 任務必須遵守的可靠性原則。**新 ADR 直接援引**，不用每份重新辯論 idempotency 或 SPOF 要不要處理。

---

## 1. 冪等性（Idempotency）是硬規則

**定義**：同一 operation 重放多次，產生的系統狀態等同於執行一次。

**適用範圍**：所有寫操作（發布文章、更新排名、寄 Slack、存記憶），無一例外。

**實作模式**：

```python
def publish_post(draft: DraftV1) -> PublishResult:
    # 1. 用 draft_id 作為 idempotency key
    existing = wp_client.find_by_meta("nakama_draft_id", draft.draft_id)
    if existing:
        return PublishResult(status="already_published", post_id=existing.id)
    # 2. 執行操作
    post = wp_client.create_post(...)
    # 3. 把 idempotency key 寫進 post meta
    wp_client.update_meta(post.id, "nakama_draft_id", draft.draft_id)
    return PublishResult(status="published", post_id=post.id)
```

**為什麼**：cron 重跑、retry、事故恢復都會 re-execute。沒冪等 = 雙發文章 / 重複寄信 / 同一告警 DM 五次。

## 2. Atomic Claim Pattern

跨 worker 從 queue 抓工作必須是 atomic。**絕對禁止** `SELECT ... WHERE status='approved' LIMIT 1` 然後 `UPDATE` 這種 read-then-write。

**正確作法**（SQLite）：

```python
def claim_approved_drafts(worker_id: str, batch: int = 5) -> list[int]:
    with db.transaction():
        rows = db.execute("""
            UPDATE approval_queue
            SET status = 'claimed',
                worker_id = ?,
                claimed_at = CURRENT_TIMESTAMP
            WHERE id IN (
                SELECT id FROM approval_queue
                WHERE status = 'approved'
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
            )
            RETURNING id
        """, (worker_id, batch)).fetchall()
    return [r["id"] for r in rows]
```

SQLite 的 `RETURNING` + 單 transaction 保證原子性；PostgreSQL 同理（加 `SELECT ... FOR UPDATE SKIP LOCKED`）。

**為什麼**：你以為只有一個 Usopp worker？過半年 Usopp 會有 batch publisher 跑定時發布，另一個跑即時發布 — 同時抓同一筆 = 雙發。

## 3. State 存在單一處（SoT）

每個實體只有一份 source of truth：

| 實體 | SoT | 非 SoT（僅快取 / 投影） |
|---|---|---|
| 文章內容 | `wp_posts.post_content`（WordPress） | Brook 的 draft 檔案、Obsidian 備份 |
| Approval 佇列狀態 | `state.db` 的 `approval_queue` 表 | Bridge UI memory、Slack 訊息文字 |
| Agent 記憶 | `memory.db` | Bridge dashboard 顯示 |
| 排程文章 | WP 的 `post_status='future'` | Brook 內部 schedule table（如果有） |
| Franky 監控記錄 | `state.db` 的 `monitoring_*` 表 | Bridge 圖表 |

**禁止** 同一份事實在多處可改且沒 sync 機制。

## 4. SPOF 識別與緩解

每個 agent / gateway / cron 都要在 ADR 裡列「如果這個掛了會發生什麼」。

**Nakama 目前的 SPOF**：

| SPOF | 影響 | 緩解 |
|---|---|---|
| VPS (nakama.shosho.tw) | 所有 agent 停擺 | 外部 uptime probe（UptimeRobot 免費方案）— VPS 掛時從 Cloudflare 外部 ping，失敗時從 Franky 以外的通道（email）通知修修 |
| state.db | 所有跨 agent 狀態失效 | 每日 R2 備份 + WAL mode + litestream 實時同步到 R2（Phase 2） |
| Anthropic API key | Brook/Usopp/Chopper 全癱 | Phase 1：Sentry alert 檢測 401/429；Phase 2：Gemini/Grok fallback |
| WP (shosho.tw) 掛 | Brook / Usopp 無法發文 | Phase 1：Franky 發 Critical DM；Phase 2：local draft queue 累積 + 自動 recover |
| Slack | Nami / Franky DM 失敗 | 降級：email fallback（需 SMTP 或 Resend） |

新 ADR **必須** 列出其系統的 SPOF 表。

## 5. Retry 與 Backoff

**何時 retry**：transient error（network timeout、5xx、429 rate limit、connection reset）。

**何時不 retry**：4xx 業務錯誤（401 auth、403 permission、422 validation、409 conflict）。

**實作**：

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    reraise=True,
)
def call_external_api(...):
    ...
```

**Budget**：單次操作總 retry 時間不超過 60 秒（避免 cron 被卡死）。長時間操作要走背景 worker + async。

**指數退避 jitter**：多個 worker 同時 retry 會 herd，加 `wait_exponential_jitter` 或手動 `+random(0, 2)`。

## 6. Circuit Breaker（Phase 2）

對於**下游服務整體不可用**的情境，retry 沒用（只會放大負載）。Circuit breaker 模式：

```
closed（正常）→ 5 次連續失敗 → open（跳過呼叫，快速 fail）
open → 30 秒後 → half-open（放一個 probe 過）
half-open → 成功 → closed; 失敗 → open
```

Phase 1 不實作，但設計 external API client 時留介面（e.g. `use pybreaker`）。

## 7. Timeout 是必填

每個外部呼叫都要設 timeout。**禁止**裸 `requests.get(url)` 不帶 timeout（會永遠卡住）。

**預設值**：
- 本機 HTTP（WP on same VPS）：3 秒
- 同洲外部 API（Anthropic US-East）：15 秒
- 跨洲外部 API（GSC / GA4 europe）：30 秒
- LLM call：60 秒（含 thinking）
- 長文 LLM 批次（> 10k 輸出）：180 秒

## 8. Dead Letter Queue

任何失敗 3 次仍未成功的工作必須進 DLQ，**不能靜默丟棄**。

```sql
CREATE TABLE dlq (
    id INTEGER PRIMARY KEY,
    original_table TEXT NOT NULL,
    original_id TEXT NOT NULL,
    failure_reason TEXT NOT NULL,
    payload TEXT NOT NULL,
    failed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    retry_count INTEGER NOT NULL
);
```

Bridge `/bridge/dlq` 顯示所有卡住的項目，修修可手動重試或放棄。**DLQ 每週一自動提醒**（Franky weekly digest）。

## 9. 可觀察的失敗

失敗要**有明確訊號**：

- 寫 structured log（含 operation id、input summary、失敗 step、stack trace）
- 增加 `operations_failed_total{op=...}` metric（Phase 2 Prometheus）
- 進 DLQ
- 若是 Critical：觸發 alert 通道

**禁止**：
- `except Exception: pass` 吞掉例外
- 只 log 不 raise 也不累計（debug 時找不到）
- 錯誤訊息沒上下文（`"failed"` 是爛訊息，`"failed to publish draft 42 to wp_shosho: 401 Unauthorized"` 是好訊息）

## 10. Crash Safety

cron 任務與背景 worker 被 SIGKILL 時不能腐化狀態。

**Checklist**：
- [ ] 用 transaction 包住多步寫操作
- [ ] Partial writes 有 rollback 或 idempotency key
- [ ] SQLite 開 WAL mode（`PRAGMA journal_mode=WAL`）
- [ ] 長時間任務 checkpoint 到 DB（處理到第幾個 item），重啟從 checkpoint 續跑
- [ ] 避免 in-memory state 跨 call（process 重啟 = state 丟失）

## 11. Schema 遷移的可靠性

schema 改版時 migration 要符合：

1. **向前相容**：新 code 能讀舊 schema（至少一個版本）
2. **向後相容**：舊 code 讀新 schema 不會炸（未知欄位預設忽略，除非 `extra="forbid"` — 那就版本化處理）
3. **Dry run 機制**：migration 有 `--dry-run` flag 先跑一次看影響
4. **可回滾**：每個 migration 有對應的 down migration（至少文件化）

## 12. 不做的事

- **不為 100% uptime 設計**。個人專案沒有 SLA，99% 夠用，把資源花在 MTTR（mean time to recovery）而非 MTBF（mean time between failure）
- **不做複雜的分散式共識**。SQLite + 單 VPS 是 baseline，需要時再上 PostgreSQL
- **不用 full microservice 架構**。Nakama 是 monolith by design，agent 是邏輯分割而非部署分割
- **不在沒監控的情況下開啟 auto-retry**。壞掉的 retry 會吃光 rate limit，先建 observability 再放 retry

---

## 13. 與其他原則的關係

- **Schema 原則**（[schemas.md](schemas.md)）：定義「傳什麼」
- **Reliability 原則**（本檔）：定義「怎麼保證送到且只送到一次」
- **Observability 原則**（[observability.md](observability.md)）：定義「出問題時怎麼知道」

三者互補，新 ADR 要同時檢查三份。
