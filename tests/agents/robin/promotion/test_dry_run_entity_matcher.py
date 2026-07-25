"""Tests for ``agents.robin.promotion.dry_run_entity_matcher`` (ADR-034 v2 PR3)."""

from __future__ import annotations

from agents.robin.promotion.dry_run_entity_matcher import DryRunEntityMatcher
from shared.schemas.entity_promotion import EntityCandidate
from shared.schemas.promotion_manifest import PersonMetadata


def _candidate() -> EntityCandidate:
    return EntityCandidate(
        candidate_id="cand-1",
        entity_type="person",
        label="Andrew Huberman",
        evidence_language="en",
        source_refs=["00:12:34"],
        raw_quotes=["x"],
        metadata=PersonMetadata(),
    )


def test_dry_run_returns_match_basis_none_and_zero_confidence() -> None:
    matcher = DryRunEntityMatcher()
    outcome = matcher.match(_candidate(), kb_index=None, primary_lang="en")
    assert outcome.canonical_match.match_basis == "none"
    assert outcome.canonical_match.confidence == 0.0
    assert outcome.canonical_match.matched_entity_path is None
    assert outcome.conflict_signals == []


def test_dry_run_is_deterministic_byte_identical() -> None:
    matcher = DryRunEntityMatcher()
    candidate = _candidate()
    a = matcher.match(candidate, kb_index=None, primary_lang="en")
    b = matcher.match(candidate, kb_index=None, primary_lang="en")
    assert a == b


def test_dry_run_ignores_kb_index_and_primary_lang() -> None:
    """Protocol shape accepts both args but dry-run never consults them.
    Smoke test that pathological values don't blow up."""
    matcher = DryRunEntityMatcher()
    outcome = matcher.match(
        _candidate(),
        kb_index="not-an-index",  # type: ignore[arg-type]
        primary_lang="",
    )
    assert outcome.canonical_match.match_basis == "none"
