---
source_adr: ADR-006-hitl-approval-queue
verification_round: 2
reviewer_model: claude-sonnet-4-6
elapsed_seconds: 99
review_date: 2026-04-22
---

# ADR-006-hitl-approval-queue — 修訂驗證（claude-sonnet）

# ADR-006 修訂版驗證報告

---

## 1. Blocker 逐項檢核

### Blocker #1：`payload` JSON blob 無 schema、無版本、難查詢

**原 blocker**：`payload TEXT NOT NULL` 無 Pydantic schema、無 version、每個 agent 塞什麼都可以，UI 無法穩定擷取 title，schema 升級無路徑。

**修訂版回應**：✅ 完整解

**證據**：
- §2 定義 `shared/schemas/approval.py`，`PublishWpPostV1` / `UpdateWpPostV1` 均有 `model_config = ConfigDict(extra="forbid", frozen=True)` 與 `schema_version: Literal[1]`，符合 schemas.md 嚴格規則
- §1 schema 新增 `payload_version INTEGER NOT NULL`、`action_type TEXT NOT NULL`、`title_snippet TEXT NOT NULL`
- §2 Reader 端的 `match payload_version` 分支 + `UnknownPayloadVersionError` 處理未知版本
- DoD checklist 有「Payload schema drift 測試：未知 `payload_version` raise、`extra` 欄位被 `forbid` 擋下」
- 唯一細節：`ApprovalPayloadV1` 的 discriminated union 缺少顯式 `discriminator="action_type"` 宣告（Pydantic v2 要求），但這是實作細節，ADR 骨架設計正確，工程師補一行即可，不構成 blocker

---

### Blocker #2：`peek_approved` 有 race condition，需改為原子 claim

**原 blocker**：多 worker 同時呼叫 `peek_approved` 會拿到同一批 draft，重複執行，SQLite 沒有 `SELECT FOR UPDATE`。

**修訂版回應**：✅ 完整解

**證據**：
- §3 `claim_approved_drafts()` 使用 `BEGIN IMMEDIATE` + `UPDATE ... WHERE id IN (SELECT ...) RETURNING *`，一次原子完成，無 read-then-write
- `worker_id`、`claimed_at` 欄位記錄認領者
- DoD checklist 有「10 worker × 100 draft stress test，每筆只被 claim 一次、總認領 = 100、無 lock error」

**一個細節值得確認**：`db.transaction(isolation="IMMEDIATE")` 這個 wrapper API 需要確保它真的發出 `BEGIN IMMEDIATE` 而非 `BEGIN DEFERRED`（SQLite 預設是 DEFERRED）。ADR 描述了語義但沒有展示 `shared/db.py` 的實作。這不是 ADR 層級的問題，但實作時要特別注意。

---

### Blocker #3：Obsidian 整合傳輸機制未解

**原 blocker**：`obsidian://` 路徑假設 Linux VPS 能直讀 Windows 本機路徑，傳輸機制完全未定義，是「架構幻覺」。

**修訂版回應**：🔄 拆到別處，去向明確

**證據**：
- Context 段落明確說明「本版把 Obsidian 整合切到獨立 ADR-006b（Phase 2 research）」
- Notes 末尾再次標注「Obsidian 整合 → ADR-006b」
- §7 Route 結構完全沒有 `obsidian-reimport` endpoint
- 「不做的事」清單第一條就是「Obsidian 雙向同步 → ADR-006b」

ADR-006b 尚未看到內容，但本 ADR 範圍內這個 blocker 已正確隔離。

---

### Blocker #4：SQLite WAL + litestream + 定義 RPO

**原 blocker**：多 process 讀寫 `state.db` 預設非 WAL 會 `database is locked`；R2 備份不是 RPO 解，兩次備份之間的資料若崩潰全丟。

**修訂版回應**：⚠️ 部分解

**證據（已解部分）**：
- §5 明確列出四條 PRAGMA：`WAL`、`synchronous=NORMAL`、`busy_timeout=5000`、`foreign_keys=ON`，且說明「每次開 connection 時 set」
- DoD checklist 有「DB connection 開啟時 PRAGMA 齊全」

**還差什麼**：
- litestream 明確推到 Phase 2，Phase 1 只做 daily R2 snapshot
- ADR **沒有定義 Phase 1 的 RPO 數字**。review §5 要求「定義 RPO（例如 <5 分鐘）」——Phase 1 用 daily snapshot，RPO 實際上是「最多 24 小時資料丟失」，這與 SLO 章節列的其他精確數字形成落差，應明確寫出「Phase 1 RPO = 24h（daily snapshot），Phase 2 目標 < 5 min（litestream）」而非沉默
- 這是 **Medium** 風險：工程師和業主對 Phase 1 的 data loss tolerance 應該要有共識，ADR 現在用沉默迴避了這個數字

---

### Blocker #5：`status` 欄位沒有 state machine 保護

**原 blocker**：status 轉換完全靠 application logic，無 CHECK constraint、無 FSM guard，bug 可把 `executed` 改回 `pending` 重發布。

**修訂版回應**：✅ 完整解，且超出預期

**證據**：
- §1 schema：`CHECK (status IN ('pending','in_review','approved','rejected','claimed','published','failed','archived'))`
- §4 `ALLOWED_TRANSITIONS` dict 完整列出所有合法轉移
- `transition()` 函式有兩層保護：application 層 whitelist check + DB 層 `WHERE status = from_status` 的 conditional UPDATE（防 TOCTOU）
- `ConcurrentTransitionError` 處理 rowcount=0 的並發衝突
- 新增 `in_review` 中間狀態，解決了原 ADR 的 `pending → approved` 直跳問題
- 新增 `archived` 終態，解決 Gemini 提的資料清理問題
- `failed_permanent` 被改名為 `archived`（透過 `failed → archived` 路徑），語義更清晰，解決 Claude 指出的 rejected 語義污染問題
- DoD 有「FSM 非法轉移測試」

**一個設計決定值得留意**：`in_review ──(shosho edit)──→ in_review` 是自環轉移，`ALLOWED_TRANSITIONS["in_review"]` 包含 `"in_review"` 自身。`transition()` 呼叫自環時會觸發 UPDATE（updated_at 更新），但 FSM log 會出現 `from=in_review to=in_review` 的 transition record，對可觀測性是雜訊。建議在 `transition()` 加一行：若 `from_status == to_status` 且非 payload edit，則只 log payload change 不 log state transition。這是 Low 嚴重度設計細節。

---

## 2. 新發現的問題

### P1（High）：`ApprovalPayloadV1` discriminated union 宣告不完整

**問題描述**：
```python
ApprovalPayloadV1 = Annotated[
    Union[PublishWpPostV1, UpdateWpPostV1],
    # Pydantic v2 discriminator
]
```
`# Pydantic v2 discriminator` 是一行註解，不是實際宣告。Pydantic v2 要求：
```python
Field(discriminator="action_type")
```
沒有這行，`model_validate()` 在 Reader 端**不會**根據 `action_type` 自動選擇子類型，而是 fallback 到逐一嘗試（try first match），可能把 `UpdateWpPostV1` 的 payload 錯誤驗證成 `PublishWpPostV1` 失敗後再試下一個，或在欄位重疊時靜默接受錯誤類型。

**嚴重度**：High（功能性錯誤，在 Phase 1 只有兩個 subclass 時可能被壓住，Phase 2 加 `send_newsletter` 時會爆）

**建議修法**：
```python
from pydantic import Field
ApprovalPayloadV1 = Annotated[
    Union[PublishWpPostV1, UpdateWpPostV1],
    Field(discriminator="action_type")
]
```
並在 DoD checklist 加「validate 一個 `UpdateWpPostV1` payload 確認回傳型別是 `UpdateWpPostV1` 而非 `PublishWpPostV1`」。

---

### P2（High）：`claimed → approved` 的 timeout reset 機制只有 SPOF 表格一行文字，完全未實作

**問題描述**：SPOF 表格「Usopp worker 掛掉」的 Phase 1 緩解寫：「`claimed_at + 15 min` timeout 自動 reset 回 `approved`（future cron）」，但：
- DoD checklist 完全沒有這個 cron
- `ALLOWED_TRANSITIONS` 沒有 `claimed → approved` 路徑
- 如果 Usopp 在 `claimed` 狀態當掉，這筆 draft 永遠卡在 `claimed`，不會被任何 worker 再認領

這不是「Phase 2」問題——Usopp crash 是 Phase 1 可能發生的事，且影響是 **approved draft 永遠不被發布**，修修不會知道（除了隔天 Nami 早報）。

**嚴重度**：High（正確性風險，且現有 FSM 設計堵死了恢復路徑）

**建議修法**：
1. 把 `claimed → approved` 加進 `ALLOWED_TRANSITIONS`（限制條件：`claimed_at` 超過 15 分鐘且 `worker_id` 對應的 process 不存活）
2. 或改成：`claimed → failed`（timeout 觸發），然後走 `failed → claimed`（retry）的現有路徑
3. Phase 1 DoD 加一個 cron/heartbeat task：每 5 分鐘掃描 `claimed_at < now - 15min` 的 row 並 reset
4. 或最簡單：在 Usopp worker 的 main loop 加 startup claim recovery（啟動時把所有 `worker_id=self.id AND status=claimed` reset 回 `approved`）

---

### P3（Medium）：`/bridge/drafts/metrics` 路徑與 `/bridge/drafts/{id}` 路徑衝突

**問題描述**：FastAPI/Starlette 的路由匹配是從上到下，`GET /bridge/drafts/{id}` 的 `{id}` 是字串路徑參數，會匹配 `/bridge/drafts/metrics`，把 `"metrics"` 當成 `id` 去查資料庫，結果 404 或 422。

**嚴重度**：Medium（實作時會立刻發現，但 ADR 應該明確說）

**建議修法**：兩個選項：
- 把 metrics endpoint 改為 `/bridge/metrics`（和 §7 其他 Bridge 路徑一致，也符合通用 metrics 概念）
- 或在 router 宣告中把 `/bridge/drafts/metrics` 放在 `/bridge/drafts/{id}` **之前**，並在 `{id}` 加型別限制 `{id:int}`

---

### P4（Medium）：Phase 1 RPO 未明確定義（承接 Blocker #4 的剩餘問題）

**問題描述**：如上 Blocker #4 分析，SLO 章節列了 `approved → published p95 < 5 分鐘` 等精確數字，但 Phase 1 的 RPO 沒有寫出來。daily snapshot 意味著最壞情況丟失近 24 小時的 approved/pending drafts，這個 trade-off 應該明確讓業主（修修）知情並接受。

**嚴重度**：Medium（不是工程問題，是風險溝通問題）

**建議修法**：在 SLO 章節或 Consequences/風險段落加一行：「Phase 1 RPO = 24h（daily snapshot），意味著 VPS 崩潰最壞情況下當天的 approved/pending queue 需要人工重建；Phase 2 litestream 將 RPO 降至 <5 分鐘。」

---

### P5（Medium）：`UpdateWpPostV1.patch: dict` 過於寬鬆

**問題描述**：`patch: dict` 沒有任何型別約束，等於繞過了 `extra="forbid"` 的保護——agent 可以在 `patch` 裡放任意內容（包括 `{"_password": "..."}`），Pydantic 不會擋。這和整份 ADR 的 schema 嚴格性原則矛盾。

**嚴重度**：Medium（現在不會出問題，但 Phase 2 加 Chopper/Sanji 時可能成為注入路徑）

**建議修法**：定義一個 `WpPostPatch` model，明確列出可修改的欄位（`title`、`content`、`tags` 等），或至少改為 `patch: dict[str, str | int | list[str]]`。

---

### P6（Low）：`enqueue()` 的 `title_snippet` 拉取邏輯未定義

**問題描述**：§2 說「`enqueue` 內部會 `payload_model.model_dump_json()` 與 title_snippet 拉取」，但沒說怎麼拉。`PublishWpPostV1` 有 `title` 欄位，`UpdateWpPostV1` 只有 `change_summary`，未來的 `send_newsletter` 可能是 `subject`。如果 `enqueue()` 用 `getattr(payload_model, 'title', None) or getattr(payload_model, 'subject', None) or ...` 這種 ad-hoc 方式，又回到了 blocker #1 的問題。

**嚴重度**：Low（架構意圖清楚，但實作時容易退化）

**建議修法**：在每個 payload subclass 加一個 `@property def display_title(self) -> str`，`enqueue()` 統一呼叫 `payload_model.display_title`。或在 base 層加 `Protocol`。

---

### P7（Low）：`diff_target_id` snapshot 語義已在 §8 釐清，但 schema 沒有對應的 snapshot 欄位

**問題描述**：§8 說「enqueue 當下從 WP 拉一份存入 payload」，但 `PublishWpPostV1` / `UpdateWpPostV1` 都沒有 `original_post_snapshot` 欄位，snapshot 要存在哪裡？是存進 `payload` JSON？還是 DB 另開欄位？這個決定沒有落地。

**嚴重度**：Low（Phase 1 的 `UpdateWpPostV1` 會用到，不是 Phase 2 問題）

**建議修法**：在 `UpdateWpPostV1` 加 `original_content_snapshot: str | None = None`（optional，因為舊文 WP API 可能失敗），並在 DoD 加「`diff_target_id` 非空時，enqueue 時必須嘗試拉 WP post snapshot，失敗則 log warning 但不阻塞入隊」。

---

## 3. 修訂品質評估

| 維度 | 分數 | 一句話評語 |
|---|---|---|
| Schema / 契約完整度 | 8/10 | `DraftPayloadV1` discriminated union 設計正確但宣告有語法空洞，`patch: dict` 過寬，其餘欄位設計扎實 |
| Reliability 機制（idempotency / atomic / SPOF）| 7/10 | Atomic claim 和 FSM 雙層保護設計優秀，但 `claimed` 狀態的 timeout recovery 只有一行 SPOF 表格文字、FSM 路徑未補齊，是真實的正確性漏洞 |
| Observability（log / metric / SLO / probe）| 8/10 | structured log + metrics endpoint + Nami 早報整合設計完整，SLO 數字具體；唯一缺口是 Phase 1 RPO 數字沉默 |
| 可實作性（工程師照寫能不能動） | 8/10 | SQL、Python、CLI 範例齊全、路徑清楚；metrics 路由衝突和 discriminator 缺漏是兩個會讓工程師卡 30 分鐘的細節 |
| 範圍聚焦度（沒再 scope creep） | 9/10 | Obsidian 切乾淨、Phase 2 清單明確、DoD 可執行；沒有再長出新功能 |

---

## 4. 最終判定

### ✅ **Go，帶條件**

工程師可以開始寫 code，但以下兩個問題**在開始實作 `shared/schemas/approval.py` 之前**必須修掉——它們不需要重寫 ADR，改幾行即可，但不改的話後續測試或 Phase 2 會在這兩點上爆：

**條件一（必修，High）：補齊 `ApprovalPayloadV1` discriminator 宣告**
把 `# Pydantic v2 discriminator` 那行換成 `Field(discriminator="action_type")`，並在 DoD 加一個驗證測試。不修的話，Phase 1 兩個 subclass 時測試可能壓住問題，Phase 2 加第三個 subclass 時會靜默解析錯誤。

**條件二（必修，High）：定義 `claimed` 狀態的 timeout recovery 機制**
在 SPOF 表格說「future cron」但 DoD 沒有這個 task，FSM 也沒有 `claimed → approved` 路徑。Usopp crash 是 Phase 1 真實風險。最小修法：在 DoD 加「Usopp worker 啟動時把 `worker_id=self.id AND status=claimed` reset 回 `approved`」，並在 `ALLOWED_TRANSITIONS` 補這條路徑。

---

**Phase 1 實作過程中要特別盯的風險**：

1. **`BEGIN IMMEDIATE` 是否真的發出**：`shared/db.py` 的 `transaction(isolation="IMMEDIATE")` wrapper 實作必須驗證，SQLite 預設 `BEGIN DEFERRED` 不夠，壓力測試前先用 `sqlite3` CLI 確認 WAL 模式生效 + IMMEDIATE 語義正確。

2. **metrics 路由順序**：`/bridge/drafts/metrics` 必須在 FastAPI router 中早於 `/bridge/drafts/{id:int}` 宣告（或改路徑為 `/bridge/metrics`），否則第一次 Nami 早報抓 metrics 時會回 422，安靜失效。