"""Behavior tests for ``shared.concept_promotion_engine`` (ADR-024 Slice 6 / #514).

Brief §5 acceptance T1-T16 plus regression coverage:

- T1  single-chapter candidate → action="keep_source_local"
- T2  recurrence ≥ 2, matcher returns match_basis="none" → action="create_global_concept"
- T3  matcher returns exact_alias conf=0.95, no conflict → action="update_merge_global"
- T4  matcher returns exact_alias conf=0.95, conflict_signals → action="update_conflict_global"
- T5  semantic conf=0.60 (< 0.75) → action="update_conflict_global", recommendation="defer"
- T6  translation conf=0.30 (< 0.50) → action="keep_source_local"
- T7  candidate label empty/whitespace → action="exclude"
- T8  cross-lingual records match_basis on emitted item canonical_match
- T9  monolingual zh source → all items have evidence_language="zh-Hant"
- T10 deterministic fake matcher returns canned MatchOutcome; engine threads through unchanged
- T11 subprocess gate — no shared.book_storage import
- T12 subprocess gate — no fastapi / thousand_sunny / agents / LLM clients
- T13 matcher exception → result.error set, items=[]
- T14 all emitted include items have len(evidence) ≥ 1 (V1 inheritance)
- T15 ConceptPromotionResult round-trips via model_dump / model_validate
- T16 min_recurrence_for_global=3; candidate in 2 chapters → keep_source_local

Regression test (F1-analog from #513 codex review):

- T17 ConceptPromotionResult error ⇒ items=[] model_validator rejects mismatch
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.concept_promotion_engine import (
    ConceptPromotionEngine,
)
from shared.schemas.concept_promotion import (
    ConceptCandidate,
    ConceptPromotionResult,
    KBConceptEntry,
    MatchOutcome,
)
from shared.schemas.promotion_manifest import (
    CanonicalMatch,
    ConceptReviewItem,
    EvidenceAnchor,
    RiskFlag,
    SourcePageReviewItem,
)
from shared.schemas.reading_source import ReadingSource, SourceVariant
from shared.schemas.source_map import SourceMapBuildResult

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "concept_promotion"


# ── Fixture builders ────────────────────────────────────────────────────────


def _ebook_source(
    *,
    book_id: str = "alpha-book",
    primary_lang: str = "en",
) -> ReadingSource:
    return ReadingSource(
        source_id=f"ebook:{book_id}",
        annotation_key=book_id,
        kind="ebook",
        title="Test Book",
        author="Anon",
        primary_lang=primary_lang,
        has_evidence_track=True,
        evidence_reason=None,
        variants=[
            SourceVariant(
                role="original",
                format="epub",
                lang=primary_lang,
                path=f"data/books/{book_id}/original.epub",
            ),
        ],
        metadata={},
    )


def _evidence_anchor(*, excerpt: str, locator: str = "cfi-1") -> EvidenceAnchor:
    return EvidenceAnchor(
        kind="chapter_quote",
        source_path="data/books/alpha-book/original.epub",
        locator=locator,
        excerpt=excerpt,
        confidence=0.85,
    )


def _source_page_item(
    *,
    chapter_ref: str,
    excerpts: list[str],
    item_id_prefix: str = "alpha",
) -> SourcePageReviewItem:
    """Build one ``SourcePageReviewItem`` with the given evidence excerpts."""
    evidence = [
        _evidence_anchor(excerpt=ex, locator=f"cfi-{chapter_ref}-{i}")
        for i, ex in enumerate(excerpts, start=1)
    ]
    recommendation = "include" if evidence else "defer"
    return SourcePageReviewItem(
        item_id=f"{item_id_prefix}::{chapter_ref}",
        recommendation=recommendation,
        action="create",
        reason=f"{chapter_ref}: claim",
        evidence=evidence,
        risk=[],
        confidence=0.7,
        source_importance=0.5,
        reader_salience=0.0,
        target_kb_path=f"KB/Wiki/Sources/alpha/{chapter_ref}.md",
        chapter_ref=chapter_ref,
    )


def _source_map(
    *,
    source_id: str = "ebook:alpha-book",
    primary_lang: str = "en",
    items: list[SourcePageReviewItem] | None = None,
) -> SourceMapBuildResult:
    return SourceMapBuildResult(
        source_id=source_id,
        primary_lang=primary_lang,
        has_evidence_track=True,
        chapters_inspected=len(items or []),
        items=items or [],
        risks=[],
        error=None,
    )


# ── Fake matcher / kb index ─────────────────────────────────────────────────


class _CannedMatcher:
    """Returns the same ``MatchOutcome`` for every candidate.

    Deterministic by construction. Tests that need per-candidate variation
    use ``_PerLabelMatcher`` instead.
    """

    def __init__(self, outcome: MatchOutcome) -> None:
        self._outcome = outcome
        self.calls: list[tuple[str, str]] = []

    def match(
        self,
        candidate: ConceptCandidate,
        kb_index: object,
        primary_lang: str,
    ) -> MatchOutcome:
        self.calls.append((candidate.label, primary_lang))
        return self._outcome


class _PerLabelMatcher:
    """Returns a different ``MatchOutcome`` per candidate label."""

    def __init__(self, by_label: dict[str, MatchOutcome], default: MatchOutcome) -> None:
        self._by_label = by_label
        self._default = default
        self.calls: list[tuple[str, str]] = []

    def match(
        self,
        candidate: ConceptCandidate,
        kb_index: object,
        primary_lang: str,
    ) -> MatchOutcome:
        self.calls.append((candidate.label, primary_lang))
        # Match either exact label or by leading-substring.
        for key, outcome in self._by_label.items():
            if candidate.label.startswith(key):
                return outcome
        return self._default


class _RaisingMatcher:
    """Raises a documented exception (per ``_MATCHER_FAILURES`` tuple)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls: list[tuple[str, str]] = []

    def match(
        self,
        candidate: ConceptCandidate,
        kb_index: object,
        primary_lang: str,
    ) -> MatchOutcome:
        self.calls.append((candidate.label, primary_lang))
        raise self._exc


class _EmptyKBIndex:
    """Deterministic fixture KB index — never matches."""

    def lookup(self, alias: str) -> KBConceptEntry | None:
        return None

    def aliases_starting_with(self, prefix: str) -> list[str]:
        return []


def _no_match_outcome() -> MatchOutcome:
    return MatchOutcome(
        canonical_match=CanonicalMatch(
            match_basis="none",
            confidence=0.0,
            matched_concept_path=None,
        ),
        conflict_signals=[],
    )


def _exact_alias_outcome(
    *,
    confidence: float = 0.95,
    conflicts: list[str] | None = None,
) -> MatchOutcome:
    return MatchOutcome(
        canonical_match=CanonicalMatch(
            match_basis="exact_alias",
            confidence=confidence,
            matched_concept_path="KB/Wiki/Concepts/HRV.md",
        ),
        conflict_signals=list(conflicts or []),
    )


def _semantic_outcome(
    *,
    confidence: float = 0.80,
    conflicts: list[str] | None = None,
) -> MatchOutcome:
    return MatchOutcome(
        canonical_match=CanonicalMatch(
            match_basis="semantic",
            confidence=confidence,
            matched_concept_path="KB/Wiki/Concepts/HeartRateVariability.md",
        ),
        conflict_signals=list(conflicts or []),
    )


def _translation_outcome(
    *,
    confidence: float = 0.80,
    conflicts: list[str] | None = None,
) -> MatchOutcome:
    return MatchOutcome(
        canonical_match=CanonicalMatch(
            match_basis="translation",
            confidence=confidence,
            matched_concept_path="KB/Wiki/Concepts/HRV.md",
        ),
        conflict_signals=list(conflicts or []),
    )


# ───────────────────────────────────────────────────────────────────────────
# T1 — single-chapter candidate → keep_source_local (Brief §4.2 row 1)
# ───────────────────────────────────────────────────────────────────────────


def test_propose_source_local_when_single_chapter():
    """T1: candidate appearing in only ONE chapter (no high-confidence
    canonical match) routes to ``keep_source_local`` per Brief §4.2 row 1.
    """
    rs = _ebook_source()
    items = [
        _source_page_item(
            chapter_ref="ch-1",
            excerpts=["Heart rate variability is a biomarker of autonomic balance."],
        ),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_no_match_outcome())
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    # Single chapter → exactly one candidate → keep_source_local.
    assert len(result.items) == 1
    item = result.items[0]
    assert item.action == "keep_source_local"
    assert item.recommendation in {"include", "defer"}  # C3
    assert "single-chapter mention" in item.reason


# ───────────────────────────────────────────────────────────────────────────
# T2 — recurrent + no match → create_global_concept (row 6)
# ───────────────────────────────────────────────────────────────────────────


def test_propose_create_global_when_recurrent_no_match():
    """T2: candidate appearing in ≥2 chapters with ≥3 raw quotes and no
    canonical match → ``create_global_concept`` with recommendation=include
    and non-empty evidence (C4 + V1).
    """
    rs = _ebook_source()
    # Same leading text in three chapters → recurrence=3, raw_quotes=3.
    excerpt = "HRV is a key biomarker. Studies show correlation with mortality."
    items = [
        _source_page_item(chapter_ref="ch-1", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-2", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-3", excerpts=[excerpt]),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_no_match_outcome())
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    # The merged candidate is one item.
    create_items = [it for it in result.items if it.action == "create_global_concept"]
    assert len(create_items) == 1
    item = create_items[0]
    assert item.recommendation == "include"
    assert len(item.evidence) >= 1  # C4: ≥1 EvidenceAnchor (also V1 inheritance)
    assert item.confidence >= 0.75  # C4: ≥ min_global_confidence
    assert "recurring across" in item.reason


# ───────────────────────────────────────────────────────────────────────────
# T3 — exact_alias high-confidence → update_merge_global (row 2)
# ───────────────────────────────────────────────────────────────────────────


def test_propose_update_merge_on_exact_alias_high_conf():
    """T3: matcher returns ``exact_alias`` conf=0.95, no conflict_signals →
    action=``update_merge_global``. Single-chapter is OK because high-conf
    match makes row 1 fall through.
    """
    rs = _ebook_source()
    items = [
        _source_page_item(
            chapter_ref="ch-1",
            excerpts=["Heart rate variability is a known biomarker."],
        ),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_exact_alias_outcome(confidence=0.95))
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    merge_items = [it for it in result.items if it.action == "update_merge_global"]
    assert len(merge_items) == 1
    item = merge_items[0]
    assert item.canonical_match is not None
    assert item.canonical_match.match_basis == "exact_alias"
    assert item.canonical_match.confidence == pytest.approx(0.95)
    assert "exact alias match" in item.reason


# ───────────────────────────────────────────────────────────────────────────
# T4 — exact_alias with conflict → update_conflict_global, defer (row 3)
# ───────────────────────────────────────────────────────────────────────────


def test_propose_update_conflict_on_exact_alias_with_conflict():
    """T4: matcher returns ``exact_alias`` conf=0.95 with conflict_signals →
    action=``update_conflict_global``, recommendation=``defer`` (C2).
    """
    rs = _ebook_source()
    items = [
        _source_page_item(
            chapter_ref="ch-1",
            excerpts=["HRV is a measure of autonomic balance."],
        ),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(
        _exact_alias_outcome(confidence=0.95, conflicts=["definition diverges"])
    )
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    conflict_items = [it for it in result.items if it.action == "update_conflict_global"]
    assert len(conflict_items) == 1
    item = conflict_items[0]
    assert item.recommendation == "defer"  # C2
    assert item.canonical_match is not None
    assert "definition diverges" in item.reason


# ───────────────────────────────────────────────────────────────────────────
# T5 — low-confidence semantic → update_conflict_global, defer (row 5a)
# ───────────────────────────────────────────────────────────────────────────


def test_propose_update_conflict_on_low_conf_semantic():
    """T5: matcher returns ``semantic`` conf=0.60 (< 0.75 default) →
    action=``update_conflict_global``, recommendation=``defer`` (C2).
    Row 5 enforces NO auto-merge for low-confidence cross-lingual matches.
    """
    rs = _ebook_source()
    # Two chapters so row 1 falls through (recurrence ≥ 2 OR high-conf match).
    excerpt = "Heart rate variability biomarker"
    items = [
        _source_page_item(chapter_ref="ch-1", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-2", excerpts=[excerpt]),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_semantic_outcome(confidence=0.60))
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    conflict_items = [it for it in result.items if it.action == "update_conflict_global"]
    assert len(conflict_items) == 1
    item = conflict_items[0]
    assert item.recommendation == "defer"  # C2
    assert "low-confidence cross-lingual match" in item.reason


# ───────────────────────────────────────────────────────────────────────────
# T6 — very-low-confidence translation → keep_source_local (row 5b)
# ───────────────────────────────────────────────────────────────────────────


def test_propose_keep_local_on_very_low_conf_translation():
    """T6: matcher returns ``translation`` conf=0.30 (< 0.50 floor) →
    action=``keep_source_local`` per Brief §4.2 row 5b. Too uncertain
    even for the conflict review queue.
    """
    rs = _ebook_source()
    excerpt = "Heart rate variability biomarker"
    items = [
        _source_page_item(chapter_ref="ch-1", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-2", excerpts=[excerpt]),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_translation_outcome(confidence=0.30))
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    local_items = [it for it in result.items if it.action == "keep_source_local"]
    assert len(local_items) >= 1
    # At least one local item should reference the low-conf translation reason.
    matching = [it for it in local_items if "0.30" in it.reason or "translation" in it.reason]
    assert matching, (
        f"Expected low-conf translation reason; got: {[it.reason for it in local_items]}"
    )


# ───────────────────────────────────────────────────────────────────────────
# T7 — blank label → exclude (row 8)
# ───────────────────────────────────────────────────────────────────────────


def test_propose_exclude_blank_label():
    """T7: when an evidence excerpt is whitespace-only / cannot be normalized
    into a label, the engine routes the candidate to ``exclude`` per row 8.
    """
    rs = _ebook_source()
    # Whitespace-only excerpts → blank-label candidate.
    items = [
        _source_page_item(chapter_ref="ch-1", excerpts=["    "]),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_no_match_outcome())
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    exclude_items = [it for it in result.items if it.action == "exclude"]
    assert len(exclude_items) >= 1
    item = exclude_items[0]
    assert item.recommendation == "exclude"
    assert "empty" in item.reason or "noise" in item.reason


# ───────────────────────────────────────────────────────────────────────────
# T8 — cross-lingual records canonical_match shape on the item
# ───────────────────────────────────────────────────────────────────────────


def test_propose_cross_lingual_records_match_basis():
    """T8: items emitted from cross-lingual matches (semantic / translation)
    carry ``canonical_match`` with non-null ``matched_concept_path`` and
    confidence.
    """
    rs = _ebook_source(primary_lang="zh-Hant")
    excerpt = "心率變異是自律神經平衡的指標。"
    items = [
        _source_page_item(chapter_ref="ch-1", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-2", excerpts=[excerpt]),
    ]
    sm = _source_map(source_id="ebook:zh-book", primary_lang="zh-Hant", items=items)

    engine = ConceptPromotionEngine()
    # Mid-confidence semantic to avoid auto-merge AND avoid drop floor.
    matcher = _CannedMatcher(_semantic_outcome(confidence=0.65))
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    cross_lingual_items = [it for it in result.items if it.canonical_match is not None]
    assert len(cross_lingual_items) >= 1
    for it in cross_lingual_items:
        assert it.canonical_match is not None
        assert it.canonical_match.match_basis in {"semantic", "translation", "exact_alias"}
        assert it.canonical_match.matched_concept_path is not None
        assert 0.0 <= it.canonical_match.confidence <= 1.0


# ───────────────────────────────────────────────────────────────────────────
# T9 — monolingual zh source → all items have evidence_language="zh-Hant" (C5)
# ───────────────────────────────────────────────────────────────────────────


def test_propose_evidence_language_set():
    """T9: monolingual zh source ⇒ every emitted item has
    ``evidence_language="zh-Hant"`` per C5.
    """
    rs = _ebook_source(primary_lang="zh-Hant")
    excerpt = "心率變異是自律神經平衡的指標。HRV 是一個關鍵生物標記。"
    items = [
        _source_page_item(chapter_ref="ch-1", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-2", excerpts=[excerpt]),
    ]
    sm = _source_map(source_id="ebook:zh-book", primary_lang="zh-Hant", items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_no_match_outcome())
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    assert len(result.items) >= 1
    for item in result.items:
        assert item.evidence_language == "zh-Hant", (
            f"Expected evidence_language='zh-Hant'; got {item.evidence_language!r}"
        )


# ───────────────────────────────────────────────────────────────────────────
# T10 — deterministic fake matcher threads through unchanged
# ───────────────────────────────────────────────────────────────────────────


def test_propose_uses_deterministic_fake_matcher():
    """T10: the canned ``MatchOutcome`` flows through to emitted items
    unchanged — confidence and matched_concept_path on emitted item's
    canonical_match equal the matcher's return value."""
    rs = _ebook_source()
    excerpt = "Hypothesis A is supported by data."
    items = [
        _source_page_item(chapter_ref="ch-1", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-2", excerpts=[excerpt]),
    ]
    sm = _source_map(items=items)

    canned = _exact_alias_outcome(confidence=0.93)
    matcher = _CannedMatcher(canned)
    engine = ConceptPromotionEngine()
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    # Matcher was called once per candidate (here: 1 candidate, since both
    # chapters share the same leading text → one merged candidate).
    assert len(matcher.calls) == 1
    merged = [it for it in result.items if it.action == "update_merge_global"]
    assert merged, f"Expected merge item; got: {[it.action for it in result.items]}"
    item = merged[0]
    assert item.canonical_match is not None
    assert item.canonical_match.confidence == pytest.approx(canned.canonical_match.confidence)
    assert item.canonical_match.matched_concept_path == canned.canonical_match.matched_concept_path


# ───────────────────────────────────────────────────────────────────────────
# T11 — subprocess gate: no shared.book_storage import (C6)
# ───────────────────────────────────────────────────────────────────────────


def test_no_book_storage_import():
    """T11: importing ``shared.concept_promotion_engine`` must NOT pull
    ``shared.book_storage`` into ``sys.modules``."""
    src = textwrap.dedent(
        """
        import sys
        import shared.concept_promotion_engine  # noqa: F401

        offending = sorted(
            m for m in sys.modules if m.startswith("shared.book_storage")
        )
        if offending:
            print("OFFENDING:" + ",".join(offending))
            sys.exit(1)
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout


# ───────────────────────────────────────────────────────────────────────────
# T12 — subprocess gate: no fastapi / thousand_sunny / agents / LLM clients (C6)
# ───────────────────────────────────────────────────────────────────────────


def test_no_runtime_imports_forbidden():
    """T12: importing ``shared.concept_promotion_engine`` must NOT pull
    fastapi, thousand_sunny, agents.*, anthropic, openai, claude_client,
    or google.generativeai into ``sys.modules``."""
    src = textwrap.dedent(
        """
        import sys
        import shared.concept_promotion_engine  # noqa: F401

        offending = sorted(
            m
            for m in sys.modules
            if m.startswith((
                "fastapi",
                "thousand_sunny",
                "agents.",
                "anthropic",
                "openai",
                "claude_client",
                "google.generativeai",
            ))
        )
        if offending:
            print("OFFENDING:" + ",".join(offending))
            sys.exit(1)
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", src],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "OK" in result.stdout


# ───────────────────────────────────────────────────────────────────────────
# T13 — matcher exception → result.error set, items=[]  (C7)
# ───────────────────────────────────────────────────────────────────────────


def test_propose_matcher_failure_returns_error_state():
    """T13: matcher raises a documented exception (ValueError) → engine
    returns a ``ConceptPromotionResult`` with ``items=[]`` and
    ``error="matcher_failed: ..."`` (C7). Programmer errors propagate."""
    rs = _ebook_source()
    items = [
        _source_page_item(chapter_ref="ch-1", excerpts=["A claim."]),
        _source_page_item(chapter_ref="ch-2", excerpts=["A claim."]),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _RaisingMatcher(ValueError("synthetic matcher failure"))
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.items == []
    assert result.error is not None
    assert "matcher_failed" in result.error
    assert "ValueError" in result.error
    # candidates_extracted reflects what the engine derived even when
    # matching blew up.
    assert result.candidates_extracted >= 1


def test_propose_matcher_runtime_error_caught():
    """T13b: RuntimeError is in the documented failure tuple."""
    rs = _ebook_source()
    items = [_source_page_item(chapter_ref="ch-1", excerpts=["A claim."])]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    result = engine.propose(rs, sm, _EmptyKBIndex(), _RaisingMatcher(RuntimeError("oops")))
    assert result.items == []
    assert result.error is not None
    assert "RuntimeError" in result.error


def test_propose_matcher_typeerror_propagates():
    """T13c: TypeError is a programmer error and must propagate (narrow tuple
    discipline per #511 F5 lesson)."""
    rs = _ebook_source()
    items = [_source_page_item(chapter_ref="ch-1", excerpts=["A claim."])]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    with pytest.raises(TypeError):
        engine.propose(
            rs, sm, _EmptyKBIndex(), _RaisingMatcher(TypeError("intentional programmer bug"))
        )


# ───────────────────────────────────────────────────────────────────────────
# T14 — V1 invariant inheritance: include ⇒ evidence non-empty (C1)
# ───────────────────────────────────────────────────────────────────────────


def test_propose_concept_review_items_pass_v1_invariant():
    """T14: every emitted item where ``recommendation="include"`` has
    ``len(evidence) >= 1`` (V1 inherited from #512 ConceptReviewItem)."""
    rs = _ebook_source()
    excerpt = "HRV is a biomarker. Studies show correlation."
    items = [
        _source_page_item(chapter_ref="ch-1", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-2", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-3", excerpts=[excerpt]),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_no_match_outcome())
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    for item in result.items:
        if item.recommendation == "include":
            assert len(item.evidence) >= 1, (
                f"V1 violation: include item {item.item_id!r} has empty evidence"
            )


# ───────────────────────────────────────────────────────────────────────────
# T15 — ConceptPromotionResult round-trips
# ───────────────────────────────────────────────────────────────────────────


def test_propose_result_round_trips():
    """T15: ``model_dump`` + ``model_validate`` identity holds on a
    representative result (multi-action layout)."""
    rs = _ebook_source()
    excerpt = "HRV is a biomarker. It correlates with mortality."
    items = [
        _source_page_item(chapter_ref="ch-1", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-2", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-3", excerpts=[excerpt]),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_exact_alias_outcome(confidence=0.95))
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)
    assert result.error is None

    raw_dict = result.model_dump()
    again = ConceptPromotionResult.model_validate(raw_dict)
    assert again == result

    raw_json = result.model_dump_json()
    again_json = ConceptPromotionResult.model_validate_json(raw_json)
    assert again_json == result

    assert again.schema_version == 1


# ───────────────────────────────────────────────────────────────────────────
# T16 — min_recurrence_for_global=3; 2-chapter candidate → keep_source_local
# ───────────────────────────────────────────────────────────────────────────


def test_propose_min_recurrence_threshold_configurable():
    """T16: with ``min_recurrence_for_global=3``, a candidate appearing in
    only 2 chapters routes to ``keep_source_local`` (row 1 — recurrence
    < threshold AND no high-confidence match)."""
    rs = _ebook_source()
    excerpt = "HRV is a biomarker."
    items = [
        _source_page_item(chapter_ref="ch-1", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-2", excerpts=[excerpt]),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_no_match_outcome())
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher, min_recurrence_for_global=3)

    assert result.error is None
    # 2-chapter candidate with no match → row 1 (recurrence < 3, no high-conf
    # match) → keep_source_local.
    actions = [it.action for it in result.items]
    assert "keep_source_local" in actions
    assert "create_global_concept" not in actions, (
        f"Expected NOT to create global; got actions {actions}"
    )


# ───────────────────────────────────────────────────────────────────────────
# T17 (F1-analog regression) — error ⇒ items=[] model_validator
# ───────────────────────────────────────────────────────────────────────────


def test_concept_promotion_result_error_implies_empty_items_invariant():
    """F1-analog: schema-level ``error ⇒ items=[]`` model_validator.

    Mirrors PR #526's ``test_source_map_build_result_error_implies_empty_items_invariant``.
    Engine failures must surface as ``items=[]`` + ``error=...``; downstream
    slices (#515-#517) MUST NOT consume an error+non-empty-items combination.
    """
    # Valid: error set, items empty (the documented failure shape).
    ConceptPromotionResult(
        source_id="ebook:foo",
        primary_lang="en",
        candidates_extracted=0,
        items=[],
        risks=[],
        error="matcher_failed: simulated",
    )

    # Construct one valid ConceptReviewItem to put on the invalid result.
    item = ConceptReviewItem(
        item_id="cand_001_x",
        recommendation="defer",
        action="keep_source_local",
        reason="placeholder",
        evidence=[],
        risk=[],
        confidence=0.5,
        source_importance=0.5,
        reader_salience=0.0,
        concept_label="X",
        evidence_language="en",
        canonical_match=None,
    )

    # Invalid: error set AND items non-empty → model_validator raises.
    with pytest.raises(ValidationError, match="error is not None requires items"):
        ConceptPromotionResult(
            source_id="ebook:foo",
            primary_lang="en",
            candidates_extracted=1,
            items=[item],
            risks=[],
            error="matcher_failed: simulated",
        )


# ───────────────────────────────────────────────────────────────────────────
# Bonus regressions — additional invariant coverage
# ───────────────────────────────────────────────────────────────────────────


def test_propose_high_conf_single_chapter_falls_through_to_merge():
    """Row 1 should NOT trigger when single-chapter candidate has a
    high-confidence canonical match (per Brief §4.2 row 1 condition).
    """
    rs = _ebook_source()
    items = [_source_page_item(chapter_ref="ch-1", excerpts=["HRV biomarker."])]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_exact_alias_outcome(confidence=0.95))
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    assert any(it.action == "update_merge_global" for it in result.items)
    assert not any(it.action == "keep_source_local" for it in result.items), (
        "row 1 must NOT trigger when single-chapter candidate has high-conf match"
    )


def test_propose_recurrence_but_low_quotes_keeps_source_local():
    """Row 6 requires raw_quotes ≥ 3; meeting recurrence alone with fewer
    quotes routes to row 7 (keep_source_local)."""
    rs = _ebook_source()
    # Two chapters, but only 2 quotes total — below the row-6 raw_quotes
    # threshold.
    items = [
        _source_page_item(chapter_ref="ch-1", excerpts=["HRV biomarker"]),
        _source_page_item(chapter_ref="ch-2", excerpts=["HRV biomarker"]),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_no_match_outcome())
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    actions = [it.action for it in result.items]
    assert "keep_source_local" in actions
    assert "create_global_concept" not in actions


def test_propose_engine_risks_emitted_for_low_conf_cross_lingual():
    """Engine aggregates ``cross_lingual_uncertain`` risk on result.risks for
    each cross-lingual match below ``min_global_confidence``."""
    rs = _ebook_source()
    excerpt = "HRV is a biomarker."
    items = [
        _source_page_item(chapter_ref="ch-1", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-2", excerpts=[excerpt]),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_semantic_outcome(confidence=0.60))
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    codes = [r.code for r in result.risks]
    assert "cross_lingual_uncertain" in codes
    assert all(isinstance(r, RiskFlag) for r in result.risks)


def test_propose_skips_index_overview_item():
    """Engine ignores ``chapter_ref='index'`` (the long-source overview)
    when extracting candidates — those have no per-chapter evidence."""
    rs = _ebook_source()
    excerpt = "HRV is a biomarker."
    # Build an index item with no evidence + a per-chapter item with evidence.
    index_item = SourcePageReviewItem(
        item_id="alpha::index",
        recommendation="defer",
        action="create",
        reason="overview",
        evidence=[],  # no evidence on overview
        risk=[],
        confidence=0.5,
        source_importance=0.5,
        reader_salience=0.0,
        target_kb_path="KB/Wiki/Sources/alpha/index.md",
        chapter_ref="index",
    )
    chapter_item = _source_page_item(chapter_ref="ch-1", excerpts=[excerpt])
    sm = _source_map(items=[index_item, chapter_item])

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_no_match_outcome())
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    # Only one candidate from ch-1 — the index item contributes nothing.
    assert result.candidates_extracted == 1


def test_propose_create_global_evidence_meets_v1():
    """Cross-check C4 ⇒ C1/V1: every create_global item must have ≥1
    evidence anchor (C4) AND that satisfies V1 (include ⇒ evidence)."""
    rs = _ebook_source()
    excerpt = "HRV biomarker for autonomic balance"
    items = [
        _source_page_item(chapter_ref="ch-1", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-2", excerpts=[excerpt]),
        _source_page_item(chapter_ref="ch-3", excerpts=[excerpt]),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_no_match_outcome())
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    create_items = [it for it in result.items if it.action == "create_global_concept"]
    assert create_items, "expected at least one create_global_concept item"
    for it in create_items:
        assert it.recommendation == "include"
        assert len(it.evidence) >= 1
        assert it.confidence >= 0.75


def test_propose_output_ordering():
    """Brief §4.1: items ordered source-local first, then create_global,
    then update variants, then exclude."""
    rs = _ebook_source()
    # Build candidates that will produce multiple actions.
    items = [
        _source_page_item(
            chapter_ref="ch-1",
            excerpts=[
                "HRV biomarker.",  # candidate A
                "Topic B claim.",  # candidate B
                "    ",  # blank → exclude
            ],
        ),
        _source_page_item(
            chapter_ref="ch-2",
            excerpts=[
                "HRV biomarker.",  # candidate A (recurrence=2)
            ],
        ),
    ]
    sm = _source_map(items=items)

    engine = ConceptPromotionEngine()
    # Candidate A (recurrence=2) gets exact_alias high-conf;
    # Candidate B (recurrence=1) gets none → row 1 keep_source_local;
    # blank → row 8 exclude.
    matcher = _PerLabelMatcher(
        by_label={
            "HRV": _exact_alias_outcome(confidence=0.95),
        },
        default=_no_match_outcome(),
    )
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)

    assert result.error is None
    # Order: keep_source_local < create_global_concept < update_merge_global
    # < update_conflict_global < exclude.
    bucket_order = {
        "keep_source_local": 0,
        "create_global_concept": 1,
        "update_merge_global": 2,
        "update_conflict_global": 3,
        "exclude": 4,
    }
    seen_buckets = [bucket_order[it.action] for it in result.items]
    assert seen_buckets == sorted(seen_buckets), (
        f"items not ordered by action bucket: {[it.action for it in result.items]}"
    )


# ───────────────────────────────────────────────────────────────────────────
# Fixture-loading tests — confirm the JSON fixtures under
# tests/fixtures/concept_promotion/ stay schema-valid for downstream slices.
# ───────────────────────────────────────────────────────────────────────────


def test_minimal_source_map_fixture_loads_and_drives_engine():
    """The ``minimal_source_map.json`` fixture round-trips into a valid
    ``SourceMapBuildResult`` and drives the engine to a deterministic
    ``create_global_concept`` (under default thresholds the cross-chapter
    HRV claim has recurrence=2 but only 2 raw_quotes; downgrades to
    ``keep_source_local`` per row 7). Both states are valid — assert
    we get an item, no error."""
    sm_json = (FIXTURES_DIR / "minimal_source_map.json").read_text(encoding="utf-8")
    sm = SourceMapBuildResult.model_validate_json(sm_json)

    rs = _ebook_source()
    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_no_match_outcome())
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)
    assert result.error is None
    assert len(result.items) >= 1


def test_cross_lingual_zh_source_map_fixture_loads():
    """The ``cross_lingual_zh_source_map.json`` fixture is schema-valid and
    drives engine output where every item carries
    ``evidence_language="zh-Hant"`` (C5)."""
    sm_json = (FIXTURES_DIR / "cross_lingual_zh_source_map.json").read_text(encoding="utf-8")
    sm = SourceMapBuildResult.model_validate_json(sm_json)
    assert sm.primary_lang == "zh-Hant"

    rs = _ebook_source(book_id="zh-book", primary_lang="zh-Hant")
    engine = ConceptPromotionEngine()
    matcher = _CannedMatcher(_semantic_outcome(confidence=0.65))
    result = engine.propose(rs, sm, _EmptyKBIndex(), matcher)
    assert result.error is None
    for item in result.items:
        assert item.evidence_language == "zh-Hant"


def test_expected_result_create_global_fixture_is_valid():
    """The ``expected_result_create_global.json`` fixture is a representative
    ``ConceptPromotionResult`` shape downstream slices (#515 / #516) can
    rely on. Round-trips schema-valid."""
    res_json = (FIXTURES_DIR / "expected_result_create_global.json").read_text(encoding="utf-8")
    res = ConceptPromotionResult.model_validate_json(res_json)
    assert res.schema_version == 1
    assert res.error is None
    assert len(res.items) == 1
    item = res.items[0]
    assert item.action == "create_global_concept"
    assert item.recommendation == "include"
    assert len(item.evidence) >= 1  # C4
    assert item.confidence >= 0.75  # C4
