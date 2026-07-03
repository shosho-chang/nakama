"""Video source map builder (ADR-035 §D6 / PR3a-ii).

Produces a ``SourceMapBuildResult`` for a ``youtube_video`` Reading Source
by reading user annotations from the ADR-017 annotation store. User
annotations ARE the evidence — there is no claim extraction step. This
deliberately bypasses ``agents.robin.promotion.source_map_builder.SourceMapBuilder`` and
its injected ``ClaimExtractor`` Protocol, which is designed for
claim-density evaluation of prose chapters.

Output shape: one ``SourcePageReviewItem`` with ``chapter_ref="whole"``;
each annotation becomes one ``EvidenceAnchor`` with
``kind="timestamp_range"`` and ``locator=f"t={start}-{end}"`` (or
``f"t={start}"`` for a point anchor). Mirrors short-source layout in the
existing builder.

Result carries ``schema_version=2`` because timestamp_range anchors
require v=2 per ADR-035 §D5 (V13 invariant on PromotionManifest, B7 on
SourceMapBuildResult).
"""

from __future__ import annotations

import re

from shared.annotation_store import AnnotationSetAny, get_annotation_store
from shared.log import get_logger
from shared.schemas.annotations import AnnotationSetV3
from shared.schemas.promotion_manifest import (
    EvidenceAnchor,
    RiskFlag,
    SourcePageReviewItem,
)
from shared.schemas.reading_source import ReadingSource, SourceVariant
from shared.schemas.source_map import SourceMapBuildResult

_logger = get_logger("nakama.agents.robin.promotion.video_source_map_builder")


_DEFAULT_MAX_EXCERPT_CHARS = 800
"""Per-anchor excerpt cap (mirrors B3 in source_map_builder). Cue excerpts
are typically short — this cap is a defensive ceiling."""

_CUE_BUCKET_SCALE = 20
"""50ms bucket scale for cue-start dedup (mirrors robin.py ``annBucket``)."""

_T_LOCATOR_RE = re.compile(r"t=([0-9]+(?:\.[0-9]+)?)(?:-([0-9]+(?:\.[0-9]+)?))?")


def _parse_t_locator_pair(cfi: str | None) -> tuple[float, float | None] | None:
    """Extract ``(start, end?)`` seconds from an ADR-035 §D5 ``t=`` locator.

    Mirrors ``thousand_sunny.routers.robin._parse_t_locator`` shape but
    also returns the optional end. Returns ``None`` if ``cfi`` is missing
    or not in the timestamp-range shape.
    """
    if not cfi:
        return None
    m = _T_LOCATOR_RE.search(cfi)
    if not m:
        return None
    try:
        start = float(m.group(1))
    except (TypeError, ValueError):
        return None
    end_str = m.group(2)
    end: float | None = None
    if end_str is not None:
        try:
            end = float(end_str)
        except (TypeError, ValueError):
            end = None
    return start, end


def _slug_from_youtube_source_id(source_id: str) -> str:
    """Best-effort slug derivation for ``youtube:{video_id}`` source ids.

    Mirrors ``source_map_builder._slugify`` policy: the builder treats
    ``source_id`` as opaque transport per #509 N3; this helper only
    builds a filesystem-safe path hint that callers may override.
    """
    last = source_id.rsplit("/", 1)[-1]
    if ":" in last and "/" not in source_id:
        last = last.split(":", 1)[-1]
    slug = re.sub(r"[\s:]+", "-", last).strip("-")
    return slug or "video"


def _select_original_variant(reading_source: ReadingSource) -> SourceVariant | None:
    for v in reading_source.variants:
        if v.role == "original":
            return v
    return None


def _annotation_excerpt(item) -> str:
    """Return the surface text that anchors this annotation in the
    transcript. For Highlight/Annotation v3 items this is ``text_excerpt``
    (the cue text snapped at save time); for Reflection items (chapter-
    level) we fall back to the body since no cue excerpt exists.
    """
    excerpt = (getattr(item, "text_excerpt", "") or "").strip()
    if excerpt:
        return excerpt
    if item.type == "reflection":
        return (getattr(item, "body", "") or "").strip()
    return ""


def _format_locator(start: float, end: float | None) -> str:
    """Format a timestamp_range locator per ADR-035 §D5.

    ``t=<start>-<end>`` when end is known and distinct from start;
    ``t=<start>`` for a single point. Floats are formatted via the
    standard repr to preserve precision (``3.14`` not ``3.140000``);
    integer-valued floats render as ``3`` not ``3.0`` for readability.
    """

    def fmt(x: float) -> str:
        if x == int(x):
            return str(int(x))
        return "%g" % x

    if end is None or end <= start:
        return f"t={fmt(start)}"
    return f"t={fmt(start)}-{fmt(end)}"


def build_video_source_map(
    reading_source: ReadingSource,
    annotation_set: AnnotationSetV3 | None = None,
    *,
    max_excerpt_chars: int = _DEFAULT_MAX_EXCERPT_CHARS,
) -> SourceMapBuildResult:
    """Build a source map for a ``youtube_video`` Reading Source from user
    annotations.

    Caller contract:

    - ``reading_source.kind`` MUST be ``"youtube_video"``; ``ValueError``
      otherwise (mirrors B1-style entry gate).
    - ``reading_source.has_evidence_track`` MUST be ``True``; ``ValueError``
      otherwise (matches the existing builder contract — annotation-only
      sync routes through a separate path).
    - ``annotation_set`` is optional; when ``None`` we load via
      ``get_annotation_store().load(reading_source.annotation_key)``.

    Returns a ``SourceMapBuildResult`` with ``schema_version=2`` (required
    by B7 for timestamp_range anchors). On zero usable annotations the
    item is emitted with ``recommendation="defer"`` and a
    ``low_signal_count`` risk; never raises for empty annotations.
    """
    if reading_source.kind != "youtube_video":
        raise ValueError(
            f"build_video_source_map requires kind='youtube_video'; "
            f"got kind={reading_source.kind!r}"
        )
    if not reading_source.has_evidence_track:
        raise ValueError(
            f"build_video_source_map requires has_evidence_track=True; "
            f"got has_evidence_track=False (source_id="
            f"{reading_source.source_id!r}, evidence_reason="
            f"{reading_source.evidence_reason!r}). Caller seeking "
            f"annotation-only sync must route via the Reading Overlay path."
        )

    variant = _select_original_variant(reading_source)
    if variant is None:
        return SourceMapBuildResult(
            schema_version=2,
            source_id=reading_source.source_id,
            primary_lang=reading_source.primary_lang,
            has_evidence_track=reading_source.has_evidence_track,
            chapters_inspected=0,
            items=[],
            risks=[],
            error=(
                f"variant_selection_failed: has_evidence_track=True but no "
                f"role='original' variant present (source_id="
                f"{reading_source.source_id!r})"
            ),
        )

    if annotation_set is None:
        loaded = get_annotation_store().load(reading_source.annotation_key)
        annotation_set = _coerce_to_v3(loaded)

    slug = _slug_from_youtube_source_id(reading_source.source_id)
    item_id = f"{slug}::whole"
    target_kb_path = f"KB/Wiki/Sources/{slug}/whole.md"

    evidence, dropped = _annotations_to_evidence(
        annotation_set,
        source_path=variant.path,
        max_excerpt_chars=max_excerpt_chars,
    )

    if dropped:
        _logger.info(
            "video_source_map dropped annotations without timestamp locators",
            extra={
                "category": "video_source_map_dropped_annotations",
                "source_id": reading_source.source_id,
                "dropped_count": dropped,
            },
        )

    risks: list[RiskFlag] = []
    if not evidence:
        risks.append(
            RiskFlag(
                code="low_signal_count",
                severity="medium",
                description=(
                    "No usable timestamp-anchored annotations on this video; "
                    "promotion will defer until the user marks at least one cue."
                ),
            )
        )

    recommendation = "include" if evidence else "defer"
    # User-curated annotations carry definitive intent; confidence is
    # maxed when at least one anchor survived. Empty → mid confidence so
    # callers can still surface the defer reason without false certainty.
    confidence = 1.0 if evidence else 0.5
    reason = _synthesize_reason(reading_source.title, len(evidence))

    item = SourcePageReviewItem(
        item_id=item_id,
        recommendation=recommendation,
        action="create",
        reason=reason,
        evidence=evidence,
        risk=risks,
        confidence=confidence,
        source_importance=0.5,
        reader_salience=0.0,
        target_kb_path=target_kb_path,
        chapter_ref="whole",
    )

    return SourceMapBuildResult(
        schema_version=2,
        source_id=reading_source.source_id,
        primary_lang=reading_source.primary_lang,
        has_evidence_track=reading_source.has_evidence_track,
        chapters_inspected=1,
        items=[item],
        risks=[],
        error=None,
    )


def _coerce_to_v3(loaded: AnnotationSetAny | None) -> AnnotationSetV3 | None:
    """Return ``loaded`` only if it is an AnnotationSetV3; otherwise None.

    PR3a-ii only consumes v3 sets — Reader writes v3 for video saves
    (ADR-035 PR2b). Pre-v3 legacy sets pre-date the youtube_video kind
    and therefore cannot exist for a video source in practice; we treat
    them as absent rather than coercing through best-effort upcasting.
    """
    if loaded is None:
        return None
    if isinstance(loaded, AnnotationSetV3):
        return loaded
    _logger.warning(
        "video_source_map ignoring non-v3 annotation set",
        extra={
            "category": "video_source_map_non_v3_annotation_set",
            "slug": getattr(loaded, "slug", None),
        },
    )
    return None


def _annotations_to_evidence(
    annotation_set: AnnotationSetV3 | None,
    *,
    source_path: str,
    max_excerpt_chars: int,
) -> tuple[list[EvidenceAnchor], int]:
    """Convert annotations to EvidenceAnchor list.

    Items without a parseable ``t=`` locator are dropped (counted in the
    returned ``dropped`` total). Items sharing a 50ms cue bucket are
    de-duplicated keeping the first occurrence — defensive against
    legacy multi-mark-per-cue data; PR2b enforces upsert at write time
    so duplicates should not appear on new saves.

    Output order: ascending by cue start so the rendered review page
    reads top-to-bottom in playback order.
    """
    if annotation_set is None or not annotation_set.items:
        return [], 0

    seen_buckets: set[int] = set()
    rows: list[tuple[float, EvidenceAnchor]] = []
    dropped = 0
    for item in annotation_set.items:
        cfi = getattr(item, "cfi", None)
        parsed = _parse_t_locator_pair(cfi)
        if parsed is None:
            dropped += 1
            continue
        start, end = parsed
        bucket = round(start * _CUE_BUCKET_SCALE)
        if bucket in seen_buckets:
            continue
        seen_buckets.add(bucket)

        excerpt = _annotation_excerpt(item)
        if len(excerpt) > max_excerpt_chars:
            excerpt = excerpt[: max_excerpt_chars - 1] + "…"

        rows.append(
            (
                start,
                EvidenceAnchor(
                    kind="timestamp_range",
                    source_path=source_path,
                    locator=_format_locator(start, end),
                    excerpt=excerpt,
                    confidence=1.0,
                ),
            )
        )

    rows.sort(key=lambda r: r[0])
    return [anchor for _, anchor in rows], dropped


def _synthesize_reason(title: str, anchor_count: int) -> str:
    """Build the ``SourcePageReviewItem.reason`` string for video items.

    Format mirrors the existing builder's ``_synthesize_reason`` shape:
    ``"{title}: N annotation(s)"`` (or ``low signal`` when 0).
    """
    head = (title or "Untitled").strip() or "Untitled"
    body = (
        f"{anchor_count} annotation{'' if anchor_count == 1 else 's'}"
        if anchor_count
        else "low signal"
    )
    return f"{head}: {body}"
