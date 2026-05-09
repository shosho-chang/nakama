# Agent Brief — N509 Reading Source Registry (ADR-024 Slice 1)

**Issue:** [#509](https://github.com/shosho-chang/nakama/issues/509)
**Parent PRD:** #508
**Slice of:** ADR-024 (`docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`)
**Branch:** `impl/N509-reading-source-registry`
**Worktree (host):** `E:/nakama-N509-source-registry`
**Implementation plan:** `docs/plans/2026-05-09-N509-reading-source-registry.md` (read this first)
**Frozen:** 2026-05-09

P9 六要素。Brief 的目的是給接手 agent / 修修一份「不靠群聊歷史也能上手」的單一文件。

---

## 1. 目標

在 `shared/` 提供 `ReadingSourceRegistry.resolve(key) -> ReadingSource | None`，把 ebook、Inbox markdown、KB Source page 三種既有的閱讀來源 normalize 成單一 `ReadingSource` value-object，並 **明確標出 original/evidence track 與 display/bilingual track 的差異**。

不啟動 Source Promotion；只做 identity + variants 解析。為 #510 Reading Overlay、#511 Promotion Preflight、#513 Source Map Builder、#517 Reading Context Package 提供共同接點。

---

## 2. 範圍

新增三個檔 + 一組 fixture：

| 路徑 | 動作 |
|---|---|
| `shared/schemas/reading_source.py` | **新增** — Pydantic 模型 (`ReadingSource`, `SourceVariant`) + `SourceKind`/`TrackRole`/`VariantFormat` Literal。`extra="forbid"` + value-object `frozen=True`。|
| `shared/reading_source_registry.py` | **新增** — `ReadingSourceRegistry` + 三個 SourceKey dataclass（`BookKey`, `InboxKey`, `KBSourceKey`）+ `resolve(key)` dispatch。|
| `tests/shared/test_reading_source_registry.py` | **新增** — 9 個 unit test（見 §5 驗收）。零 `fastapi` import。|
| `tests/fixtures/reading_source/` | **新增** — `tiny_book/{original,bilingual}.epub` + `inbox/{foo,foo-bilingual}.md` + `kb_source/sample-source.md`。EPUB 用 `zipfile` + 最小 OPF 程式內生成，不放二進位檔。|

讀取（不改）：`shared/book_storage.py`, `shared/annotation_store.py:annotation_slug`, `shared/utils.py:extract_frontmatter`, `shared/config.py:get_vault_path`, `shared/schemas/books.py`。

**完全不碰**：`agents/robin/ingest.py`、`agents/robin/inbox_writer.py`、`thousand_sunny/routers/books.py`、`thousand_sunny/routers/robin.py`、任何 LLM call、任何 vault/KB write、任何 SQL migration、任何 URL fetcher。

---

## 3. 輸入

- **Source 1 (ebook)**：`shared.book_storage.get_book(book_id)` 已 ship 並由 Reader upload path 寫入；`Book.has_original` 控制有無 original.epub。
- **Source 2 (inbox doc)**：`Inbox/kb/{slug}.md` 由 `inbox_writer.InboxWriter.write_to_inbox` 寫；雙語 sibling 規則：`{slug}-bilingual.md` + frontmatter `derived_from: {slug}.md`。
- **Source 3 (kb_source)**：`KB/Wiki/Sources/...` filesystem layout（textbook ingest 寫入的 chapter source page，或單檔 article source page）。
- **Slug 規則**：`shared.annotation_store.annotation_slug(filename, frontmatter)`。Inbox 的 `ReadingSource.source_id` **必須 = `annotation_slug` 的回傳**，不重新發明，這樣 #510 Reading Overlay Service 可以 1:1 join。
- **Vault root**：`shared.config.get_vault_path()`，**runtime 解析** 不在 import time。

**ADR-024 / shared memory canonical 詞彙** 直接沿用：Reading Source / Reading Overlay / original (evidence) track / display (bilingual) track / Source-local Concept / Global KB Concept / Promotion Manifest。

---

## 4. 輸出

### 4.1 `ReadingSource` schema (frozen value-object)

```python
SourceKind   = Literal["ebook", "inbox_document", "kb_source"]
TrackRole    = Literal["original", "display"]
VariantFormat = Literal["epub", "markdown", "html"]

class SourceVariant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    role: TrackRole
    format: VariantFormat
    lang: str        # bcp-47 short: "en", "zh-Hant", "bilingual"
    path: str        # ebook → "data/books/{book_id}/original.epub"; inbox → vault-rel md path; kb → vault-rel
    bytes_estimate: int | None = None

class ReadingSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    source_id: str
    kind: SourceKind
    title: str
    author: str | None = None
    primary_lang: str        # never "bilingual"
    has_evidence_track: bool # False if only display/bilingual exists
    variants: list[SourceVariant] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)
```

### 4.2 Resolver service

```python
@dataclass(frozen=True)
class BookKey:      book_id: str
@dataclass(frozen=True)
class InboxKey:     relative_path: str   # e.g. "Inbox/kb/foo.md"
@dataclass(frozen=True)
class KBSourceKey:  relative_path: str   # e.g. "Books/x/ch1" (no .md)

SourceKey = BookKey | InboxKey | KBSourceKey

class ReadingSourceRegistry:
    def __init__(self, vault_root: Path | None = None) -> None: ...
    def resolve(self, key: SourceKey) -> ReadingSource | None: ...
```

### 4.3 三種 kind 的解析規則

詳見 plan §4.3。重點：

- **ebook**：`primary_lang` = `Book.lang_pair.split("-")[0]`，預設 `"en"`；`has_original=True` → 兩 variant（original epub + display bilingual epub）；`has_original=False` → 一 variant（display only，`has_evidence_track=False`）。Variant `path` 走 `data/books/{book_id}/original.epub`（match `book_storage.read_book_blob` 既有路徑）。
- **inbox**：依 `{slug}.md` / `{slug}-bilingual.md` 是否並存決定 1 或 2 variants。`source_id = annotation_slug(filename, frontmatter)`。`primary_lang = frontmatter.lang or "en"`。
- **kb_source**：單一 variant（original markdown）。`source_id = relative_path stem`。`has_evidence_track=True`。

### 4.4 Test fixtures

- `tests/fixtures/reading_source/inbox/foo.md`、`foo-bilingual.md`（雙語 sibling 範例）
- `tests/fixtures/reading_source/kb_source/sample-source.md`
- ebook fixture **在 test setup 內用 `zipfile` 動態產生**（不簽進 binary blob）

---

## 5. 驗收

### Issue 列出的 4 條 AC（必過）

- [ ] A Reading Source can be resolved for at least one book fixture and one markdown document fixture.
- [ ] The resolved shape distinguishes original/evidence track from display/bilingual track.
- [ ] The service is reusable outside Thousand Sunny route handlers.
- [ ] Tests cover ebook, inbox/document, and missing evidence-track cases.

### 9 個具體 unit test 必過

| Test | 驗 |
|---|---|
| `test_resolve_ebook_with_original` | `has_original=True` → 2 variants (original en + display bilingual) |
| `test_resolve_ebook_bilingual_only` | `has_original=False` → 1 variant + `has_evidence_track=False` |
| `test_resolve_ebook_missing` | empty DB → `None` |
| `test_resolve_inbox_plain_md` | 單檔 → 1 variant + `source_id == annotation_slug(...)` |
| `test_resolve_inbox_with_bilingual_sibling` | 雙檔並存 → 2 variants (original + display) |
| `test_resolve_inbox_bilingual_only` | 只 `-bilingual.md` 存在 → 1 display variant + `has_evidence_track=False` |
| `test_resolve_inbox_missing` | empty inbox → `None` |
| `test_resolve_kb_source` | 單檔 → 1 original variant + `source_id == path stem` |
| `test_no_fastapi_imports` | `python -c "import shared.reading_source_registry"` 不觸發 fastapi import — confirms reusability |

### Self-imposed gates（per ADR-024）

- [ ] 零新 dependency
- [ ] Pydantic schema 全部 `extra="forbid"`
- [ ] Test 檔零 `fastapi` / `thousand_sunny` import
- [ ] Inbox `source_id` 等於 `annotation_slug(filename, frontmatter)`
- [ ] `primary_lang` 永遠不是 `"bilingual"`
- [ ] `python -m pytest tests/shared/test_reading_source_registry.py -v` 全綠
- [ ] `python -m ruff check shared/ tests/shared/` 無 error
- [ ] PR body 含 P7-COMPLETION 區塊

---

## 6. 邊界（不能碰）

- ❌ Promotion / Manifest / Preflight 任何邏輯（slices #511-#515）
- ❌ Reading Overlay 寫入或變更（slice #510）
- ❌ 修改 `agents/robin/ingest.py`、`inbox_writer.py`、`thousand_sunny/routers/*`
- ❌ 任何 LLM call（含 mocked LLM batch fixture）
- ❌ Vault / DB / KB 寫入；registry 是 pure resolver
- ❌ 新 SQL migration；reuse `books` table
- ❌ 列舉 API（`list_all_reading_sources()`）— Promotion Preflight (#511) 需要時再加
- ❌ URL / web capture — PRD #508 已凍結這條取消，由 Toast / Obsidian Clipper 接手
- ❌ 投機性把 Promotion 用得到的欄位塞進 `ReadingSource`（manifest_id、promotion_status 等）
- ❌ 自定義 slug 規則 — import `annotation_slug` 即可
- ❌ Sandcastle dispatch — 此 slice 約 200 LOC + tests，本 worktree 跑

---

## 7. References

- ADR: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion（canonical 詞彙）
- Shared decision: `memory/shared/decision/source_promotion_stage3_stage4_architecture.md`
- Cross-session findings: `docs/research/2026-05-09-digest-md-cross-session-findings.md`（特別是 §9 修修語言收斂為 zh-Hant + en）
- Plan (full impl detail): `docs/plans/2026-05-09-N509-reading-source-registry.md`
- Schemas principle: `docs/principles/schemas.md`
- Existing schema patterns: `shared/schemas/{books,ingest_result,kb,annotations}.py`

---

## 8. Triage status

`needs-triage` → 等修修讀 Brief + plan 後決定。**不要** 自行 relabel `ready-for-agent`。Sandcastle dispatch 也不在此 slice 範圍。
