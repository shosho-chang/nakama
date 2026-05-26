# ADR-034: Promotion Polymorphism Unification + Entity Review

**Date:** 2026-05-26
**Status:** Draft v1 (pending 修修 sign-off; multi-agent panel review optional — see §Panel Evaluation)
**Owner:** 修修
**Related:** [ADR-024](ADR-024-source-promotion-and-reading-context-package.md) (Source Promotion) · [ADR-020](ADR-020-textbook-ingest-v3-rewrite.md) (textbook ingest v3) · `improve-codebase-architecture` grill 2026-05-26

---

## Context

ADR-024 凍結 Source Promotion 為 Reading Source → KB 的審批管線，產出兩種 `ReviewItem` subtype：

- `SourcePageReviewItem` — 一整章 / 一頁 source 升格為 `KB/Wiki/Sources/...`
- `ConceptReviewItem` — 從 source 抽出的 concept 升格為 `KB/Wiki/Concepts/...`

實作落在 `shared/promotion_*` 五個 module（acceptance_gate / renderer / commit / review_service / preflight）。

### 觸發此 ADR 的 grill（2026-05-26）

`improve-codebase-architecture` audit 點出兩個摩擦：

1. **`_resolve_target_path` 雙寫** — `gate.py:115-128` 跟 `commit.py:502-518` 各自實作「從 ReviewItem 拿 target_kb_path」的邏輯。兩處邏輯本應一致，但無 single source — 一邊改另一邊忘記同步是 silent bug 風險。
2. **isinstance ladder 散在 3 個 module** — `SourcePageReviewItem` vs `ConceptReviewItem` 的 type-discriminated 分支總計 ~6 處跨 gate / renderer / commit。新增 subtype 要動三檔。

Grill 還浮現一個直接相關的設計題：**第三種 ReviewItem subtype 短期內會不會出現？**

修修在 grill 中描述了 YouTube / Podcast Reader 的需求 — 看影片時即時對轉錄稿 highlight + annotate，看完後 promote 有價值的內容進 KB。順帶想搜「**哪個人**在哪部影片講過什麼」。這個 use case 帶出 Person / Org Entity 的 cross-source 統一管理需求。

目前 Book Entity 走 `kb_writer.write_book_entity()` 完全繞過 promotion gate（textbook ingest 完直接寫檔）。Person / Org Entity 還沒有 first-class 機制。

## Decision

凍結三條方向：

### D1. Entity 採 Hybrid Gate

| Entity 類型 | 進 promotion gate？ | 理由 |
|---|---|---|
| **Book Entity** | ❌ Auto-create | 修修主動 ingest = approved by definition；gate 為純 ceremony |
| **Person Entity** | ✅ Gate + fast-track | 拼字變體 / 別名 / 一次性引用 disambig 需要人類判斷；YouTube/Podcast 場景帶來大量 Person surface |
| **Org Entity** | ✅ Gate + fast-track | 同 Person，confidence fast-track 更積極（拼字變體少） |

**Confidence-based fast-track**（套用既有 `canonical_match.confidence` 欄位）：

```
confidence > 0.9   → auto-approve（不進 UI review queue，仍記 manifest）
0.5 ≤ confidence ≤ 0.9 → 進 UI review queue
confidence < 0.5  → 進 review queue 但預設 defer
```

LLM 變強時 threshold 可調高、queue 自然縮小 — 架構不變。

### D2. EntityReviewItem 採路 A — 單一 class + `entity_type` enum

```python
class EntityReviewItem(BaseModel):
    item_id: str
    entity_type: Literal["person", "organization", "book", "place"]
    entity_label: str
    aliases: list[str]
    canonical_match: EntityCanonicalMatch | None
    evidence: list[EvidenceAnchor]
    entity_metadata: dict[str, Any]  # 各 type 專屬欄位（ISBN / affiliation / org_type 等）
    # ...共用欄位：recommendation, confidence, source_importance, reader_salience,
    #          human_decision, promoted_at, promoted_from_manifest...
```

**拒絕路 B**（拆 `PersonReviewItem` / `OrgReviewItem` / `BookReviewItem` 三個獨立 class）— 理由：

- Entity 之間共通性遠大於差異性（都有 name / aliases / cross-source canonical_match / evidence-based promotion）
- `ConceptReviewItem` 是路 A 的既有 pattern（沒拆「醫學 / 化學 / 心理 concept」）— 一致性
- entity-specific 欄位走 `entity_metadata: dict[str, Any]` 不污染 schema 階層
- 渲染差異化（Book 頁有 author / publisher，Person 頁有 affiliation）交給 renderer 內部 `entity_type` switch 處理

**最終 ReviewItem subtype 數：3 種**（SourcePage / Concept / Entity）。

### D3. 多型 dispatch 走 `functools.singledispatch`

每個 concern（target 解析、渲染、type-specific 驗證）由自己的 module 擁有，subtype-specific 邏輯透過 `@register` decorator 在該 module 內 append。

**拒絕「方法掛在 Pydantic base class」** — 理由：

- `shared/schemas/` 既有慣例為純 data class，不 import behavior modules（yaml / formatter / evidence renderer）
- determinism 契約（renderer 兩次 run byte-identical）屬於 renderer module，不該 leak 進 schema
- schema versioning 跟 renderer 演進 cadence 不同 — 鬆耦合較好

**拒絕 visitor pattern** — 兩層 indirection、ceremony 過重；Python singledispatch 已足夠表達多型 dispatch。

具體形狀：

```python
# shared/promotion_targets.py（新檔案）
from functools import singledispatch

@singledispatch
def resolve_target_path(item, vault_root: Path) -> str | None:
    raise NotImplementedError(f"No target resolver for {type(item).__name__}")

@resolve_target_path.register
def _(item: SourcePageReviewItem, vault_root: Path) -> str | None:
    return item.target_kb_path

@resolve_target_path.register
def _(item: ConceptReviewItem, vault_root: Path) -> str | None:
    cm = item.canonical_match
    return cm.matched_concept_path if cm and cm.matched_concept_path else None

# 未來 EntityReviewItem 加進來：
@resolve_target_path.register
def _(item: EntityReviewItem, vault_root: Path) -> str | None:
    # entity_type → KB/Wiki/Entities/{People|Organizations|Books}/<slug>.md
    ...
```

同 pattern 套用：
- `render_review_item(item, manifest) -> str` in `shared/promotion_renderer.py`
- `validate_type_specific_invariants(item, ...) -> list[AcceptanceFinding]` in `shared/promotion_acceptance_gate.py`（Concept 的 G6 移過去；未來 Entity-specific invariant 同樣 register）

### Sequencing — 兩 PR ship

**PR1 — Pure refactor（no behavior change）**

- 新增 `shared/promotion_targets.py`（singledispatch + SourcePage / Concept register）
- `gate.py` 移除 `_resolve_target_path` 雙寫 → 呼叫 `resolve_target_path()`
- `gate.py` G6 ConceptReviewItem-only invariant → `validate_type_specific_invariants()` singledispatch
- `renderer.py` `render_source_page` / `render_concept_page` 改 `@render_review_item.register`，對外公開單一 entry `render_review_item()`
- `commit.py` `_render_item` isinstance dispatch → 呼叫 `render_review_item()`，`_resolve_target_path` 雙寫消除
- 驗收：既有 test 全綠 + render byte-identical + `grep isinstance.*ReviewItem` 結果 = 0
- 邊界：不動 schema、不加 EntityReviewItem、不改 review_service / preflight / 任何 router

**PR2 — EntityReviewItem add**

- `shared/schemas/promotion_manifest.py` 新增 `EntityReviewItem` + `entity_type` enum + `EntityCanonicalMatch`
- `promotion_targets` / `promotion_renderer` / `promotion_acceptance_gate` 各 `register` Entity case
- `concept_promotion_engine.py` 擴成 `entity_promotion_engine`（或新建 sibling module）— 抽出 entity + canonical_match
- `promotion_review_service.py` 加 confidence fast-track 邏輯（auto-approve `confidence > 0.9` 寫直接 commit + manifest）
- Book Entity bypass 保留 — `kb_writer.write_book_entity()` 維持現狀，不改路徑
- 新 test set 覆蓋 Entity flow + fast-track
- 邊界：YouTube Reader 本身（Reader UI / ASR pipeline / timestamp anchor）是獨立 vertical slice，不在 PR2 scope

## Considered Options

### Rejected: Split Person / Org / Book into separate ReviewItem classes (路 B)

更 type-safe（Book 一定有 `authors`、Person 一定有 `name`），但：

- ReviewItem subtype 變 5 種 → polymorphism 痛點放大、review queue UI 要處理 5 種卡片
- 跟既有 `ConceptReviewItem` 不拆 sub-domain 的 pattern 不一致
- entity-specific 欄位差異不大到需要 schema-level type safety（Pydantic `entity_metadata: dict[str, Any]` 或 discriminated union 可以局部處理）

### Rejected: Methods on Pydantic ReviewItem base class

最高 locality（`item.render()` IDE 直接跳），但污染 `shared/schemas/` 純 data class 慣例 — schema module 突然要 import yaml + renderer helpers，跟既有界線衝突。

### Rejected: Visitor pattern

兩層 indirection、需要顯式 visitor class registry、Python 沒有語法糖支持。Singledispatch 已足夠表達同樣多型，ceremony 更輕。

### Rejected: Every Entity through gate uniformly (Book 也走 HITL)

修修主動 ingest 一本教科書 = 已 approved，再過 gate 是純 ceremony。Hybrid 設計（Book auto / Person+Org gate）對應實際 disambig 難度差異。

### Rejected: 不做 deepening，只修 `_resolve_target_path` 雙寫

最小 PR，但只解一個 symptom — isinstance ladder 散落仍在，EntityReviewItem 加進來時痛點放大 3 倍。

## Consequences

### Positive

- `_resolve_target_path` silent drift 風險消除
- 新增 ReviewItem subtype 從「動 3 個 module」變「在 3 個 module 各 append 1 個 `@register`」— 加減的 diff 線性 + locality 高
- ADR-024 §Decision 的「Source page + Concept」框架自然擴張到「+ Entity」，不違背原 ADR
- Person / Org Entity 進 gate 抑制 KB stub bloat（呼應 KB stub crisis 2026-05-06 教訓）
- LLM 變強時 fast-track threshold 上調即可吸收，不需要 redesign gate
- YouTube / Podcast Reader 完工後接 promotion gate 不需要新 ReviewItem subtype（產出仍是 SourcePage + Concept；evidence anchor 加 `kind="timestamp"` 即可）

### Negative

- PR1 是 pure refactor，短期不帶 user-visible 價值（但解 silent bug risk）
- singledispatch 對 mypy / pyright type narrowing 在某些版本支援不完整 — 需驗證 nakama 既有 lint stack 吃得下
- `entity_metadata: dict[str, Any]` 是逃逸艙 — 若未來欄位爆炸，需重新評估是否走 discriminated union
- Book Entity 雙路徑（auto-create vs gate）並存 — 文件需明確標示「Book Entity 不經 gate」

### Neutral

- Confidence fast-track threshold（0.9）是初始猜測，需收集 manifest 紀錄後校準
- `promotion_targets.py` 是新檔案 — 增加一個 import surface，但換來 single source of truth

## Open questions

- **Book Entity 是否未來統一進 gate？** 暫不決定；若 textbook ingest 跑多了發現 Book metadata 也有 disambig 需求（同書多版本、譯本 vs 原文），再評估
- **EvidenceAnchor.kind 列舉表** — `timestamp` (video/podcast) / `page` (book) / `paragraph_xpath` (web) / `pdf_bbox` (PDF) 的 closed enum 應由 schema 統一定義；PR2 同時收
- **Entity 跨 source backlink 寫法** — Concept 已有 `mentioned_in: [source-paths]` pattern，Entity 沿用即可，但 confirm 跟 KB indexer 一致

## Panel evaluation (this ADR)

見 ADR-033 v2 之 panel review pattern。本 ADR 的 panel 必要性評估：

| 風險面 | 評估 | Panel 必要 |
|---|---|---|
| Polymorphism mechanism（singledispatch vs OO method） | well-known engineering trade-off | Low |
| 路 A vs 路 B Entity schema | 已論證；contrarian view = type safety win | Medium |
| Hybrid gate（Book bypass）| 純 product taste 判斷 | Low |
| Confidence threshold 0.9 | hand-wavy 初始值 | Low（PR2 收 manifest 後校準） |
| 跟 ADR-024 / ADR-017 / ADR-021 衝突 | 已 cross-check 無衝突 | Low |
| 影響 cost / security / scalability | 無 | Low |

整體：**Panel optional**。若修修要 sanity check 路 A vs 路 B 決定 + Codex 對 singledispatch 在大型 codebase 的 maintainability 意見，可跑短版 panel（焦點 audit，非全面 strategic review）。否則可直接進 PR1 實作。

## Implementation handoff

PR1 的 P9 六要素 Task Prompt 凍結於 grill conversation log（2026-05-26）；獨立可 dispatch，不依賴 PR2。
