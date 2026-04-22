---
source_adr: ADR-006-hitl-approval-queue
reviewer_model: grok-4
elapsed_seconds: 59
review_date: 2026-04-22
---

# ADR-006-hitl-approval-queue — grok 審查

### 1. 核心假設檢驗
這份ADR基於多個未明說的假設，包括：(1) VPS單機環境（2vCPU/4GB RAM）足以處理所有agent的enqueue/execute負載，無需分佈式設計；(2) SQLite作為單檔資料庫，能可靠處理並發寫入（多agent daemon同時peek/mark），忽略多進程鎖定衝突；(3) Obsidian vault總是可存取（本地F:磁碟固定存在，無雲端同步問題）；(4) 修修作為唯一reviewer，無需考慮多使用者並發審批；(5) 所有payload均為JSON可序列化，無二進位附件（如圖片）。  
這些假設容易出錯的包括：SQLite並發假設（VPS資源低，agent daemon若同時執行，可能鎖定延遲或死鎖）；Obsidian整合假設（若vault移到雲端或路徑變更，export/reimport會斷）；單機VPS假設（無高可用，機器重啟可能中斷queue處理）。

### 2. 風險分析
- (a) 未被提及但會造成生產問題的風險：資料隱私洩漏（payload含敏感內容如社群回覆，直接存SQLite無加密，若VPS被入侵全暴露）；Obsidian export的安全性（寫本地.md檔，若修修機器中毒，draft外洩）；依賴單一Bridge UI（若UI down，修修無法審批，導致queue積壓）；法規風險（台灣個資法，若payload含讀者資料，未加密儲存可能違規）。
- (b) 已提及但嚴重度被低估的風險：SQLite單點崩潰（僅提R2備份，但恢復時間窗內queue丟失會中斷發布，低估了業務連續性影響）；長文payload儲存（預估5MB，但若圖片base64嵌入或queue積壓到千篇，SQLite膨脹會吃光128GB NVMe，導致VPS I/O瓶頸）。
- (c) 已提及但嚴重度被高估的風險：Bridge UI的CSRF/brute force（ADR提HMAC cookie + API key，但VPS低流量、僅修修使用，高估了攻擊表面；實際上內部工具，外部暴露風險低）。

### 3. 替代方案
有更簡單的替代路徑，如使用Redis作為in-memory queue（取代SQLite），僅存metadata，payload丟S3/R2，減少單檔資料庫負擔；或直接用Celery/RQ task queue，內建retry/priority，agent daemon變成worker。作者沒選可能是想保持全自建（無外部依賴），但這忽略了VPS資源限制，Redis更輕量且proven在低端機上。  
更穩的第三方工具包括：Airtable或Notion作為queue UI（取代自建Bridge，支援表格/通知/審批流程，免寫API）；Trello/Kanban board整合Slack webhook（簡單drag-drop approve，proven在內容團隊）。這些能取代自建queue，理由是作者偏好自控（Obsidian整合），但第三方更穩（有SLA），尤其VPS不穩時。

### 4. 實作 pitfalls
工程師照此寫，最容易踩坑包括：(1) SQLite schema缺少updated_at的自動trigger（僅DEFAULT CURRENT_TIMESTAMP，update時需手動SET，否則audit不準；檔名state.db）；(2) API契約不嚴謹，如enqueue返回int (id)，但若SQLite AUTOINCREMENT overflow（極端下），返回型別崩；peek_approved的Draft型別未定義（shared/approval_queue.py，需指定dataclass否則payload解析失敗）；(3) Obsidian reimport邏輯（POST /bridge/drafts/obsidian-reimport/{id}），若vault檔名變更或多版本，payload_source的obsidian://path解析會錯，導致import空檔；(4) daemon peek邏輯無鎖（多daemon同時peek同id，造成重複execute）；(5) UI inline edit的POST /bridge/drafts/{id}/edit，若payload JSON過大（>4KB），FastAPI預設body limit會拒絕，無提及擴充。

### 5. 缺失的視角
資安缺失嚴重（payload無加密，API無rate limit細節，Obsidian export無驗證本地路徑安全）；效能講得太輕（SQLite在2vCPU/4GB VPS上，queue>500項時查詢慢，無提及index優化或分頁）；運維缺失（無自動備份rotation，R2僅提但無cron細節；無健康檢查daemon若hang）；法規沒講（台灣健康內容若含醫療建議，審批需記錄合規，但無欄位）；可觀測性輕忽（audit log僅表內，無central logging如ELK，難追蹤跨agent錯誤）；成本沒提（VPS固定費，若queue膨脹吃資源，無auto-scale）；可測試性缺失（無unit test範例，如enqueue/mark_executed的edge case）；可維護性輕（schema無migration工具，未來加欄位需手動ALTER）。

### 6. Phase 拆分建議
Obsidian整合（export/reimport）應拆成獨立ADR（太特定，需單獨討論vault sync風險）；審批級別表（per agent可調）可拆成配置ADR，與未來擴充綁定。  
延後到Phase 2+：UI keyboard shortcuts與diff view（非核心，先做基本table）；通知整合（Nami Brief，可後加）。  
必須Phase 1完成：資料模型與核心API（enqueue/peek/mark），audit log（reviewer等欄位），錯誤處理（retry/failed邏輯），否則queue無法運作。

### 7. 結論
- (a) 整體可行性評分 5/10（概念好但過度依賴單機SQLite與Obsidian，忽略VPS限制，易在規模化崩潰）。
- (b) 建議：修改後通過
- (c) 最blocking的1-2個問題：(1) SQLite並發與備份不足，需加WAL模式與即時replication；(2) 資安缺失（payload加密），否則生產上線有洩漏風險。