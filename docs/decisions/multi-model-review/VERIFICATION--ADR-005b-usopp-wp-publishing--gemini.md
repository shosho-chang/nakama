---
source_adr: ADR-005b-usopp-wp-publishing
verification_round: 2
reviewer_model: gemini-2.5-pro
elapsed_seconds: 22
review_date: 2026-04-22
---

# ADR-005b-usopp-wp-publishing — 修訂驗證（gemini）

好的，身為資深軟體架構師，這是我對 ADR-005b 修訂版的驗證報告。

---

### 1. Blocker 逐項檢核

#### 1.1. Draft Object Schema + Gutenberg Validation 未定義

-   **原 blocker 一句話描述**：Brook / Usopp 的核心 contract 不存在，且無 block markup validator，導致無法並行開發與髒數據風險。
-   **修訂版回應**：🔄 拆到別處
-   **證據**：ADR-005b 開頭 `Context` 區明確指出「Brook compose 行為由 ADR-005a 負責」。ADR §9 `PublishRequestV1` schema 也直接援引來自 ADR-005a 的 `DraftV1`。此舉是合理的 scope 拆分，驗證工作轉移至對 ADR-005a 的 review。

#### 1.2. 無 Staging 環境 + 無測試策略

-   **原 blocker 一句話描述**：在生產 WP（192 篇既有內容）上直接測試 API 是災難性的。
-   **修訂版回應**：✅ 完整解
-   **證據**：ADR §8 `測試策略` 章節明確定義了三層測試：
    -   **Staging 環境**：`Docker WP staging` 搭配 20 篇 subset 資料。
    -   **CI 策略**：Unit test 用 `responses` library mock，Integration test 打 staging，Smoke test 做 E2E 驗證。
    -   **安全護欄**：`生產永遠不是測試環境`，CI 有 `PYTEST_WP_BASE_URL` 檢查。

#### 1.3. 狀態機 / idempotency / 原子性缺失

-   **原 blocker 一句話描述**：無持久化狀態的 retry 會產生孤兒 post 或重複發文。
-   **修訂版回應**：✅ 完整解
-   **證據**：
    -   **狀態機**：ADR §1 `Publish State Machine` 定義了 `queued` 到 `done` 的 9 個狀態，並存入 `state.db`，支持 crash 恢復。
    -   **Idempotency**：ADR §2 `Idempotency 設計` 設計了雙層防禦：`publish_jobs.draft_id` (Nakama 側) 和 `nakama_draft_id` post meta (WordPress 側)，並提供 `find_by_meta` 的 pre-check 流程。
    -   **原子性**：ADR §4 `Atomic Publish` 採用 `status=draft` → 驗證 → `status=publish` 的流程，確保發布是最後一步，失敗時不會有半成品上線。

#### 1.4. 資安設計（auth + secret + 最小權限）未定義

-   **原 blocker 一句話描述**：Application password 洩漏等於全站失守，且權限、存放、輪換均無規範。
-   **修訂版回應**：✅ 完整解
-   **證據**：ADR §7 `Auth / Secret / 最小權限` 以表格形式清晰定義了：
    -   **最小權限**：自訂 `nakama_publisher` 角色，繼承 Editor 但禁止高危權限。
    -   **Secret 存放**：`.env` 檔案權限 `0600`。
    -   **輪換策略**：每 90 天輪換。
    -   **HMAC 範圍**：釐清僅用於 agent 內部，不與 WP REST 混淆。
    -   **Log 遮罩**：密碼不進 log。
    -   還額外處理了 `wp_kses_post` 過濾器陷阱，展現了深度。

#### 1.5. VPS 資源 benchmark 未做

-   **原 blocker 一句話描述**：4GB RAM 能否扛住並發負載未經驗證，是賭博。
-   **修訂版回應**：🔄 拆到別處
-   **證據**：ADR `Notes` 區塊明確指出「VPS benchmark（review §2.10）統一在 ADR-007 Franky 範疇處理，本 ADR 僅列為前置依賴」。這是合理的，將系統級的資源規劃從單一 agent 的 ADR 中解耦。

### 2. 新發現的問題

修訂版品質很高，幾乎沒有引入新問題，但有一點值得在實作前釐清。

1.  **問題描述**：ADR §2 `Idempotency 設計` 中，`GET /wp/v2/posts?meta_key=...` 的 pre-check 是個 race condition 的潛在來源。在高併發下，兩個 Usopp worker 可能同時執行 pre-check、都發現 post 不存在，然後都去執行 `create_post`，最終導致重複文章。雖然 WP core 會對 slug 做唯一性處理（例如 `my-post` 和 `my-post-2`），但這依然會產生一個非預期的孤兒 post。
    **嚴重度**：Medium
    **建議修法**：在 `create_post` 呼叫時，將 `nakama_draft_id` meta field 包在 request 裡。然後在 WP 端透過 `register_post_meta` API，將 `nakama_draft_id` 註冊為 `single => true, unique => true`。這能將唯一性檢查下推到資料庫層，利用 DB transaction 的原子性來防止 race condition，比應用層的 `check-then-act` 模式更可靠。若無法在 WP 端加 unique meta，則應在 Nakama 側 `publish_jobs` 表 claim draft 時加上 DB-level lock。

### 3. 修訂品質評估

| 維度 | 1-10 分 | 一句話評語 |
|---|---|---|
| Schema / 契約完整度 | 9/10 | 清晰定義了內外部 schema，援引通用原則，並建立了 anti-corruption layer，僅 `DraftV1` 需看另一份 ADR。 |
| Reliability 機制（idempotency / atomic / SPOF）| 9/10 | 狀態機、雙層 idempotency、原子發布流程設計得非常穩健，SPOF 表清晰，僅 race condition 可再加強。 |
| Observability（log / metric / SLO / probe）| 8/10 | 開工 Checklist 列出了具體的 log 欄位、metric counter/gauge 和 SLO，但 ADR 正文對此著墨較少，需依賴工程師自律實現。 |
| 可實作性（工程師照寫能不能動） | 10/10 | ADR 提供了偽代碼、表格化規範、清晰的步驟，甚至點出了 `wp_kses_post` 這類實作陷阱，工程師可以直接照著 checkist 開工。 |
| 範圍聚焦度（沒再 scope creep） | 10/10 | 完美聚焦在 Usopp publish 流程，果斷將 Brook (`ADR-005a`)、Approval Queue (`ADR-006`)、VPS benchmark (`ADR-007`) 拆分出去，是 ADR 典範。 |

### 4. 最終判定

-   **go / no-go**：**Go**。這份修訂版 ADR 品質極高，直接且完整地回應了上一輪 review 的所有核心 blocker。團隊可以立即基於此文件開工。

-   **Phase 1 實作過程中要特別盯的 1-2 個風險**：
    1.  **Idempotency Race Condition**：開工前，團隊應快速討論我指出的 race condition 問題，並決定採用 DB-level meta unique constraint 或 application-level lock 來解決。這項調整很小，但對系統穩定性至關重要。
    2.  **LiteSpeed Purge API 整合**：ADR §5 將此列為「開工第一週實測」的項目，這是對的。但這個外部依賴存在不確定性（auth 方式、可用性）。應指派一位工程師在 Sprint 第一天就開始研究並回報，若發現 API 不可用或不穩定，團隊需立即啟用 fallback 方案（例如，依賴 TTL 過期）。