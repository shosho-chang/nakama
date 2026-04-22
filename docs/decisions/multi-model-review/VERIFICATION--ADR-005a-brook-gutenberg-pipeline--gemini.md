---
source_adr: ADR-005a-brook-gutenberg-pipeline
verification_round: 2
reviewer_model: gemini-2.5-pro
elapsed_seconds: 22
review_date: 2026-04-22
---

# ADR-005a-brook-gutenberg-pipeline — 修訂驗證（gemini）

好的，身為資深軟體架構師，這是我對 ADR-005a 修訂版的驗證報告。

---

### 1. Blocker 逐項檢核

針對 Consolidated Review §5「最 blocking 的問題」每一項的檢核結果如下：

| 原 blocker | 修訂版回應 | 證據 |
| :--- | :--- | :--- |
| **1. Draft Object Schema + Gutenberg Validation 未定義** | ✅ **完整解** | **Schema**：修訂版 `Decision §2` 中定義了完整的 Pydantic schema `DraftV1`，包含所有欄位、型別、版本，並援引了通用原則 `schemas.md`。**Gutenberg Validation**：修訂版 `Decision §1` 和 `§4` 徹底改變了策略，從「LLM 直出 HTML」改為「LLM 輸出 AST + 確定性 builder」，並增加了 `GutenbergValidator` 作為雙重保障。這直接且漂亮地解決了核心問題。 |
| **2. 無 Staging 環境 + 無測試策略** | 🔄 **拆到別處** | 修訂版 ADR-005a 是一份聚焦於 Brook pipeline 的設計文件，不應包含基礎設施的建置決策。這個 blocker 顯然屬於 **ADR-005b (Usopp publishing)** 或一個更上層的 ADR-005 (overall) 的範疇。此 ADR 內有提及 `roundtrip 測試與既有 192 篇 migration`，顯示測試策略有被考量，但 staging 環境的建置不在其 scope。**判定：正確地將此議題排除在此 ADR 範疇之外。** |
| **3. 狀態機 / idempotency / 原子性缺失** | ⚠️ **部分解** | **Idempotency**：修訂版 `Consequences > Idempotency` 章節定義了 `draft_id` 的生成方式 `(operation_id, title_hash)`，確保 Brook compose 是冪等的，這點做得很好。**狀態機/原子性**：這部分責任已被明確劃分給 Usopp 和 `approval_queue`（ADR-006）。`ADR-005a` 產出 `DraftV1` 後的流程它不負責，是合理的關注點分離。**還差什麼**：此 ADR 仍應更明確地指出，`DraftV1` 的 `draft_id` 將被下游系統（ADR-005b/ADR-006）用作 idempotency key。目前的寫法暗示了這點，但沒有寫死成契約。 |
| **4. 資安設計（auth + secret + 最小權限）未定義** | 🔄 **拆到別處** | 與 Blocker #2 類似，資安設計屬於與 WordPress 直接互動的 Usopp (ADR-005b) 的責任範疇。ADR-005a (Brook) 不直接與 WP 溝通，因此不包含 auth 設計是正確的 scope 分割。 |
| **5. VPS 資源 benchmark 未做** | 🔄 **拆到別處** | 同上，資源 benchmark 應在 ADR-005b (Usopp) 或整體架構 ADR-005 中處理，因為它涉及多個 agent 的併發執行。ADR-005a 聚焦於 schema 和 build pipeline，將此問題排除是合理的。 |

**小結**：ADR-005a 成功地將原先龐大、模糊的 ADR-005 拆解成一個聚焦且可執行的單元。它**完全解決**了其範疇內最關鍵的兩個 blocker（Schema & Gutenberg Validation），並明智地將其他 blocker 的責任劃分給其他 ADR。

---

### 2. 新發現的問題

修訂版雖然品質很高，但引入或暴露了以下新問題：

1.  **AST 遞迴深度無限制，可能導致 DoS 或效能問題**
    *   **問題描述**：`BlockNodeV1` 的 `children` 欄位是 `list["BlockNodeV1"]`，這是一個遞迴定義。若 LLM 產出一個極深（例如 500 層）的巢狀列表或引言，`gutenberg_builder.py` 的遞迴序列化過程可能導致 Python `RecursionError` 或顯著的效能下降。
    *   **嚴重度**：Medium
    *   **建議修法**：在 `DraftV1` schema 層級加入一個 Pydantic custom validator，限制 AST 的最大深度（例如 `max_depth=10`），在 compose 階段就 fail fast。

2.  **`primary_category` 與 Style Profile 的耦合過於僵硬**
    *   **問題描述**：ADR 假設 `primary_category` 和 `config/style-profiles/{category}.yaml` 檔名一一對應。這意味著新增一個 category 就必須新增一個 profile 檔案，缺乏彈性。例如，若未來有 10 個 science 子分類，但只想共用 2 個 profile，目前的設計無法直接支援。
    *   **嚴重度**：Medium
    *   **建議修法**：在 Style Profile YAML 中增加一個 `applies_to_categories: ["neuroscience", "sport-science"]` 欄位，讓 Brook 在查找時依此對應，而不是依賴檔名。這樣 category 和 profile 就可以多對多，更具擴展性。

---

### 3. 修訂品質評估

| 維度 | 1-10 分 | 一句話評語 |
|---|---|---|
| Schema / 契約完整度 | **10/10** | 極為出色。Pydantic schema 嚴格、完整、版本化，並援引通用原則，是團隊 ADR 的典範。 |
| Reliability 機制（idempotency / atomic / SPOF）| **8/10** | 在自身範疇內做得很好（draft_id 冪等性、純函數），但對下游系統的契約可以再明確一點。 |
| Observability（log / metric / SLO / probe）| **9/10** | 完整且務實。明確定義了 structured log 欄位、metrics 和可量化的 SLO，完全符合 `observability.md` 的要求。 |
| 可實作性（工程師照寫能不能動） | **10/10** | 極高。提供了清晰的 schema、函式簽名和實作策略，工程師幾乎可以直接將其翻譯成 code。 |
| 範圍聚焦度（沒再 scope creep） | **10/10** | 滿分。這份 ADR 最大的成功在於它精準地切分問題，只解決 Brook pipeline，抗拒了處理所有問題的誘惑。 |

---

### 4. 最終判定

-   **go / no-go**：**Go**。工程師可以立即基於此份 ADR 開始實作 Brook 的 `DraftV1` 生成 pipeline。這份修訂版品質極高，是 ADR 的優秀範例。

-   **Phase 1 實作過程中要特別盯的風險**：
    1.  **AST 遞迴攻擊**：在 Validator 實作階段，務必加入最大深度限制的測試案例，防止惡意或錯誤的 LLM 輸出癱瘓 builder。
    2.  **Round-trip 測試覆蓋率**：針對 192 篇既有文章的 `parse → build` 測試是關鍵。如果通過率低，需要儘早決策是擴充 builder 功能還是手動清理舊文章的 markup。這個任務的複雜度可能被低估。