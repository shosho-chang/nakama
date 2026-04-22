---
source_adr: ADR-005a-brook-gutenberg-pipeline
verification_round: 2
reviewer_model: grok-4
elapsed_seconds: 58
review_date: 2026-04-22
---

# ADR-005a-brook-gutenberg-pipeline — 修訂驗證（grok）

### 1. Blocker 逐項檢核

- **原 blocker 一句話描述**：Draft Object Schema + Gutenberg Validation 未定義 —— Brook / Usopp 的核心 contract 不存在 + 無 block markup validator，開工即兩個 agent 無法並行。  
  **修訂版回應**：✅ 完整解。  
  **證據**：2. Schema 定義 DraftV1 等完整 Pydantic schema；4. Gutenberg Validator 定義 validate 函式與白名單、comment parity 等檢查；3. Gutenberg Builder API 契約提供 builder/parse 確保 markup 正確。

- **原 blocker 一句話描述**：無 Staging 環境 + 無測試策略 —— 在生產 WP（192 篇既有內容）上試 API = 災難一次。  
  **修訂版回應**：⚠️ 部分解（有測試策略但無 staging 環境，僅提 round-trip test 對既有文章，無法模擬 WP 整合）。  
  **證據**：開工 Checklist 的測試部分（Gutenberg builder 單元測試、round-trip test 對 192 篇、validator 對壞 markup 測試）；但無 staging 實例提及。

- **原 blocker 一句話描述**：狀態機 / idempotency / 原子性缺失 —— 沒持久化 state 的 retry 會產生孤兒 post + 重複文章。  
  **修訂版回應**：⚠️ 部分解（有 idempotency 設計但無完整狀態機，原子性依賴 Usopp 側 queue）。  
  **證據**：Idempotency 章節定義 draft_id 基於 operation_id + title_hash，builder/validator 為純函式冪等；跟 ADR-005b 的介面提及寫入 approval_queue（ADR-006）。

- **原 blocker 一句話描述**：資安設計（auth + secret + 最小權限）未定義 —— application password 洩漏 = 全站 game over。  
  **修訂版回應**：❌ 未解。  
  **證據**：無任何資安相關章節或規範。

- **原 blocker 一句話描述**：VPS 資源 benchmark 未做 —— 4GB RAM 能不能扛 Brook + Usopp + Robin + MySQL 並發，沒實測就上 = 賭博。  
  **修訂版回應**：❌ 未解。  
  **證據**：無 benchmark 提及或實測計劃。

### 2. 新發現的問題

- **問題描述**：ComplianceFlagsV1 schema 雖定義，但無機制強制 LLM 誠實填寫 bool 值（如 claims_no_therapeutic_effect），可能導致虛假 compliance 通過而無實際 scan 驗證。 / **嚴重度**：High / **建議修法**：在 Gutenberg Validator 加 compliance scan 邏輯，掃描 content 內 blacklist_terms 並 override flags 若 mismatch。
- **問題描述**：Tag 策略僅 filter 既有 tag，但 LLM 可能重複建議相同 tag 導致 tags list 冗餘，無去重邏輯。 / **嚴重度**：Medium / **建議修法**：在 DraftV1 schema 加 Field(unique_items=True) 強制去重，或 builder 側加 filter。
- **問題描述**：SLO 定義 compose p95 < 90 秒，但無外部 probe 驗證（如 observability.md §4 要求），僅內部 metric。 / **嚴重度**：Medium / **建議修法**：開工 Checklist 加 external probe 設計，如 Franky 定期 ping Brook endpoint。
- **問題描述**：Open Questions 提 raw_html block 作為逃生艙，但無禁用機制，Phase 1 若誤開可能引入未驗證 HTML。 / **嚴重度**：Low / **建議修法**：在 BlockNodeV1 schema 移除 "html_raw" 直到 Phase 2。

### 3. 修訂品質評估

| 維度 | 1-10 分 | 一句話評語 |
|---|---|---|
| Schema / 契約完整度 | 9 | Schema 嚴格定義且援引原則，涵蓋 AST 到 draft 全鏈，但 compliance 強制性不足。 |
| Reliability 機制（idempotency / atomic / SPOF）| 7 | Idempotency 落地 draft_id，SPOF 表列清楚，但 atomicity 依賴外部 ADR-005b 未完整。 |
| Observability（log / metric / SLO / probe）| 8 | Structured log、metric、SLO 定義到位，operation_id 貫穿，但缺外部 probe。 |
| 可實作性（工程師照寫能不能動） | 8 | Pipeline、三類 profile、builder API 具體，開工 Checklist 詳盡，但 staging 缺漏影響整合測試。 |
| 範圍聚焦度（沒再 scope creep） | 9 | 嚴格限 Brook 端，拆分 ADR-005b/c 避免膨脹，僅回應特定 blocker。 |

### 4. 最終判定

- **no-go**：工程師還不能開始寫 code。  
- **如果 no-go**：必須先修的 1-2 個 blocking 項目：(1) 補 staging 環境與測試策略（blocker 2 未全解）；(2) 定義資安設計（blocker 4 全未解）。