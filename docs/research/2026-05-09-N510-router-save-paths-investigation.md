# N510 read-only investigation — router save paths, book_digest_writer V3 handling, annotation_merger V3 dispatch

**Issue:** #510 (`[ADR-024 S2] Reading Overlay V3 Stabilization`)
**Trigger:** Pre-rework investigation requested 2026-05-09 after Codex reviewed the v1 Brief and flagged uncertainty about the three areas below.
**Status:** Read-only — no code changes, no Brief edits in this pass.
**Author:** Claude (Opus 4.7) — investigated against main `c73e44d` (post-#512 v2 docs commit; #509 already in main at `0f2742f`).
**Output:** Concrete file/line references for each of the three flagged areas, plus a recommendation on the V3 dispatch option.

---

## 0. TL;DR for #510 v2 Brief revision

| Area | v1 Brief flag | Investigation finding |
|---|---|---|
| `routers/books.py` annotation save | "Confirm `book.id` 直用是否需要 sanity-check" | **Already correct.** Save path uses `book_id` directly, which equals `ReadingSource.annotation_key` for ebook. Migration is a verification + an optional 404 sanity gate via `registry.resolve(BookKey)`. **No slug-derivation change.** |
| `routers/robin.py` inbox save | "Confirm slug derivation point" | **Real migration target.** `routers/robin.py:235` does `slug = annotation_slug(file, frontmatter)` ad hoc. **Subtle bilingual-sibling divergence**: registry collapses to bilingual sibling for `annotation_key`; current Reader route does not. Confirm whether `_get_inbox_files` collapse already prevents users from ever reaching `foo.md` URL when bilingual exists. |
| `book_digest_writer` V3 handling | "Confirm load type tolerance" | **Already V3-aware via duck-typing.** `hasattr(ann_set, "book_id")` (line 200) works for V2 + V3. Item type checks include `"reflection"` (line 223, 245). **No code change needed; only a regression test.** |
| `annotation_merger` V3 dispatch | "Pick rewrite vs reuse-v2-helpers" | **Real silent correctness bug.** Current dispatcher (line 212-214) treats V3 sets as V1 paper, dropping book reflections and routing book annotations to wrong section. **Recommend Option A** (additive V3 branch) below. |

The Brief's v1 "discovery phase" suggestion stands but the scope is narrower than v1 implied: ~80% of #510's behavior already works; the merger dispatch fix is the substantive change.

---

## 1. `routers/books.py` annotation save path

### Endpoint shape

`thousand_sunny/routers/books.py:340-375` — `POST /api/books/{book_id}/annotations`:

```python
async def post_annotations(
    book_id: str,
    payload: dict,
    background_tasks: BackgroundTasks,
):
    schema_version = payload.get("schema_version")
    try:
        if schema_version == 3:
            ann_set: AnnotationSetV2 | AnnotationSetV3 = AnnotationSetV3.model_validate(payload)
        else:
            ann_set = AnnotationSetV2.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(422, detail=exc.errors()) from exc

    payload_book_id = ann_set.book_id if ann_set.book_id is not None else book_id
    if payload_book_id != book_id:
        raise HTTPException(422, detail="book_id in URL does not match payload")
    book = get_book(book_id)
    if book is None:
        raise HTTPException(404, detail=f"book not found: {book_id}")
    get_annotation_store().save(upgrade_to_v3(ann_set))
    background_tasks.add_task(_write_digest_in_background, book_id)
    return {"ok": True, "digest_status": "queued"}
```

Key facts:

- **Store key = `book_id`** (passed as URL parameter; never derived via `annotation_slug`).
- Persistence is uniformly V3 via `upgrade_to_v3(ann_set)` (line 373) — Reader UI may post V2 or V3, but on-disk shape is V3.
- Existence check uses `get_book(book_id)` (line 366) — current 404 gate.

### `GET` shape (line 314-322)

```python
async def get_annotations(book_id: str):
    ...
    store = get_annotation_store()
    ann_set = store.load(book_id) or AnnotationSetV2(...)  # default V2 for legacy compat
```

Loads using `book_id` directly. On miss, returns an empty V2 set as legacy default.

### `ReadingSource.annotation_key` parity check

Per `shared/reading_source_registry.py:215` (Slice 1 in main): for an ebook,
`annotation_key = book_id`. Therefore:

```
registry.resolve(BookKey(book_id)).annotation_key == book_id   # by construction
```

This is a structural identity, not a runtime coincidence. The books-side save path **does not change**: it already uses `book_id`, which **is** the registry's `annotation_key`.

### What changes for #510 (books side)

Two options for the books-side migration, neither is a behavior change:

**Option (a) — strict registry sanity gate.** Replace the `get_book(book_id)` 404 check with `registry.resolve(BookKey(book_id))`:

```python
from shared.reading_source_registry import ReadingSourceRegistry, BookKey
rs = ReadingSourceRegistry().resolve(BookKey(book_id))
if rs is None:
    raise HTTPException(404, detail=f"book not found: {book_id}")
store_key = rs.annotation_key  # == book_id, but routed through registry
```

Pro: single source of truth (router-side existence check matches registry's existence semantics).
Con: extra blob read + EPUB metadata extract on every save (registry derives `primary_lang` upstream — see `_resolve_book` lines 158-170 in `shared/reading_source_registry.py`). Save endpoint becomes ~50-200ms slower depending on EPUB size. Not catastrophic but worth measuring.

**Option (b) — leave save path alone; only migrate read-only callers.**
The Brief should clarify the books-side migration is **optional for the save path** and **required only at any new read site that needs `ReadingSource.primary_lang` / `evidence_reason`**. Since #510 doesn't add such read sites (digest writer reads via store, not via registry), the books-side save path can stay as-is.

**Recommendation for v2 Brief**: prefer (b). The hot save path doesn't need the extra registry resolve. Migration is documentation: "book annotation save path is already aligned with `ReadingSource.annotation_key`; no code change required". Keep the v1 Brief's claim in §4.1, but refine it to reflect that no actual call-site change is needed for books.

---

## 2. `routers/robin.py` inbox save path

### Reader page route — slug derivation site

`thousand_sunny/routers/robin.py:235`:

```python
slug = annotation_slug(file, frontmatter)
ann_store: AnnotationStore = get_annotation_store()
ann_set = ann_store.load(slug)
annotations = [item.model_dump() for item in ann_set.items] if ann_set else []
```

Where `file` is the inbox-relative path passed by the URL, and `frontmatter` is parsed from `file`'s contents at line 229. The slug is then injected into `reader.html` template as `slug` (line 246) for the Reader UI to use when posting annotations back.

### Save endpoint

`thousand_sunny/routers/robin.py:275-293`:

```python
async def save_annotations(
    ann_set: AnnotationSet,         # AnnotationSet == AnnotationSetV1 (alias)
    nakama_auth: str | None = Cookie(None),
):
    ...
    _resolve_reader_base(ann_set.base)
    store: AnnotationStore = get_annotation_store()
    store.save(upgrade_to_v3(ann_set))
    return {"status": "ok", "unsynced_count": store.unsynced_count(ann_set.slug)}
```

**Note**: endpoint type annotation is `AnnotationSet` (= V1 alias per `shared/annotation_store.py:40`). The `slug` used for `unsynced_count` (line 293) is whatever the client posted. Persistence is V3 (`upgrade_to_v3` at line 292). The **slug derivation** for the inbox path therefore happens at the Reader page route (`routers/robin.py:235`), not at the save endpoint — the save endpoint trusts whatever slug the page injected into the Reader UI.

### Bilingual sibling collapse — divergence to confirm

The registry's inbox resolution at `shared/reading_source_registry.py:251-273` picks `user_facing_path` as the bilingual sibling if it exists, else the original. `annotation_key` is then `annotation_slug(user_facing.name, user_facing_fm)`. This mirrors `thousand_sunny/routers/robin.py:_get_inbox_files`'s collapse rule (per `agents/robin/CONTEXT.md` § Inbox sibling collapse).

Reader page route at line 235 does NOT do this collapse — it computes slug from whatever `file` URL the user landed on. Two outcomes:

- **If `_get_inbox_files` already prevents users from ever opening `foo.md` URL when `foo-bilingual.md` exists**, the divergence is moot — users only ever land on the bilingual URL, and current behavior matches registry behavior by accident of routing.
- **If a user can land on `foo.md` URL** (e.g. direct link, bookmark, query param fallback), current behavior derives slug from `foo.md` frontmatter; registry would derive from `foo-bilingual.md` frontmatter. These slugs may or may not match, depending on whether the bilingual sibling has a different `title:` field.

**Confirmation needed by implementer**: search `thousand_sunny/routers/robin.py` reader entry routes (`/files/...`, `/reader/...`, etc.) for the URL→file path mapping and check whether any code-path lets the user open `foo.md` when `foo-bilingual.md` exists. If yes, this is a behavior change that #510 must call out (intentional convergence with registry / `_get_inbox_files`). If no, it's a no-op convergence and just a code cleanup.

### What changes for #510 (inbox side)

`routers/robin.py:235` migrates to:

```python
from shared.reading_source_registry import ReadingSourceRegistry, InboxKey

rs = ReadingSourceRegistry().resolve(InboxKey(file))  # `file` is vault-relative
if rs is None:
    raise HTTPException(404, detail=f"找不到檔案：{file}")
slug = rs.annotation_key
```

Cost note: each Reader page render now does a registry resolve (+ `read_text` + `_strict_parse_frontmatter`). The current code already reads + parses frontmatter at line 228-229 (for the `frontmatter_raw` and template injection), so the additional cost is the registry's resolve overhead — minimal.

**Caveats for the implementer**:

- `InboxKey.relative_path` accepts vault-relative paths under `Inbox/kb/`. Confirm `file` parameter at this route is already a vault-relative path. `shared/reading_source_registry.py:236-242` does a `Path.resolve()` + `relative_to(vault_resolved)` check, so traversal is rejected, but if `file` is already absolute or has a different prefix shape, the call must be adjusted.
- The Reader page injects `slug` into `reader.html` (line 246). The Reader UI subsequently posts `ann_set.slug = <that slug>` to `/save-annotations`. The migration changes the slug value at the page render, but the save endpoint is unchanged — it trusts whatever the Reader UI posted. **Net effect**: a single source of truth at the page-render side.

---

## 3. `book_digest_writer.py` — V3 schema handling

### V3-aware by duck-typing already

`agents/robin/book_digest_writer.py:182-209`:

```python
def write_digest(book_id: str) -> DigestReport:
    ...
    ann_set = get_annotation_store().load(book_id)
    if ann_set is None or not hasattr(ann_set, "book_id"):
        return DigestReport(
            book_id=book_id,
            chapters_rendered=0,
            ...
            errors=[f"no annotations found for book_id={book_id!r}"],
        )
    ...
```

Key fact: `hasattr(ann_set, "book_id")` is **True for both V2 and V3** book-shaped sets:

- `AnnotationSetV2.book_id: str` — required, always present.
- `AnnotationSetV3.book_id: str | None = None` — optional but present as attribute.

So `not hasattr(ann_set, "book_id")` returns False for both, AND the function proceeds for V3 book sets. (For paper V3 sets where `book_id is None`, the check passes but item iteration would produce non-book items — moot since digest writer is only called from books-side `BackgroundTasks` at `routers/books.py:374`.)

### Item type handling

Line 218-225 (parsing existing feedback before overwrite):

```python
for item in ann_set.items:
    if item.type == "highlight":
        cfi_to_query[item.cfi] = item.text_excerpt
    elif item.type == "annotation":
        cfi_to_query[item.cfi] = f"{item.text_excerpt}\n{item.note}"
    elif item.type in ("comment", "reflection") and item.cfi_anchor:
        cfi_to_query[item.cfi_anchor] = item.body[:500]
```

Line 245 (chapter grouping):

```python
for item in ann_set.items:
    if item.type in ("comment", "reflection"):
        anchor = getattr(item, "cfi_anchor", None)
        ch = item.chapter_ref or (_extract_chapter_ref(anchor) if anchor else "unknown")
    else:
        ch = _extract_chapter_ref(item.cfi)
```

Both branches handle `"comment"` (V2) and `"reflection"` (V3) symmetrically. `getattr(item, "cfi_anchor", None)` is also defensive — V3 `ReflectionV3` has `cfi_anchor: str | None = None` so the attribute exists; the `getattr` fallback is for paranoia.

Line 125-139 (item rendering):

```python
if item.type == "highlight":
    ...
elif item.type == "annotation":
    ...
else:  # comment (v2) / reflection (v3) — same shape, renamed in ADR-021 §1
    query = item.body[:500]
    label = "C"
    body_text = item.body
    cfi = item.cfi_anchor or ""
```

The else-branch covers V2 + V3. Item shape is identical (both have `body`, `chapter_ref`, `cfi_anchor`).

### What changes for #510 (digest writer side)

**No code change.** Just a regression test for V3 input:

- T5 in v1 Brief (`test_book_digest_writer_v3`) is sufficient — feed an `AnnotationSetV3` book set with `ReflectionV3` items into `write_digest()` and assert the rendered markdown matches the V2 expected output (byte-equal for equivalent input).
- T4 (`test_book_digest_writer_v2_unchanged`) remains a regression for V2 path — already covered by existing tests in `tests/agents/robin/test_book_digest_writer.py`.

---

## 4. `annotation_merger.py` — V3 dispatch options

### Current dispatcher

`agents/robin/annotation_merger.py:175-214`:

```python
def sync_source_to_concepts(self, slug: str) -> SyncReport:
    from shared.annotation_store import AnnotationSetV2

    store = get_annotation_store()
    ann_set = store.load(slug)

    if ann_set is None:
        return SyncReport(...)

    # Idempotency short-circuit
    if ann_set.last_synced_at is not None and not any(
        item.modified_at > ann_set.last_synced_at for item in ann_set.items
    ):
        return SyncReport(...)

    if isinstance(ann_set, AnnotationSetV2):
        return self._sync_v2(ann_set)
    return self._sync_v1(ann_set, slug)
```

**Bug**: V3 sets fall through to `_sync_v1` (paper path) because they are NOT V2 instances. For book sets that have been migrated to V3 via `scripts/migrate_annotations_v3.py`:

- `_sync_v1` filters items to `[item for item in ann_set.items if item.type == "annotation"]` (line 217). This drops `HighlightV3` (correct) AND `ReflectionV3` (incorrect — should route to notes.md).
- `_sync_v1` writes to `## 個人觀點` section with `<!-- annotation-from: {slug} -->` markers (line 99-126 + 274). Book annotations should go to `## 讀者註記` with `<!-- annotation-from: {book_id} -->` markers (per `_sync_v2` at line 294-344 + `_replace_v2_marker_block` at line 359-378).
- `_sync_v2` is where comments → `notes.md` via `book_notes_writer.write_notes(book_id, comments)` (line 302-308). This NEVER runs for V3 book sets currently. **Reflections silently disappear at sync time.**

### Three dispatch options

**Option A — Additive V3 branch** (Brief v1 lean):

```python
if isinstance(ann_set, AnnotationSetV3):
    return self._sync_v3(ann_set, slug)
if isinstance(ann_set, AnnotationSetV2):
    return self._sync_v2(ann_set)
return self._sync_v1(ann_set, slug)
```

Then `_sync_v3` discriminates book vs paper via `ann_set.book_id is not None`:

```python
def _sync_v3(self, ann_set: AnnotationSetV3, slug: str) -> SyncReport:
    if ann_set.book_id is not None:
        return self._sync_v3_book(ann_set)
    return self._sync_v3_paper(ann_set, slug)
```

**Pros**:
- V1 + V2 paths untouched. Minimal regression risk.
- Strong type discrimination — each branch has a known set type.
- `_sync_v3_book` can copy the structural skeleton of `_sync_v2` and substitute V3 item-shape access (`ReflectionV3.body` and `cfi_anchor` are identical to V2 `CommentV2`'s, so most logic is shared).

**Cons**:
- `_sync_v3_book` duplicates ~80% of `_sync_v2`. Refactor candidate later (e.g. extract shared helpers like `_dispatch_book_items_to_destinations(book_id, comments, annotations)`).
- After `migrate_annotations_v3.py` runs across the vault, `_sync_v2` becomes dead code (unless future tests still exercise V2 directly). May be acceptable for backward compatibility.

**Option B — Read-side normalize-to-V3**:

```python
def sync_source_to_concepts(self, slug: str) -> SyncReport:
    ann_set = store.load(slug)
    if ann_set is None: ...

    # Normalize at entry — V1 / V2 → V3
    if not isinstance(ann_set, AnnotationSetV3):
        ann_set = upgrade_to_v3(ann_set)

    # Idempotency check (works on V3 modified_at)
    ...

    if ann_set.book_id is not None:
        return self._sync_book(ann_set)
    return self._sync_paper(ann_set, slug)
```

**Pros**:
- One item shape internally — reduces total code path count.
- Future V4 migration would also normalize at this entry point.

**Cons**:
- V2 codepath disappears entirely. Any caller (test, script, BackgroundTask) that depends on the merger reading V2 directly without `upgrade_to_v3` would now silently use V3.
- `upgrade_to_v3` is at `shared.annotation_store` (used in `routers/{books,robin}.py` save sites). It already exists and is exercised. But scope of #510 widens: V2 path elimination touches existing tests + maybe documentation.

**Option C — Make `_sync_v2` accept V3 by item-type duck-typing**:

```python
if isinstance(ann_set, (AnnotationSetV2, AnnotationSetV3)) and ann_set.book_id:
    return self._sync_v2(ann_set)  # already supports type∈{"comment","reflection"} via duck-typing
return self._sync_v1(ann_set, slug)
```

Then `_sync_v2` adjusts its filter at line 307:
```python
comments = [i for i in ann_set.items if i.type in ("comment", "reflection")]
annotations = [i for i in ann_set.items if i.type == "annotation"]
```

**Pros**:
- Smallest diff. Reuses the same v2 helpers verbatim.
- No duplication.

**Cons**:
- Function name `_sync_v2` becomes misleading — it now handles V2 + V3 book sets.
- V3 paper sets (`book_id is None`) still fall through to `_sync_v1`, which works for paper (that's where v3 papers belong) but the dispatcher's intent isn't clear from the type guard alone.
- Future V4 paper sets would also fall through to `_sync_v1` until that branch is updated. Less explicit.

### Recommendation

**Option A** for #510. Reasons:

1. Additive — preserves V1 + V2 codepaths as known-good fallbacks.
2. Branch-per-version matches the existing code style and the comment at lines 168-173 ("v1 → ## 個人觀點 ... v2 → ## 讀者註記 ... v3 → ...").
3. Future refactor (consolidate to Option B once V2 callers are gone) is a clean follow-up commit, not a #510 risk.
4. The 80% duplication between `_sync_v2` and `_sync_v3_book` is small enough (the V2 + V3 item shapes are nearly identical post ADR-021 §1) and can be addressed by extracting `_dispatch_book_items` helper without rewriting the dispatch.

Note: Option A's `_sync_v3_book` should call into the SAME `book_notes_writer.write_notes(book_id, reflections)` that `_sync_v2` uses (line 302-308). `book_notes_writer` likely accepts V2 `CommentV2` only today; check whether it handles V3 `ReflectionV3` via duck-typing too. If not, that's an additional small change in scope. (Not investigated here; flag for implementer.)

---

## 5. Other findings worth noting

### Idempotency check at dispatcher (line 200-202)

```python
if ann_set.last_synced_at is not None and not any(
    item.modified_at > ann_set.last_synced_at for item in ann_set.items
):
    return SyncReport(...)
```

Works for V3 because `HighlightV3`, `AnnotationV3`, `ReflectionV3` all carry `modified_at` (per `shared/schemas/annotations.py:135, 153, 171`). No change needed.

### `routers/books.py` line 322 — empty default uses V2

```python
ann_set = AnnotationSetV2(...)
```

The legacy default at the GET endpoint creates an empty V2 set. This is fine for a Reader UI that posts V2 (gets upgraded on save). No #510 change required, but it's a minor inconsistency: `GET` returns V2-shaped empty, `POST` saves as V3. Reader UI must handle either via `schema_version` discrimination — currently it does (per the `if schema_version == 3` branch at line 354). Document, don't change.

### `_get_inbox_files` location

`thousand_sunny/routers/robin.py:_get_inbox_files` — confirmed the only definition (per `Grep` at session start). Lines 136-194 (per #509 Brief reference). Bilingual sibling collapse rule lives there. Registry's inbox resolution mirrors this rule via `_resolve_inbox` lines 230-368 in `shared/reading_source_registry.py`.

### Migration script `scripts/migrate_annotations_v3.py`

Out of scope per Brief §6 boundary 6 (no rewrite). Unchanged.

---

## 6. Implications for #510 v2 Brief

When the Brief is reworked (per user instruction "do not label" — defer until investigation reviewed), the v2 should:

1. **Trim §1 goal scope.** "Migrate reader-save callers" applies primarily to `routers/robin.py:235`. The `routers/books.py` save path is already aligned and only needs documentation. Don't promise a migration that has no real change.
2. **Promote the merger fix to lead position.** It's the only substantive bug. v1 Brief listed it as one of four equal goals; in v2 it should be the headline.
3. **Resolve the V3 dispatch option.** Recommend Option A (additive V3 branch). Document Options B + C as deferred refactors.
4. **Add a confirmation task on `book_notes_writer`.** Whether it accepts V3 `ReflectionV3` via duck-typing (similar to `book_digest_writer`'s pattern) determines whether `_sync_v3_book → write_notes` works directly or needs a small adapter.
5. **Add a confirmation task on bilingual-sibling URL reachability.** Section 2 above flags the divergence; v2 Brief should list "verify whether `foo.md` URL is reachable when `foo-bilingual.md` exists; if yes, document as intentional behavior change to converge with `_get_inbox_files`".
6. **Drop the "annotation_only_sync remains separate from factual Source Promotion" AC bullet from §1.** It's already true today (Source Promotion doesn't exist yet). It belongs as a §6 boundary, not a goal.
7. **Reduce LOC estimate.** v1 said ~400-600 LOC. After this investigation, `_sync_v3_book` + `_sync_v3_paper` + `_resolve_v3` + tests is closer to ~200-300 LOC. Books-side and digest-side are no-op or near-no-op.

The investigation does NOT change scope direction; it sharpens it.

---

## 7. References

- ADR-024: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`
- ADR-017: annotation data model (KB/Annotations layout)
- ADR-021 §1: V3 unified annotation store
- Reading Source Registry (#509): `shared/reading_source_registry.py`, `shared/schemas/reading_source.py` (main `0f2742f`)
- Annotation schemas: `shared/schemas/annotations.py`
- Annotation store: `shared/annotation_store.py`
- Annotation merger: `agents/robin/annotation_merger.py:175-378`
- Book digest writer: `agents/robin/book_digest_writer.py:182-...`
- Routers touched: `thousand_sunny/routers/{books,robin}.py`
- Existing v2 tests: `tests/agents/robin/test_annotation_merger_v2.py`, `tests/agents/robin/test_book_digest_writer.py`
- v3 schema tests: `tests/shared/test_annotation_v3.py`, `tests/shared/test_schemas_annotations.py`

### v1 Brief reference

- `docs/task-prompts/N510-reading-overlay-v3-stabilization.md` (untracked draft from 2026-05-09)

---

## 8. Out of scope for this investigation

- Did NOT exercise `_sync_v3` against real data. Pure code-path analysis.
- Did NOT measure registry-resolve overhead at the inbox Reader route — flagged as a non-blocking concern (likely negligible).
- Did NOT audit `book_notes_writer` for V3 `ReflectionV3` acceptance — flagged as a confirmation task for the implementer.
- Did NOT inspect `tests/agents/robin/test_annotation_merger_v2.py` to determine which tests would also need V3-equivalent companions.
- Did NOT decide whether v2 Brief should commit to a more aggressive Option B refactor.
