"""Tests for ``shared.entity_promotion_engine`` (ADR-034 v2 PR2b-ii).

Covers:
- All 7 action policy rows
- Person + Organization variant routing (metadata preserved)
- Matcher failure → ``error`` state (items=[], E7 invariant)
- Empty / blank label → exclude (no matcher call)
- Cross-lingual uncertainty risk aggregation
- Engine boundary: no LLM imports (smoke check)
"""

from __future__ import annotations

from typing import Iterable

import pytest

from shared.entity_promotion_engine import (
    EntityPromotionEngine,
    KBEntityIndex,
)
from shared.schemas.entity_promotion import (
    EntityCandidate,
    EntityMatchOutcome,
    KBEntityEntry,
)
from shared.schemas.promotion_manifest import (
    EntityCanonicalMatch,
    EntityReviewItem,
    OrganizationMetadata,
    PersonMetadata,
)
from shared.schemas.reading_source import ReadingSource, SourceVariant

# ── Fixtures ────────────────────────────────────────────────────────────────


def _reading_source(primary_lang: str = "en") -> ReadingSource:
    # Engine schema only constrains source_id / primary_lang transport
    # semantics. Tests use ``inbox_document`` + markdown variant as the
    # cheapest valid construction; real callers (future YouTube/Podcast
    # reader) extend the SourceKind / VariantFormat enums per #509 N6
    # closed-set extension protocol before passing real podcast sources in.
    return ReadingSource(
        source_id="inbox:Inbox/transcripts/hubermanlab-ep-123.md",
        annotation_key="hubermanlab-ep-123",
        kind="inbox_document",
        title="Huberman Lab Ep 123",
        author="Andrew Huberman",
        primary_lang=primary_lang,
        has_evidence_track=True,
        evidence_reason=None,
        variants=[
            SourceVariant(
                role="original",
                format="markdown",
                lang=primary_lang,
                path="Inbox/transcripts/hubermanlab-ep-123.md",
            ),
        ],
        metadata={},
    )


def _person_candidate(
    *,
    candidate_id: str = "cand-person-1",
    label: str = "Andrew Huberman",
    aliases: list[str] | None = None,
    raw_quotes: list[str] | None = None,
    source_refs: list[str] | None = None,
) -> EntityCandidate:
    return EntityCandidate(
        candidate_id=candidate_id,
        entity_type="person",
        label=label,
        aliases=[] if aliases is None else aliases,
        evidence_language="en",
        source_refs=["00:12:34"] if source_refs is None else source_refs,
        raw_quotes=(
            ["Huberman discussing dopamine baselines."] if raw_quotes is None else raw_quotes
        ),
        metadata=PersonMetadata(affiliation="Stanford University", role="Professor"),
    )


def _org_candidate(
    *,
    candidate_id: str = "cand-org-1",
    label: str = "Stanford University",
    raw_quotes: list[str] | None = None,
) -> EntityCandidate:
    return EntityCandidate(
        candidate_id=candidate_id,
        entity_type="organization",
        label=label,
        evidence_language="en",
        source_refs=["00:20:00"],
        raw_quotes=raw_quotes or ["affiliated with Stanford University."],
        metadata=OrganizationMetadata(org_type="academic", jurisdiction="US"),
    )


# ── Fake matcher / kb index ─────────────────────────────────────────────────


class _FakeKBEntityIndex:
    """Read-only fixture; engine only calls ``lookup`` / ``aliases_starting_with``
    through the matcher (Protocol structural typing means we don't need to
    inherit)."""

    def __init__(self, entries: Iterable[KBEntityEntry] = ()) -> None:
        self._entries = list(entries)

    def lookup(self, alias: str) -> KBEntityEntry | None:
        for entry in self._entries:
            if alias in entry.aliases or alias == entry.canonical_label:
                return entry
        return None

    def aliases_starting_with(self, prefix: str) -> list[str]:
        out: list[str] = []
        for entry in self._entries:
            for a in entry.aliases:
                if a.startswith(prefix):
                    out.append(a)
        return out


class _FixedMatcher:
    """Returns the same ``EntityMatchOutcome`` regardless of input."""

    def __init__(self, outcome: EntityMatchOutcome) -> None:
        self._outcome = outcome

    def match(
        self,
        candidate: EntityCandidate,
        kb_index: KBEntityIndex,
        primary_lang: str,
    ) -> EntityMatchOutcome:
        return self._outcome


class _RaisingMatcher:
    """Always raises — drives E6/E7 failure path."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def match(self, candidate, kb_index, primary_lang):  # noqa: ARG002, ANN001
        raise self._exc


# ── Row tests ───────────────────────────────────────────────────────────────


def test_row1_blank_label_excludes_candidate_with_no_matcher_call() -> None:
    """Row 1: empty/whitespace label → exclude (no matcher call)."""

    class _AssertNotCalledMatcher:
        def match(self, *args, **kwargs):  # noqa: ANN002, ANN003, ARG002
            raise AssertionError("matcher should not be called for blank label")

    candidate = _person_candidate(candidate_id="cand-blank", label="   ")
    engine = EntityPromotionEngine()
    result = engine.propose(
        _reading_source(),
        [candidate],
        _FakeKBEntityIndex(),
        _AssertNotCalledMatcher(),
    )
    assert result.candidates_received == 1
    assert result.items == []
    assert result.error is None


def test_row2_exact_alias_high_conf_clean_routes_to_update_merge() -> None:
    matcher = _FixedMatcher(
        EntityMatchOutcome(
            canonical_match=EntityCanonicalMatch(
                match_basis="exact_alias",
                matched_entity_path="KB/Wiki/Entities/People/Andrew Huberman.md",
                confidence=0.95,
            ),
            conflict_signals=[],
        )
    )
    engine = EntityPromotionEngine()
    result = engine.propose(_reading_source(), [_person_candidate()], _FakeKBEntityIndex(), matcher)
    assert len(result.items) == 1
    item = result.items[0]
    assert isinstance(item, EntityReviewItem)
    assert item.action == "update_merge_entity"
    assert item.recommendation == "include"
    assert item.metadata.entity_type == "person"
    assert item.canonical_match is not None
    assert item.canonical_match.matched_entity_path == "KB/Wiki/Entities/People/Andrew Huberman.md"


def test_row3_exact_alias_with_conflict_routes_to_update_conflict() -> None:
    matcher = _FixedMatcher(
        EntityMatchOutcome(
            canonical_match=EntityCanonicalMatch(
                match_basis="exact_alias",
                matched_entity_path="KB/Wiki/Entities/People/Andrew Huberman.md",
                confidence=0.95,
            ),
            conflict_signals=["affiliation diverges"],
        )
    )
    engine = EntityPromotionEngine()
    result = engine.propose(_reading_source(), [_person_candidate()], _FakeKBEntityIndex(), matcher)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.action == "update_conflict_entity"
    assert item.recommendation == "defer"
    assert len(item.risk) == 1
    assert item.risk[0].code == "other"
    assert "entity_metadata_conflict" in item.risk[0].description


def test_row4_semantic_high_conf_clean_routes_to_update_merge() -> None:
    matcher = _FixedMatcher(
        EntityMatchOutcome(
            canonical_match=EntityCanonicalMatch(
                match_basis="semantic",
                matched_entity_path="KB/Wiki/Entities/People/Andrew Huberman.md",
                confidence=0.80,
            ),
            conflict_signals=[],
        )
    )
    engine = EntityPromotionEngine()
    result = engine.propose(_reading_source(), [_person_candidate()], _FakeKBEntityIndex(), matcher)
    assert len(result.items) == 1
    assert result.items[0].action == "update_merge_entity"


def test_row5_semantic_below_threshold_above_drop_routes_to_conflict_defer() -> None:
    matcher = _FixedMatcher(
        EntityMatchOutcome(
            canonical_match=EntityCanonicalMatch(
                match_basis="translation",
                matched_entity_path="KB/Wiki/Entities/People/Andrew Huberman.md",
                confidence=0.60,
            ),
            conflict_signals=[],
        )
    )
    engine = EntityPromotionEngine()
    result = engine.propose(_reading_source(), [_person_candidate()], _FakeKBEntityIndex(), matcher)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.action == "update_conflict_entity"
    assert item.recommendation == "defer"
    # Cross-lingual uncertainty aggregated to engine.risks
    assert any(r.code == "cross_lingual_uncertain" for r in result.risks)


def test_row6_semantic_below_drop_threshold_excludes() -> None:
    matcher = _FixedMatcher(
        EntityMatchOutcome(
            canonical_match=EntityCanonicalMatch(
                match_basis="translation",
                matched_entity_path="KB/Wiki/Entities/People/Andrew Huberman.md",
                confidence=0.40,
            ),
            conflict_signals=[],
        )
    )
    engine = EntityPromotionEngine()
    result = engine.propose(_reading_source(), [_person_candidate()], _FakeKBEntityIndex(), matcher)
    assert result.items == []
    # Risk not aggregated below drop threshold.
    assert not result.risks


def test_row7_match_basis_none_routes_to_create_entity() -> None:
    matcher = _FixedMatcher(
        EntityMatchOutcome(
            canonical_match=EntityCanonicalMatch(
                match_basis="none",
                matched_entity_path=None,
                confidence=0.0,
            ),
            conflict_signals=[],
        )
    )
    engine = EntityPromotionEngine()
    result = engine.propose(_reading_source(), [_person_candidate()], _FakeKBEntityIndex(), matcher)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.action == "create_entity"
    assert item.recommendation == "include"  # has evidence
    assert item.canonical_match is None


def test_row7_create_entity_without_quotes_downgrades_to_defer() -> None:
    """E3 + V1 invariant: include requires evidence; engine downgrades to
    defer when raw_quotes empty so create_entity items never violate."""
    matcher = _FixedMatcher(
        EntityMatchOutcome(
            canonical_match=EntityCanonicalMatch(
                match_basis="none",
                matched_entity_path=None,
                confidence=0.0,
            ),
            conflict_signals=[],
        )
    )
    candidate = _person_candidate(raw_quotes=[], source_refs=[])
    engine = EntityPromotionEngine()
    result = engine.propose(_reading_source(), [candidate], _FakeKBEntityIndex(), matcher)
    assert len(result.items) == 1
    assert result.items[0].recommendation == "defer"


# ── Variant routing ─────────────────────────────────────────────────────────


def test_organization_candidate_yields_organization_metadata_in_item() -> None:
    matcher = _FixedMatcher(
        EntityMatchOutcome(
            canonical_match=EntityCanonicalMatch(
                match_basis="none",
                matched_entity_path=None,
                confidence=0.0,
            ),
            conflict_signals=[],
        )
    )
    engine = EntityPromotionEngine()
    result = engine.propose(_reading_source(), [_org_candidate()], _FakeKBEntityIndex(), matcher)
    item = result.items[0]
    assert item.metadata.entity_type == "organization"
    assert isinstance(item.metadata, OrganizationMetadata)
    assert item.metadata.org_type == "academic"


def test_candidate_entity_type_metadata_mismatch_rejected_at_construction() -> None:
    """E4 — EntityCandidate validator catches metadata mismatch at
    schema layer, never reaches the engine."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="differs from metadata.entity_type"):
        EntityCandidate(
            candidate_id="cand-mismatch",
            entity_type="organization",
            label="X",
            evidence_language="en",
            source_refs=[],
            raw_quotes=[],
            metadata=PersonMetadata(),  # mismatched variant
        )


# ── Failure paths ───────────────────────────────────────────────────────────


def test_matcher_value_error_routes_to_error_state_with_empty_items() -> None:
    engine = EntityPromotionEngine()
    result = engine.propose(
        _reading_source(),
        [_person_candidate()],
        _FakeKBEntityIndex(),
        _RaisingMatcher(ValueError("bad match")),
    )
    assert result.items == []
    assert result.error is not None
    assert "matcher_failed" in result.error
    assert "ValueError" in result.error


def test_matcher_runtime_error_routes_to_error_state() -> None:
    engine = EntityPromotionEngine()
    result = engine.propose(
        _reading_source(),
        [_person_candidate()],
        _FakeKBEntityIndex(),
        _RaisingMatcher(RuntimeError("transient failure")),
    )
    assert result.items == []
    assert "RuntimeError" in result.error


def test_matcher_programmer_error_propagates() -> None:
    """TypeError / AttributeError signal programmer errors and must
    propagate (narrow tuple excludes them)."""
    engine = EntityPromotionEngine()
    with pytest.raises(TypeError):
        engine.propose(
            _reading_source(),
            [_person_candidate()],
            _FakeKBEntityIndex(),
            _RaisingMatcher(TypeError("programmer bug")),
        )


# ── Result shape ────────────────────────────────────────────────────────────


def test_result_carries_source_id_and_primary_lang_from_reading_source() -> None:
    matcher = _FixedMatcher(
        EntityMatchOutcome(
            canonical_match=EntityCanonicalMatch(
                match_basis="none",
                matched_entity_path=None,
                confidence=0.0,
            ),
            conflict_signals=[],
        )
    )
    engine = EntityPromotionEngine()
    rs = _reading_source(primary_lang="zh-Hant")
    result = engine.propose(rs, [_person_candidate()], _FakeKBEntityIndex(), matcher)
    assert result.source_id == rs.source_id
    assert result.primary_lang == "zh-Hant"
    assert result.candidates_received == 1


def test_multiple_candidates_each_yield_one_item_in_order() -> None:
    matcher = _FixedMatcher(
        EntityMatchOutcome(
            canonical_match=EntityCanonicalMatch(
                match_basis="none",
                matched_entity_path=None,
                confidence=0.0,
            ),
            conflict_signals=[],
        )
    )
    candidates = [
        _person_candidate(candidate_id="cand-1", label="A"),
        _org_candidate(candidate_id="cand-2", label="B"),
        _person_candidate(candidate_id="cand-3", label="C"),
    ]
    engine = EntityPromotionEngine()
    result = engine.propose(_reading_source(), candidates, _FakeKBEntityIndex(), matcher)
    assert [item.item_id for item in result.items] == ["cand-1", "cand-2", "cand-3"]
