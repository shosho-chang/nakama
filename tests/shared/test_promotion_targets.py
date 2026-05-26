"""Tests for shared.promotion_targets — ADR-034 v2 single source of truth.

Verifies the `match`-based dispatch behaves identically to the pre-PR1
inline `isinstance` branches in `promotion_acceptance_gate` + the
`_resolve_target_path` helper in `promotion_commit`, and that the default
arm raises (no silent fallback, per ADR-034 v2 §D3).
"""

from __future__ import annotations

import pytest

from shared.promotion_targets import resolve_target_path
from shared.schemas.promotion_manifest import (
    CanonicalMatch,
    ConceptReviewItem,
    EvidenceAnchor,
    SourcePageReviewItem,
)


def _evidence() -> list[EvidenceAnchor]:
    return [
        EvidenceAnchor(
            kind="chapter_quote",
            source_path="data/books/x/original.epub",
            locator="epubcfi(/6/4[ch1]!/4/2/16/1:0,/6/4[ch1]!/4/2/16/1:200)",
            excerpt="ATP hydrolysis releases energy.",
            confidence=0.9,
        )
    ]


def _make_source(target: str | None = "KB/Wiki/Sources/foo/ch1.md") -> SourcePageReviewItem:
    return SourcePageReviewItem(
        item_id="src-1",
        recommendation="include",
        action="create",
        reason="r",
        evidence=_evidence(),
        risk=[],
        confidence=0.9,
        source_importance=0.8,
        reader_salience=0.5,
        target_kb_path=target,
    )


def _make_concept(
    matched_path: str | None = None,
    match_basis: str = "none",
) -> ConceptReviewItem:
    canonical = None
    if match_basis != "none" or matched_path is not None:
        # V10: non-none basis requires path; "none" requires path=None.
        canonical = CanonicalMatch(
            match_basis=match_basis,  # type: ignore[arg-type]
            matched_concept_path=matched_path,
            confidence=0.85,
        )
    return ConceptReviewItem(
        item_id="concept-1",
        recommendation="include",
        action="create_global_concept",
        reason="r",
        evidence=_evidence(),
        risk=[],
        confidence=0.9,
        source_importance=0.8,
        reader_salience=0.5,
        concept_label="ATP",
        evidence_language="en",
        canonical_match=canonical,
    )


def test_source_page_with_target_returns_path() -> None:
    item = _make_source("KB/Wiki/Sources/foo/ch1.md")
    assert resolve_target_path(item) == "KB/Wiki/Sources/foo/ch1.md"


def test_source_page_with_none_target_returns_none() -> None:
    item = _make_source(target=None)
    assert resolve_target_path(item) is None


def test_source_page_with_whitespace_target_returns_none() -> None:
    item = _make_source(target="   ")
    assert resolve_target_path(item) is None


def test_concept_with_matched_path_returns_path() -> None:
    item = _make_concept(matched_path="KB/Wiki/Concepts/ATP.md", match_basis="exact_alias")
    assert resolve_target_path(item) == "KB/Wiki/Concepts/ATP.md"


def test_concept_without_canonical_match_returns_none() -> None:
    item = _make_concept(matched_path=None, match_basis="none")
    assert resolve_target_path(item) is None


def test_default_arm_raises_for_unknown_subtype() -> None:
    """ADR-034 v2 §D3 — `case _: raise` enforces register hygiene."""

    class _FakeReviewItem:
        item_id = "fake-1"

    with pytest.raises(NotImplementedError, match="_FakeReviewItem"):
        resolve_target_path(_FakeReviewItem())  # type: ignore[arg-type]
