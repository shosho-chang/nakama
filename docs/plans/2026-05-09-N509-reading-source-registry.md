# N509 — Reading Source Registry (ADR-024 Slice 1) — v2

**Issue:** #509
**Branch:** `impl/N509-reading-source-registry`
**Worktree:** `E:/nakama-N509-source-registry`
**Status:** Plan only — no LLM batch, no Sandcastle dispatch, no code yet
**Author:** Claude (Opus 4.7) — handed off by 修修 2026-05-09
**Revision:** v3 (micro-rework after self-review NB1-NB4 + N2/N3/N6 — edge-case contracts tightened)
**Audit trail:**
- v1 → v2 reclassification: `docs/research/2026-05-09-N509-codex-audit-reclassified.md`
- v2 self-review (basis for v3 micro-rework): `docs/research/2026-05-09-N509-v2-self-review.md`

---

## 0. Scope anchor (ADR-024 § Decision + 修修 verbal direction)

ADR-024 main defines a **Reading Source** as "an ebook, web document, or inbox document before it is necessarily promoted into formal KB". 修修's verbal direction (2026-05-09) further reduces:

- **URL ingest cancelled** — web capture goes through Toast / Obsidian Clipper, which physically lands files in `Inbox/kb/`. Web documents ARE inbox documents in this system; they are **not** a separate `SourceKind`.
- **Textbook-grade is a promotion mode**, not a Reading Source kind. Textbook ingest never produces or consumes a `ReadingSource`; it operates on EPUBs / Raw markdown directly. `KB/Wiki/Sources/...` is **promotion output** — never a Reading Source.
- **Reading Source has exactly two kinds:** `ebook` and `inbox_document`.

> **Note on issue #509 body divergence (per N2 self-review finding)**
> Issue #509 body says "ebook, Inbox document, web document, and textbook-grade source" (4 kinds). v2/v3 deliberately reduce to **2 kinds** because:
> - ADR-024 §Context groups "Inbox / web document reading" as one category (web docs come via Toast / Obsidian Clipper, physically landing in `Inbox/kb/`).
> - 修修 verbal direction (2026-05-09): "URL ingest 已取消; web capture 交給 browser/Obsidian plugin" → web is metadata, not kind.
> - ADR-024 §Decision: textbook is a *promotion mode* applied to ebook source; never produces a `ReadingSource`.
>
> The issue body framing predates the post-PR-#441 ADR-024 + verbal direction. #509 v3 implements the latest design. Future readers landing here from issue #509 should expect this divergence.

修修 拍板 2026-05-09 (Q1/Q2/Q3):

- **Q1 (primary_lang)**: derive from upstream metadata (`BookMetadata.lang` for ebook; frontmatter `lang` for inbox). Do **not** use `Book.lang_pair.split("-")[0]` as long-term contract; do **not** wait for PR #507 `Book.mode` to merge.
- **Q2 (evidence policy)**: #509 exposes `has_evidence_track` + `evidence_reason` only. Block / defer / degrade policy lives in #511 (Promotion Preflight) and #513 / #514 (Source Map / Concept Promotion).
- **Q3 (enumeration)**: #509 is a resolver, not a directory. No `list_*` API in this slice. #511 extends the registry surface when it needs enumeration.

Slice 1 (#509) builds **only the resolution surface** — given a `BookKey` or `InboxKey`, return a normalized `ReadingSource` value object. No promotion logic, no manifest, no LLM, no UI.

---

## 1. 目標 (Goal)

Provide `shared.reading_source_registry.ReadingSourceRegistry.resolve(key) -> ReadingSource | None` that:

- Normalizes ebook (book in `books` table) and inbox markdown (`Inbox/kb/*.md`) into one `ReadingSource` value-object.
- Distinguishes **original / evidence track** (factual layer) from **display / bilingual track** (Reader UX layer).
- Lists available **variants** (en / zh-Hant / bilingual) with role + format + lang + path.
- Separates **stable `source_id`** (namespace-qualified, never mutates with frontmatter) from **mutable `annotation_key`** (used to join `KB/Annotations/{annotation_key}.md` per existing reader save path).
- Canonicalizes the inbox bilingual sibling pair: `InboxKey("foo.md")` and `InboxKey("foo-bilingual.md")` resolve to the **same logical source**.
- Surfaces `has_evidence_track` + `evidence_reason` without enforcing downstream policy.
- Public surface does not depend on FastAPI / Thousand Sunny route handler runtime.

---

## 2. 範圍 (Scope — files to add / modify)

### Add (new files)

| Path | Purpose |
|---|---|
| `shared/schemas/reading_source.py` | Pydantic schema: `ReadingSource`, `SourceVariant`, `SourceKind`, `TrackRole`, `VariantFormat` literals. `extra="forbid"` + value-object `frozen=True`. |
| `shared/reading_source_registry.py` | Resolver service: `BookKey`, `InboxKey` dataclasses + `ReadingSourceRegistry` class with `resolve(key)` dispatch + `_normalize_primary_lang` helper. |
| `tests/shared/test_reading_source_registry.py` | Unit tests (zero `fastapi` / `thousand_sunny` imports — confirms reusability). |
| `tests/fixtures/reading_source/` | Tiny EPUB (zipfile-built in test setup) + markdown fixtures (no external dependencies). |

### Touch (read-only — no behavior change in slice 1)

| Path | Why we read it |
|---|---|
| `shared/book_storage.py` | `get_book(book_id)` and `read_book_blob(book_id, lang=...)` — reused as-is. |
| `shared/epub_metadata.py` | `extract_metadata(blob_bytes)` returns `BookMetadata`; we use `.lang` as primary_lang truth source. |
| `shared/annotation_store.py` | `annotation_slug(filename, frontmatter)` — used to compute `annotation_key` for inbox resolution. |
| `shared/utils.py` | `extract_frontmatter`, `read_text` reused. |
| `shared/config.py` | `get_vault_path()` — resolved at registry instantiation, not import. |

### Out of scope (explicit — see §6 Boundaries)

- `kb_source` kind (any resolver against `KB/Wiki/Sources/...` — that's promotion output, owned by #513 / #515)
- `web_document` kind (web docs are physically inbox documents; origin is metadata, not kind)
- Source Promotion (slice 6 / 7), Promotion Manifest (slice 4), Promotion Preflight (slice 3), Reading Overlay Service (slice 2)
- URL / web-document fetchers (PRD #508 + 修修 verbal: cancelled)
- Textbook chapter-level decomposition (handled by ADR-020 ingest, not registry)
- Writes to vault, DB, KB
- New Reader UI behavior
- Enumeration / listing API

---

## 3. 輸入 (Inputs / upstream contracts)

| Input | Source | Notes |
|---|---|---|
| `book_id` | `shared/book_storage.py:get_book()` returns `Book`; reused as-is | `Book.has_original` controls evidence-track presence. `Book.lang_pair` is **NOT** used as primary_lang truth source (per Q1). |
| Ebook blob bytes | `shared/book_storage.py:read_book_blob(book_id, lang="en"\|"bilingual")` | Used to derive primary_lang via `extract_metadata`. One blob read per resolve; acceptable since registry is not a hot path. |
| Ebook metadata | `shared/epub_metadata.py:extract_metadata(blob_bytes)` returns `BookMetadata` | `BookMetadata.lang` is upstream truth source for ebook primary_lang (per Q1). |
| Inbox path | `Inbox/kb/{slug}.md` filesystem layout (PRD §資料 schema, `inbox_writer.py`) | Bilingual sibling = `{slug}-bilingual.md` (frontmatter `derived_from: {slug}.md`). |
| Inbox sibling collapse rule | `thousand_sunny/routers/robin.py:_get_inbox_files` (lines 136-194) | When `{stem}-bilingual.md` exists, the bilingual sibling is the user-facing reader/annotation target; the plain `{stem}.md` is hidden but kept on disk for re-translation. **Registry mirrors this rule for `annotation_key`.** |
| Annotation slug rule | `shared/annotation_store.py:annotation_slug` (lines 64-78) | Title-priority: frontmatter `title` > filename stem. Used to compute `annotation_key`, **NOT** `source_id`. |
| Vault root | `shared/config.py:get_vault_path()` | Resolved at registry instantiation (constructor takes optional override for tests). |

**Upstream invariants (don't break):**

- `Book` schema (`shared/schemas/books.py`) — leave untouched.
- `IngestResult` frontmatter (`shared/schemas/ingest_result.py`) — leave untouched.
- `annotation_slug()` semantics — registry uses, does not redefine.
- `extract_metadata()` semantics — registry uses, does not redefine.

**Parallel un-merged axis (PR #507 — Phase 1 monolingual-zh PRD):**

PR #507 introduces `Book.mode: Mode = Literal["monolingual-zh", "bilingual-en-zh"]` and deprecates `Book.lang_pair`. **#509 v2 deliberately does not depend on `Book.mode`** (per Q1) — primary_lang derives from `BookMetadata.lang` which is upstream of both `lang_pair` and `mode`. Therefore #509 lands cleanly regardless of PR #507 merge order. After PR #507 lands, #511 / #513 / #514 may also consume `Book.mode` directly; #509's surface remains stable.

---

## 4. 輸出 (Deliverables)

### 4.1 Schema sketch — `shared/schemas/reading_source.py`

```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

SourceKind = Literal["ebook", "inbox_document"]
"""Two kinds only.
ebook         — record in `books` table; epub blob in data/books/{book_id}/
inbox_document — markdown file in Inbox/kb/{slug}.md
                 (web docs land here via Toast/Obsidian Clipper —
                  origin is metadata, not a separate kind)
"""

TrackRole = Literal["original", "display"]
"""
original — factual evidence layer (en source for English book; zh source for
           Chinese article; absent when only a bilingual file exists)
display  — Reader UX layer (bilingual EPUB, bilingual sibling md). May equal
           original when there is no separate display track.
"""

VariantFormat = Literal["epub", "markdown"]


class SourceVariant(BaseModel):
    """One concrete file/blob backing a Reading Source."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    role: TrackRole
    format: VariantFormat
    lang: str  # bcp-47 short: "en", "zh-Hant", "bilingual"
    path: str
    """Canonical path:
       ebook   → 'data/books/{book_id}/original.epub'
                 or 'data/books/{book_id}/bilingual.epub'
                 (matches `book_storage.read_book_blob` signature)
       inbox   → vault-relative md path, e.g. 'Inbox/kb/foo.md'
    """
    bytes_estimate: int | None = None  # cheap stat; None if unknown


class ReadingSource(BaseModel):
    """Normalized Reading Source — pre-promotion identity + tracks.

    Slice 1 only. Source Promotion fields (manifest_id, promotion_status,
    review_decisions, ...) are added in later slices; do NOT add them
    speculatively.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1

    source_id: str
    """Stable namespace-qualified identity. NEVER mutates with frontmatter
    edits or sibling lifecycle changes.
        ebook          → 'ebook:{book_id}'
        inbox_document → 'inbox:{logical_original_path}'
                         where logical_original_path strips '-bilingual.md'
                         if present, otherwise the path as-is.
                         e.g. InboxKey('Inbox/kb/foo.md')
                          and InboxKey('Inbox/kb/foo-bilingual.md')
                          BOTH resolve to source_id 'inbox:Inbox/kb/foo.md'.
    """

    annotation_key: str
    """Key for joining KB/Annotations/{annotation_key}.md per existing
    reader save path. May mutate with frontmatter title changes (per
    annotation_slug semantics). Do NOT use for stable identity.
        ebook          → book_id
                         (matches existing books.py annotation save:
                          KB/Annotations/{book_id}.md)
        inbox_document → annotation_slug(user_facing_filename, frontmatter)
                         where user_facing = bilingual sibling if exists,
                         else plain (mirrors `_get_inbox_files` collapse).
    """

    kind: SourceKind
    title: str
    author: str | None = None

    primary_lang: str
    """BCP-47 short lang of the evidence/original-language content.
    Derived from upstream metadata; never from Book.lang_pair.
        ebook          → BookMetadata.lang via extract_metadata(blob_bytes)
        inbox_document → frontmatter 'lang' field; 'unknown' if absent
    Normalization (see _normalize_primary_lang in §4.3):
        any zh-* tag (zh, zh-TW, zh-Hant, zh-CN, zh-Hans) → 'zh-Hant'
        any en-* tag (en, en-US, en-GB)                    → 'en'
        missing / empty / other                            → 'unknown'
    NEVER 'bilingual'.
    """

    has_evidence_track: bool
    """True when an original-language track exists. Used by #511 / #513 /
    #514 to decide block / defer / degrade. #509 does NOT enforce policy.
    """

    evidence_reason: str | None = None
    """Short stable reason code when has_evidence_track=False; None when True.

    Closed set for schema_version=1:
        'no_original_uploaded' — ebook with has_original=False; only
                                 bilingual.epub blob exists.
        'bilingual_only_inbox' — inbox_document where only the
                                 -bilingual.md sibling exists; no plain
                                 original sibling on disk.

    Downstream slices may match on this reason code to apply policy.

    Extensibility (per N6 self-review): the closed set is FROZEN for
    schema_version=1. Later slices MAY extend with new codes only if they
    (a) bump `schema_version` on `ReadingSource`, (b) document the new code
    in this docstring, (c) update downstream policy in #511 / #513 / #514
    to handle it. Silent extension is forbidden.
    """

    variants: list[SourceVariant] = Field(default_factory=list, min_length=1)
    """At least one variant. Stable invariants:
       - has_evidence_track=True ⇒ exactly one variant has role='original'
       - has_evidence_track=False ⇒ no variant has role='original'
    """

    metadata: dict[str, str] = Field(default_factory=dict)
    """Cheap pass-through string-only metadata.

    NB3 contract (per v3 self-review): `lang_pair` is NEVER passed through
    even though `Book.lang_pair` exists in main. Consumers MUST use
    `ReadingSource.primary_lang` for language semantics. Re-deriving language
    from `Book.lang_pair` on the downstream side bypasses Q1 and is
    explicitly forbidden by §6 boundary 13.

        ebook → isbn, published_year, original_metadata_lang
                (raw `metadata.lang` before _normalize_primary_lang)
        inbox → original_url, fulltext_layer, fulltext_source
                (per IngestResult frontmatter contract)
    """
```

### 4.2 Service sketch — `shared/reading_source_registry.py`

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Union

from shared import book_storage
from shared.annotation_store import annotation_slug
from shared.config import get_vault_path
from shared.epub_metadata import extract_metadata
from shared.schemas.reading_source import ReadingSource, SourceVariant
from shared.utils import extract_frontmatter, read_text


@dataclass(frozen=True)
class BookKey:
    book_id: str


@dataclass(frozen=True)
class InboxKey:
    """Vault-relative path under Inbox/kb/, e.g. 'Inbox/kb/foo.md' or
    'Inbox/kb/foo-bilingual.md'. Both resolve to the same logical source.
    """
    relative_path: str


SourceKey = Union[BookKey, InboxKey]


def _normalize_primary_lang(raw: str | None) -> str:
    """Normalize BCP-47-ish lang tag to project-canonical short form.

    Per 修修's stated language scope (永遠 zh-Hant + en):
        any zh-* tag → 'zh-Hant'
        any en-* tag → 'en'
        missing / empty / unrecognized → 'unknown'
    NEVER returns 'bilingual'. Caller must not default to 'en' on missing.
    """
    if not raw:
        return "unknown"
    s = raw.lower().strip()
    if s.startswith("zh"):
        return "zh-Hant"
    if s.startswith("en"):
        return "en"
    return "unknown"


class ReadingSourceRegistry:
    def __init__(self, vault_root: Path | None = None) -> None:
        self._vault = Path(vault_root) if vault_root else get_vault_path()

    def resolve(self, key: SourceKey) -> ReadingSource | None:
        match key:
            case BookKey(book_id):
                return self._resolve_book(book_id)
            case InboxKey(rel):
                return self._resolve_inbox(rel)
        raise TypeError(f"Unknown SourceKey: {type(key).__name__}")

    # _resolve_book / _resolve_inbox — see §4.3 below.
```

### 4.3 Resolution rules

#### Ebook (`BookKey`)

> **Error handling contract (NB1, per v3 self-review)**: every step below that
> calls `book_storage.*` or `extract_metadata` MUST be wrapped so that any
> uncaught exception (`BookStorageError`, `FileNotFoundError`, IO error,
> `MalformedEPUBError`) results in `_resolve_book` **returning `None` and
> logging a `WARNING`** via `shared.log.get_logger("nakama.shared.reading_source_registry")`.
> The registry NEVER propagates uncontrolled exceptions to callers. Tests
> `test_resolve_ebook_malformed_blob` + `test_resolve_ebook_blob_io_error`
> cover this contract; see §7 R2 for the unified policy.

1. `book = book_storage.get_book(book_id)`. If `None` → return `None`. On unexpected exception → return `None` + log WARNING.
2. **Derive primary_lang from upstream metadata, not lang_pair (per Q1):**
   - Pick blob to introspect: `lang="en"` if `book.has_original` else `lang="bilingual"`.
   - Wrap blob read + metadata extract in try/except (NB1 contract):
     - `blob = book_storage.read_book_blob(book_id, lang=blob_lang)`
     - `metadata = extract_metadata(blob)`
   - On any failure (`BookStorageError`, `FileNotFoundError`, `MalformedEPUBError`, generic IO) → return `None` + log WARNING (`category="ebook_blob_read_failed"`, include `book_id`).
   - On success: `primary_lang = _normalize_primary_lang(metadata.lang)`.
3. Build identity:
   - `source_id = f"ebook:{book_id}"`.
   - `annotation_key = book_id` (matches existing `KB/Annotations/{book_id}.md` reader save path).
4. Build variants:
   - If `book.has_original`:
     - Append `SourceVariant(role="original", format="epub", lang=primary_lang, path=f"data/books/{book_id}/original.epub")`.
     - Append `SourceVariant(role="display", format="epub", lang="bilingual", path=f"data/books/{book_id}/bilingual.epub")`.
   - Else:
     - Append `SourceVariant(role="display", format="epub", lang=primary_lang, path=f"data/books/{book_id}/bilingual.epub")` — note `lang` reflects what's actually in the blob (e.g. `"zh-Hant"` for a Phase 1 monolingual-zh book; `"en"` for a true bilingual file).
5. Set `has_evidence_track = book.has_original`.
6. Set `evidence_reason = None` if `has_evidence_track` else `"no_original_uploaded"`.
7. Metadata pass-through (NB3 contract — `lang_pair` deliberately excluded):
   - `isbn`, `published_year`, `original_metadata_lang` (raw `metadata.lang` before normalize, useful for debugging "why did primary_lang come out as X").
   - **Do NOT include `Book.lang_pair`** even though it exists on `Book`. Per Q1 + §6 boundary 13, downstream consumers must read `ReadingSource.primary_lang` for language semantics; passing `lang_pair` through would seduce re-derivation.

#### Inbox document (`InboxKey`)

1. **Compute logical original path:**
   - If `relative_path.endswith("-bilingual.md")`: `logical_original = relative_path[:-len("-bilingual.md")] + ".md"`.
   - Else: `logical_original = relative_path`.
2. **Compute logical bilingual path:** `logical_bilingual = logical_original[:-3] + "-bilingual.md"`.
3. **Determine which siblings exist on disk:**
   - `original_exists = (vault / logical_original).is_file()`
   - `bilingual_exists = (vault / logical_bilingual).is_file()`
   - If neither exists → return `None`.
4. **Identity (per canonicalization rule):**
   - `source_id = f"inbox:{logical_original}"` (always — even if only bilingual exists on disk; logical_original is the stable anchor).

> ⚠️ **Design callout (N3 self-review) — `source_id` may refer to a non-existent path**
>
> In case (b) bilingual-only, the file at `logical_original` does NOT exist on disk, but `source_id` still uses it as the stable anchor. This is intentional:
>
> - If the user later re-translates and the original sibling appears, `source_id` remains stable (the new file just slots into the existing logical identity).
> - If the user later deletes the bilingual sibling and only the original survives, `source_id` remains stable too.
>
> **Trade-off**: `source_id` is **NOT a filesystem lookup key**; it is a **logical identity**. Implementations doing `Path(source_id.removeprefix("inbox:")).is_file()` will get `False` in case (b). To open / read the actual file, **always use `variants[*].path`**. Test 8 (`test_resolve_inbox_bilingual_only`) explicitly asserts this behavior.

5. **Pick the user-facing file for `annotation_key` (mirrors `_get_inbox_files` collapse rule):**
   - If `bilingual_exists`: user-facing = bilingual.
   - Else: user-facing = original.
   - Wrap `read_text` + `extract_frontmatter` in try/except (NB1 contract):
     - On `OSError` (file disappeared between `is_file()` check and read) or YAML parse failure → return `None` + log WARNING (`category="inbox_frontmatter_parse_failed"`, include `relative_path`).
   - On success: `annotation_key = annotation_slug(Path(user_facing_path).name, user_facing_frontmatter)`.
   - **NB4 contract**: if `annotation_slug` returns an empty string (frontmatter title empty AND filename stem produces no slug chars), `_resolve_inbox` raises `ValueError` with a message identifying the offending path. Do NOT emit a `ReadingSource` with empty `annotation_key`. See §7 R6 + §5 self-imposed gate.
6. **Build variants — three single-file cases explicit (per F9 fix):**

   | Case | Original on disk | Bilingual on disk | Variants | has_evidence_track | evidence_reason |
   |---|---|---|---|---|---|
   | (a) plain-only | ✓ | ✗ | 1× `role="original"`, lang=primary_lang | True | None |
   | (b) bilingual-only | ✗ | ✓ | 1× `role="display"`, lang="bilingual" | False | "bilingual_only_inbox" |
   | (c) both | ✓ | ✓ | 2× variants (original + display) | True | None |

7. **primary_lang (per Q1 — frontmatter, never inferred):**
   - For (a) and (c): from original sibling's frontmatter `lang` field.
   - For (b) bilingual-only: from bilingual sibling's frontmatter `lang` field (often missing → `"unknown"`). **NB2 contract — case (b) `primary_lang` is best-effort / low-confidence**: translator's `lang:` convention is not pinned (may write source lang OR target lang OR be empty). Downstream slices (#511 / #513 / #514) reading `primary_lang` MUST consult `evidence_reason == "bilingual_only_inbox"` and treat the value as low-confidence — never as authoritative evidence-language truth. See §7 R7.
   - Apply `_normalize_primary_lang`.
8. **Title** and **author**: from user-facing sibling's frontmatter (`title`, `author`).
9. **Metadata pass-through:** `original_url`, `fulltext_layer`, `fulltext_source` from user-facing frontmatter (string-coerced).

### 4.4 Test matrix — `tests/shared/test_reading_source_registry.py`

| # | Test | Setup | Assertion |
|---|---|---|---|
| 1 | `test_resolve_ebook_with_original` | Insert Book row with `has_original=True`; tiny EPUB blobs at `data/books/{book_id}/{original,bilingual}.epub` (built via zipfile in setup); original blob's `<dc:language>` = `en`. | 2 variants (original en + display bilingual); `has_evidence_track=True`; `evidence_reason is None`; `primary_lang == "en"`; `source_id == f"ebook:{book_id}"`; `annotation_key == book_id`. |
| 2 | `test_resolve_ebook_bilingual_only_true_bilingual` | Book with `has_original=False`; bilingual.epub blob whose `<dc:language>` = `en`. | 1 variant `role="display"`; `has_evidence_track=False`; `evidence_reason == "no_original_uploaded"`; `primary_lang == "en"`. |
| 3 | `test_resolve_ebook_phase1_monolingual_zh` | Book with `has_original=False`; bilingual.epub blob whose `<dc:language>` = `zh-TW`. | 1 variant `role="display"`, `lang == "zh-Hant"`; `has_evidence_track=False`; `evidence_reason == "no_original_uploaded"`; `primary_lang == "zh-Hant"`. |
| 4 | `test_resolve_ebook_lang_normalization` | Book with `<dc:language>` in {`zh`, `zh-TW`, `zh-Hant`, `zh-CN`, `zh-Hans`, `en`, `en-US`, `en-GB`, `ja`, ``}. | Asserts normalization table: `zh-*` → `"zh-Hant"`; `en-*` → `"en"`; `ja` → `"unknown"`; empty → `"unknown"`. (Parametrized.) |
| 5 | `test_resolve_ebook_missing` | Empty DB. | Returns `None`. |
| 6 | `test_resolve_ebook_orphan_blob_no_db_row` | Blob exists at `data/books/{book_id}/original.epub` but no row in `books`. | Returns `None` (DB is the source of truth; orphan blobs do not auto-register). |
| 7 | `test_resolve_inbox_plain_only` | `Inbox/kb/foo.md` exists with frontmatter `lang: en`; no bilingual sibling. | 1 variant `role="original"`; `has_evidence_track=True`; `source_id == "inbox:Inbox/kb/foo.md"`; `annotation_key == annotation_slug("foo.md", fm)`. |
| 8 | `test_resolve_inbox_bilingual_only` | Only `Inbox/kb/foo-bilingual.md` exists. | 1 variant `role="display"`, `lang == "bilingual"`; `has_evidence_track=False`; `evidence_reason == "bilingual_only_inbox"`; `source_id == "inbox:Inbox/kb/foo.md"` (logical, even though file doesn't exist on disk). |
| 9 | `test_resolve_inbox_both_siblings_canonicalize` | Both `foo.md` and `foo-bilingual.md` exist. Resolve once with `InboxKey("Inbox/kb/foo.md")` and once with `InboxKey("Inbox/kb/foo-bilingual.md")`. | Both calls return ReadingSource with **identical `source_id`** (`"inbox:Inbox/kb/foo.md"`); 2 variants in each result; `annotation_key` derives from bilingual sibling (matches `_get_inbox_files` collapse rule); `has_evidence_track=True`. |
| 10 | `test_resolve_inbox_missing_lang_frontmatter` | `Inbox/kb/foo.md` with frontmatter that has no `lang:` field. | `primary_lang == "unknown"` (does NOT default to `"en"`). |
| 11 | `test_resolve_inbox_missing_path` | Empty inbox. | Returns `None`. |
| 12 | `test_resolve_inbox_path_outside_vault` | `InboxKey("../../etc/passwd")` or path traversal attempt. | Raises `ValueError` (path normalization rejects escape). |
| 13 | `test_resolve_inbox_empty_annotation_slug` | Inbox file whose frontmatter title is empty AND filename stem produces empty slug after slugify. | **`pytest.raises(ValueError)`** with error message identifying the offending path. Does NOT return a `ReadingSource` with empty `annotation_key`. Locked by §5 self-imposed gate + §7 R6 contract. (NB4 contract — pinned in v3.) |
| 14 | `test_no_fastapi_imports` | `python -c "import shared.reading_source_registry"` in subprocess and assert no `fastapi` / `thousand_sunny` modules in `sys.modules` afterwards. | Pass — confirms reusability outside route handlers. |
| 15 | `test_vault_root_required` | `ReadingSourceRegistry(vault_root=None)` when `get_vault_path()` raises (e.g. unconfigured env). | Constructor fails fast with clear error; does not silently swallow. |
| 16 | `test_resolve_ebook_malformed_blob` | Book row exists; blob bytes are valid zip but missing OPF / `<dc:language>` (`MalformedEPUBError` or similar). | Returns `None` (not raises). Logs WARNING with `category="ebook_blob_read_failed"` + `book_id`. (NB1 contract — pinned in v3.) |
| 17 | `test_resolve_ebook_blob_io_error` | Book row exists; `data/books/{book_id}/{original,bilingual}.epub` deleted from disk between DB row insert and resolve call. | Returns `None` (not raises). Logs WARNING. (NB1 contract.) |
| 18 | `test_resolve_inbox_malformed_frontmatter` | `Inbox/kb/foo.md` exists but YAML frontmatter is malformed (e.g. unclosed quotes, tab indentation). | Returns `None` (not raises). Logs WARNING with `category="inbox_frontmatter_parse_failed"` + `relative_path`. (NB1 contract.) |

**Removed from v1 plan (no longer applicable):**
- `test_resolve_kb_source` and any `KBSourceKey` test — `kb_source` kind dropped.

### 4.5 Fixtures — `tests/fixtures/reading_source/`

- `inbox/foo-en-only.md` — frontmatter `title: Foo`, `lang: en`
- `inbox/bar-zh-only.md` — frontmatter `title: 巴爾`, `lang: zh-Hant`
- `inbox/baz.md` + `inbox/baz-bilingual.md` — sibling pair; `baz.md` has `lang: en`; bilingual has `derived_from: baz.md`
- `inbox/qux-bilingual.md` — bilingual without plain sibling
- `inbox/no-lang.md` — frontmatter without `lang:` field
- Ebook fixtures: built dynamically in `conftest.py` via `zipfile` + minimal OPF (set `<dc:language>` per test parameter); written to `tmp_path / "data" / "books" / "{book_id}"`. No binary EPUBs committed.

---

## 5. 驗收 (Acceptance criteria)

### Issue #509 列出的 4 條 AC（必過）

- [ ] A Reading Source can be resolved for at least one book fixture and one markdown document fixture.
- [ ] The resolved shape distinguishes original/evidence track from display/bilingual track.
- [ ] The service is reusable outside Thousand Sunny route handlers.
- [ ] Tests cover ebook, inbox/document, and missing evidence-track cases.

### Self-imposed gates (per ADR-024 + 拍板)

- [ ] No new dependency added; reuses existing `book_storage`, `annotation_store.annotation_slug`, `extract_frontmatter`, `extract_metadata`.
- [ ] `extra="forbid"` on every Pydantic model (per `docs/principles/schemas.md`).
- [ ] Test file imports zero `fastapi` / `thousand_sunny` symbols (asserted in test 14).
- [ ] `source_id` namespace prefix is fixed: `ebook:` or `inbox:` only.
- [ ] `source_id` for inbox documents is **stable across sibling lifecycle** — `InboxKey("foo.md")` and `InboxKey("foo-bilingual.md")` produce identical `source_id` (asserted in test 9).
- [ ] `annotation_key` for inbox uses `annotation_slug(user_facing_file, fm)` where user_facing follows `_get_inbox_files` collapse rule.
- [ ] `primary_lang` derives from upstream metadata only — no `Book.lang_pair.split` anywhere in code (asserted by `grep -n 'lang_pair' shared/reading_source_registry.py shared/schemas/reading_source.py` returning at most legacy-comment matches).
- [ ] `primary_lang` never returns `"bilingual"`.
- [ ] `primary_lang` returns `"unknown"` (not `"en"`) when upstream metadata is missing.
- [ ] `evidence_reason` uses the closed set `{None, "no_original_uploaded", "bilingual_only_inbox"}`. **Closed for schema_version=1**; extension requires bumping `schema_version` + docstring update + downstream policy update (per N6 v3 contract).
- [ ] **NB1 (v3)**: every blob-read / metadata-extract / frontmatter-parse failure inside `_resolve_book` or `_resolve_inbox` returns `None` + logs WARNING via `shared.log.get_logger("nakama.shared.reading_source_registry")`. Registry NEVER propagates uncontrolled exceptions to callers. Asserted by tests 16/17/18.
- [ ] **NB3 (v3)**: `Book.lang_pair` is NOT included in `ReadingSource.metadata` for ebook resolution. Asserted by `grep -n '"lang_pair"' shared/reading_source_registry.py` returning zero matches and an explicit assertion in tests 1-3 that `"lang_pair" not in result.metadata`.
- [ ] **NB4 (v3)**: `_resolve_inbox` raises `ValueError` (with message identifying the offending path) when `annotation_slug` would return an empty string. Asserted by test 13.
- [ ] **NB2 (v3)**: case (b) bilingual-only inbox `primary_lang` is documented as low-confidence in schema docstring + §4.3 step 7 + §7 R7. No code-level enforcement at #509; downstream policy contract.
- [ ] `python -m pytest tests/shared/test_reading_source_registry.py -v` 全綠.
- [ ] `python -m ruff check shared/ tests/shared/` no error.
- [ ] PR body contains a P7-COMPLETION self-review block.

---

## 6. 邊界 (Boundaries — what NOT to do)

| # | Don't | Why |
|---|---|---|
| 1 | No `kb_source` kind / `KBSourceKey` | Stage 3/4 promotion output ≠ Stage 2 reading input (ADR-024) |
| 2 | No `web_document` kind | Web docs land in `Inbox/kb/` via Toast/Obsidian Clipper; origin is metadata not kind |
| 3 | No textbook kind | Textbook ingest is a promotion mode applied to ebook source; never produces or consumes a `ReadingSource` |
| 4 | No Promotion / Manifest / Preflight logic | Slices #511-#515 |
| 5 | No Reading Overlay logic | Slice #510. Registry only references `annotation_slug` for `annotation_key` |
| 6 | No changes to `agents/robin/ingest.py`, `inbox_writer.py`, `thousand_sunny/routers/*` | Read-only consumers of existing storage |
| 7 | No writes to vault, DB, or KB | Pure resolver |
| 8 | No LLM call | Pure deterministic |
| 9 | No new SQL migration | Reuses existing `books` table |
| 10 | No URL fetcher / web capture | Cancelled per PRD #508 + 修修 verbal |
| 11 | No enumeration / listing API in #509 (per Q3) | `#511` extends registry surface when it needs scanning |
| 12 | No dependency on `Book.mode` from PR #507 (per Q1) | #509 must land cleanly regardless of PR #507 merge order; primary_lang derives from `BookMetadata.lang` upstream of both `lang_pair` and `mode` |
| 13 | No use of `Book.lang_pair` to derive `primary_lang` (per Q1) | `lang_pair` is being deprecated by PR #507; `BookMetadata.lang` is the upstream truth |
| 14 | No `evidence_reason` policy enforcement (per Q2) | `#509` exposes flag + reason only; block / defer / degrade lives in `#511` / `#513` / `#514` |
| 15 | Don't speculatively model future Promotion fields on `ReadingSource` | Add when later slices need them |
| 16 | Don't redefine `annotation_slug` rules — import from `shared.annotation_store` | One source of truth |

---

## 7. Risks + open questions (revised post-Q1/Q2/Q3 拍板)

| # | Item | Mitigation |
|---|---|---|
| R1 | One extra blob read per `_resolve_book` (for `extract_metadata` to get primary_lang) | Cheap (EPUB OPF parse <100ms); registry is not a hot path. If a slice later proves it needs caching, add `lru_cache` on `(book_id, blob_hash)` then. |
| R2 | **NB1 unified failure policy (v3)**: any blob-read / metadata-extract / frontmatter-parse failure inside `_resolve_book` or `_resolve_inbox` (e.g. `MalformedEPUBError`, `BookStorageError`, `FileNotFoundError`, generic IO error, malformed YAML in inbox frontmatter) | **Contract**: catch the exception, `return None` from the resolve method, log a `WARNING`-level message via `shared.log.get_logger("nakama.shared.reading_source_registry")` with structured fields `category` (`"ebook_blob_read_failed"` / `"inbox_frontmatter_parse_failed"`) and the input key. **Never** propagate uncontrolled exceptions to the caller. Tests 16/17/18 cover the three categories. Asserted by §5 self-imposed gate (NB1). |
| R3 | `BookMetadata.lang` may be a multilang string (`"en;zh-TW"`) — EPUB spec allows multiple `<dc:language>` tags | `extract_metadata` currently takes the first; document this in the helper. If a future ebook has multi-lang OPF and we need finer behavior, extend the helper not the registry. |
| R4 | PR #507 merging will introduce `Book.mode`; #511 / #513 / #514 may consume `Book.mode` directly | Out of scope for #509. Registry surface is stable across PR #507 merge. Note in PR description: "no migration needed when PR #507 lands". |
| R5 | Inbox `path_outside_vault` security check needs path-resolution carefully on Windows (case-insensitive) | Use `Path.resolve()` + `is_relative_to(vault_root)` check; covered by test 12. |
| R6 | **NB4 contract lock (v3)**: empty-slug edge case (test 13) — annotation_slug returns empty string when frontmatter title is empty AND filename produces no slug chars | **Contract**: `_resolve_inbox` raises `ValueError` with a message identifying the offending path. #509 does NOT emit a `ReadingSource` with empty `annotation_key`. Locked in §5 self-imposed gate (NB4) and asserted by test 13. #510 must align if it needs different behavior — relaxation requires a #509 follow-up PR, not silent override. |
| R7 | **NB2 low-confidence primary_lang (v3)**: case (b) bilingual-only inbox `primary_lang` derived from bilingual sibling frontmatter is best-effort — translator's `lang:` convention is not pinned (may write source lang OR target lang OR be empty) | **Contract surface**: schema docstring on `evidence_reason` + §4.3 inbox step 7 (b). Downstream slices (#511 / #513 / #514) **MUST** treat `evidence_reason == "bilingual_only_inbox"` as a signal that `primary_lang` is low-confidence and is not authoritative evidence-language truth. #509 surfaces flag + reason; downstream picks policy per Q2. No code-level enforcement at #509. |
| Q-OPEN-1 | Should `metadata.original_metadata_lang` (raw before normalize) be preserved on `ReadingSource.metadata`? | **Lean: yes** — useful for debugging "why is primary_lang 'unknown'?" without re-reading the EPUB. |

---

## 8. Step-by-step execution plan

1. Add `shared/schemas/reading_source.py` (schema only — no behavior).
2. Add `tests/fixtures/reading_source/` markdown fixtures.
3. Add `tests/shared/test_reading_source_registry.py` skeleton with the 15-test matrix marked `@pytest.mark.xfail` (red).
4. Add `shared/reading_source_registry.py` with `_normalize_primary_lang` + `BookKey` / `InboxKey` dataclasses + skeleton `resolve()`.
5. Implement `_resolve_book` until tests 1-6 pass (un-xfail one at a time).
6. Implement `_resolve_inbox` until tests 7-13 pass.
7. Implement reusability + bootstrap tests 14-15.
8. `python -m pytest tests/shared/test_reading_source_registry.py -v` clean.
9. `python -m ruff check shared/ tests/shared/` clean.
10. Open PR `impl/N509-reading-source-registry` → main with P7-COMPLETION block.
11. **Do not** auto-merge; await 修修 review.

**Dispatch decision:** Slice 1 is small (~250 LOC + tests). Run **locally in this worktree, not Sandcastle**. Sandcastle reserved for slices 6 / 7 (Source Map Builder + Concept Promotion Engine — those touch LLM batch).

---

## 9. Downstream slice dependencies (informational — do not implement here)

| Slice | Issue | Depends on #509 surface |
|---|---|---|
| #510 Reading Overlay V3 stabilization | `ReadingSource.annotation_key` must equal what existing reader save path uses (`book_id` for ebook, `annotation_slug(...)` for inbox). |
| #511 Promotion Preflight | (a) Per Q3: extends registry with `list_*` API when needed. (b) Reads `has_evidence_track` + `evidence_reason` to apply block/defer/degrade policy (per Q2). (c) May consume `Book.mode` directly if PR #507 merged by then. |
| #513 Source Map Builder | `ReadingSource.variants` to know which track to chunk. `primary_lang` for chunk language tagging. `has_evidence_track=False` → may skip or degrade. |
| #514 Concept Promotion Engine | `ReadingSource.primary_lang` for evidence-language tagging. `evidence_reason` for low-confidence cross-lingual exception handling. |
| #515 Promotion Commit + Acceptance Gate | `source_id` for manifest record keying (stable across sibling lifecycle). |
| #516 Promotion Review UI | Reads `ReadingSource` for display; does not modify. |
| #517 Reading Context Package | `has_evidence_track` to decide what to expose to Brook surface. `annotation_key` to fetch overlay. |

---

## 10. References

- ADR: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md` (main, PR #441 merged)
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion (canonical vocabulary)
- Shared decision: `memory/shared/decision/source_promotion_stage3_stage4_architecture.md`
- Cross-session findings: `docs/research/2026-05-09-digest-md-cross-session-findings.md` (修修 language scope: 永遠 zh-Hant + en)
- **Codex audit reclassification** (this rework's basis): `docs/research/2026-05-09-N509-codex-audit-reclassified.md`
- **PR #507 Phase 1 monolingual-zh PRD** (parallel un-merged axis):
  - Branch: `feat/monolingual-zh-pilot-prd-handoff`
  - PRD body: `docs/plans/2026-05-09-monolingual-zh-reader-annotation-pilot-prd.md` (qa-adr021)
  - Grill summary: `docs/plans/2026-05-08-monolingual-zh-source-grill.md` (qa-adr021)
- Issue #509: `[ADR-024 S1] Reading Source Registry`
- Parent PRD #508: `Source Promotion and Reading Context Package`
- Schemas principle: `docs/principles/schemas.md`
- Existing schema patterns: `shared/schemas/{books,ingest_result,kb,annotations}.py`
- Inbox sibling collapse rule: `thousand_sunny/routers/robin.py:_get_inbox_files` lines 136-194

---

## 11. v1 → v2 Diff summary (for reviewers)

| Area | v1 | v2 |
|---|---|---|
| `SourceKind` | 3 (`ebook` / `inbox_document` / `kb_source`) | **2** (`ebook` / `inbox_document`) — F1, F2 |
| Source keys | `BookKey` / `InboxKey` / `KBSourceKey` | `BookKey` / `InboxKey` only — F2 |
| `source_id` | `source_id == annotation_slug(...)` for inbox (mutates with frontmatter) | **Two-field model**: stable `source_id` (`ebook:{book_id}` / `inbox:{logical_original_path}`) + mutable `annotation_key` (per-existing reader save path) — F3 |
| Inbox sibling | Single-key resolve, no canonicalization | **Canonicalize**: both `InboxKey("foo.md")` and `InboxKey("foo-bilingual.md")` resolve to identical `source_id` — F4 |
| Inbox single-file rule | Ambiguous ("only one exists = original/True"); contradicted test matrix | **Three explicit cases** (a) plain-only / (b) bilingual-only / (c) both — F9 |
| Ebook path syntax | `books:{book_id}:en` (later contradicted by `data/books/{book_id}/original.epub`) | Single canonical: `data/books/{book_id}/original.epub` / `bilingual.epub` — F8 |
| `primary_lang` derivation | `Book.lang_pair.split("-")[0]`, default `"en"` | `BookMetadata.lang` via `extract_metadata` + `_normalize_primary_lang`; never `"en"` default — Q1, F5 |
| `has_evidence_track=False` policy | Silent | `evidence_reason` field exposes WHY; policy lives in `#511` / `#513` / `#514` — Q2, F6 |
| Enumeration | "Out of scope, add when needed" (vague) | Explicit boundary: `#511` extends registry surface — Q3, F7 |
| Test matrix | 9 tests | **15 tests** — added: orphan blob, lang normalization, sibling canonicalize, missing lang frontmatter, path outside vault, empty annotation_slug, vault_root required, Phase 1 monolingual-zh case |
| Phase 1 PRD (PR #507) | Not mentioned | §3, §6 #12, §10 explicit references; §11 R4 risk noted — N1, N3 |
| `kb_source` fixture | Included | Removed |

---

## 12. v2 → v3 Micro-rework summary (edge-case contracts tightened)

v3 is a micro-rework: no architectural changes; only edge-case contracts tightened per `docs/research/2026-05-09-N509-v2-self-review.md`.

| # | Self-review finding | v2 state | v3 contract |
|---|---|---|---|
| **NB1** | Error handling coverage incomplete (only `extract_metadata` was specified) | §7 R2 covered only `MalformedEPUBError` | **Unified failure policy**: every blob-read / metadata-extract / frontmatter-parse failure → return `None` + WARNING log via `shared.log.get_logger("nakama.shared.reading_source_registry")`. NEVER propagate uncontrolled exceptions. §4.3 ebook step 1/2 + inbox step 5 wrapped explicitly. New tests 16/17/18. §5 self-imposed gate added. |
| **NB2** | bilingual-only inbox `primary_lang` ambiguity | Implicit best-effort | **Documented contract**: case (b) `primary_lang` is best-effort / low-confidence; downstream slices (#511 / #513 / #514) **MUST** consult `evidence_reason == "bilingual_only_inbox"` and treat lang as low-confidence. New §7 R7 + §4.3 inbox step 7 (b) note + §5 self-imposed gate. No code-level enforcement at #509 per Q2. |
| **NB3** | `metadata.lang_pair` legacy passthrough | `lang_pair` exposed as "legacy reference" in metadata | **Removed entirely**: §4.3 ebook step 7 + §4.1 schema docstring forbid passing `lang_pair` through. `grep -n '"lang_pair"' shared/reading_source_registry.py` must return zero matches. Tests 1-3 explicitly assert `"lang_pair" not in result.metadata`. §5 self-imposed gate added. |
| **NB4** | Test 13 implementation-choice loose ("raise OR return empty") | §7 R6 had "lean: raise" but not pinned | **Locked**: `_resolve_inbox` raises `ValueError` with offending-path message when `annotation_slug` returns empty. Test 13 rewritten to `pytest.raises(ValueError)`. §7 R6 promoted to "Contract"; §5 self-imposed gate added. |
| **N2** | Issue #509 body 4-kind framing divergence not explicitly noted | §0 said "2 kinds" without acknowledging issue body | **Explicit divergence note** added to §0 — quotes 4-kind issue body, justifies 2-kind reduction via ADR-024 §Context + 修修 verbal + textbook-as-promotion-mode. |
| **N3** | bilingual-only `source_id` refers to non-existent path (reverse-intuitive) | Buried in step 4 prose + test 8 parenthetical | **Promoted to bold design callout** in §4.3 inbox after step 4: explicit "logical identity, NOT filesystem lookup key; use `variants[*].path` for file access". |
| **N6** | `evidence_reason` closed set extensibility unclear | Closed set listed but no extension protocol | **Extension protocol documented** in schema docstring: closed for `schema_version=1`; future extension requires (a) bump `schema_version`, (b) docstring update, (c) downstream policy update. §5 self-imposed gate updated. |

**Test count**: 15 → **18** (added test 16 `test_resolve_ebook_malformed_blob`, test 17 `test_resolve_ebook_blob_io_error`, test 18 `test_resolve_inbox_malformed_frontmatter`).

**Self-imposed gates**: 13 → **17** (added NB1 / NB2 / NB3 / NB4 + extended evidence_reason gate with N6 extension protocol).

**Boundaries**: 16 (unchanged; v3 doesn't add new boundaries — it tightens contracts within existing boundary scope).

**Items NOT changed in v3 (still v2-as-shipped)**:
- 2-kind `SourceKind` enum
- `source_id` / `annotation_key` two-field model
- Inbox sibling canonicalization rule
- Single canonical ebook path syntax
- Three-case inbox single-file rule
- Q1 / Q2 / Q3 拍板 contracts
- §6 boundaries (12-16 stay; new contracts referenced from boundaries 13/14)
- Plan §8 step-by-step execution plan
- §9 downstream slice dependencies
