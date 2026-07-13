"""Entity Promotion Engine (ADR-034 v2 PR2b-ii).

Deterministic entity promotion engine — input is a list of
``EntityCandidate`` (caller's NER / LLM-backed extraction step is
upstream of this engine, by design), output is an
``EntityPromotionResult`` carrying ``EntityReviewItem`` entries ready
to wrap into a ``PromotionManifest`` (``schema_version=2``).

Parallels :mod:`agents.robin.promotion.concept_promotion_engine`. Diverges in two
intentional ways:

1. **No candidate extraction inside the engine.** Concept's engine walks
   a ``SourceMapBuildResult`` deterministically. Entity extraction is
   fundamentally NER-shaped (proper nouns, cross-lingual surface
   variants) and naturally LLM-backed — but the engine boundary forbids
   LLM imports (E5). So the engine takes pre-classified candidates and
   leaves NER to the caller.
2. **Simpler action policy.** Concept has 8 rows (recurrence threshold,
   raw_quote count, multiple confidence bands). Entity has 5 rows —
   single-mention entities are valid (proper nouns are stable
   identifiers), so the recurrence threshold concept does not apply.

No LLM call inside this module — matching is delegated to an injected
``EntityMatcher`` Protocol. Real KB index access is delegated to an
injected ``KBEntityIndex`` Protocol.

Engine NEVER imports ``shared.book_storage``, ``fastapi``,
``thousand_sunny.*``, ``agents.*``, or LLM clients (E5 / subprocess
gates inherited from concept_promotion_engine).

Hard invariants enforced (see :mod:`shared.schemas.entity_promotion`
module docstring):

- E1-E7 (see schema docstring).

Engine action policy (top-down first-match):

| Row | Condition                                                  | Action                  |
|-----|------------------------------------------------------------|-------------------------|
| 1   | candidate label empty/blank                                | exclude                 |
| 2   | exact_alias AND conf ≥ 0.90 AND no conflict_signals        | update_merge_entity     |
| 3   | exact_alias AND conflict_signals non-empty                 | update_conflict_entity  |
| 4   | semantic/translation AND conf ≥ min_global_confidence      | update_merge_entity     |
|     |                                  AND no conflict_signals                            |
| 5   | semantic/translation AND conf < min_global_confidence      | update_conflict_entity  |
|     |                                       AND conf ≥ 0.50      |                         |
| 6   | semantic/translation AND conf < 0.50                       | exclude                 |
| 7   | match_basis="none"                                         | create_entity           |
"""

from __future__ import annotations

from typing import Protocol

from shared.log import get_logger
from shared.schemas.entity_promotion import (
    EntityCandidate,
    EntityMatchOutcome,
    EntityPromotionResult,
    KBEntityEntry,
)
from shared.schemas.promotion_manifest import (
    EntityCanonicalMatch,
    EntityReviewItem,
    EvidenceAnchor,
    RiskFlag,
)
from shared.schemas.reading_source import ReadingSource

_logger = get_logger("nakama.agents.robin.promotion.entity_promotion_engine")


# ── Caller-supplied protocols ─────────────────────────────────────────────────


class EntityMatcher(Protocol):
    """Cross-source / cross-lingual entity matcher protocol.

    Implementations decide the matching algorithm (alias lookup,
    embedding similarity, LLM-backed disambiguation). The engine threads
    the returned ``EntityMatchOutcome`` directly into the action policy
    without re-interpretation.

    Implementations MUST NOT mutate the caller-supplied candidate.
    Implementations may raise; the engine catches
    ``_MATCHER_FAILURES`` and routes to error state.
    """

    def match(
        self,
        candidate: EntityCandidate,
        kb_index: "KBEntityIndex",
        primary_lang: str,
    ) -> EntityMatchOutcome:
        """Return best canonical match (or ``"none"`` basis) + confidence."""
        ...


class KBEntityIndex(Protocol):
    """Read-only view of existing global KB entities. Mirrors
    :class:`agents.robin.promotion.concept_promotion_engine.KBConceptIndex` shape."""

    def lookup(self, alias: str) -> KBEntityEntry | None:
        """Return the KB entity entry whose ``aliases`` contains ``alias``
        (case-insensitive normalization is the implementation's choice).
        Returns ``None`` when no match."""
        ...

    def aliases_starting_with(self, prefix: str) -> list[str]:
        """Return aliases that start with ``prefix``. Used by future
        prefix-search matchers."""
        ...


# ── Policy thresholds ─────────────────────────────────────────────────────────

_DEFAULT_MIN_GLOBAL_CONFIDENCE = 0.75
"""Threshold for auto-merge of semantic/translation matches (row 4)."""

_HIGH_CONFIDENCE_EXACT_ALIAS_THRESHOLD = 0.90
"""Exact_alias must clear 0.90 to auto-merge without conflict review
(row 2). Mirrors concept_promotion_engine threshold."""

_LOW_CONFIDENCE_DROP_THRESHOLD = 0.50
"""Below this, semantic/translation matches route to ``exclude`` (row 6
— too uncertain even for the conflict queue)."""

_MAX_REASON_CHARS = 200
"""Per ConceptReviewItem builder convention — keep ``reason`` ≤ 200 chars."""

_MAX_RAW_QUOTES_PER_CANDIDATE = 3
"""Mirrors concept candidate raw_quotes cap. Engine truncates beyond this."""


# Documented matcher failures — narrow tuple so programmer errors
# (TypeError, AttributeError, KeyboardInterrupt) propagate.
_MATCHER_FAILURES = (ValueError, RuntimeError, OSError, KeyError)
"""Documented matcher exceptions caught by the engine and routed to
``EntityPromotionResult.error``. Other exception types propagate."""


# ── Engine ────────────────────────────────────────────────────────────────────


class EntityPromotionEngine:
    """Deterministic entity promotion engine.

    One ``propose(reading_source, candidates, kb_index, matcher)`` per
    source; no caching, no enumeration. Caller is responsible for
    upstream gating: engine MUST only be invoked with already-validated
    ``EntityCandidate`` objects (Pydantic constructor enforces E4
    entity_type/metadata consistency).

    Construction takes no arguments — engine is stateless. Caller injects
    matcher and kb_index per ``propose()`` call.
    """

    def propose(
        self,
        reading_source: ReadingSource,
        candidates: list[EntityCandidate],
        kb_index: KBEntityIndex,
        matcher: EntityMatcher,
        *,
        min_global_confidence: float = _DEFAULT_MIN_GLOBAL_CONFIDENCE,
    ) -> EntityPromotionResult:
        """Propose entity promotion items for ``reading_source``.

        Matching: each candidate is passed to ``matcher.match(candidate,
        kb_index, reading_source.primary_lang)``. The returned
        ``EntityMatchOutcome`` drives the action policy.

        Failure: matcher exceptions in the documented tuple
        (``ValueError``, ``RuntimeError``, ``OSError``, ``KeyError``)
        are caught; engine returns an ``EntityPromotionResult`` with
        ``items=[]`` and ``error=...``. Programmer errors propagate.

        Returns a frozen ``EntityPromotionResult``.
        """
        candidates_received = len(candidates)

        # Match each candidate. Catch documented failures via narrow tuple;
        # route to error state. Programmer errors propagate.
        outcomes: list[EntityMatchOutcome | None] = []
        try:
            for candidate in candidates:
                if _is_label_blank(candidate.label):
                    outcomes.append(None)  # row 1 — no matcher call needed
                else:
                    outcomes.append(matcher.match(candidate, kb_index, reading_source.primary_lang))
        except _MATCHER_FAILURES as exc:
            _logger.warning(
                "entity matcher failed",
                extra={
                    "category": "entity_matcher_failed",
                    "source_id": reading_source.source_id,
                },
            )
            return EntityPromotionResult(
                source_id=reading_source.source_id,
                primary_lang=reading_source.primary_lang,
                candidates_received=candidates_received,
                items=[],
                risks=[],
                error=f"matcher_failed: {type(exc).__name__}: {exc!s}",
            )

        # Apply action policy per candidate + outcome.
        items: list[EntityReviewItem] = []
        engine_risks: list[RiskFlag] = []
        for candidate, outcome in zip(candidates, outcomes, strict=True):
            item = self._apply_policy(
                candidate=candidate,
                outcome=outcome,
                primary_lang=reading_source.primary_lang,
                min_global_confidence=min_global_confidence,
            )
            if item is not None:
                items.append(item)
            # Aggregate engine-level cross-lingual uncertainty risk
            # (mirrors concept_promotion_engine).
            if (
                outcome is not None
                and outcome.canonical_match.match_basis in {"semantic", "translation"}
                and outcome.canonical_match.confidence < min_global_confidence
                and outcome.canonical_match.confidence >= _LOW_CONFIDENCE_DROP_THRESHOLD
            ):
                engine_risks.append(
                    RiskFlag(
                        code="cross_lingual_uncertain",
                        severity="medium",
                        description=(
                            f"Low-confidence cross-lingual entity match for "
                            f"{candidate.label!r} (basis="
                            f"{outcome.canonical_match.match_basis}, "
                            f"conf={outcome.canonical_match.confidence:.2f})"
                        ),
                    )
                )

        return EntityPromotionResult(
            source_id=reading_source.source_id,
            primary_lang=reading_source.primary_lang,
            candidates_received=candidates_received,
            items=items,
            risks=engine_risks,
            error=None,
        )

    # ── Action policy ────────────────────────────────────────────────────────

    def _apply_policy(
        self,
        *,
        candidate: EntityCandidate,
        outcome: EntityMatchOutcome | None,
        primary_lang: str,
        min_global_confidence: float,
    ) -> EntityReviewItem | None:
        """Apply top-down first-match policy. Returns ``None`` for
        excluded candidates so callers can filter (row 1 / row 6 exclusion).
        """
        if _is_label_blank(candidate.label):
            return None  # row 1 — exclude blank-label candidates

        # outcome is non-None for non-blank candidates per the propose() loop.
        assert outcome is not None  # noqa: S101 — engine invariant

        canonical = outcome.canonical_match
        conflict = outcome.conflict_signals

        # Row 2: exact_alias + high conf + clean → update_merge_entity.
        if (
            canonical.match_basis == "exact_alias"
            and canonical.confidence >= _HIGH_CONFIDENCE_EXACT_ALIAS_THRESHOLD
            and not conflict
        ):
            return _build_update_merge_item(
                candidate=candidate,
                canonical_match=canonical,
                primary_lang=primary_lang,
                reason=f"exact alias match against {canonical.matched_entity_path}",
            )

        # Row 3: exact_alias + conflict → update_conflict_entity.
        if canonical.match_basis == "exact_alias" and conflict:
            return _build_update_conflict_item(
                candidate=candidate,
                canonical_match=canonical,
                primary_lang=primary_lang,
                conflict_signals=conflict,
                reason=(
                    f"exact alias match against {canonical.matched_entity_path} "
                    f"but content conflicts: {', '.join(conflict)}"
                ),
            )

        # Row 4: semantic/translation + ≥ threshold + clean → update_merge_entity.
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
                    f"{canonical.matched_entity_path}, "
                    f"conf={canonical.confidence:.2f}"
                ),
            )

        # Row 5: semantic/translation + below threshold + ≥ 0.50
        # → update_conflict_entity (defer for human review).
        if (
            canonical.match_basis in {"semantic", "translation"}
            and canonical.confidence < min_global_confidence
            and canonical.confidence >= _LOW_CONFIDENCE_DROP_THRESHOLD
        ):
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

        # Row 6: semantic/translation + < 0.50 → exclude.
        if (
            canonical.match_basis in {"semantic", "translation"}
            and canonical.confidence < _LOW_CONFIDENCE_DROP_THRESHOLD
        ):
            return None

        # Row 7: match_basis="none" → create_entity.
        if canonical.match_basis == "none":
            return _build_create_entity_item(
                candidate=candidate,
                primary_lang=primary_lang,
                min_global_confidence=min_global_confidence,
                reason=(
                    f"no existing KB entity matches {candidate.label!r}; "
                    f"create new {candidate.entity_type} entity"
                ),
            )

        # Defensive — match_basis is a closed Literal so this is unreachable
        # absent schema drift. Raise so register-hygiene caller sees it.
        raise NotImplementedError(
            f"_apply_policy: unhandled match_basis={canonical.match_basis!r}. "
            f"Action policy table needs a new row."
        )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _is_label_blank(label: str) -> bool:
    """Empty / whitespace-only labels route to ``exclude``."""
    return not label or not label.strip()


def _truncate(text: str, limit: int = _MAX_REASON_CHARS) -> str:
    """Truncate ``text`` to ``limit`` chars, appending ``…`` when cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _quotes_to_evidence(
    candidate: EntityCandidate,
) -> list[EvidenceAnchor]:
    """Build ``EvidenceAnchor`` list from candidate's ``raw_quotes``.

    Each non-blank quote becomes one anchor with kind=``external_ref``
    (caller-supplied source_refs may use timestamp / chapter / xpath
    locators — engine uses ``external_ref`` as the variant-agnostic
    fallback so cross-source entities don't require a closed enum
    expansion before PR2b-ii ships).
    """
    anchors: list[EvidenceAnchor] = []
    capped_quotes = candidate.raw_quotes[:_MAX_RAW_QUOTES_PER_CANDIDATE]
    for idx, quote in enumerate(capped_quotes):
        if not quote.strip():
            continue
        # Pair quote with source_ref by index; missing source_refs degrade
        # to candidate_id so the anchor is still constructible.
        if idx < len(candidate.source_refs):
            source_ref = candidate.source_refs[idx]
        else:
            source_ref = candidate.candidate_id
        anchors.append(
            EvidenceAnchor(
                kind="external_ref",
                source_path=source_ref,
                locator=source_ref,
                excerpt=quote.strip(),
                confidence=0.85,
            )
        )
    return anchors


def _build_update_merge_item(
    *,
    candidate: EntityCandidate,
    canonical_match: EntityCanonicalMatch,
    primary_lang: str,
    reason: str,
) -> EntityReviewItem:
    """Build an ``update_merge_entity`` ``EntityReviewItem``.

    Recommendation is ``include`` (clean merge); evidence required by V1.
    When no quotes available, downgrade to ``defer`` (cannot auto-merge
    without evidence — mirrors concept builder).
    """
    evidence = _quotes_to_evidence(candidate)
    recommendation = "include" if evidence else "defer"
    return EntityReviewItem(
        item_id=candidate.candidate_id,
        recommendation=recommendation,
        action="update_merge_entity",
        reason=_truncate(reason),
        evidence=evidence,
        risk=[],
        confidence=canonical_match.confidence,
        source_importance=0.7,
        reader_salience=0.5,
        entity_label=candidate.label,
        aliases=list(candidate.aliases),
        evidence_language=candidate.evidence_language or primary_lang,
        metadata=candidate.metadata,
        canonical_match=canonical_match,
    )


def _build_update_conflict_item(
    *,
    candidate: EntityCandidate,
    canonical_match: EntityCanonicalMatch,
    primary_lang: str,
    conflict_signals: list[str],
    reason: str,
) -> EntityReviewItem:
    """Build an ``update_conflict_entity`` ``EntityReviewItem``.

    E2: recommendation=``defer`` (always) — conflict must go to human
    review queue. Evidence still attached so reviewer can inspect.
    """
    evidence = _quotes_to_evidence(candidate)
    risks: list[RiskFlag] = []
    for signal in conflict_signals:
        # RiskCode enum is closed; entity-specific conflict signals fall
        # under "other" with structured description. Adding a dedicated
        # ``entity_metadata_conflict`` code is a closed-set extension that
        # bumps schema_version — deferred until reviewer feedback shows
        # the description-string approach is insufficient.
        risks.append(
            RiskFlag(
                code="other",
                severity="medium",
                description=f"entity_metadata_conflict: {signal}",
            )
        )
    return EntityReviewItem(
        item_id=candidate.candidate_id,
        recommendation="defer",
        action="update_conflict_entity",
        reason=_truncate(reason),
        evidence=evidence,
        risk=risks,
        confidence=canonical_match.confidence,
        source_importance=0.7,
        reader_salience=0.5,
        entity_label=candidate.label,
        aliases=list(candidate.aliases),
        evidence_language=candidate.evidence_language or primary_lang,
        metadata=candidate.metadata,
        canonical_match=canonical_match,
    )


def _build_create_entity_item(
    *,
    candidate: EntityCandidate,
    primary_lang: str,
    min_global_confidence: float,
    reason: str,
) -> EntityReviewItem:
    """Build a ``create_entity`` ``EntityReviewItem``.

    E3: requires ≥1 ``EvidenceAnchor`` AND ``confidence ≥
    min_global_confidence``. When no quotes are available, downgrade
    to ``defer`` (caller may not have surfaced evidence for entity NER
    yet — mirrors concept create builder).
    """
    evidence = _quotes_to_evidence(candidate)
    if not evidence:
        # No evidence — downgrade to defer so V1 invariant
        # (include ⇒ non-empty evidence) cannot be violated.
        recommendation = "defer"
    else:
        recommendation = "include"
    return EntityReviewItem(
        item_id=candidate.candidate_id,
        recommendation=recommendation,
        action="create_entity",
        reason=_truncate(reason),
        evidence=evidence,
        risk=[],
        confidence=min_global_confidence,
        source_importance=0.7,
        reader_salience=0.5,
        entity_label=candidate.label,
        aliases=list(candidate.aliases),
        evidence_language=candidate.evidence_language or primary_lang,
        metadata=candidate.metadata,
        canonical_match=None,
    )
