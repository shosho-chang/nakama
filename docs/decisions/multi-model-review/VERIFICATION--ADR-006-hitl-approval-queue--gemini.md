---
source_adr: ADR-006-hitl-approval-queue
verification_round: 2
reviewer_model: gemini-2.5-pro
elapsed_seconds: 22
review_date: 2026-04-22
---

# ADR-006-hitl-approval-queue — 修訂驗證（gemini）

好的，這是一份資深軟體架構師對 ADR-006 修訂版的驗證報告。

---

### 1. Blocker 逐項檢核

針對 `multi-model-review/ADR-006--CONSOLIDATED.md` §5「最 blocking 的問題」，逐項驗證修訂版 ADR 的回應。

1.  **原 blocker**：Payload schema 未定義，Brook/Usopp/Bridge 之間無穩定契約。
    *   **修訂版回應**：✅ **完整解**
    *   **證據**：ADR §2 `Pydantic Payload Schema` 引入 `ApprovalPayloadV1` discriminated union，並為 `publish_wp_post` / `update_post` 定義了嚴格的 Pydantic model (`extra="forbid"`)。DB schema §1 新增 `payload_version` 與 `action_type` 欄位，完整援引 `docs/principles/schemas.md` 原則。

2.  **原 blocker**：`peek_approved` 有 race condition，多 worker 會重複執行同一任務。
    *   **修訂版回應**：✅ **完整解**
    *   **證據**：ADR §3 `Atomic Claim` 提出 `claim_approved_drafts` 函式，其 SQL 語法使用 `UPDATE ... WHERE id IN (SELECT ...) RETURNING *`，並包裹在 `BEGIN IMMEDIATE` transaction 內。此為解決此問題的標準、正確模式。DoD checklist 也包含並發壓力測試。

3.  **原 blocker**：Obsidian 整合的傳輸機制未定義，是架構幻覺。
    *   **修訂版回應**：🔄 **拆到別處**
    *   **證據**：ADR 在「本版相對於前版的變更」一節中明確指出：「本版把 Obsidian 整合切到獨立 ADR-006b（Phase 2 research），Phase 1 只做 Bridge 核心 queue 骨架」。這完全採納了 review 的建議，將未解的問題隔離，讓 Phase 1 範圍收斂且可執行。

4.  **原 blocker**：SQLite 備份保護不足，預設非 WAL 會鎖死。
    *   **修訂版回應**：⚠️ **部分解**
    *   **證據**：ADR §5 `SQLite 設定` 明確要求啟用 `PRAGMA journal_mode = WAL`、`synchronous = NORMAL` 與 `busy_timeout = 5000`，解決了並發鎖定問題。但 litestream 連續 replication 被移到 Phase 2，Phase 1 採用 daily snapshot，這意味著 RPO 仍是 24 小時而非 review 建議的 < 5 分鐘。雖然 ADR 在 §SPOF 表格中誠實地列出了此風險，但它並未完全解決 blocker #4 的「RPO」部分。

5.  **原 blocker**：`status` 欄位沒有 state machine 保護，有重複發布風險。
    *   **修訂版回應**：✅ **完整解**
    *   **證據**：ADR §4 `Status FSM` 提供了雙層保護：(1) DB 層的 `CHECK (status IN (...))` constraint；(2) Application 層的 `transition()` 函式與 `ALLOWED_TRANSITIONS` 白名單，並在 UPDATE 時使用 `WHERE id = ? AND status = ?` 防止 TOCTOU。這是非常穩健的實作。

---

### 2. 新發現的問題

1.  **問題描述**：`status` 欄位定義與 FSM 白名單不一致。
    *   **嚴重度**：**High**
    *   **細節**：DB schema (`CREATE TABLE` in §1) 中 `CHECK`約束的 `status` 列表是 `('pending','in_review','approved','rejected', 'claimed','published','failed','archived')`。然而，FSM 的 `ALLOWED_TRANSITIONS` dict (§4) 卻缺少對 `in_review` 狀態的定義，且 transition table 文字描述與 dict key 也不完全匹配（例如文字版有 `pending -> in_review`，但 dict 裡沒有）。這會導致 code 實作時直接拋出 `KeyError` 或產生非預期的行為。
    *   **建議修法**：在動工前，必須校對 ADR §1 的 `CHECK` 列表、§4 的文字版 transition table、以及 §4 的 Python `ALLOWED_TRANSITIONS` dict，確保三者完全一致。

2.  **問題描述**：Litestream 被推到 Phase 2，使 Phase 1 的 SPOF 風險高於 review 建議。
    *   **嚴重度**：**Medium**
    *   **細節**：原 review §2.3 和 §5 都強調 litestream 是解決 RPO 問題的關鍵。新 ADR 雖然承認此風險，但將其延後。考量到 VPS 可能因任何原因（硬體故障、Vultr 平台問題）而毀損，daily snapshot 意味著最壞情況下會丟失近 24 小時內所有已 approve 但未執行的重要 draft。對於一個生產系統，這個 RPO 偏高。
    *   **建議修法**：強烈建議將 litestream 的部署拉回 Phase 1 DoD。其設定相對簡單，對資源消耗不大，但對可靠性的提升是根本性的。若堅持延後，需由產品負責人明確簽字接受「最慘可能丟失 24 小時內所有待辦項目」的風險。

---

### 3. 修訂品質評估

| 維度 | 1-10 分 | 一句話評語 |
|---|---|---|
| Schema / 契約完整度 | 10 | 非常完整。從 SQL DDL、Pydantic model 到 JSON blob versioning，無可挑剔。 |
| Reliability 機制（idempotency / atomic / SPOF）| 8 | atomic claim 與 FSM 的設計極為穩健，但將 litestream 延後處理使 SPOF 風險緩解打了折扣。 |
| Observability（log / metric / SLO / probe）| 9 | 涵蓋了結構化 log、metrics endpoint、SLO 定義，並與原則文件掛鉤，設計非常專業。 |
| 可實作性（工程師照寫能不能動） | 9 | 除了 FSM 定義不一致的小瑕疵，ADR 提供了 SQL、Python 程式碼片段與清晰的 DoD，可直接轉為開發任務。 |
| 範圍聚焦度（沒再 scope creep） | 10 | 範圍收斂做得極好，果斷將 Obsidian 整合切分出去，並明確列出「不做的事」，是修訂的典範。 |

---

### 4. 最終判定

*   **go / no-go**：**Go**，但需先修正一個小 blocker。

*   **動工前必須修正**：
    1.  **FSM 定義一致性**：修正 ADR §4，確保 `CHECK` 約束、文字描述、`ALLOWED_TRANSITIONS` dict 三者對狀態的定義完全一致。這項修改預計不超過 30 分鐘。

*   **Phase 1 實作過程中要特別盯的風險**：
    1.  **資料遺失風險 (SPOF)**：由於 litestream 被推到 Phase 2，團隊必須嚴格確保 daily R2 snapshot cron job 的監控與告警是有效的。任何 snapshot 失敗都應視為 P1 等級的事件處理。
    2.  **Payload 大小**：ADR 已指出長文 payload 可能使 DB 膨脹。Franky 需從第一天起就監控 `state.db` 的檔案大小與 I/O 表現，以便在問題發生前及早規劃 Phase 2 的應對方案（如 payload 外置）。