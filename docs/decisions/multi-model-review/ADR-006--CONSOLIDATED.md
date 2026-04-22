---
source_adr: ADR-006-hitl-approval-queue
consolidation_date: 2026-04-22
reviewer_models: [claude-sonnet-4-6, gemini-2.5-pro, grok-4]
---

# ADR-006 Multi-Model Review — Consolidated

## 1. 三家可行性評分對照

| 模型 | 分數 | 建議 |
|---|---|---|
| Claude Sonnet 4.6 | 5/10 | 修改後通過 |
| Gemini 2.5 Pro | 3/10 | 退回重寫 |
| Grok 4 | 5/10 | 修改後通過 |

**共識**：三家全都認為方向正確但設計有根本缺陷；Gemini 最嚴厲（退回重寫），Claude / Grok 認為骨架可用但必須補齊才能開工。

---

## 2. 三家共識（一致指出的問題）

### 2.1 Obsidian 整合的傳輸機制根本沒解決（3/3 共識，最嚴重）
- **誰提出**：Claude、Gemini、Grok
- **問題**：`payload_source='obsidian://path/to/file.md'` 與 `POST /bridge/drafts/obsidian-reimport/{id}` 假設 Linux VPS 上的 Bridge 能直接讀寫修修 Windows 本機 `F:\Shosho LifeOS\Drafts\`，底層同步機制完全沒定義。ADR 把這當 Open Question，但其實是架構決定。
- **證據**：
  - Claude：「這是 Windows 本地路徑。Bridge 跑在 Vultr VPS（Linux）。這兩個東西怎麼通？……這其實是個架構決定，不是偏好問題。」
  - Gemini：「這是一個極其脆弱的假設……同步機制的延遲、衝突、失敗都會直接破壞此工作流程。」「必須移除。」
  - Grok：「若vault移到雲端或路徑變更，export/reimport會斷。」
- **修修該怎麼辦**：開工前二選一
  1. **移出 Phase 1**：Obsidian 整合獨立成 ADR-006b，Phase 1 只做 Bridge inline edit（Gemini、Claude 建議）
  2. **決定傳輸方案**：若要留，先選定（Obsidian Local REST API plugin / Advanced URI plugin / Syncthing），並把 API、authz、conflict detection 寫進 ADR

### 2.2 `peek_approved` 有 race condition，需改為原子 claim（3/3 共識）
- **誰提出**：Claude、Gemini、Grok
- **問題**：多 agent daemon（甚至同一 agent 多 worker）同時呼叫 `peek_approved` 會拿到同一批 draft，重複執行。SQLite 沒有 `SELECT FOR UPDATE`，需在同一 transaction 內做 atomic SELECT+UPDATE。
- **證據**：
  - Claude：「`peek_approved` 缺少 FOR UPDATE 語意……正確寫法是在同一個 transaction 內做 SELECT + UPDATE，但 ADR 沒有說明這個約束。」
  - Gemini：「此 API 設計有嚴重的競爭條件漏洞……正確的契約應該是 `claim_approved_drafts`，這個方法在資料庫層面必須是原子的。」
  - Grok：「daemon peek邏輯無鎖（多daemon同時peek同id，造成重複execute）。」
- **修修該怎麼辦**：把 `peek_approved` 改名 `claim_approved_drafts`，用 `BEGIN IMMEDIATE` + `UPDATE status='approved' → 'executing' RETURNING *` 一次原子完成。並在 Phase 1 DoD 裡加「多 worker 並行測試不得出現重複 execute」這條測試。

### 2.3 SQLite 並發與備份保護不足（3/3 共識）
- **誰提出**：Claude、Gemini、Grok
- **問題**：多 web process + 多 agent daemon 同時讀寫 `state.db`，預設非 WAL 會 `database is locked`。R2 備份不是 RPO 解，兩次備份之間的 approved/pending 若遇 DB 崩潰就全丟。
- **證據**：
  - Claude：「SQLite 預設不是 WAL mode，兩個 process 同時操作很容易出現 `database is locked` 錯誤……RPO 沒有定義。」
  - Gemini：「SQLite 的 table-level locking 可能成為效能瓶頸……R2 備份是災難恢復，通常有時間差。」
  - Grok：「需加WAL模式與即時replication……生產上線有洩漏風險。」
- **修修該怎麼辦**：Phase 1 必須明確啟用 WAL + `busy_timeout` + `synchronous=NORMAL`；定義 RPO（例如 <5 分鐘），用 `litestream` 做連續 replication 到 R2，不靠 cron snapshot。

### 2.4 `payload` JSON blob 無 schema、無版本、難查詢（3/3 共識）
- **誰提出**：Claude、Gemini、Grok
- **問題**：`payload TEXT NOT NULL` 是一個無 schema、無 version 的 JSON blob，每個 agent 塞什麼都可以；UI 的 `title snippet` 無法穩定擷取；schema 改了無遷移路徑；結構化分析全都做不了。
- **證據**：
  - Claude：「`payload` 無版本號，schema 升級無路徑……不同 agent 的 payload 可能有 `title`、`post_title`、`subject`、`caption`。」
  - Gemini：「應該將 `payload` 中可結構化的部分拆分成獨立欄位，只將正文等非結構化部分存為 JSON。」
  - Grok：「peek_approved的Draft型別未定義……需指定dataclass否則payload解析失敗。」
- **修修該怎麼辦**：Phase 1 前先用 Pydantic 定義 `DraftPayloadV1`（每個 action type 一個 subclass），DB 增加 `payload_version INT NOT NULL`、`action_type TEXT NOT NULL`，把 `title`、`slug` 拉成獨立欄位供 UI 列表與查詢。

### 2.5 `status` 欄位沒有 state machine 保護（2/3 共識）
- **誰提出**：Claude、Gemini
- **問題**：status 轉換完全靠 application logic，沒有 CHECK constraint 也沒有 FSM guard，任何 bug 都能把 `executed` 改回 `pending` 重發布一次。對公開受眾是真實傷害。
- **證據**：
  - Claude：「沒有 CHECK constraint，沒有 transition guard。一個 bug 就能讓已發布的文章再次被執行發布一次。」
  - Gemini：「資料庫層面沒有任何約束來防止非法狀態轉移。」
- **修修該怎麼辦**：DB 加 `CHECK (status IN (...))`；application 層寫 `transition(from, to)` 白名單 dict，所有狀態變動必須走這個 function，禁止直 UPDATE。

### 2.6 可觀測性完全缺席（3/3 共識）
- **誰提出**：Claude、Gemini、Grok
- **問題**：ADR 只寫「Franky 監控」但沒定義監控什麼。沒有 queue depth / pending 等待 SLO / approval rate / failure rate / 錯誤 aggregation。
- **證據**：
  - Claude：「沒有定義 pending 時間的 SLO……`mark_failed` 的 error 存哪都不確定。」
  - Gemini：「佇列深度……平均等待審批時間……這些指標應接入 Prometheus/Grafana。」
  - Grok：「無central logging如ELK，難追蹤跨agent錯誤。」
- **修修該怎麼辦**：Phase 1 DoD 加入最小可觀測：結構化 log（draft_id、status transition、agent）+ 一個 `/bridge/metrics` endpoint 吐 queue depth / 各 status count / 24h p95 pending 時間，Nami 早報從這裡抓。

### 2.7 可測試性 / 資料庫遷移策略缺席（2/3 共識）
- **誰提出**：Claude、Gemini（Grok 提「無unit test範例」算半票）
- **問題**：schema 直接 `CREATE TABLE` 沒遷移框架（Alembic），未來加欄位手動 ALTER 會痛；ApprovalQueue class 有 DB 依賴但沒 test 策略。
- **修修該怎麼辦**：Phase 1 引入 Alembic（或 `yoyo-migrations`），schema 變更一律走 migration；`ApprovalQueue` 測試用 in-memory SQLite fixture，CI 跑並發 claim race test。

---

## 3. 各家 unique 觀點

### Claude Sonnet 獨到看法
- **Pitfall 6：`diff_target_id` 的 snapshot 語義未定**—diff view 要比較舊 post vs 新 draft，舊 post 要即時 WP API 拉還是 enqueue 時 snapshot？ADR 沒說。
- **失敗自動轉 `rejected` 會污染 audit log 語義**—`rejected` 在 ADR 定義是「修修主動拒絕」，系統失敗寫進去會讓 `reviewed_at`/`reviewer` 出現 NULL 但 status=rejected 的矛盾。應新增獨立 `failed_permanent` 狀態。
- **draft 過期機制缺失**—時事文週一產、週五 approve 時已過期，沒有 `expires_at` 也沒有「等了 N 天」警告。
- **運維無 manual fallback**—Bridge UI 掛了修修完全無法 approve，至少該有 CLI `python manage.py approve-draft {id}`。
- **法規分類**—健康內容有《食安法》與 NCC 風險，`review_note` 是 free text 無法結構化分析 reject 原因。

### Gemini 獨到看法
- **資料清理策略缺失**—`approval_queue` 表會無限增長，`executed`/`rejected` 舊紀錄沒歸檔/刪除策略，幾個月後索引效率下降。
- **建議用 PostgreSQL 取代 SQLite**—Docker 跑 PG 資源佔用不多，有 row-level lock、JSONB 可索引、Alembic 成熟遷移工具，直接解決鎖定與 payload 查詢問題。
- **`edit` 與 `approve` endpoint 職責混淆**—若兩者都能改 payload，語義不清。應明確拆 `PUT /bridge/drafts/{id}/content` 負責改內容、`POST /bridge/drafts/{id}/approve` 只改狀態。
- **二進位 payload 問題**—未來 Chopper 梗圖、Usopp 特色圖不能塞 `payload TEXT`，需要 R2 外部儲存 + reference，ADR 完全沒前瞻。

### Grok 獨到看法
- **payload 無加密的資安/個資風險**—payload 含讀者資料（社群回覆）直接明文存 SQLite，VPS 被入侵全暴露，台灣個資法可能違規。Claude/Gemini 都沒強調這點。
- **第三方 queue UI 替代方案**—Airtable / Notion / Trello 作為 queue UI 取代自建 Bridge，支援表格+通知+審批流程，免寫 API 且有 SLA，VPS 不穩時更穩。這是 Claude/Gemini 都沒提的成本角度。
- **FastAPI body 4KB 預設上限**—UI inline edit POST 若 payload JSON 過大，FastAPI 預設 body limit 會拒絕，需明確擴充。
- **VPS I/O 吃 NVMe**—若圖片 base64 嵌入或 queue 積壓千篇，SQLite 膨脹會吃光 128GB，導致 VPS I/O 瓶頸。

---

## 4. 三家不同意的點

### CSRF / 資安嚴重度
- **Claude**：低估了，HMAC cookie 不足以防 CSRF，「不是高估，是真實風險但描述太輕」。
- **Gemini**：高估了，單一使用者內部工具，不是 Phase 1 首要威脅，「投入過多精力不如先解決資料一致性」。
- **Grok**：高估了，VPS 低流量僅修修使用，外部暴露風險低。但 Grok 另外強調 **payload 加密** 是缺失。
- **仲裁**：Gemini/Grok 對 CSRF 的判斷合理（內部工具、單一使用者），但 Grok 的 **payload 明文** 是獨立新問題，應該處理。CSRF 可延後，payload 敏感欄位加密放 Phase 2。

### 整體分數與是否退回重寫
- **Claude/Grok 5/10 修改後通過** vs **Gemini 3/10 退回重寫**。
- Gemini 嚴厲的原因是 race condition + Obsidian sync 被視為「根本性缺陷」，Claude/Grok 認為骨架可用。
- **仲裁**：介於兩者之間。骨架（SQLite queue + HITL gate + status 欄位）確實可用，但 claim 原子性、Obsidian 傳輸機制、payload schema 三個 blocker 開工前不補齊會立刻崩。Claude 的「修改後通過但要補這些清單」是最務實的落地方式。

### 替代方案：要不要引入 Redis / Celery / PostgreSQL
- **Claude**：APScheduler with SQLite job store 最輕，避免 Redis。
- **Gemini**：PostgreSQL + Redis RQ 是基礎設施不是可選項。
- **Grok**：Redis in-memory queue 取代 SQLite、或直接 Airtable。
- **仲裁**：4GB VPS 下 Redis/Celery 額外資源不划算，Claude 的輕方案 + Gemini 指出的「atomic claim 寫 SQL」已足夠。保持 SQLite，但補 WAL + litestream + atomic claim + Alembic。

---

## 5. 最 blocking 的問題（合併版）

**按開工前必解的優先順序**：

1. **Payload schema 必須先定義（Pydantic）** — Claude/Gemini/Grok 都點名。沒有這個，Brook 和 Usopp 的 payload 會在實作中 drift，Bridge UI title/diff/edit 全會崩。
2. **`peek_approved` 改原子 `claim_approved_drafts`** — 3 家共識。多 worker 重複 execute 是正確性問題不是優化。
3. **Obsidian 整合：移出 Phase 1 或決定傳輸機制** — 3 家共識，現在是「UI 設計完但底層不存在」的幻覺。
4. **SQLite WAL + litestream replication + 定義 RPO** — 3 家共識，取代目前「R2 備份 cover」的誤導說法。
5. **Status FSM 保護**（CHECK constraint + transition whitelist） — 2 家共識，防重複發布。

---

## 6. 合併建議：Phase 1 開工前必做清單

**Schema / 資料層**
- [ ] 新增欄位：`payload_version INT NOT NULL`、`action_type TEXT NOT NULL`、`retry_count INT DEFAULT 0`、`error_log TEXT`、`title_snippet TEXT`（從 payload 拉出供 UI 查詢）
- [ ] `status` 加 `CHECK (status IN ('pending','approved','executing','executed','rejected','failed_permanent'))`，新增 `executing` 中間狀態與 `failed_permanent` 取代 Claude 指出的 rejected 語義污染
- [ ] SQLite 啟 WAL + `busy_timeout=5000` + `synchronous=NORMAL`
- [ ] 引入 Alembic（或 yoyo），禁止手動 ALTER
- [ ] litestream 連續 replication 到 R2，RPO < 5 分鐘

**API / 邏輯層**
- [ ] Pydantic `DraftPayloadV1` 定義每個 action_type 的 schema（Phase 1 只需 `publish_wp_post`）
- [ ] `peek_approved` 改名 `claim_approved_drafts`，用 `BEGIN IMMEDIATE` + `UPDATE ... RETURNING` 原子認領
- [ ] Python `transition(draft_id, from_status, to_status)` 封裝所有 status 變動，禁止直 UPDATE
- [ ] `POST /bridge/drafts/{id}/approve` vs `PUT /bridge/drafts/{id}/content` 職責拆清（採 Gemini 建議）
- [ ] CLI fallback：`python -m shared.approval_queue approve {id}` 供 Bridge UI 掛時用

**Obsidian（二選一）**
- [ ] 方案 A：Phase 1 完全不做 Obsidian 整合，拆成 ADR-006b，UI 先只支援 inline edit
- [ ] 方案 B：決定用 Obsidian Local REST API plugin / Advanced URI，寫進 ADR 並加 conflict detection

**可觀測性**
- [ ] 結構化 log：每次 status transition 記 `draft_id / agent / from / to / actor / ts`
- [ ] `/bridge/metrics`：queue depth by status、24h p95 pending 時間、failure count
- [ ] Nami 早報抓 `/bridge/metrics` 顯示

**測試**
- [ ] in-memory SQLite fixture
- [ ] 並發 claim race test（起 5 個 thread 同時 claim，驗證無重複）
- [ ] state machine 非法轉換拒絕測試

**延後 Phase 2+**
- 鍵盤快捷鍵、diff view、`review_note` 結構化分類
- payload 敏感欄位加密（Grok 提）
- 資料歸檔策略（Gemini 提）
- 審批級別配置化
- 圖片/二進位 payload 外接 R2

---

## 7. 修修下一步

**把 ADR-006 改成「骨架版 v1 + ADR-006b Obsidian 整合」兩份文件，Phase 1 只做骨架版**，理由：三家都認為 Obsidian 傳輸機制是最脆弱的一環且是獨立架構決定，把它從 Phase 1 切掉後剩下的 SQLite queue + HITL gate 是可執行的 MVP，上述 Schema/API/可觀測性清單照補即可落地；Obsidian 整合另開 ADR 充分調研 Local REST API / Advanced URI 再回來接。

---

**產出檔案**：`f:\nakama\docs\decisions\multi-model-review\ADR-006--CONSOLIDATED.md`
