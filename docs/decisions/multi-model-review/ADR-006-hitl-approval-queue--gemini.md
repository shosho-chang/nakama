---
source_adr: ADR-006-hitl-approval-queue
reviewer_model: gemini-2.5-pro
elapsed_seconds: 42
review_date: 2026-04-22
---

# ADR-006-hitl-approval-queue — gemini 審查

好的，這份 ADR-006 文件我已審閱。以下是我的評估。

### 1. 核心假設檢驗

這份 ADR 基於以下未明說的假設，其中一些風險較高：

-   **假設 1：工作流是單線程的。** ADR 設計隱含了「只有一個審核者（修修）」且「一個 draft 在任何時間點只會被一個 agent 或一個使用者操作」。當未來出現多位助理或更複雜的協作流程時，目前的鎖定機制（或說，缺乏鎖定機制）會導致 race conditions，例如修修在編輯時，另一個程序試圖更新它。
-   **假設 2：所有 `payload` 都是可序列化為 JSON 的純文字資料。** 文件假設所有待審批的內容都可以輕易地用 JSON 表示。如果未來需要處理二進位檔案（如 Chopper 自動生成的回覆梗圖、Usopp 要發佈的特色圖片），目前的 `payload TEXT` schema 將無法應對，需要引入外部儲存（如 R2）和參照。
-   **假設 3：VPS 上的本地檔案系統是可靠且同步的。** `payload_source='obsidian://path/to/file.md'` 這個設計，以及 `POST /bridge/drafts/obsidian-reimport/{id}` 的 API，都假設了 Bridge 後端（跑在 VPS 上）可以直接存取修修的 Obsidian vault（`F:\Shosho LifeOS\Drafts\`）。這是一個極其脆弱的假設。這暗示了 VPS 和修修的個人工作機之間存在某种形式的檔案同步（如 Syncthing, Dropbox），但文件中並未提及。這個同步機制的延遲、衝突、失敗都會直接破壞此工作流程。
-   **假設 4：SQLite 的效能和併發性足夠。** 目前單一使用者的情境下，SQLite 搭配 WAL 模式或許堪用。但系統中存在多個 agent daemons（Usopp, Chopper...）和 Web UI 同時讀寫 `state.db`。在高頻率操作下，SQLite 的 table-level locking 可能成為效能瓶頸，導致 `database is locked` 錯誤。尤其當 `payload` 變大時，寫入延遲會加劇此問題。

### 2. 風險分析

-   **(a) 未被提及但會造成生產問題的風險**
    1.  **資料庫遷移（Schema Evolution）**：ADR 沒有定義 `approval_queue` schema 的演進策略。未來若要增加欄位（例如 `scheduled_at`），如何向下相容地更新 table？目前直接用 SQL `CREATE TABLE`，缺乏版本控制和遷移腳本（如 Alembic），這在系統迭代中是個定時炸彈。
    2.  **Obsidian 同步災難**：如核心假設所述，依賴一個未定義的檔案同步機制是最大的風險。同步失敗、延遲、版本衝突（修修在 A 機編輯，B 機的舊版本同步上來覆蓋）會導致資料遺失或錯亂。此外，`F:\` 路徑硬編碼在 Windows 之外的環境無法運作。
    3.  **Agent 執行緒競爭**：`peek_approved` 的實作細節未明。如果多個 agent（或同一個 agent 的多個 worker）同時呼叫它，它們可能會拿到相同的 draft 列表，導致重複執行。需要一個原子性的「認領」操作，例如 `UPDATE ... SET status='executing' WHERE id IN (...) AND status='approved' RETURNING ...`。
    4.  **長事務與鎖定**：將 50KB 的 `payload` 存入 SQLite 的 `TEXT` 欄位，會增加 I/O 時間，延長資料庫事務，提高 `database is locked` 的機率。

-   **(b) 已提及但嚴重度被低估的風險**
    1.  **`payload` 存在 SQLite**：作者提到 100 篇 50KB 的文章是 5MB，並認為 SQLite 能扛。這低估了問題的複雜度。問題不在於總大小，而在於單次 I/O 的負擔和對資料庫 VACUUM 的需求。更嚴重的是，將非結構化的巨大 JSON blob 塞進關聯式資料庫，會讓未來的查詢和分析變得極其困難和低效。
    2.  **SQLite 單點故障**：文件中提到「被 R2 備份 cover」，這是一種誤導性的安全感。R2 備份是災難恢復（Disaster Recovery），通常有時間差（例如每小時一次）。如果 `state.db` 在兩次備份之間損毀，中間所有 `pending`、`approved` 的 draft 都會遺失。這對一個以佇列為核心的系統是不可接受的。

-   **(c) 已提及但嚴重度被高估的風險**
    1.  **CSRF / Brute force**：在一個只有單一使用者（修修）且通過 HMAC cookie 認證的內部管理介面（Bridge）上，這類風險相對較低。只要現有的認證機制穩固，這不是 Phase 1 的首要威脅。投入過多精力去防範，不如先解決資料一致性和可靠性問題。

### 3. 替代方案

1.  **資料庫選擇**：
    -   **替代方案**：在目前 VPS 資源限制下，使用 **PostgreSQL**。它同樣可以跑在 Docker 容器中，佔用資源不多，但提供了行級鎖、更強的併發處理能力、JSONB 原生資料類型（可索引）和更成熟的遷移工具。這能直接解決 SQLite 的鎖定和 `payload` 查詢問題。
    -   **為何作者沒選**：可能是為了追求「零設定」、「單檔案」的簡潔性，低估了 agent 系統的併發寫入壓力。

2.  **佇列與任務分發**：
    -   **替代方案**：使用一個真正的任務佇列系統，如 **Redis + RQ (Redis Queue)** 或 **Celery**。Draft 的 `id` 和 metadata 存於 SQL 資料庫，但 `approved` 的任務 ID 被推送到 Redis list 中。Agent worker 從 Redis `BLPOP` 任務，保證了原子性和任務分發的可靠性，避免了 `peek_approved` 的競爭問題。
    -   **為何作者沒選**：可能認為引入 Redis 會增加架構複雜度。但在這個 agent 系統中，一個可靠的任務佇列是基礎設施，不是可選項。自建輪詢機制 (`peek_approved`) 是在重新發明一個不可靠的輪子。

3.  **Obsidian 整合**：
    -   **替代方案**：放棄脆弱的檔案系統同步。改為 **API-based 整合**。Bridge UI 提供「複製 Markdown」按鈕，修修手動貼到 Obsidian。完成後，再從 Obsidian 手動複製貼回 Bridge 的 textarea。或者，利用 Obsidian 的 `Advanced URI` plugin，讓 Bridge 生成一個 `obsidian://` 連結，點擊後能在 Obsidian 中創建一個帶有內容的新筆記。雖然摩擦力稍高，但它健壯、可靠、無同步依賴。
    -   **為何作者沒選**：追求極致的「無縫」體驗，但犧牲了系統的穩定性。

### 4. 實作 pitfalls

1.  **`approval_queue` schema**:
    -   `payload TEXT NOT NULL`：如前述，這是最大的坑。巨大的 JSON blob 會拖垮效能，且無法對其內容進行有效查詢。應該將 `payload` 中可結構化的部分（如 `title`, `slug`, `metadata`）拆分成獨立欄位，只將正文等非結構化部分存為 JSON 或 TEXT。
    -   `execution_result TEXT`：同樣，API 回傳的 ID 或 URL 應該是結構化的，例如 `executed_post_id TEXT`, `executed_post_url TEXT`，而非塞進一個 JSON blob。這對後續追蹤和監控至關重要。
    -   **缺乏 FSM 約束**：`status` 欄位的狀態轉移完全依賴應用層邏輯。資料庫層面沒有任何約束來防止非法狀態轉移（例如從 `executed` 跳回 `pending`）。

2.  **`shared/approval_queue.py` API 契約**:
    -   `peek_approved(source_agent: str, limit: int = 10)`：此 API 設計有嚴重的競爭條件漏洞。Worker A 和 Worker B 可能同時 peek 到同一個 draft ID，導致重複執行。正確的契約應該是 `claim_approved_drafts(agent: str, limit: int)`，這個方法在資料庫層面必須是原子的，將 `status` 從 `approved` 修改為 `executing`，並返回被成功認領的 drafts。

3.  **`thousand_sunny/routers/bridge.py` API 契約**:
    -   `POST /bridge/drafts/obsidian-reimport/{id}`：這個 API 的存在本身就是個巨大的坑，它依賴一個不可靠的外部機制（檔案同步）。一旦同步延遲，它就會讀取到舊的內容，覆蓋掉修修的最新修改。必須移除。
    -   `POST /bridge/drafts/{id}/edit` 和 `POST /bridge/drafts/{id}/approve`：如果這兩個 API 都包含 `payload`，它們的語義就不清晰。應該明確：`edit` 只負責更新內容，`approve` 只負責改變狀態。如果 approve 時可以順便修改，API 路徑應該反映出來，如 `PUT /bridge/drafts/{id}/content` 和 `POST /bridge/drafts/{id}/approve`。

### 5. 缺失的視角

-   **可觀測性（Observability）**：ADR 完全沒提。這個佇列是系統的中樞，必須有詳細的監控。例如：
    -   佇列深度（不同 `status` 的 draft 數量）的時序圖。
    -   Draft 平均等待審批時間、平均執行時間。
    -   `failed` 狀態的錯誤率和錯誤類型分佈。
    -   這些指標應接入 Prometheus/Grafana 或類似的監控儀表板。
-   **可維護性**：
    -   **資料庫遷移**：如前述，完全缺失。
    -   **資料清理策略**：`approval_queue` 表會無限增長。對於 `executed` 和 `rejected` 的舊紀錄，沒有定義歸檔或刪除策略。幾個月後，這個表會變得臃腫，影響索引效率。
-   **成本**：雖然 ADR 提到了 LLM cost tracking，但忽略了儲存成本和 I/O 成本。將大量 TEXT 存在 `state.db`，會增加 R2 備份的大小和傳輸成本。更重要的是，它會增加 VPS 的 I/O 부荷，這在資源受限的環境中是關鍵瓶頸。
-   **可測試性**：ADR 沒有描述如何測試這個流程。特別是與 Obsidian 的檔案系統互動，這部分邏輯極難進行自動化單元測試或整合測試，進一步證明了該設計的脆弱性。

### 6. Phase 拆分建議

-   **必須 Phase 1 完成**：
    1.  **核心資料庫和 Schema**：重新設計 `approval_queue` schema，將 `payload` 拆分，並選擇 SQLite 或 PostgreSQL（強烈建議後者）。
    2.  **核心生命週期 API**：實現 `enqueue`, `approve`, `reject`，以及一個**原子性的 `claim`** 操作來取代 `peek`。
    3.  **基礎 Bridge UI**：實現 Table view、inline edit（僅限 metadata 和短文本）、Approve/Reject 按鈕。
    4.  **Agent 執行邏輯**：Usopp agent 實現從佇列中 `claim` 任務並執行的 daemon。

-   **可以延後到 Phase 2+**：
    1.  **Obsidian 整合**：整個 Obsidian 檔案同步方案應被擱置或重新設計。Phase 1 可以先用簡單的「複製/貼上」工作流。
    2.  **Diff View**：`diff_target_id` 相關功能是優化，不是核心。可以後續再加。
    3.  **UI 優化**：鍵盤快捷鍵、數字徽章等都是體驗優化，可以在核心流程跑通後再實現。
    4.  **自動重試與警報**：失敗後自動轉 `rejected` 和 Slack alert 可以在 Phase 2 完善。Phase 1 手動重試即可。
    5.  **審批級別配置化**：初期可以硬編碼所有發布都需要審批，配置化是後續優化。

-   **應拆分成獨立 ADR**：
    1.  **「任務佇列與 Agent Worker 架構」**：這是一個比 HITL UI 更基礎的設施。應該有獨立的 ADR 來決策是採用輪詢、Redis，還是其他機制，並定義 worker 的併發、錯誤處理和生命週期。ADR-006 試圖一次解決兩個問題（HITL 介面 + 任務分發），導致兩者都沒設計好。

### 7. 結論

-   **(a) 整體可行性評分：3/10**
    -   **理由**：方向正確，但技術選型和核心設計（特別是 Obsidian 整合和任務拾取機制）存在根本性缺陷。直接照此實作，系統將會非常不穩定、難以維護，且充滿 race conditions。它嚴重低估了併發和資料一致性的挑戰。

-   **(b) 建議：退回重寫**
    -   這份 ADR 需要從根本上重新思考其資料持久化、任務分發和外部系統整合的策略。

-   **(c) 最 blocking 的 1-2 個問題**：
    1.  **不可靠的 Obsidian 檔案系統整合**：必須用一個穩健的、解耦的方案（如 API 或手動複製貼上）取代當前基於檔案同步的設計。這是整個工作流中最脆弱的一環。
    2.  **不安全的任務拾取機制 `peek_approved`**：必須替換為一個原子性的「認領」操作，以防止多個 agent worker 重複執行同一個任務。這是保證系統正確性的核心。