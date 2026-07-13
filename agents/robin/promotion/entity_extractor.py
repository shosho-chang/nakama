"""Entity extractor Protocol + dry-run implementation (ADR-034 v2 PR4).

Caller-side step that produces :class:`EntityCandidate` list from a
Reading Source's content, ready to feed into
:class:`agents.robin.promotion.entity_promotion_engine.EntityPromotionEngine.propose`.

Entity extraction is fundamentally NER-shaped (proper nouns,
cross-lingual surface variants) and naturally LLM-backed — but the
promotion engine boundary forbids LLM imports. This Protocol lives in
the same boundary as ``ClaimExtractor`` (deterministic shape, LLM-backed
implementations live outside the ``agents.robin.promotion`` boundary).

``DryRunEntityExtractor`` ships in this module — deterministic
placeholder that returns an empty candidate list. Production wiring
uses it until a real NER extractor lands. Parallels
:class:`agents.robin.promotion.dry_run_extractor.DryRunClaimExtractor`.
"""

from __future__ import annotations

from typing import Protocol

from shared.schemas.entity_promotion import EntityCandidate
from shared.schemas.reading_source import ReadingSource
from shared.schemas.source_map import SourceMapBuildResult


class EntityExtractor(Protocol):
    """Extract entity candidates from a Reading Source's content.

    Implementations MAY be LLM-backed (NER over chapter text / podcast
    transcript) or deterministic (regex / dict-driven). Implementations
    MUST NOT mutate the caller-supplied inputs. Implementations may
    raise; the calling service catches documented exceptions and routes
    to a degraded state (parallel to source_map_builder's
    ``_EXTRACTOR_FAILURES`` tuple).

    Input shape:

    - ``reading_source``: the normalized #509 source identity. Carries
      ``source_id``, ``primary_lang``, ``kind`` (ebook / inbox_document
      / future podcast etc.) — implementations decide how much of this
      to consult.
    - ``source_map``: the #513 builder's result, where each item's
      ``evidence`` list carries the actual text excerpts to NER. Using
      the source_map (rather than reading content via a blob loader)
      keeps the extractor's I/O surface tiny — the SAME content the
      concept engine already extracted claims from.

    Output: ordered list of :class:`EntityCandidate`. Order is preserved
    by the engine; implementations should emit in a stable order
    (sort by candidate_id is fine).
    """

    def extract(
        self,
        reading_source: ReadingSource,
        source_map: SourceMapBuildResult,
    ) -> list[EntityCandidate]: ...


class DryRunEntityExtractor:
    """Deterministic dry-run ``EntityExtractor``.

    Returns an empty candidate list regardless of input. Used by
    production wiring until an LLM-backed extractor lands — keeps the
    review flow runnable end-to-end (manifest carries source +
    concept items, just no entity items yet).

    Stateless, no constructor arguments, no filesystem IO, no
    ``anthropic`` import. Same call always returns the same value.
    """

    def extract(
        self,
        reading_source: ReadingSource,
        source_map: SourceMapBuildResult,
    ) -> list[EntityCandidate]:
        return []
