# N509 — Reading Source Registry (ADR-024 Slice 1)

**Issue:** #509
**Branch:** `impl/N509-reading-source-registry`
**Worktree:** `E:/nakama-N509-source-registry`
**Status:** Plan only — no LLM batch, no Sandcastle dispatch yet
**Author:** Claude (Opus 4.7) — handed off by 修修 2026-05-09

---

## 0. Scope anchor (ADR-024 § Decision)

ADR-024 defines a **Reading Source** as "an ebook, web document, or inbox document before it is necessarily promoted into formal KB". It also separates:

- **Reading Overlay** (annotations / digest / notes) — personal interaction layer
- **Source Promotion** (future slices) — lifting Reading Source into knowledge-grade KB
- **Original / evidence track** — the language-of-record body used for factual claims
- **Display / bilingual track** — what 修修 sees in Reader

Slice 1 (#509) builds **only the resolution surface** — given a lookup key (book_id, inbox path, or KB source path), return a normalized `ReadingSource` value object. No promotion logic, no manifest, no LLM, no UI.

---

## 1. 目標 (Goal)

提供 single resolver service `shared.reading_source_registry`, given a lookup key, returns a normalized `ReadingSource` value object that:

- 統一 ebook / inbox markdown / KB Source page 三種來源在同一語言層
- 區分 **original/evidence track**（factual layer）vs **display/bilingual track**（Reader UX layer）
- 列出可用 **variants**（en / zh-Hant / bilingual / other），每個 variant 標明 path、format、language
- 公開 surface 不依賴 FastAPI / Thousand Sunny route handler 的執行環境

---

## 2. 範圍 (Scope — files to add / modify)

### Add (new files)

| Path | Purpose |
|---|---|
| `shared/schemas/reading_source.py` | Pydantic schema: `ReadingSource`, `SourceVariant`, `SourceKind`, `TrackRole` literals. `extra="forbid"`, `frozen=True` for value-object lines. |
| `shared/reading_source_registry.py` | Resolver service: `resolve(key) -> ReadingSource \| None`. Dispatches on `SourceKey` shape. |
| `tests/shared/test_reading_source_registry.py` | Unit tests (no fastapi imports — confirms reusability). |
| `tests/fixtures/reading_source/` | Tiny EPUB + markdown fixtures (no external dependencies). |

### Touch (read-only — no behavior change in slice 1)

| Path | Why we read it |
|---|---|
| `shared/book_storage.py` | `get_book(book_id)` / `read_book_blob()` already canonical for ebook track resolution; registry calls it as-is. |
| `shared/annotation_store.py` | `annotation_slug(filename, frontmatter)` is the canonical slug rule for inbox docs; reuse so registry's `source_id` for inbox aligns with annotations. |
| `shared/utils.py` | `extract_frontmatter`, `read_text`, `slugify` reused. |

### Out of scope (explicit)

- Source Promotion (slice 6 / 7), Promotion Manifest (slice 4), Promotion Preflight (slice 3), Reading Overlay Service (slice 2)
- URL / web-document import (PRD #508 says URL ingest cancelled; web capture goes through browser/Obsidian plugin)
- Textbook chapter-level decomposition (handled by ADR-020 ingest, not registry)
- Writes to vault, DB, or KB
- New Reader UI behavior

---

## 3. 輸入 (Inputs / upstream contracts)

| Input | Source | Notes |
|---|---|---|
| `book_id` | `shared/book_storage.py:get_book()` returns `Book` (already used by Reader/upload paths) | Reusable as-is; original.epub vs bilingual.epub track determined by `Book.has_original`. |
| Inbox path | `Inbox/kb/{slug}.md` filesystem layout (PRD §資料 schema, `inbox_writer.py`) | Bilingual sibling = `{slug}-bilingual.md` (frontmatter `derived_from: {slug}.md`). Original = `{slug}.md`. |
| KB Source path | `KB/Wiki/Sources/...` filesystem layout (existing for textbook-grade ingest) | Read-only enumeration; promotion writes to it in later slice. |
| Annotation slug rule | `shared/annotation_store.py:annotation_slug` | `ReadingSource.source_id` for inbox MUST match this slug so future Reading Overlay Service can join. |
| Vault root | `shared/config.py:get_vault_path()` | Resolved at registry instantiation, not import. |

**Upstream invariants (don't break):**

- `Book` schema (`shared/schemas/books.py`) — leave untouched.
- `IngestResult` frontmatter (`shared/schemas/ingest_result.py`) — leave untouched.
- `annotation_slug()` semantics — registry uses, does not redefine.

---

## 4. 輸出 (Deliverables)

### 4.1 Schema sketch — `shared/schemas/reading_source.py`

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

SourceKind = Literal["ebook", "inbox_document", "kb_source"]
"""
ebook         — record in `books` table; epub blob in data/books/{book_id}/
inbox_document — markdown file in Inbox/kb/{slug}.md (Toast-imported or hand-dropped)
kb_source     — already-materialized page in KB/Wiki/Sources/... (textbook-grade ingest output)
"""

TrackRole = Literal["original", "display"]
"""
original — factual evidence layer (en source for English book, zh source for Chinese book)
display  — Reader UX layer (bilingual EPUB, bilingual sibling md). May equal original.
"""

VariantFormat = Literal["epub", "markdown", "html"]


class SourceVariant(BaseModel):
    """One concrete file/blob backing a Reading Source."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    role: TrackRole
    format: VariantFormat
    lang: str  # bcp-47 short: "en", "zh-Hant", "bilingual"
    path: str  # vault-relative for md / "books:{book_id}:{lang}" for ebook blobs
    bytes_estimate: int | None = None  # cheap stat; None if unknown


class ReadingSource(BaseModel):
    """Normalized Reading Source — pre-promotion identity + tracks.

    Slice 1 only. Source Promotion fields (manifest_id, promotion_status, ...)
    are added in later slices; do NOT add them speculatively.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1

    source_id: str
    """Stable identity. ebook → book_id; inbox_document → annotation_slug
    (so Reading Overlay can join 1:1); kb_source → vault-relative path stem."""

    kind: SourceKind
    title: str
    author: str | None = None
    primary_lang: str
    """The 'evidence language': en for English textbook, zh-Hant for Chinese
    article. Bilingual is never primary_lang."""

    has_evidence_track: bool
    """False when only display/bilingual track exists (e.g. user uploaded bilingual
    EPUB without original). Promotion paths must check this."""

    variants: list[SourceVariant] = Field(default_factory=list)
    """At least one variant. Original-track variant must be present when
    has_evidence_track is True."""

    metadata: dict[str, str] = Field(default_factory=dict)
    """Cheap pass-through: isbn, published_year, original_url for inbox.
    Strings only; richer metadata stays in source schemas."""
```

### 4.2 Service sketch — `shared/reading_source_registry.py`

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from shared import book_storage
from shared.annotation_store import annotation_slug
from shared.config import get_vault_path
from shared.schemas.reading_source import ReadingSource, SourceVariant
from shared.utils import extract_frontmatter, read_text


@dataclass(frozen=True)
class BookKey:
    book_id: str


@dataclass(frozen=True)
class InboxKey:
    """vault-relative path under Inbox/kb/, e.g. 'Inbox/kb/foo.md'."""
    relative_path: str


@dataclass(frozen=True)
class KBSourceKey:
    """vault-relative path under KB/Wiki/Sources/."""
    relative_path: str


SourceKey = Union[BookKey, InboxKey, KBSourceKey]


class ReadingSourceRegistry:
    def __init__(self, vault_root: Path | None = None) -> None:
        self._vault = Path(vault_root) if vault_root else get_vault_path()

    def resolve(self, key: SourceKey) -> ReadingSource | None:
        match key:
            case BookKey(book_id):
                return self._resolve_book(book_id)
            case InboxKey(rel):
                return self._resolve_inbox(rel)
            case KBSourceKey(rel):
                return self._resolve_kb_source(rel)
        raise TypeError(f"Unknown SourceKey: {type(key).__name__}")

    # implementations: _resolve_book, _resolve_inbox, _resolve_kb_source
    # See §5 for resolution rules.
```

### 4.3 Resolution rules (per kind)

**Ebook (`BookKey`):**

1. `book_storage.get_book(book_id)` → `None` if missing → return `None`.
2. `source_id = book_id`, `title = book.title`, `author = book.author`, `primary_lang = book.lang_pair.split('-')[0]` (the `en` part of `en-zh`).
3. `has_evidence_track = book.has_original`.
4. Variants:
   - if `has_original`: append `original / epub / lang=en / path=books:{book_id}:en`
   - always: append `display / epub / lang=bilingual / path=books:{book_id}:bilingual`
5. Metadata: isbn, published_year, lang_pair.

**Inbox document (`InboxKey`):**

1. Read `vault/{relative_path}`. Missing → `None`.
2. Extract frontmatter.
3. `source_id = annotation_slug(Path(relative_path).name, frontmatter)`.
4. Detect bilingual sibling:
   - If filename ends `-bilingual.md` → original sibling = strip suffix → if exists, this file is `display`, sibling is `original`.
   - If filename plain `{stem}.md` and `{stem}-bilingual.md` exists → this file is `original`, sibling is `display`.
   - If only one exists, that single file is both original AND display (single variant, role=original, has_evidence_track=True).
5. `primary_lang = frontmatter.lang or "en"` for original; bilingual sibling always `lang="bilingual"`.
6. `has_evidence_track`: True when an original-language file exists; False only if user uploaded bilingual-only.

**KB Source (`KBSourceKey`):**

1. Read `vault/KB/Wiki/Sources/{relative_path}`. Missing → `None`.
2. Frontmatter → title / lang.
3. `source_id` = relative_path stem (no `.md`).
4. Single variant: `original / markdown / lang=primary_lang / path=relative_path`.
5. `has_evidence_track = True`.

### 4.4 Test matrix — `tests/shared/test_reading_source_registry.py`

| Test | Setup | Assertion |
|---|---|---|
| `test_resolve_ebook_with_original` | Insert Book row, has_original=True | 2 variants (original en + display bilingual), `has_evidence_track=True` |
| `test_resolve_ebook_bilingual_only` | Insert Book row, has_original=False | 1 variant (display), `has_evidence_track=False` |
| `test_resolve_ebook_missing` | empty DB | returns `None` |
| `test_resolve_inbox_plain_md` | write `Inbox/kb/foo.md` only | 1 variant (original markdown), source_id matches `annotation_slug` |
| `test_resolve_inbox_with_bilingual_sibling` | foo.md + foo-bilingual.md | 2 variants, original=foo.md, display=foo-bilingual.md |
| `test_resolve_inbox_bilingual_only` | foo-bilingual.md (no foo.md) | 1 variant (display), `has_evidence_track=False` |
| `test_resolve_inbox_missing` | empty inbox | returns `None` |
| `test_resolve_kb_source` | write `KB/Wiki/Sources/Books/x/ch1.md` | 1 variant (original markdown), source_id="Books/x/ch1" |
| `test_no_fastapi_imports` | static check / `import shared.reading_source_registry` in plain script | Pass — confirms reusability outside route handlers |

### 4.5 Fixtures — `tests/fixtures/reading_source/`

- `tiny_book/original.epub` — 1-chapter "Hello world" (build with `zipfile` + minimal OPF in test setup, no external EPUB binary)
- `tiny_book/bilingual.epub` — same shape, marked bilingual
- `inbox/foo.md` — frontmatter with `title: Foo`, `lang: en`
- `inbox/foo-bilingual.md` — `derived_from: foo.md`, `lang: bilingual`
- `kb_source/sample-source.md` — frontmatter with `title: Sample`, `lang: en`

Tests use `tmp_path` to materialize copies into a synthetic vault layout. Book table populated via `shared.book_storage.insert_book` against in-memory SQLite.

---

## 5. 驗收 (Acceptance criteria — copied verbatim from #509)

- [ ] A Reading Source can be resolved for at least one book fixture and one markdown document fixture.
- [ ] The resolved shape distinguishes original/evidence track from display/bilingual track.
- [ ] The service is reusable outside Thousand Sunny route handlers.
- [ ] Tests cover ebook, inbox/document, and missing evidence-track cases.

**Plus self-imposed gates (not in #509 body but required by ADR-024):**

- [ ] No new dependency added; reuses existing `book_storage`, `annotation_store.annotation_slug`, `extract_frontmatter`.
- [ ] `extra="forbid"` on every Pydantic model (per `docs/principles/schemas.md`).
- [ ] Test file imports zero `fastapi` / `thousand_sunny` symbols.
- [ ] `source_id` for inbox documents equals `annotation_slug(filename, frontmatter)` so a future Reading Overlay Service joins 1:1.
- [ ] `primary_lang` for ebook derives from `Book.lang_pair`'s first segment; bilingual is never primary.

---

## 6. 邊界 (Boundaries — what NOT to do)

- **No** Promotion / Manifest / Preflight code paths. Those are #511 / #512 / #513 / #514.
- **No** Reading Overlay logic — that's #510. Registry only references `annotation_slug` for ID alignment.
- **No** changes to `ingest.py`, `inbox_writer.py`, `books.py` route handler. Read-only consumers of existing storage.
- **No** writes to vault, DB, or KB. Pure resolver.
- **No** LLM call. Pure deterministic.
- **No** new SQL migration. Reuses existing `books` table.
- **No** URL fetcher / web capture. PRD #508 confirmed cancelled.
- **No** discovery / enumeration API yet (`list_all_reading_sources()`). Single-key resolve only — list APIs land when Promotion Preflight (#511) needs them.
- **Don't** duplicate `annotation_slug` rules — import from `shared.annotation_store`.
- **Don't** speculatively model future Promotion fields on `ReadingSource`. Add when later slices need them.

---

## 7. Risks + open questions

| # | Item | Mitigation |
|---|---|---|
| R1 | `Book.lang_pair` split assumption (`en-zh` → `en`) may break for monolingual-zh book (`lang_pair="zh"`). | Test both shapes; fall back to first segment, default to `"en"` if missing. |
| R2 | `annotation_slug` calls `_slugify` which depends on existing book/inbox slug semantics. Change here propagates. | Don't change it; just import. Test asserts equality. |
| R3 | KB Source registry assumes filesystem layout — but textbook ingest is mid-repair (PR #441 staging). | Slice 1 only resolves a single given path. No enumeration. Stable as long as the path exists. |
| Q1 | Should `ReadingSource` carry a stable hash for promotion idempotency? | **Defer.** Promotion Manifest (#512) will define what idempotency means. Registry stays minimal. |
| Q2 | Should the registry surface a way to register a third-party source kind? | **Defer.** Three kinds cover today's three import paths (ebook upload / Toast inbox / textbook ingest). |
| Q3 | Path encoding for ebook blob (`books:{book_id}:en`) — is this too clever? | Alternative: `data/books/{book_id}/original.epub`. Use the latter — match `book_storage.read_book_blob` signature. |

→ **Pre-impl decision: take Q3 alternative**. Variant `path` for ebook becomes `data/books/{book_id}/original.epub` (or `bilingual.epub`). Registry will not synthesize blob bytes; consumer reads via `book_storage.read_book_blob` if needed.

---

## 8. Step-by-step execution plan

1. Add `shared/schemas/reading_source.py` (schema only).
2. Add `tests/fixtures/reading_source/` mini fixtures.
3. Add `shared/reading_source_registry.py` skeleton + 3 `_resolve_*` methods.
4. Add `tests/shared/test_reading_source_registry.py` — write failing tests first per AC matrix.
5. Implement until all tests pass.
6. `python -m pytest tests/shared/test_reading_source_registry.py -v` clean.
7. `python -m ruff check shared/ tests/shared/` clean.
8. P7-COMPLETION self-review block in PR body.
9. Open PR `impl/N509-reading-source-registry` → main; **do not** auto-merge.

**Dispatch decision:** Slice 1 is small (~200 LOC + tests). Run **locally in this worktree, not Sandcastle**. Sandcastle reserved for slices 6 / 7 (Source Map Builder + Concept Promotion Engine — those touch LLM batch).

---

## 9. Downstream slice dependencies (informational — do not implement here)

| Slice | Issue | Depends on #509 surface |
|---|---|---|
| #510 Reading Overlay V3 stabilization | needs `ReadingSource.source_id` to align with `annotation_slug` |
| #511 Promotion Preflight | needs registry list API (will add then) |
| #513 Source Map Builder | needs `ReadingSource.variants` to know which track to chunk |
| #514 Concept Promotion Engine | needs `ReadingSource.primary_lang` for evidence-language tagging |
| #517 Reading Context Package | needs `ReadingSource.has_evidence_track` to decide what to expose to Brook surface |

---

## 10. References

- ADR-024: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion
- Shared decisions: `memory/shared/decision/source_promotion_stage3_stage4_architecture.md`
- PRD: GitHub issue #508
- Issue: GitHub #509
- Schemas convention: `docs/principles/schemas.md`
