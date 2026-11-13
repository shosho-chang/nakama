"""Weave AnnotationSetV1 annotation callouts into a bilingual MD body.

Pure string transformation — no IO, no LLM.

``weave(body, items)`` inserts Obsidian ``> [!annotation]`` callouts below
each paragraph block whose text contains the annotation's ``ref`` as a plain
substring. Highlights (``type="highlight"``) carry no callout — their
``==text==`` markers already live in the source MD body.

Paragraph splitting: blocks separated by one or more blank lines. A block
matches when ``annotation.ref`` is a substring of the block's raw text (MVP
exact substring match — fuzzy match is Phase 2).

Multiple annotations on the same block are appended in list order, each as
its own callout. Ref not found → warning via ``log_fn``; callout skipped.
Partial weave beats a failed ingest.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from shared.log import get_logger
from shared.schemas.annotations import AnnotationItemV1, AnnotationV1

logger = get_logger("nakama.robin.annotation_weave")

_PARA_SPLIT = re.compile(r"\n{2,}")


def weave(
    body: str,
    items: Sequence[AnnotationItemV1],
    *,
    log_fn: Callable[..., None] | None = None,
) -> str:
    """Return ``body`` with ``> [!annotation]`` callouts woven inline.

    Only ``AnnotationV1`` (``type="annotation"``) items produce callouts.
    ``HighlightV1`` (``type="highlight"``) items are silently skipped — their
    ``==text==`` markers are already embedded in the MD body.

    Args:
        body:   Bilingual MD body text (without frontmatter).
        items:  Annotation items from an ``AnnotationSetV1``.
        log_fn: Called with a warning message when a ref is not found.
                Defaults to ``logger.warning``.

    Returns:
        Body with callouts inserted, or the original body when no
        ``AnnotationV1`` items exist.
    """
    _warn = log_fn if log_fn is not None else logger.warning

    annotations = [item for item in items if isinstance(item, AnnotationV1)]
    if not annotations:
        return body

    # Split on 2+ consecutive newlines, preserving paragraph order.
    blocks: list[str] = _PARA_SPLIT.split(body)

    # Accumulate callouts per block index.  Multiple annotations on the same
    # block are appended in input order.
    block_callouts: dict[int, list[str]] = {}
    for ann in annotations:
        matched = False
        for idx, block in enumerate(blocks):
            if ann.ref in block:
                block_callouts.setdefault(idx, []).append(_render_callout(ann.note))
                matched = True
                break
        if not matched:
            _warn(
                "annotation_weave: ref not found in any paragraph — skipping (ref=%r, note=%r)",
                ann.ref[:60],
                ann.note[:60],
            )

    # Reassemble: each block followed by its callouts (if any).
    parts: list[str] = []
    for idx, block in enumerate(blocks):
        parts.append(block)
        if idx in block_callouts:
            parts.extend(block_callouts[idx])

    return "\n\n".join(parts)


def _render_callout(note: str) -> str:
    """Render ``note`` as an Obsidian ``> [!annotation]`` callout block.

    Multi-line notes have each line prefixed with ``> ``.  An empty note
    produces an empty callout body line (``> ``).
    """
    lines = note.splitlines() if note else [""]
    body_lines = "\n".join(f"> {line}" for line in lines)
    return f"> [!annotation]\n{body_lines}"
