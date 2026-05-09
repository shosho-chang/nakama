"""ConceptPageAnnotationMerger — syncs reader annotations into Concept pages.

ADR-017 Slice 2 + Slice 5 (book extension) + ADR-021 §1 (V3 unified store):
per-source full-replace via HTML comment boundary markers, dispatched on
schema_version.

Flow (shared):
  1. Load AnnotationSet from store
  2. Short-circuit if nothing changed since last sync (idempotency)
  3. Dispatch on schema_version

v1 paper path (AnnotationSet):
  - Filter to Annotation items only (Highlight not synced — ADR-017 §Q4 asymmetric)
  - LLM (tool_use forced JSON): annotations + concept list → {concept_slug: callout_block}
  - Insert / replace boundary-marked block in each matched Concept page ## 個人觀點

v2 book path (AnnotationSetV2):
  - Comments → ``KB/Wiki/Sources/Books/{book_id}/notes.md`` via book_notes_writer
  - Annotations → Concept page ## 讀者註記 via _ask_merger_llm_v2 (highlights skipped, same §Q4)
  - Per-book boundary markers keep multi-book aggregation isolated

v3 unified path (AnnotationSetV3):
  - Discriminated by ``book_id`` presence:
    * book set (``book_id is not None``) — mirrors v2 book path:
        ReflectionV3 → notes.md (chapter_ref required; missing-ref reflections
        are dropped with a warning since notes.md groups by chapter heading)
        AnnotationV3 → ## 讀者註記 (re-uses v2 helpers; item shape compatible)
        HighlightV3 → skipped (ADR-017 §Q4 asymmetric)
    * paper set (``book_id is None``) — mirrors v1 paper path:
        AnnotationV3 → ## 個人觀點 with per-source markers
        ReflectionV3 → skipped + warned (no v1-paper Reader UI surface)
        HighlightV3 → skipped (ADR-017 §Q4 asymmetric)
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel

from shared.annotation_store import get_annotation_store
from shared.config import get_vault_path
from shared.kb_writer import KB_CONCEPTS_DIR, _load_page, _write_page_file
from shared.log import get_logger
from shared.prompt_loader import load_prompt

logger = get_logger("nakama.robin.annotation_merger")

_SECTION_HEADING = "## 個人觀點"
_MARKER_OPEN_TMPL = "<!-- annotation-from: {slug} -->"
_MARKER_CLOSE_TMPL = "<!-- /annotation-from: {slug} -->"

_V2_SECTION_HEADING = "## 讀者註記"
_V2_MARKER_OPEN_TMPL = "<!-- annotation-from: {book_id} -->"
_V2_MARKER_CLOSE_TMPL = "<!-- end-annotation-from: {book_id} -->"

_MERGER_TOOL: dict = {
    "name": "merge_annotations",
    "description": (
        "Return the per-concept callout blocks for annotations that match existing concept pages. "
        "Only include concepts with a genuine thematic match. "
        "Empty mapping means no annotations matched any concept."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "mapping": {
                "type": "object",
                "description": (
                    "Keys: concept_slug exactly as listed in the concept list. "
                    "Values: callout block markdown string for that concept."
                ),
                "additionalProperties": {"type": "string"},
            }
        },
        "required": ["mapping"],
    },
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MergerLLMError(Exception):
    """Raised when the LLM fails to invoke the merge_annotations tool correctly."""


# ---------------------------------------------------------------------------
# SyncReport
# ---------------------------------------------------------------------------


class SyncReport(BaseModel):
    """Report from sync_source_to_concepts."""

    source_slug: str
    concepts_updated: list[str]
    annotations_merged: int
    skipped_annotations: int
    errors: list[str]
    unsynced_count: int = 0


# ---------------------------------------------------------------------------
# Boundary marker helpers (pure functions — no I/O)
# ---------------------------------------------------------------------------


def _replace_marker_block(body: str, source_slug: str, callout_block: str) -> str:
    """Insert or replace per-source annotation block in ## 個人觀點 section.

    Idempotent: calling with the same source_slug and callout_block twice produces
    identical output.  Per-source isolation: only the block for source_slug is
    touched; other sources' blocks in the same section are left unchanged.
    """
    open_marker = _MARKER_OPEN_TMPL.format(slug=source_slug)
    close_marker = _MARKER_CLOSE_TMPL.format(slug=source_slug)
    full_block = f"{open_marker}\n{callout_block}\n{close_marker}"

    # Case 1: existing markers → wipe and re-render (idempotent)
    if open_marker in body and close_marker in body:
        start = body.index(open_marker)
        end = body.index(close_marker) + len(close_marker)
        return body[:start] + full_block + body[end:]

    # Case 2: ## 個人觀點 section exists → append inside it (before next H2 or end)
    if _SECTION_HEADING in body:
        section_start = body.index(_SECTION_HEADING) + len(_SECTION_HEADING)
        m = re.search(r"\n## ", body[section_start:])
        if m:
            insert_at = section_start + m.start()
            return body[:insert_at] + "\n\n" + full_block + body[insert_at:]
        return body.rstrip("\n") + "\n\n" + full_block + "\n"

    # Case 3: no section → append section + block at end
    return body.rstrip("\n") + f"\n\n{_SECTION_HEADING}\n\n{full_block}\n"


# ---------------------------------------------------------------------------
# LLM boundary (monkeypatch this in tests)
# ---------------------------------------------------------------------------


def _ask_merger_llm(prompt: str) -> dict[str, str]:
    """Call LLM to map annotations → concept callout blocks using forced tool_use.

    Uses tool_choice to guarantee structured JSON output — eliminates the raw-text
    JSON parsing failures that occurred with the previous ask() approach.

    Returns:
        {concept_slug: callout_block_markdown_string}
    Raises:
        MergerLLMError: if the LLM did not invoke the merge_annotations tool.
    """
    from shared.llm import ask_with_tools

    response = ask_with_tools(
        messages=[{"role": "user", "content": prompt}],
        tools=[_MERGER_TOOL],
        model="claude-opus-4-7",
        max_tokens=8000,
        tool_choice={"type": "tool", "name": "merge_annotations"},
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "merge_annotations":
            mapping = block.input.get("mapping", {})
            return {k: v for k, v in mapping.items() if isinstance(k, str) and isinstance(v, str)}

    raise MergerLLMError("merger LLM did not invoke merge_annotations tool")


# ---------------------------------------------------------------------------
# ConceptPageAnnotationMerger
# ---------------------------------------------------------------------------


class ConceptPageAnnotationMerger:
    """Syncs reader annotations into Concept pages, dispatched on schema_version.

    v1 → ## 個人觀點 (papers, AnnotationSet)
    v2 → ## 讀者註記 (books, AnnotationSetV2) + comments to KB/Wiki/Sources/Books/.../notes.md
    """

    def sync_source_to_concepts(self, slug: str) -> SyncReport:
        """Read annotations for slug, dispatch on schema_version, merge.

        Args:
            slug: source slug (e.g. "sport-nutrition-ch3" for v1 paper, or
                book_id like "kahneman-thinking-fast-slow" for v2/v3 book)

        Returns:
            SyncReport with counts, updated concept slugs, and any errors.
        """
        from shared.annotation_store import AnnotationSetV2, AnnotationSetV3

        store = get_annotation_store()
        ann_set = store.load(slug)

        if ann_set is None:
            return SyncReport(
                source_slug=slug,
                concepts_updated=[],
                annotations_merged=0,
                skipped_annotations=0,
                errors=[f"AnnotationStore: no entry for slug '{slug}'"],
            )

        # Idempotency short-circuit: nothing new since last sync → skip LLM call
        if ann_set.last_synced_at is not None and not any(
            item.modified_at > ann_set.last_synced_at for item in ann_set.items
        ):
            return SyncReport(
                source_slug=slug,
                concepts_updated=[],
                annotations_merged=0,
                skipped_annotations=0,
                errors=[],
                unsynced_count=0,
            )

        if isinstance(ann_set, AnnotationSetV3):
            return self._sync_v3(ann_set, slug)
        if isinstance(ann_set, AnnotationSetV2):
            return self._sync_v2(ann_set)
        return self._sync_v1(ann_set, slug)

    def _sync_v1(self, ann_set, slug: str) -> SyncReport:
        annotations = [item for item in ann_set.items if item.type == "annotation"]
        if not annotations:
            return SyncReport(
                source_slug=slug,
                concepts_updated=[],
                annotations_merged=0,
                skipped_annotations=0,
                errors=[],
            )

        concept_slugs = self._list_concept_slugs()
        prompt = load_prompt(
            "robin",
            "merge_annotations",
            source_slug=slug,
            concept_slugs=", ".join(concept_slugs) if concept_slugs else "(none)",
            annotations_json=json.dumps(
                [a.model_dump() for a in annotations],
                ensure_ascii=False,
                indent=2,
            ),
        )
        try:
            linkage: dict[str, str] = _ask_merger_llm(prompt)
        except MergerLLMError as exc:
            logger.error(
                "annotation merger LLM failed",
                extra={"source_slug": slug, "error": str(exc)},
            )
            return SyncReport(
                source_slug=slug,
                concepts_updated=[],
                annotations_merged=len(annotations),
                skipped_annotations=0,
                errors=["⚠️ 同步錯誤：LLM 回傳格式錯誤，請重試"],
            )

        concepts_updated: list[str] = []
        skipped = 0
        errors: list[str] = []

        for concept_slug, callout_block in linkage.items():
            concept_path = get_vault_path() / KB_CONCEPTS_DIR / f"{concept_slug}.md"
            if not concept_path.exists():
                logger.warning(
                    "concept not found, skipping annotation sync",
                    extra={"concept_slug": concept_slug, "source_slug": slug},
                )
                skipped += 1
                continue

            result = _load_page(concept_path)
            if result is None:
                errors.append(f"failed to load concept page: {concept_slug}")
                continue
            fm, body = result

            updated_body = _replace_marker_block(body, slug, callout_block.strip())
            if updated_body != body:
                _write_page_file(concept_path, fm, updated_body)
                concepts_updated.append(concept_slug)

        return SyncReport(
            source_slug=slug,
            concepts_updated=concepts_updated,
            annotations_merged=len(annotations),
            skipped_annotations=skipped,
            errors=errors,
        )

    def _list_concept_slugs(self) -> list[str]:
        """Return sorted list of existing concept page slugs."""
        concepts_dir = get_vault_path() / KB_CONCEPTS_DIR
        if not concepts_dir.exists():
            return []
        return sorted(p.stem for p in concepts_dir.glob("*.md"))

    def _sync_v2(self, ann_set) -> SyncReport:
        """v2 book path: comments → notes.md (Slice 5B), annotations → concepts ## 讀者註記.

        Highlights are not synced (ADR-017 §Q4 — same asymmetric rule as v1).
        Comments do not propagate to concept pages (per PRD #378 user-story 32:
        readers' subjective reflections must not pollute the cross-source aggregator);
        they are routed to a per-book notes.md instead.
        """
        from agents.robin.book_notes_writer import write_notes

        book_id = ann_set.book_id

        # 1. Comments → KB/Wiki/Sources/Books/{book_id}/notes.md (idempotent on empty list)
        comments = [i for i in ann_set.items if i.type == "comment"]
        write_notes(book_id, comments)

        # 2. Annotations → Concept page ## 讀者註記 (LLM-matched)
        annotations = [i for i in ann_set.items if i.type == "annotation"]
        if not annotations:
            return SyncReport(
                source_slug=book_id,
                concepts_updated=[],
                annotations_merged=0,
                skipped_annotations=0,
                errors=[],
            )

        concept_slugs = _list_concept_slugs_v2()
        try:
            mapping = _ask_merger_llm_v2(annotations, concept_slugs)
        except MergerLLMError as exc:
            logger.error(
                "annotation merger v2 LLM failed",
                extra={"book_id": book_id, "error": str(exc)},
            )
            return SyncReport(
                source_slug=book_id,
                concepts_updated=[],
                annotations_merged=len(annotations),
                skipped_annotations=0,
                errors=["⚠️ 同步錯誤：LLM 回傳格式錯誤，請重試"],
            )

        concepts_updated, skipped, errors = _upsert_concept_blocks_v2(book_id, mapping)
        return SyncReport(
            source_slug=book_id,
            concepts_updated=concepts_updated,
            annotations_merged=len(annotations),
            skipped_annotations=skipped,
            errors=errors,
        )

    def _sync_v3(self, ann_set, slug: str) -> SyncReport:
        """v3 unified path: book vs paper sub-discrimination via ``book_id``.

        ADR-021 §1: post-migration sets use V3 schema regardless of source
        kind. ``book_id`` presence is the canonical discriminator (set by
        ``upgrade_to_v3`` at save boundaries).
        """
        if ann_set.book_id is not None:
            return self._sync_v3_book(ann_set)
        return self._sync_v3_paper(ann_set, slug)

    def _sync_v3_book(self, ann_set) -> SyncReport:
        """v3 book path: mirrors _sync_v2 dispatch.

        ReflectionV3 → notes.md (chapter_ref required; reflections without
        chapter_ref are dropped with a logged warning since notes.md groups by
        chapter heading and a None heading would render as ``## None``).

        AnnotationV3 → Concept page ## 讀者註記 via _ask_merger_llm_v2 +
        _upsert_concept_blocks_v2 (item shape — text_excerpt / note / cfi —
        matches AnnotationV2; the LLM input contract via model_dump is
        compatible).

        HighlightV3 → skipped (ADR-017 §Q4 asymmetric — highlights are not
        synced to concept pages, same as v1 + v2).
        """
        from agents.robin.book_notes_writer import write_notes

        book_id = ann_set.book_id

        reflections = [i for i in ann_set.items if i.type == "reflection"]
        reflections_with_chapter = [r for r in reflections if r.chapter_ref]
        dropped = len(reflections) - len(reflections_with_chapter)
        if dropped:
            logger.warning(
                "v3 book sync: dropping reflections without chapter_ref",
                extra={"book_id": book_id, "dropped_count": dropped},
            )
        write_notes(book_id, reflections_with_chapter)

        annotations = [i for i in ann_set.items if i.type == "annotation"]
        if not annotations:
            return SyncReport(
                source_slug=book_id,
                concepts_updated=[],
                annotations_merged=0,
                skipped_annotations=0,
                errors=[],
            )

        concept_slugs = _list_concept_slugs_v2()
        try:
            mapping = _ask_merger_llm_v2(annotations, concept_slugs)
        except MergerLLMError as exc:
            logger.error(
                "annotation merger v3 book LLM failed",
                extra={"book_id": book_id, "error": str(exc)},
            )
            return SyncReport(
                source_slug=book_id,
                concepts_updated=[],
                annotations_merged=len(annotations),
                skipped_annotations=0,
                errors=["⚠️ 同步錯誤：LLM 回傳格式錯誤，請重試"],
            )

        concepts_updated, skipped, errors = _upsert_concept_blocks_v2(book_id, mapping)
        return SyncReport(
            source_slug=book_id,
            concepts_updated=concepts_updated,
            annotations_merged=len(annotations),
            skipped_annotations=skipped,
            errors=errors,
        )

    def _sync_v3_paper(self, ann_set, slug: str) -> SyncReport:
        """v3 paper path: mirrors _sync_v1 structure.

        AnnotationV3 → ## 個人觀點 section with ``<!-- annotation-from: {slug} -->``
        markers (re-uses _replace_marker_block + _ask_merger_llm).

        ReflectionV3 on paper sets is dropped + warned: V1 paper had no
        comment kind and there is no Reader UI surface for paper-side
        reflections. Reflections are intentionally a book-only concept.

        HighlightV3 → skipped (ADR-017 §Q4 asymmetric).
        """
        reflections = [i for i in ann_set.items if i.type == "reflection"]
        if reflections:
            logger.warning(
                "v3 paper sync: dropping reflections (no Reader UI surface for paper reflections)",
                extra={"slug": slug, "dropped_count": len(reflections)},
            )

        annotations = [i for i in ann_set.items if i.type == "annotation"]
        if not annotations:
            return SyncReport(
                source_slug=slug,
                concepts_updated=[],
                annotations_merged=0,
                skipped_annotations=0,
                errors=[],
            )

        concept_slugs = self._list_concept_slugs()
        prompt = load_prompt(
            "robin",
            "merge_annotations",
            source_slug=slug,
            concept_slugs=", ".join(concept_slugs) if concept_slugs else "(none)",
            annotations_json=json.dumps(
                [a.model_dump() for a in annotations],
                ensure_ascii=False,
                indent=2,
            ),
        )
        try:
            linkage: dict[str, str] = _ask_merger_llm(prompt)
        except MergerLLMError as exc:
            logger.error(
                "annotation merger v3 paper LLM failed",
                extra={"source_slug": slug, "error": str(exc)},
            )
            return SyncReport(
                source_slug=slug,
                concepts_updated=[],
                annotations_merged=len(annotations),
                skipped_annotations=0,
                errors=["⚠️ 同步錯誤：LLM 回傳格式錯誤，請重試"],
            )

        concepts_updated: list[str] = []
        skipped = 0
        errors: list[str] = []

        for concept_slug, callout_block in linkage.items():
            concept_path = get_vault_path() / KB_CONCEPTS_DIR / f"{concept_slug}.md"
            if not concept_path.exists():
                logger.warning(
                    "concept not found, skipping v3 paper annotation sync",
                    extra={"concept_slug": concept_slug, "source_slug": slug},
                )
                skipped += 1
                continue

            result = _load_page(concept_path)
            if result is None:
                errors.append(f"failed to load concept page: {concept_slug}")
                continue
            fm, body = result

            updated_body = _replace_marker_block(body, slug, callout_block.strip())
            if updated_body != body:
                _write_page_file(concept_path, fm, updated_body)
                concepts_updated.append(concept_slug)

        return SyncReport(
            source_slug=slug,
            concepts_updated=concepts_updated,
            annotations_merged=len(annotations),
            skipped_annotations=skipped,
            errors=errors,
        )


# ---------------------------------------------------------------------------
# v2 book path helpers — ## 讀者註記
# ---------------------------------------------------------------------------


def _list_concept_slugs_v2() -> list[str]:
    concepts_dir = get_vault_path() / KB_CONCEPTS_DIR
    if not concepts_dir.exists():
        return []
    return sorted(p.stem for p in concepts_dir.glob("*.md"))


def _replace_v2_marker_block(body: str, book_id: str, callout_block: str) -> str:
    """Insert or replace per-book annotation block in ## 讀者註記 section. Idempotent."""
    open_marker = _V2_MARKER_OPEN_TMPL.format(book_id=book_id)
    close_marker = _V2_MARKER_CLOSE_TMPL.format(book_id=book_id)
    full_block = f"{open_marker}\n{callout_block}\n{close_marker}"

    if open_marker in body and close_marker in body:
        start = body.index(open_marker)
        end = body.index(close_marker) + len(close_marker)
        return body[:start] + full_block + body[end:]

    if _V2_SECTION_HEADING in body:
        section_start = body.index(_V2_SECTION_HEADING) + len(_V2_SECTION_HEADING)
        m = re.search(r"\n## ", body[section_start:])
        if m:
            insert_at = section_start + m.start()
            return body[:insert_at] + "\n\n" + full_block + body[insert_at:]
        return body.rstrip("\n") + "\n\n" + full_block + "\n"

    return body.rstrip("\n") + f"\n\n{_V2_SECTION_HEADING}\n\n{full_block}\n"


def _ask_merger_llm_v2(items, concept_slugs: list[str]) -> dict[str, str]:
    """LLM call for v2 book items → per-concept callout mapping (tool_use forced JSON)."""
    from shared.llm import ask_with_tools

    items_json = json.dumps(
        [i.model_dump() for i in items],
        ensure_ascii=False,
        indent=2,
    )
    prompt = (
        f"You are a knowledge-base curator. Map the following book highlights and annotations "
        f"to the most relevant concept pages.\n\n"
        f"Existing concept slugs:\n{', '.join(concept_slugs) if concept_slugs else '(none)'}\n\n"
        f"Book annotations (JSON):\n{items_json}\n\n"
        f"For each matched concept, produce a callout block attributed to the book source. "
        f"Only include concepts with a genuine thematic match."
    )

    response = ask_with_tools(
        messages=[{"role": "user", "content": prompt}],
        tools=[_MERGER_TOOL],
        model="claude-opus-4-7",
        max_tokens=8000,
        tool_choice={"type": "tool", "name": "merge_annotations"},
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "merge_annotations":
            mapping = block.input.get("mapping", {})
            return {k: v for k, v in mapping.items() if isinstance(k, str) and isinstance(v, str)}

    raise MergerLLMError("merger LLM did not invoke merge_annotations tool (v2)")


def _upsert_concept_blocks_v2(
    book_id: str, mapping: dict[str, str]
) -> tuple[list[str], int, list[str]]:
    """Write v2 callout blocks into Concept pages. Returns (updated, skipped, errors)."""
    concepts_dir = get_vault_path() / KB_CONCEPTS_DIR
    concepts_updated: list[str] = []
    skipped = 0
    errors: list[str] = []

    for concept_slug, callout_block in mapping.items():
        concept_path = concepts_dir / f"{concept_slug}.md"
        if not concept_path.exists():
            logger.warning(
                "concept not found, skipping v2 annotation sync",
                extra={"concept_slug": concept_slug, "book_id": book_id},
            )
            skipped += 1
            continue
        result = _load_page(concept_path)
        if result is None:
            errors.append(f"failed to load concept page: {concept_slug}")
            continue
        fm, body = result
        updated_body = _replace_v2_marker_block(body, book_id, callout_block.strip())
        if updated_body != body:
            _write_page_file(concept_path, fm, updated_body)
            concepts_updated.append(concept_slug)

    return concepts_updated, skipped, errors


# ---------------------------------------------------------------------------
# Public dispatch entry point
# ---------------------------------------------------------------------------


def sync_annotations_for_slug(slug: str) -> SyncReport:
    """Public dispatch entry point — forwards to ConceptPageAnnotationMerger.

    The merger's ``sync_source_to_concepts`` method handles v1/v2 dispatch,
    idempotency, and (for v2) routes comments to ``book_notes_writer``.
    """
    return ConceptPageAnnotationMerger().sync_source_to_concepts(slug)
