# STAGE-1.5-AMBIGUITY: concept dispatch module found but L2/L3 source_paragraphs
# are not threaded through dispatch_concept(). Validators live here in isolation
# until Stage 4.5/5.3 wires them into the dispatch call with a source_paragraphs arg.
from __future__ import annotations

from shared.concept_dispatch import IngestFailError

__all__ = [
    "IngestFailError",
    "L2_FORBIDDEN_STRINGS",
    "L3_FORBIDDEN_STRINGS",
    "validate_l2_concept",
    "validate_l3_concept",
]

L2_FORBIDDEN_STRINGS: list[str] = [
    "Will be enriched later",
    "TODO",
    "Placeholder",
    "(this chapter)",
]

# L3 shares the same banned phrases as L2; kept as a separate binding so callers
# can reference the correct tier without knowing the implementation is identical.
L3_FORBIDDEN_STRINGS: list[str] = L2_FORBIDDEN_STRINGS


def validate_l2_concept(body: str, source_paragraphs: list[str]) -> None:
    """Raise IngestFailError if any L2 concept rule is violated.

    `body` is the page body (post-frontmatter).
    `source_paragraphs` are non-empty verbatim paragraphs from the current chapter.
    """
    word_count = len(body.split())
    if word_count < 200:
        raise IngestFailError(f"L2 word_count={word_count} < 200")
    if not any(p.strip() and p.strip() in body for p in source_paragraphs):
        raise IngestFailError("L2 missing chapter source paragraph")
    for s in L2_FORBIDDEN_STRINGS:
        if s in body:
            raise IngestFailError(f"L2 forbidden string: {s!r}")


def validate_l3_concept(body: str, source_paragraphs_by_chapter: dict[str, list[str]]) -> None:
    """Raise IngestFailError if any L3 concept rule is violated.

    `body` is the page body (post-frontmatter).
    `source_paragraphs_by_chapter` maps chapter key → verbatim paragraph list for that chapter.
    L3 requires ≥ 2 chapters to each contribute at least one matching paragraph.
    """
    word_count = len(body.split())
    if word_count < 200:
        raise IngestFailError(f"L3 word_count={word_count} < 200")
    chapters_with_match = sum(
        1
        for paras in source_paragraphs_by_chapter.values()
        if any(p.strip() and p.strip() in body for p in paras)
    )
    if chapters_with_match < 2:
        raise IngestFailError(f"L3 source_paragraphs from {chapters_with_match} chapters < 2")
    for s in L3_FORBIDDEN_STRINGS:
        if s in body:
            raise IngestFailError(f"L3 forbidden string: {s!r}")
