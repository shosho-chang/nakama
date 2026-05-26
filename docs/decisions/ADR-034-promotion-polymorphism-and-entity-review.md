# ADR-034: Promotion Polymorphism Unification + Entity Review

**Date:** 2026-05-26
**Status:** Draft v2 (post multi-agent-panel review 2026-05-26; pending 修修 sign-off)
**Owner:** 修修
**Related:** [ADR-024](ADR-024-source-promotion-and-reading-context-package.md) (Source Promotion) · [ADR-020](ADR-020-textbook-ingest-v3-rewrite.md) (textbook ingest v3) · `improve-codebase-architecture` grill 2026-05-26

> **v1 → v2 change log** — v1 went through a focused short panel (Codex GPT-5 medium + Gemini 2.5 Pro). Panel verbatim audits at:
> - [`docs/research/2026-05-26-codex-adr034-audit.md`](../research/2026-05-26-codex-adr034-audit.md)
> - [`docs/research/2026-05-26-gemini-adr034-audit.md`](../research/2026-05-26-gemini-adr034-audit.md)
> - [`docs/research/2026-05-26-adr034-panel-integration-matrix.md`](../research/2026-05-26-adr034-panel-integration-matrix.md)
>
> Integration matrix in §Panel Integration. Top v2 deltas:
>
> 1. **D2 reframed** — `entity_metadata: dict[str, Any]` rejected by both auditors as Schema-on-Read regression / "smearing complexity". Replaced with **discriminated metadata union** (`Annotated[Union[PersonMetadata, OrganizationMetadata], Field(discriminator="entity_type")]`) — extends existing repo pattern at `promotion_manifest.py:321` instead of inventing a new shape.
> 2. **D3 reframed** — `functools.singledispatch` deprioritized; **`match` statement** is the primary dispatch mechanism. Reasoning: (a) Gemini caught toxic D2+D3 interaction (singledispatch + dict[str, Any] = "fake polymorphism" via double-dispatch), (b) `match` is more explicit / LLM-readable / static-exhaustiveness-friendly, (c) repo has no mypy/pyright config so singledispatch type-narrowing claim was theoretical.
> 3. **Book removed from gated enum** — Codex caught §D1/§D2 contradiction (D1 says Book bypasses gate, D2 included `book` in gated enum). PR2 `EntityReviewItem` covers Person + Organization only; Book stays in `kb_writer.write_book_entity()` exclusively. Place deferred to future.
> 4. **Dispatch defaults must RAISE** — never silent return empty list / None. `case _: raise NotImplementedError(...)` is the idiom.
> 5. **D2+D3 are coupled** — Gemini novel insight: cannot evaluate schema choice and polymorphism choice independently. v2 framing treats them as one decision.
> 6. **mypy/pyright claim dropped** from §Negative — `pyproject.toml` has Ruff + pytest but no static type checker; v1's "narrowing benefits" was theoretical.

---

## Context

ADR-024 凍結 Source Promotion 為 Reading Source → KB 的審批管線，產出兩種 `ReviewItem` subtype：

- `SourcePageReviewItem` — 一整章 / 一頁 source 升格為 `KB/Wiki/Sources/...`
- `ConceptReviewItem` — 從 source 抽出的 concept 升格為 `KB/Wiki/Concepts/...`

實作落在 `shared/promotion_*` 五個 module（acceptance_gate / renderer / commit / review_service / preflight）。

**Schema 既有約定（重要）：** `shared/schemas/promotion_manifest.py:319-322` 已用 Pydantic v2 discriminated union pattern：

```python
ReviewItem = Annotated[
    Union[SourcePageReviewItem, ConceptReviewItem],
    Field(discriminator="item_kind"),
]
```

新加 ReviewItem subtype 必須延續這個 pattern，不應該另發明 `dict[str, Any]` 逃逸艙。v1 漏看這條既有約定，v2 修正。

### 觸發此 ADR 的 grill（2026-05-26）

`improve-codebase-architecture` audit 點出兩個摩擦：

1. **`_resolve_target_path` 雙寫** — `gate.py:115-128`（inline isinstance branches）跟 `commit.py:501-520`（helper `_resolve_target_path()`）各自實作「從 ReviewItem 拿 target_kb_path」的邏輯。兩處邏輯本應一致，但無 single source — 一邊改另一邊忘記同步是 silent bug 風險。Codex 已驗證行號正確。
2. **isinstance ladder 散在 3 個 module** — `SourcePageReviewItem` vs `ConceptReviewItem` 的 type-discriminated 分支總計 ~6 處跨 gate / renderer / commit。新增 subtype 要動三檔。

Grill 還浮現一個直接相關的設計題：**第三種 ReviewItem subtype 短期內會不會出現？**

修修在 grill 中描述了 YouTube / Podcast Reader 的需求 — 看影片時即時對轉錄稿 highlight + annotate，看完後 promote 有價值的內容進 KB。順帶想搜「**哪個人**在哪部影片講過什麼」。這個 use case 帶出 Person / Organization Entity 的 cross-source 統一管理需求。

目前 Book Entity 走 `kb_writer.write_book_entity()` 完全繞過 promotion gate（textbook ingest 完直接寫檔）— 這條路徑 v2 維持不變。Person / Organization Entity 還沒有 first-class 機制，這是 PR2 要補的。

## Decision

凍結三條方向（D2 + D3 在 v2 panel 後同步調整）：

### D1. Entity 採 Hybrid Gate

| Entity 類型 | 進 promotion gate？ | 理由 |
|---|---|---|
| **Book Entity** | ❌ Auto-create | 修修主動 ingest = approved by definition；gate 為純 ceremony；走 `kb_writer.write_book_entity()` 既有路徑 |
| **Person Entity** | ✅ Gate + fast-track | 拼字變體 / 別名 / 一次性引用 disambig 需要人類判斷；YouTube/Podcast 場景帶來大量 Person surface |
| **Organization Entity** | ✅ Gate + fast-track | 同 Person，confidence fast-track 更積極（拼字變體少） |
| **Place / Product Entity** | 🔜 Deferred | 目前無確切 use case，PR2 不包含；未來真出現再開新 metadata 變體加進 union |

**Confidence-based fast-track**（套用既有 `canonical_match.confidence` 欄位）：

```
confidence > 0.9   → auto-approve（不進 UI review queue，仍記 manifest）
0.5 ≤ confidence ≤ 0.9 → 進 UI review queue
confidence < 0.5  → 進 review queue 但預設 defer
```

LLM 變強時 threshold 可調高、queue 自然縮小 — 架構不變。

### D2 (v2). EntityReviewItem 採 discriminated metadata union

延續 `promotion_manifest.py:319-322` 既有 `Field(discriminator="item_kind")` pattern。`EntityReviewItem` 仍是 top-level `ReviewItem` union 的一員（讓 promotion gate / review queue UI 看到「一種 entity 卡片」），但 entity-specific 欄位用**typed metadata 子類別** + Pydantic discriminator，**不用 `dict[str, Any]`**：

```python
class PersonMetadata(BaseModel):
    entity_type: Literal["person"] = "person"
    affiliation: str | None = None
    role: str | None = None
    birth_year: int | None = None
    credentials: list[str] = Field(default_factory=list)

class OrganizationMetadata(BaseModel):
    entity_type: Literal["organization"] = "organization"
    org_type: Literal["academic", "company", "government", "ngo"] | None = None
    jurisdiction: str | None = None
    website: str | None = None
    parent_org: str | None = None

EntityMetadata = Annotated[
    Union[PersonMetadata, OrganizationMetadata],
    Field(discriminator="entity_type"),
]

class EntityReviewItem(BaseModel):
    item_kind: Literal["entity"] = "entity"
    item_id: str
    entity_label: str
    aliases: list[str] = Field(default_factory=list)
    metadata: EntityMetadata
    canonical_match: EntityCanonicalMatch | None = None
    evidence: list[EvidenceAnchor]
    # ...共用欄位（source_id, recommendation, confidence, source_importance,
    #            reader_salience, human_decision, promoted_at, ...）

# Top-level union extended (PR2):
ReviewItem = Annotated[
    Union[SourcePageReviewItem, ConceptReviewItem, EntityReviewItem],
    Field(discriminator="item_kind"),
]
```

**為什麼用 discriminated metadata union 而非完全拆 PersonReviewItem / OrgReviewItem 獨立 class（Gemini 的 Option 1，Codex 的 secondary recommendation）：**

- 上層 `ReviewItem` union 維持 3 個 member（Source / Concept / Entity）— review queue UI、gate workflow、manifest schema 都看到「Entity 是一種」
- Entity 之間的共通行為（canonical_match disambig、confidence fast-track、cross-source backlink）走 single code path
- entity-specific 欄位仍有 type safety（Pydantic 在 boundary validate metadata 結構）
- 加新 entity 類型 = 加一個 Metadata class 進 union，不動上層 `ReviewItem`

**拒絕 v1 路 A（`dict[str, Any]`）的理由（panel 共識）：**

- 失敗 deletion test — 複雜度不會消失，會 smear 到 renderer / engine / gate / tests / UI 的 string-key checks（Codex §Section 2）
- Schema-on-Read regression — Pydantic 的價值是 boundary validation；`dict[str, Any]` 把驗證 push 給每個 consumer（Gemini §Section 1）
- ConceptReviewItem precedent 不成立 — Concept 有單一 metadata shape，Entity 各 type 形狀差異實質（Person 的 affiliation vs Book 的 ISBN）
- 跟既有 `Field(discriminator="item_kind")` pattern 不一致 — 為了一致性投資 0.5d schema 設計時間，省下未來 ~2-3d dict-key drift debug

**拒絕完全拆 5 個獨立 ReviewItem class（路 B 原版）的理由：**

- 上層 `ReviewItem` union 變 5 個 member → gate / commit / review_service 的通用流程要在 5 case 之間共享邏輯
- Entity 之間的「review queue 是 entity」這個 affordance 在 schema 階層消失
- discriminated metadata union 已給足 type safety，不需要再拆上層

**最終 ReviewItem subtype 數：3 種** （SourcePage / Concept / Entity），entity 內部 metadata 變體 2 種（Person / Organization）。

### D3 (v2). 多型 dispatch 走 `match` statement，不走 `functools.singledispatch`

每個 concern（target 解析、渲染、type-specific 驗證）在自己的 module 內用 `match` 對 `ReviewItem` 做 outer dispatch。EntityMetadata 變體則由 inner `match` 處理。

**示例：**

```python
# shared/promotion_targets.py（新檔案）
def resolve_target_path(item: ReviewItem, vault_root: Path) -> str | None:
    """Single source of truth — 解 gate.py 跟 commit.py 雙寫。"""
    match item:
        case SourcePageReviewItem(target_kb_path=p):
            return p
        case ConceptReviewItem(canonical_match=cm) if cm and cm.matched_concept_path:
            return cm.matched_concept_path
        case ConceptReviewItem():
            return None
        case EntityReviewItem(metadata=meta):
            return _entity_target_path(item.entity_label, meta)
        case _:
            raise NotImplementedError(
                f"No target resolver for ReviewItem subtype: {type(item).__name__}"
            )


def _entity_target_path(label: str, metadata: EntityMetadata) -> str:
    """Inner dispatch on metadata variant."""
    slug = _slugify(label)
    match metadata:
        case PersonMetadata():
            return f"KB/Wiki/Entities/People/{slug}.md"
        case OrganizationMetadata():
            return f"KB/Wiki/Entities/Organizations/{slug}.md"
        case _:
            raise NotImplementedError(
                f"No target path for EntityMetadata variant: {type(metadata).__name__}"
            )
```

同 pattern 套用到：
- `render_review_item(item, manifest) -> str` in `shared/promotion_renderer.py`
- `validate_type_specific_invariants(item) -> list[AcceptanceFinding]` in `shared/promotion_acceptance_gate.py`

**`case _: raise` 是必要紀律（Codex §Section 3 + integration matrix item #4）：**

- 漏 register 新 subtype 時 — `match` 走 `case _:` 顯式 raise，不會 silent 走 default empty list / None
- 比 `isinstance` ladder 更安全 — `match` 對 Python `__match_args__` 結構化解構驗證
- 比 `singledispatch` 更安全 — singledispatch 漏 register 默默走 base function；要靠額外測試強制 register hygiene

**拒絕 `functools.singledispatch` 的理由（v2 panel 修正）：**

- **D2+D3 toxic 耦合（Gemini novel insight）** — singledispatch 對 `EntityReviewItem` 粗粒度 dispatch 之後仍需 inner `entity_type` switch，變 "double-dispatch" anti-pattern。`match` 一層直接到 `PersonMetadata` / `OrganizationMetadata`
- **LLM-readability** — nakama codebase 高度被 LLM 編輯（Claude / Codex）。`@register` 是 non-local（要全域搜 `@render_review_item.register`），`match` 是 local + explicit。LLM agent 加新 subtype 時 trace 控制流更直接
- **Type-checker reality** — repo 無 mypy / pyright config（`pyproject.toml` 只有 Ruff + pytest），v1 「singledispatch 對 mypy narrowing 友善」是 theoretical claim；`match` 同樣需要 static analyzer 才有 exhaustiveness 保證，但 Python 3.10+ 對 `match` 的 IDE support 更普遍
- **Register hygiene** — singledispatch 漏 register 預設 fallback；`match` `case _: raise` 是強制顯式

**拒絕 「方法掛在 Pydantic base class」 的理由（不變）：**

- `shared/schemas/` 既有慣例為純 data class，不 import behavior modules（yaml / formatter / evidence renderer）
- determinism 契約（renderer 兩次 run byte-identical）屬於 renderer module，不該 leak 進 schema

> **註：** Gemini §Section 3 push 回「schema purity 是 dogmatic 信念」這個論點有理；但本 ADR 仍維持 schema 不 import behavior 的紀律，理由是 v2 改用 `match` 之後 dispatch 已經 explicit + local，加 method-on-model 帶不來額外 locality，反而模糊 schema/behavior 界線。修修若未來想 revisit，留 open question。

**拒絕 visitor pattern 的理由（不變）：**

- 兩層 indirection、需要顯式 visitor class registry
- `match` 已給 exhaustiveness pressure（搭配 future static analyzer），不需要 visitor 的 ceremony

### Sequencing — 兩 PR ship

**PR1 — Pure refactor（no behavior change）**

- 新增 `shared/promotion_targets.py`（`match`-based `resolve_target_path()`，SourcePage / Concept 兩 case）
- `gate.py` 移除 `_resolve_target_path` 雙寫 → 呼叫 `resolve_target_path()`
- `gate.py` G6 ConceptReviewItem-only invariant → `validate_type_specific_invariants()` 以 `match` 實作
- `renderer.py` `render_source_page` / `render_concept_page` 合併進 `render_review_item()` 用 `match` dispatch；保留既有 frontmatter tuple + body order，byte-identical 不變
- `commit.py` `_render_item` isinstance dispatch → 呼叫 `render_review_item()`，`_resolve_target_path` 雙寫消除
- 驗收：既有 test 全綠 + render byte-identical + `grep isinstance.*ReviewItem` 結果 = 0 + 所有 `match` 都有 `case _: raise NotImplementedError(...)`
- 邊界：不動 schema、不加 EntityReviewItem、不改 review_service / preflight / 任何 router

**PR2 — EntityReviewItem add（Person + Organization scope）**

- `shared/schemas/promotion_manifest.py`：
  - 新增 `PersonMetadata` + `OrganizationMetadata` + `EntityMetadata` discriminated union
  - 新增 `EntityReviewItem`（`item_kind: Literal["entity"]`，`metadata: EntityMetadata`）
  - 擴 `ReviewItem` union 加入 `EntityReviewItem`，discriminator 維持 `item_kind`
- `promotion_targets.py` / `promotion_renderer.py` / `promotion_acceptance_gate.py` 各加 `case EntityReviewItem():` 分支（外加 inner `match` on metadata）
- `concept_promotion_engine.py` 擴成 `entity_promotion_engine`（或新建 sibling module）— 抽出 entity + canonical_match（用 LLM）
- `promotion_review_service.py` 加 confidence fast-track 邏輯（auto-approve `confidence > 0.9` 寫直接 commit + manifest）
- Book Entity bypass 保留 — `kb_writer.write_book_entity()` 維持現狀（**`book` 不在 `EntityMetadata` union 內**，schema 明確不暗示存在 Book review path）
- 新 test set 覆蓋 Entity flow + fast-track + metadata 變體驗證
- 邊界：YouTube Reader 本身（Reader UI / ASR pipeline / timestamp anchor）是獨立 vertical slice，不在 PR2 scope；Place / Product entity_type 真出現需求時再加進 union

## Considered Options

### Rejected: Single `EntityReviewItem` with `entity_type` enum + `dict[str, Any]` (v1 路 A)

v2 panel 共識 reject — 失敗 deletion test、Schema-on-Read regression、跟既有 `Field(discriminator=...)` pattern 不一致、跟 `singledispatch` 組合產生 fake polymorphism。Codex audit §Section 2 + Gemini audit §Section 2 詳論。

### Rejected: Split into PersonReviewItem / OrganizationReviewItem / BookReviewItem at top-level

更 type-safe，但：
- 上層 `ReviewItem` union 變 5 個 member → 通用 promotion 流程要在 5 case 之間散
- 「review queue 是 entity」這個 affordance 在 schema 階層消失
- v2 discriminated metadata union 已給足 type safety，不需要再拆上層

### Rejected: `functools.singledispatch` as primary dispatch (v1 D3)

v2 改用 `match`。理由見 §D3 panel 修正論點。Codex §Section 3 + Gemini §Section 3。

### Rejected: Methods on Pydantic ReviewItem base class

維持 reject — schema purity 紀律雖然 Gemini push 回「dogmatic」，但 v2 改 `match` 後 dispatch 已 local + explicit，method-on-model 帶不來 net 收益。

### Rejected: Visitor pattern

`match` 已給 exhaustiveness pressure，visitor ceremony 太重。

### Rejected: Every Entity through gate uniformly (Book 也走 HITL)

修修主動 ingest 一本教科書 = 已 approved，再過 gate 是純 ceremony。Hybrid 設計（Book auto / Person+Org gate）對應實際 disambig 難度差異。

### Rejected: 不做 deepening，只修 `_resolve_target_path` 雙寫

最小 PR，但只解一個 symptom — isinstance ladder 散落仍在，EntityReviewItem 加進來時痛點放大。

## Consequences

### Positive

- `_resolve_target_path` silent drift 風險消除
- 新增 ReviewItem subtype 從「動 3 個 module 找 isinstance」變「在 3 個 module 各 append 一個 `case` 分支」— 加減 diff 線性，且 `case _: raise` 強制顯式覆蓋
- 延續既有 `Field(discriminator="item_kind")` schema convention 一致性，新人 / LLM 都好 onboard
- ADR-024 §Decision 的「Source page + Concept」框架自然擴張到「+ Entity（含 Person/Organization 變體）」
- Person / Org Entity 進 gate 抑制 KB stub bloat（呼應 KB stub crisis 2026-05-06 教訓）
- LLM 變強時 fast-track threshold 上調即可吸收，不需要 redesign gate
- YouTube / Podcast Reader 完工後接 promotion gate 不需要新 ReviewItem subtype（產出仍是 SourcePage + Concept；evidence anchor 加 `kind="timestamp"` 即可）
- `match` 比 `singledispatch` LLM-codebase-navigability 更好（Gemini 觀察） — 對 AI-edited codebase 有實質價值

### Negative

- PR1 是 pure refactor，短期不帶 user-visible 價值（但解 silent bug risk）
- `match` 的 exhaustiveness 檢查需要靜態分析器配合（mypy / pyright），repo 目前未配置；短期靠 runtime `case _: raise` + test 覆蓋
- `EntityMetadata` discriminated union 隨類型增加會擴張 — 但 Pydantic v2 對此 pattern 有 first-class 支援
- Book Entity 雙路徑（auto-create via kb_writer vs gate-bypass-by-schema-omission）需要文件明確標示

### Neutral

- Confidence fast-track threshold（0.9）是初始猜測，需收集 manifest 紀錄後校準
- `promotion_targets.py` 是新檔案 — 增加一個 import surface，但換來 single source of truth
- v1 → v2 在 panel 介入後從「invent new shape」轉成「extend existing pattern」— 工程量持平，risk 降低

## Open questions

- **Book Entity 是否未來統一進 gate？** 暫不決定；若 textbook ingest 跑多了發現 Book metadata 也有 disambig 需求（同書多版本、譯本 vs 原文），再評估加 `BookMetadata` 進 union + Book correction review flow
- **Place / Product Entity？** PR2 不收；真出現 use case（例如旅遊文章 ingest 抽地點）再加變體
- **EvidenceAnchor.kind 列舉表** — `timestamp` (video/podcast) / `page` (book) / `paragraph_xpath` (web) / `pdf_bbox` (PDF) 的 closed enum 應由 schema 統一定義；PR2 同時收
- **Entity 跨 source backlink 寫法** — Concept 已有 `mentioned_in: [source-paths]` pattern，Entity 沿用即可，但 confirm 跟 KB indexer 一致
- **靜態分析器導入** — 補 mypy / pyright 後可開啟 `match` exhaustiveness 警告；獨立 cleanup PR，不 block PR1/PR2
- **Schema purity revisit?** — Gemini push 回「schema purity 是 dogmatic」這個論點未在 v2 採納，但留 open question；若未來發現 `match`-based dispatch 不夠 ergonomic，可重評估 method-on-model 或 abstract method

## Panel Integration

完整 3-way 整合矩陣：[`docs/research/2026-05-26-adr034-panel-integration-matrix.md`](../research/2026-05-26-adr034-panel-integration-matrix.md)

**Panel result：** v1 → v2 panel 採納了 6 條 universal/2-of-2 共識項 + 4 條 single-source 但 evidence-solid 項；維持 1 條 single-source dissent（schema purity）為 open question。詳見 matrix。

**Implementation handoff：** PR1 的 P9 六要素 Task Prompt 凍結於 grill conversation log（2026-05-26）；獨立可 dispatch，不依賴 PR2。PR2 schema 設計細節（`PersonMetadata` / `OrganizationMetadata` 欄位） v2 已給草稿；實作前可短 grill 一輪確認欄位完整性。
