# ADR-005a: Brook Gutenberg Pipeline

**Date:** 2026-04-22
**Status:** Proposed (r2)
**Phase:** Phase 1 Week 2
**Supersedes section of:** [ADR-005](ADR-005-publishing-infrastructure.md)

---

## Context

Brook compose 的最終交付物是一個 draft object，要能被 Usopp（[ADR-005b](ADR-005b-usopp-wp-publishing.md)）無轉換地推到 WordPress。現況兩個關鍵風險：

1. **LLM 直出 Gutenberg block HTML 沒驗證層**。Multi-model review 三家一致指出：WP REST API 對格式錯誤的 block markup 不會報錯，會默寫入資料庫，前台 render 破碎（缺 `<!-- wp:xxx -->` 註解、段落外包錯、class 名稱錯、block 巢狀錯）。Review blocker #1。
2. **Brook ↔ Usopp 沒有 draft schema**，兩個 agent 無法並行開發（Claude 獨到觀點，review blocker）。

本 ADR 定義 Brook 到 Usopp 的介面契約。Usopp 如何實際 POST 到 WP 由 ADR-005b 負責；Bricks template 維護由 [ADR-005c](ADR-005c-bricks-template-maintenance.md) 負責。

## Decision

### 1. Pipeline 分三段

```
compose(topic, context) → DraftV1
                              ↓
                   GutenbergBuilder.build()
                              ↓
                    GutenbergHTMLV1（含 raw_html + ast）
                              ↓
                    GutenbergValidator.validate()
                              ↓
                    若 valid → enqueue 到 approval_queue（ADR-006）
                    若 invalid → 單次 LLM self-correction；再失敗 → fail fast + DLQ
```

Brook 不直接讓 LLM 吐整份 block HTML，改讓 LLM 吐 **結構化 JSON AST**（Claude 替代方案，review §2.2），再由 `shared/gutenberg_builder.py` 確定性地序列化為 block HTML。LLM 只負責內容，不負責 markup 正確性。

### 2. Schema（援引 [schemas.md](../principles/schemas.md)）

存放於 `shared/schemas/publishing.py`，top of file 註明 `ADR-005a`：

Schema 定義順序（依相依性排列，Python 直譯器由上至下載入，被引用者必先定義）：
`BlockNodeV1` → `GutenbergHTMLV1` → `FeaturedImageBriefV1` → `DraftComplianceV1` → `DraftV1`。

```python
# shared/schemas/publishing.py — ADR-005a / ADR-005b

# AST 遞迴深度上限，防 LLM 產生極深巢狀造成 RecursionError / DoS
MAX_AST_DEPTH = 6


class BlockNodeV1(BaseModel):
    """Gutenberg AST 單一 block 節點"""
    model_config = ConfigDict(extra="forbid", frozen=True)
    # Phase 1 白名單：`html_raw` 暫不開放（Open Question 1），
    # Phase 2 有明確需求時升 V2 並同步調整 builder/validator。
    block_type: Literal[
        "paragraph", "heading", "list", "list_item",
        "quote", "image", "code", "separator"
    ]
    attrs: dict[str, str | int | bool] = Field(default_factory=dict)
    content: str | None = None
    children: list["BlockNodeV1"] = Field(default_factory=list)


def _ast_depth(nodes: list[BlockNodeV1]) -> int:
    if not nodes:
        return 0
    return 1 + max((_ast_depth(n.children) for n in nodes), default=0)


class GutenbergHTMLV1(BaseModel):
    """
    `ast` 是 source of truth；`raw_html` 必須等於 `gutenberg_builder.build(ast)` 的輸出。
    任何手動構建（測試 fixture、migration script）若兩者不一致，schema 層即 fail fast。
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    ast: list[BlockNodeV1]              # source of truth
    raw_html: str                       # serialized output（給 WP REST `content` 欄位）
    validator_version: str              # "gutenberg_builder_0.1.0"

    @model_validator(mode="after")
    def _ast_depth_within_limit(self) -> "GutenbergHTMLV1":
        depth = _ast_depth(self.ast)
        if depth > MAX_AST_DEPTH:
            raise ValueError(
                f"AST depth {depth} 超過上限 {MAX_AST_DEPTH}（防遞迴 DoS）"
            )
        return self

    @model_validator(mode="after")
    def _ast_and_html_consistent(self) -> "GutenbergHTMLV1":
        # 動態 import 避免 schema 模組循環依賴
        from shared import gutenberg_builder
        expected = gutenberg_builder.build(self.ast)
        if expected.raw_html != self.raw_html:
            raise ValueError("raw_html 與 build(ast) 結果不一致，ast 是 source of truth")
        return self


class FeaturedImageBriefV1(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    purpose: Literal["hero", "inline", "social"]
    description: constr(min_length=10, max_length=500)
    style: str
    keywords: list[str]


class DraftComplianceV1(BaseModel):
    """
    Brook compose 階段的合規狀態快照（regex scan + LLM self-check 共同填寫）。

    與 ADR-005b §10 的 `PublishComplianceGateV1` 不同：
    - `DraftComplianceV1`（本 schema）：compose 期 snapshot，描述「Brook 寫時有沒有避開療效、有沒有加免責」
    - `PublishComplianceGateV1`（ADR-005b §10）：publish gate scan 結果，Brook enqueue + Usopp claim 各掃一次
    兩者皆存於 DraftV1 與 ApprovalPayloadV1，分別代表不同階段的合規視角。

    `detected_blacklist_hits` 非空時 Bridge HITL 應顯示警告。非例外清單、非豁免清單。
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    claims_no_therapeutic_effect: bool  # 未聲稱療效
    has_disclaimer: bool
    detected_blacklist_hits: list[str] = Field(
        default_factory=list,
        description="compose 時 regex scan 命中的黑名單詞彙，非空時 Bridge 應顯示警告"
    )


# 既有 category / tag slug 格式：小寫英數、連字號、CJK
_SLUG_PATTERN = r"^[a-z0-9一-鿿][a-z0-9\-一-鿿]*$"


class DraftV1(BaseModel):
    """Brook → approval_queue → Usopp 的核心 contract"""
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    draft_id: constr(pattern=r"^draft_\d{8}T\d{6}_[0-9a-f]{6}$")
    created_at: AwareDatetime
    agent: Literal["brook"]
    operation_id: constr(pattern=r"^op_[0-9a-f]{8}$")
    # 內容
    title: constr(min_length=5, max_length=120)
    slug_candidates: list[constr(pattern=r"^[a-z0-9-]{3,80}$")] = Field(min_length=1, max_length=3)
    content: GutenbergHTMLV1
    excerpt: constr(min_length=20, max_length=300)
    # 分類
    primary_category: Literal[
        "blog", "podcast", "book-review", "people",
        "neuroscience", "sport-science", "nutrition-science",
        "weight-loss-science", "sleep-science", "emotion-science",
        "longevity-science", "preventive-healthcare", "productivity-science"
    ]
    secondary_categories: list[constr(pattern=_SLUG_PATTERN, max_length=50)] = Field(
        default_factory=list, max_length=2
    )
    # tags 僅既有 slug；schema 強制 slug 格式（CJK + a-z0-9-），白名單比對在 compose 層
    tags: list[constr(pattern=_SLUG_PATTERN, max_length=50)] = Field(
        default_factory=list, max_length=10
    )
    # SEO
    focus_keyword: constr(min_length=2, max_length=60)
    meta_description: constr(min_length=50, max_length=155)
    # 圖片（Phase 1 人工，featured_media_id 在 Bridge approve 時填）
    featured_image_brief: FeaturedImageBriefV1 | None = None
    # Compliance（review §2.11）
    compliance: DraftComplianceV1
    # Style profile 來源
    style_profile_id: constr(pattern=r"^[a-z0-9-]+@\d+\.\d+\.\d+$")  # e.g. "book-review@0.1.0"

    @model_validator(mode="after")
    def _tags_unique(self) -> "DraftV1":
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("tags 不可重複")
        if len(self.secondary_categories) != len(set(self.secondary_categories)):
            raise ValueError("secondary_categories 不可重複")
        return self
```

所有欄位遵守 schemas.md 第 3/4/5/7 條：`schema_version` 必填、`extra="forbid"`、`AwareDatetime`、`Literal` 取代 str。

**實作備註**：
- `raw_html` 另一個等效作法是用 `@computed_field`，但為了 DB round-trip 方便（直接 store `raw_html` 給下游 Usopp POST 使用而不必 re-build），本 ADR 選擇「兩欄並存 + `model_validator` 守恆」。
- `secondary_categories` 僅驗 slug 格式，白名單比對（避免 WP 端 category not found）在 compose 層次做，與 tags 策略一致。
- **`_ast_and_html_consistent` 遞迴陷阱**：此 validator 呼叫 `gutenberg_builder.build()`，而 `build()` 本身若走一般 `GutenbergHTMLV1(...)` 建構會再觸發此 validator → `build()` → …無限遞迴。實作守則：`gutenberg_builder.build()` 是 canonical constructor，**必須用 `model_construct()` 繞 validator**（跳過 Pydantic 驗證）。`model_validator` 僅在外部手動建構場景生效（tests、migration、LLM 直出的 `html_raw` 反序列化），守護「ast 與 raw_html 不同步」這個錯誤狀態被檢出。

### 3. Gutenberg Builder API 契約

`shared/gutenberg_builder.py`：

```python
def build(ast: list[BlockNodeV1]) -> GutenbergHTMLV1:
    """AST → raw_html；純函式，無 LLM。保證 output validator 必過。"""

def parse(raw_html: str) -> list[BlockNodeV1]:
    """raw_html → AST；用於 roundtrip 測試與既有 192 篇 migration。"""
```

Builder 對每個 `block_type` 有固定模板，例：

```python
# paragraph
<!-- wp:paragraph -->
<p>{escaped_content}</p>
<!-- /wp:paragraph -->

# heading level 2
<!-- wp:heading {"level":2} -->
<h2 class="wp-block-heading">{escaped_content}</h2>
<!-- /wp:heading -->
```

未知 block_type 直接 raise，不嘗試猜測。

### 4. Gutenberg Validator（review blocker #1）

`shared/gutenberg_validator.py`：

```python
class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    valid: bool
    errors: list[ValidationErrorV1]

def validate(html: str) -> ValidationResult:
    """檢查所有 `<!-- wp:xxx -->` 必有對應 `<!-- /wp:xxx -->`；
    blocks 不交錯；attrs JSON 合法；allowed block_type 白名單；
    段落內不含 block-level 標籤。"""
```

策略：

- **白名單 block_type**（與 builder 一致，未列入的 block 一律 reject）
- **Comment parity check**：`<!-- wp:X -->` 數量 = `<!-- /wp:X -->` 數量且配對
- **Attr JSON 驗證**：`<!-- wp:heading {"level":2} -->` 內的 JSON 必 parsable
- **段落乾淨度**：`<p>` 內不允許 `<h1>`/`<h2>`/`<div>`/block comment
- **AST 深度上限**：parse 後 AST 深度 ≤ `MAX_AST_DEPTH`（6），超過即 reject，防 LLM 產無限嵌套
- **Round-trip check**：`parse(raw_html)` → `build(ast)` 必須 `==` raw_html（faithful serializer）

**成功率 SLO**：> 99%（build output 理論上應 100%，validator 專門擋 builder bug + 未預期 LLM raw_html 注入）。

### 5. 三類 Style Profile 銜接

Brook 依 `primary_category` 查 `config/style-profiles/{category}.yaml`：

```yaml
# config/style-profiles/book-review.yaml
id: book-review
version: 0.1.0
extends: default
tone:
  voice: "第一人稱思辨，偶爾自嘲"
  sentence_length_target: medium
structure:
  required_sections: [hook, synopsis, three_takeaways, personal_angle, recommendation]
  heading_levels: [h2, h3]
compliance:
  blacklist_terms: [治癒, 根治, 立即見效]   # review §2.11
  require_disclaimer_if_any_of: [劑量, 治療, 診斷]
```

Phase 1 三個 profile：`book-review`、`people`、`science`（9 個 science sub 共用 `science` profile，透過 variant 欄位區分）。

`style_profile_id` 在 DraftV1 裡落地，版本化（`book-review@0.1.0`）讓事後能 replay。

### 6. Tag 策略（review §2.8）

- Phase 1 **絕對禁止** Brook 產生新 tag（採 Gemini 建議）
- Brook 拿到既有 497 tag list 的壓縮版（只傳該 category 下常用的前 50 個），避免 token 爆炸
- LLM 建議的 tag 不在既有清單 → 該 tag 丟棄，log `WARNING tag_not_found`，不進 draft

### 7. Compliance Guardrail（review §2.11）

- Style profile 的 `blacklist_terms` 在 compose 後做 regex scan，命中 → raise + log
- Draft schema 的 `DraftComplianceV1` 必填；Bridge HITL checklist 再確認一次

## Consequences

### 正面
- Brook / Usopp 可並行開發（schema 是硬契約）
- LLM 不負責 markup → block syntax 破碎機率接近零
- Style profile 版本化 → 日後調整能 replay 追蹤
- Gutenberg validator = migration 192 篇既有文章的工具

### 風險與緩解

| 風險 | 緩解 |
|---|---|
| LLM 吐 AST 超出 block_type 白名單 | `ask_llm_structured` with Pydantic schema，JSON schema 強制 enum；失敗 retry 一次 |
| `tags` 欄位 LLM 亂填不存在的 slug | Brook 側先做 filter，Usopp 側再驗一次 |
| 497 tag 全塞 prompt → token 爆 | 傳 category 相關前 50 個 + Robin 擷取 concept 交集 |
| Style profile YAML 改版無通知 | `style_profile_id` 帶版本，改版時升版號並保留舊檔 |

### SPOF（依 [reliability.md](../principles/reliability.md) §4）

| SPOF | 影響 | 緩解 |
|---|---|---|
| Anthropic API | compose 全停 | Phase 1：Sentry alert 401/429；Phase 2：Gemini fallback |
| `config/style-profiles/*.yaml` | compose 無 style | 啟動時預載並 schema validate；檔案缺失 fail fast 不降級 |
| Gutenberg builder/validator bug | 所有 draft invalid | CI 對 192 篇既有文章做 parse→build round-trip regression test |

### Idempotency（依 reliability.md §1）

- `draft_id` 由 `(operation_id, title_hash)` 決定，同一 compose 重跑產生同 draft_id
- Builder/Validator 均為純函式無副作用，天然冪等
- Brook 不直接寫 WP，寫操作落在 Usopp（ADR-005b）

### Schema Version

- `DraftV1`、`GutenbergHTMLV1`、`BlockNodeV1`、`FeaturedImageBriefV1`、`DraftComplianceV1` 皆 v1
- 未來若改 AST 結構 → V2 + migrator（schemas.md §3 流程）

## SLO（依 [observability.md](../principles/observability.md) §5）

| 指標 | 適用範圍 | 目標 |
|---|---|---|
| Brook 單篇 compose（含 AST build） | 新產 DraftV1 | p95 < 90 秒 |
| Gutenberg validator 成功率 | 新產 DraftV1（LLM → builder 路徑） | > 99%（理論 100%） |
| Draft schema 驗證失敗率 | 新產 DraftV1 | < 1%（失敗視為 compose failure） |
| Blacklist term 漏網率（抽檢） | 新產 DraftV1 | 0（硬規則） |
| Round-trip（`parse → build == identity`）通過率 | 既有 192 篇 legacy migration | Phase 1 Week 2 跑 baseline 後再定（Open Question 2） |

## 資安考量

雖然 ADR-005a 不直接呼叫 WP REST，LLM 生成內容仍可能引入 XSS / 注入向量，下游 Usopp POST 後由 WP render 到瀏覽器就會執行。本 ADR 守住「draft 內容本身的 sanitization」邊界：

1. **LLM output escape**：
   - `gutenberg_builder` 對 `content` 欄位一律 `html.escape()`，避免 `<script>` / `<iframe>` / `<object>` 等 DOM attack vector 透過 AST 注入。
   - `attrs` 欄位的 value 若含 `<`、`>`、`"`、`'`、`javascript:`、`data:` 前綴 → builder raise，validator reject。
   - 禁止 `on*` 事件屬性（`onclick`、`onerror`、`onload` 等）出現在任何 `attrs` key 中；builder 層直接 blacklist。
2. **`html_raw` block 已從 Phase 1 白名單移除**（見 §2 schema），杜絕 LLM 透過逃生艙繞過 escape 的可能。
3. **Compliance flag 審核路徑**：
   - `DraftComplianceV1` 的 bool 值由 Brook compose 階段的 regex scan + LLM self-check 共同填寫，不由 LLM 單獨決定。
   - `detected_blacklist_hits` 非空時，即使 `claims_no_therapeutic_effect=True` 也必須進 Bridge HITL 並以 WARNING 顯示（防 LLM 虛假 compliance）。
   - Validator 在 build 後再跑一次 blacklist regex scan，若命中數與 `detected_blacklist_hits` 不符 → raise（防 schema 與實際內容脫節）。
4. **Secret 隔離**：Brook 不持有任何 WP credential；所有 WP 相關 secret 由 ADR-005b 管理，Brook 的 `.env` 只有 LLM provider key。
5. **AST 遞迴 DoS**：`MAX_AST_DEPTH = 6` 在 schema 層擋，validator 層再驗一次（雙層保險，見 §2/§4）。
6. **Compliance 審核路徑（外部）**：Bridge HITL 看到 `detected_blacklist_hits` 非空或 `has_disclaimer=False` 時，必須強制人工 approve 才能進入 ADR-006 queue 下游；此規則在 ADR-006 approve flow 落地，本 ADR 僅規範 schema 與 compose 端語義。

## 開工 Checklist

**Schema（schemas.md）**
- [ ] `shared/schemas/publishing.py` 完整定義五個 V1 schema，top-of-file 註明 ADR-005a/005b
- [ ] `schema_version` 欄位全部 Literal 固定
- [ ] 所有 schema `extra="forbid"` + `frozen=True`
- [ ] `AwareDatetime` 強制 tzinfo
- [ ] ID 欄位 `constr(pattern=...)`

**Reliability（reliability.md）**
- [ ] `draft_id` idempotency key 設計（§1）
- [ ] LLM call timeout = 60 秒（§7）
- [ ] LLM structured output 失敗 retry 1 次 + DLQ（§5, §8）
- [ ] Style profile 檔案缺失 fail fast，不默默 fallback
- [ ] SPOF 表列入

**Observability（observability.md）**
- [ ] 每篇 compose 生成 `operation_id`（§2），貫穿 Brook 所有下游呼叫
- [ ] Structured log 記 `draft_id / category / style_profile_id / word_count / llm_tokens / duration_ms`（§1）
- [ ] LLM cost 寫入 `data/usage_log.jsonl`（§10）
- [ ] `compose_duration_ms` histogram、`gutenberg_validation_failures_total` counter 寫 metrics_timeseries（§1 Metric 表）
- [ ] Blacklist hit 發 WARNING log

**測試**
- [ ] Gutenberg builder 單元測試（每個 block_type）
- [ ] Round-trip test：`parse → build == identity` 對 192 篇既有文章（fixture 放 `tests/fixtures/legacy_posts/` 匿名化 subset）
- [ ] Validator 對故意壞 markup（缺 closing comment、nested 錯、非白名單 block）回 invalid
- [ ] Pydantic schema 的 extra field 丟 ValidationError
- [ ] Compliance blacklist regex 命中測試

**資安（見「資安考量」章節）**
- [ ] `content` 欄位含 `<script>` / `<iframe>` / `<object>` → builder escape 後 validator 仍通過；實際字面輸出須為 `&lt;script&gt;` 不含真 tag
- [ ] `attrs` value 含 `javascript:` / `data:` 前綴 → builder raise
- [ ] `attrs` key 為 `onclick` / `onerror` / `onload` → builder raise
- [ ] AST 深度 = 7（超過 `MAX_AST_DEPTH`）→ schema ValidationError
- [ ] `raw_html != build(ast).raw_html` → schema ValidationError（consistency validator）
- [ ] `tags` 含空字串 / 含空格 / 含 `<` → slug constraint ValidationError
- [ ] `tags` 含重複元素 → `_tags_unique` ValidationError
- [ ] `detected_blacklist_hits` 與 content 實際命中不符 → validator raise

## 跟 ADR-005b 的介面

Brook 產出 `DraftV1` 寫入 `approval_queue`（ADR-006）。Usopp 從 queue claim 時讀取的型別就是 `DraftV1`。Brook 不呼叫 Usopp，兩者不共享 process 內記憶體。

**責任分工（避免兩邊以為對方做）**：

| 項目 | 負責 ADR |
|---|---|
| `DraftV1` schema 與 builder/validator 本身的單元測試 | ADR-005a（本 ADR） |
| Staging WP instance（Docker WP + MySQL）、端到端整合測試 | **ADR-005b §8** |
| WP application password 存儲、輪換、最小權限、HMAC 作用域、REST rate limit | **ADR-005b §Auth** |
| VPS RAM/CPU footprint benchmark（Brook + Usopp + Robin + MySQL 並發） | **ADR-005b / 整體 infra ticket** |
| `draft_id` 作為下游 idempotency key 的契約 | 本 ADR 提供，ADR-005b 消費 |
| 既有 192 篇 round-trip fixture 來源（`tests/fixtures/legacy_posts/` 匿名化 subset）與 CI 配置 | 本 ADR 提供 fixture 定義，CI runner 配置 ADR-005b |

**強制依賴聲明**：本 ADR 的開工前置條件包含 ADR-005b §Auth 章已有負責人、ADR-005b §8 staging 計畫已定案；若 ADR-005b 尚未草稿，Brook pipeline 可獨立實作並以單元測試驗證，但不得 end-to-end 接 WP 上線。

## Open Questions

1. ~~`html_raw` block（AST 裡允許 LLM 插入 raw HTML 的逃生艙）要不要 Phase 1 就開？~~ **（r2 已解）** Phase 1 直接從 `BlockNodeV1.block_type` 的 `Literal` 移除；Phase 2 有需求時發 DraftV2 或於 builder 層走 feature flag。
2. Round-trip test 對 192 篇既有文章的通過率若 < 95%，是該修 builder 還是該做 legacy 轉換層？—— 先測再決定（Week 2 第一件事跑 baseline，通過率數字出來前 migration SLO 留空）。

## Notes

- 本 ADR 拆自 ADR-005，回應 multi-model review §2.2、§3 Claude 獨到觀點、§5 blocker #1
- 2026-04-22 提出

## 修訂歷程

### r2 — 2026-04-22

根據 multi-model verification（Claude Sonnet / Gemini / Grok）反饋修訂：

- **Critical 修**：重排 §2 schema 定義順序為 `BlockNodeV1 → GutenbergHTMLV1 → FeaturedImageBriefV1 → DraftComplianceV1 → DraftV1`，修正 `DraftComplianceV1` 被 `DraftV1` 引用時尚未定義的 `NameError`（Claude Sonnet 問題 A）。
- **High 修**：
  - `DraftV1.tags` 與 `secondary_categories` 加 slug `constr(pattern=_SLUG_PATTERN)` 約束，並加 `_tags_unique` model_validator 去重（Claude Sonnet 問題 B、Grok tag 去重）。
  - `GutenbergHTMLV1` 加 `_ast_and_html_consistent` model_validator，確保 `raw_html == build(ast).raw_html`，`ast` 為 source of truth（Claude Sonnet 問題 C）。
  - 新增「跟 ADR-005b 的介面」責任分工表與強制依賴聲明，明確 staging（ADR-005b §8）、auth（ADR-005b §Auth）、VPS benchmark 歸屬（Grok blocker 2/4/5、Claude Sonnet blocker 4/5）。
- **Medium 修**：
  - 新增 `MAX_AST_DEPTH = 6` 常數與 `_ast_depth_within_limit` validator、validator 層再驗，防遞迴 DoS（Gemini 問題 1）。
  - 新增「資安考量」章節（放在 SLO 後），涵蓋 LLM output escape、`javascript:` / `data:` / `on*` blacklist、compliance 審核雙重驗證、secret 隔離（Grok blocker 4、Claude Sonnet 問題 G）。
  - SLO 表加「適用範圍」欄，區分新產 DraftV1 vs 既有 192 篇 migration；既有篇 round-trip 通過率改為「Week 2 baseline 後再定」（Claude Sonnet 問題 D）。
  - `DraftComplianceV1.reviewed_blacklist_terms` 改名 `detected_blacklist_hits` 並加 docstring，釐清語義為命中清單非豁免清單（Claude Sonnet 問題 G）。
  - 從 `BlockNodeV1.block_type` 移除 `"html_raw"`，關閉 schema 允許但邏輯禁止的隱藏炸彈；Open Question 1 標示已解（Claude Sonnet 問題 E、Grok 問題 4）。
  - 開工 Checklist 測試段補 8 項資安與一致性測試。
- **未引入新風險**：所有變更均為 narrowing（更嚴格的約束、更明確的責任分工），不改變 pipeline 架構，builder/validator API 簽名、DraftV1 對 Usopp 的欄位契約形狀不變。`detected_blacklist_hits` 命名改動為語義修正（原 `reviewed_blacklist_terms` 語義不清，實作尚未落地故無 migration 成本）。
