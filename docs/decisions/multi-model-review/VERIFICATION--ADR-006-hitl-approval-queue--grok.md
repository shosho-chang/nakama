---
source_adr: ADR-006-hitl-approval-queue
verification_round: 2
reviewer_model: grok-4
elapsed_seconds: 49
review_date: 2026-04-22
---

# ADR-006-hitl-approval-queue — 修訂驗證（grok）

### 1. Blocker 逐項檢核

針對 Consolidated Review §5「最 blocking 的問題」每一項，逐一檢核修訂版 ADR 的回應。

- **原 blocker 一句話描述**：Payload schema 必須先定義（Pydantic）。  
  **修訂版回應**：✅ 完整解。  
  **證據**：§2 Pydantic Payload Schema 詳細定義 ApprovalPayloadV1 與子類別（如 PublishWpPostV1），包含版本欄位、Pydantic 配置（extra=forbid）、enqueue/reader 端實作範例，並援引 principles/schemas.md。

- **原 blocker 一句話描述**：`peek_approved` 改原子 `claim_approved_drafts`。  
  **修訂版回應**：✅ 完整解。  
  **證據**：§3 Atomic Claim 實作 claim_approved_drafts 函數，使用 BEGIN IMMEDIATE + UPDATE RETURNING 確保原子性，並有 DoD 驗收測試（10 thread stress test）。

- **原 blocker 一句話描述**：Obsidian 整合：移出 Phase 1 或決定傳輸機制。  
  **修訂版回應**：🔄 拆到別處（指明去向：獨立成 ADR-006b-obsidian-vault-sync.md，Phase 2 research）。  
  **證據**：Context 節明述本版切掉 Obsidian 整合到 ADR-006b，Phase 1 只做核心 queue 骨架；Consequences 與 Notes 節重申不做 Obsidian。

- **原 blocker 一句話描述**：SQLite WAL + litestream replication + 定義 RPO。  
  **修訂版回應**：⚠️ 部分解（WAL 已啟用，但 litestream 延到 Phase 2，RPO 未明確定義為 <5 分鐘，只提 daily snapshot）。  
  **證據**：§5 SQLite 設定有 PRAGMA journal_mode=WAL、synchronous=NORMAL、busy_timeout=5000；litestream 列為 Phase 2，Phase 1 用 daily snapshot 到 R2；SPOF 表提及 RPO 風險但未定義具體值。

- **原 blocker 一句話描述**：Status FSM 保護（CHECK constraint + transition whitelist）。  
  **修訂版回應**：✅ 完整解。  
  **證據**：§4 Status FSM 有 DB CHECK constraint 與 ALLOWED_TRANSITIONS dict，transition() 函數封裝所有變動（含 TOCTOU 防護），並有 DoD 非法轉移測試。

### 2. 新發現的問題

修訂版引入或暴露的新問題（原 review 沒抓到的），按嚴重度排序。僅列尚未完全緩解且影響 Phase 1 的實質問題。

- **問題描述**：litestream replication 延到 Phase 2，導致 Phase 1 RPO 依賴 daily snapshot，可能達 24 小時（未定義明確 RPO 值），DB 崩潰時 approved draft 丟失風險高。  
  **嚴重度**：Critical。  
  **建議修法**：Phase 1 即引入 litestream（如 review §6 建議），或明確定義 RPO <1 小時並用 cron 每小時 snapshot 暫代。

- **問題描述**：SPOF 表中 Usopp worker 的緩解（claimed_at +15 min timeout reset）標為「future cron」，但 Phase 1 DoD 未包含實作，導致卡在 claimed 狀態的 draft 無法自動回 approved。  
  **嚴重度**：High。  
  **建議修法**：DoD 加入簡單 cron job 實作 timeout reset，或 Phase 1 要求多 worker 部署以分散風險。

- **問題描述**：Slack 通知 Phase 1 靠 polling + source_agent 負責發 DM，但未定義通知失敗重試機制，若 Brook 入隊後 Slack API 斷，修修可能錯過 draft。  
  **嚴重度**：Medium。  
  **建議修法**：在 enqueue() 內加 retry 邏輯（援引 principles/reliability.md），或 Phase 1 DoD 加通知送達率測試。

- **問題描述**：diff_target_id 的 snapshot 語義在 §1 提及但 §8 說「enqueue 當下從 WP 拉一份存入 payload」，未說明如何儲存（payload JSON 內？獨立欄位？），可能導致 diff 漂移。  
  **嚴重度**：Low。  
  **建議修法**：§8 補充 snapshot 儲存細節（如 payload['original_content']），並加 DoD 測試。

### 3. 修訂品質評估

| 維度 | 1-10 分 | 一句話評語 |
|---|---|---|
| Schema / 契約完整度 | 9 | Pydantic 定義精準，版本與歧視聯合一應俱全，僅缺未來 action_type 擴充範例。 |
| Reliability 機制（idempotency / atomic / SPOF）| 8 | Atomic claim 與 FSM 強健，SPOF 表詳盡，但 litestream 延遲與 timeout cron 未實作削弱 Phase 1 可靠性。 |
| Observability（log / metric / SLO / probe）| 9 | 結構化 log、metrics endpoint、SLO 定義齊全，Nami 整合到位，僅缺外部 probe 實作。 |
| 可實作性（工程師照寫能不能動） | 9 | 代碼片段與 DoD 清單具體，援引原則文件一致，唯 litestream Phase 2 需補 migration 步驟。 |
| 範圍聚焦度（沒再 scope creep） | 10 | 嚴格切掉 Obsidian 到 ADR-006b，Phase 1 骨架純淨，Consequences 明確列不做事項。 |

### 4. 最終判定

- **go / no-go**：go（工程師可以開始寫 code）。  
- **如果 go**：Phase 1 實作過程中要特別盯的 1-2 個風險：(1) litestream 缺席導致的 RPO 漏洞（建議實作前補上或用每小時 snapshot 暫代）；(2) claimed 狀態 timeout reset 未實作，可能造成 draft 卡死（DoD 需加 cron）。