"""Dry-run ``ClaimExtractor`` (ADR-024 Slice 10 / N518b).

Deterministic, non-LLM placeholder implementation of the ``ClaimExtractor``
Protocol declared in ``shared.source_map_builder`` (#513). Used by the
production wiring when ``NAKAMA_PROMOTION_MODE=dry_run`` (the default in
N518) so the promotion review surface can be exercised end-to-end without
any LLM call, network, or API key.

**Why this exists.** The N518a wiring landed disk adapters + service
construction; the previous body of this module was a STUB raising
``NotImplementedError`` so ``POST /promotion-review/.../start`` returned
500 with a clear deferred-to-N518b message. N518b replaces that STUB with
a deterministic body so 修修 can exercise the full flow against fixture
data. The full LLM-backed extractor lands in N519 behind the same
``NAKAMA_PROMOTION_MODE`` gate.

Determinism contract (W2 / brief §6 boundary 3 / AT16-AT18):

- ``extract(chapter_text, chapter_title, primary_lang)`` is a pure function
  of its inputs. Same inputs → byte-identical output across calls.
- The output is seeded by ``hashlib.sha256(chapter_text + chapter_title)``;
  no ``random.Random()`` without explicit seed, no timestamp, no env reads.
- Each emitted ``claim`` string starts with ``"[DRY-RUN] "`` so the review
  UI can surface the dry-run mode visually (every claim is clearly marked
  as placeholder, never silently confused with real LLM output).
- Output count is bounded to 1-3 claims per call; the exact count is also
  hash-derived so different chapter texts yield a measurable spread (AT17).
- NO ``anthropic`` import (W2 / WT10 subprocess gate).
"""

from __future__ import annotations

import hashlib

from shared.schemas.source_map import ClaimExtractionResult, QuoteAnchor

# Fixed pool of dry-run claim templates. Keeping these short + topic-neutral
# so they don't pollute review UI with misleading "real-looking" claims.
# The template is rendered with the chapter title so each chapter still gets
# a recognizable claim — useful when 修修 is sanity-checking the wiring.
_CLAIM_TEMPLATES: tuple[str, ...] = (
    "Placeholder claim referencing {title} (no LLM ran in dry-run mode).",
    "Synthetic claim derived from {title} — dry-run extractor produced this without LLM.",
    "Dry-run filler claim for {title}; awaiting LLM-backed extractor in N519.",
    "Stub claim from chapter {title}; review UI exercises end-to-end in dry-run.",
    "Deterministic dry-run claim about {title}; no anthropic call was made.",
    "Fixture-shaped claim from {title}, hash-seeded per N518b dry-run mode.",
)
"""Pool of claim templates the dry-run body picks from. The hash seed selects
which entries appear and in which order. Pool size is deliberately larger
than the max output (3) so the spread test (AT17) sees variety across
different ``source_id`` inputs."""

_DRY_RUN_PREFIX = "[DRY-RUN] "
"""Marker prepended to every emitted claim text so the review UI can
visually flag dry-run output. This is also asserted by AT18."""

_MAX_CLAIMS_PER_CHAPTER = 3
"""Hard cap on emitted claim count per ``extract()`` call. The N518 brief
§4.1 states "1-3 fixture-shaped ClaimRecords per chapter"; the hash-seeded
count picks an integer in ``[1, _MAX_CLAIMS_PER_CHAPTER]``."""


class DryRunClaimExtractor:
    """Deterministic dry-run ``ClaimExtractor`` (no LLM, no network).

    Production wiring (``thousand_sunny.app`` lifespan → ``promotion_wiring``)
    constructs this class for ``NAKAMA_PROMOTION_MODE=dry_run`` (the default
    in N518). The full LLM-backed extractor lands in N519 behind the same
    config gate.

    Stateless — no constructor arguments, no per-instance state. Same input
    text always produces the same output across calls AND across instances.
    """

    def extract(
        self,
        chapter_text: str,
        chapter_title: str,
        primary_lang: str,
    ) -> ClaimExtractionResult:
        """Return 1-3 deterministic dry-run claims for ``chapter_text``.

        Algorithm:

        1. Hash ``chapter_text`` + ``chapter_title`` with SHA-256.
        2. Use bytes 0-3 of the digest as the claim count (mod
           ``_MAX_CLAIMS_PER_CHAPTER`` plus 1) → integer in ``[1, 3]``.
        3. Use byte 4+ groups to pick template indices into ``_CLAIM_TEMPLATES``.
        4. Render each template with ``chapter_title`` and prefix ``[DRY-RUN] ``.
        5. Build a single ``QuoteAnchor`` from the leading 200 chars of
           chapter text so downstream long-source layout has at least one
           evidence anchor (otherwise every item routes to ``defer`` with
           empty evidence — fine for tests but masks the wiring).

        Output is a frozen ``ClaimExtractionResult``; ``primary_lang`` is
        not used for shaping (the dry-run body is language-agnostic;
        language-aware behaviour lands with the LLM extractor in N519).
        """
        digest = hashlib.sha256((chapter_text + "\x00" + chapter_title).encode("utf-8")).digest()

        claim_count = (digest[0] % _MAX_CLAIMS_PER_CHAPTER) + 1

        # Pick distinct templates by walking digest bytes; modulo pool size,
        # de-duplicate to avoid two identical claims per chapter.
        chosen_indices: list[int] = []
        cursor = 1
        while len(chosen_indices) < claim_count and cursor < len(digest):
            idx = digest[cursor] % len(_CLAIM_TEMPLATES)
            if idx not in chosen_indices:
                chosen_indices.append(idx)
            cursor += 1
        # Fallback: if dedup left us short (unlikely with 32-byte digest),
        # cycle through the pool deterministically to fill the remainder.
        fallback_cursor = 0
        while len(chosen_indices) < claim_count:
            if fallback_cursor not in chosen_indices:
                chosen_indices.append(fallback_cursor)
            fallback_cursor += 1

        title = chapter_title.strip() or "(untitled)"
        claims = [
            _DRY_RUN_PREFIX + _CLAIM_TEMPLATES[idx].format(title=title) for idx in chosen_indices
        ]

        # One synthetic quote per call so #513's _quotes_to_evidence has
        # something to anchor on. Locator is "dry-run" — #513 schema treats
        # locator as opaque and the dry-run flag is already on the claim
        # text, so this won't be confused with a real CFI / line range.
        excerpt = (chapter_text or "").strip()[:200]
        if not excerpt:
            excerpt = f"{_DRY_RUN_PREFIX}empty chapter ({title})"
        short_quotes = [
            QuoteAnchor(
                excerpt=excerpt,
                locator="dry-run",
                confidence=0.5,
            )
        ]

        # Confidence is a fixed midpoint — the review UI shows it but
        # nothing should auto-approve based on dry-run confidence anyway.
        return ClaimExtractionResult(
            claims=claims,
            key_numbers=[],
            figure_summaries=[],
            table_summaries=[],
            short_quotes=short_quotes,
            extraction_confidence=0.5,
        )
