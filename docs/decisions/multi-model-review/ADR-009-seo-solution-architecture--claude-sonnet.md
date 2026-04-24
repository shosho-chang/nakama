---
source_adr: ADR-009-seo-solution-architecture
reviewer_model: claude-sonnet-4-6
elapsed_seconds: 163
review_date: 2026-04-24
---

# ADR-009-seo-solution-architecture — claude-sonnet 審查

# ADR-009 架構審查報告

---

## 1. 核心假設檢驗

### 假設 A：`keyword-research` frontmatter 是穩定的輸入合約
ADR 把 `keyword-research` frontmatter schema 當成 `seo-keyword-enrich` 的輸入合約，但從未正式定義這個 schema。Open Items #4 說「`keyword-research` 要升版需同步升 `SEOContextV1`」，卻沒有任何機制保證這件事發生。這是一個單向依賴被當成雙向契約在用的典型錯誤。

**實際狀況**：`keyword-research` 已 production 凍結，但 SKILL.md frontmatter 是 Markdown 文字，不是 Pydantic schema。沒有 `KeywordResearchOutputV1` schema，`seo-keyword-enrich` 的 parser 就是在 parse 自由格式文字，schema drift 是必然的。

### 假設 B：GSC API 的 `striking distance` filter 在 28 天維度下有足夠資料量
修修是單一創作者，小站。GSC 28 天資料量可能不夠讓 `position ge=10.0 le=21.0` 的 filter 有任何命中。如果某篇文章只有 3 次 impression，GSC 的 average position 數值本身就不可信（統計上是噪音）。ADR 完全沒討論「資料量不足時 `StrikingDistanceV1` 回空 list 的 UX」。

### 假設 C：Claude 的 system prompt 長度對 compose 品質沒有負面影響
`_build_seo_block` 把 striking distance、cannibalization、competitor SERP summary（最長 3000 字）全塞進 system prompt。`SEOContextV1` 滿載時這個 block 可能超過 2000 tokens。ADR 沒有討論：這些 token 和 `StyleProfile`、原有 prompt 合計後有沒有超過 context budget、會不會稀釋風格指令的權重。

### 假設 D：DataForSEO 的 health keyword 限制只影響 `search_volume`
ADR 在 D2 說「health 類會被 anonymize」，建議省略此欄位。但 DataForSEO 的 `keyword_difficulty` 計算本身依賴 SERP 分析，health 類關鍵字在 SERP 受 Google Health 政策影響（特殊版面、Knowledge Panel 插入），DataForSEO 回傳的 difficulty 數值對 health niche 是否有代表性完全沒討論。

### 假設 E：3 個 skill 的觸發詞路由是可靠的
整個架構假設 Claude agent 能穩定地根據 SKILL.md 的 `description` 把正確的使用者訊息路由到正確的 skill。這個路由機制（presumably Claude function calling 或 tool use）的失敗模式完全沒討論：錯誤路由、多個 skill 同時被觸發、沒有任何 skill 被觸發。

### 假設 F：`shared/gsc_client.py` 能同時服務 interactive 和 batch 兩種使用模式
ADR 說 ADR-008 Phase 2 會 `import` 這個 client 加 batched wrapper。但 interactive query（ADR-009）的設計考量（快速回應、小量資料、auth token cache）和 batch 設計（大量資料、error recovery、resumable）是根本不同的需求。一個 client 同時服務兩個場景，要嘛 batch 場景妥協，要嘛 interactive 場景過度複雜。

---

## 2. 風險分析

### (a) 未被提及但會造成生產問題的風險

**風險 1：GSC API quota exhaustion**

GSC Search Analytics API 每天的 quota 限制是 per-project 的，不是 per-property。ADR-008（批次落庫）和 ADR-009（互動式查詢）共用同一個 service account，就很可能共用同一個 GCP project，因此共用 quota。當 ADR-008 Phase 2 的 cron job 在跑批次同步，修修同時觸發 `seo-keyword-enrich`，誰先把 quota 用完是未定義行為。ADR 沒有 quota budget 分配機制。

**風險 2：`compose_and_enqueue` 的 signature 改動需要測試所有 call sites**

D5 修改了 `compose_and_enqueue` 的 signature。但 ADR 只說「跑 `tests/agents/brook/test_compose*.py` 全綠」。問題是：這個函數在整個系統裡有多少個 call site？Nami？Zoro？Chat handler？如果有 call site 是用 positional argument 而非 keyword argument，加新參數就是 breaking change（即使有預設值）。ADR 說「向後相容」但沒有驗證手段。

**風險 3：`seo_context` 序列化 / 反序列化跨 skill 傳遞**

ADR 說 `seo-keyword-enrich` 輸出 `SEOContextV1`「寫成 markdown + frontmatter，下游 skill 可 parse」。這句話隱藏了一個巨大的工程問題：Pydantic model 序列化成 YAML frontmatter，再反序列化回 Pydantic model，中間有型別精度損失（float → string → float）、None vs 缺欄位的 YAML 表示差異、`AwareDatetime` 的 timezone 序列化格式等。這個往返序列化沒有被定義，沒有被測試。

**風險 4：firecrawl SERP 爬取的法律風險**

ADR 在 D2 把 firecrawl 列為「免費 quota 內」的數據源，用途是爬取「競品 top-3 SERP 頁面結構」。被爬的網站可能有 robots.txt 禁止、ToS 禁止爬蟲、或在台灣法規下構成著作權問題（摘要雖有合理使用空間，但「結構爬取」更接近複製）。ADR 的法規段落完全沒提這件事。

**風險 5：`SEOContextV1.competitor_serp_summary` 的 prompt injection 風險**

競品網站的內容會被爬取、被 Claude Haiku 摘要後直接插入 system prompt。如果競品網站故意在頁面裡放 prompt injection payload（這在 SEO 圈子是已知攻擊面），這段內容會直接影響 Brook compose 的行為。ADR 完全沒有 sanitization 步驟。

**風險 6：`StrikingDistanceV1.current_position` 的 confloat 邊界在 GSC API 實際行為下會出問題**

GSC API 回傳的 average position 對 impression 很少的關鍵字可能是 1.0（只出現一次，排名第一），或是超過 100（第 11 頁）。`confloat(ge=10.0, le=21.0)` 的 schema validation 在這些邊緣值上會直接拋 ValidationError，但 ADR 把這個 filter 邏輯說成是「由 skill 的 filter logic 決定」。如果 skill 在 validate 前沒有 filter，就是 runtime error。

### (b) 已提及但嚴重度被低估的風險

**風險：跨 skill schema drift（ADR 原文 §Consequences 有提，但低估了）**

ADR 說「`SEOContextV1` 任何變更都影響 3 個 skill + Brook compose，緩解：走 `schema_version: Literal[1]`」。這個緩解措施在 runtime 根本沒有效果。`Literal[1]` 只確保你不會把 V2 的 object 當 V1 用，但如果你在 V1 裡加了 optional field，`extra="forbid"` 加上 `frozen=True` 並不能防止 consumer 端讀到 None 後邏輯出錯。真正的問題是：`_build_seo_block` 讀 None 欄位的行為沒有 contract，而且這個 helper 未來一定會被改。

**風險：DataForSEO $50 sunk cost（ADR §Consequences 有提，但輕描淡寫）**

ADR 說「credits 不過期，若月用量極低可能用好幾年」，語氣輕鬆。實際嚴重度更高：DataForSEO 的 API credential 跟儲值帳戶綁定，如果 DataForSEO 調整 health vertical 政策（變更計費方式、關閉 API 端點）或帳戶被 flag（Health 類內容大量查詢），不只是 $50，而是整個 `seo-keyword-enrich` 的 `difficulty` 欄位變成廢物，但 `_build_seo_block` 的 prompt 邏輯已經依賴它。

### (c) 已提及但嚴重度被高估的風險

**風險：`_build_compose_system_prompt` 修改破壞 compose 測試**

ADR 在 §Consequences 第 5 條用了相當篇幅描述 regression test 策略（byte-identical comparison、snapshot test）。實際上這個風險很低——`seo_context=None` 是預設值，只要測試覆蓋這條 path，現有行為不會受影響。ADR 花太多篇幅在這個不需要複雜 mitigation 的點，反而讓讀者忽視了更嚴重的序列化問題（風險 3）。

**風險：3 個 skill 觸發詞需維護**

ADR 把觸發詞邊界當成一個需要主動維護的風險。實際上，只要 SKILL.md 的 `description` 寫清楚，agent routing 本身會吸收大部分的歧義。這不是需要在 ADR 層花大量篇幅定義的問題，是 skill 作者的工作。

---

## 3. 替代方案

### 替代方案 A：直接擴展 `keyword-research` skill 的輸出格式

ADR 選擇建立獨立的 `seo-keyword-enrich` skill，但 `keyword-research` 已經在做關鍵字研究，只是沒有 GSC enrichment。更簡單的架構是：在 `keyword-research` 的輸出 frontmatter 裡直接加一個 `enrichment: null` 或 `enrichment: {...}` 欄位，讓 `keyword-research` 的 Phase 2 版本選擇性地呼叫 GSC。

**為什麼作者沒選**：因為 `keyword-research` 已 production 凍結，不想動它。這個理由有一定合理性，但凍結一個還在 Phase 1 的 project 的 skill 然後說「不能改」是否合理，值得質疑。真正的凍結應該是「API 契約不變」，而不是「不能加 optional 輸出欄位」。

### 替代方案 B：用 Google Sheets + Apps Script 做 GSC 數據聚合

對於修修這個 single-creator 的 use case，Google Sheets 連接 GSC 是完全 proven 的解法（GSC 官方支援 Looker Studio，Data Studio connector 免費）。這比建 `shared/gsc_client.py` 快 10 倍，且修修可以自己操作。

**為什麼作者沒選**：沒有提及。應該明確說為什麼要自建 GSC client 而不是接現成工具。可能的理由是「要把 GSC 數據自動注入 compose」，但這個整合點其實可以讓 Claude 讀一個 Google Sheets URL 或 CSV export。

### 替代方案 C：用 `serpapi` 或 `valueserp` 取代 DataForSEO

SerpAPI（$50/月，2000 searches）和 ValueSERP（$30/月）都有 Python SDK，不需要自建 `shared/dataforseo_client.py`，且對 health keyword 的限制比 DataForSEO 更寬鬆（因為它們是 SERP 爬蟲而非 Google Ads 數據聚合商）。DataForSEO 的 `keyword_difficulty` 是他們自己的 ML model，不是 Google 原生數據，可信度問題更大。

**為什麼作者沒選**：prior-art 選了 DataForSEO 但理由不充分，主要是「$50 起步」聽起來比月費便宜，但沒有和 SerpAPI 的成本做逐項比較。

### 替代方案 D：`seo-audit-post` 直接包裝 Lighthouse CLI

ADR 選擇呼叫 PageSpeed Insights API（需要 API key、有 rate limit、需要網路）。但 Lighthouse CLI 已經是 Google 官方工具，可以本機跑（VPS 上跑 headless Chrome），輸出完整 JSON，包含 SEO category 的所有細節，且 100% offline。

**為什麼作者沒選**：VPS 只有 2vCPU / 4GB RAM，跑 headless Chrome 有記憶體壓力。這個理由是合理的，但 ADR 沒有明說。

---

## 4. 實作 Pitfalls

### Pitfall 1：`seo-keyword-enrich` 的 frontmatter parse 沒有定義

**具體問題**：ADR 說 `seo-keyword-enrich` 讀 `keyword-research` 的 frontmatter，但這個 frontmatter 是什麼格式？是 YAML？有哪些欄位？`core_keywords` 是 `list[str]` 還是 `list[dict]`？

工程師照 ADR 寫的時候，會去看 `.claude/skills/keyword-research/SKILL.md` 的實際輸出，然後寫一個 `parse_keyword_research_frontmatter()` 函數。這個函數沒有 schema 保護，`keyword-research` 輸出格式一改就 runtime error，且沒有 test fixture 能捕捉到，因為 fixture 是工程師自己手寫的，不是從 schema 生成的。

**修正**：要求 `keyword-research` 的輸出定義 `KeywordResearchOutputV1` Pydantic schema，並在 `shared/schemas/` 落地，`seo-keyword-enrich` import 並 validate。

### Pitfall 2：`SEOContextV1` 序列化為 YAML frontmatter 的精度問題

**具體問題**：`SEOContextV1.related_keywords[i].difficulty` 是 `confloat(ge=0, le=100)`。序列化成 YAML 可能是 `difficulty: 45.7`，但如果 Python 的 float representation 是 `45.699999999999996`，YAML dump 後再 parse 回來就不是原來的值。

更嚴重的問題是 `AwareDatetime`：Python 的 `datetime` 序列化成 YAML 的格式和 Pydantic 的 `model_dump(mode="json")` 輸出不同。如果工程師用 `yaml.dump(seo_context.model_dump())`，`generated_at` 可能變成 Python datetime object 而不是 ISO string，YAML 輸出會是 `2026-04-24 10:00:00+08:00`，再 parse 回 Pydantic 時 `AwareDatetime` validator 可能過不了。

**修正**：明確定義序列化策略。建議用 `seo_context.model_dump(mode="json")` → JSON string 直接存在 frontmatter 的單一欄位 `seo_context_json`，避免手動 YAML 序列化的所有問題。或者走獨立的 `.seo_context.json` 檔案，frontmatter 只存路徑。

### Pitfall 3：`host_to_target_site` mapping 在 `shared/gsc_client.py` 的位置錯誤

**具體問題**：D3 說「`shared/gsc_client.py` 提供 helper `host_to_target_site("shosho.tw") → "wp_shosho"`」，並且這個 mapping 要加 fixture test。但這個 mapping 的邏輯是 business logic（`"shosho.tw"` 對應 `"wp_shosho"` 是 Nakama 的設定，不是 GSC API 的行為），放在 `gsc_client.py` 裡違反了 `gsc_client.py` 應該是 thin API wrapper 的設計原則。

更嚴重的是：`TargetKeywordV1.site` 用 `["shosho.tw", "fleet.shosho.tw"]`，`SEOContextV1.site` 用 `["wp_shosho", "wp_fleet"]`，這兩套 Literal 同時存在於不同 schema，但 mapping 只在 `gsc_client.py` 一個地方定義。任何一邊新增 site，另一邊不同步就是 runtime KeyError。ADR 說「加 fixture test 保 mapping 不壞」，但沒有說這個 test 怎麼保證兩套 Literal 的窮舉對齊。

**修正**：把 mapping 移到 `shared/schemas/site_mapping.py`，定義雙向 mapping 並加 test 確保 `set(TARGET_SITE_TO_HOST.keys()) == set(TargetSite.__args__)`。

### Pitfall 4：`_build_seo_block` 沒有 token budget 控制

**具體問題**：`competitor_serp_summary: constr(max_length=3000)` 最多 3000 字元。`related_keywords` 最多 20 個 `KeywordMetricV1`，每個都有 keyword、metrics、sources。`striking_distance` 最多 10 個。`cannibalization_warnings` 最多 5 個。全部滿載時，`_build_seo_block` 的輸出可能接近 1500 tokens。

加上 `StyleProfile` 的 system prompt（假設幾百 tokens），再加上 user message，Claude Sonnet 的 context 足夠，但問題是：LLM 對 system prompt 尾端的注意力比開頭低，大量 SEO 數據可能被實際上忽略。ADR 說 SEO block 放在「輸出規範之後」，這意味著 SEO 數據在整個 system prompt 的最末端，是注意力最弱的位置。

**修正**：`_build_seo_block` 應有 max token budget（例如 500 tokens），超出時做優先級截斷（striking distance > cannibalization warning > related keywords > competitor summary）。需要在 ADR 層定義這個優先級，不能留給實作者自由發揮。

### Pitfall 5：`StrikingDistanceV1` 的 `confloat(ge=10.0, le=21.0)` 會在正常 GSC 資料上拋 ValidationError

**具體問題**：GSC API 回傳的 `average_position` 在以下情況會超出這個範圍：

- 關鍵字排名在第 3 頁以後（position > 30）但 impression 很少，修修站台可能很多這種
- 某篇文章在某個關鍵字排名第 8-10（position < 10），是 striking distance 的邊緣，工程師可能想收進來但 schema 擋掉

ADR 說「實際收錄範圍由 enrich skill 的 filter logic 決定」，但如果工程師先建 `StrikingDistanceV1` object 再 filter，就會在 build object 時拿到 ValidationError。正確做法應該是先 filter（`10.0 <= avg_position <= 21.0`）再建 schema object，但這個順序 ADR 沒有明說，工程師很容易寫反。

**修正**：在 ADR 裡明確說明：「GSC raw rows 先在 skill script 層做 position filter，filter 通過的 rows 才建 `StrikingDistanceV1` object」。或者把 schema 的 constraint 放寬（移除 `confloat` 的上下界），把 business logic 的 filter 放在 skill 裡。

### Pitfall 6：`compose_and_enqueue` 的所有 call sites 需要稽核

**具體問題**：ADR 只指出 `compose.py:431` 是修改點，但沒有列出這個函數有哪些 call sites。如果有任何 call site 用了 positional argument（即使只是測試 fixture），加新參數後就可能出現意外行為。更大的問題是：ADR 要求「跑 `tests/agents/brook/test_compose*.py` 全綠」，但如果 call sites 散落在其他 agent 的測試裡（如 `tests/agents/nami/`），這條驗收條件不夠。

**修正**：PR 驗收條件應改為「`grep -r "compose_and_enqueue" tests/` 的所有 test 全綠」，並在 PR description 列出所有 call sites 的清單。

---

## 5. 缺失的視角

### 資安（嚴重缺失）

ADR 有 Secrets 管理（D8），但以下資安議題完全沒有：

1. **Prompt injection**：`competitor_serp_summary` 來自外部網頁，會被插入 system prompt。ADR 完全沒有 sanitization 策略。最低限度應該定義：firecrawl 爬取的內容在送給 Claude Haiku 摘要前，要 strip 所有看起來像 prompt 指令的段落（`ignore previous instructions`、角色扮演指令等）。

2. **DataForSEO credential 洩漏面**：`.env` 存放 `DATAFORSEO_LOGIN` / `DATAFORSEO_PASSWORD`（Basic auth）。如果這個 `.env` 被 commit 到 Git 或在 Slack 訊息裡被印出，影響範圍是 DataForSEO 帳戶的全部 credits。ADR 說「遵守 `feedback_no_secrets_in_chat.md`」但沒有說 structured log 裡的 DataForSEO HTTP request header 有沒有 sanitization。

3. **GSC service account 的 scope 範圍**：ADR 說「Viewer role」，但沒有說 Google API scope 是 `https://www.googleapis.com/auth/webmasters.readonly` 還是 `https://www.googleapis.com/auth/webmasters`。如果 `shared/gsc_client.py` 的實作要求 write scope，就是給了比需要更多的權限。

### 可觀測性（部分缺失）

ADR 援引 `observability.md §1` 和 `§9`，但沒有定義：

- `seo-keyword-enrich` 的執行時間 SLO 是多少？如果 DataForSEO 回應慢、firecrawl 超時，整個 skill 要跑多久才算異常？
- `seo-audit-post` 的 25 條 check + 10 條 LLM check，有沒有 per-check 的 pass/fail metric？還是只有整體的 markdown report？
- `SEOContextV1` 各欄位的 None rate 要不要 track？如果 `difficulty` 永遠是 None（DataForSEO 一直 fail），多久後要 alert？

### 可測試性（中度缺失）

ADR 說了 `compose.py` 的測試策略，但沒說：

- `shared/gsc_client.py` 如何測試？直接打 GSC API（需要 credential）還是有 mock？如果沒有 VCR cassette 或 fixture，CI 環境沒有 GSC credential 就不能跑 integration test。
- `seo-audit-post` 的 25 條 deterministic check 如何測試？需要一個已知的 HTML fixture。這個 fixture 要放在哪裡？
- `StrikingDistanceV1` 的 ValidationError case（position 超出範圍）有沒有測試？

### 效能（缺失）

VPS 是 2vCPU / 4GB RAM。`seo-audit-post` 需要：fetch HTML（網路 I/O） + PageSpeed API call（網路 I/O） + 25 條 check（CPU） + 10 條 LLM call（網路 I/O，逐條還是批次？）。如果是逐條 LLM call，就是 10 次 round-trip。ADR 沒有說這些 check 是 batch 一次送 Claude 還是逐條送，直接影響 latency 和成本。`seo-keyword-enrich` 需要 GSC API + DataForSEO API + firecrawl（3 次外部 call）+ Claude Haiku（1 次）。有沒有並行？

### 法規（嚴重缺失）

ADR 提到「台灣藥事法 SEO 不衝突」作為 `seo-audit-post` 的 LLM check 項目之一，但沒有定義這個 check 的具體規則。台灣藥事法第 65-69 條限制的是廣告，SEO 的關鍵字優化算不算廣告？如果 `seo-optimize-draft`（Phase 2）根據 SEO context 改寫文章，建議加入的關鍵字如果是藥品名稱，是否構成違規廣告？這個邊界 ADR 完全沒有定義，丟給 LLM 的 semantic check 去判斷是不夠的。

### 成本（部分缺失）

ADR 的 D6 有成本估算，但：

- 沒有估算 `shared/pagespeed_client.py` 的 API call 數量。PageSpeed Insights 免費 tier 是 400 requests/day/key，`seo-audit-post` 如果被頻繁觸發，會不會撞到上限？
- firecrawl 的「免費 quota」是多少？ADR 完全沒說。如果 `seo-keyword-enrich` 每次都爬 top-3 競品（3 次 firecrawl call），一週跑 10 次就是 30 次 firecrawl call，免費 quota 是否夠用？
- DataForSEO 按查詢計費，但 ADR 沒說每次 `seo-keyword-enrich` 會送幾個 keyword 去查 difficulty。20 個 related keywords 都送，還是只送前 5 個？

### 可維護性（中度缺失）

`shared/seo_audit/` 計畫拆成 `metadata.py` / `headings.py` / `images.py` / `schema_markup.py`，但沒有說 `seo-audit-post` 的 25 條 check rule set 怎麼管理。如果修修想新增或修改一條 rule，要改哪個檔案？有沒有一個 rule registry？還是散在各個 module 裡？

---

## 6. Phase 拆分建議

### 應獨立拆成獨立 ADR 的內容

**`shared/gsc_client.py` 應獨立成 ADR-009a**

這個 client 被 ADR-008 和 ADR-009 共用，但設計決策（OAuth flow、quota 管理、retry 策略、interactive vs batch 模式的接口設計）目前分散在兩個 ADR 裡，且有互相矛盾的假設。把它獨立出來，讓 ADR-008 和 ADR-009 都 reference ADR-009a，才是正確的。

**Brook compose `seo_context` 整合應獨立成 ADR-009b**

D5 觸碰的是 Brook compose 的核心 API contract，影響所有呼叫 `compose_and_enqueue` 的地方。這個決策的影響範圍不亞於 ADR-005a。把它混在 ADR-009 裡，讓 ADR-009 承擔兩個不同的決策（SEO