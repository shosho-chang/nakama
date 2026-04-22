---
source_adr: ADR-005a-brook-gutenberg-pipeline
verification_round: 2
reviewer_model: claude-sonnet-4-6
elapsed_seconds: 93
review_date: 2026-04-22
---

# ADR-005a-brook-gutenberg-pipeline — 修訂驗證（claude-sonnet）

# ADR-005a 修訂版驗證報告

---

## 1. Blocker 逐項檢核

### Blocker #1：Draft Object Schema + Gutenberg Validation 未定義

- **原 blocker**：Brook / Usopp 無共同 schema，無法並行開發；無 block markup validator，LLM 直出 HTML 會靜默寫入髒資料。
- **修訂版回應**：✅ **完整解**
- **證據**：
  - §2 完整定義 `DraftV1`、`GutenbergHTMLV1`、`BlockNodeV1`、`FeaturedImageBriefV1`、`ComplianceFlagsV1` 五個 Pydantic V1 schema，所有欄位型別、constraints、required/optional 都有。
  - §3 LLM 改吐 JSON AST，`gutenberg_builder.py` 純函式序列化，徹底切斷 LLM 與 markup 正確性的耦合。
  - §4 定義 `GutenbergValidator` 五項驗證規則（comment parity、attr JSON、白名單、段落乾淨度、round-trip），失敗路徑 fail fast → DLQ。
  - 開工 Checklist 有對應測試項目涵蓋故意壞 markup 與 extra field。

---

### Blocker #2：無 Staging 環境 + 無測試策略

- **原 blocker**：無 WP test instance，工程師只能在生產 WP（192 篇）上試 API，一次錯誤 payload 即污染資料。
- **修訂版回應**：⚠️ **部分解**
- **證據**：
  - ADR-005a 是 Brook 端的 ADR，WP API 呼叫屬 Usopp（ADR-005b）範疇，因此 staging Docker WP instance 的架設責任合理拆走。
  - **但本 ADR 對自身測試策略仍有缺口**：Checklist 要求「Round-trip test 對 192 篇既有文章」，卻沒說這些文章從哪來（生產 DB dump？anonymized fixture？）、CI 環境是否有辦法跑這個測試。若 CI 無法存取 192 篇，round-trip regression test 就是空頭支票。
  - 尚差：明確聲明 `parse → build` round-trip test 的 fixture 來源（建議：`tests/fixtures/legacy_posts/` 放匿名化 HTML subset），以及 CI pipeline 配置。

---

### Blocker #3：狀態機 / idempotency / 原子性缺失

- **原 blocker**：publish flow 非原子，中途 crash + retry 產生孤兒 post 或重複文章，無 idempotency key。
- **修訂版回應**：✅ **完整解（就 Brook 端範圍而言）**
- **證據**：
  - §Idempotency：`draft_id` 由 `(operation_id, title_hash)` 決定，同一 compose 重跑產生同 `draft_id`，天然冪等。
  - Builder/Validator 均為純函式，無副作用。
  - Brook 不直接寫 WP，寫操作全落 ADR-005b，明確分責。
  - `operation_id` 欄位在 `DraftV1` 落地，貫穿下游。
  - **剩餘風險**：WP 端的 state machine（`pending → post_created → seo_written → published`）責任在 ADR-005b，本 ADR 已合理說明邊界，不視為本 ADR 缺失。

---

### Blocker #4：資安設計（auth + secret + 最小權限）未定義

- **原 blocker**：application password 存儲、輪換、最小權限全無規範；HMAC 作用域不清；REST endpoint 無 rate limit。
- **修訂版回應**：🔄 **拆到別處**
- **證據**：
  - ADR-005a 範疇是 Brook 的 compose pipeline，不含 WP REST 呼叫，因此 auth 設計合理屬 ADR-005b。
  - **但本 ADR 未明確聲明「auth 由 ADR-005b 負責」**，驗收者無法確認這個 blocker 在系統裡有人接下。建議在 §「跟 ADR-005b 的介面」補一行：「WP application password 存儲、最小權限、HMAC 作用域由 ADR-005b §Auth 章定義」。目前留有責任真空。

---

### Blocker #5：VPS 資源 benchmark 未做

- **原 blocker**：4GB RAM 跑 Brook + Usopp + Robin + MySQL 並發，無實測 benchmark，OOM 風險。
- **修訂版回應**：🔄 **拆到別處（但去向不明確）**
- **證據**：
  - ADR-005a 有 SLO 表（Brook p95 < 90 秒），有 SPOF 表（Anthropic API），但沒提 RAM / CPU footprint 估算。
  - §SPOF 表沒列「VPS OOM」項目，這在 reliability.md §4 對應原則下應出現。
  - **差距**：本 ADR 沒說 Brook 的 LLM inference 記憶體佔用上限是多少，也沒說 benchmark 由哪份 ADR 負責。Brooks 的 Anthropic API call 是遠端 inference，本機記憶體壓力相對低，但 Robin PubMed digest 若在同進程就有問題。建議：SPOF 表補「VPS OOM（Brook 進程 + Robin）」一行，並援引負責 benchmark 的 ADR 或 ticket 編號。

---

## 2. 新發現的問題

### 問題 A：`ComplianceFlagsV1` 被 `DraftV1` 使用但定義順序倒置

- **問題描述**：`DraftV1` 在 §2 的 code block 裡使用 `ComplianceFlagsV1`，但 `ComplianceFlagsV1` 的 class 定義卻放在 `DraftV1` 之後（code block 末尾）。Python 直譯器由上到下執行，`DraftV1` 定義時 `ComplianceFlagsV1` 尚未存在，會拋 `NameError`。
- **嚴重度**：**Critical**（copy-paste 進去直接無法 import）
- **建議修法**：`ComplianceFlagsV1` 的定義移到 `DraftV1` 之前。正確順序應為：`BlockNodeV1` → `GutenbergHTMLV1` → `FeaturedImageBriefV1` → `ComplianceFlagsV1` → `DraftV1`。

---

### 問題 B：`DraftV1.tags` 型別為 `list[str]`，但 doc 說「僅既有 tag slug」，無機器可驗證的約束

- **問題描述**：§2 schema 的 `tags` 欄位型別是 `list[str]`，注釋說「僅既有 tag slug」，但 slug 格式沒有 `constr(pattern=...)` 約束。§6 說 Brook 側會過濾，Usopp 側再驗一次，但 `DraftV1` schema 本身不 enforce。若 Brook 的 filter 有 bug，髒 tag 值會通過 Pydantic 驗證，到 Usopp 才爆，違反 fail-fast 原則。
- **嚴重度**：**High**
- **建議修法**：
  ```python
  tags: list[constr(pattern=r"^[a-z0-9-]{2,80}$")] = Field(default_factory=list, max_length=10)
  ```
  另外考慮在 `DraftV1` 的 `model_validator` 加一個針對 `tags` 對照白名單的 validator（白名單在啟動時載入），但白名單 hot-reload 問題複雜，至少先做 slug format validation。

---

### 問題 C：`GutenbergHTMLV1` 同時存 `ast` 和 `raw_html`，但沒有強制保證兩者一致

- **問題描述**：`GutenbergHTMLV1` 有 `ast`（source of truth）和 `raw_html`（給 WP REST 用），但 schema 本身沒有 `model_validator` 驗證 `build(ast) == raw_html`。若有人手動構建這個物件（e.g. 測試 fixture、migration script），兩者可能不同步，WP 存入的是 `raw_html` 而 ADR 說 `ast` 是 source of truth，日後 replay 邏輯會出問題。
- **嚴重度**：**High**
- **建議修法**：加 `model_validator(mode="after")`：
  ```python
  @model_validator(mode="after")
  def ast_and_html_consistent(self) -> "GutenbergHTMLV1":
      expected = gutenberg_builder.build(self.ast)
      if expected.raw_html != self.raw_html:
          raise ValueError("raw_html 與 ast build 結果不一致")
      return self
  ```
  或者更乾脆：`raw_html` 改為 `@computed_field`，在讀取時動態 build，不允許直接傳入，杜絕不一致可能。

---

### 問題 D：Round-trip SLO「> 99%」與「builder 對 192 篇既有文章」的衝突

- **問題描述**：§4 說「validator 成功率 SLO > 99%」，同時 Open Question 2 說「round-trip test 通過率若 < 95%，是修 builder 還是做 legacy 轉換層？」。這兩個數字相互矛盾：如果 192 篇既有文章的 round-trip < 95% 都在考慮之內，那 99% SLO 是針對「新產文章」還是「全部文章」？Migration path 不清楚就先寫 SLO，等於 SLO 是浮動的。
- **嚴重度**：**Medium**
- **建議修法**：SLO 表加一欄「適用範圍」，明確區分：
  - 新產 DraftV1（LLM → builder 路徑）：validator 成功率 > 99%（理論 100%）
  - 既有 192 篇 migration（`parse` 路徑）：先跑 baseline 再定 SLO，Open Question 2 答案決定前不寫數字

---

### 問題 E：`html_raw` block_type 已列入白名單但 Open Question 1 說「建議 disabled」

- **問題描述**：§2 `BlockNodeV1.block_type` 的 `Literal` 枚舉包含 `"html_raw"`，但 Open Question 1 說「建議預設 disabled，Phase 2 再開」。問題在於：Pydantic `Literal` 是靜態定義，`"html_raw"` 在白名單裡就表示 schema 層接受，無法「預設 disabled」。Builder 若遇到 `html_raw` 要怎麼處理，ADR 未說。
- **嚴重度**：**Medium**
- **建議修法**：Phase 1 直接把 `"html_raw"` 從 `Literal` 移除；Phase 2 要用時發新 DraftV2 或透過 feature flag 在 builder 層面處理（builder 收到 `html_raw` 拋 `NotImplementedError`，validator 拒絕）。「schema 允許但邏輯禁止」是一個隱藏炸彈。

---

### 問題 F：`secondary_categories` 型別為 `list[str]`，無白名單約束

- **問題描述**：`primary_category` 用 `Literal` 強型別，但 `secondary_categories: list[str]` 完全沒約束，LLM 可以塞任意字串。Usopp POST 時 WP 會回 400（category not found），但 Draft 本身已過 schema 驗證，錯誤在 Usopp 端才發現，違反 fail-fast。
- **嚴重度**：**Medium**
- **建議修法**：`secondary_categories` 的型別改為與 `primary_category` 相同的 `Literal` 集合，或用 `constr(pattern=r"^[a-z0-9-]+$")` + 在 `model_validator` 對照固定白名單。

---

### 問題 G：Style Profile YAML 的 `blacklist_terms` 與 `ComplianceFlagsV1.reviewed_blacklist_terms` 語義不清

- **問題描述**：§5 style profile YAML 有 `blacklist_terms: [治癒, 根治, 立即見效]`，§2 `ComplianceFlagsV1` 有 `reviewed_blacklist_terms: list[str]`。這個欄位的語義是「命中的詞彙清單」還是「確認沒問題的例外清單」？如果是命中清單，名稱應該是 `detected_blacklist_terms` 或 `flagged_terms`；如果是例外清單，語義更危險，ADR 完全沒解釋。HITL 在 Bridge 看到這個欄位，不知道是警告還是豁免。
- **嚴重度**：**Medium**
- **建議修法**：重新命名並加 docstring：
  ```python
  detected_blacklist_hits: list[str] = Field(
      default_factory=list,
      description="compose 時 regex scan 命中的黑名單詞彙，非空時 Bridge 介面應顯示警告"
  )
  ```
  同時 §7 Compliance guardrail 補充：命中 → raise（不進 queue），或命中 → 進 queue 但 Bridge 強制 warning（兩種策略要選一個）。

---

## 3. 修訂品質評估

| 維度 | 分數 | 一句話評語 |
|---|---|---|
| Schema / 契約完整度 | 8/10 | 五個 V1 schema 完整、constraints 到位、版本欄位齊全，但 class 定義順序有 NameError、`tags` 缺 slug format constraint、`secondary_categories` 無白名單，三個可直接寫 code 時爆炸的細節。 |
| Reliability 機制（idempotency / atomic / SPOF）| 7/10 | `draft_id` idempotency 設計清楚，純函式架構天然冪等；SPOF 表存在但缺 VPS OOM 項目，auth blocker 去向未明確聲明。 |
| Observability（log / metric / SLO / probe）| 8/10 | structured log schema 欄位具體（`draft_id / category / style_profile_id / word_count / llm_tokens / duration_ms`）、SLO 表有數字、metrics 有 histogram/counter；缺 SLO 適用範圍區分（新文章 vs migration），LLM cost 寫入 `usage_log.jsonl` 策略正確。 |
| 可實作性（工程師照寫能不能動） | 7/10 | Builder API 契約、block template、validator 規則描述夠具體，工程師基本能動手；但 `ComplianceFlagsV1` 定義順序錯、`html_raw` 在 schema 但說 disabled、round-trip fixture 來源未說明，這三個會讓工程師卡住問人。 |
| 範圍聚焦度（沒再 scope creep） | 9/10 | 成功把 WP REST 呼叫、auth、staging 環境、Bricks、FluentCRM 都切走，拆三份 ADR 的邊界清楚；Open Question 2 關於 192 篇 migration 的決策延後合理。 |

---

## 4. 最終判定

### **no-go（尚差一步）**

問題 A（`ComplianceFlagsV1` 定義順序倒置）是 Critical 等級，照 ADR 現狀 copy-paste 進去直接 `NameError`，工程師無法 import schema，整個 pipeline 無法啟動。這一個問題就足以 block 開工。

問題 B（`tags` 無 slug format constraint）和問題 C（`GutenbergHTMLV1` 的 `ast`/`raw_html` 一致性無保障）雖是 High 而非 Critical，但它們會在第一週就製造靜默的資料品質 bug，在 CI 測試裡跑過但上 staging 才爆，建議同批修完。

---

**必須先修的 2 個 blocking 項目：**

1. **修正 `ComplianceFlagsV1` 定義順序**（Critical，5 分鐘能修，不修無法 import）：`ComplianceFlagsV1` 定義移到 `DraftV1` 之前，並同時補齊正確的類別順序 `BlockNodeV1 → GutenbergHTMLV1 → FeaturedImageBriefV1 → ComplianceFlagsV1 → DraftV1`。

2. **`GutenbergHTMLV1` 加 consistency validator + 移除 `html_raw` 白名單**（High + Medium 合批）：`raw_html` 改為 `computed_field` 或加 `model_validator` 確保 `build(ast) == raw_html`；`"html_raw"` 從 `BlockNodeV1.block_type` 的 `Literal` 移除，Phase 2 再加。這兩個改動保護 schema 的 source-of-truth 承諾不被打破。

---

**修完這兩點後可開工，Phase 1 實作過程要特別盯的風險：**

1. **Round-trip test 對 192 篇既有文章的通過率**：Open Question 2 的答案會決定 migration 路徑，建議 Week 2 第一件事就跑 `parse → build` baseline，通過率數字出來前 migration SLO 不要寫定，但不能拖到 Week 3 才跑，否則整個 Gutenberg pipeline 的 regression test 沒有地基。

2. **ADR-005b 的 auth blocker 接手確認**：本 ADR 已合理把 auth 設計移給 ADR-005b，但目前沒有任何文字確認 ADR-005b 已接下這個責任。若 ADR-005b 尚未草稿，Brook pipeline 完成後 Usopp 無法安全呼叫 WP REST，整個系統 end-to-end 仍通不了。建議在本 ADR 的「跟 ADR-005b 的介面」章節加一行強制依賴聲明，並在 Phase 1 kickoff 時確認 ADR-005b 的 auth 章已有負責人。