"""ConceptPageAnnotationMerger — syncs annotations to Concept page ## 個人觀點 section.

ADR-017 Slice 2: per-source full-replace via HTML comment boundary markers.

Flow:
  1. Load AnnotationSet from store
  2. Short-circuit if nothing changed since last sync (idempotency)
  3. Filter to Annotation items only (Highlight items are not synced — ADR-017 §Q4 asymmetric)
  4. List existing concept slugs from vault
  5. LLM (tool_use forced JSON): annotations + concept list → {concept_slug: callout_block_str}
  6. For each concept in result: insert / replace boundary-marked block in ## 個人觀點
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
    """Syncs annotations from AnnotationStore[slug] into Concept page ## 個人觀點 sections."""

    def sync_source_to_concepts(self, slug: str) -> SyncReport:
        """Read annotations for slug, merge into matching concept pages.

        Args:
            slug: source slug (e.g. "sport-nutrition-ch3")

        Returns:
            SyncReport with counts, updated concept slugs, and any errors.
        """
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
