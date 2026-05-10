"""Dry-run ``ClaimExtractor`` STUB (ADR-024 Slice 10 / N518a).

**N518a is stub-only.** This module satisfies the ``ClaimExtractor``
Protocol shape (``shared.source_map_builder.ClaimExtractor``) so
``PromotionReviewService.__init__`` can construct successfully at app
startup, but the ``extract()`` method raises ``NotImplementedError`` when
called. Production wiring boots the promotion review surface in dry-run
mode by default; a user clicking "Start review" hits this stub and gets a
clear 500 explaining the deferred slice.

The deterministic dry-run body lands in **N518b** (separate PR).

Why split: the N518 brief originally bundled wiring + dry-run body in one
slice. Splitting reduces the per-PR review surface and lets the wiring
land + smoke independently of the dry-run logic. The brief's §6 boundaries
3 / 8 (no real LLM in N518) still hold — the future N518b body must remain
deterministic with no ``anthropic`` import.
"""

from __future__ import annotations

from shared.schemas.reading_source import ReadingSource  # noqa: F401 — Protocol shape
from shared.schemas.source_map import ClaimExtractionResult


class DryRunClaimExtractor:
    """STUB — satisfies the ``ClaimExtractor`` Protocol but raises on call.

    Production wiring (``thousand_sunny.app`` lifespan) constructs this
    class for ``NAKAMA_PROMOTION_MODE=dry_run`` so the service constructs
    cleanly. Calling ``extract()`` raises ``NotImplementedError`` — the
    deterministic body lands in N518b. The full Protocol signature is
    preserved so type checkers and structural Protocol checks pass.
    """

    def extract(
        self,
        chapter_text: str,
        chapter_title: str,
        primary_lang: str,
    ) -> ClaimExtractionResult:
        """Raise ``NotImplementedError`` per N518a stub-only contract.

        The real deterministic body — hash chapter text → 1-3 fixture-shaped
        ClaimRecords with ``[DRY-RUN]`` prefix — is implemented in N518b.
        Until that lands, ``POST /promotion-review/source/{id_b64}/start``
        will surface a 500 with this message; that is the documented
        N518a known-limitation.
        """
        raise NotImplementedError("DryRunClaimExtractor.extract: full impl deferred to N518b")
