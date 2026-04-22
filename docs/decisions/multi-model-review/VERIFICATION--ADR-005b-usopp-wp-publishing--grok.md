---
source_adr: ADR-005b-usopp-wp-publishing
verification_round: 2
reviewer_model: grok-4
elapsed_seconds: 45
review_date: 2026-04-22
---

# ADR-005b-usopp-wp-publishing — 修訂驗證（grok）

### 1. Blocker 逐項檢核

- **原 blocker 一句話描述**：Draft Object Schema + Gutenberg Validation 未定義（Brook / Usopp 的核心 contract 不存在 + 無 block markup validator，開工即兩個 agent 無法並行）。
  - **修訂版回應**：⚠️ 部分解（Draft Schema 援引 ADR-005a，但 Gutenberg Validation 只做粗略 fetch 比對 content 一致，未有嚴格 block syntax validator 或 JSON AST 轉換）。
  - **證據**：§9 Schema 援引 ADR-005a 的 DraftV1；§4 Atomic Publish 的 validated 步驟僅 assert fetched content 與 draft 一致，無 validator 細節。

- **原 blocker 一句話描述**：無 Staging 環境 + 無測試策略（在生產 WP 上試 API = 災難一次）。
  - **修訂版回應**：✅ 完整解。
  - **證據**：§8 測試策略明確定義 Docker WP staging、CI unit/integration/smoke test、錄製 cassette，以及生產 WP 禁連 guard。

- **原 blocker 一句話描述**：狀態機 / idempotency / 原子性缺失（沒持久化 state 的 retry 會產生孤兒 post + 重複文章）。
  - **修訂版回應**：✅ 完整解。
  - **證據**：§1 Publish State Machine 定義持久化 state 與續跑邏輯；§2 Idempotency 設計雙層 key（Nakama + WP meta）；§4 Atomic Publish 先 draft 後 publish + 驗證。

- **原 blocker 一句話描述**：資安設計（auth + secret + 最小權限）未定義（application password 洩漏 = 全站 game over）。
  - **修訂版回應**：✅ 完整解。
  - **證據**：§7 Auth / Secret / 最小權限 表格規範自訂角色、.env 權限、輪換、HMAC 範圍、log 遮罩、rate limit；並處理 wp_kses_post 陷阱。

- **原 blocker 一句話描述**：VPS 資源 benchmark 未做（4GB RAM 能不能扛並發，沒實測就上 = 賭博）。
  - **修訂版回應**：🔄 拆到別處（移到 ADR-007 Franky 處理）。
  - **證據**：Notes 節明說統一在 ADR-007 範疇，本 ADR 僅列為前置依賴。

### 2. 新發現的問題

- **問題描述**：可觀測性雖然有 structured log 和 SLO，但未定義外部 probe（observability.md §4），如 publish 後 Franky 自動驗證 post 是否上線，導致 cache purge 失敗可能延遲偵測。嚴重度：High。建議修法：SLO 加 external probe 指標（如 publish 後 1 分鐘 curl permalink 驗證內容）。
- **問題描述**：健康內容 compliance 完全缺席（原 review §2.11），修訂版未加 Brook guardrail 或 HITL checklist，台灣法規風險未緩解。嚴重度：High。建議修法：援引 ADR-005a 加 compliance 詞彙黑名單，並在 PublishRequestV1 加 compliance_approved 欄位。
- **問題描述**：LLM / 運維成本估算缺失（原 review §2.9），修訂版未加 cost 估算章節或預算上限，Phase 1 預算風險未控管。嚴重度：Medium。建議修法：Consequences 加 cost 估算段落，依 feedback_llm_cost_estimation.md 計算 token 消耗。
- **問題描述**：Tag 策略雖禁止新增，但未處理過多 tag (497) 的 token 爆量問題（原 review §2.8），Usopp 只 skip 不存在 tag，未定義 Brook 選 tag 的過濾邏輯。嚴重度：Medium。建議修法：§6 加 tag selection 限制（如 max 5 個），並拆 tag governance ADR。
- **問題描述**：開工 Checklist 雖列 SPOF 表，但 state.db 的備份策略（reliability.md §3）僅提 WAL + R2，未定義 restore runbook 或測試，單點故障風險未全緩解。嚴重度：Low。建議修法：Checklist 加 state.db restore test。

### 3. 修訂品質評估

| 維度 | 1-10 分 | 一句話評語 |
|---|---|---|
| Schema / 契約完整度 | 8 | 外部 schema 援引原則且有 extra=forbid，但 Gutenberg 驗證仍弱，依賴 ADR-005a 未完整整合。 |
| Reliability 機制（idempotency / atomic / SPOF）| 9 | State machine 和雙層 idempotency 設計紮實，SPOF 表與 retry 規則符合原則，僅少 restore 細節。 |
| Observability（log / metric / SLO / probe）| 7 | Structured log 和 SLO 定義齊全，但缺 external probe 和 operation_id 跨 agent 傳遞。 |
| 可實作性（工程師照寫能不能動） | 8 | 代碼範例和 checklist 具體，工程師可依循，但 LiteSpeed purge auth 仍 open question 需實測。 |
| 範圍聚焦度（沒再 scope creep） | 9 | 嚴格回應特定 blocker，未混入 Phase 2 元素如 FluentCRM，拆分明確。 |

### 4. 最終判定

- **go / no-go**：no-go。
- **如果 no-go**：必須先修的 1-2 個 blocking 項目：(1) 完整定義 Gutenberg block validator（或切 JSON AST 方案），整合到 validated 步驟；(2) 加健康內容 compliance guardrail 和 HITL checklist，避免法規風險。