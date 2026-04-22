---
source_adr: ADR-005-publishing-infrastructure
consolidation_date: 2026-04-22
reviewer_models: [claude-sonnet-4-6, gemini-2.5-pro, grok-4]
---

# ADR-005 Multi-Model Review — Consolidated

## 1. 三家可行性評分對照

| 模型 | 分數 | 建議 |
|---|---|---|
| Claude Sonnet 4.6 | 4/10 | 退回修改（核心方向可保留，補齊 5 項才能開工） |
| Gemini 2.5 Pro | 3/10 | 退回重寫（玻璃大砲，缺穩定性/可觀測性/可維護性） |
| Grok 4 | 6/10 | 修改後通過（方向可行但風險低估、替代方案未探討） |

平均 **4.3/10**，三家一致判定「不可直接開工」，分歧在「修改 vs 重寫」的幅度。

---

## 2. 三家共識（至少 2 家同時指出的問題）

### 2.1 SEOPress / WP plugin API 契約脆弱 —— 高嚴重

- **誰提出**：全（Claude / Gemini / Grok）
- **問題**：`/wp-json/seopress/v1/posts/{id}` 屬實驗性 endpoint，歷來有無公告 breaking change；ADR 無 API contract 快照、無 fallback、無整合測試 stub。
- **證據**：
  - Claude：「歷史版本有多次無公告 breaking change…一旦 SEOPress 升版，Usopp 的 publish flow 靜默失敗，文章帶著空白 SEO meta 就上線。」
  - Gemini：「外掛 API 契約不變假設…一次更新就可能破壞 Usopp 的發布流程。」
  - Grok：「SEOPress API 若在 9.4.1 後變更，Usopp 的 publish flow 會直接失效。」
- **修修該怎麼辦**：
  1. 在 ADR 附 SEOPress 9.4.1 的 **exact payload + HTTP method 快照**（Claude 提示 POST/PUT 不同版本不同）。
  2. 增加 **fallback 方案**：直接寫 `_seopress_titles_title`、`_seopress_titles_desc` 等 post meta key，繞開 plugin REST 中間層（Claude 替代方案）。
  3. CI 加一個對 staging WP 的 integration smoke test，plugin 升版時立刻看到紅燈。

### 2.2 Gutenberg Block HTML 生成缺驗證層 —— 高嚴重

- **誰提出**：全（Claude / Gemini / Grok）
- **問題**：ADR 假設 Brook 能穩定輸出合法 block markup，但沒 validator、沒 schema。LLM 在長文、列表、引言、嵌套時極易出錯，WP 不會報錯直接存髒 HTML。
- **證據**：
  - Claude：「LLM 在長文章中極易在 block boundary 出錯…WP REST API 對格式錯誤的 block markup 不會報錯…前台 render 出來的是破碎的 HTML。」
  - Gemini：「必須要有嚴格的 HTML 清理和驗證層…Gutenberg 編輯器直接報錯『This block contains unexpected or invalid content』。」
  - Grok：「若 draft object 的 content_html 未驗證 Gutenberg 格式（例如缺少 `<!-- wp: -->`），POST 會 render 失敗。」
- **修修該怎麼辦**：
  1. **改 Brook 輸出結構化 JSON AST**（heading/paragraph/list/quote），由 `wordpress_client.py` 的 serializer 轉 block markup（Claude 替代方案，< 200 行 Python）。
  2. 若保留 LLM 直出 HTML，publish 前加 block syntax validator，failed 就 fail fast 回 Bridge 重試。

### 2.3 可觀測性 / 結構化日誌完全缺失 —— 高嚴重

- **誰提出**：全（Claude / Gemini / Grok）
- **問題**：整個 publish flow 4-5 個 API call 串起來，任一失敗無結構化 log 可 debug。「Franky 監控 RAM」不是替代品。
- **證據**：
  - Claude：「publish 成功/失敗應該有 structured log 含 post_id、slug、draft_id、每個 API call 的 latency 和 response code。」
  - Gemini：「日誌、監控、追蹤全缺失…一旦出問題根本無法追蹤。」
  - Grok：「Usopp error 未推到 Franky。」
- **修修該怎麼辦**：Phase 1 開工前定義 structured logging schema（含 trace_id、post_id、draft_id、step、latency_ms、http_status），另拆 ADR「Nakama Observability Strategy」。

### 2.4 缺測試策略 / 無 Staging 環境 —— 高嚴重

- **誰提出**：Claude + Gemini
- **問題**：ADR 沒說 WP REST 如何 mock、SEOPress 如何 stub；工程師只能在生產 WP（含 192 篇既有文章）上試 API，一次錯誤 payload 就污染資料。
- **證據**：
  - Claude：「沒有 WP test instance 或 recorded cassette，工程師在 CI 裡根本無法測試 Usopp。」
  - Gemini：「沒有 Staging 環境，直接在生產環境測試是災難。」
- **修修該怎麼辦**：Phase 1 第一件事 = 架 Docker WordPress staging 實例（複製 192 篇資料的 subset），CI 用 `responses` library 或 recorded cassette mock HTTP。

### 2.5 原子性 / idempotency / 狀態管理缺失 —— 高嚴重

- **誰提出**：Claude + Gemini + Grok
- **問題**：publish flow 「建 post → 寫 SEO → publish」非原子操作。中途 crash + retry 會造成重複文章或孤兒 post；沒 idempotency key 設計。
- **證據**：
  - Claude：「如果 Usopp 在 `POST /wp/v2/posts` 之後、`POST /seopress/v1/posts/{id}` 之前 crash，retry 會建立重複文章。」
  - Gemini：「產生大量『半成品』垃圾資料…必須被重新設計為具備持久化、可重試、冪等性的狀態機或任務佇列。」
  - Grok：「race condition…需處理 retry backoff，否則無限迴圈。」
- **修修該怎麼辦**：Usopp 用 `shared/approval_queue.py`（Grok 點名）或 persistent task queue，每個 draft 有 state machine（`pending → post_created → seo_written → published`），retry 時先查 state 而不是重跑。

### 2.6 資安缺失（auth、secret、rate limit） —— 中高嚴重

- **誰提出**：全（Claude / Gemini / Grok）
- **問題**：Application password 的存儲、輪換、最小權限全無規範；HMAC 用途模糊；REST endpoint 無 rate limit；VPS 被入侵即全失守。
- **證據**：
  - Claude：「HMAC 保護什麼…application password 的 WP user 應該有最小權限，但 ADR 沒有規定。」
  - Gemini：「依賴 Application Password 進行認證，若密碼洩漏，整個網站內容的控制權將暴露。」
  - Grok：「application password 若存 env var 未加密，VPS 被入侵即暴露。」
- **修修該怎麼辦**：ADR 加 auth 專章，指定 (a) WP user 角色 = Editor 或自訂最小權限角色（不用 Administrator）、(b) application password 存 `.env` + VPS 檔案權限 0600、(c) HMAC 的作用域（internal agent 間 vs WP REST）寫清楚。

### 2.7 LiteSpeed Cache 失效策略被輕忽 —— 中嚴重

- **誰提出**：Claude + Gemini
- **問題**：ADR Open Question 3「讓 LiteSpeed plugin 自己偵測」無驗證依據；REST 分步更新不一定 100% 觸發 cache purge。
- **證據**：
  - Claude：「Franky 監控在 publish 後立刻抓頁面做 SEO 驗證，很可能讀到 stale cache。」
  - Gemini：「不一定能 100% 觸發所有相關快取（頁面快取、對象快取、CDN 快取）的精準清除。」
- **修修該怎麼辦**：publish 成功後 Usopp 顯式呼叫 LiteSpeed purge API（或 WP hook），不依賴 plugin 自動偵測。

### 2.8 Tag 過多（497）的實際處理路徑不明 —— 中嚴重

- **誰提出**：全（Claude / Gemini / Grok）
- **問題**：ADR 說「不自動擴增」但沒定義如果 Brook 建議的 tag 不存在，Usopp 是 create 還是 skip。
- **證據**：
  - Claude：「497 個 tag 的 list 如果全部丟給 Brook 做 selection context，token 數量就爆了。」
  - Gemini：「會產生更多無意義、重複或錯誤的標籤，加速內容熵增。」
  - Grok：「Brook 可透過 Robin 過濾，影響有限。」（低估但同點議題）
- **修修該怎麼辦**：Phase 1 **完全禁止 Brook 新增 tag**（Gemini 建議），只從既有 497 個中選；Usopp 遇到不存在的 tag = skip + 寫 log 等修修手動加。另拆 ADR 做 tag cleanup governance。

### 2.9 LLM / 運維成本完全沒估 —— 中嚴重

- **誰提出**：Claude + Gemini + Grok
- **問題**：Brook 一篇文章 token 數、Robin digest 成本、plugin 授權費全無 estimate。
- **證據**：三家都獨立指出。
- **修修該怎麼辦**：ADR 加 cost 估算章節（依 `feedback_llm_cost_estimation.md` 含 output + thinking tokens），並定義 Phase 1 月度 LLM 預算上限。

### 2.10 VPS 4GB RAM + MySQL 共用的風險被低估 —— 中嚴重

- **誰提出**：Claude + Gemini + Grok
- **問題**：Brook LLM + Usopp batch + Robin PubMed + MySQL buffer pool 同時跑，OOM killer 會殺進程丟資料。「Franky 監控」只是 downstream，不能解決根本問題。
- **證據**：
  - Claude：「具體數字都不在 ADR 裡，『Franky 監控』不能解決根本問題，只能讓你知道什麼時候死。」
  - Gemini：「這是一個單點故障（SPOF）架構，風險極高。」
  - Grok：「MySQL OOM killer 會殺掉進程，導致資料遺失。」
- **修修該怎麼辦**：Phase 1 開工前實測 benchmark（Grok 點名：Brook + Usopp 並發負載下 RAM 曲線），根據結果決定是否升級 VPS 或加 swap / PHP-FPM worker tuning。

### 2.11 健康內容法規 compliance 缺席 —— 中嚴重

- **誰提出**：Claude + Grok
- **問題**：台灣《健康食品管理法》、醫療廣告規範對療效聲稱有限制；FluentCRM 蒐集 subscriber 也涉個資法。ADR 完全沉默。
- **證據**：
  - Claude：「這不是 Phase 2 的問題，是 Day 1 的問題。」
  - Grok：「若內容涉及健康建議，無 disclaimer 可能違反台灣醫事法。」
- **修修該怎麼辦**：Brook style profile 加 compliance guardrail（禁止療效聲稱的黑名單詞彙）；Bridge HITL 介面加 compliance checklist（「本文未聲稱療效 / 有附 disclaimer」修修必須打勾才能 approve）。

---

## 3. 各家 unique 觀點（只有一家提出但值得看的）

### Claude Sonnet 獨到看法

1. **`content_html` 變數名稱 vs WP REST `content` 欄位不對齊** —— 這類名稱錯誤會讓文章發成功但 `post_content` 空白，dev 環境若沒 end-to-end 驗證會存活很久。是具體 pitfall，工程師實作時必看。
2. **WP `wp_kses_post` 過濾器靜默清除非 admin 的 style/figure attribute** —— 如果用 Editor 最小權限 user（為了資安），Brook 產的自訂 HTML 會被無聲清掉。資安和功能互相咬的 trap。
3. **Draft Object Schema 是整個系統的核心 contract** —— Brook 和 Usopp 無此 schema 無法並行開發；`featured_media_id` 跨 Brook → Bridge → Usopp 的傳遞路徑完全沒定義。這個 blocker 另外兩家沒抓到這麼具體。

### Gemini 獨到看法

1. **Headless CMS + SSG（Astro / Next.js）的替代方案** —— 保留 WP 當 CMS backend，前端改 SSG，能徹底擺脫 plugin API 不穩 + 效能瓶頸。ADR 沒討論這條是因為怕遷移成本，但 192 篇不是大數字。
2. **Phase 1 應完全禁止 Brook 自動化 tagging + 用單一 `default.yaml` style profile** —— Gemini 的 scope 壓縮建議最激進：Phase 1 核心只留「Brook 草稿 → Usopp draft 發布」，SEOPress、tag、featured image、style profile 全延後。這種 MVP 切法比 Claude / Grok 更保守、更實際。
3. **`shared/fluent_client.py` 根本不該出現在這個 ADR** —— Gemini 明確指出它屬 ADR-008 範疇，這個 ADR 混了太多 Phase 2/3 東西。

### Grok 獨到看法

1. **直接點名 `shared/approval_queue.py` 需加 lock 避免 concurrent approve 衝突** —— 這是唯一具體指出 HITL approve race condition 的模型，而且給了檔案命名。
2. **Category slug 拼錯（e.g. "neuroscience" 拼成 "neurosience"）POST 直接 400 無 fallback** —— 實作細節層級最具體的一個 pitfall。
3. **為何選 SEOPress 而非更普及的 Yoast SEO？ADR 無解釋** —— 這是三家裡唯一質疑選型理由的，值得在 ADR 加一句 rationale。

---

## 4. 三家不同意的點

**分歧 1：Phase 1 範圍該壓縮到多激進？**
- Gemini：最激進 —— 禁止 tag、SEO 手動設、featured image 人工、用單一 default style profile。
- Claude：中度 —— 保留核心 flow 但要求補齊 5 項 schema/設計（draft schema、SEOPress payload、idempotency、style profile schema、auth 設計）。
- Grok：最寬鬆 —— 只要補資安 + VPS benchmark 就能通過。
- **仲裁建議**：採 **Claude 的中度方案為基準，吸收 Gemini 的「SEOPress 和 tagging 延後」建議**。理由：Gemini 激進派把 SEO 設為次要是錯的（SEO 是 ADR 第一天就要解的問題，等 Phase 2 再補會積 debt）；Grok 寬鬆派忽略了 draft schema / idempotency / Gutenberg validation 三個會讓開工即爆炸的 blocker。折中是：核心 flow + auth + observability + Gutenberg validator + staging 環境 Phase 1 做完；SEOPress 整合拆成 ADR-005a 與 Phase 1 並行但獨立驗收；tag 自動化延到 Phase 2。

**分歧 2：Bricks AI Studio 風險嚴重度**
- Claude：高估（Phase 1 不在關鍵路徑，應移除）。
- Grok：高估（人工橋接可暫代）。
- Gemini：沒提。
- **仲裁建議**：從 Phase 1 風險欄移除，降級到 Phase 2+ 預覽章節。

**分歧 3：替代方案的 Headless CMS 路線**
- Gemini 強力推（Headless + SSG 或 Contentful / Sanity）。
- Grok 提但認為遷移成本太高。
- Claude 沒提。
- **仲裁建議**：Phase 1 不轉 Headless（修修熟 WP + Bricks + 既有內容），但在 ADR 加一段「未來重構路徑」：若 Phase 2 後 plugin API 崩盤/效能撞牆，遷 Headless 是備案。

**分歧 4：tag 清理嚴重度**
- Gemini：「模型污染」問題，嚴重低估。
- Grok：「Brook 可透過 Robin 過濾，影響有限」，嚴重高估。
- Claude：中性，指出 token 數 + 不存在 tag 的 fallback 路徑未定義。
- **仲裁建議**：採 Gemini + Claude 觀點。Phase 1 禁止 Brook 新增 tag，只從既有選；同時拆獨立 ADR 做 tag governance cleanup。

---

## 5. 最 blocking 的問題（合併版，按嚴重度排序）

1. **Draft Object Schema + Gutenberg Validation 未定義**（Claude blocker 1+2 合併 + Gemini + Grok 呼應）
   —— Brook / Usopp 的核心 contract 不存在 + 無 block markup validator，開工即兩個 agent 無法並行。
2. **無 Staging 環境 + 無測試策略**（Gemini blocker 2 + Claude 缺失視角）
   —— 在生產 WP（192 篇既有內容）上試 API = 災難一次。
3. **狀態機 / idempotency / 原子性缺失**（Gemini blocker 1 + Claude R4 + Grok retry 坑）
   —— 沒持久化 state 的 retry 會產生孤兒 post + 重複文章。
4. **資安設計（auth + secret + 最小權限）未定義**（Grok blocker 2 + Claude 缺失視角 + Gemini pitfall）
   —— application password 洩漏 = 全站 game over。
5. **VPS 資源 benchmark 未做**（Grok blocker 1 + Claude R8 + Gemini 資源競爭）
   —— 4GB RAM 能不能扛 Brook + Usopp + Robin + MySQL 並發，沒實測就上 = 賭博。

---

## 6. 合併建議：Phase 1 開工前必做清單

**Tier A — Blocker（不做不能開工）**

- [ ] 寫完整 **Draft Object JSON Schema**（含 `content_json`（改 AST）、slug candidates、`featured_media_id`、所有欄位型別與 required/optional），Brook 和 Usopp 都依賴此 schema
- [ ] 建 **Docker WordPress staging 實例**（複製 192 篇的 subset 供測試）+ CI mock 策略決定（`responses` library 或 recorded cassette）
- [ ] 設計 **Usopp state machine / persistent queue**（`shared/approval_queue.py` 加 lock + idempotency key 基於 slug hash）
- [ ] 補 **auth / secret / 最小權限** 專章：WP user 角色 = Editor 自訂、application password 存法、HMAC 作用域、retry backoff 策略
- [ ] 實測 **VPS benchmark**：Brook LLM inference + Usopp batch publish + Robin PubMed digest 並發下 RAM/CPU 曲線，給出 headroom 數字

**Tier B — Phase 1 中期可補（但要在 ADR 先寫定）**

- [ ] **Gutenberg block validator**（或改走 JSON AST + Python serializer 方案，Claude 替代方案）
- [ ] **Structured logging schema**（trace_id / post_id / draft_id / latency_ms / http_status）
- [ ] **SEOPress 9.4.1 exact payload 快照 + post meta fallback 方案**（拆 ADR-005a）
- [ ] **LiteSpeed cache purge 顯式呼叫**（不依賴 plugin 自動偵測）
- [ ] **Style profile YAML schema**（TBD 換成結構定義，即使值還沒填）
- [ ] **健康內容 compliance guardrail** + HITL checklist
- [ ] **LLM cost 估算**（含 output + thinking token）+ 月度預算上限

**Tier C — Phase 1 ADR 應移除或拆走**

- [ ] `shared/fluent_client.py` → ADR-008 範疇，從本 ADR 刪除
- [ ] Bricks AI Studio 風險 → 從 Phase 1 風險欄移到「Phase 2+ 預覽」
- [ ] 多平台 `PublishTarget` Protocol 抽象 → 延後到 Phase 2 有第二個 target 時再設計
- [ ] Tag 清理 governance → 另拆 ADR

---

## 7. 修修下一步

**建議：修改後通過（取 Claude + Gemini 中間路線，Grok 6/10 太樂觀）。**

理由：三家平均 4.3/10，共識集中在 10 個系統性缺陷（API 契約脆弱、Gutenberg 無 validator、無 staging、無 observability、無 idempotency、無 auth 設計、cache invalidation、tag 策略、LLM 成本、VPS 資源），但核心方向（WP REST + Gutenberg + shared lib 抽象 + HITL bridge）沒有根本錯誤，Gemini 的「退回重寫」略重。建議補完上述 Tier A 五項 blocker 後重新 review 一輪，Tier B 納入 ADR 文本但可 Phase 1 中期實作。Tier C 砍掉/拆走以降低這份 ADR 的 scope 膨脹。
