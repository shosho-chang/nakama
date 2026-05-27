"""Dry-run ``EntityMatcher`` (ADR-034 v2 PR3 — parallel to N518b's
``DryRunConceptMatcher``).

Deterministic, non-LLM placeholder implementation of the ``EntityMatcher``
Protocol declared in ``shared.entity_promotion_engine``. Used by the
production wiring when LLM-backed entity matching is not yet available
so the promotion review surface can exercise the entity flow end-to-end
without any LLM call.

**Why "always uncertain".** Same as the concept dry-run: mode's job is
to make the review UI surface every entity candidate as
needs-human-judgment so 修修 sees the full review flow. Returning
``match_basis="none"`` + low confidence routes every Entity through
:func:`shared.entity_promotion_engine._apply_policy` row 7 →
``create_entity`` action (with ``recommendation`` determined by evidence
presence per E3).

The full LLM-backed matcher (LLM-aided cross-source disambiguation,
alias resolution against ``KB/Wiki/Entities/``) is future work behind
the same wiring gate; this placeholder unblocks downstream surfaces.

Determinism contract:

- ``match(candidate, kb_index, primary_lang)`` is a pure function. Same
  inputs → byte-identical output.
- Every call returns ``EntityCanonicalMatch(match_basis="none",
  matched_entity_path=None, confidence=0.0)`` and an empty
  ``conflict_signals`` list. The ``"none"`` basis + ``None`` path
  satisfies the V10 mirror invariant on ``EntityCanonicalMatch``.
- NO ``anthropic`` import.
- NO env reads, no filesystem IO.
"""

from __future__ import annotations

from shared.schemas.entity_promotion import (
    EntityCandidate,
    EntityMatchOutcome,
    KBEntityEntry,  # noqa: F401 — Protocol shape (only used for type docs)
)
from shared.schemas.promotion_manifest import EntityCanonicalMatch

_DRY_RUN_CONFIDENCE = 0.0
"""Confidence baseline for the dry-run matcher. Zero combined with
``match_basis="none"`` routes every candidate to row 7 (create_entity).
Mirrors ``DryRunConceptMatcher`` — this is not a real match, it's a
placeholder making intent explicit."""


class DryRunEntityMatcher:
    """Deterministic dry-run ``EntityMatcher`` (no LLM, no network).

    Stateless — no constructor arguments, no per-instance state. Pure
    function semantics: same call always returns equivalent values.
    """

    def match(
        self,
        candidate: EntityCandidate,
        kb_index,  # KBEntityIndex Protocol — left untyped to avoid runtime cycle
        primary_lang: str,
    ) -> EntityMatchOutcome:
        """Return a "no global match" outcome with zero confidence.

        Inputs are accepted to satisfy the Protocol shape but are NOT
        consulted. The dry-run policy is "always uncertain" — every
        candidate routes to ``create_entity`` so the review UI surfaces
        the full flow.

        Returns a frozen ``EntityMatchOutcome`` with:

        - ``canonical_match.match_basis = "none"``
        - ``canonical_match.confidence = 0.0``
        - ``canonical_match.matched_entity_path = None``
        - ``conflict_signals = []``
        """
        return EntityMatchOutcome(
            canonical_match=EntityCanonicalMatch(
                match_basis="none",
                confidence=_DRY_RUN_CONFIDENCE,
                matched_entity_path=None,
            ),
            conflict_signals=[],
        )
