"""Source Map Builder service (ADR-024 Slice 5 / issue #513).

Deterministic claim-dense source map builder for one normalized Reading
Source (#509). Produces an ordered list of ``SourcePageReviewItem``
candidates per the #512 contract, ready to be wrapped into a
``PromotionManifest`` by downstream slices (#515).

No LLM call inside this module — claim/figure/table extraction is delegated
to an injected ``ClaimExtractor`` Protocol. Slice 5 ships ONLY the protocol
+ a deterministic fixture extractor in tests; LLM-backed implementations
live outside this slice (e.g. a future ``agents/robin/source_map_extractor``).

Builder reads variant bytes via an injected ``blob_loader: Callable[[str], bytes]``
— same N3 contract as #511 ``PromotionPreflight``. Builder NEVER imports
``shared.book_storage`` and NEVER parses ``ReadingSource.source_id`` (per
Brief §6 boundary 2 + 3). Failure paths catch documented exceptions via
narrow tuples (per #511 F5 lesson).

Hard invariants enforced (Brief §4.3):

- B1 ``has_evidence_track=False`` ⇒ ``ValueError`` at ``build()`` entry.
- B2 Every ``recommendation="include"`` item has ≥1 ``EvidenceAnchor`` (inherited
     from #512 ``SourcePageReviewItem`` V1 invariant).
- B3 ``EvidenceAnchor.excerpt`` length ≤ ``max_excerpt_chars``.
- B4 Sum of all emitted excerpt chars ≤ 30% of inspected chapter chars.
- B5 ``chapter_ref`` unique within ``items``.
- B6 Extractor exception (narrow tuple) → ``items=[]`` + ``error=...``.
- B7 Builder NEVER imports ``shared.book_storage`` (T11 subprocess gate).
- B8 Builder NEVER imports LLM clients / ``fastapi`` / ``thousand_sunny.*`` /
     ``agents.*`` (T12 subprocess gate).
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from pathlib import PurePosixPath
from typing import Protocol

from shared.epub_metadata import MalformedEPUBError, extract_metadata
from shared.log import get_logger
from shared.schemas.promotion_manifest import (
    EvidenceAnchor,
    RiskFlag,
    SourcePageReviewItem,
)
from shared.schemas.reading_source import ReadingSource, SourceVariant
from shared.schemas.source_map import (
    ChapterCandidate,
    ClaimExtractionResult,
    QuoteAnchor,
    SourceMapBuildResult,
)

_logger = get_logger("nakama.shared.source_map_builder")


BlobLoader = Callable[[str], bytes]
"""Maps a ``SourceVariant.path`` string to raw file bytes.

Production callers (consumer slices) inject a loader that resolves vault_root
+ path and reads from disk. Tests inject an in-memory dict-backed loader.
The builder NEVER imports ``shared.book_storage`` / vault helpers — path
resolution is the loader's job.
"""


class ClaimExtractor(Protocol):
    """Pure protocol — implementations may be LLM-backed or deterministic.

    Slice 5 ships ONLY the protocol + a deterministic fixture extractor for
    tests. LLM-backed implementation is the caller's responsibility (lives
    outside this slice, e.g. a future ``agents/robin/source_map_extractor.py``).

    Implementations MUST be callable per-chapter and MUST NOT mutate the
    caller-supplied chapter text. Implementations may raise; the builder
    catches ``_EXTRACTOR_FAILURES`` (narrow tuple) and routes to error state.
    """

    def extract(
        self,
        chapter_text: str,
        chapter_title: str,
        primary_lang: str,
    ) -> ClaimExtractionResult: ...


# ── Policy thresholds (Brief §4.2) ───────────────────────────────────────────

_DEFAULT_MAX_EXCERPT_CHARS = 800
_DEFAULT_MAX_REASON_CHARS = 200
_DEFAULT_MIN_CHAPTER_CHARS = 1500

_LONG_SOURCE_TOTAL_MULTIPLIER = 3
"""``total_chars >= min_chapter_chars * 3`` ⇒ long-source layout (per-chapter
items + ``index`` overview). Below ⇒ short-source layout (single ``whole``)."""

_LOW_SIGNAL_THRESHOLD = 5
"""Below ``len(claims) < _LOW_SIGNAL_THRESHOLD`` ⇒ emit ``low_signal_count``
risk flag on the per-item ``risk`` list (Brief §4.2 step 5)."""

_EXCERPT_BUDGET_FRACTION = 0.30
"""Sum of all emitted excerpt chars across all items ≤ 30% of total chapter
chars (Brief §4.3 B4 / claim-dense not mirror)."""

# EPUB OCF / OPF namespaces (mirror shared/promotion_preflight.py constants).
_NS_CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
_NS_OPF = "http://www.idpf.org/2007/opf"

# Strip XML/HTML tags so chapter text is plain prose for extractor input.
_XML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE_RUN = re.compile(r"\s+")

# Markdown ATX heading: 1-6 ``#`` then space then content. We split on H1+H2.
_MD_H1_OR_H2 = re.compile(r"^(#{1,2}) (\S.*)$")


# Documented failure modes for the boundary calls — narrow tuples so
# programmer errors (TypeError, AttributeError, KeyboardInterrupt) propagate.
_LOADER_FAILURES = (OSError, FileNotFoundError, KeyError, ValueError)
"""Loader can raise IO-style errors or KeyError when keyed by missing path.
ValueError covers test loaders that explicitly reject paths."""

_EPUB_BODY_FAILURES = (
    zipfile.BadZipFile,
    ET.ParseError,
    KeyError,
    OSError,
    UnicodeDecodeError,
)
"""EPUB body extraction touches zip + XML + UTF-8; these are the
expected failure modes."""

_EXTRACTOR_FAILURES = (ValueError, RuntimeError, OSError)
"""Documented extractor failures. Other exceptions (TypeError,
AttributeError, KeyboardInterrupt) propagate — they signal programmer bugs,
not data-quality issues."""


class _BuildError(Exception):
    """Internal sentinel for build-time failures we want to surface as a
    populated ``SourceMapBuildResult.error`` rather than crash."""


class SourceMapBuilder:
    """Deterministic claim-dense source map builder.

    One ``build(reading_source, extractor)`` per source; no caching, no
    enumeration. Caller is responsible for preflight gating: builder MUST
    only be invoked when ``PreflightReport.recommended_action`` ∈
    {``proceed_full_promotion``, ``proceed_with_warnings``} per Brief §3.

    Construction takes a ``blob_loader`` (required). Builder NEVER imports
    ``shared.book_storage``; T11 subprocess gate enforces that invariant.
    """

    def __init__(self, blob_loader: BlobLoader) -> None:
        self._blob_loader = blob_loader

    # ── Public API ──────────────────────────────────────────────────────────

    def build(
        self,
        reading_source: ReadingSource,
        extractor: ClaimExtractor,
        *,
        max_excerpt_chars: int = _DEFAULT_MAX_EXCERPT_CHARS,
        max_reason_chars: int = _DEFAULT_MAX_REASON_CHARS,
        min_chapter_chars: int = _DEFAULT_MIN_CHAPTER_CHARS,
    ) -> SourceMapBuildResult:
        """Build a claim-dense source map for ``reading_source``.

        Raises ``ValueError`` if ``reading_source.has_evidence_track is False``
        (B1 / Brief §4.3) — caller seeking annotation-only sync must use a
        different path. Source Map Builder is for evidence-backed promotion only.

        Returns a frozen ``SourceMapBuildResult``. On builder failure (extractor
        exception, blob unreadable, malformed epub) returns a result with
        ``items=[]`` and ``error=...``; does NOT raise.
        """
        if not reading_source.has_evidence_track:
            # B1 — caller contract violation. Caller seeking annotation-only
            # sync must route via #510 overlay path, not Source Map Builder.
            raise ValueError(
                f"SourceMapBuilder.build requires has_evidence_track=True; "
                f"got has_evidence_track=False (source_id="
                f"{reading_source.source_id!r}, evidence_reason="
                f"{reading_source.evidence_reason!r}). Caller must route to "
                f"annotation_only_sync via the Reading Overlay path."
            )

        # Variant selection: per #509 invariant, has_evidence_track=True ⇒
        # exactly one variant has role='original'. Mirrors #511 selection.
        variant = self._select_variant(reading_source)
        if variant is None:
            _logger.warning(
                "source_map variant selection failed",
                extra={
                    "category": "source_map_variant_selection_failed",
                    "source_id": reading_source.source_id,
                },
            )
            return SourceMapBuildResult(
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

        try:
            chapters, build_risks = self._inspect(reading_source, variant)
        except _BuildError as exc:
            return SourceMapBuildResult(
                source_id=reading_source.source_id,
                primary_lang=reading_source.primary_lang,
                has_evidence_track=reading_source.has_evidence_track,
                chapters_inspected=0,
                items=[],
                risks=[],
                error=str(exc),
            )

        # Extract per chapter. Catch documented extractor failures via narrow
        # tuple; route to error state. Programmer errors propagate.
        try:
            extractions = [
                extractor.extract(c.chapter_text, c.chapter_title, reading_source.primary_lang)
                for c in chapters
            ]
        except _EXTRACTOR_FAILURES as exc:
            _logger.warning(
                "source_map extractor failed",
                extra={
                    "category": "source_map_extractor_failed",
                    "source_id": reading_source.source_id,
                },
            )
            return SourceMapBuildResult(
                source_id=reading_source.source_id,
                primary_lang=reading_source.primary_lang,
                has_evidence_track=reading_source.has_evidence_track,
                chapters_inspected=len(chapters),
                items=[],
                risks=build_risks,
                error=f"extractor_failed: {type(exc).__name__}: {exc!s}",
            )

        # Layout decision: total_chars vs min_chapter_chars * multiplier.
        total_chars = sum(c.char_count for c in chapters)
        is_long = total_chars >= min_chapter_chars * _LONG_SOURCE_TOTAL_MULTIPLIER

        # Compute excerpt budget (B4). Per-item allocation: split the budget
        # by chapter share of total chars so big chapters get proportional
        # excerpt headroom. Each individual excerpt is also capped at
        # max_excerpt_chars (B3); the budget enforces the global 30% ceiling.
        excerpt_budget_total = int(total_chars * _EXCERPT_BUDGET_FRACTION)

        items = self._assemble_items(
            reading_source=reading_source,
            variant=variant,
            chapters=chapters,
            extractions=extractions,
            is_long=is_long,
            max_excerpt_chars=max_excerpt_chars,
            max_reason_chars=max_reason_chars,
            excerpt_budget_total=excerpt_budget_total,
        )

        return SourceMapBuildResult(
            source_id=reading_source.source_id,
            primary_lang=reading_source.primary_lang,
            has_evidence_track=reading_source.has_evidence_track,
            chapters_inspected=len(chapters),
            items=items,
            risks=build_risks,
            error=None,
        )

    # ── Variant selection ───────────────────────────────────────────────────

    @staticmethod
    def _select_variant(reading_source: ReadingSource) -> SourceVariant | None:
        """Pick the ``role='original'`` variant. ``has_evidence_track=True``
        guarantees exactly one such variant per #509 invariant. Returns
        ``None`` defensively if the documented invariant is violated."""
        for v in reading_source.variants:
            if v.role == "original":
                return v
        return None

    # ── Inspection ──────────────────────────────────────────────────────────

    def _inspect(
        self,
        reading_source: ReadingSource,
        variant: SourceVariant,
    ) -> tuple[list[ChapterCandidate], list[RiskFlag]]:
        """Load + chunk ``variant`` into ``ChapterCandidate`` list. Returns
        ``(chapters, build_risks)``. Raises ``_BuildError`` (caught above) on
        documented IO/parse failure.
        """
        if reading_source.kind == "ebook":
            return self._inspect_ebook(variant)
        return self._inspect_inbox(variant)

    def _inspect_ebook(
        self, variant: SourceVariant
    ) -> tuple[list[ChapterCandidate], list[RiskFlag]]:
        """Inspect an EPUB variant into per-spine-item chapter candidates.

        Pure stdlib (``zipfile`` + ``xml.etree`` + regex). Failures collapse to
        ``_BuildError`` (caller routes to error state).
        """
        try:
            blob = self._blob_loader(variant.path)
        except _LOADER_FAILURES as exc:
            _logger.warning(
                "source_map ebook blob load failed",
                extra={"category": "source_map_ebook_load_failed", "path": variant.path},
            )
            raise _BuildError(f"blob_load_failed: {exc!s}") from exc

        try:
            metadata = extract_metadata(blob)
        except MalformedEPUBError as exc:
            _logger.warning(
                "source_map ebook metadata parse failed",
                extra={"category": "source_map_ebook_parse_failed", "path": variant.path},
            )
            raise _BuildError(f"epub_parse_failed: {exc!s}") from exc

        try:
            spine_items = _extract_epub_spine_items(blob)
        except _EPUB_BODY_FAILURES as exc:
            _logger.warning(
                "source_map ebook body extract failed",
                extra={"category": "source_map_ebook_body_failed", "path": variant.path},
            )
            raise _BuildError(f"epub_body_failed: {exc!s}") from exc

        # Build a TOC lookup so chapter titles come from nav when possible.
        # ``metadata.toc`` is a flat-or-nested list of TocEntry; we want the
        # title keyed by spine href stem. Fallback: chapter title from XHTML
        # ``<title>`` or ``<h1>``; final fallback: ``Chapter {i}``.
        toc_titles = _build_toc_title_map(metadata.toc)

        candidates: list[ChapterCandidate] = []
        for idx, (href, raw_xhtml) in enumerate(spine_items, start=1):
            text = _strip_xml(raw_xhtml)
            href_stem = PurePosixPath(href).name
            title = toc_titles.get(href_stem) or _extract_xhtml_title(raw_xhtml) or f"Chapter {idx}"
            candidates.append(
                ChapterCandidate(
                    chapter_ref=f"ch-{idx}",
                    chapter_title=title,
                    chapter_text=text,
                    char_count=len(text),
                    word_count=_word_count(text),
                )
            )

        build_risks: list[RiskFlag] = []
        if len(metadata.toc) == 0:
            build_risks.append(
                RiskFlag(
                    code="weak_toc",
                    severity="medium",
                    description=(
                        "No chapters detected in EPUB nav (builder fell back to spine indexing)"
                    ),
                )
            )

        return candidates, build_risks

    def _inspect_inbox(
        self, variant: SourceVariant
    ) -> tuple[list[ChapterCandidate], list[RiskFlag]]:
        """Inspect a markdown variant into one-or-many chapter candidates by
        H1/H2 headings. No headings → single ``whole`` candidate.

        Strict frontmatter parse via ``_strict_split_frontmatter`` per #511 F6
        lesson — ``shared.utils.extract_frontmatter`` swallows YAMLError
        silently. Builder either uses no frontmatter at all or parses
        strictly; here we strip the fence without parsing keys (we only need
        the body for chunking — title comes from heading text or stem).
        """
        try:
            blob = self._blob_loader(variant.path)
        except _LOADER_FAILURES as exc:
            _logger.warning(
                "source_map inbox blob load failed",
                extra={"category": "source_map_inbox_load_failed", "path": variant.path},
            )
            raise _BuildError(f"blob_load_failed: {exc!s}") from exc

        try:
            content = blob.decode("utf-8")
        except UnicodeDecodeError as exc:
            _logger.warning(
                "source_map inbox decode failed",
                extra={"category": "source_map_inbox_decode_failed", "path": variant.path},
            )
            raise _BuildError(f"inbox_decode_failed: {exc!s}") from exc

        body = _strict_split_frontmatter(content)

        sections = _split_markdown_by_headings(body)
        build_risks: list[RiskFlag] = []
        candidates: list[ChapterCandidate] = []

        if len(sections) == 0:
            # No H1/H2 — single ``whole`` candidate. Title from path stem.
            stem = PurePosixPath(variant.path).stem
            candidates.append(
                ChapterCandidate(
                    chapter_ref="whole",
                    chapter_title=stem or "Document",
                    chapter_text=body.strip(),
                    char_count=len(body.strip()),
                    word_count=_word_count(body),
                )
            )
            build_risks.append(
                RiskFlag(
                    code="weak_toc",
                    severity="low",
                    description=(
                        "No markdown H1/H2 headings detected; "
                        "builder emitted whole-document candidate"
                    ),
                )
            )
        else:
            for idx, (title, section_text) in enumerate(sections, start=1):
                candidates.append(
                    ChapterCandidate(
                        chapter_ref=f"sec-{idx}",
                        chapter_title=title,
                        chapter_text=section_text,
                        char_count=len(section_text),
                        word_count=_word_count(section_text),
                    )
                )

        return candidates, build_risks

    # ── Item assembly ───────────────────────────────────────────────────────

    def _assemble_items(
        self,
        *,
        reading_source: ReadingSource,
        variant: SourceVariant,
        chapters: list[ChapterCandidate],
        extractions: list[ClaimExtractionResult],
        is_long: bool,
        max_excerpt_chars: int,
        max_reason_chars: int,
        excerpt_budget_total: int,
    ) -> list[SourcePageReviewItem]:
        """Compose ``SourcePageReviewItem`` list from chapters + extractions.

        Layout:
        - Long source ⇒ ``index`` overview (no evidence; recommendation=defer)
          + per-chapter items (evidence from quotes if present, else defer).
        - Short source ⇒ single consolidated ``whole`` item (concatenates
          extraction signals across all candidates).

        Budget split: each chapter gets a share of ``excerpt_budget_total``
        proportional to its ``char_count``. Chapter excerpt sum is bounded
        by the share AND by ``max_excerpt_chars`` per individual excerpt.
        """
        slug = _slugify(reading_source.source_id)

        if not is_long:
            # Short-source layout: single consolidated item. Aggregate signals
            # across all extractions (typically 1 in this layout, but inbox
            # could split into 2-3 short sections that still fall below the
            # long-source threshold).
            return self._build_short_items(
                reading_source=reading_source,
                variant=variant,
                chapters=chapters,
                extractions=extractions,
                slug=slug,
                max_excerpt_chars=max_excerpt_chars,
                max_reason_chars=max_reason_chars,
                excerpt_budget_total=excerpt_budget_total,
            )

        return self._build_long_items(
            reading_source=reading_source,
            variant=variant,
            chapters=chapters,
            extractions=extractions,
            slug=slug,
            max_excerpt_chars=max_excerpt_chars,
            max_reason_chars=max_reason_chars,
            excerpt_budget_total=excerpt_budget_total,
        )

    def _build_short_items(
        self,
        *,
        reading_source: ReadingSource,
        variant: SourceVariant,
        chapters: list[ChapterCandidate],
        extractions: list[ClaimExtractionResult],
        slug: str,
        max_excerpt_chars: int,
        max_reason_chars: int,
        excerpt_budget_total: int,
    ) -> list[SourcePageReviewItem]:
        """Single-page ``whole`` consolidated item path."""
        all_claims: list[str] = []
        all_quotes: list[QuoteAnchor] = []
        confidences: list[float] = []
        for ext in extractions:
            all_claims.extend(ext.claims)
            all_quotes.extend(ext.short_quotes)
            confidences.append(ext.extraction_confidence)

        title = chapters[0].chapter_title if chapters else "Document"
        evidence = _quotes_to_evidence(
            all_quotes,
            source_path=variant.path,
            max_excerpt_chars=max_excerpt_chars,
            budget_total=excerpt_budget_total,
        )

        recommendation = "include" if len(evidence) >= 1 else "defer"
        reason = _synthesize_reason(claims=all_claims, chapter_title=title, cap=max_reason_chars)
        confidence = (sum(confidences) / len(confidences)) if confidences else 0.0
        risks = _per_item_risks(claim_count=len(all_claims))

        item = SourcePageReviewItem(
            item_id=f"{slug}::whole",
            recommendation=recommendation,
            action="create",
            reason=reason,
            evidence=evidence,
            risk=risks,
            confidence=confidence,
            source_importance=0.5,
            reader_salience=0.0,
            target_kb_path=f"KB/Wiki/Sources/{slug}/whole.md",
            chapter_ref="whole",
        )
        return [item]

    def _build_long_items(
        self,
        *,
        reading_source: ReadingSource,
        variant: SourceVariant,
        chapters: list[ChapterCandidate],
        extractions: list[ClaimExtractionResult],
        slug: str,
        max_excerpt_chars: int,
        max_reason_chars: int,
        excerpt_budget_total: int,
    ) -> list[SourcePageReviewItem]:
        """Long-source layout: ``index`` overview first, then per chapter."""
        total_chars = sum(c.char_count for c in chapters) or 1

        # Index overview: no evidence (book-level summary, not a chapter
        # quote). recommendation='defer' since #512 V1 requires evidence
        # for include. Caller / #515 may upgrade to a custom action.
        all_claims: list[str] = []
        for ext in extractions:
            all_claims.extend(ext.claims)
        index_reason = _synthesize_reason(
            claims=all_claims,
            chapter_title=reading_source.title,
            cap=max_reason_chars,
        )
        index_item = SourcePageReviewItem(
            item_id=f"{slug}::index",
            recommendation="defer",
            action="create",
            reason=index_reason,
            evidence=[],
            risk=_per_item_risks(claim_count=len(all_claims)),
            confidence=0.5,
            source_importance=0.5,
            reader_salience=0.0,
            target_kb_path=f"KB/Wiki/Sources/{slug}/index.md",
            chapter_ref="index",
        )

        items: list[SourcePageReviewItem] = [index_item]
        for chapter, extraction in zip(chapters, extractions, strict=True):
            chapter_share = max(1, int(excerpt_budget_total * (chapter.char_count / total_chars)))
            evidence = _quotes_to_evidence(
                extraction.short_quotes,
                source_path=variant.path,
                max_excerpt_chars=max_excerpt_chars,
                budget_total=chapter_share,
            )
            recommendation = "include" if len(evidence) >= 1 else "defer"
            reason = _synthesize_reason(
                claims=extraction.claims,
                chapter_title=chapter.chapter_title,
                cap=max_reason_chars,
            )
            items.append(
                SourcePageReviewItem(
                    item_id=f"{slug}::{chapter.chapter_ref}",
                    recommendation=recommendation,
                    action="create",
                    reason=reason,
                    evidence=evidence,
                    risk=_per_item_risks(claim_count=len(extraction.claims)),
                    confidence=extraction.extraction_confidence,
                    source_importance=0.5,
                    reader_salience=0.0,
                    target_kb_path=f"KB/Wiki/Sources/{slug}/{chapter.chapter_ref}.md",
                    chapter_ref=chapter.chapter_ref,
                )
            )

        return items


# ── Module-level helpers ────────────────────────────────────────────────────


def _word_count(text: str) -> int:
    """Whitespace-split token count. Mirrors #511 contract — language-agnostic
    enough for rough size estimation; no tokenizer dependency."""
    if not text:
        return 0
    return len(_WHITESPACE_RUN.split(text.strip())) if text.strip() else 0


def _strict_split_frontmatter(content: str) -> str:
    """Strip a leading YAML frontmatter fence and return the body. Does NOT
    parse YAML keys — builder doesn't need them. Mirrors #511 F6 lesson:
    NEVER call ``shared.utils.extract_frontmatter`` (which swallows YAMLError
    silently); when full parsing IS needed, copy ``promotion_preflight._strict_extract_frontmatter``
    instead. Slice 5 only needs the body, so this thinner helper suffices.
    """
    if not content.startswith("---"):
        return content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return content
    return parts[2].lstrip("\n")


def _split_markdown_by_headings(body: str) -> list[tuple[str, str]]:
    """Split a markdown body on H1/H2 ATX headings. Returns ``[(title, section_text), ...]``.

    Each section's text includes everything from after the heading line up to
    (but not including) the next H1/H2 heading line. Pre-heading content is
    discarded — callers that hit no headings get an empty list and route to
    the ``whole`` candidate.
    """
    lines = body.splitlines()
    sections: list[tuple[str, str]] = []
    current_title: str | None = None
    current_buf: list[str] = []

    for line in lines:
        m = _MD_H1_OR_H2.match(line)
        if m is not None:
            if current_title is not None:
                sections.append((current_title, "\n".join(current_buf).strip()))
            current_title = m.group(2).strip()
            current_buf = []
        else:
            if current_title is not None:
                current_buf.append(line)

    if current_title is not None:
        sections.append((current_title, "\n".join(current_buf).strip()))

    return sections


def _strip_xml(xhtml: str) -> str:
    """Strip XML/HTML tags + collapse whitespace; cheap text extraction.
    Mirrors ``shared.promotion_preflight._strip_xml``."""
    no_tags = _XML_TAG.sub(" ", xhtml)
    return _WHITESPACE_RUN.sub(" ", no_tags).strip()


_XHTML_TITLE = re.compile(r"<title[^>]*>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL)
_XHTML_H1 = re.compile(r"<h1[^>]*>(?P<title>.*?)</h1>", re.IGNORECASE | re.DOTALL)


def _extract_xhtml_title(xhtml: str) -> str | None:
    """Pull a chapter title from XHTML — prefer ``<h1>`` body content (the
    visible chapter heading), fall back to ``<head><title>``. Returns None
    if neither is present."""
    h1_match = _XHTML_H1.search(xhtml)
    if h1_match is not None:
        title = _strip_xml(h1_match.group("title")).strip()
        if title:
            return title
    title_match = _XHTML_TITLE.search(xhtml)
    if title_match is not None:
        title = _strip_xml(title_match.group("title")).strip()
        if title:
            return title
    return None


def _build_toc_title_map(toc) -> dict[str, str]:
    """Flatten a (possibly nested) TocEntry list into ``{href_stem: title}``.

    href_stem is the file-name component of TOC ``href`` (TOC may carry
    fragments like ``ch1.xhtml#sec1``; we want ``ch1.xhtml``).
    """
    out: dict[str, str] = {}

    def walk(entries) -> None:
        for entry in entries:
            href = entry.href.split("#", 1)[0]
            stem = PurePosixPath(href).name
            if stem and entry.title and stem not in out:
                out[stem] = entry.title.strip()
            walk(entry.children)

    walk(toc)
    return out


def _slugify(source_id: str) -> str:
    """Derive a whitespace-safe slug from ``source_id``'s last path segment.

    The builder NEVER parses ``source_id`` for namespace prefix per #509 N3
    contract — this is "best-effort filename hint", not identity logic.
    Caller may override ``target_kb_path`` post-build. Slug rules:

    - take the last ``/`` segment (works for ``ebook:foo`` and ``inbox:Inbox/kb/foo.md``)
    - strip filename extension (``.md`` / ``.epub``)
    - strip namespace prefix (``ebook:``, ``inbox:``) only when it leads the
      remaining segment — pure cosmetic so paths read as ``Sources/foo`` not
      ``Sources/ebook:foo``.
    - replace whitespace + colons with hyphens; collapse runs.
    """
    last = source_id.rsplit("/", 1)[-1]
    # Drop a leading namespace prefix if it's still on this segment (single
    # source_id case like ``ebook:abc``).
    if ":" in last and "/" not in source_id:
        last = last.split(":", 1)[-1]
    # Trim filename extension.
    if "." in last:
        last = last.rsplit(".", 1)[0]
    # Whitespace + remaining colons → hyphen; collapse runs.
    slug = re.sub(r"[\s:]+", "-", last).strip("-")
    return slug or "source"


def _synthesize_reason(*, claims: list[str], chapter_title: str, cap: int) -> str:
    """Build the ``SourcePageReviewItem.reason`` string.

    Format: ``"{chapter_title}: {claims[0]}"`` truncated to ``cap`` chars.
    When claims is empty, returns ``"{chapter_title}: low signal"`` (still ≤ cap).
    """
    head = chapter_title.strip() or "Untitled"
    body = claims[0].strip() if claims else "low signal"
    text = f"{head}: {body}"
    if len(text) > cap:
        # Reserve 1 char for ``…`` ellipsis so callers see truncation.
        text = text[: max(0, cap - 1)] + "…"
    return text


def _per_item_risks(*, claim_count: int) -> list[RiskFlag]:
    """Build per-item ``RiskFlag`` list. Currently only emits
    ``low_signal_count`` when the extractor returned fewer than 5 claims (Brief
    §4.2 step 5)."""
    risks: list[RiskFlag] = []
    if claim_count < _LOW_SIGNAL_THRESHOLD:
        risks.append(
            RiskFlag(
                code="low_signal_count",
                severity="medium",
                description=f"Only {claim_count} claims extracted (<{_LOW_SIGNAL_THRESHOLD})",
            )
        )
    return risks


def _quotes_to_evidence(
    quotes: list[QuoteAnchor],
    *,
    source_path: str,
    max_excerpt_chars: int,
    budget_total: int,
) -> list[EvidenceAnchor]:
    """Convert ``QuoteAnchor`` list to ``EvidenceAnchor`` list, enforcing
    per-excerpt cap (B3) and per-chapter total budget (chapter share of B4).

    Budget enforcement: iterate quotes in order, truncate each to
    min(max_excerpt_chars, remaining_budget). Stop adding once the remaining
    budget hits zero. This means low-signal chapters with many short quotes
    may emit fewer evidence entries than ``len(quotes)``.
    """
    evidence: list[EvidenceAnchor] = []
    remaining = budget_total
    for q in quotes:
        if remaining <= 0:
            break
        cap = min(max_excerpt_chars, remaining)
        truncated = q.excerpt[:cap]
        if not truncated:
            continue
        evidence.append(
            EvidenceAnchor(
                kind="chapter_quote",
                source_path=source_path,
                locator=q.locator,
                excerpt=truncated,
                confidence=q.confidence,
            )
        )
        remaining -= len(truncated)
    return evidence


def _extract_epub_spine_items(blob: bytes) -> list[tuple[str, str]]:
    """Return ``[(href_in_zip, raw_xhtml_text), ...]`` in spine order.

    Pure stdlib (``zipfile`` + ``xml.etree``). Returns the *raw* XHTML so
    callers can lift ``<title>`` / ``<h1>`` cheaply; whitespace stripping is
    a downstream concern. Failures propagate; caller wraps them as
    ``epub_body_failed``.
    """
    out: list[tuple[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = set(zf.namelist())
        if "META-INF/container.xml" not in names:
            raise MalformedEPUBError("Missing META-INF/container.xml")
        opf_path = _find_opf_path(zf.read("META-INF/container.xml"))
        opf_root = ET.fromstring(zf.read(opf_path).decode("utf-8", errors="replace"))
        opf_dir = str(PurePosixPath(opf_path).parent)

        manifest_map = _build_manifest_map(opf_root, opf_dir)
        spine = opf_root.find(f"{{{_NS_OPF}}}spine")
        if spine is None:
            return []
        for itemref in spine:
            idref = itemref.get("idref")
            if not idref or idref not in manifest_map:
                continue
            href = manifest_map[idref]
            if href not in names:
                continue
            try:
                xhtml = zf.read(href).decode("utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                # Skip unreadable spine items. Narrow tuple — programmer
                # errors propagate (mirrors #511 F5 lesson).
                continue
            out.append((href, xhtml))
    return out


def _find_opf_path(container_xml: bytes) -> str:
    """Mirrors ``shared.promotion_preflight._find_opf_path``."""
    root = ET.fromstring(container_xml.decode("utf-8", errors="replace"))
    for rf in root.iter(f"{{{_NS_CONTAINER}}}rootfile"):
        path = rf.get("full-path")
        if path:
            return path
    raise MalformedEPUBError("No rootfile found in container.xml")


def _build_manifest_map(opf_root: ET.Element, opf_dir: str) -> dict[str, str]:
    """Map manifest item ``id`` → resolved spine-readable path."""
    mf = opf_root.find(f"{{{_NS_OPF}}}manifest")
    if mf is None:
        return {}
    out: dict[str, str] = {}
    for item in mf:
        item_id = item.get("id")
        href = item.get("href")
        if not item_id or not href:
            continue
        out[item_id] = f"{opf_dir}/{href}" if opf_dir else href
    return out
