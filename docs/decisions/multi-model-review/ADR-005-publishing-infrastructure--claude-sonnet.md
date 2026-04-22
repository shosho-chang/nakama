---
source_adr: ADR-005-publishing-infrastructure
reviewer_model: claude-sonnet-4-6
elapsed_seconds: 130
review_date: 2026-04-22
---

# ADR-005-publishing-infrastructure — claude-sonnet 審查

# ADR-005 審查報告

---

## 1. 核心假設檢驗

**A1：SEOPress Pro REST API 在 9.4.1 是穩定可靠的**
這是全文最危險的假設。`/wp-json/seopress/v1/posts/{id}` 這個 endpoint 在 SEOPress 的官方文件中屬於「實驗性」功能，歷史版本有多次無公告 breaking change。ADR 沒有任何 API contract 快照、沒有 integration test stub、也沒有 fallback。一旦 SEOPress 升版，Usopp 的 publish flow 靜默失敗，文章帶著空白 SEO meta 就上線了。

**A2：Gutenberg block HTML 從 LLM 輸出是可靠的**
ADR 假設 Brook 能穩定輸出合法的 Gutenberg block comment syntax（`<!-- wp:paragraph -->`）。實際上 LLM 在長文章中極易在 block boundary 出錯——少 closing comment、嵌套錯誤、attribute JSON 格式錯誤。WP REST API 對格式錯誤的 block markup 不會報錯，它直接存進 `post_content`，前台 render 出來的是破碎的 HTML，且 Bricks 的 `Post Content` element 不會提示錯誤。

**A3：application password 是夠用的 auth 機制**
ADR 提到 HMAC（在 shared lib 的描述裡），但沒說 HMAC 用在哪、保護什麼。如果只是 WordPress application password，那它是 base64 明文傳輸（依賴 HTTPS）。沒有 token rotation、沒有 IP allowlist、沒有 per-agent 的最小權限設計。

**A4：两站共 MySQL 不需要連接池管理**
ADR 把這個風險輕描淡寫交給 Franky 監控。但根本問題是：Usopp batch publish + Brook 同時做 `/kb/research` + Robin 跑 PubMed digest，三個 IO-heavy 任務在同一個 4GB RAM 的 MySQL instance 上，沒有任何 connection limit / queue 設計。

**A5：WP REST API 的 `post_content` 欄位可直接寫 rendered HTML**
ADR 說「REST API 可讀可寫」，但 WP REST API 實際上有兩個欄位：`content.raw`（block markup）和 `content.rendered`（render 後 HTML）。寫入時必須用 `content.raw`。如果 Brook 輸出的 `content_html` 變數名稱和實際 payload key 不一致，存進去的會是 rendered 版（或更糟：被 WP 的 kses 過濾器清理掉自訂 attribute）。

**A6：`wps-hide-login` 不影響 REST API**
ADR 直接斷言「REST API 不受影響」但沒有任何測試依據。這個 plugin 的部分版本會在特定設定下對 `/wp-json/` 路徑重定向或加入 nonce 要求。

**A7：197 篇文章的 taxonomy 足夠做 Brook 的分類訓練**
style profile 三個欄位全是 TBD，ADR 假設這個訓練可以在上線前完成。這不是技術假設，是 project management 假設，且沒有 deadline。

---

## 2. 風險分析

### (a) 未被提及但會造成生產問題的風險

**R1：LiteSpeed Cache 與 REST API publish 的 cache invalidation 競態**
Usopp 做 `PATCH status=publish` 之後，LiteSpeed 的 cache purge 是非同步的（plugin hook 觸發）。如果 Franky 監控在 publish 後立刻抓頁面做 SEO 驗證，很可能讀到 stale cache。ADR 的 Open Question 3 把這個問題打發掉（「讓 LiteSpeed plugin 自己偵測」），但這個決定沒有驗證依據。

**R2：`happyfiles-pro` media library 與 REST API 的相容性**
Usopp 的 `POST /wp/v2/media` 是標準 flow，但 `happyfiles-pro` 會在 media upload 後自動分類。如果 Usopp 上傳的 featured image 沒有帶 HappyFiles 的 folder 參數，圖片會進 uncategorized，媒體庫會逐漸混亂。這在 192 篇之後的規模是 operational problem。

**R3：`advanced-custom-fields-pro` 與 REST API 的 field 暴露**
ACF Pro 預設會把所有 field group 暴露到 REST API response（包括任何 internal 欄位）。Usopp 的 GET response 會比預期大很多，更嚴重的是如果 ACF 有 repeater field 或 file field，REST API 的 response schema 會動態改變，導致 Usopp 的 response parser 在不可預期的時間點拋 KeyError。

**R4：Usopp 沒有 idempotency 設計**
如果 Usopp 在 `POST /wp/v2/posts` 之後、`POST /seopress/v1/posts/{id}` 之前 crash，retry 會建立重複文章。ADR 有提 retry 但沒有說明 idempotency key 的設計。在 `shared/wordpress_client.py` 的層級如果沒有「先查 slug 是否存在」的 guard，生產環境裡一定會出現重複文章。

**R5：Brook 輸出的 `focus_keyword` 和 SEOPress 的 `focus_keyword` 欄位名稱未對齊**
SEOPress 的 REST API payload 的精確欄位名稱沒有在 ADR 裡寫出來。不同版本的 SEOPress Pro 用過 `seopress_titles_keywords`、`focus_kw`、`_seopress_analysis_target_kw` 等不同 key（因為底層是直接存 post meta）。這個對應沒有文件就是一個定時炸彈。

**R6：WordPress 的 `kses` HTML 過濾器**
WP 的 `wp_kses_post` 會在非管理員角色寫入 `post_content` 時過濾掉某些 HTML 標籤和 attribute。如果 Usopp 使用的 application password 對應的 WP user 不是 Administrator 角色（應該如此，最小權限），Brook 產生的特定 HTML（例如含 `style` attribute 的 block、`<figure>` 的某些 attribute）會被靜默清除。

### (b) 已提及但嚴重度被低估的風險

**R7：497 個 tag 的問題**
ADR 說「影響 Brook 的 tag 選擇品質」，這是嚴重低估。真正的問題是：497 個 tag 的 list 如果全部丟給 Brook 做 selection context，token 數量就爆了（光 tag list 就可能 500-800 tokens）。如果不丟，Brook 會選出不在現有 list 的 tag（因為沒有 constraint）。ADR 說「不自動擴增」但沒說 Usopp 如何 enforce——如果 Brook 建議的 tag 不存在，Usopp 是 create 還是 skip？這個決策路徑完全沒有描述。

**R8：兩站共 MySQL + 4GB RAM**
ADR 說「Franky 監控」就帶過了。但具體問題是：OpenLiteSpeed + PHP-FPM 的 worker pool 設定在 4GB RAM 的 VPS 上通常是 10-20 worker，每個 worker 常駐 50-80MB。加上 MySQL buffer pool 吃 1-1.5GB，Brook 做 LLM inference 時的 RAM 佔用是多少？這些數字都不在 ADR 裡，「Franky 監控」不能解決根本問題，只能讓你知道什麼時候死。

### (c) 已提及但嚴重度被高估的風險

**R9：Bricks AI Studio 未裝的問題**
ADR 把這個列為風險，但根據決策本身（Brook 永遠走 Gutenberg，Bricks 只負責 render），Bricks AI Studio 根本不在 Phase 1 的關鍵路徑上。這個風險其實是 Phase 2+ 的 concern，不應該在 Phase 1 ADR 的風險欄位佔位，它在這裡只是製造噪音。

---

## 3. 替代方案

**關於 SEOPress REST API：**
更 proven 的做法是直接用 WP REST API 的 `meta` 欄位寫 post meta（`_seopress_titles_title`、`_seopress_titles_desc` 等），繞過 SEOPress 的中間層 API。這樣 Usopp 對 SEOPress plugin 版本變化有隔離。壞處是要自己查清楚 SEOPress 的 post meta key 名稱，但這些 key 比 REST endpoint 穩定得多（它們是存進 DB 的 key，改了會破壞現有資料，plugin 作者不敢亂動）。作者沒選這條路可能是因為不知道 SEOPress 的 REST API 底層就是在包 post meta，或者覺得 REST endpoint 「比較正式」。

**關於 Gutenberg block 生成：**
與其讓 Brook 直接輸出 Gutenberg block markup，更穩的做法是 Brook 輸出**結構化 JSON**（heading / paragraph / list / quote 的 AST），由 `wordpress_client.py` 中的 serializer 負責轉換成 block markup。這樣 LLM 只需要輸出乾淨的 JSON（失敗容易偵測），block syntax 的正確性由程式碼保證。這個 pattern 有 `@wordpress/blocks` 的 JS 實作可以參考邏輯，Python 端自己寫 serializer 也不超過 200 行。作者沒選這條路可能是為了「簡單」，但實際上反而讓最容易出錯的部分留在 LLM 手裡。

**關於 HITL bridge：**
ADR-006 應該就是 bridge，但這個 ADR 裡的 bridge 設計（Bridge `/bridge/drafts`）完全是一個黑盒子。如果 bridge 只是一個輕量審核介面，直接用 Basecamp、Notion、或甚至 Google Form + Apps Script 這類 proven 工具作為 Phase 1 的 HITL 介面，比自建 bridge 便宜得多。作者為什麼不選：可能是因為 HITL 需要帶 structured metadata（`featured_media_id`、slug 選擇）而不是純文字審核，現成工具做不到這個精度。這個理由勉強說得過去，但 ADR 裡沒有說清楚。

---

## 4. 實作 Pitfalls

**P1：`content_html` 變數名稱與 WP REST payload 衝突**
Brook 的 draft object 有 `content_html` 欄位，但 WP REST API 的 `POST /wp/v2/posts` body 應該是：
```json
{"content": "<!-- wp:paragraph --><p>...</p><!-- /wp:paragraph -->"}
```
如果 `wordpress_client.py` 的 mapping 寫成 `payload["content_html"] = draft.content_html`，文章會發布成功（HTTP 201）但 `post_content` 是空的，因為 WP 會忽略未知欄位而不報錯。這個 bug 在 dev 環境如果沒有做 end-to-end 驗證（實際去看 WP DB 的 `post_content`）可能存活很久。

**P2：SEOPress endpoint 的 HTTP method 未確認**
ADR 寫 `POST /wp-json/seopress/v1/posts/{id}`，但根據 SEOPress 的 source code，這個 endpoint 在某些版本是 `PUT`，在某些版本接受 `POST`。如果版本不對，會收到 405 Method Not Allowed，retry 邏輯會重試但永遠失敗。`wordpress_client.py` 的 retry 邏輯必須對 4xx 和 5xx 做不同處理，但 ADR 沒有說明。

**P3：`config/style-profiles/{category}.yaml` 的 category slug 對應**
一個文章可以有多個 category。ADR 說「依 draft 的 primary category 自動套對應 style profile」，但沒有定義什麼是 primary category。如果一篇文章 category 是 `[book-review, neuroscience]`，Brook 用哪個？如果 `book-review.yaml` 不存在（因為 TBD 還沒生成），系統是 hard crash 還是 fallback 到 default profile？這個 edge case 在實作時如果沒有明確處理，會在 Brook 第一次跑讀書心得科普混合類型文章時爆。

**P4：`PublishTarget` Protocol 的 `platform` 欄位 Literal 型別**
```python
platform: Literal["wordpress", "fluentcrm", "instagram", "facebook", "youtube"]
```
這個 Protocol 定義是正確的，但 `Usopp.publish()` 的實作必然有一個 dispatch 邏輯（`if target.platform == "wordpress": ...`）。ADR 沒有說這個 dispatch 在哪裡。如果 dispatch 在 `Usopp` class 裡面，Phase 2 加 FluentCRM 就要改 Usopp 的核心邏輯，違反 Open/Closed。如果 dispatch 用 plugin registry pattern，ADR 應該說清楚。

**P5：`featured_media_id` 的傳遞路徑**
Phase 1 的流程是修修手動選圖後在 Bridge 填入 `featured_media_id`。但 Bridge 的 draft object schema 裡沒有這個欄位的定義（這在 ADR-006 裡，但這個 ADR 直接依賴它）。如果 ADR-006 的 draft schema 沒有 `featured_media_id`，Usopp 就拿不到這個值，featured image 永遠是空的。這是兩個 ADR 之間的介面未對齊問題。

**P6：slug 候選清單與 WP 的 slug 去重機制**
Brook 生成 3 個候選 slug，但 WP REST API 在建立 post 時如果 slug 已存在，會自動加 `-2`、`-3` 後綴而不是報錯。如果修修選了一個已存在的 slug，Usopp 不會知道實際使用的 slug 和預期不同，這會影響後續的 SEO canonical 設定和 LiteSpeed cache purge URL。

---

## 5. 缺失的視角

**資安（嚴重缺失）**
Application password 的 WP user 應該有最小權限（Editor 或自訂角色），但 ADR 沒有規定。`shared/wordpress_client.py` 裡提到 HMAC 但完全沒有說明 HMAC 保護什麼——是 Nakama 內部 agent 間的通訊？還是呼叫 WP REST API 的 request signing？這兩個是完全不同的事情。Application password 本身沒有 request signing，只有 Basic Auth。如果 HMAC 是內部用的，那 WP REST 那段的 credential 管理根本沒有描述。

**可觀測性（嚴重缺失）**
整個 publish flow 沒有任何 structured logging 或 tracing 的要求。一篇文章從 Brook 產出到 WP 上線，中間有 4-5 個 API call。任何一個失敗，工程師要怎麼 debug？ADR 沒有說 Usopp 要把哪些事件寫到哪裡（Franky 監控是 downstream consumer，不是替代品）。最基本的：publish 成功/失敗應該有 structured log 含 `post_id`、`slug`、`draft_id`、每個 API call 的 latency 和 response code。

**可測試性（嚴重缺失）**
`shared/wordpress_client.py` 沒有任何 testing strategy。WP REST API 要怎麼 mock？SEOPress endpoint 要怎麼 stub？如果沒有 WP test instance 或 recorded cassette，工程師在 CI 裡根本無法測試 Usopp。ADR 應該說明：是用 `responses` library mock HTTP、還是架 WP docker instance、還是用 WireMock。

**法規（輕描淡寫）**
修修是健康與長壽內容創作者。台灣的《健康食品管理法》和衛福部的醫療廣告規範對健康類內容有特定限制（不能聲稱療效等）。ADR 完全沒有提這個面向。Brook 的 style profile 應該要有 compliance guardrail，HITL 的 approve 介面應該有醒目的 compliance checklist。這不是 Phase 2 的問題，是 Day 1 的問題。

**成本（缺失）**
Brook 寫一篇文章要 call 多少 token？Robin PubMed digest 要多少？這些 LLM cost 沒有任何 estimate。VPS 的固定成本知道，但 API cost 是變動的，也是整個 Nakama 系統最不可預測的成本。ADR 對此完全沉默。

**可維護性（部分缺失）**
style-profile YAML 全是 TBD，但沒有說這些 YAML 的 schema。之後工程師要維護這些 YAML 時，沒有 JSON Schema validation，沒有說明哪些欄位是必填，Brook 的 system prompt 怎麼消費這些 YAML 也沒有描述。這不是小事，style profile 是這個系統的核心配置。

---

## 6. Phase 拆分建議

**應該拆成獨立 ADR：**

- **ADR-005a: Brook Content Model & Gutenberg Serialization**
  Brook 的輸出格式（draft object schema）和 block HTML 的生成策略應該獨立出來。這不是 publishing infrastructure 的問題，是 content generation 的問題，兩件事放在同一個 ADR 讓邊界不清。

- **ADR-005b: Usopp Auth & Credential Management**
  WordPress application password、HMAC 的使用方式、per-site credential 的 env var 命名規範，這些應該獨立成一個小 ADR，因為它影響 security posture，不應該藏在 shared lib 描述裡一句話帶過。

**應該延後到 Phase 2+（不應該在這個 ADR 描述）：**
- `PublishTarget` Protocol 的 multi-platform 設計（第 8 節）：Phase 1 只有 WordPress，過早抽象。把 Protocol 的設計留到 Phase 2 有實際第二個 target 時再做，更能做出正確的抽象。現在的設計很可能在遇到 FluentCRM 時就需要大改。
- `shared/fluent_client.py` 的描述：Phase 2/3 的東西，不應該出現在 Phase 1 ADR 的決策表裡。

**必須 Phase 1 完成（但 ADR 目前描述不足）：**
- Draft object 的完整 JSON Schema（含 `featured_media_id`、slug candidates、所有欄位的型別和 required/optional）——這是 Brook 和 Usopp 之間的 API contract，沒有它兩個 agent 無法並行開發。
- Style profile YAML schema（不能是 TBD 就上線）。
- Usopp 的 idempotency 設計。
- 健康內容的 compliance checklist 在 HITL 介面上的呈現。

---

## 7. 結論

### (a) 整體可行性評分：**4 / 10**

方向正確，技術選型沒有大錯（Gutenberg REST、SEOPress、shared lib 抽象），但這份 ADR 把「計畫做什麼」和「怎麼做才不會出錯」混在一起，關鍵的介面定義、error handling 策略、測試策略全部缺席。TBD 的數量（style profile、HMAC 說明、idempotency、draft schema）在一個要投入生產的系統裡是不可接受的。照這份 ADR 開工，工程師在實作的第一週就會遇到 3-4 個需要重新開 meeting 確認的 blocker。

### (b) 建議：**退回修改**

不是退回重寫，核心方向可以保留，但至少要補齊以下內容才能開工：
1. Draft object 的完整 JSON Schema
2. SEOPress REST API 的 exact payload 格式（含版本快照）
3. Usopp 的 idempotency 設計
4. Style profile YAML 的 schema definition（TBD 換成實際欄位定義，即使值還沒填）
5. Auth 設計的明確描述

### (c) 最 Blocking 的問題

**Blocking 1：Draft Object Schema 未定義**
Brook 和 Usopp 是兩個獨立 agent，它們之間的 contract（draft object）是整個系統的核心介面。這份 ADR 只用文字描述了幾個欄位，沒有完整的 JSON Schema、沒有型別定義、`featured_media_id` 甚至不在 Brook 的輸出裡（它在 Bridge）。沒有這個 schema，Brook 開發和 Usopp 開發無法並行，整合時會是災難。

**Blocking 2：Gutenberg Block 的生成與驗證策略**
ADR 假設 Brook 能穩定輸出合法的 Gutenberg block markup，但沒有任何 validation step。在 Usopp 呼叫 `POST /wp/v2/posts` 之前，必須有一個 block syntax validator（可以用 regex 或簡單的 parser）。如果 LLM 輸出的 markup 有問題，應該在 publish 前 fail fast 並返回 Bridge 要求 Brook 重試，而不是讓破碎的 HTML 靜默發布到生產環境。這個機制不存在，整個 content pipeline 就是不可靠的。