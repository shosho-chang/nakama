---
source_adr: ADR-006-hitl-approval-queue
reviewer_model: claude-sonnet-4-6
elapsed_seconds: 120
review_date: 2026-04-22
---

# ADR-006-hitl-approval-queue — claude-sonnet 審查

# ADR-006 審查報告

---

## 1. 核心假設檢驗

**假設一：修修每天早上會主動打開 Bridge 看 queue**
這是整個審批流程的人肉 SLA。ADR 沒有定義「pending 超過多久」算是問題，也沒有 escalation 路徑。若修修出遊三天，所有 draft 卡在 pending，後續 agent 的行為是？content calendar 是否有時效性？完全沒說。

**假設二：SQLite 的 WAL mode 已開啟，或不需要**
ADR 說「agent daemon 撿起 approved」。這意味著有一個 web process（Bridge/FastAPI）在寫 status，同時有 agent daemon 在讀並更新 status。SQLite 預設不是 WAL mode，兩個 process 同時操作很容易出現 `database is locked` 錯誤。ADR 完全沒提 SQLite 的 concurrency 設定。

**假設三：`payload` 裡的 JSON 格式是全系統共識**
ADR 說 payload 是「JSON: full draft object」，但沒有定義 schema。Brook 塞進去的 payload 結構和 Chopper 塞進去的能共用同一個 UI render 邏輯嗎？`title snippet` 要從哪個 JSON key 撈？ADR 假設這個問題不存在。

**假設四：Obsidian vault 路徑 `F:\` 永遠可達**
這個路徑是 Windows 本地路徑。Bridge 跑在 Vultr VPS（Linux）。這兩個東西怎麼通？是透過 SMB mount？還是 Obsidian Sync？還是本機 client 在跑某個同步腳本？ADR 把這個當 Open Question，但這其實是個架構決定，不是偏好問題。

**假設五：`peek_approved` 不會發生 race condition**
兩個 agent（例如未來 Usopp 和 Sanji）同時 `peek_approved` + 撿同一筆 draft 的情況，ADR 沒處理。雖然目前 agent 可能跑在同一個 process，但「agent daemon」的複數隱含這個風險。

**假設六：失敗 3 次自動轉 `rejected` 是合理語義**
`rejected` 在 ADR 的定義是「修修主動拒絕」。系統失敗自動寫成 `rejected` 會汙染 audit log 語義，也會讓 `reviewed_at` 和 `reviewer` 欄位出現 NULL 但 status=rejected 的矛盾狀態。

---

## 2. 風險分析

### (a) 未被提及但會造成生產問題的風險

**R1：`payload` 無版本號，schema 升級無路徑**
`payload TEXT NOT NULL` 是一個 JSON blob，沒有 `payload_version` 欄位。六個月後 Brook 改變了 draft 的 JSON 結構，舊的 pending draft 用新 UI render 會靜默爛掉（key missing，UI 顯示空白或 crash）。沒有 migration 策略。

**R2：`status` 欄位沒有 state machine 保護**
任何有 DB 寫入權限的 code 都可以把 `status=executed` 的 draft 改回 `pending`。沒有 CHECK constraint，沒有 transition guard。一個 bug 就能讓已發布的文章再次被執行發布一次。這是重複發布的風險，對公開受眾而言是真實傷害。

**R3：`POST /bridge/drafts/{id}/approve` 直接改 payload 後 execute，沒有 re-validation**
API 設計說 approve 可以「optional edit payload」。這意味著 approve 這個 endpoint 同時做了兩件事：修改內容 + 觸發批准。若 edit 後的 payload 格式破壞（e.g., 修修手動改了 JSON 刪掉逗號），agent 執行時才會炸，而不是在 approve 當下 fail fast。

**R4：Obsidian reimport 沒有 conflict detection**
流程：export → 修修在 Obsidian 改 → 另一個人（或修修自己）在 Bridge inline edit → reimport。reimport 會直接覆蓋 inline 的修改，沒有任何警告。雖然現在「只有一個 reviewer」，但就算是同一個人，在不同裝置上的操作也可能衝突。

**R5：`execution_result` 存 JSON blob，但 agent 執行失敗時的 `error` 存在哪？**
`mark_failed(draft_id, error: str)` 的 error 要寫進哪個欄位？schema 裡沒有 `error_log` 欄位，`execution_result` 只說是「成功時的 return payload」。失敗訊息要存哪？

**R6：沒有 draft 過期機制**
一篇關於特定時事的文章被 Brook 在週一生成，修修週五才看到並 approve，Usopp 發布出去。這篇文章的時效性可能已過。ADR 沒有 `expires_at` 欄位，也沒有任何機制警告「這個 draft 已經等了 N 天」。

---

### (b) 已提及但嚴重度被低估的風險

**`state.db` 崩潰**
ADR 說「被 R2 備份 cover」就帶過了。問題在於：備份頻率是多少？最後一次備份到崩潰之間的 pending drafts 會全部消失。若修修已經 approve 但 daemon 尚未 execute，這筆記錄若丟失，修修不會知道她批准過，但文章也沒發出去。這不是「備份能 cover」的問題，是 RPO 沒有定義的問題。

**CSRF 風險**
ADR 說「用現有 HMAC cookie + API key」就算處理了。但 POST endpoints（approve/reject/edit）如果沒有明確的 CSRF token 機制，HMAC cookie 本身不足以防護 CSRF。這不是「高估」，是真實風險但描述太輕描。

---

### (c) 已提及但嚴重度被高估的風險

**「長文 payload 50KB × 100 篇 = 5MB」**
這個計算太保守，也太悲觀。SQLite 處理數 GB 完全沒問題，5MB 連監控都不需要。這個風險根本不用寫進 ADR。真正的問題不是大小，是前面說的 schema 版本控制，這個被忽略了。

---

## 3. 替代方案

**替代方案一：用現有 task queue（Celery + Redis 或 APScheduler）取代自建 daemon 輪詢**
ADR 設計的模式是 agent daemon 主動 `peek_approved` 輪詢 SQLite。這是在重新發明 task queue。Celery 或 ARQ（async Redis queue）可以直接處理「approve 後觸發 execute」的語意，有 retry、dead letter queue、worker concurrency 保護都是內建的。作者沒選可能是因為要避免引入 Redis 依賴（4GB RAM 的機器上 Redis 是合理的），但 APScheduler with job stores 就能跑在 SQLite 上，也不用自己寫輪詢邏輯。

**替代方案二：approve 直接同步執行，而不是 daemon 撿**
目前架構：approve → 改 status → daemon 輪詢發現 → 執行。這中間有一個時間窗口，狀態是 `approved` 但尚未執行，這個中間狀態需要 daemon、需要 retry 邏輯、需要 failed 狀態。替代方案：approve endpoint 直接 call agent 的 execute function（或 dispatch 一個 background task via FastAPI `BackgroundTasks`）。approved 和 executed 可以合并，簡化 state machine。缺點是 approve request 的 response time 會包含 WP API 的延遲，但對 HITL 場景這完全可接受。

**替代方案三：Obsidian 整合用 Obsidian Local REST API plugin**
與其自己寫到 Windows 路徑（這個問題根本沒解決），直接用 [Obsidian Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api) plugin，Bridge 透過 HTTP call 到修修本機的 Obsidian。這是 proven 的方案，有明確的 API contract，不依賴路徑 mapping。作者沒選，可能不知道這個 plugin 存在，或認為太複雜。但這比「F:\\ 路徑怎麼從 Linux VPS 寫進去」要清晰得多。

---

## 4. 實作 Pitfalls

**Pitfall 1：`shared/approval_queue.py` 的 `peek_approved` 缺少 FOR UPDATE 語意**
SQLite 沒有 `SELECT FOR UPDATE`，但可以用 `BEGIN IMMEDIATE` transaction 保護。若工程師直接寫：
```python
SELECT * FROM approval_queue WHERE status='approved' LIMIT 10
```
然後在 Python 裡處理，再 UPDATE status，這中間有 race condition 窗口。正確寫法是在同一個 transaction 內做 SELECT + UPDATE，但 ADR 沒有說明這個約束，工程師照 API signature 實作很自然會寫出有問題的版本。

**Pitfall 2：`payload_source` 欄位和 `payload` 欄位的同步問題**
當 `payload_source = 'obsidian://path/to/file.md'` 時，`payload` 欄位存的是什麼？是 export 時的 snapshot？還是空的？reimport 之後 `payload` 更新但 `payload_source` 還指著那個路徑？如果修修 reimport 後又做了 inline edit，`payload_source` 還有效嗎？這個欄位的 truth source 語義是模糊的，工程師會用自己的方式詮釋，最後出現不一致的 payload 狀態。

**Pitfall 3：`POST /bridge/drafts/{id}/approve` 的 request body schema 未定義**
文件說「optional edit payload」，但沒有定義這個 endpoint 的 request body 長什麼樣。工程師可能寫成：
- `{"payload": {...}}` 整包替換
- `{"edits": {"title": "新標題"}}` patch 模式
- `{"approve": true, "payload": {...}}` 混在一起

這三種設計行為完全不同，但 ADR 沒有給 contract。前端和後端工程師（即使是同一個人）在不同時間點會做出不同決定。

**Pitfall 4：`title snippet` 來自哪裡？**
UI 說「table first：agent · platform · action · title snippet」。`title snippet` 是從 `payload` JSON 裡 parse 出來的。但 `payload` schema 未定義，不同 agent 的 payload 可能有 `title`、`post_title`、`subject`（newsletter）、`caption`（IG）。若工程師寫 `payload_json.get('title', '')` 就直接上，newsletter 的標題就會永遠是空的。需要一個 per-action 的 title extraction strategy。

**Pitfall 5：失敗三次自動轉 `rejected` 的計數器存在哪？**
Schema 裡沒有 `retry_count` 欄位。工程師要自己加，或是從 `agent_runs` 反查（但 `agent_runs` 加了 `approval_draft_id` 外鍵，這是可行的）。但 ADR 沒說清楚，工程師可能在 `execution_result` 裡塞 retry count，或在 Python 記憶體裡 track，restart 就歸零，永遠到不了 3 次。

**Pitfall 6：`diff_target_id` 的型別是 `TEXT` 但代表 WP post ID**
WP post ID 是整數。存成 TEXT 沒有直接問題，但若工程師在 diff view 用它去查 WP API，需要 cast。更大的問題是：diff view 要比較「舊 post 內容」vs「新 draft payload」，舊 post 的內容要即時從 WP API 拉，還是在 enqueue 時就 snapshot？ADR 完全沒說，工程師在實作 diff view 時會做出不同假設。

---

## 5. 缺失的視角

**可測試性：完全缺席**
`ApprovalQueue` class 有 DB 依賴，但 ADR 沒有說明如何 test。沒有提 in-memory SQLite fixture、沒有說明 agent daemon 的測試策略、也沒有提 Bridge API 的 integration test 應該怎麼跑。這個 system 是整個發布流程的關鍵 gate，沒有測試策略就上 production 是很大的風險。

**可觀測性：幾乎沒有**
ADR 說「Franky 監控」，但完全沒定義監控什麼。具體缺失：
- 沒有定義 pending 時間的 SLO（e.g., 「pending 超過 24hr 要 alert」）
- 沒有 dashboard metrics（queue depth by agent, approval rate, execution failure rate）
- `mark_failed` 的 error 存哪都不確定，Franky 怎麼 aggregate？

**資安：API key 管理完全沒說**
Bridge 說用「現有 HMAC cookie + API key」，但：
- API key 存在哪？`.env`？`state.db`？
- agent 呼叫 `enqueue` 時用什麼驗證身份？
- 若 agent 跑在同一台機器上，直接 import `ApprovalQueue` 就能 enqueue，完全沒有 auth。這是 design，但沒說清楚。若未來有 agent 跑在不同機器（或有人能在 VPS 上跑任意 code），任何 process 都能塞東西進 queue。

**法規 / 內容責任**
修修是健康領域內容創作者，台灣有《食品安全衛生管理法》和 NCC 相關規範。若 agent 產生的內容有健康聲稱（health claims）錯誤，HITL gate 是唯一防線。ADR 沒有說明 reject 的理由是否需要分類（法規問題 vs 文體問題 vs 事實錯誤），這對未來改善 agent prompt 有意義，但也有法規 audit 的實務意義。`review_note` 是 free text，無法結構化分析。

**成本：間接成本沒算**
ADR 說「Brook 產 draft 的 LLM cost 記在 `api_calls`」，但 rejected 的 draft 代表的 LLM 浪費沒有被追蹤。若 reject rate 很高（e.g., 修修常常 reject Brook 的文），這是 Brook prompt 需要改進的訊號，但目前沒有 reject 原因的結構化資料（`review_note` 是 free text），無法做 reject pattern analysis。

**運維：沒有 manual intervention 路徑**
若 Bridge UI 掛了，修修完全無法 approve 任何東西。ADR 沒有說明「Bridge 掛掉時的 fallback」是什麼。至少應該有一個 CLI 命令（`python manage.py approve-draft {id}`）讓有 SSH 的人可以手動操作。

---

## 6. Phase 拆分建議

**必須 Phase 1 完成（否則整個 HITL 不能運作）**
- SQLite schema（含補上 `retry_count`、`error_log`、`payload_version` 這幾個缺失欄位）
- `ApprovalQueue.enqueue` / `mark_executed` / `mark_failed`
- `GET /bridge/drafts` + `POST approve` + `POST reject`
- status state machine 的 CHECK constraint 或 application-level guard
- Agent daemon 的 `peek_approved` + execute 迴圈（含 SQLite WAL mode 設定）
- Payload schema 定義（至少 Usopp 的 `publish_post` 格式）

**應該拆成獨立 ADR**
- **Obsidian 整合**（ADR-006b）：`F:\` 路徑問題、Obsidian Local REST API vs 路徑 mount、conflict detection、export/reimport 的 truth source 語義——這整塊是一個獨立的架構決定，現在是 Open Question 但被當成 Phase 1 feature 在設計。
- **Agent Daemon 架構**（或在 ADR-005 裡補充）：daemon 怎麼跑、輪詢頻率、WAL mode、race condition 保護——這些是基礎設施決定，不應該藏在 HITL ADR 裡。

**可以延後到 Phase 2+**
- Keyboard shortcuts（A/R/E）
- Diff view（`diff_target_id`）：有用但不是 MVP
- `review_note` 結構化分類
- 多 agent 的 payload schema 統一（Phase 1 只需支援 Usopp 的格式）
- Chopper 的 per-phase 審批規則
- Retry UI（Bridge 上的「retry」按鈕）

---

## 7. 結論

### (a) 整體可行性評分：**5 / 10**

核心設計方向正確：SQLite queue + HITL gate 是合理且務實的選擇，不過度工程化。但有太多「留白」是工程師會踩進去的坑，不是「細節之後再說」的問題，而是會在實作時做出互相矛盾的決定，最後積累成難以 debug 的 inconsistency。Obsidian 整合架構完全沒解決，卻在 UI 設計裡當成已決定的功能在描述。

### (b) 建議：**修改後通過**

不需要退回重寫，骨架是可用的。但必須在開工前補上以下內容才能簽字。

### (c) 最 blocking 的問題

**Blocking 1：Payload schema 必須先定義**
在任何 agent 寫 `enqueue` 之前，必須有一份明確的 payload JSON schema（用 Pydantic model 或 JSON Schema 都行），且 Bridge UI 的 title extraction、inline edit 欄位、diff view 都依賴這個 schema。這不是 Open Question，這是 Day 1 的 API contract。沒有這個，Brook 和 Usopp 的 payload 格式會在實作中 drift，之後要 migrate 很痛。

**Blocking 2：Obsidian 整合的傳輸機制必須決定，或從 Phase 1 移除**
`F:\Shosho LifeOS\Drafts\` 是 Windows 本地路徑。Bridge 跑在 Linux VPS。這兩者之間的傳輸沒有解決方案，卻在 UI 設計裡有一個「Export to Obsidian」按鈕和「obsidian-reimport」endpoint。若這個功能 Phase 1 不做，就從 ADR-006 移除，另立 ADR-006b。若要做，必須先決定傳輸機制（Obsidian Local REST API / Syncthing / SMB mount / Obsidian Sync + polling），這個決定會影響 VPS 的網路設定、依賴項、安全邊界。現在的狀態是「設計了 UI 但底層不存在」，這會讓工程師在實作時自己發明一個未經審查的方案。