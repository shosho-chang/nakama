# Agent Brief — N510 Reading Overlay V3 Stabilization (ADR-024 Slice 2) — v2

**Issue:** [#510](https://github.com/shosho-chang/nakama/issues/510)
**Parent PRD:** #508
**Slice of:** ADR-024 (`docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`)
**Branch:** `impl/N510-reading-overlay-v3-stabilization`
**Worktree (host):** `E:/nakama-N510-reading-overlay-v3-stabilization`
**Drafted:** 2026-05-09 v1; revised 2026-05-09 v2 (post-investigation)
**Investigation:** `docs/research/2026-05-09-N510-router-save-paths-investigation.md` (in main)
**Status:** v2 draft — awaiting 修修 review before relabeling `ready-for-agent`

P9 六要素。Brief 是 self-contained handoff，不靠群聊歷史也能上手。

---

## 0. Revision summary

### v1 → v2 (post read-only investigation)

The investigation note (in main `docs/research/2026-05-09-N510-router-save-paths-investigation.md`) tightened scope substantially. v1 promised four equal-weight changes; v2 reflects what's actually a code change vs. a documentation / verification step.

| Area | v1 framing | v2 framing | Source |
|---|---|---|---|
| `routers/books.py` save | "Migrate to `ReadingSource.annotation_key`" — implied real change | **No code change.** Already aligned: `book_id` (URL param) == `ReadingSource.annotation_key` for ebook by registry construction. Documentation only. | Investigation §1 |
| `routers/robin.py` inbox save | "Migrate" | **Real migration target.** `routers/robin.py:235` does `slug = annotation_slug(file, frontmatter)` ad hoc; replace with registry-driven `annotation_key`. Plus a bilingual-sibling URL-reachability confirmation. | Investigation §2 |
| `book_digest_writer.py` | "Confirm V3-aware load" | **Already V3-aware via duck-typing.** No code change; only a regression test (T-N5). | Investigation §3 |
| `annotation_merger.py` V3 dispatch | "Pick rewrite vs reuse" | **Real silent-correctness bug — lead change.** V3 sets fall through to `_sync_v1` (paper path), dropping `ReflectionV3` and routing book annotations to wrong section. **Recommend Option A (additive V3 branch + book/paper sub-discrimination via `book_id is not None`).** | Investigation §4 |

**LOC estimate revised: ~200-300** (down from v1's 400-600). The merger dispatch + the inbox router migration + tests are the substantive work; books-side and digest-side are no-op or near-no-op.

---

## 0.1 Scope anchor

Per ADR-024 §Decision: "Reading Overlay and Source Promotion have separate authority. Source maps govern factual claims; Reading Overlay governs personal meaning."

Per `agents/robin/CONTEXT.md` § Source Promotion: `digest.md` and `notes.md` remain Reading Overlay views, distinct from promoted Source pages.

Slice 2 (#510) **stabilizes** existing Reader paths so:

1. Book annotation saves using **AnnotationSetV3 (ADR-021 §1)** continue to produce `digest.md` views without regression.
2. Book reflections (V3 `ReflectionV3` items) route correctly into `notes.md` instead of being silently dropped.
3. Regression tests cover annotation store v1 / v2 / v3, book digest, book notes, and the merger dispatch.

This slice does NOT add new Reading Overlay features. The "stabilization" gap is specifically: **the v3 unified store has no dispatch in the merger, so book annotations / reflections in V3 sets are misrouted**. After v3 migration (per ADR-021), the merger must continue to surface the same Reader UX it did under v2.

### Anchor on `#509` Reading Source Registry

`#509` shipped to main at commit `0f2742f` (2026-05-09). It exposes `ReadingSource.annotation_key`:

- `ebook` → `book_id` (matches existing `KB/Annotations/{book_id}.md` save path).
- `inbox_document` → `annotation_slug(user_facing_filename, frontmatter)` (mirrors `_get_inbox_files` collapse rule).

**`#510` MUST consume `ReadingSource.annotation_key`** for the inbox-side reader-save derivation. The books-side route already uses `book_id` directly, which equals `annotation_key` by construction — no change required.

```python
from shared.reading_source_registry import ReadingSourceRegistry, BookKey, InboxKey

registry = ReadingSourceRegistry()
rs = registry.resolve(InboxKey(rel_path))    # books side already uses book_id directly
key = rs.annotation_key                       # use this for KB/Annotations/{key}.md
```

---

## 1. 目標 (Goal)

**Lead change**: fix the silent V3 dispatch bug in `agents.robin.annotation_merger`. Currently `AnnotationSetV3` falls through to `_sync_v1` (paper path), which drops `ReflectionV3` items and routes book annotations to the wrong markdown section. Add an additive V3 branch (Option A) that preserves V1 + V2 paths untouched.

**Secondary change**: migrate the inbox-side reader page (`routers/robin.py:235`) so `slug` is derived via `ReadingSourceRegistry.resolve(InboxKey(...)).annotation_key` instead of ad-hoc `annotation_slug(...)`. Single source of truth for the inbox join key.

**Verification-only**: confirm `routers/books.py` annotation save (already aligned), `book_digest_writer.write_digest()` (already V3-aware via duck-typing), and bilingual-sibling URL reachability semantics. Document findings, add regression tests, but do NOT introduce code change for these.

This slice does NOT:

- Add Source Promotion / Manifest / Preflight / commit logic.
- Add new Reader UI features.
- Change `digest.md` / `notes.md` rendering format (full-replace semantics preserved; byte-equal output for unchanged inputs).
- Touch `KB/Wiki/Sources/...` (that's promotion output, not Reading Overlay).
- Eliminate the V2 path. V2 stays as a known-good fallback. (V2 → V3 normalization is a separate refactor, deferred.)

---

## 2. 範圍

### 2.1 Files to modify

| Path | Action | Why |
|---|---|---|
| `agents/robin/annotation_merger.py` | **Edit** — add `_sync_v3` branch (Option A) at lines 175-214; add `_sync_v3_book` and `_sync_v3_paper` helpers | Lead change. Currently V3 sets fall through to `_sync_v1`; book reflections silently dropped (Investigation §4). |
| `thousand_sunny/routers/robin.py` | **Edit** — line 235: replace `slug = annotation_slug(file, frontmatter)` with `slug = registry.resolve(InboxKey(file)).annotation_key` (404 on None). Wire `ReadingSourceRegistry` dependency at module level (mirror books router pattern). | Inbox-side reader-save consistency. |

### 2.2 Files to add

| Path | Purpose |
|---|---|
| `tests/agents/robin/test_annotation_merger_v3.py` | New test file for V3 dispatch path (T-N1-N4). |
| `tests/agents/robin/test_book_digest_writer_v3.py` | New test file for V3 digest rendering regression (T-N5). |
| `tests/thousand_sunny/test_robin_inbox_save_v2.py` (or extend existing) | Inbox reader-save migration regression (T-N7-N8). |

### 2.3 Files to read (NOT modify)

| Path | Why |
|---|---|
| `shared/reading_source_registry.py` (#509) | `ReadingSourceRegistry.resolve()` API. |
| `shared/schemas/reading_source.py` (#509) | `ReadingSource.annotation_key` semantics. |
| `shared/schemas/annotations.py` | V1 / V2 / V3 schema shapes; `CommentV2 → ReflectionV3` alias at lines 84-94. |
| `shared/annotation_store.py` | Reader save path, `annotation_slug`, `AnnotationSetAny` union. |
| `agents/robin/book_digest_writer.py` | Already V3-aware via `hasattr(ann_set, "book_id")` (line 200) + `item.type ∈ {"comment", "reflection"}` (lines 211, 219). **No code change**, regression test only. |
| `agents/robin/book_notes_writer.py` | Receives V2 `CommentV2` today; **confirm it accepts V3 `ReflectionV3` via duck-typing** before reusing in `_sync_v3_book` (Investigation §4 caveat — implementer must check). If not duck-typed, add small adapter; do not rewrite. |
| `thousand_sunny/routers/robin.py:_get_inbox_files` (lines 136-194) | Bilingual sibling collapse rule — mirrored in registry. |
| `thousand_sunny/routers/books.py:340-375` | Already aligned (Investigation §1) — read for confirmation, no change. |
| `scripts/migrate_annotations_v3.py` | V2-to-V3 migration. Read-only; relevant for round-trip test. |

### 2.4 Out of scope (per §6 boundary)

- Source Promotion (#511, #512, #513, #514, #515)
- New Reading Overlay features
- `digest.md` / `notes.md` render format changes
- `KB/Wiki/Sources/*` writes
- New annotation kinds beyond V3 schema
- Migration tooling rewrite (`scripts/migrate_annotations_v3.py` stays as-is)
- LLM prompt changes (V3 path re-uses V2 prompts via shared helpers)
- `ReadingSourceRegistry` API extensions
- Bilingual sibling collapse rule changes
- V2 path elimination (deferred refactor)

---

## 3. 輸入 (upstream contracts)

| Input | Source | Notes |
|---|---|---|
| `ReadingSource.annotation_key` | `shared.reading_source_registry.ReadingSourceRegistry` (#509) | Stable derivation per source. ebook → `book_id`; inbox → `annotation_slug(user_facing, fm)`. |
| `AnnotationSetV3` | `shared.schemas.annotations` (ADR-021 §1) | W3C Web Annotation–shaped items: `HighlightV3`, `AnnotationV3`, `ReflectionV3`. Discriminated by `type`. |
| `AnnotationSetAny` | `shared.annotation_store` | Union `V1 | V2 | V3`. Existing `get_annotation_store()` returns this. |
| `CommentV2 → ReflectionV3` alias | `shared/schemas/annotations.py` lines 84-94 | Backward-compat. Old callers importing `CommentV2` continue to work. |
| `_get_inbox_files` collapse rule | `thousand_sunny/routers/robin.py` lines 136-194 | Bilingual sibling collapse; registry mirrors it for `annotation_key`. Reader UI continues to use `_get_inbox_files` for listing (unchanged). |

### `#509` shipped contract (read-only)

```python
# shared/reading_source_registry.py
class ReadingSourceRegistry:
    def resolve(self, key: SourceKey) -> ReadingSource | None: ...

# shared/schemas/reading_source.py
class ReadingSource(BaseModel):
    source_id: str          # stable; do NOT use as KB/Annotations key
    annotation_key: str     # USE THIS for KB/Annotations/{annotation_key}.md
    primary_lang: str
    has_evidence_track: bool
    evidence_reason: Literal["no_original_uploaded", "bilingual_only_inbox"] | None
    variants: list[SourceVariant]
    metadata: dict[str, Any]
```

### Documentation hierarchy

- ADR-024 (main, PR #441 merged) — overall ADR
- ADR-017 (annotation data model) — annotation file layout
- ADR-021 §1 — V3 unified annotation store
- `agents/robin/CONTEXT.md` § Source Promotion — Reading Overlay vs Source Promotion split
- `shared/schemas/reading_source.py` (#509 in main) — `annotation_key` semantics
- `docs/research/2026-05-09-N510-router-save-paths-investigation.md` — pre-rework investigation (read first)
- `docs/principles/schemas.md` — schema discipline

---

## 4. 輸出

### 4.1 Lead change — V3 dispatch in `annotation_merger.py` (Option A)

**Before (lines 175-214 of `agents/robin/annotation_merger.py`):**

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
    return self._sync_v1(ann_set, slug)   # V3 falls here — BUG
```

**After (Option A — additive V3 branch + book/paper sub-discrimination):**

```python
def sync_source_to_concepts(self, slug: str) -> SyncReport:
    from shared.annotation_store import AnnotationSetV2, AnnotationSetV3

    store = get_annotation_store()
    ann_set = store.load(slug)
    if ann_set is None:
        return SyncReport(...)
    # Idempotency short-circuit (works for V3 — modified_at exists on all V3 item types)
    if ann_set.last_synced_at is not None and not any(
        item.modified_at > ann_set.last_synced_at for item in ann_set.items
    ):
        return SyncReport(...)

    if isinstance(ann_set, AnnotationSetV3):
        return self._sync_v3(ann_set, slug)
    if isinstance(ann_set, AnnotationSetV2):
        return self._sync_v2(ann_set)
    return self._sync_v1(ann_set, slug)


def _sync_v3(self, ann_set: AnnotationSetV3, slug: str) -> SyncReport:
    if ann_set.book_id is not None:
        return self._sync_v3_book(ann_set)
    return self._sync_v3_paper(ann_set, slug)


def _sync_v3_book(self, ann_set: AnnotationSetV3) -> SyncReport:
    # Mirrors _sync_v2 structure. Items already discriminated by type:
    #   AnnotationV3 → "## 讀者註記" section + boundary block
    #   ReflectionV3 → notes.md via book_notes_writer
    #   HighlightV3 → skipped (per ADR-017 §Q4 asymmetric)
    annotations = [i for i in ann_set.items if i.type == "annotation"]
    reflections = [i for i in ann_set.items if i.type == "reflection"]
    # Re-use _sync_v2's helpers where shape is identical (V2 CommentV2 ≅ V3 ReflectionV3
    # for body / cfi_anchor / chapter_ref). Confirm book_notes_writer accepts ReflectionV3
    # (see §2.3 caveat — adapter only if needed).
    # ...

def _sync_v3_paper(self, ann_set: AnnotationSetV3, slug: str) -> SyncReport:
    # Mirrors _sync_v1 structure for paper sources. AnnotationV3 → "## 個人觀點"
    # section with <!-- annotation-from: {slug} --> markers.
    # ReflectionV3 on paper sets has no current Reader UI surface (V1 paper had no
    # comment kind); for now, skip and warn. Document in implementation PR.
    # ...
```

**Implementation notes for the agent**:

- Do NOT remove `_sync_v1` or `_sync_v2`. They remain known-good fallbacks for un-migrated stores.
- Reuse `_dispatch_book_items` / `_replace_v2_marker_block` helpers if extractable; if extraction adds risk, duplicate the small body and refactor in a follow-up.
- The `book_notes_writer.write_notes(book_id, comments)` at line 302-308 of `_sync_v2` accepts a list of items with `body` / `chapter_ref` / `cfi_anchor` / `modified_at` shape. `ReflectionV3` exposes the same (per `shared/schemas/annotations.py:163-189`). **Confirm before reusing**; if not duck-typed, add a 5-line adapter (NOT a rewrite).

### 4.2 Inbox reader page migration in `routers/robin.py:235`

**Before:**

```python
slug = annotation_slug(file, frontmatter)
ann_store: AnnotationStore = get_annotation_store()
ann_set = ann_store.load(slug)
```

**After:**

```python
from shared.reading_source_registry import ReadingSourceRegistry, InboxKey

rs = ReadingSourceRegistry().resolve(InboxKey(file))   # `file` is vault-relative
if rs is None:
    raise HTTPException(404, detail=f"找不到檔案：{file}")
slug = rs.annotation_key
ann_store: AnnotationStore = get_annotation_store()
ann_set = ann_store.load(slug)
```

**Caveats** (Investigation §2):

- `InboxKey.relative_path` accepts vault-relative paths under `Inbox/kb/`. Confirm `file` parameter at this route is already a vault-relative path. Adjust if absolute / different prefix shape.
- The save endpoint at `routers/robin.py:275-293` is unchanged — it trusts whatever `slug` the Reader UI posted. The migration changes `slug` derivation only at page render; save remains a thin store-write.
- The Reader page still injects `slug` into `reader.html` (line 246) for the Reader UI's subsequent POST. Net effect: single source of truth at page-render side.

### 4.3 Bilingual-sibling URL reachability — confirmation task

The registry collapses to bilingual sibling for `annotation_key`; the current Reader page route at `routers/robin.py:235` does not. Two outcomes (Investigation §2):

- **If `_get_inbox_files` already prevents users from ever opening `foo.md` URL when `foo-bilingual.md` exists** (i.e. listing UI never shows the original-only path), the divergence is moot — current behavior matches registry behavior by accident of routing. Migration is a no-op convergence.
- **If a user CAN land on `foo.md` URL** (direct link, bookmark, query param fallback), current behavior derives slug from `foo.md` frontmatter; registry would derive from `foo-bilingual.md` frontmatter. These slugs may differ if the bilingual sibling has a different `title:` field. **This is a behavior change** that must be called out in the PR body as intentional convergence.

**Implementer task**: search `thousand_sunny/routers/robin.py` reader entry routes (`/files/...`, `/reader/...`, etc.) for the URL→file mapping. Determine reachability. Document in PR body. If reachable, add a regression test (T-N9) asserting the convergence.

### 4.4 Regression tests (T-N1 — T-N9, all NEW)

| # | Test | Asserts |
|---|---|---|
| T-N1 | `test_annotation_merger_v1_paper_unchanged` | V1 paper sync still works; V2 / V3 codepaths NOT touched. |
| T-N2 | `test_annotation_merger_v2_book_unchanged` | V2 book sync (`AnnotationV2 → Concept page`, `CommentV2 → notes.md`) continues to work. |
| T-N3 | `test_annotation_merger_v3_book_routes_correctly` | V3 book sync: `AnnotationV3 → 讀者註記 section`; `ReflectionV3 → notes.md`; `HighlightV3` skipped (per ADR-017 §Q4). |
| T-N4 | `test_annotation_merger_v3_paper_routes_or_warns` | V3 paper sync: `AnnotationV3 → 個人觀點 section`; `ReflectionV3` on paper sets is skipped + warned (no Reader UI surface). |
| T-N5 | `test_book_digest_writer_v3_regression` | Feed `AnnotationSetV3` book set with `ReflectionV3` items into `write_digest()`; output is byte-equal to V2 digest given equivalent items. |
| T-N6 | `test_book_notes_writer_accepts_reflection_v3` | `book_notes_writer.write_notes(book_id, [ReflectionV3, ...])` produces same `notes.md` shape as V2 `CommentV2` input. (If not duck-typed, this test exercises the new adapter.) |
| T-N7 | `test_inbox_reader_page_uses_registry_slug` | Mock `routers/robin.py:235`; assert it calls `registry.resolve(InboxKey(file)).annotation_key` and injects that slug into `reader.html`. |
| T-N8 | `test_inbox_reader_page_404_on_missing` | When `registry.resolve(...)` returns `None`, route raises HTTP 404. |
| T-N9 | `test_inbox_bilingual_sibling_url_reachability` (conditional — see §4.3) | If `foo.md` is reachable when `foo-bilingual.md` exists, assert slug derives from bilingual sibling. Else: skip + document. |

(Existing V1 / V2 tests remain green; this slice is additive at the V3 level + a router-side migration.)

---

## 5. 驗收

### Issue #510 列出的 4 條 AC（必過）

- [ ] Book annotation saves using V3 still generate or preserve `digest.md` behavior.
- [ ] Book reflections route to `notes.md` through the intended book path.
- [ ] Regression tests cover annotation store v1/v2/v3, book digest, book notes, and sync dispatch.
- [ ] (Annotation-only sync stays separate from Source Promotion — moved to §6 boundary, asserted by absence rather than active scope.)

### Self-imposed gates

- [ ] `agents/robin/annotation_merger.py` has additive `_sync_v3` + `_sync_v3_book` + `_sync_v3_paper`. V1 + V2 paths byte-identical to pre-PR (asserted by `git diff --stat` on `_sync_v1` / `_sync_v2` showing zero changes inside their bodies).
- [ ] `thousand_sunny/routers/robin.py:235` no longer calls `annotation_slug(...)` directly; uses `ReadingSourceRegistry`. Verified by `grep -nE 'annotation_slug\(' thousand_sunny/routers/robin.py` returning at most 0-1 legacy / non-save call sites.
- [ ] `thousand_sunny/routers/books.py` save path unchanged (verification only). `git diff origin/main thousand_sunny/routers/books.py` empty or doc-only.
- [ ] `agents/robin/book_digest_writer.py` unchanged. T-N5 regression added.
- [ ] `agents/robin/book_notes_writer.py` either unchanged or has only a small adapter. T-N6 covers.
- [ ] No changes to `digest.md` or `notes.md` rendering format. Byte-equal output for unchanged inputs (T-N5 / T-N6 snapshot compare).
- [ ] No changes to `scripts/migrate_annotations_v3.py`.
- [ ] No new dependencies (`requirements.txt` unchanged).
- [ ] `python -m pytest tests/agents/robin/test_annotation_merger_v3.py tests/agents/robin/test_book_digest_writer_v3.py tests/thousand_sunny/test_robin_inbox_save_v2.py -v` clean.
- [ ] `python -m pytest tests/agents/robin/ tests/thousand_sunny/test_books_annotations_api.py tests/test_robin_router.py -v` (full regression sweep) clean.
- [ ] `python -m ruff check thousand_sunny/routers/ agents/robin/ tests/agents/robin/` clean.
- [ ] `python -m ruff format --check thousand_sunny/routers/ agents/robin/ tests/agents/robin/` clean.
- [ ] PR body contains a P7-COMPLETION self-review block + bilingual-sibling URL reachability finding (per §4.3).

---

## 6. 邊界（不能碰）

| # | Don't | Why |
|---|---|---|
| 1 | ❌ Source Promotion / Manifest / Preflight logic | #511 / #512 / #513-#515 |
| 2 | ❌ New Reader UI features | This is stabilization, not feature work |
| 3 | ❌ Change `digest.md` / `notes.md` rendering format | Full-replace semantics preserved; byte-equal regression |
| 4 | ❌ KB/Wiki/Sources/* writes | Promotion output, not Reading Overlay |
| 5 | ❌ New annotation kinds beyond V3 schema | Schema lives in `shared/schemas/annotations.py`; no new V3 sub-type in #510 |
| 6 | ❌ Migration tooling rewrite | `scripts/migrate_annotations_v3.py` stays as-is |
| 7 | ❌ Prompt rewrites in `_ask_merger_llm_v2` | V3 path re-uses V2 prompt; prompt evolution is a separate slice |
| 8 | ❌ `ReadingSourceRegistry` API extensions | #509 is shipped; #510 is read-only consumer |
| 9 | ❌ Bilingual sibling collapse rule changes | `_get_inbox_files` semantics unchanged; registry mirrors |
| 10 | ❌ New SQL migration | None needed |
| 11 | ❌ LLM call changes | Use existing prompts |
| 12 | ❌ New annotation file path conventions | `KB/Annotations/{annotation_key}.md` pattern preserved |
| 13 | ❌ V2 path elimination / normalize-to-V3-at-entry | V2 stays as known-good fallback; refactor is deferred |
| 14 | ❌ Books-side router save changes | Already aligned (Investigation §1); modification would introduce risk for no benefit |
| 15 | ❌ `book_digest_writer.py` rewrites | Already V3-aware via duck-typing (Investigation §3); modification would introduce risk |
| 16 | ❌ Annotation-only sync becoming Source Promotion | These are separate authority lanes per ADR-024; never converge |

---

## 7. References

### Primary

- ADR: `docs/decisions/ADR-024-source-promotion-and-reading-context-package.md`
- ADR-017: annotation data model
- ADR-021 §1: unified V3 annotation store
- Robin context: `agents/robin/CONTEXT.md` § Source Promotion
- ReadingSource (#509): `shared/schemas/reading_source.py`, `shared/reading_source_registry.py` (main `0f2742f`)
- Annotation schemas: `shared/schemas/annotations.py`
- Annotation store: `shared/annotation_store.py`
- Annotation merger: `agents/robin/annotation_merger.py`
- Book digest writer: `agents/robin/book_digest_writer.py`
- Book notes writer: `agents/robin/book_notes_writer.py`
- Migration script: `scripts/migrate_annotations_v3.py`
- Routers: `thousand_sunny/routers/{books,robin}.py`
- **Pre-rework investigation: `docs/research/2026-05-09-N510-router-save-paths-investigation.md`** (read first)

### Issue / PR

- Issue #510: `[ADR-024 S2] Reading Overlay V3 Stabilization`
- Parent PRD #508: `Source Promotion and Reading Context Package`
- Slice 1 (#509) ship: PR #518 squash-merged at `0f2742f` (2026-05-09)
- Investigation note + N511 v2 + memory in main: PR #519 squash-merged at `b981d2e` (2026-05-09)

---

## 8. Triage status

`needs-triage` → 等修修讀 v2 Brief 後決定。**不要**自行 relabel `ready-for-agent`，**不要**直接開始 code。

LOC estimate: **~200-300** (down from v1's 400-600 after investigation tightened scope).

Dispatch decision: **Suitable for sandcastle batch dispatch** after relabel `ready-for-agent` (per `feedback_sandcastle_default.md` default = sandcastle rule). Slice is bounded — merger dispatch + one router migration + tests.
