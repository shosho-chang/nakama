# Agent Brief — N517 Reading Context Package + Writing Assist Surface (ADR-024 Slice 9)

**Issue:** [#517](https://github.com/shosho-chang/nakama/issues/517)
**Parent PRD:** #508
**Slice of:** ADR-024 (`docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`)
**Branch:** `impl/N517-reading-context-package`
**Worktree (host):** `E:/nakama-N517-reading-context-package`
**Drafted:** 2026-05-10
**Status:** awaiting #513 + #514 merge (and ideally #516) + 修修 review before relabeling `ready-for-agent`

P9 六要素。Brief 是 self-contained handoff，不靠群聊歷史也能上手。

---

## 0. Scope anchor

ADR-024 §Decision: "For Stage 4, Robin may produce a **Reading Context Package** from annotations, notes, digest, promoted source map, Concept links, idea clusters, questions, evidence board, and outline skeletons. A Brook-owned or shared **Writing Assist Surface** may present this package and help insert links, references, and prompts. It must not generate finished prose or ghostwrite Line 2 atomic content."

`agents/robin/CONTEXT.md` § Source Promotion (Stage 4 ownership bridge): "Robin may produce a `Reading Context Package`... This package is a Stage 3 → Stage 4 handoff object for 修修's hand-writing, not a draft. A Brook-owned or shared `Writing Assist Surface` may present the package, insert links/references/prompts, and help 修修 navigate materials, but must not use it to ghostwrite Line 2 atomic content."

`agents/robin/CONTEXT.md` (writing-assist boundary): "Allowed outputs are structure skeletons, question prompts, idea clusters, tension maps, evidence boards, outline candidates, missing-piece prompts, and pointers to 修修's own annotations/source evidence. It may say what a section needs to answer and which materials could support it; it must not generate completed sentences, finished paragraphs, or a first-person opening in 修修's voice."

Slice 9 (#517) is the **Stage 3 → Stage 4 handoff package** + the writing-assist UI surface that presents it. The hard invariant: **no ghostwriting**. No completed paragraphs. No first-person voice. No book-review prose.

### Where Slice 1-8 land (in main as of #517 dispatch time)

`#517` consumes:

- `ReadingSource` (#509)
- Reading Overlay (#510) — annotations, digest.md, notes.md
- `SourceMapBuildResult` (#513) committed Source pages
- `ConceptPromotionResult` (#514) committed Concept pages
- `PromotionManifest` (#512) — review history (optional context for evidence board)
- Optionally #516 UI shell for reuse

### Pipeline anchor

CONTENT-PIPELINE.md Stage 3 → Stage 4 boundary. This slice IS the boundary. It packages 修修's reading materials for the writing scaffold WITHOUT crossing into authorship.

---

## 1. 目標 (Goal)

Two coordinated outputs:

1. **`agents/robin/reading_context_package.py`** — Robin-owned package builder.
   `ReadingContextPackageBuilder.build(source_id, *, annotations_path, notes_path, digest_path, source_map_dir, concepts_dir) -> ReadingContextPackage`.
   Aggregates a deterministic package from existing on-disk artifacts. Does NOT produce prose; ONLY structured material.

2. **Writing Assist Surface** — Brook-owned (or shared) presentation surface.
   - Choice A (preferred): Thousand Sunny route + template that renders the package as a navigable scaffold (section skeleton + question prompts + evidence board + outline candidates + missing-piece prompts + pointers).
   - Choice B (fallback): Markdown export rendered as a write-up scaffold file under `Inbox/writing-assist/{slug}.md`. NOT auto-published; preview-only.

The hard, test-asserted invariant: **the writing assist surface does NOT generate completed sentences, finished paragraphs, or first-person voice content**.

不啟動 LLM (use deterministic structure-only synthesis)、不寫完成段落、不寫第一人稱、不渲染 Line 2 atomic content。

---

## 2. 範圍

### Add

| Path | Action | Reason |
|---|---|---|
| `agents/robin/reading_context_package.py` | **新增** | `ReadingContextPackageBuilder` class. |
| `shared/schemas/reading_context_package.py` | **新增** | `ReadingContextPackage`, `IdeaCluster`, `Question`, `EvidenceItem`, `OutlineSkeleton`, `MissingPiecePrompt`, `WritingAssistOutput` Pydantic value-objects. ALL `extra="forbid"` + `frozen=True`. |
| `shared/writing_assist_surface.py` | **新增** | `WritingAssistSurface.render(package) -> WritingAssistOutput`. Pure structural rendering: maps package → outline shell, question prompts, evidence board, pointers. NO completed prose. |
| `thousand_sunny/routers/writing_assist.py` (Choice A) | **新增** | Thin route handlers. GET `/writing-assist/{source_id_b64}` → render. |
| `thousand_sunny/templates/writing_assist/scaffold.html` (Choice A) | **新增** | Scaffold template — outline skeleton + question prompts + evidence board. NO body-text rendering of full sentences. |
| `tests/agents/test_reading_context_package.py` | **新增** | Builder unit tests. |
| `tests/shared/test_writing_assist_surface.py` | **新增** | Surface unit tests + **no-ghostwriting boundary tests** (assert no completed sentence patterns, no first-person tokens). |
| `tests/thousand_sunny/test_writing_assist_routes.py` | **新增** (Choice A) | Route-level tests. |
| `tests/fixtures/reading_context/` | **新增** | Fake annotations, digest, notes, source map, concept fixtures. |

### Read-only consumption

| Path | Why |
|---|---|
| `shared/schemas/reading_source.py` (#509), `shared/schemas/promotion_manifest.py` (#512), `shared/schemas/source_map.py` (#513), `shared/schemas/concept_promotion.py` (#514) | Aggregation contracts. |
| `agents/robin/*` overlay paths (#510) | digest, notes, annotation files (read-only). |

### 完全不碰

- `agents/brook/*` direct write — Brook will reuse but not own this slice's logic; #517 keeps the surface in `shared/` so Brook can import.
- `KB/Wiki/*` write — package READS from committed pages but does NOT modify.
- LLM calls — package is deterministic aggregation; surface is deterministic structural rendering.
- `shared.book_storage` — N3 contract.
- 寫完整句子、完成段落、第一人稱開頭、書評語氣的 prose — hard invariant.

---

## 3. 輸入

### Caller contract

Builder:

```python
class ReadingContextPackageBuilder:
    def __init__(self, *, annotation_loader: Callable[[str], list[Annotation]] | None = None):
        ...

    def build(
        self,
        reading_source: ReadingSource,
        *,
        digest_path: Path,
        notes_path: Path,
        annotations_path: Path,
        source_map_dir: Path,
        concepts_dir: Path,
    ) -> ReadingContextPackage:
        ...
```

Surface:

```python
class WritingAssistSurface:
    def render(self, package: ReadingContextPackage) -> WritingAssistOutput:
        ...
```

### Caller responsibilities

- Caller (route handler / Brook agent) supplies file paths from app config.
- LLM enrichment (e.g. clustering ideas via embeddings) is OUT of slice — if needed, caller pre-clusters and feeds the result into builder via an injected protocol.
- Surface output `WritingAssistOutput` is structural only; caller chooses how to display.

### Documentation hierarchy

- ADR-024 §Decision (Stage 4 boundary, no ghostwriting)
- `agents/robin/CONTEXT.md` § Source Promotion (writing-assist boundary)
- CONTENT-PIPELINE.md Stage 3 → Stage 4 transition

---

## 4. 輸出

### 4.1 Schema sketch (`shared/schemas/reading_context_package.py`)

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class IdeaCluster(BaseModel):
    """A grouping of related annotations / claims that suggest a section."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    cluster_id: str
    label: str  # short descriptor — NOT a sentence
    annotation_refs: list[str] = Field(default_factory=list)
    claim_refs: list[str] = Field(default_factory=list)


class Question(BaseModel):
    """An open question the writing should address. NOT an answer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str
    text: str  # bare question; ends with '?'
    related_clusters: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    """A pointer to evidence (annotation / source page quote / concept page)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_kind: Literal["annotation", "source_quote", "concept_link"]
    locator: str  # path + offset (annotation), CFI, concept_path, etc.
    excerpt: str  # ≤ 200 chars
    source: str  # human-readable source descriptor


class OutlineSkeleton(BaseModel):
    """Outline candidate — section headings only. NO content under each."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    skeleton_id: str
    section_labels: list[str] = Field(default_factory=list)
    """Section heading labels in order. Each label is ≤ 80 chars and is NOT a
    sentence (no terminal period); it's a heading like 'HRV 在訓練中的角色'.
    """


class MissingPiecePrompt(BaseModel):
    """Identifies what evidence/argument is missing — does NOT supply it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_id: str
    text: str  # describes what's missing; phrased as a need, not an answer


class ReadingContextPackage(BaseModel):
    """Stage 3 → Stage 4 handoff. NOT a draft."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    source_id: str  # transport only
    annotations: list[EvidenceItem] = Field(default_factory=list)
    digest_excerpts: list[EvidenceItem] = Field(default_factory=list)
    notes_excerpts: list[EvidenceItem] = Field(default_factory=list)
    source_quotes: list[EvidenceItem] = Field(default_factory=list)
    concept_links: list[EvidenceItem] = Field(default_factory=list)
    idea_clusters: list[IdeaCluster] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    outline_skeleton: OutlineSkeleton | None = None
    missing_piece_prompts: list[MissingPiecePrompt] = Field(default_factory=list)
    error: str | None = None


class WritingAssistOutput(BaseModel):
    """Surface output. ONLY structural — no completed prose."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    package_source_id: str
    section_blocks: list[SectionBlock] = Field(default_factory=list)
    """Each block: heading + bullet pointers + evidence references. NO body prose."""

    pointer_index: dict[str, str] = Field(default_factory=dict)
    """label → URI for navigation (e.g. annotation:foo:42 → fully qualified)."""


class SectionBlock(BaseModel):
    """One outline section's scaffold. NOT body content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    heading: str  # ≤ 80 chars, no terminal period
    question_prompts: list[str] = Field(default_factory=list)
    """Each prompt ends with '?'."""

    evidence_pointers: list[EvidenceItem] = Field(default_factory=list)
    missing_piece_prompts: list[str] = Field(default_factory=list)
```

### 4.2 No-ghostwriting boundary (test-enforced)

The hard invariant. Surface output MUST satisfy ALL of:

| ID | Rule | Test |
|---|---|---|
| W1 | NO `SectionBlock` field contains a sentence ending with `.` `。` `!` `！` outside of `evidence_pointers[*].excerpt` (which IS quoted source content, not authored). | parametrized regex sweep |
| W2 | NO `SectionBlock.heading` ends with terminal punctuation (`.` `。` `!` `?` `？`). | regex sweep |
| W3 | NO output field contains first-person tokens (`我`, `我們`, `I `, `we `, `my `, `we'll`, etc.) outside `evidence_pointers[*].excerpt` (excerpt may legitimately quote source). | parametrized regex sweep |
| W4 | NO output field contains "I think" / "I believe" / "我認為" / "我覺得" / "我相信" patterns. | regex sweep |
| W5 | `Question.text` ends with `?` or `？`. | per-field check |
| W6 | `MissingPiecePrompt.text` does NOT end with `.` `。` (must be phrased as need, not assertion). | per-field check |
| W7 | Total `WritingAssistOutput` size — sum of all string field lengths excluding evidence excerpts — ≤ 5000 chars. (Sanity: scaffolds are short; long output suggests prose creep.) | length check |

These rules are enforced by `WritingAssistSurface.render()` and double-checked by tests. Any future LLM-backed enrichment MUST maintain these invariants OR be rejected at the surface boundary.

### 4.3 Builder behavior

Pure aggregation:

1. Read `digest.md` → extract heading-prefixed sections → emit `EvidenceItem` per non-empty section.
2. Read `notes.md` → same.
3. Read annotations (via injected loader) → emit `EvidenceItem` per annotation.
4. Walk `source_map_dir/{source_slug}/*.md` → extract claim-list bullets + quote anchors → emit `EvidenceItem` for each quote.
5. Walk `concepts_dir/*.md` → for concepts where `mentioned_in` includes this source → emit `concept_link` `EvidenceItem`.
6. **Cluster** annotations by simple deterministic heuristic (e.g. by `chapter_ref` if available, or by 3-gram overlap fallback). NO LLM call.
7. **Generate questions**: read existing question annotations (annotations tagged `question`) → emit `Question` directly. Builder does NOT auto-generate new questions in this slice.
8. **Outline skeleton**: derive section heading candidates from cluster labels (deterministic alphabetical or count-ordered). Slice 9 ships ONLY the section list; deeper outline is future work.
9. **Missing-piece prompts**: identify clusters with annotations but no source-page evidence → emit `MissingPiecePrompt(text="<cluster label>: 需要更多 evidence")` (NOT prose).
10. Return `ReadingContextPackage`.

### 4.4 Surface behavior

Pure structural mapping:

1. For each `IdeaCluster` → produce a `SectionBlock`:
   - `heading = cluster.label` (no terminal punctuation; truncate ≤ 80).
   - `question_prompts` = related questions' text (each ending with `?`).
   - `evidence_pointers` = associated `EvidenceItem` objects.
   - `missing_piece_prompts` = related missing-piece text.
2. Build `pointer_index` from all evidence locators → URL-safe identifiers.
3. Validate W1-W7; raise on violation.
4. Return `WritingAssistOutput`.

---

## 5. 驗收 (Acceptance)

### Issue #517 listed AC

- Package can be generated from Reading Overlay + promoted Source Map + Concept links.
- Package includes annotations, notes, digest, idea clusters, questions, evidence board, outline skeleton, and links.
- Writing Assist Surface presents scaffold only.
- It must not generate finished prose, first-person paragraphs, or a book review draft.
- Tests assert the no-ghostwriting boundary.

### Unit tests (builder)

| # | Test | Asserts |
|---|---|---|
| BT1 | `test_build_aggregates_digest_excerpts` | digest.md w/ 3 H2 sections → 3 EvidenceItem entries in `digest_excerpts`. |
| BT2 | `test_build_aggregates_notes_excerpts` | analogous. |
| BT3 | `test_build_aggregates_annotations` | injected loader returns 5 annotations → 5 EvidenceItem entries. |
| BT4 | `test_build_aggregates_source_quotes` | source_map_dir has 2 chapter pages with quote bullets → quotes extracted. |
| BT5 | `test_build_aggregates_concept_links` | concepts_dir has 1 concept with `mentioned_in` referencing this source → 1 concept_link. |
| BT6 | `test_build_clusters_annotations_deterministically` | run builder twice on same input → identical cluster output. |
| BT7 | `test_build_extracts_questions_from_tagged_annotations` | annotations tagged `question` → Question entries with `?` terminal. |
| BT8 | `test_build_outline_skeleton_lists_cluster_labels` | outline_skeleton.section_labels matches cluster labels (no auto-generated content). |
| BT9 | `test_build_missing_piece_prompts_when_evidence_gap` | cluster with no source-page evidence → MissingPiecePrompt. |
| BT10 | `test_build_no_llm_call` | subprocess: `anthropic`, `openai` absent. |
| BT11 | `test_build_no_book_storage_import` | subprocess. |
| BT12 | `test_build_round_trips` | model_dump + model_validate identity. |

### Unit tests (surface)

| # | Test | Asserts |
|---|---|---|
| WT1 | `test_surface_renders_section_blocks_from_clusters` | n clusters → n SectionBlocks. |
| WT2 | `test_surface_pointer_index_built` | every EvidenceItem appears in pointer_index. |
| WT3 | `test_surface_section_block_heading_no_terminal_punctuation` | enforced; violation raises. |
| WT4 | `test_surface_no_first_person_tokens` (W3) | parametrized: assert `我`, `我們`, `I `, `we `, `my `, `our `, etc. NOT in any non-excerpt field. |
| WT5 | `test_surface_no_completed_sentence_in_non_excerpt_fields` (W1) | regex sweep: heading + question + missing_piece + pointer values do NOT match `[A-Za-z一-鿿]+[。.!！]$`. |
| WT6 | `test_surface_no_i_think_patterns` (W4) | parametrized: "I think", "I believe", "我認為", "我覺得", "我相信" NOT in non-excerpt content. |
| WT7 | `test_surface_question_prompt_ends_with_question_mark` (W5) | every question prompt ends with `?` or `？`. |
| WT8 | `test_surface_size_budget` (W7) | total non-excerpt char count ≤ 5000 for fixture package. |
| WT9 | `test_surface_violation_raises` | manually inject SectionBlock with completed sentence → surface render raises `ValueError("ghostwriting detected: ...")`. |
| WT10 | `test_surface_evidence_excerpt_unaffected_by_no_ghostwriting_rules` | excerpt CONTAINING "I think" passes (it's a quoted source, not authored). |
| WT11 | `test_surface_no_book_storage_import` | subprocess. |
| WT12 | `test_surface_round_trips` | model_dump + model_validate identity. |

### Route tests (Choice A)

| # | Test | Asserts |
|---|---|---|
| RT1 | `test_route_renders_scaffold_for_known_source` | GET `/writing-assist/{id}` → 200 + section blocks rendered. |
| RT2 | `test_route_does_not_render_completed_prose` | grep response HTML for first-person tokens; assert absent (excluding excerpt blocks). |
| RT3 | `test_route_uses_design_tokens` | template uses `var(--token-*)`; no hardcoded colors. |

### Self-imposed gates

- [ ] 零新 dependency
- [ ] 全部 model `extra="forbid"`
- [ ] All Literal enums frozen for `schema_version=1`
- [ ] **Closed-set extension protocol**
- [ ] **No-ghostwriting boundary** enforced AT surface render time AND tested
- [ ] `python -m pytest tests/agents/test_reading_context_package.py tests/shared/test_writing_assist_surface.py tests/thousand_sunny/test_writing_assist_routes.py -v` 全綠
- [ ] `python -m ruff check ...` 無 error
- [ ] `python -m ruff format --check ...` clean
- [ ] PR body 含 P7-COMPLETION 區塊 + Aesthetic direction 段落 (UI exists)

---

## 6. 邊界（不能碰）

| # | Don't | Why |
|---|---|---|
| 1 | ❌ Generate completed sentences in non-excerpt fields | hard invariant W1 |
| 2 | ❌ First-person voice ("我", "I ", "我們", etc.) | hard invariant W3 |
| 3 | ❌ "I think" / "我認為" pattern | hard invariant W4 |
| 4 | ❌ Auto-generate book-review prose | ADR-024 §Decision |
| 5 | ❌ LLM call in builder or surface | use deterministic aggregation; LLM enrichment is a future slice with stricter boundary tests |
| 6 | ❌ Write to `KB/Wiki/*` or modify Source pages / Concepts pages | read-only consumption |
| 7 | ❌ Reading Overlay write (digest, notes, annotations) | #510 owns overlay |
| 8 | ❌ `shared.book_storage` import | N3 contract |
| 9 | ❌ Modify upstream schemas | upstream contracts frozen |
| 10 | ❌ Auto-publish Inbox/writing-assist/*.md as final output | preview/scaffold only |
| 11 | ❌ `git add .` outside scope | hygiene |
| 12 | ❌ Bare `except Exception` | #511 F5 lesson |
| 13 | ❌ Synthesize answers to questions | builder lists questions; surface presents them; neither answers them |
| 14 | ❌ Outline content under section headings | only labels, no body text |

---

## 7. References

### Primary

- ADR: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md` (Stage 4 boundary)
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion (writing-assist boundary)
- CONTENT-PIPELINE.md Stage 3 → Stage 4 transition

### Pattern reference

- `shared/promotion_preflight.py` (#511) — protocol injection + narrow exception tuples
- `agents/robin/*` overlay (#510) — annotation / digest / notes loaders

### Issue / PR

- Issue #517: `[ADR-024 S9] Reading Context Package + Writing Assist Surface`
- Parent PRD #508
- Slices 5 (#513) + 6 (#514): merge before dispatch
- Slice 8 (#516): preferred merge before dispatch (UI shell reuse)

---

## 8. Triage status

`needs-triage` → 等修修讀 Brief 後決定。**不要**自行 relabel `ready-for-agent`，**不要**直接開始 code。

Slice 9 等 #513 + #514 + #516 merge 後再 dispatch。**No-ghostwriting boundary** 是 hard architectural invariant — implementer 必須 read ADR-024 §Decision + CONTEXT.md writing-assist-boundary 段全文，並把 W1-W7 invariants 全寫進測試。預估 ~1100 LOC + tests + templates.
