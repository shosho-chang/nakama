"""Concept Promotion Engine service (ADR-024 Slice 6 / issue #514).

Deterministic concept promotion engine for one normalized Reading Source
(#509) plus its built source map (#513). Produces an ordered list of
``ConceptReviewItem`` candidates per the #512 contract, ready to be wrapped
into a ``PromotionManifest`` by downstream slices (#515).

No LLM call inside this module — concept matching is delegated to an
injected ``ConceptMatcher`` Protocol. Real KB index access is delegated
to an injected ``KBConceptIndex`` Protocol. Slice 6 ships ONLY the
protocols + a deterministic in-engine candidate extractor; LLM-backed
matcher implementations live outside this slice (e.g. a future
``agents/robin/concept_matcher.py``).

Engine NEVER imports ``shared.book_storage``, ``fastapi``,
``thousand_sunny.*``, ``agents.*``, or LLM clients (per Brief §6 boundary
checks). Engine NEVER parses ``ReadingSource.source_id`` (per #509 N3
contract). Failure paths catch documented exceptions via narrow tuples
(per #511 F5 lesson).

Hard invariants enforced (Brief §4.3):

- C1 Every emitted ``ConceptReviewItem`` is per #512 schema (V1 invariant inherited).
- C2 ``update_conflict_global`` items have ``recommendation="defer"``.
- C3 ``keep_source_local`` items have ``recommendation in {"include", "defer"}``.
- C4 ``create_global_concept`` items have ≥1 ``EvidenceAnchor`` AND
     ``confidence ≥ min_global_confidence``.
- C5 ``ConceptReviewItem.evidence_language`` derived from candidate.evidence_language.
- C6 Engine NEVER imports ``shared.book_storage`` / ``fastapi`` /
     ``thousand_sunny.*`` / ``agents.*`` / LLM clients.
- C7 On matcher exception (narrow tuple): set ``result.error``, return whatever
     items completed.

Engine action policy (Brief §4.2 — top-down first-match):

| Row | Condition                                                       | Action                  |
|-----|-----------------------------------------------------------------|-------------------------|
| 1   | recurrence < min_recurrence_for_global AND no high-conf match   | keep_source_local       |
| 2   | exact_alias AND conf ≥ 0.90 AND no conflict_signals             | update_merge_global     |
| 3   | exact_alias AND conflict_signals non-empty                      | update_conflict_global  |
| 4   | semantic/translation AND conf ≥ min_global_confidence AND clean | update_merge_global     |
| 5   | semantic/translation AND conf < min_global_confidence           | update_conflict_global  |
|     |                                                       (defer if conf ≥ 0.50) |
|     |                                                       OR keep_source_local   |
| 6   | match_basis="none" AND recurrence ≥ min AND raw_quotes ≥ 3      | create_global_concept   |
| 7   | match_basis="none" AND insufficient recurrence/evidence         | keep_source_local       |
| 8   | candidate label empty/blank                                     | exclude                 |
"""

from __future__ import annotations

import re
from typing import Protocol

from shared.log import get_logger
from shared.schemas.concept_promotion import (
    ConceptCandidate,
    ConceptPromotionResult,
    KBConceptEntry,
    MatchOutcome,
)
from shared.schemas.promotion_manifest import (
    CanonicalMatch,
    ConceptReviewItem,
    EvidenceAnchor,
    RiskFlag,
)
from shared.schemas.reading_source import ReadingSource
from shared.schemas.source_map import SourceMapBuildResult

_logger = get_logger("nakama.shared.concept_promotion_engine")


# ── Caller-supplied protocols ─────────────────────────────────────────────────


class ConceptMatcher(Protocol):
    """Cross-source / cross-lingual matcher protocol.

    Slice 6 ships ONLY the protocol + deterministic fixture matchers in tests.
    LLM-backed implementation is the caller's responsibility (lives outside
    this slice).

    Implementations MUST be callable per-candidate and MUST NOT mutate the
    caller-supplied candidate. Implementations may raise; the engine catches
    ``_MATCHER_FAILURES`` (narrow tuple) and routes to error state.
    """

    def match(
        self,
        candidate: ConceptCandidate,
        kb_index: "KBConceptIndex",
        primary_lang: str,
    ) -> MatchOutcome:
        """Return best canonical match (or ``"none"`` basis) + confidence.

        Implementations decide the matching algorithm (alias lookup,
        embedding similarity, translation matching, etc.). The engine
        threads the returned ``MatchOutcome`` directly into the action
        policy without re-interpretation.
        """
        ...


class KBConceptIndex(Protocol):
    """Read-only view of existing global KB concepts.

    Slice 6 ships ONLY the protocol + deterministic fixture indexes in tests.
    Real KB index reader is out of scope (lives in #515 / a downstream
    slice).
    """

    def lookup(self, alias: str) -> KBConceptEntry | None:
        """Return the KB concept entry whose ``aliases`` contains ``alias``
        (case-insensitive normalization is the implementation's choice).
        Returns ``None`` when no match."""
        ...

    def aliases_starting_with(self, prefix: str) -> list[str]:
        """Return aliases that start with ``prefix``. Used by future
        prefix-search matchers; deterministic fixture indexes may return
        empty lists."""
        ...


# ── Policy thresholds (Brief §4.2) ────────────────────────────────────────────

_DEFAULT_MIN_GLOBAL_CONFIDENCE = 0.75
_DEFAULT_MIN_RECURRENCE_FOR_GLOBAL = 2

_MIN_RAW_QUOTES_FOR_CREATE_GLOBAL = 3
"""Brief §4.2 row 6: ``create_global_concept`` requires recurrence threshold
AND ``len(raw_quotes) >= 3``."""

_HIGH_CONFIDENCE_EXACT_ALIAS_THRESHOLD = 0.90
"""Brief §4.2 row 2: exact_alias must clear 0.90 to auto-merge."""

_LOW_CONFIDENCE_DROP_THRESHOLD = 0.50
"""Brief §4.2 row 5b: cross-lingual match below 0.50 ⇒ ``keep_source_local``
(too low even for human conflict review queue)."""

_MAX_RAW_QUOTES_PER_CANDIDATE = 3
"""Brief §4.1: ``raw_quotes`` ≤ 3 short excerpts."""

_MAX_CANDIDATE_LABEL_CHARS = 80
"""Per-label cap on the deterministic-extracted label string. Long
excerpts are truncated to this length before label normalization."""

_MAX_REASON_CHARS = 200
"""Per Brief §4.2 / mirroring #513 builder: keep ``reason`` ≤ 200 chars."""


# Documented matcher failures — narrow tuple so programmer errors
# (TypeError, AttributeError, KeyboardInterrupt) propagate. ``KeyError``
# included because matchers commonly use dict lookups against the KB index.
_MATCHER_FAILURES = (ValueError, RuntimeError, OSError, KeyError)
"""Documented matcher exceptions caught by the engine and routed to
``ConceptPromotionResult.error``. Other exception types propagate."""


# Whitespace-stripping for label normalization. We keep label as-is for
# ``ConceptCandidate.label`` (preserve original case + lang form), but use
# a normalized form as the grouping key.
_WHITESPACE_RUN = re.compile(r"\s+")

# Strip ASCII + CJK punctuation noise at the head/tail of a label.
_LABEL_PUNCTUATION_CHARS = r"[\s　\.,;:!?\-—–'\"`(\[\{)\]\}]+"
_LABEL_PUNCTUATION = re.compile(rf"^{_LABEL_PUNCTUATION_CHARS}|{_LABEL_PUNCTUATION_CHARS}$")

# A simple sentence-end split — first sentence (or first 80 chars) becomes
# the label substring. Catches ASCII ``.``, ``!``, ``?`` and CJK fullwidths.
_SENTENCE_END = re.compile(r"[\.!?。！？]")


# ── Engine ────────────────────────────────────────────────────────────────────


class ConceptPromotionEngine:
    """Deterministic concept promotion engine.

    One ``propose(reading_source, source_map, kb_index, matcher)`` per source
    map; no caching, no enumeration. Caller is responsible for upstream
    gating: engine MUST only be invoked with a valid ``SourceMapBuildResult``
    (``error is None``, ``items`` non-empty) — the engine does NOT re-validate
    the source map's preconditions (B1 / preflight gating is the caller's
    job).

    Construction takes no arguments — engine is stateless. Caller injects
    matcher and kb_index per ``propose()`` call.
    """

    # ── Public API ──────────────────────────────────────────────────────────

    def propose(
        self,
        reading_source: ReadingSource,
        source_map: SourceMapBuildResult,
        kb_index: KBConceptIndex,
        matcher: ConceptMatcher,
        *,
        min_global_confidence: float = _DEFAULT_MIN_GLOBAL_CONFIDENCE,
        min_recurrence_for_global: int = _DEFAULT_MIN_RECURRENCE_FOR_GLOBAL,
    ) -> ConceptPromotionResult:
        """Propose concept promotion items for ``reading_source``.

        Extraction: deterministic walk over ``source_map.items`` →
        ``list[ConceptCandidate]``. Each candidate's evidence is the
        EvidenceAnchor list from the source map items where it appeared.

        Matching: each candidate is passed to ``matcher.match(candidate,
        kb_index, reading_source.primary_lang)``. The returned
        ``MatchOutcome`` drives the action policy (Brief §4.2).

        Failure: matcher exceptions in the documented tuple
        (``ValueError``, ``RuntimeError``, ``OSError``, ``KeyError``) are
        caught; engine returns a ``ConceptPromotionResult`` with ``items=[]``
        and ``error=...``. Programmer errors (``TypeError``,
        ``AttributeError``, ``KeyboardInterrupt``) propagate.

        Returns a frozen ``ConceptPromotionResult``.
        """
        # Extract candidates from source map items deterministically. This
        # step never raises — it operates on already-validated #512/#513
        # value-objects.
        candidates = self._extract_candidates(reading_source, source_map)
        candidates_extracted = len(candidates)

        # Match each candidate via the injected matcher. Catch documented
        # failures via narrow tuple; route to error state. Programmer errors
        # propagate. We try-catch around the WHOLE loop so any partial items
        # are dropped per C7 (engine returns items=[] on error).
        outcomes: list[MatchOutcome | None] = []
        try:
            for candidate in candidates:
                if not _is_label_blank(candidate.label):
                    outcome = matcher.match(candidate, kb_index, reading_source.primary_lang)
                    outcomes.append(outcome)
                else:
                    # Blank label routes to row 8 (exclude); no matcher call needed.
                    outcomes.append(None)
        except _MATCHER_FAILURES as exc:
            _logger.warning(
                "concept matcher failed",
                extra={
                    "category": "concept_matcher_failed",
                    "source_id": reading_source.source_id,
                },
            )
            return ConceptPromotionResult(
                source_id=reading_source.source_id,
                primary_lang=reading_source.primary_lang,
                candidates_extracted=candidates_extracted,
                items=[],
                risks=[],
                error=f"matcher_failed: {type(exc).__name__}: {exc!s}",
            )

        # Apply action policy per candidate + outcome. Each candidate yields
        # exactly one ``ConceptReviewItem`` (no fan-out).
        items: list[ConceptReviewItem] = []
        engine_risks: list[RiskFlag] = []
        for candidate, outcome in zip(candidates, outcomes, strict=True):
            item = self._apply_policy(
                candidate=candidate,
                outcome=outcome,
                primary_lang=reading_source.primary_lang,
                min_global_confidence=min_global_confidence,
                min_recurrence_for_global=min_recurrence_for_global,
            )
            items.append(item)
            # Aggregate engine-level risks for cross-lingual uncertainty.
            if (
                outcome is not None
                and outcome.canonical_match.match_basis in {"semantic", "translation"}
                and outcome.canonical_match.confidence < min_global_confidence
            ):
                engine_risks.append(
                    RiskFlag(
                        code="cross_lingual_uncertain",
                        severity="medium",
                        description=(
                            f"Low-confidence cross-lingual match for "
                            f"{candidate.label!r} (basis="
                            f"{outcome.canonical_match.match_basis}, "
                            f"conf={outcome.canonical_match.confidence:.2f})"
                        ),
                    )
                )

        # Order: source-local first, then create_global, then update variants,
        # then exclude (per Brief §4.1 docstring).
        items = _order_items(items)

        return ConceptPromotionResult(
            source_id=reading_source.source_id,
            primary_lang=reading_source.primary_lang,
            candidates_extracted=candidates_extracted,
            items=items,
            risks=engine_risks,
            error=None,
        )

    # ── Candidate extraction ────────────────────────────────────────────────

    def _extract_candidates(
        self,
        reading_source: ReadingSource,
        source_map: SourceMapBuildResult,
    ) -> list[ConceptCandidate]:
        """Deterministic candidate extraction from source map items.

        Algorithm (V1 deterministic — future LLM-backed extractors may live
        outside this slice):

        1. Walk ``source_map.items``; skip the ``index`` overview (it has no
           per-chapter evidence anchors).
        2. For each item with non-empty evidence, derive a ``label`` from
           the leading text of each ``EvidenceAnchor.excerpt`` (first
           sentence or first 80 chars, normalized via whitespace-collapse +
           punctuation-strip).
        3. Group across items by ``_normalize_key(label)``: same-key
           candidates from different ``chapter_ref`` values merge into one
           candidate (chapter_refs += this item's ref, raw_quotes += this
           excerpt). Within-chapter duplicates contribute their first
           occurrence's quote only — recurrence is cross-chapter.
        4. Cap ``raw_quotes`` at ``_MAX_RAW_QUOTES_PER_CANDIDATE``.

        Empty-label candidates (blank/whitespace excerpts) are still emitted
        with ``label=""`` so the action policy can route them to
        ``exclude`` per row 8.
        """
        # Map normalized_key → in-progress candidate state. Insertion order
        # is preserved (Python dict ordering) so output is stable.
        groups: dict[str, _CandidateGroup] = {}

        for item in source_map.items:
            # Skip the long-source ``index`` overview — it carries no
            # per-chapter evidence and exists for surface-level summary only.
            if item.chapter_ref == "index":
                continue
            for anchor in item.evidence:
                label = _derive_label(anchor.excerpt)
                key = _normalize_key(label)
                if key not in groups:
                    groups[key] = _CandidateGroup(
                        first_label=label,
                        chapter_refs=[],
                        raw_quotes=[],
                        seen_chapter_refs=set(),
                    )
                group = groups[key]
                # chapter_refs are de-duplicated cross-chapter (recurrence
                # signal). Within a chapter, multiple anchors don't bump
                # recurrence (they're the same chapter).
                if item.chapter_ref not in group.seen_chapter_refs:
                    group.chapter_refs.append(item.chapter_ref)
                    group.seen_chapter_refs.add(item.chapter_ref)
                # raw_quotes capped per Brief §4.1.
                if len(group.raw_quotes) < _MAX_RAW_QUOTES_PER_CANDIDATE:
                    group.raw_quotes.append(anchor.excerpt)

        candidates: list[ConceptCandidate] = []
        for idx, (key, group) in enumerate(groups.items(), start=1):
            candidate_id = f"cand_{idx:03d}_{key[:24]}" if key else f"cand_{idx:03d}_blank"
            candidates.append(
                ConceptCandidate(
                    candidate_id=candidate_id,
                    label=group.first_label,
                    aliases=[],  # V1 deterministic: no alias extraction
                    evidence_language=reading_source.primary_lang,
                    chapter_refs=list(group.chapter_refs),
                    raw_quotes=list(group.raw_quotes),
                )
            )
        return candidates

    # ── Action policy (Brief §4.2 top-down first-match) ─────────────────────

    def _apply_policy(
        self,
        *,
        candidate: ConceptCandidate,
        outcome: MatchOutcome | None,
        primary_lang: str,
        min_global_confidence: float,
        min_recurrence_for_global: int,
    ) -> ConceptReviewItem:
        """Apply Brief §4.2 first-match policy to one candidate.

        ``outcome=None`` is permitted for blank-label candidates (row 8).
        Other rows require a non-None outcome. We trust the matcher's
        ``MatchOutcome`` shape — schema-level validation already ran when
        the matcher constructed it.
        """
        # Row 8: extractor returned empty/blank label → exclude.
        if _is_label_blank(candidate.label):
            return _build_exclude_item(candidate, primary_lang)

        # outcome should be non-None for non-blank candidates per
        # the propose() loop above.
        assert outcome is not None  # noqa: S101 — engine invariant

        canonical = outcome.canonical_match
        conflict = outcome.conflict_signals
        recurrence = len(candidate.chapter_refs)

        # Row 1: recurrence < threshold AND no high-confidence match.
        # "high-confidence" means exact_alias≥0.90 OR sem/trans≥min_global_confidence.
        is_high_conf_match = _is_high_confidence_match(canonical, min_global_confidence)
        if recurrence < min_recurrence_for_global and not is_high_conf_match:
            return _build_keep_local_item(
                candidate=candidate,
                primary_lang=primary_lang,
                reason=(
                    f"single-chapter mention ({recurrence} chapter ref); not promoting globally"
                ),
            )

        # Row 2: exact_alias AND high confidence AND clean → merge.
        if (
            canonical.match_basis == "exact_alias"
            and canonical.confidence >= _HIGH_CONFIDENCE_EXACT_ALIAS_THRESHOLD
            and not conflict
        ):
            return _build_update_merge_item(
                candidate=candidate,
                canonical_match=canonical,
                primary_lang=primary_lang,
                reason=(f"exact alias match against {canonical.matched_concept_path}"),
            )

        # Row 3: exact_alias AND conflict → conflict review (defer).
        if canonical.match_basis == "exact_alias" and conflict:
            return _build_update_conflict_item(
                candidate=candidate,
                canonical_match=canonical,
                primary_lang=primary_lang,
                conflict_signals=conflict,
                reason=(
                    f"exact alias match against {canonical.matched_concept_path} "
                    f"but content conflicts: {', '.join(conflict)}"
                ),
            )

        # Row 4: semantic/translation AND ≥ min_global_confidence AND clean → merge.
        if (
            canonical.match_basis in {"semantic", "translation"}
            and canonical.confidence >= min_global_confidence
            and not conflict
        ):
            return _build_update_merge_item(
                candidate=candidate,
                canonical_match=canonical,
                primary_lang=primary_lang,
                reason=(
                    f"{canonical.match_basis} match against "
                    f"{canonical.matched_concept_path}, "
                    f"conf={canonical.confidence:.2f}"
                ),
            )

        # Row 5: semantic/translation AND < min_global_confidence
        # ⇒ defer (if conf ≥ 0.50) OR keep_source_local (if < 0.50).
        if (
            canonical.match_basis in {"semantic", "translation"}
            and canonical.confidence < min_global_confidence
        ):
            if canonical.confidence >= _LOW_CONFIDENCE_DROP_THRESHOLD:
                return _build_update_conflict_item(
                    candidate=candidate,
                    canonical_match=canonical,
                    primary_lang=primary_lang,
                    conflict_signals=conflict,
                    reason=(
                        f"low-confidence cross-lingual match "
                        f"({canonical.confidence:.2f}); requires human review"
                    ),
                )
            # Below 0.50 — too uncertain even for the conflict queue.
            return _build_keep_local_item(
                candidate=candidate,
                primary_lang=primary_lang,
                reason=(
                    f"low-confidence {canonical.match_basis} match "
                    f"({canonical.confidence:.2f} < "
                    f"{_LOW_CONFIDENCE_DROP_THRESHOLD:.2f}); keeping source-local"
                ),
            )

        # Row 6: match_basis="none" AND recurrence ≥ threshold AND raw_quotes ≥ 3
        # ⇒ create_global_concept.
        if (
            canonical.match_basis == "none"
            and recurrence >= min_recurrence_for_global
            and len(candidate.raw_quotes) >= _MIN_RAW_QUOTES_FOR_CREATE_GLOBAL
        ):
            return _build_create_global_item(
                candidate=candidate,
                primary_lang=primary_lang,
                min_global_confidence=min_global_confidence,
                reason=(
                    f"recurring across {recurrence} chapters; "
                    f"no global match found; {len(candidate.raw_quotes)} "
                    f"evidence quotes available"
                ),
            )

        # Row 7: match_basis="none" AND insufficient recurrence/evidence
        # ⇒ keep_source_local.
        return _build_keep_local_item(
            candidate=candidate,
            primary_lang=primary_lang,
            reason=(
                f"no global match; insufficient evidence for global "
                f"(recurrence={recurrence}, raw_quotes={len(candidate.raw_quotes)})"
            ),
        )


# ── Internal helpers ──────────────────────────────────────────────────────────


class _CandidateGroup:
    """Per-key in-progress accumulator used during candidate extraction.

    Plain class (not a dataclass / pydantic) — internal to ``_extract_candidates``
    and never escapes the function. Mutability is intentional during the walk;
    final ``ConceptCandidate`` objects are frozen pydantic models.
    """

    __slots__ = ("first_label", "chapter_refs", "raw_quotes", "seen_chapter_refs")

    def __init__(
        self,
        *,
        first_label: str,
        chapter_refs: list[str],
        raw_quotes: list[str],
        seen_chapter_refs: set[str],
    ) -> None:
        self.first_label = first_label
        self.chapter_refs = chapter_refs
        self.raw_quotes = raw_quotes
        self.seen_chapter_refs = seen_chapter_refs


def _derive_label(excerpt: str) -> str:
    """Derive a deterministic label from an evidence excerpt.

    Algorithm:
    1. Trim leading/trailing whitespace and punctuation.
    2. Take the first sentence (split on ``.``, ``!``, ``?`` or
       fullwidth equivalents). If no sentence-end found, take the
       full string.
    3. Truncate to ``_MAX_CANDIDATE_LABEL_CHARS`` chars.
    4. Collapse whitespace runs.

    Returns ``""`` for whitespace-only inputs (routes to row 8 exclude).
    """
    if not excerpt:
        return ""
    text = _WHITESPACE_RUN.sub(" ", excerpt).strip()
    text = _LABEL_PUNCTUATION.sub("", text).strip()
    if not text:
        return ""
    # First sentence — split on first sentence-end marker.
    parts = _SENTENCE_END.split(text, maxsplit=1)
    head = parts[0].strip() if parts else text
    if not head:
        head = text
    if len(head) > _MAX_CANDIDATE_LABEL_CHARS:
        head = head[:_MAX_CANDIDATE_LABEL_CHARS].rstrip()
    return head


def _normalize_key(label: str) -> str:
    """Normalization for grouping: lowercase + collapse whitespace.

    Two excerpts whose derived labels differ only by case / whitespace
    map to the same group → contribute to recurrence count.
    """
    if not label:
        return ""
    return _WHITESPACE_RUN.sub(" ", label).strip().lower()


def _is_label_blank(label: str) -> bool:
    """Per Brief §4.2 row 8: ``exclude`` when label is empty or whitespace."""
    return not label or not label.strip()


def _is_high_confidence_match(
    canonical: CanonicalMatch,
    min_global_confidence: float,
) -> bool:
    """Row 1 helper: does this match qualify as 'high-confidence'?

    - exact_alias with conf ≥ 0.90
    - semantic/translation with conf ≥ min_global_confidence
    - none / low conf → False

    Used to decide whether row 1 (recurrence < threshold AND no high-conf
    match) applies — a single-chapter mention paired with a strong match
    skips row 1 and falls through to rows 2-5.
    """
    if canonical.match_basis == "exact_alias":
        return canonical.confidence >= _HIGH_CONFIDENCE_EXACT_ALIAS_THRESHOLD
    if canonical.match_basis in {"semantic", "translation"}:
        return canonical.confidence >= min_global_confidence
    return False


def _quotes_to_evidence(
    candidate: ConceptCandidate,
    *,
    source_path_hint: str | None = None,
) -> list[EvidenceAnchor]:
    """Convert candidate raw quotes into ``EvidenceAnchor`` list.

    Each raw quote becomes one ``EvidenceAnchor(kind="chapter_quote", ...)``.
    Locator format is ``"concept-quote-{idx}"`` since the engine doesn't
    have access to the original CFI/line-range here (those are owned by
    the source map item that produced the quote — we lose that link in
    the deterministic group-by-label flow). #515 may rebind locators
    when committing.

    ``source_path`` falls back to a candidate-derived placeholder when
    no hint is provided.
    """
    source_path = source_path_hint or f"concept://{candidate.candidate_id}"
    out: list[EvidenceAnchor] = []
    for idx, quote in enumerate(candidate.raw_quotes, start=1):
        if not quote.strip():
            continue
        out.append(
            EvidenceAnchor(
                kind="chapter_quote",
                source_path=source_path,
                locator=f"concept-quote-{idx}",
                excerpt=quote,
                confidence=0.7,
            )
        )
    return out


def _truncate(text: str, *, cap: int = _MAX_REASON_CHARS) -> str:
    """Reason / message truncation with ellipsis suffix when over cap."""
    if len(text) <= cap:
        return text
    return text[: max(0, cap - 1)] + "…"


def _build_keep_local_item(
    *,
    candidate: ConceptCandidate,
    primary_lang: str,
    reason: str,
) -> ConceptReviewItem:
    """Build a ``keep_source_local`` ConceptReviewItem.

    C3: recommendation must be ``"include"`` or ``"defer"`` (NOT exclude —
    local concepts still useful inside the source). When evidence is
    available we recommend include so #515 commits the source-local glossary;
    without evidence we defer.
    """
    evidence = _quotes_to_evidence(candidate)
    recommendation = "include" if evidence else "defer"
    return ConceptReviewItem(
        item_id=candidate.candidate_id,
        recommendation=recommendation,
        action="keep_source_local",
        reason=_truncate(reason),
        evidence=evidence,
        risk=[],
        confidence=0.6,
        source_importance=0.4,
        reader_salience=0.0,
        concept_label=candidate.label,
        evidence_language=candidate.evidence_language or primary_lang,
        canonical_match=None,
    )


def _build_create_global_item(
    *,
    candidate: ConceptCandidate,
    primary_lang: str,
    min_global_confidence: float,
    reason: str,
) -> ConceptReviewItem:
    """Build a ``create_global_concept`` ConceptReviewItem.

    C4: requires ≥1 EvidenceAnchor AND confidence ≥ min_global_confidence.
    Reaching this row implies the candidate has ≥3 raw quotes (Brief §4.2
    row 6 gate); we set confidence at exactly ``min_global_confidence`` so
    the C4 invariant is always satisfied (per-candidate confidence is
    deterministic; LLM-backed engines may compute higher).
    """
    evidence = _quotes_to_evidence(candidate)
    return ConceptReviewItem(
        item_id=candidate.candidate_id,
        recommendation="include",
        action="create_global_concept",
        reason=_truncate(reason),
        evidence=evidence,
        risk=[],
        confidence=min_global_confidence,
        source_importance=0.7,
        reader_salience=0.0,
        concept_label=candidate.label,
        evidence_language=candidate.evidence_language or primary_lang,
        canonical_match=None,
    )


def _build_update_merge_item(
    *,
    candidate: ConceptCandidate,
    canonical_match: CanonicalMatch,
    primary_lang: str,
    reason: str,
) -> ConceptReviewItem:
    """Build an ``update_merge_global`` ConceptReviewItem.

    Recommendation is ``include`` (clean merge); evidence required by V1.
    """
    evidence = _quotes_to_evidence(candidate)
    # C1/V1: include requires non-empty evidence. When no quotes available,
    # downgrade to defer (cannot auto-merge without evidence).
    recommendation = "include" if evidence else "defer"
    return ConceptReviewItem(
        item_id=candidate.candidate_id,
        recommendation=recommendation,
        action="update_merge_global",
        reason=_truncate(reason),
        evidence=evidence,
        risk=[],
        confidence=canonical_match.confidence,
        source_importance=0.7,
        reader_salience=0.0,
        concept_label=candidate.label,
        evidence_language=candidate.evidence_language or primary_lang,
        canonical_match=canonical_match,
    )


def _build_update_conflict_item(
    *,
    candidate: ConceptCandidate,
    canonical_match: CanonicalMatch,
    primary_lang: str,
    conflict_signals: list[str],
    reason: str,
) -> ConceptReviewItem:
    """Build an ``update_conflict_global`` ConceptReviewItem.

    C2: ``recommendation="defer"`` (always) — conflict must go to human
    review queue. Evidence still attached so reviewer can inspect.
    """
    evidence = _quotes_to_evidence(candidate)
    risks: list[RiskFlag] = []
    if conflict_signals:
        risks.append(
            RiskFlag(
                code="duplicate_concept",
                severity="medium",
                description=(
                    f"Conflict with existing concept "
                    f"{canonical_match.matched_concept_path}: "
                    f"{', '.join(conflict_signals)}"
                ),
            )
        )
    if (
        canonical_match.match_basis in {"semantic", "translation"}
        and canonical_match.confidence < _DEFAULT_MIN_GLOBAL_CONFIDENCE
    ):
        risks.append(
            RiskFlag(
                code="cross_lingual_uncertain",
                severity="medium",
                description=(
                    f"Low-confidence {canonical_match.match_basis} match "
                    f"(conf={canonical_match.confidence:.2f})"
                ),
            )
        )
    return ConceptReviewItem(
        item_id=candidate.candidate_id,
        recommendation="defer",
        action="update_conflict_global",
        reason=_truncate(reason),
        evidence=evidence,
        risk=risks,
        confidence=canonical_match.confidence,
        source_importance=0.6,
        reader_salience=0.0,
        concept_label=candidate.label,
        evidence_language=candidate.evidence_language or primary_lang,
        canonical_match=canonical_match,
    )


def _build_exclude_item(
    candidate: ConceptCandidate,
    primary_lang: str,
) -> ConceptReviewItem:
    """Build an ``exclude`` ConceptReviewItem (row 8 — blank label).

    Recommendation is ``exclude`` (matches LLM output). Evidence allowed
    to be empty (V1 only enforces include ⇒ evidence). ``concept_label``
    must be non-empty per #512 schema; we substitute a placeholder for
    empty-label candidates so the schema accepts the item.
    """
    placeholder_label = candidate.label.strip() or "(empty)"
    return ConceptReviewItem(
        item_id=candidate.candidate_id,
        recommendation="exclude",
        action="exclude",
        reason="candidate label empty or noise",
        evidence=[],
        risk=[],
        confidence=0.1,
        source_importance=0.0,
        reader_salience=0.0,
        concept_label=placeholder_label,
        evidence_language=candidate.evidence_language or primary_lang,
        canonical_match=None,
    )


# ── Output ordering ──────────────────────────────────────────────────────────


_ACTION_ORDER = {
    "keep_source_local": 0,
    "create_global_concept": 1,
    "update_merge_global": 2,
    "update_conflict_global": 3,
    "exclude": 4,
}
"""Per Brief §4.1 docstring on ``items``: source-local first, then create_global,
then update variants, then exclude. Stable within each bucket via
``enumerate`` index."""


def _order_items(items: list[ConceptReviewItem]) -> list[ConceptReviewItem]:
    """Stable-sort items by ``_ACTION_ORDER`` bucket; preserve relative order
    within a bucket (mirrors `sorted(..., key=...)` semantics)."""
    return sorted(items, key=lambda it: _ACTION_ORDER[it.action])
