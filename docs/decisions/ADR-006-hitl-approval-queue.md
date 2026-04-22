# ADR-006: Human-in-the-Loop Approval Queue（Bridge `/bridge/drafts`）

**Date:** 2026-04-22
**Status:** Proposed
**Supersedes:** 2026-04-22 的前一版 ADR-006（scope 過寬版）
**Last revised:** 2026-04-22（吸收三家 multi-model verification 的必修項，見文末 Changelog）

---

## Context

所有對外發布的 agent（Usopp、Chopper、Sanji、未來 Brook 自產 draft）都要 gate 在**修修批准**後才能執行。本 ADR 定義其核心 approval queue 骨架。

**本版相對於前版的變更**：
- 前版把 Obsidian vault 雙向同步納入 Phase 1，multi-model review（`multi-model-review/ADR-006--CONSOLIDATED.md` §2.1）三家一致指出傳輸機制未解，為「架構幻覺」
- 本版把 Obsidian 整合切到獨立 [ADR-006b](ADR-006b-obsidian-vault-sync.md)（Phase 2 research），**Phase 1 只做 Bridge 核心 queue 骨架**
- 其餘 review 列的 5 個 blocker（Pydantic schema / atomic claim / WAL+litestream / FSM / observability）全數在本版吸收

**消費者**：
- Phase 1：Brook（壓稿 → 入 queue）、Usopp（認領 → 執行發布）
- Phase 2+：Chopper（社群回覆）、Sanji（社群公告）
- 記憶系統已部署 `/bridge` landing + `/bridge/memory` + `/bridge/cost`（見 `project_agent_memory_design.md`），本 ADR 是第四個 Bridge surface

---

## Decision

### 1. 資料模型（SQLite `state.db` 的 `approval_queue` 表）

所有欄位補齊版本、狀態、workflow 標籤、可觀測欄位。schema 原則見 [docs/principles/schemas.md](../principles/schemas.md)。

```sql
CREATE TABLE approval_queue (
    -- 主鍵與時間
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')),

    -- Workflow 標籤
    source_agent      TEXT NOT NULL,        -- 'brook' | 'usopp' | 'chopper' | 'sanji'
    target_platform   TEXT NOT NULL,        -- 'wp_shosho' | 'wp_fleet' | 'fluentcrm' | 'fluentcommunity' | 'ig'
    target_site       TEXT,                  -- 'shosho.tw' | 'fleet.shosho.tw' | NULL
    action_type       TEXT NOT NULL,        -- 'publish_post' | 'update_post' | 'send_newsletter' | 'reply_comment'
    priority          INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100),

    -- Payload（versioned）
    payload_version   INTEGER NOT NULL,     -- 對應 shared/schemas/approval.py 的 Literal version
    payload           TEXT NOT NULL,        -- JSON validated by Pydantic ApprovalPayloadV{N}
    title_snippet     TEXT NOT NULL,        -- 從 payload 拉出，供列表 UI 穩定擷取
    diff_target_id    TEXT,                 -- 若是 update 現有 post，原始 post ID（snapshot 語義見 §8）

    -- Status FSM（CHECK 約束）
    status            TEXT NOT NULL
                      CHECK (status IN ('pending','in_review','approved','rejected',
                                        'claimed','published','failed','archived')),

    -- Audit
    reviewer          TEXT,                  -- 通常 'shosho'
    review_note       TEXT,
    reviewed_at       TEXT,

    -- Claim & execution
    worker_id         TEXT,                  -- 認領的 worker 識別
    claimed_at        TEXT,
    published_at      TEXT,
    execution_result  TEXT,                  -- JSON: {"post_id":..., "url":...}
    retry_count       INTEGER NOT NULL DEFAULT 0,
    error_log         TEXT,                  -- 最近一次失敗訊息（含 operation_id）

    -- Observability
    operation_id      TEXT NOT NULL,         -- 入隊時生成，跨 log 串接（見 observability.md §2）
    cost_usd_compose  REAL                   -- 生產此 draft 的 LLM 成本（from api_calls）
);

CREATE INDEX idx_queue_status_priority ON approval_queue(status, priority DESC, created_at ASC);
CREATE INDEX idx_queue_agent_status    ON approval_queue(source_agent, status);
CREATE INDEX idx_queue_operation       ON approval_queue(operation_id);
```

**為什麼這些欄位**（回應 review blocker）：
- `payload_version` + `action_type`：review §2.4 要求，無版本的 JSON blob 無遷移路徑
- `title_snippet`：從 payload 拉出獨立欄位，UI 列表不用解析 JSON
- `worker_id` / `claimed_at`：atomic claim 的認領證據
- `retry_count` / `error_log`：失敗可追查、DLQ 轉入條件
- `operation_id`：[observability.md §2](../principles/observability.md) 硬規則
- `cost_usd_compose`：Bridge UI 顯示「這篇花了多少 token」供 HITL 判斷

### 2. Pydantic Payload Schema（回應 blocker #1）

依 [schemas.md §1-3](../principles/schemas.md) 原則，在 `shared/schemas/approval.py` 定義：

```python
# shared/schemas/approval.py
from typing import Literal, Annotated, Union
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, AwareDatetime, constr

class PublishWpPostV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)
    schema_version: Literal[1] = 1
    action_type: Literal["publish_post"]
    target_site: Literal["wp_shosho", "wp_fleet"]
    title: constr(min_length=5, max_length=120)
    slug: constr(pattern=r"^[a-z0-9\-]+$")
    category: str
    tags: list[str]
    post_content_html: str            # Gutenberg HTML
    seo_focus_keyword: str
    scheduled_at: AwareDatetime | None = None
    draft_id: constr(pattern=r"^draft_\d{8}T\d{6}_[a-f0-9]{6}$")

class UpdateWpPostV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)
    schema_version: Literal[1] = 1
    action_type: Literal["update_post"]
    target_site: Literal["wp_shosho", "wp_fleet"]
    wp_post_id: int
    patch: dict           # only changed fields
    change_summary: str   # 人類可讀的修改說明，供 HITL review
    draft_id: str

# Pydantic v2 discriminated union：以 action_type 為 discriminator field
# 新增 action_type 時在此 Union 擴充，Pydantic 會自動依 action_type 分派
ApprovalPayloadV1 = Annotated[
    Union[PublishWpPostV1, UpdateWpPostV1],
    Field(discriminator="action_type"),
]

# Reader 端用 TypeAdapter 驗證；避免 model_validate() 在 Union 上的 ambiguous 匹配
ApprovalPayloadV1Adapter = TypeAdapter(ApprovalPayloadV1)
```

**Writer 端**（agent 入隊前）：
```python
payload_model = PublishWpPostV1(...)
queue.enqueue(
    source_agent="brook",
    payload_version=1,
    payload_model=payload_model,
)
# enqueue 內部會 payload_model.model_dump_json() 與 title_snippet 拉取
```

**Reader 端**（worker 認領後）：
```python
raw = json.loads(row["payload"])
match row["payload_version"]:
    case 1:
        # 走 TypeAdapter 才能正確觸發 discriminator 分派
        payload = ApprovalPayloadV1Adapter.validate_python(raw)
    case _:
        raise UnknownPayloadVersionError(row["payload_version"])
```

schema 升級走 [schemas.md §3](../principles/schemas.md) 流程。

### 3. Atomic Claim（回應 blocker #2）

依 [reliability.md §2](../principles/reliability.md) 硬規則，**絕不**用 read-then-write。實作於 `shared/approval_queue.py`：

```python
def claim_approved_drafts(
    worker_id: str,
    source_agent: str,
    batch: int = 5,
    timeout_s: int = 5,
) -> list[ClaimedDraft]:
    """
    原子認領 approved → claimed，回傳已認領列表。
    同時被多 worker 呼叫不會重複（SQLite BEGIN IMMEDIATE + RETURNING）。
    """
    with db.transaction(isolation="IMMEDIATE", busy_timeout_ms=timeout_s * 1000):
        rows = db.execute("""
            UPDATE approval_queue
            SET status     = 'claimed',
                worker_id  = ?,
                claimed_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'),
                updated_at = strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now')
            WHERE id IN (
                SELECT id FROM approval_queue
                WHERE status = 'approved'
                  AND source_agent = ?
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
            )
            RETURNING id, payload_version, payload, operation_id, action_type
        """, (worker_id, source_agent, batch)).fetchall()
    return [ClaimedDraft(**r) for r in rows]
```

**驗收測試**（DoD checklist 一項）：起 10 個 thread 同時 claim 100 筆 approved draft，驗證：
- 每筆 draft 只有 1 個 worker_id 拿到
- 總認領數 = 100（不漏）
- 無 `database is locked` exception（WAL + busy_timeout 生效）

### 4. Status FSM（回應 blocker #4）

**Single Source of Truth**：Python 的 `ALLOWED_TRANSITIONS` dict 是 FSM 的唯一權威來源。DB 的 `CHECK (status IN (...))` 約束與下方文字版轉移表**由 dict 反向生成／人工同步後驗過**，三處必須一致。migration 新增／移除狀態時，改 dict 後同步更新另兩處、重跑 FSM 測試。

**狀態集合**（DB CHECK 與 dict key 必須相等）：
```
pending, in_review, approved, rejected, claimed, published, failed, archived
```

**合法轉移表**（與下方 dict 逐行對應）：

```
pending    ──(enter review)──────→ in_review
pending    ──(auto-approve*)─────→ approved    # 未啟用，預留
in_review  ──(shosho approve)────→ approved
in_review  ──(shosho reject)─────→ rejected
in_review  ──(shosho edit)───────→ in_review   # payload 變更但狀態保持
approved   ──(worker claim)──────→ claimed
claimed    ──(exec success)──────→ published
claimed    ──(exec fail)─────────→ failed
claimed    ──(stale timeout)─────→ approved    # §4.1 reset cron
failed     ──(retry)─────────────→ claimed     # retry_count < 3
failed     ──(give up)───────────→ archived    # retry_count >= 3, 轉 DLQ
published  ──(cleanup)───────────→ archived
rejected   ──(cleanup)───────────→ archived
```

**實作（SoT）**：

```python
# shared/approval_queue.py — FSM 的 SoT，DB CHECK 與文字版皆以此為準
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending":   {"in_review", "approved"},
    "in_review": {"in_review", "approved", "rejected"},
    "approved":  {"claimed"},
    "claimed":   {"published", "failed", "approved"},  # approved 為 stale timeout reset，見 §4.1
    "failed":    {"claimed", "archived"},
    "published": {"archived"},
    "rejected":  {"archived"},
    "archived":  set(),
}

# DB CHECK 欄位值集合（= dict key 集 ∪ 所有 target）；測試時斷言與 §1 schema 裡的 CHECK 一致
ALL_STATUSES: set[str] = set(ALLOWED_TRANSITIONS.keys()) | {
    s for targets in ALLOWED_TRANSITIONS.values() for s in targets
}
assert ALL_STATUSES == {
    "pending", "in_review", "approved", "rejected",
    "claimed", "published", "failed", "archived",
}

def transition(draft_id: int, from_status: str, to_status: str, *, actor: str, **extras) -> None:
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, set()):
        raise IllegalStatusTransitionError(
            f"draft={draft_id} {from_status}→{to_status} not allowed"
        )
    # 帶條件 UPDATE，防止 TOCTOU
    changed = db.execute("""
        UPDATE approval_queue
        SET status = ?, updated_at = ?, ...
        WHERE id = ? AND status = ?
    """, (to_status, now_utc(), draft_id, from_status)).rowcount
    if changed == 0:
        raise ConcurrentTransitionError(f"draft={draft_id} not in status={from_status}")
    log_transition(draft_id, from_status, to_status, actor, extras)
```

**驗收測試**：
- 對每種非法轉移（例如 `published → pending`）必須 raise `IllegalStatusTransitionError`
- 斷言 `ALL_STATUSES` 與 `001_approval_queue.sql` 裡 CHECK 列表完全相等（防止三處漂移）

### 4.1 Claimed 狀態 timeout reset（回應 Usopp crash SPOF）

Worker 認領 draft 後若 process 當掉（OOM、VPS reboot、未捕捉例外），該筆 row 會永遠卡在 `claimed`，後續 `claim_approved_drafts()` 不會再看到它。Phase 1 用一個週期性 reset cron 處理，不依賴 Phase 2 多 worker。

**實作**：

```python
# shared/approval_queue.py
STALE_CLAIM_THRESHOLD_S = 10 * 60  # 10 分鐘無進展視為 worker 掛掉

def reset_stale_claims() -> list[int]:
    """
    把 claimed 超過 STALE_CLAIM_THRESHOLD_S 的 row 透過 transition() 打回 approved，
    讓下一輪 claim_approved_drafts() 可以重新認領。回傳被 reset 的 draft id 列表。
    """
    cutoff = iso_utc(now_utc() - timedelta(seconds=STALE_CLAIM_THRESHOLD_S))
    rows = db.execute("""
        SELECT id FROM approval_queue
        WHERE status = 'claimed' AND claimed_at < ?
    """, (cutoff,)).fetchall()
    reset_ids = []
    for r in rows:
        try:
            transition(r["id"], "claimed", "approved",
                       actor="stale_claim_reset",
                       note=f"claimed > {STALE_CLAIM_THRESHOLD_S}s, worker presumed dead")
            reset_ids.append(r["id"])
        except ConcurrentTransitionError:
            # worker 在我們掃描的空窗裡剛好寫入 published/failed，正常跳過
            continue
    return reset_ids
```

**排程**：cron 每 5 分鐘執行一次（`*/5 * * * * python -m shared.approval_queue reset-stale-claims`），worker crash 後最壞 15 分鐘內會重回 queue。

**不做**：process heartbeat、worker fencing token（Phase 2 多 worker 時再考慮）。

### 5. SQLite 設定（回應 blocker #3）

依 [reliability.md §10](../principles/reliability.md) crash safety 規範：

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA busy_timeout = 5000;
PRAGMA foreign_keys = ON;
```

**設定時機**：application 開 DB connection 時（`shared/db.py` connection pool）每次 set。

**Phase 2 litestream**：連續 replication 到 Cloudflare R2，RPO < 5 分鐘：

```
litestream replicate \
  /home/nakama/data/state.db \
  s3://nakama-state-backup/state.db \
  --config /etc/litestream.yml
```

Phase 1 先開 WAL + daily snapshot 到 R2，litestream 在 Phase 2 補上（risk 列於下方）。

**Phase 1 資料保護依賴 Franky**：daily R2 snapshot cron、snapshot 成功/失敗告警、R2 bucket lifecycle 由 Franky（見 ADR-007）負責。本 ADR 只負責 DB 層 PRAGMA，不重複定義備份 pipeline。

### 6. Migration 框架

引入 `yoyo-migrations`（輕量、無 ORM 依賴）。schema 變更一律走 migration，禁止手動 `ALTER TABLE`。

```
state.db/migrations/
├── 001_approval_queue.sql          # 本 ADR 初始 schema
├── 002_approval_queue_indices.sql  # 後續加索引
└── ...
```

### 7. Bridge Route 結構

```
GET  /bridge/drafts                       # 列表（filter: agent, status, priority）
GET  /bridge/drafts/{id}                  # 詳細頁（含 diff preview）
PUT  /bridge/drafts/{id}/content          # 修改 payload（走 Pydantic validate）
POST /bridge/drafts/{id}/approve          # 只改 status（pending/in_review → approved）
POST /bridge/drafts/{id}/reject           # 改 status + review_note
POST /bridge/drafts/{id}/claim            # 供 CLI fallback 手動標記（稀少用）
GET  /bridge/drafts/metrics               # queue depth、pending p95、24h failure count
```

**為什麼拆 `PUT content` 與 `POST approve`**（review §3 Gemini 建議）：
- `PUT content` 只改內容，status 保持 `in_review`
- `POST approve` 只改 status，不接受新 content（避免「編輯+核准」混合時 audit 含糊）
- 修修在 UI 一次點擊按鈕時，前端照順序發兩個 request

### 8. HITL UX 守則（美學 first-class，見 CLAUDE.md）

Bridge `/bridge/drafts` 介面每個 draft 卡片顯示：

- 來源 agent + 頭像 / icon
- 產出時戳（相對時間）+ operation_id 尾 6 碼
- **Style profile version**：Brook compose 時用的 style profile（供 HITL 判斷是否應用對的 voice）
- **Diff against existing post**：若 `diff_target_id` 非空，show side-by-side diff
  - Snapshot 語義：**enqueue 當下**從 WP 拉一份存入 payload（避免 WP 同時被其他管道改導致 diff 漂移）
- **Compose 成本**：`cost_usd_compose` 顯示 `$0.03` 供判斷「這篇若 reject 損失多少」
- **Actions**：`Approve` / `Reject` / `Edit`，每個動作點擊後立即 disable 防雙擊

Diff view、鍵盤快捷鍵、review_note 結構化分類放 Phase 2。

### 9. Slack 通知鉤子（Phase 1 polling）

- Phase 1：Nami Morning Brief 從 `/bridge/drafts/metrics` 抓 pending count，納入早報
- Phase 1：新 draft 入隊時，`source_agent` 負責發一則 Slack DM（Brook 的 compose 流程尾端呼叫 `nami.notify_pending_draft(draft_id)`）
- Phase 2：Bridge 主動發 webhook 到 Slack（狀態：待研究）

### 10. CLI Fallback

Bridge UI 掛時修修仍能 approve：

```
python -m shared.approval_queue list --status pending
python -m shared.approval_queue approve <id> --reviewer shosho --note "ok"
python -m shared.approval_queue reject  <id> --reviewer shosho --note "reason"
```

走同一個 `transition()` 函式，FSM 保護仍生效。

---

## Schema / Idempotency / SPOF / Schema Version 四件（Phase 1 必備）

### Schema version
`ApprovalPayloadV1` + DB 欄位 `payload_version INT NOT NULL`。未來 V2 走 [schemas.md §3](../principles/schemas.md) migration 流程。

### Idempotency
遵循 [reliability.md §1](../principles/reliability.md)：
- `operation_id` 存入 `approval_queue.operation_id`
- Usopp 執行 publish 時以 `draft_id` 作 WP meta 的 idempotency key（WP 已有 post → return existing post_id，不重發）
- FSM 的 `claimed → published` 轉移 application 加 WHERE clause 防重複 UPDATE

### SPOF
依 [reliability.md §4](../principles/reliability.md) 列出：

| SPOF | 影響 | Phase 1 緩解 | Phase 2 強化 |
|---|---|---|---|
| `state.db` 單檔 | 全部 pending/approved/claimed 可能丟失 | WAL + Franky daily R2 snapshot（cron + 告警監控，見 ADR-007） | litestream 連續 replication |
| Bridge web process | 修修無法用 UI 審批 | CLI fallback（§10） | 第二個 process（非 Phase 1） |
| Usopp worker | approved draft 不會被認領 | §4.1 `reset_stale_claims` cron（每 5 分鐘），claimed 超過 10 分鐘自動 reset 回 `approved` | 多 worker + fencing token |
| Slack 通知 | 修修不知道有新 draft | Nami 早報（每天一次保底） | webhook + retry |

### SLO
依 [observability.md §5](../principles/observability.md)：

- **入隊到 Slack 通知** p95 < 60 秒
- **approved → published**（含 worker polling）p95 < 5 分鐘
- **Bridge UI `/bridge/drafts` 載入** p95 < 500 ms
- **Claim race：重複認領率 = 0**（DoD 測試驗證）
- 達不到 → 修，不喊口號

---

## Consequences

### 正面
- Phase 1 scope 收斂到可落地的核心 queue，三家 review 列的 5 個 blocker 全吸收
- Pydantic schema 讓 Brook / Usopp 合約穩定，UI 列表不用靠 ad-hoc parse
- Atomic claim + FSM 雙層保護，防雙發（review 的首要正確性風險）
- Observability 從 day 1 內建（operation_id、metrics endpoint、structured log）

### 風險
- SQLite 仍是單點（Phase 1 接受，Phase 2 litestream 緩解）
- Payload 長文存 SQLite，50 KB × 1000 篇 = 50 MB，Phase 1 能扛但需 Franky 監控 DB 大小
- 二進位 payload（圖片 base64）不支援，Phase 2 Chopper 梗圖出現時要外掛 R2 + reference（review §3 Gemini 提）
- Payload 敏感欄位（使用者回覆含個資）目前明文存 SQLite，VPS 入侵則暴露（review §3 Grok 提，Phase 2 加欄位級加密）

### 不做的事（Phase 1）
- Obsidian 雙向同步 → [ADR-006b](ADR-006b-obsidian-vault-sync.md)
- 鍵盤快捷鍵、diff side-by-side 精緻化、review_note 結構化分類
- 多人 approve、role-based 授權
- 草稿協作編輯
- Payload 欄位加密
- 圖片/二進位 payload 外接 R2
- 自動過期（`expires_at`）與失效通知（Claude 提，Phase 2）

---

## 開工 Checklist（Definition of Done）

### Schema / 資料層
- [ ] `shared/schemas/approval.py`：`ApprovalPayloadV1` discriminated union 定義完成，含 `PublishWpPostV1` / `UpdateWpPostV1`
- [ ] `state.db/migrations/001_approval_queue.sql`：本 ADR §1 的 schema，CHECK constraint + indices 齊全
- [ ] `yoyo-migrations` 引入，`yoyo apply` 能跑起 001
- [ ] DB connection 開啟時 `PRAGMA journal_mode=WAL; synchronous=NORMAL; busy_timeout=5000`

### 邏輯層
- [ ] `shared/approval_queue.py`：`enqueue()` / `claim_approved_drafts()` / `transition()` / `mark_published()` / `mark_failed()` / `reset_stale_claims()`
- [ ] Atomic claim 實作與 `BEGIN IMMEDIATE` 正確
- [ ] `ALLOWED_TRANSITIONS` 完整，所有公開 API 一律經 `transition()`
- [ ] `ApprovalPayloadV1` 用 `Field(discriminator="action_type")`，Reader 端走 `ApprovalPayloadV1Adapter.validate_python()`；驗證測試塞一個 `UpdateWpPostV1` payload，斷言 `type(payload) is UpdateWpPostV1`（防 discriminator 漏設導致 ambiguous match）
- [ ] FSM 一致性測試：斷言 `ALL_STATUSES` 與 `001_approval_queue.sql` CHECK 列表逐一相等
- [ ] `reset_stale_claims()` 單元測試：造一筆 `claimed_at` 超 10 分鐘的 row，跑 reset 後狀態變 `approved`、`worker_id` 清空

### Bridge
- [ ] `thousand_sunny/routers/drafts.py`：§7 七個 route 齊全
- [ ] `/bridge/drafts` 列表頁設計過（見 CLAUDE.md 美學要求 + `docs/design-system.md`），非 AI slop default
- [ ] `/bridge/drafts/{id}` 詳細頁顯示 §8 所有欄位（來源 / style / diff / 成本）
- [ ] CLI fallback `python -m shared.approval_queue ...` 三個子命令可用

### Observability
- [ ] 每個 status transition 寫 structured log：`operation_id / draft_id / agent / from / to / actor / ts`（照 [observability.md §1](../principles/observability.md)）
- [ ] `/bridge/drafts/metrics` endpoint 吐：queue depth by status、24h p95 pending 時間、24h failure count
- [ ] Nami Morning Brief 抓 `/bridge/drafts/metrics` 加入早報

### 測試
- [ ] in-memory SQLite fixture，`conftest.py` 每測隔離
- [ ] **Atomic claim stress test**：10 worker × 100 draft，驗證每筆只被 claim 一次、總認領 = 100、無 lock error
- [ ] **FSM 非法轉移測試**：對每個 illegal transition 確認 raise `IllegalStatusTransitionError`
- [ ] **Payload schema drift 測試**：未知 `payload_version` raise、`extra` 欄位被 `forbid` 擋下
- [ ] **Idempotency 測試**：同一 `draft_id` 被 Usopp 執行兩次，WP 端只有一篇 post（mock WP client）

### 部署
- [ ] VPS `.env` 變數新增（若有）照 `feedback_vps_env_drift_check.md` 確認
- [ ] `thousand-sunny` service 重啟流程寫入 runbook
- [ ] Phase 1 daily R2 snapshot cron 由 Franky（ADR-007）負責，含成功/失敗告警
- [ ] `reset_stale_claims` cron 進 VPS crontab（`*/5 * * * *`），失敗寫 structured log
- [ ] litestream 連續 replication 延到 Phase 2

---

## Notes

- 本 ADR 與 [ADR-005 publishing infrastructure] 共生：Usopp 的 publish worker 消費本 queue
- Obsidian 整合 → [ADR-006b](ADR-006b-obsidian-vault-sync.md)
- Phase 1 資料保護（daily R2 backup + 告警）→ [ADR-007 Franky](ADR-007-franky-ops.md)
- Multi-model review 原始報告 → [multi-model-review/ADR-006--CONSOLIDATED.md](multi-model-review/ADR-006--CONSOLIDATED.md)
- Round-2 verification（三家）：[multi-model-review/VERIFICATION--ADR-006-hitl-approval-queue--claude-sonnet.md](multi-model-review/VERIFICATION--ADR-006-hitl-approval-queue--claude-sonnet.md) / [gemini](multi-model-review/VERIFICATION--ADR-006-hitl-approval-queue--gemini.md) / [grok](multi-model-review/VERIFICATION--ADR-006-hitl-approval-queue--grok.md)
- 三份原則：[schemas.md](../principles/schemas.md) / [reliability.md](../principles/reliability.md) / [observability.md](../principles/observability.md)
- Bridge UI 美學：CLAUDE.md §美學要求 + `docs/design-system.md`，拒絕 AI slop default

---

## Changelog

### 2026-04-22 — Round-2 verification 修正
依三家 multi-model verification（claude-sonnet / gemini / grok）必修項調整，不改動總體架構：

- **§2 Pydantic discriminated union（Claude Sonnet High）**：補 `Field(discriminator="action_type")` 明確宣告、加 `use_enum_values=True`、Reader 端改走 `ApprovalPayloadV1Adapter = TypeAdapter(...)` 的 `validate_python()`，避免 Union 上的 ambiguous 匹配。DoD 加 discriminator 驗證測試。
- **§4 FSM 三處一致性（Gemini High）**：宣告 `ALLOWED_TRANSITIONS` 為 FSM SoT，DB CHECK 與文字轉移表皆由此對齊；加 `ALL_STATUSES` 集合斷言確保三處不漂移；DoD 加「CHECK 列表與 dict 狀態集相等」測試。
- **§4.1 新增 Claimed timeout reset（Claude Sonnet High + Grok High）**：補 `claimed → approved` 轉移路徑、`reset_stale_claims()` 函式、10 分鐘 stale threshold、每 5 分鐘 cron；SPOF 表「Usopp worker 掛掉」列的「future cron」改為 Phase 1 實作。
- **§5 litestream Phase 1 缺席的 RPO 漏洞（Grok Medium）**：加一句 reference 聲明 Phase 1 daily R2 snapshot + 告警由 Franky（ADR-007）負責，不在本 ADR 重複定義。SPOF 表的 `state.db` 列同步補 Franky reference。
- **DoD / Notes**：補 discriminator 測試、FSM 一致性測試、`reset_stale_claims` 單元測試、cron 部署項，並加入三份 verification 報告連結。
