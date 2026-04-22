---
source_adr: ADR-005b-usopp-wp-publishing
verification_round: 2
reviewer_model: claude-sonnet-4-6
elapsed_seconds: 105
review_date: 2026-04-22
---

# ADR-005b-usopp-wp-publishing — 修訂驗證（claude-sonnet）

# ADR-005b 修訂版驗證報告

---

## 1. Blocker 逐項檢核

### Blocker 1｜Draft Object Schema + Gutenberg Validation 未定義

**原 blocker**：Brook / Usopp 核心 contract 不存在，且無 block markup validator，兩個 agent 無法並行開工。

**修訂版回應**：🔄 拆到別處

**證據**：ADR-005b Context 第一句明確寫「`DraftV1`（schema 見 ADR-005a）」，§9 Schema 的 `PublishRequestV1` 包裹 `draft: DraftV1` 並引用 ADR-005a。Gutenberg block validation 設計在 ADR-005a 範疇。

**評估**：拆法合理，ADR-005b 是消費者而非定義者，不應重複定義 `DraftV1`。但本 ADR 在 §4 Atomic Publish 中直接使用 `draft.content.raw_html`，而非 `DraftV1` 的 content 欄位型別——這個欄位名稱必須與 ADR-005a 中的 `DraftV1` schema 精確對齊。**若 ADR-005a 尚未通過 review，本 ADR 的上游依賴是懸空的，不能獨立開工。**

---

### Blocker 2｜無 Staging 環境 + 無測試策略

**原 blocker**：在生產 WP（192 篇既有內容）上試 API，一次錯誤 payload 就污染資料。

**修訂版回應**：✅ 完整解

**證據**：§8 測試策略完整定義三層測試（Unit with `responses` cassette、Integration 打 Docker staging WP、Smoke 5 篇 end-to-end）；明確要求 WP 6.9.4 + SEOPress 9.4.1 的 Docker 環境，複製 20 篇 subset；開工 Checklist 的「測試」區塊有 CI 禁連生產 WP 的 guard（`PYTEST_WP_BASE_URL` 必須非 shosho.tw）。這是本次修訂中做得最完整的一塊。

---

### Blocker 3｜狀態機 / idempotency / 原子性缺失

**原 blocker**：沒持久化 state 的 retry 會產生孤兒 post + 重複文章。

**修訂版回應**：✅ 完整解

**證據**：§1 定義完整 state machine（queued → claimed → media_ready → post_draft → seo_ready → validated → published → cache_purged → done），每步寫 DB 後才推進；§2 雙層 idempotency（`draft_id` UNIQUE index + WP 側 `nakama_draft_id` post meta）；§4 Atomic Publish 先建 draft、全驗證後才切 publish，crash 留 WP draft 不上線；Franky 每日 cron 掃孤兒 post 為安全網。三個 sub-problem 全部有設計。

---

### Blocker 4｜資安設計（auth + secret + 最小權限）未定義

**原 blocker**：application password 洩漏 = 全站 game over。

**修訂版回應**：✅ 完整解（有一個技術細節需確認）

**證據**：§7 Auth 專章完整覆蓋：自訂 `nakama_publisher` 角色（繼承 Editor，明確禁止 `edit_users` / `manage_options` / `install_plugins`）、`.env` 權限 0600、每 90 天輪換 + 緩衝期、HTTPS 強制、HMAC 作用域釐清（僅 agent 間，WP REST 不用）、log 遮罩、rate limit 1 req/sec。`wp_kses_post` 陷阱也被注意到並給出建議（AST 白名單 block 迴避）。

**待確認細節**：`nakama_publisher` role 的 capabilities 白名單在本 ADR 只以文字描述，未列出完整 capability list（例如 `publish_posts`、`upload_files`、`edit_published_posts` 等）。開工 Checklist 有「`nakama_publisher` 角色註冊 script」這個 action item，但 ADR 本身沒給 capability 完整清單，工程師實作時可能自行猜測而給多權限。建議在 §7 或附錄補上明確 capability 白名單。

---

### Blocker 5｜VPS 資源 benchmark 未做

**原 blocker**：4GB RAM 能不能扛 Brook + Usopp + Robin + MySQL 並發，沒實測就上 = 賭博。

**修訂版回應**：🔄 拆到別處

**證據**：Consequences §「Notes」末段明確寫「VPS benchmark（review §2.10）統一在 ADR-007 Franky 範疇處理，本 ADR 僅列為前置依賴」。

**評估**：拆法可接受，Franky 做資源監控確實是合理歸屬。但「僅列為前置依賴」這句話沒有落地——Blocker 連結（若 ADR-007 benchmark 顯示資源不足，ADR-005b 需要做什麼 contingency？）在 ADR-005b 中完全沒有說明。建議在 SPOF 表或 Consequences 中加一行：「若 ADR-007 benchmark 顯示 Usopp batch publish 與 Brook LLM 同時跑會超過 3.5GB，Phase 1 發布排程需強制序列化（不並發）」，使 contingency 有跡可循。

---

## 2. 新發現的問題

### P1｜`draft.content.raw_html` 欄位名稱與 ADR-005a 的耦合未保護
**嚴重度：High**

§4 Atomic Publish 的 code snippet 直接使用 `draft.content.raw_html`，但 ADR-005a 尚未在本報告範疇內通過 review，無法確認 `DraftV1.content` 的型別是 `str`（raw HTML）還是 JSON AST object。若 ADR-005a 採納 Consolidated Review 的「JSON AST + Python serializer」建議，`DraftV1.content` 就不再有 `.raw_html` 屬性，§4 的 code snippet 直接錯誤。本 ADR 在這個欄位上沒有任何保護或 `assert isinstance` 的型別防禦。

**建議修法**：在 §4 code snippet 中改為呼叫一個抽象方法 `draft.content.to_wp_html()` 或 `serializer.render(draft.content)`，將序列化策略的選擇封裝在 ADR-005a 負責的層，ADR-005b 不直接假設 content 的 internal format。並在 Interface 章節明確寫出 ADR-005a 需向 ADR-005b 承諾的 content 介面。

---

### P2｜`validated` 步驟的比對邏輯過於簡單，可能誤判成功
**嚴重度：High**

§4 的 Step 4c 寫「讀回來比對 content 一致」，但 code snippet 只比對 `fetched.meta["nakama_draft_id"] == draft.draft_id`，並非真正比對 content 一致性。兩件事是不同的：meta 存到了不代表 `post_content` 存到了（Claude 在上輪 review 明確指出 `content_html` 欄位名稱錯誤可能讓文章靜默空白）。此外，`wp_kses_post` 若對 content 做過濾，`fetched.content.rendered` 可能與送出的不同，比對 raw content 字串會永遠 fail。

**建議修法**：明確定義 validated 步驟的比對策略：至少驗證 (a) `fetched.meta["nakama_draft_id"]` 存在且正確、(b) `fetched.content.raw` 非空（長度 > 閾值，例如 100 字元）、(c) `fetched.status == "draft"`。不需要逐字元比對（因為 `wp_kses_post` 過濾），但要防止空白文章靜默過關。

---

### P3｜LiteSpeed Purge 的 auth 方案在 ADR 定稿前懸空，但 Checklist 把它當 done 處理
**嚴重度：Medium**

§5 描述了三種 LiteSpeed purge 方案（admin-ajax nonce / LiteSpeed API token / WP-CLI via SSH），但明確標示「第一週實測決定」（Open Question 1）。問題是開工 Checklist 的 Observability 區塊包含 `cache_purge_status` 這個 metric，以及 `PublishResultV1.cache_purged: bool`，這些都預設 purge 有實作。若第一週發現三種方案都不可行（例如 Docker 環境跑不起 LiteSpeed plugin），整個 cache purge 流程需要重設計，而 schema 已經 frozen（`extra="forbid"`）。

**建議修法**：在 `PublishResultV1` 的 `cache_purged` 欄位加 `cache_purge_status: Literal["purged", "skipped_plugin_unavailable", "skipped_endpoint_unknown", "failed"]` 替代純 bool，讓「不確定 endpoint」的情況有合法的 schema 表達；或在 Open Question 1 解決前，把 Checklist 中 cache purge 的實作 item 標為「blocked on Open Question 1」而非直接列為 TODO。

---

### P4｜`nakama_draft_id` post meta 的 WP 設定未定義
**嚴重度：Medium**

§2 的 idempotency 設計依賴 `GET /wp/v2/posts?meta_key=nakama_draft_id&meta_value=...` 查詢，但 WP REST API 預設**不允許按 custom meta 查詢 post**，除非 meta key 在 `register_post_meta()` 時設定 `show_in_rest: true` 且 `auth_callback` 允許。ADR 完全沒提到如何在 WP 側 register 這個 meta key，工程師照寫會發現 meta query 永遠回傳空（而不是 404），導致 idempotency 雙發保護靜默失效。

**建議修法**：在 §2 或開工 Checklist 加「WordPress 側需在 `functions.php` 或 custom plugin 中 `register_post_meta('post', 'nakama_draft_id', ['show_in_rest' => true, 'single' => true, 'type' => 'string', 'auth_callback' => '__return_true'])`」，並在 staging 的 integration test 中驗證 meta query 實際可用。

---

### P5｜Compliance guardrail（Tier B）在本 ADR 完全消失
**嚴重度：Medium**

Consolidated Review §2.11 明確標示健康內容法規 compliance 是「Day 1 的問題，不是 Phase 2」，並要求 HITL checklist 加 compliance checkbox。ADR-005b 在 Consequences 和開工 Checklist 中完全沒有提到 compliance guardrail 或 HITL checklist 的 compliance 欄位。理解 ADR-005b 範疇聚焦在 Usopp 發布機制，但 HITL approval（ADR-006 範疇）的 compliance checklist 至少應在本 ADR 的 Interface 章節中作為對 ADR-006 的要求被提出。

**建議修法**：在「跟 ADR-005a / ADR-006 的介面」章節加一行：「ADR-006 的 HITL approve 介面需包含 compliance checklist（本文不涉及療效聲稱 / 有附 disclaimer）作為 `reviewer` 核准的必要條件，未打勾不得進入 Usopp publish flow」。

---

### P6｜SLO 的「Publish 成功率 > 98%」缺分母定義
**嚴重度：Low**

§SLO 定義「Publish 成功率 > 98%」但沒有說分母是什麼：是所有進入 `queued` 狀態的 job？是所有非 DLQ 的 job？還是 retry 後的最終成功率？這在 observability 實作時會讓 `publish_jobs_in_state` gauge 無法正確計算 SLO burn rate。

**建議修法**：補一行「分母 = 進入 `claimed` 狀態的 job 總數（即排除 atomic claim 前的 schema validation failure）；分子 = 最終 state = `done` 的 job 數（包含從 DLQ 人工恢復後成功的）」。

---

## 3. 修訂品質評估

| 維度 | 分數 | 一句話評語 |
|---|---|---|
| Schema / 契約完整度 | 8 | `PublishRequestV1` / `PublishResultV1` 定義清晰，外部 schema anti-corruption layer 設計到位；扣分在 `raw_html` 欄位耦合 ADR-005a 未保護，以及 `nakama_draft_id` WP 側 register 缺失。 |
| Reliability 機制（idempotency / atomic / SPOF）| 9 | State machine、雙層 idempotency、先 draft 後 publish、Franky 孤兒掃描、DLQ、WAL + R2 備份，幾乎每個 reliability.md 原則都有對應設計；扣分在 `validated` 步驟比對邏輯的實際實作細節不夠嚴謹。 |
| Observability（log / metric / SLO / probe）| 8 | Structured log 欄位、histogram by step、counter by API + status_code、gauge by state、`/healthz` WP 連線檢查全部到位；扣分在 SLO 分母定義模糊，以及 LiteSpeed purge 狀態在 schema 只用 bool 表達不足。 |
| 可實作性（工程師照寫能不能動）| 7 | State machine、idempotency、retry config、auth 設計都有足夠的 code skeleton；三個扣分點：`nakama_draft_id` meta query 需要 WP 側 register 才能動（否則靜默失效）、`nakama_publisher` capability 清單未列、`validated` 步驟 code 有 bug（只比對 meta 沒比對 content）。 |
| 範圍聚焦度（沒再 scope creep）| 9 | 成功把 Gutenberg、FluentCRM、多平台 Protocol 抽象全部切走；VPS benchmark 指向 ADR-007；`shared/fluent_client.py` 不見了；相比原 ADR-005 範圍壓縮顯著。扣一分是因為 LiteSpeed 的三種實作方案還是留太多未決定項在 ADR 本體。 |

---

## 4. 最終判定

### ⚠️ Conditional Go

**結論**：工程師可以開始搭 Docker staging 環境和撰寫 `publish_jobs` table schema，但不能開始寫核心 publish 邏輯，直到以下兩項確認。

---

**必須先修的 Blocking 項目（2 項）**

**B1｜確認 ADR-005a 已通過 review，並對齊 `DraftV1.content` 介面**

ADR-005b 的整個 §4 Atomic Publish 依賴 `draft.content.raw_html`。若 ADR-005a 採 JSON AST 方案（review 建議方向），這個欄位不存在，§4 的 code skeleton 錯誤。兩個 ADR 必須對齊 content 欄位的型別契約後，Usopp 才能實作 `create_post` 呼叫。**這是唯一真正的開工 blocker。**

**B2｜補上 `nakama_draft_id` WP 側 meta registration 的實作說明**

沒有 `register_post_meta()` 的 `show_in_rest: true`，§2 idempotency 的 WP 側查詢（`GET /wp/v2/posts?meta_key=...`）靜默回傳空陣列，雙重防護變成單層防護，工程師不會知道這個 bug 直到出現重複文章。這個修改只需要在 §2 或 Checklist 加三行說明，但不修的風險是 idempotency 這個最重要的 blocker 修了半套。

---

**Go 後 Phase 1 實作中要特別盯的風險（2 項）**

**W1｜`validated` 步驟的靜默空白文章風險**

§4 Step 4c 的比對只驗 meta，沒驗 `post_content` 非空。在 staging 環境的 integration test 中，必須加一個 test case：故意送空字串 content，驗證 validated 步驟能 catch 到（而非通過）。若 `wp_kses_post` 行為導致 content 比對困難，至少要驗 `content.raw` 長度 > 100 字元。

**W2｜LiteSpeed purge 方案在第一週必須有實測結論**

Open Question 1 懸空會讓 `PublishResultV1.cache_purged` 的語意不確定，也讓 SLO「Cache purge 成功率 > 95%」無法量測。第一週實測若三種方案都有障礙，需要立刻更新 §5 和 `PublishResultV1` schema（把 bool 改為 Literal status），而不是等到 Phase 1 結束才發現 schema 需要 breaking change。