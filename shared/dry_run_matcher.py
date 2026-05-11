"""Dry-run ``ConceptMatcher`` (ADR-024 Slice 10 / N518b).

Deterministic, non-LLM placeholder implementation of the ``ConceptMatcher``
Protocol declared in ``shared.concept_promotion_engine`` (#514). Used by the
production wiring when ``NAKAMA_PROMOTION_MODE=dry_run`` (the default in
N518) so the promotion review surface can be exercised without any LLM call.

**Why "always uncertain".** The dry-run mode's job is to make the review UI
surface every claim/concept as needs-human-judgment so 修修 sees the full
review flow. Returning ``match_basis="none"`` + low confidence ensures that
the engine routes:

- candidates with sufficient recurrence + evidence → ``create_global_concept``
  (so the create-global path is exercised end-to-end);
- candidates with insufficient recurrence/evidence → ``keep_source_local``.

Either way the result is observable in the review UI without depending on
KB content or matcher cleverness. The full LLM-backed matcher lands in
**N519** behind the same ``NAKAMA_PROMOTION_MODE`` gate.

Determinism contract (W2 / brief §6 boundary 3 / AT19):

- ``match(candidate, kb_index, primary_lang)`` is a pure function. Same
  inputs → byte-identical output.
- Every call returns ``CanonicalMatch(match_basis="none",
  matched_concept_path=None, confidence=0.0)`` and an empty
  ``conflict_signals`` list. The ``"none"`` basis + ``None`` path
  satisfies the V10 invariant on ``CanonicalMatch``.
- NO ``anthropic`` import (W2 / WT10 subprocess gate).
- NO env reads, no filesystem IO.
"""

from __future__ import annotations

from shared.schemas.concept_promotion import (
    ConceptCandidate,
    KBConceptEntry,  # noqa: F401 — Protocol shape (only used for type docs)
    MatchOutcome,
)
from shared.schemas.promotion_manifest import CanonicalMatch

_DRY_RUN_CONFIDENCE = 0.0
"""Confidence baseline for the dry-run matcher. Zero is the lowest legal
value on ``CanonicalMatch``; combined with ``match_basis="none"`` it routes
every candidate through the engine's "no global match" rows. Brief §8 +
AT19 require ``confidence ≤ 0.1`` (low). We use 0.0 to make the intent
explicit — this is not a real match, this is a placeholder."""


class DryRunConceptMatcher:
    """Deterministic dry-run ``ConceptMatcher`` (no LLM, no network).

    Production wiring (``thousand_sunny.app`` lifespan → ``promotion_wiring``)
    constructs this class for ``NAKAMA_PROMOTION_MODE=dry_run`` (the default
    in N518). The full LLM-backed matcher lands in N519 behind the same
    config gate.

    Stateless — no constructor arguments, no per-instance state. Pure
    function semantics: same call always returns equivalent values.
    """

    def match(
        self,
        candidate: ConceptCandidate,
        kb_index,  # KBConceptIndex Protocol — left untyped to avoid runtime cycle
        primary_lang: str,
    ) -> MatchOutcome:
        """Return a "no global match" outcome with zero confidence.

        Inputs are accepted to satisfy the Protocol shape but are NOT
        consulted. The dry-run policy is "always uncertain" — every
        candidate routes to the source-local / human-review queue so
        the review UI surfaces the full flow.

        Returns a frozen ``MatchOutcome`` with:

        - ``canonical_match.match_basis = "none"``
        - ``canonical_match.confidence = 0.0``
        - ``canonical_match.matched_concept_path = None``
        - ``conflict_signals = []``
        """
        return MatchOutcome(
            canonical_match=CanonicalMatch(
                match_basis="none",
                confidence=_DRY_RUN_CONFIDENCE,
                matched_concept_path=None,
            ),
            conflict_signals=[],
        )
