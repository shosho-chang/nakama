"""Video speaker Entity extractor (ADR-035 PR4).

Deterministic ``EntityExtractor`` for ``youtube_video`` Reading Sources.
Reads the per-annotation ``speaker`` field (the cast chip selected at
save time, ADR-035 PR2b) and emits one ``EntityCandidate(person)`` per
distinct non-empty speaker. No LLM call — the user already labelled
who is speaking when they wrote the annotation; this extractor just
folds those labels into the Entity promotion flow.

Skipped for other Reading Source kinds (ebook / inbox_document) — they
have no speaker chip surface. ``DryRunEntityExtractor`` continues to
handle them with an empty candidate list until LLM-backed NER lands.

Pattern mirrors :mod:`shared.video_source_map_builder` — bypass-engine
slice that reads ADR-017 annotation store directly because user
annotations ARE the evidence for video.
"""

from __future__ import annotations

from collections import OrderedDict

from shared.annotation_store import AnnotationStore, get_annotation_store
from shared.log import get_logger
from shared.schemas.annotations import AnnotationSetV3
from shared.schemas.entity_promotion import EntityCandidate
from shared.schemas.promotion_manifest import PersonMetadata
from shared.schemas.reading_source import ReadingSource
from shared.schemas.source_map import SourceMapBuildResult
from shared.video_source_map_builder import _parse_t_locator_pair

_logger = get_logger("nakama.shared.video_speaker_entity_extractor")


_MAX_RAW_QUOTES_PER_CANDIDATE = 3
"""Cap on ``raw_quotes`` per emitted candidate — mirrors the schema
field doc on :class:`EntityCandidate` (\"caller controls cap\"). The
engine downstream reads only ``raw_quotes[0]`` for the evidence
excerpt, so the cap is a defensive ceiling, not a load-bearing limit."""


class VideoSpeakerEntityExtractor:
    """Deterministic ``EntityExtractor`` for ``youtube_video`` sources.

    Construction takes an optional ``annotation_store`` — defaults to
    the module-level singleton (``get_annotation_store()``). Tests
    inject in-memory fakes via the constructor; production wiring uses
    the default singleton.

    Contract:

    - For ``reading_source.kind != "youtube_video"`` returns ``[]``
      (the other kinds use :class:`DryRunEntityExtractor` until LLM-
      backed NER lands).
    - Loads the annotation set via
      ``annotation_store.load(reading_source.annotation_key)``. Missing
      store entry → ``[]``. Non-v3 sets are ignored (logged) — video
      saves always write v3 per ADR-035 PR2b.
    - Groups annotations by ``speaker`` field (case-sensitive,
      whitespace-stripped). Empty speakers are dropped — only labelled
      cues become Person candidates.
    - Emits one :class:`EntityCandidate` per distinct speaker, with:
      - ``label`` = the speaker chip text
      - ``entity_type`` = ``"person"``
      - ``source_refs`` = list of ``t={start}`` timestamps for the
        annotations that carry this speaker (preserves recurrence
        signal for :class:`EntityPromotionEngine`)
      - ``raw_quotes`` = up to ``_MAX_RAW_QUOTES_PER_CANDIDATE`` cue
        excerpts (caller cap; engine reads ``raw_quotes[0]``)
      - ``evidence_language`` = ``reading_source.primary_lang``
      - ``metadata`` = empty :class:`PersonMetadata` (no
        affiliation/role/credentials — those land in a future LLM
        enrichment pass)
    - Order preserved by first appearance of each speaker (stable for
      replay).
    """

    def __init__(self, annotation_store: AnnotationStore | None = None) -> None:
        self._annotation_store = annotation_store or get_annotation_store()

    def extract(
        self,
        reading_source: ReadingSource,
        source_map: SourceMapBuildResult,
    ) -> list[EntityCandidate]:
        if reading_source.kind != "youtube_video":
            return []
        loaded = self._annotation_store.load(reading_source.annotation_key)
        if not isinstance(loaded, AnnotationSetV3):
            if loaded is not None:
                _logger.warning(
                    "video_speaker_entity_extractor ignoring non-v3 annotation set",
                    extra={
                        "category": "video_speaker_entity_extractor_non_v3",
                        "slug": getattr(loaded, "slug", None),
                    },
                )
            return []

        grouped: OrderedDict[str, list[object]] = OrderedDict()
        for item in loaded.items:
            speaker = (getattr(item, "speaker", "") or "").strip()
            if not speaker:
                continue
            grouped.setdefault(speaker, []).append(item)

        candidates: list[EntityCandidate] = []
        for idx, (speaker, items) in enumerate(grouped.items()):
            source_refs: list[str] = []
            raw_quotes: list[str] = []
            for ann in items:
                parsed = _parse_t_locator_pair(getattr(ann, "cfi", None))
                if parsed is not None:
                    start, _end = parsed
                    locator = _format_locator(start)
                    if locator not in source_refs:
                        source_refs.append(locator)
                if len(raw_quotes) < _MAX_RAW_QUOTES_PER_CANDIDATE:
                    excerpt = (getattr(ann, "text_excerpt", "") or "").strip()
                    if excerpt:
                        raw_quotes.append(excerpt)
            candidates.append(
                EntityCandidate(
                    candidate_id=f"speaker-{idx:02d}",
                    entity_type="person",
                    label=speaker,
                    aliases=[],
                    evidence_language=reading_source.primary_lang,
                    source_refs=source_refs,
                    raw_quotes=raw_quotes,
                    metadata=PersonMetadata(),
                )
            )
        return candidates


def _format_locator(start: float) -> str:
    """Format a single-point timestamp locator. Integer-valued floats
    render as ``t=15`` (not ``t=15.0``) for readability — mirrors
    :func:`shared.video_source_map_builder._format_locator`."""
    if start == int(start):
        return f"t={int(start)}"
    return f"t={start:g}"
