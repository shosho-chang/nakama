"""Dry-run ``ConceptMatcher`` STUB (ADR-024 Slice 10 / N518a).

**N518a is stub-only.** This module satisfies the ``ConceptMatcher``
Protocol shape (``shared.concept_promotion_engine.ConceptMatcher``) so
``PromotionReviewService.__init__`` can construct successfully at app
startup, but the ``match()`` method raises ``NotImplementedError`` when
called. The full deterministic body lands in **N518b** (separate PR).

Future N518b body: always returns "no global match" + low confidence so
the engine routes everything to source-local concepts in dry-run mode.
"""

from __future__ import annotations

from shared.schemas.concept_promotion import (
    ConceptCandidate,
    KBConceptEntry,  # noqa: F401 — Protocol shape
    MatchOutcome,
)


class DryRunConceptMatcher:
    """STUB — satisfies the ``ConceptMatcher`` Protocol but raises on call.

    Production wiring (``thousand_sunny.app`` lifespan) constructs this
    class for ``NAKAMA_PROMOTION_MODE=dry_run`` so the service constructs
    cleanly. Calling ``match()`` raises ``NotImplementedError`` — the
    real body (deterministic "no match" outcome) lands in N518b.
    """

    def match(
        self,
        candidate: ConceptCandidate,
        kb_index,  # KBConceptIndex Protocol — left untyped to avoid runtime import cycle
        primary_lang: str,
    ) -> MatchOutcome:
        """Raise ``NotImplementedError`` per N518a stub-only contract.

        The real body — return a ``MatchOutcome`` with
        ``CanonicalMatch(match_basis="none", confidence=0.0)`` — is
        implemented in N518b.
        """
        raise NotImplementedError("DryRunConceptMatcher.match: full impl deferred to N518b")
