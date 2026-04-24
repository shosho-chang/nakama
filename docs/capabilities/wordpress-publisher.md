# Capability Card — `nakama-wordpress-publisher`

**Status:** Phase 1 Slice C1（daemon in place，staging E2E 未跑）
**License:** MIT（計畫開源）
**Scope:** 把已審核的 `DraftV1` 安全發布到 WordPress，含 SEO + cache purge + 法規攔截 + crash recovery。

---

## 能力

| 功能 | 方法 | 來源 |
|---|---|---|
| 讀 approval_queue → publish WP | `Publisher(wp).publish(request, ...)` | ADR-005b §1 |
| Crash-safe state machine（8 forward states + failed） | `publish_jobs` 表持久化 | ADR-005b §1 |
| 雙層 idempotency（Nakama + WP meta） | `publish_jobs.draft_id UNIQUE` + `nakama_draft_id` post meta | ADR-005b §2 |
| Advisory lock（race-free create_post） | `shared/locks.advisory_lock` | ADR-005b §2.1 |
| 三層 SEOPress fallback | `shared/seopress_writer.write_seopress` | ADR-005b §3 |
| Atomic publish（先 draft → 驗證 → 切 publish） | State `post_draft → seo_ready → validated → published` | ADR-005b §4 |
| LiteSpeed cache purge（含 noop fallback） | `shared/litespeed_purge.purge_url` | ADR-005b §5 |
| Category fail-fast（未 map 就拒） | `CategoryNotMappedError` | ADR-005b §6 |
| Compliance gate（台灣醫療/藥事法詞彙） | `shared.compliance.scan` | ADR-005b §10 |
| Alert on SEO skip（Critical Franky alert） | `agents.franky.alert_router.dispatch` | ADR-005b §3 + ADR-007 |

## Input / Output 契約

```python
from agents.usopp.publisher import Publisher
from shared.wordpress_client import WordPressClient
from shared.schemas.publishing import PublishRequestV1

wp = WordPressClient.from_env("wp_shosho")
pub = Publisher(wp)  # category_map/tag_map auto-populated on first call

result = pub.publish(
    PublishRequestV1(
        draft=draft_from_approval_queue,
        action="publish",                    # or "schedule" / "draft_only"
        reviewer="U_SHOSHO",
        featured_media_id=None,              # optional; Bridge HITL fills
    ),
    approval_queue_id=42,
    operation_id="op_12345678",
)
# result.status ∈ {"published", "scheduled", "draft_only", "already_published", "failed"}
# result.seo_status ∈ {"written", "fallback_meta", "skipped"}
# result.cache_purged: bool
# result.failure_reason: str | None
```

## Daemon（`python -m agents.usopp`）

Slice C1 加入 poll-based worker：每 `USOPP_POLL_INTERVAL_S` 秒（預設 30）呼叫 `claim_approved_drafts(source_agent="brook", batch=N)`，把每筆 `PublishWpPostV1` payload 轉成 `PublishRequestV1` 餵給 `Publisher.publish()`；`UpdateWpPostV1` 超出 Phase 1 scope，直接 `mark_failed`。

**Env 控制：**

| Env | Default | 用途 |
|---|---|---|
| `USOPP_TARGET_SITE` | `wp_shosho` | `WordPressClient.from_env` 讀的 prefix（`wp_shosho` / `wp_fleet`） |
| `USOPP_WORKER_ID` | `usopp-<hostname>` | claim 時寫入 `approval_queue.worker_id` |
| `USOPP_POLL_INTERVAL_S` | `30` | 每 cycle 的 sleep 秒數（interruptible） |
| `USOPP_BATCH_SIZE` | `5` | 單次 claim 批量 |

**Action 推論：** `payload.scheduled_at is not None → action="schedule"`，否則 `"publish"`。`"draft_only"` 在 HITL Bridge UI 有出口時再加（Phase 2）。

**Reviewer 來源：** `approval_queue.reviewer` 欄位（`approve()` 透過 `transition()` 寫入）；NULL 時 fallback `"unknown"` 並 log WARNING。

**Signal 行為：** SIGTERM / SIGINT 只設 `_shutdown` flag；current dispatch 跑完 + current sleep 中斷（1s 粒度）後清空退出。

## 不做的事

- ❌ 不 claim approval_queue 以外的 queue 分支（只看 `source_agent='brook'`）
- ❌ 不發 FluentCRM newsletter / FluentCommunity 貼文（Phase 2–3）
- ❌ 不自動建 WP category / tag（ADR-005b §6 硬規則；DraftV1 slug 不在 map 直接 fail）
- ❌ 不 grant `unfiltered_html` 給 bot user（ADR-005a AST 白名單已安全）
- ❌ 不 backfill 既有 192 篇文章的 `nakama_draft_id`
- ❌ 不處理 `UpdateWpPostV1` action_type（Phase 1 只 cover new-post，update 路徑走 HITL 人工改 WP admin）
- ❌ 不在 `/healthz` 加 WP 連線檢查 — ADR-007 `probe_wp_site` out-of-band cron 已 cover（ADR-005b line 417 superseded by ADR-007 §4）

## 依賴

- Python 3.10+
- `shared.wordpress_client.WordPressClient`（Slice A）
- `shared.locks.advisory_lock`（Slice A）
- `shared.approval_queue` + `shared.state`（PR #72 foundation）
- `agents.franky.alert_router`（PR #75，Slice B 用它發 Critical alert）
- `shared.schemas.publishing` V1（PR #72）
- `shared.schemas.external.wordpress` / `seopress`（Slice A）

## 成本

- 單篇 publish p95 < 10 秒（SLO，ADR-005b §SLO）
  - create_post + get_post + update_post + purge = 4 次 WP REST call（1 req/sec 限流下 ≈ 4 秒 wall clock）
  - SEOPress REST + get_post 驗證 = 2 次 = +2 秒
- 不呼叫 LLM
- 不呼叫 LiteSpeed 實際為 0 成本（VPS hosted）

## 失敗模式

| 失敗 | 行為 |
|---|---|
| WP 5xx/timeout | tenacity retry 3 次 (2→4→8s)，失敗 DLQ + Critical alert |
| WP 4xx auth | 不 retry，publish_jobs → failed + approval_queue → failed |
| SEOPress REST drift | → Fallback A meta keys |
| Fallback A 仍失敗 | → skip + Critical alert（publish 照發，SEO 留空待人工補） |
| Validation mismatch | publish_jobs → failed |
| Category slug 未 map | `CategoryNotMappedError` → failed |
| 合規詞彙命中 | 不 publish，寫 `compliance_flags` JSON 到 DB，approval_queue → failed，Bridge HITL 顯示警告 |
| Process crash 中途 | publish_jobs state 已持久，重啟後 `publish()` 再叫一次從當下 state 續跑；orphan WP post 透過 `find_by_meta` 認領 |

## 契約測試

- Unit: 44 tests in `tests/shared/test_compliance.py` + `test_seopress_writer.py` + `test_litespeed_purge.py` + `tests/agents/usopp/test_publisher.py`
- Unit (daemon): 16 tests in `tests/agents/usopp/test_daemon.py`（poll cycle / signal graceful / reviewer lookup / action 推論 / update_post skip / env factory）
- Live (Slice C2): `@pytest.mark.live_wp` + Docker WP 6.x + SEOPress 9.4.1 staging（未跑）

## Roadmap

- [x] v0.1 Slice A — WordPressClient + locks + external schemas
- [x] v0.1 Slice B — publisher.py + compliance + seopress_writer + litespeed_purge
- [x] v0.2 Slice C1 — `agents/usopp/__main__.py` daemon + signal handling + unit tests（本 PR）
- [ ] v0.2 Slice C2 — Docker WP 6.x staging E2E + Day 1 LiteSpeed 實測 + runbook 定稿
- [ ] v0.3 Phase 2 — multi-worker（fencing token）、WP-CLI purge fallback、update_post flow、cron_runs 追蹤成功率
