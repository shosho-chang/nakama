"""Tests for ``shared.dry_run_matcher.DryRunConceptMatcher`` (N518b).

Brief §5 / §8 acceptance:

- AT19 every match returns "no global match" semantics + confidence ≤
  threshold (we use ≤ 0.1 as the "low confidence" gate).
- Plus: deterministic across calls; pure function; no side effects;
  no ``anthropic`` import.
"""

from __future__ import annotations

import subprocess
import sys

from shared.dry_run_matcher import DryRunConceptMatcher
from shared.schemas.concept_promotion import ConceptCandidate, MatchOutcome


def _make_candidate(label: str = "HRV") -> ConceptCandidate:
    """Helper — minimal ``ConceptCandidate`` for matcher input."""
    return ConceptCandidate(
        candidate_id="cand_001_test",
        label=label,
        aliases=[],
        evidence_language="en",
        chapter_refs=["ch-1"],
        raw_quotes=["a quoted excerpt about heart rate variability"],
    )


# ── AT19 — no global match + low confidence ─────────────────────────────────


def test_at19_match_returns_no_global_match():
    """Every dry-run match has ``match_basis="none"`` — the engine routes
    to the "no global match" rows of the action policy (Brief §4.2)."""
    matcher = DryRunConceptMatcher()
    candidate = _make_candidate()
    result = matcher.match(candidate, kb_index=None, primary_lang="en")

    assert result.canonical_match.match_basis == "none"
    # V10 invariant on CanonicalMatch: basis="none" ⇒ matched_concept_path is None.
    assert result.canonical_match.matched_concept_path is None


def test_at19_match_confidence_is_low():
    """Confidence ≤ 0.1 — the engine treats this as below the auto-promote
    thresholds and routes through human review."""
    matcher = DryRunConceptMatcher()
    result = matcher.match(_make_candidate(), kb_index=None, primary_lang="en")

    assert result.canonical_match.confidence <= 0.1


def test_at19_match_has_no_conflict_signals():
    """Empty conflict_signals — there's nothing to conflict against in the
    dry-run mode. The engine relies on this to avoid routing to
    ``update_conflict_global`` rows that wouldn't make sense without a real
    matched concept."""
    matcher = DryRunConceptMatcher()
    result = matcher.match(_make_candidate(), kb_index=None, primary_lang="en")
    assert result.conflict_signals == []


# ── Determinism + statelessness ─────────────────────────────────────────────


def test_match_is_deterministic_across_calls():
    """Same candidate → same outcome across two calls. Pure function."""
    matcher = DryRunConceptMatcher()
    candidate = _make_candidate("Vitamin D")
    a = matcher.match(candidate, kb_index=None, primary_lang="en")
    b = matcher.match(candidate, kb_index=None, primary_lang="en")
    assert a.model_dump() == b.model_dump()


def test_match_is_constant_across_distinct_candidates():
    """Different candidates also return equivalent outcomes — the dry-run
    policy is "always uncertain" regardless of input shape. Different
    instances of MatchOutcome compare equal because the underlying values
    are identical."""
    matcher = DryRunConceptMatcher()
    a = matcher.match(_make_candidate("HRV"), kb_index=None, primary_lang="en")
    b = matcher.match(_make_candidate("Glucose"), kb_index=None, primary_lang="zh-Hant")
    assert a.model_dump() == b.model_dump()


def test_match_returns_match_outcome():
    """Return type is ``MatchOutcome`` (frozen pydantic) — not a dict,
    not a tuple."""
    matcher = DryRunConceptMatcher()
    result = matcher.match(_make_candidate(), kb_index=None, primary_lang="en")
    assert isinstance(result, MatchOutcome)


def test_match_does_not_consult_kb_index():
    """The dry-run matcher accepts a kb_index param to satisfy the
    Protocol but must NOT call it. We pass a sentinel that would crash
    on any attribute access — if the matcher reached for ``.lookup`` or
    ``.aliases_starting_with``, this test fails."""

    class _RaisingIndex:
        def __getattr__(self, name):
            raise AssertionError(
                f"DryRunConceptMatcher consulted kb_index ({name!r}) — "
                f"the dry-run mode must not depend on KB content"
            )

    matcher = DryRunConceptMatcher()
    # If the matcher touches kb_index, the assertion above fires.
    matcher.match(_make_candidate(), kb_index=_RaisingIndex(), primary_lang="en")


# ── No anthropic import ─────────────────────────────────────────────────────


def test_dry_run_matcher_module_does_not_import_anthropic():
    """Subprocess: import ``shared.dry_run_matcher`` and assert
    ``anthropic`` is not in ``sys.modules``."""
    code = (
        "import sys, importlib;"
        "importlib.import_module('shared.dry_run_matcher');"
        "assert 'anthropic' not in sys.modules, "
        "'dry_run_matcher pulled anthropic into sys.modules'; "
        "print('OK')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
