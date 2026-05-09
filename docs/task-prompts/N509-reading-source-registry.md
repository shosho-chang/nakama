# Agent Brief — N509 Reading Source Registry (ADR-024 Slice 1) — v2

**Issue:** [#509](https://github.com/shosho-chang/nakama/issues/509)
**Parent PRD:** #508
**Slice of:** ADR-024 (`docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`)
**Branch:** `impl/N509-reading-source-registry`
**Worktree (host):** `E:/nakama-N509-source-registry`
**Implementation plan:** `docs/plans/2026-05-09-N509-reading-source-registry.md` v3 (read this first)
**Audit reclassification:** `docs/research/2026-05-09-N509-codex-audit-reclassified.md`
**v2 self-review (basis for v3 micro-rework):** `docs/research/2026-05-09-N509-v2-self-review.md`
**Frozen:** 2026-05-09 (v3 — micro-rework after self-review NB1-NB4 + N2/N3/N6 — edge-case contracts tightened; no architectural change from v2)

P9 六要素。Brief 是 self-contained handoff，不靠群聊歷史也能上手。

---

## 0. Revision summary

### 0.1 v1 → v2 (architectural rework)

v1 Brief got Codex push-back audit (REWORK verdict). 修修 reclassified findings against latest ADR-024 direction and 拍板 three open design questions. v2 incorporates:

- **Drop `kb_source` kind** — promotion output ≠ Reading Source (F2 valid blocker).
- **Two kinds only**: `ebook` + `inbox_document`. Web docs land in inbox via Toast/Obsidian Clipper; not a separate kind. Textbook is a promotion mode, not a Reading Source kind.
- **Split stable `source_id` from mutable `annotation_key`** (F3 valid blocker).
- **Canonicalize inbox bilingual sibling pair** (F4 valid blocker).
- **Single canonical ebook path syntax** (F8 doc bug).
- **Three explicit inbox single-file cases** (F9 logic bug).
- **`primary_lang` from `BookMetadata.lang` upstream** — not from `Book.lang_pair`, not waiting for PR #507 `Book.mode` (per Q1 拍板).
- **`#509` exposes `has_evidence_track` + `evidence_reason`; downstream policy lives in `#511` / `#513` / `#514`** (per Q2 拍板).
- **No enumeration API in `#509`; `#511` extends registry surface when needed** (per Q3 拍板).

### 0.2 v2 → v3 (edge-case contract tightening)

v3 is a micro-rework: **no architectural changes**, only edge-case contracts tightened per `docs/research/2026-05-09-N509-v2-self-review.md`. Brief + plan diffs:

- **NB1**: unified failure policy — every blob-read / metadata-extract / frontmatter-parse failure → return `None` + WARNING log; never propagate uncontrolled exceptions. Tests 16/17/18 added.
- **NB2**: case (b) bilingual-only inbox `primary_lang` documented as best-effort / low-confidence; downstream MUST consult `evidence_reason == "bilingual_only_inbox"` and not treat lang as authoritative evidence-language truth.
- **NB3**: `Book.lang_pair` removed from `metadata` pass-through (was "legacy reference" in v2; v3 forbids entirely). `grep '"lang_pair"'` must return zero.
- **NB4**: empty `annotation_slug` behavior locked to `_resolve_inbox` raises `ValueError`. Test 13 rewritten to `pytest.raises(ValueError)`.
- **N2**: explicit divergence note added — issue #509 body's 4-kind framing predates ADR-024 + 修修 verbal direction; v2/v3 implements 2-kind reduction.
- **N3**: bilingual-only `source_id`-points-at-non-existent-path promoted to bold design callout (logical identity ≠ filesystem lookup; use `variants[*].path`).
- **N6**: `evidence_reason` closed-set extension protocol documented (closed for `schema_version=1`; extension requires schema bump + docstring update + downstream policy update).

### 0.3 Note on issue #509 body

Issue #509 body uses 4-kind framing (ebook + Inbox + web doc + textbook-grade) which predates the latest ADR-024 + 修修 verbal direction. v2/v3 deliberately reduce to 2 kinds (ebook + inbox_document); web docs are inbox docs (Toast/Obsidian Clipper); textbook is a promotion mode not a Reading Source kind. Future readers landing here from issue #509 should expect this divergence.

---

## 1. 目標

在 `shared/` 提供 `ReadingSourceRegistry.resolve(key) -> ReadingSource | None`，把 ebook (`books` table 內) 與 inbox markdown (`Inbox/kb/*.md`) 兩種 Reading Source normalize 成單一 `ReadingSource` value-object。

關鍵 contracts：

- **2 kinds**: `ebook` / `inbox_document` 只此兩種（textbook = promotion mode 不是 kind；web doc 物理上就是 inbox doc）
- **Track 切分**: `original` (factual evidence) vs `display` (Reader UX, bilingual)
- **Identity 雙鍵**: 穩定 `source_id` (namespace prefix `ebook:` / `inbox:`) + mutable `annotation_key` (對齊既有 reader save path)
- **Sibling canonicalize**: `InboxKey("foo.md")` 與 `InboxKey("foo-bilingual.md")` 解析到**同一 logical source**
- **`primary_lang`** 走 upstream metadata，永不來自 `Book.lang_pair`
- **`has_evidence_track` + `evidence_reason`** 暴露 flag + 原因，**不執行 policy**

不啟動 Source Promotion；只做 identity + variants 解析。為 #510 Reading Overlay、#511 Promotion Preflight、#513 Source Map Builder、#517 Reading Context Package 提供共同接點。

---

## 2. 範圍

新增三個檔 + 一組 fixture：

| 路徑 | 動作 |
|---|---|
| `shared/schemas/reading_source.py` | **新增** — Pydantic 模型 (`ReadingSource`, `SourceVariant`) + `SourceKind`/`TrackRole`/`VariantFormat` Literal。`extra="forbid"` + value-object `frozen=True`。|
| `shared/reading_source_registry.py` | **新增** — `ReadingSourceRegistry` + `BookKey` / `InboxKey` dataclass + `_normalize_primary_lang` helper + `resolve(key)` dispatch。|
| `tests/shared/test_reading_source_registry.py` | **新增** — 15 個 unit test（見 §5 驗收）。零 `fastapi` import。|
| `tests/fixtures/reading_source/` | **新增** — 5 個 inbox md fixtures + 動態 EPUB（test setup 用 `zipfile` 生成）。|

讀取（不改）：`shared/book_storage.py`、`shared/epub_metadata.py:extract_metadata`、`shared/annotation_store.py:annotation_slug`、`shared/utils.py:extract_frontmatter`、`shared/config.py:get_vault_path`、`shared/schemas/books.py`。

**完全不碰**：`agents/robin/ingest.py`、`agents/robin/inbox_writer.py`、`thousand_sunny/routers/books.py`、`thousand_sunny/routers/robin.py`、`KB/Wiki/Sources/...`、任何 LLM call、任何 vault/KB write、任何 SQL migration、任何 URL fetcher。

---

## 3. 輸入

### 主要 contracts

- **Source 1 (ebook)**: `shared.book_storage.get_book(book_id)` → `Book`. `Book.has_original` 控制 evidence-track 存在與否。**不**用 `Book.lang_pair`。
- **Source 2 (ebook metadata)**: `shared.book_storage.read_book_blob(book_id, lang="en"|"bilingual")` → bytes → `shared.epub_metadata.extract_metadata(bytes)` → `BookMetadata`. **`BookMetadata.lang` 是 ebook primary_lang 的 upstream truth source**。
- **Source 3 (inbox doc)**: `Inbox/kb/{slug}.md` 由 `inbox_writer.InboxWriter.write_to_inbox` 寫；雙語 sibling 規則：`{slug}-bilingual.md` + frontmatter `derived_from: {slug}.md`。Frontmatter `lang` field 是 inbox primary_lang truth source。
- **Inbox sibling collapse rule**: `thousand_sunny/routers/robin.py:_get_inbox_files` (lines 136-194) — `{stem}-bilingual.md` 存在時，user 看到+標註的是 bilingual sibling，原始 `{stem}.md` hidden。Registry 對 `annotation_key` 鏡像此規則。
- **Slug 規則**: `shared.annotation_store.annotation_slug(filename, frontmatter)`。**僅用於 `annotation_key`，不用於 `source_id`**（per F3 修正）。

### Parallel un-merged axis — PR #507

PR [#507](https://github.com/shosho-chang/nakama/pull/507) (`feat/monolingual-zh-pilot-prd-handoff`) 是 Phase 1 monolingual-zh Reader + Annotation pilot PRD，**未 merge 到 main**。它引入 `Book.mode: Mode = Literal["monolingual-zh", "bilingual-en-zh"]` 並 deprecate `Book.lang_pair`。

**#509 v2 刻意不依賴 `Book.mode`**（per Q1 拍板）— `primary_lang` 從 `BookMetadata.lang` upstream 取，這是 `lang_pair` 與 `mode` 的共同上游。所以 #509 與 PR #507 兩條軸線可以獨立 ship 不互鎖。PR #507 merge 後，#511 / #513 / #514 可以另外消費 `Book.mode`，但 #509 surface 不變。

### Documentation hierarchy 沿用

- ADR-024 (main, PR #441 merged) — canonical decision
- `agents/robin/CONTEXT.md` § Source Promotion — vocabulary
- `memory/shared/decision/source_promotion_stage3_stage4_architecture.md` — shared decision
- `docs/research/2026-05-09-digest-md-cross-session-findings.md` — 修修 language scope (永遠 zh-Hant + en)

---

## 4. 輸出

### 4.1 `ReadingSource` schema

```python
SourceKind   = Literal["ebook", "inbox_document"]   # 2 kinds only
TrackRole    = Literal["original", "display"]
VariantFormat = Literal["epub", "markdown"]


class SourceVariant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    role: TrackRole
    format: VariantFormat
    lang: str          # "en" / "zh-Hant" / "bilingual"
    path: str
    """Canonical path syntax (single contract):
       ebook → 'data/books/{book_id}/original.epub' / 'bilingual.epub'
       inbox → vault-relative md path, e.g. 'Inbox/kb/foo.md'
    """
    bytes_estimate: int | None = None


class ReadingSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1

    source_id: str
    """Stable namespace-qualified ID. NEVER mutates with frontmatter or
    sibling lifecycle changes.
        ebook          → 'ebook:{book_id}'
        inbox_document → 'inbox:{logical_original_path}'
                         (strips '-bilingual.md' if present)
    """

    annotation_key: str
    """Join key for KB/Annotations/{annotation_key}.md. May mutate with
    frontmatter title changes (per annotation_slug semantics). Do NOT
    use for stable identity.
        ebook          → book_id
        inbox_document → annotation_slug(user_facing_filename, fm)
                         where user_facing follows _get_inbox_files collapse
    """

    kind: SourceKind
    title: str
    author: str | None = None

    primary_lang: str
    """BCP-47 short. From upstream metadata only.
        ebook → BookMetadata.lang via extract_metadata(blob)
        inbox → frontmatter 'lang' field
    Normalization: zh-* → 'zh-Hant'; en-* → 'en'; missing → 'unknown'.
    Never 'bilingual'. Never defaults to 'en'.
    """

    has_evidence_track: bool
    """True when an original-language track exists. Downstream slices
    (#511 / #513 / #514) decide block / defer / degrade. #509 enforces no policy."""

    evidence_reason: str | None = None
    """Closed set for schema_version=1 when has_evidence_track=False:
        'no_original_uploaded'  — ebook with has_original=False
        'bilingual_only_inbox'  — inbox with only -bilingual.md sibling
       None when has_evidence_track=True.
    Closed for v1 (per N6 v3 contract). Extension requires (a) bump
    schema_version, (b) docstring update, (c) downstream policy update.
    Silent extension forbidden.
    """

    variants: list[SourceVariant] = Field(default_factory=list, min_length=1)
    """Invariants:
       has_evidence_track=True  ⇒ exactly one variant has role='original'
       has_evidence_track=False ⇒ no variant has role='original'
    """

    metadata: dict[str, str] = Field(default_factory=dict)
    """String-only pass-through. NB3 (v3): lang_pair is NEVER passed through —
    consumers MUST use ReadingSource.primary_lang for language semantics.
        ebook → isbn, published_year, original_metadata_lang
        inbox → original_url, fulltext_layer, fulltext_source
    """
```

### 4.2 Resolver service

```python
@dataclass(frozen=True)
class BookKey:
    book_id: str

@dataclass(frozen=True)
class InboxKey:
    relative_path: str   # e.g. 'Inbox/kb/foo.md' or 'Inbox/kb/foo-bilingual.md'

SourceKey = BookKey | InboxKey   # 2 keys only — no KBSourceKey


def _normalize_primary_lang(raw: str | None) -> str:
    """zh-* → 'zh-Hant'; en-* → 'en'; missing/other → 'unknown'.
    Never returns 'bilingual'. Never defaults to 'en'.
    """
    if not raw: return "unknown"
    s = raw.lower().strip()
    if s.startswith("zh"): return "zh-Hant"
    if s.startswith("en"): return "en"
    return "unknown"


class ReadingSourceRegistry:
    def __init__(self, vault_root: Path | None = None) -> None: ...
    def resolve(self, key: SourceKey) -> ReadingSource | None: ...
```

### 4.3 兩種 kind 的解析規則

#### Ebook (`BookKey`)

> **NB1 (v3) error handling contract**: every step that calls `book_storage.*` or `extract_metadata` MUST be wrapped — any uncaught exception (`BookStorageError`, `FileNotFoundError`, `MalformedEPUBError`, generic IO) → `_resolve_book` returns `None` + logs WARNING via `shared.log.get_logger("nakama.shared.reading_source_registry")`. Never propagate to caller. See plan §7 R2 + tests 16/17.

1. `book = book_storage.get_book(book_id)`. `None` → return `None`. Unexpected exception → return `None` + WARNING.
2. **Derive primary_lang from upstream metadata (per Q1):**
   - blob_lang = `"en"` if `book.has_original` else `"bilingual"`
   - Wrap blob read + metadata extract in try/except (NB1 contract); on any failure → return `None` + WARNING (`category="ebook_blob_read_failed"`)
   - On success: `blob = read_book_blob(...); metadata = extract_metadata(blob); primary_lang = _normalize_primary_lang(metadata.lang)`
3. `source_id = f"ebook:{book_id}"`; `annotation_key = book_id`.
4. Variants:
   - `has_original=True`: 2 variants — `original` (epub, primary_lang) + `display` (epub, "bilingual")
   - `has_original=False`: 1 variant — `display` (epub, primary_lang) at `bilingual.epub` path
5. `has_evidence_track = book.has_original`.
6. `evidence_reason = None if has_evidence_track else "no_original_uploaded"`.
7. metadata = `{isbn, published_year, original_metadata_lang}` — **NB3 (v3): no `lang_pair`**. Consumers must use `ReadingSource.primary_lang`.

詳見 plan §4.3 Ebook 步驟。

#### Inbox document (`InboxKey`)

詳見 plan §4.3 Inbox 七步驟 + 三-case 表。重點：

1. **Compute logical paths** from input:
   - logical_original = `path[:-len("-bilingual.md")] + ".md"` if input ends `-bilingual.md`, else `path`
   - logical_bilingual = `logical_original[:-3] + "-bilingual.md"`
2. **Check disk** for both siblings; neither → return `None`.
3. **Identity (canonicalize)**:
   - `source_id = f"inbox:{logical_original}"` always (stable across sibling lifecycle)
   - **⚠️ N3 design callout (v3)**: in case (b) bilingual-only, `logical_original` does NOT exist on disk. `source_id` is a **logical identity, NOT a filesystem lookup key**. Use `variants[*].path` for file access, never `Path(source_id.removeprefix("inbox:"))`. Asserted by test 8.
   - `annotation_key = annotation_slug(user_facing_filename, user_facing_fm)` where user_facing = bilingual sibling if exists else original
   - **NB1 (v3)**: `read_text` + `extract_frontmatter` wrapped in try/except → any failure (`OSError`, malformed YAML) → return `None` + WARNING (`category="inbox_frontmatter_parse_failed"`)
   - **NB4 (v3)**: if `annotation_slug` returns empty string → `_resolve_inbox` raises `ValueError` with offending-path message. Do NOT emit `ReadingSource` with empty `annotation_key`.
4. **Three-case variants:**

| Case | Original on disk | Bilingual on disk | Variants | has_evidence | evidence_reason |
|---|---|---|---|---|---|
| (a) plain-only | ✓ | ✗ | 1× original | True | None |
| (b) bilingual-only | ✗ | ✓ | 1× display "bilingual" | False | "bilingual_only_inbox" |
| (c) both | ✓ | ✓ | 2× (original + display) | True | None |

5. `primary_lang` from frontmatter `lang` field of (a)/(c) original, (b) bilingual; normalize. Missing → `"unknown"` (never `"en"`). **NB2 (v3)**: case (b) `primary_lang` is best-effort / low-confidence — translator's `lang:` convention not pinned. Downstream MUST consult `evidence_reason == "bilingual_only_inbox"` and treat lang as low-confidence.

### 4.4 Test fixtures

- `tests/fixtures/reading_source/inbox/foo-en-only.md` (lang: en)
- `tests/fixtures/reading_source/inbox/bar-zh-only.md` (lang: zh-Hant)
- `tests/fixtures/reading_source/inbox/baz.md` + `baz-bilingual.md` (sibling pair)
- `tests/fixtures/reading_source/inbox/qux-bilingual.md` (bilingual-only)
- `tests/fixtures/reading_source/inbox/no-lang.md` (missing `lang` frontmatter)
- Ebook fixtures: built dynamically via `zipfile` in `conftest.py`; written to `tmp_path / "data" / "books"`. Set `<dc:language>` per test.

---

## 5. 驗收

### Issue #509 列出的 4 條 AC（必過）

- [ ] A Reading Source can be resolved for at least one book fixture and one markdown document fixture.
- [ ] The resolved shape distinguishes original/evidence track from display/bilingual track.
- [ ] The service is reusable outside Thousand Sunny route handlers.
- [ ] Tests cover ebook, inbox/document, and missing evidence-track cases.

### 15 個具體 unit test 必過

| # | Test | 驗 |
|---|---|---|
| 1 | `test_resolve_ebook_with_original` | 2 variants (original en + display bilingual); `has_evidence_track=True`; `source_id=="ebook:{book_id}"`; `annotation_key==book_id` |
| 2 | `test_resolve_ebook_bilingual_only_true_bilingual` | 1 display variant; `has_evidence_track=False`; `evidence_reason=="no_original_uploaded"`; `primary_lang=="en"` |
| 3 | `test_resolve_ebook_phase1_monolingual_zh` | bilingual.epub `<dc:language>=zh-TW` → `primary_lang=="zh-Hant"`; `evidence_reason=="no_original_uploaded"` |
| 4 | `test_resolve_ebook_lang_normalization` | parametrized: zh/zh-TW/zh-Hant/zh-CN/zh-Hans → "zh-Hant"; en/en-US/en-GB → "en"; ja/empty → "unknown" |
| 5 | `test_resolve_ebook_missing` | empty DB → `None` |
| 6 | `test_resolve_ebook_orphan_blob_no_db_row` | blob but no DB row → `None` (orphan blobs do not auto-register) |
| 7 | `test_resolve_inbox_plain_only` | foo.md only → 1 original variant; `source_id=="inbox:Inbox/kb/foo.md"`; `annotation_key==annotation_slug("foo.md", fm)` |
| 8 | `test_resolve_inbox_bilingual_only` | only foo-bilingual.md → 1 display variant lang="bilingual"; `has_evidence_track=False`; `evidence_reason=="bilingual_only_inbox"`; `source_id=="inbox:Inbox/kb/foo.md"` (logical, even though file 不存在) |
| 9 | `test_resolve_inbox_both_siblings_canonicalize` | 兩 sibling 並存；`InboxKey("foo.md")` 與 `InboxKey("foo-bilingual.md")` 兩 input 解析到 **identical `source_id`**；2 variants；`annotation_key` 從 bilingual sibling derive |
| 10 | `test_resolve_inbox_missing_lang_frontmatter` | 沒 `lang:` field → `primary_lang=="unknown"` (NOT `"en"`) |
| 11 | `test_resolve_inbox_missing_path` | empty inbox → `None` |
| 12 | `test_resolve_inbox_path_outside_vault` | `InboxKey("../../etc/passwd")` → `ValueError` (path normalization rejects escape) |
| 13 | `test_resolve_inbox_empty_annotation_slug` | **NB4 (v3)**: empty title + empty stem → `pytest.raises(ValueError)` with offending-path message. Does NOT return ReadingSource with empty `annotation_key`. |
| 14 | `test_no_fastapi_imports` | subprocess `import shared.reading_source_registry` → 不觸發 fastapi/thousand_sunny 進 sys.modules |
| 15 | `test_vault_root_required` | `vault_root=None` + unconfigured env → 構造期 fail fast |
| 16 | `test_resolve_ebook_malformed_blob` | **NB1 (v3)**: blob is valid zip but missing OPF / `<dc:language>` → returns `None` + WARNING (`category="ebook_blob_read_failed"`). Not raises. |
| 17 | `test_resolve_ebook_blob_io_error` | **NB1 (v3)**: blob deleted from disk between DB row insert and resolve → returns `None` + WARNING. Not raises. |
| 18 | `test_resolve_inbox_malformed_frontmatter` | **NB1 (v3)**: malformed YAML → returns `None` + WARNING (`category="inbox_frontmatter_parse_failed"`). Not raises. |

### Self-imposed gates

- [ ] 零新 dependency
- [ ] Pydantic schema 全部 `extra="forbid"`
- [ ] Test 檔零 `fastapi` / `thousand_sunny` import (asserted by test 14)
- [ ] `source_id` namespace prefix 鎖定 `ebook:` / `inbox:` only
- [ ] `source_id` 對 inbox 在 sibling lifecycle 中**stable**（test 9 asserted）
- [ ] `annotation_key` 對 inbox 用 `annotation_slug(user_facing_file, fm)` 並對齊 `_get_inbox_files` collapse rule
- [ ] `primary_lang` 不來自 `Book.lang_pair.split` (per Q1)；`grep -n 'lang_pair' shared/reading_source_registry.py shared/schemas/reading_source.py` 至多 legacy-comment match
- [ ] `primary_lang` 永遠不是 `"bilingual"`
- [ ] `primary_lang` 缺值回 `"unknown"`，**永不**回 `"en"`
- [ ] `evidence_reason` 用 closed set `{None, "no_original_uploaded", "bilingual_only_inbox"}` — **N6 (v3)**: closed for schema_version=1; extension requires schema bump + docstring update + downstream policy update
- [ ] **NB1 (v3)**: every blob-read / metadata-extract / frontmatter-parse failure → returns `None` + WARNING via `shared.log.get_logger("nakama.shared.reading_source_registry")`. Registry never propagates uncontrolled exceptions. Asserted by tests 16/17/18.
- [ ] **NB3 (v3)**: `Book.lang_pair` not in `ReadingSource.metadata` for ebook. `grep -n '"lang_pair"' shared/reading_source_registry.py` returns zero matches; tests 1-3 explicitly assert `"lang_pair" not in result.metadata`.
- [ ] **NB4 (v3)**: `_resolve_inbox` raises `ValueError` (with offending-path message) when `annotation_slug` returns empty string. Asserted by test 13.
- [ ] **NB2 (v3)**: case (b) bilingual-only `primary_lang` documented as low-confidence in schema docstring + §4.3 step 5 + plan §7 R7. Downstream contract; no code-level enforcement at #509.
- [ ] `python -m pytest tests/shared/test_reading_source_registry.py -v` 全綠
- [ ] `python -m ruff check shared/ tests/shared/` 無 error
- [ ] PR body 含 P7-COMPLETION 區塊

---

## 6. 邊界（不能碰）

| # | Don't | Why |
|---|---|---|
| 1 | ❌ `kb_source` kind / `KBSourceKey` | promotion output ≠ Reading Source (ADR-024) |
| 2 | ❌ `web_document` kind | web docs land in inbox via Toast/Obsidian Clipper; origin = metadata not kind |
| 3 | ❌ textbook kind | textbook ingest = promotion mode applied to ebook source |
| 4 | ❌ Promotion / Manifest / Preflight 邏輯 | slices #511-#515 |
| 5 | ❌ Reading Overlay 寫入或變更 | slice #510 |
| 6 | ❌ 修改 `agents/robin/ingest.py`、`inbox_writer.py`、`thousand_sunny/routers/*` | read-only consumer |
| 7 | ❌ 任何 LLM call | pure deterministic |
| 8 | ❌ Vault / DB / KB 寫入 | pure resolver |
| 9 | ❌ 新 SQL migration | reuse `books` table |
| 10 | ❌ 列舉 API（`list_*`） | per Q3 — `#511` extends registry surface when needed |
| 11 | ❌ `Book.mode` from PR #507 dependency (per Q1) | #509 lands cleanly regardless of PR #507 merge order |
| 12 | ❌ `Book.lang_pair` 來 derive `primary_lang` (per Q1) | upstream `BookMetadata.lang` is truth source |
| 13 | ❌ `evidence_reason` policy enforcement (per Q2) | #509 expose flag + reason; policy in #511 / #513 / #514 |
| 14 | ❌ URL / web capture | cancelled per PRD #508 + 修修 verbal |
| 15 | ❌ 投機塞 Promotion-用 fields 進 `ReadingSource` (manifest_id, promotion_status...) | 加 when needed |
| 16 | ❌ 自定 slug 規則 | import `annotation_slug` 即可 |

---

## 7. References

### Primary
- ADR: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md` (main, PR #441 merged)
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion
- Shared decision: `memory/shared/decision/source_promotion_stage3_stage4_architecture.md`
- 修修 language scope: `docs/research/2026-05-09-digest-md-cross-session-findings.md` § 9
- Schemas principle: `docs/principles/schemas.md`
- Existing schema patterns: `shared/schemas/{books,ingest_result,kb,annotations}.py`
- Inbox sibling collapse: `thousand_sunny/routers/robin.py:_get_inbox_files` lines 136-194

### Implementation rework basis
- Audit reclassification (this v2 的 source-of-truth): `docs/research/2026-05-09-N509-codex-audit-reclassified.md`
- v2 plan: `docs/plans/2026-05-09-N509-reading-source-registry.md`

### Parallel un-merged axis (PR #507 — Phase 1 monolingual-zh PRD)
- Branch: `feat/monolingual-zh-pilot-prd-handoff`
- PRD body: `docs/plans/2026-05-09-monolingual-zh-reader-annotation-pilot-prd.md` (qa-adr021 worktree)
- Grill summary: `docs/plans/2026-05-08-monolingual-zh-source-grill.md` (qa-adr021 worktree)

### Issue / PR
- Issue #509: `[ADR-024 S1] Reading Source Registry`
- Parent PRD #508: `Source Promotion and Reading Context Package`
- PR #507: Phase 1 monolingual-zh PRD (open, parallel)
- PR #441: source-promotion ADR + textbook P0 repair (merged to main)

---

## 8. Triage status

`needs-triage` → 等修修讀 v2 Brief + plan 後決定。**不要** 自行 relabel `ready-for-agent`。Sandcastle dispatch 也不在此 slice 範圍（slice 1 ~250 LOC + tests，本機 worktree 跑）。
