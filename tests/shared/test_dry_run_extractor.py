"""Tests for ``shared.dry_run_extractor.DryRunClaimExtractor`` (N518b).

Brief §5 / §8 acceptance:

- AT16 ``extract(same_inputs)`` returns byte-identical lists across calls.
- AT17 returned claim count is in ``[1, 2, 3]`` and varies across distinct
  inputs (probabilistic spread test against ≥ 5 source-id-shaped seeds).
- AT18 every claim text starts with ``[DRY-RUN] ``.
- Plus: each entry is a valid ``ClaimExtractionResult`` (schema shape),
  no ``anthropic`` import (covered by WT10 in app startup tests).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys

import pytest

from shared.dry_run_extractor import DryRunClaimExtractor
from shared.schemas.source_map import ClaimExtractionResult

# ── AT16 — determinism across calls ─────────────────────────────────────────


def test_at16_extract_is_byte_identical_across_calls():
    """Same inputs → identical output across two calls. Covers W2 / brief
    §6 boundary 3 — dry-run extractor is a pure function."""
    extractor = DryRunClaimExtractor()
    chapter_text = "Some chapter prose with claims about HRV and sleep."
    chapter_title = "Chapter 1: Heart Rate Variability"

    first = extractor.extract(chapter_text, chapter_title, primary_lang="en")
    second = extractor.extract(chapter_text, chapter_title, primary_lang="en")

    # Pydantic model_dump is the easiest way to compare deeply-frozen values.
    assert first.model_dump() == second.model_dump()


def test_at16_extract_deterministic_across_instances():
    """A fresh instance produces the same output. Stateless contract."""
    chapter_text = "Repeated sleep below 5 hours raises mortality risk."
    chapter_title = "Sleep Pillars"
    a = DryRunClaimExtractor()
    b = DryRunClaimExtractor()
    assert (
        a.extract(chapter_text, chapter_title, "en").model_dump()
        == b.extract(chapter_text, chapter_title, "en").model_dump()
    )


def test_at16_different_titles_produce_different_output():
    """Determinism doesn't mean constant — different inputs → different
    outputs. Otherwise the dry-run mode would render identical claims for
    every chapter, which would be confusing in the UI."""
    extractor = DryRunClaimExtractor()
    text = "common chapter body text"

    first = extractor.extract(text, "Chapter 1", "en")
    second = extractor.extract(text, "Chapter 2", "en")

    # At least one of (count, claim text, quote excerpt) must differ.
    same_count = len(first.claims) == len(second.claims)
    same_claims = first.claims == second.claims
    if same_count and same_claims:
        pytest.fail(
            "different chapter titles produced identical claim lists; "
            "dry-run determinism collapsed across distinct inputs"
        )


# ── AT17 — bounded count + spread ───────────────────────────────────────────


def test_at17_count_is_within_one_to_three():
    """Hash-seeded count ∈ [1, 3]. Tested across 10 distinct seeds so any
    off-by-one in the modulo is surfaced."""
    extractor = DryRunClaimExtractor()
    for i in range(10):
        title = f"Chapter {i}"
        text = f"Body text for chapter {i}; lorem ipsum about claims."
        result = extractor.extract(text, title, "en")
        assert 1 <= len(result.claims) <= 3, (
            f"chapter {i!r} returned {len(result.claims)} claims; expected 1-3"
        )


def test_at17_count_varies_across_distinct_source_ids():
    """Across ≥ 5 distinct source-id-shaped seeds, the claim count should
    not be a single constant value. Probabilistic spread test — a uniform
    modulo against 3 should produce ≥ 2 distinct counts in 5 samples with
    high probability. If this test flakes, the extractor's seed entropy
    is broken."""
    extractor = DryRunClaimExtractor()
    counts: set[int] = set()
    sample_titles = [
        "Chapter 1: Sleep",
        "Chapter 2: HRV",
        "Chapter 3: Glucose",
        "Chapter 4: Mitochondria",
        "Chapter 5: Cold Exposure",
        "Chapter 6: Vitamin D",
    ]
    for title in sample_titles:
        text = f"Body text for {title.lower()}."
        counts.add(len(extractor.extract(text, title, "en").claims))

    # We require ≥ 2 distinct counts. Stronger spread (≥ 3) is desired but
    # making it exact = 3 turns the test brittle against any change to
    # the hash seed; ≥ 2 is enough to catch "always returns N claims" bugs.
    assert len(counts) >= 2, f"only saw counts={counts!r} across {len(sample_titles)} samples"


# ── AT18 — every claim starts with [DRY-RUN] ────────────────────────────────


def test_at18_every_claim_has_dry_run_prefix():
    """Every emitted ``claim`` string starts with ``[DRY-RUN] ``. The review
    UI relies on this prefix to flag dry-run output visually."""
    extractor = DryRunClaimExtractor()
    for i in range(10):
        title = f"Chapter {i}"
        text = f"Body text for chapter {i}."
        result = extractor.extract(text, title, "en")
        for claim in result.claims:
            assert claim.startswith("[DRY-RUN] "), (
                f"chapter {i} produced claim without dry-run prefix: {claim!r}"
            )


def test_at18_dry_run_prefix_for_empty_chapter_text():
    """Empty chapter text still gets dry-run-prefixed claims; the synthetic
    quote anchor falls back to a placeholder excerpt so #513 doesn't end up
    with an empty evidence list it can't anchor against."""
    extractor = DryRunClaimExtractor()
    result = extractor.extract("", "Empty Chapter", "en")

    assert all(c.startswith("[DRY-RUN] ") for c in result.claims)
    # The synthetic quote falls back to a placeholder rather than crashing.
    assert len(result.short_quotes) >= 1
    # Quote excerpt is non-empty string per QuoteAnchor semantics.
    assert result.short_quotes[0].excerpt != ""


# ── Schema shape ────────────────────────────────────────────────────────────


def test_extract_returns_valid_claim_extraction_result():
    """The dry-run extractor returns a ``ClaimExtractionResult`` (frozen
    pydantic value-object) — not a dict, not a list."""
    extractor = DryRunClaimExtractor()
    result = extractor.extract("body text", "title", "en")
    assert isinstance(result, ClaimExtractionResult)


def test_extract_result_extraction_confidence_in_unit_interval():
    """Confidence is bounded to [0.0, 1.0] per ``ClaimExtractionResult``
    schema. The dry-run body uses a fixed midpoint."""
    extractor = DryRunClaimExtractor()
    result = extractor.extract("body text", "title", "en")
    assert 0.0 <= result.extraction_confidence <= 1.0


def test_extract_short_quotes_have_dry_run_locator():
    """Synthetic quote uses a ``"dry-run"`` locator string. Mirror of the
    ``[DRY-RUN]`` prefix on claims so the UI can identify dry-run quote
    anchors at a glance even though the schema field is opaque."""
    extractor = DryRunClaimExtractor()
    result = extractor.extract("body text with substance", "Some Chapter", "en")
    assert len(result.short_quotes) >= 1
    assert result.short_quotes[0].locator == "dry-run"


def test_extract_uses_chapter_title_in_claim_text():
    """The chapter title is rendered into each claim so 修修 can correlate
    dry-run claims with chapters in the review UI."""
    extractor = DryRunClaimExtractor()
    title = "Mitochondrial Biogenesis Cascade"
    result = extractor.extract("body", title, "en")
    # At least one claim should reference the title (or a meaningful
    # substring of it). Templates render with the full title.
    assert any(title in c for c in result.claims)


# ── Subprocess gate — no anthropic import ───────────────────────────────────


def test_dry_run_extractor_module_does_not_import_anthropic():
    """Subprocess: import ``shared.dry_run_extractor`` and assert
    ``anthropic`` is not in ``sys.modules``. Mirrors WT10 from the app
    startup tests; we duplicate it here so the dry-run module's invariant
    is asserted in this file's surface too."""
    code = (
        "import sys, importlib;"
        "importlib.import_module('shared.dry_run_extractor');"
        "assert 'anthropic' not in sys.modules, "
        "'dry_run_extractor pulled anthropic into sys.modules'; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)


# ── Stability under hash collisions ─────────────────────────────────────────


def test_extract_stable_against_known_digest():
    """Pin a known input → known output (sanity check that the seed bytes
    haven't drifted). If this test fails, the extractor's hash algorithm
    or template pool changed — that's a behaviour change worth surfacing
    in code review (the test is intentionally brittle to deliberate edits).
    """
    extractor = DryRunClaimExtractor()
    text = "fixed prose"
    title = "Fixed Title"
    digest = hashlib.sha256((text + "\x00" + title).encode("utf-8")).digest()
    expected_count = (digest[0] % 3) + 1

    result = extractor.extract(text, title, "en")
    assert len(result.claims) == expected_count
