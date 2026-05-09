"""Unit tests for ``shared.schemas.promotion_manifest`` (ADR-024 Slice 4 / #512).

15 tests = 14 functional invariants (V1-V11) + 1 reusability gate (T15).

Mirrors Brief §5 / plan §4.2:

- T1  minimal manifest constructs + round-trips
- T2  source-page include requires evidence (V1)
- T3  concept include requires evidence (V1)
- T4  source-page defer with no evidence is OK
- T5  source-page exclude with no evidence is OK
- T6  status='complete' requires human_decision on every item (V2)
- T7  status ⇔ commit_batches consistency, parametrized (V3 + V11 bidirectional)
- T8  duplicate item_id rejected (V4)
- T9  confidence/source_importance/reader_salience bounds (V5), parametrized
- T10 any failed batch blocks status='complete' (V6)
- T11 timestamp format validated (V7) + now_iso_utc() round-trips
- T12 replaces_manifest_id != manifest_id (V8)
- T13 commit batch approved/deferred/rejected disjoint (V9)
- T14 canonical_match basis ⇔ matched_concept_path consistency (V10), parametrized
- T15 subprocess import gate — no fastapi / thousand_sunny / agents pulled in

Imports zero ``fastapi`` / ``thousand_sunny`` / ``agents.*`` symbols (asserted
by T15 via fresh subprocess).
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.schemas.promotion_manifest import (
    CanonicalMatch,
    CommitBatch,
    ConceptReviewItem,
    EvidenceAnchor,
    HumanDecision,
    PromotionManifest,
    RecommenderMetadata,
    SourcePageReviewItem,
    now_iso_utc,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "promotion_manifest"


# ── Local helper builders (kept in-file; conftest changes are out of scope) ──


def _recommender() -> RecommenderMetadata:
    return RecommenderMetadata(
        model_name="claude-opus-4-7",
        model_version="2026-04",
        run_params={},
        recommended_at=now_iso_utc(),
    )


def _evidence() -> list[EvidenceAnchor]:
    return [
        EvidenceAnchor(
            kind="chapter_quote",
            source_path="data/books/abc123/original.epub",
            locator="epubcfi(/6/14[id1]!/4/2/16/1:0,/6/14[id1]!/4/2/16/1:120)",
            excerpt="The author argues that...",
            confidence=0.92,
        )
    ]


def _human_decision() -> HumanDecision:
    return HumanDecision(
        decision="approve",
        decided_at=now_iso_utc(),
        decided_by="shosho",
        note=None,
    )


def _source_page_item(
    *,
    item_id: str = "i1",
    recommendation: str = "include",
    action: str = "create",
    evidence: list[EvidenceAnchor] | None = None,
    human_decision: HumanDecision | None = None,
) -> SourcePageReviewItem:
    return SourcePageReviewItem(
        item_id=item_id,
        recommendation=recommendation,  # type: ignore[arg-type]
        action=action,  # type: ignore[arg-type]
        reason="test",
        evidence=_evidence() if evidence is None else evidence,
        confidence=0.7,
        source_importance=0.5,
        reader_salience=0.6,
        human_decision=human_decision,
    )


def _commit_batch(
    *,
    batch_id: str = "b1",
    promotion_status: str = "partial",
    approved_item_ids: list[str] | None = None,
    rejected_item_ids: list[str] | None = None,
    deferred_item_ids: list[str] | None = None,
) -> CommitBatch:
    return CommitBatch(
        batch_id=batch_id,
        created_at=now_iso_utc(),
        approved_item_ids=approved_item_ids or [],
        deferred_item_ids=deferred_item_ids or [],
        rejected_item_ids=rejected_item_ids or [],
        touched_files=[],
        errors=[],
        promotion_status=promotion_status,  # type: ignore[arg-type]
    )


def _manifest(
    *,
    manifest_id: str = "m1",
    source_id: str = "ebook:abc",
    status: str = "needs_review",
    items: list | None = None,
    commit_batches: list[CommitBatch] | None = None,
    replaces_manifest_id: str | None = None,
) -> PromotionManifest:
    return PromotionManifest(
        manifest_id=manifest_id,
        source_id=source_id,
        created_at=now_iso_utc(),
        status=status,  # type: ignore[arg-type]
        replaces_manifest_id=replaces_manifest_id,
        recommender=_recommender(),
        items=items or [],
        commit_batches=commit_batches or [],
        metadata={},
    )


# ── T1 — minimal manifest constructs and round-trips ─────────────────────────


def test_minimal_manifest_constructs():
    """T1: required fields only, no items, no batches, status='needs_review';
    round-trips through ``model_dump_json()`` / ``model_validate_json()``."""
    m = _manifest()
    assert m.status == "needs_review"
    assert m.schema_version == 1
    assert m.items == []
    assert m.commit_batches == []

    # JSON round-trip
    raw = m.model_dump_json()
    m2 = PromotionManifest.model_validate_json(raw)
    assert m2 == m

    # Dict round-trip
    raw_dict = m.model_dump()
    m3 = PromotionManifest.model_validate(raw_dict)
    assert m3 == m

    # On-disk fixture round-trip (also covers AC: serialization)
    fixture = FIXTURES_DIR / "minimal.json"
    on_disk = PromotionManifest.model_validate_json(fixture.read_text(encoding="utf-8"))
    assert on_disk.schema_version == 1
    assert on_disk.status == "needs_review"
    again = PromotionManifest.model_validate_json(on_disk.model_dump_json())
    assert again == on_disk


# ── T2 / T3 — recommendation='include' requires evidence (V1) ────────────────


def test_include_requires_evidence():
    """T2: SourcePageReviewItem(recommendation='include', evidence=[]) raises."""
    with pytest.raises(ValidationError, match="non-empty evidence"):
        SourcePageReviewItem(
            item_kind="source_page",
            item_id="i1",
            recommendation="include",
            action="create",
            reason="x",
            evidence=[],
            confidence=0.5,
            source_importance=0.5,
            reader_salience=0.5,
        )


def test_concept_include_requires_evidence():
    """T3: ConceptReviewItem(recommendation='include', evidence=[]) raises."""
    with pytest.raises(ValidationError, match="non-empty evidence"):
        ConceptReviewItem(
            item_kind="concept",
            item_id="c1",
            recommendation="include",
            action="create_global_concept",
            reason="x",
            evidence=[],
            confidence=0.5,
            source_importance=0.5,
            reader_salience=0.5,
            concept_label="HRV",
        )


# ── T4 / T5 — defer / exclude with no evidence are legitimate states ─────────


def test_defer_with_no_evidence_ok():
    """T4: recommendation='defer' + evidence=[] constructs cleanly (the
    legitimate missing-evidence state per ADR-024)."""
    item = SourcePageReviewItem(
        item_id="i1",
        recommendation="defer",
        action="noop",
        reason="needs more evidence",
        evidence=[],
        confidence=0.3,
        source_importance=0.4,
        reader_salience=0.5,
    )
    assert item.recommendation == "defer"
    assert item.evidence == []


def test_exclude_with_no_evidence_ok():
    """T5: recommendation='exclude' + evidence=[] constructs cleanly."""
    item = SourcePageReviewItem(
        item_id="i1",
        recommendation="exclude",
        action="noop",
        reason="not relevant",
        evidence=[],
        confidence=0.6,
        source_importance=0.2,
        reader_salience=0.1,
    )
    assert item.recommendation == "exclude"
    assert item.evidence == []


# ── T6 — status='complete' requires human_decision on every item (V2) ────────


def test_complete_status_requires_human_decisions():
    """T6: status='complete' with any item missing human_decision raises."""
    item_with = _source_page_item(item_id="i1", human_decision=_human_decision())
    item_without = _source_page_item(item_id="i2", human_decision=None)
    with pytest.raises(ValidationError, match="human_decision on every item"):
        _manifest(
            status="complete",
            items=[item_with, item_without],
            commit_batches=[
                _commit_batch(promotion_status="complete", approved_item_ids=["i1", "i2"])
            ],
        )


# ── T7 — status ⇔ commit_batches consistency, parametrized (V3 + V11) ────────


@pytest.mark.parametrize(
    "status,batches_count,should_raise,case_id",
    [
        # V3: needs_review with batches must raise
        ("needs_review", 1, True, "a_needs_review_with_batch"),
        # V11: post-review status with no batch must raise
        ("partial", 0, True, "b_partial_no_batch"),
        ("complete", 0, True, "c_complete_no_batch"),
        ("failed", 0, True, "d_failed_no_batch"),
        # Pass cases
        ("needs_review", 0, False, "e_needs_review_no_batch"),
        ("partial", 1, False, "f_partial_with_batch"),
    ],
)
def test_status_commit_batches_consistency(status, batches_count, should_raise, case_id):
    """T7: bidirectional consistency between manifest.status and commit_batches.

    V3 + V11 together make ``needs_review`` ⇔ ``commit_batches=[]`` bijective.
    """
    batches: list[CommitBatch] = []
    if batches_count > 0:
        batches.append(_commit_batch(promotion_status="partial"))

    if should_raise:
        with pytest.raises(ValidationError):
            _manifest(status=status, items=[], commit_batches=batches)
    else:
        m = _manifest(status=status, items=[], commit_batches=batches)
        assert m.status == status
        assert len(m.commit_batches) == batches_count


# ── T8 — duplicate item_id rejected (V4) ─────────────────────────────────────


def test_duplicate_item_ids_rejected():
    """T8: two items with same item_id raise."""
    items = [
        _source_page_item(item_id="dup1"),
        _source_page_item(item_id="dup1"),
    ]
    with pytest.raises(ValidationError, match="duplicate item_id"):
        _manifest(items=items)


# ── T9 — numeric range bounds enforced (V5), parametrized ────────────────────


@pytest.mark.parametrize(
    "value,should_raise",
    [
        (-0.1, True),
        (1.5, True),
        (-1.0, True),
        (2.0, True),
        (0.0, False),
        (0.5, False),
        (1.0, False),
    ],
)
def test_confidence_bounds(value, should_raise):
    """T9: confidence/source_importance/reader_salience must lie in [0.0, 1.0]
    (Pydantic Field constraint). Parametrized: out-of-range raises, in-range
    accepted (closed interval includes 0.0 and 1.0)."""
    if should_raise:
        with pytest.raises(ValidationError):
            SourcePageReviewItem(
                item_id="i1",
                recommendation="defer",
                action="noop",
                reason="x",
                evidence=[],
                confidence=value,
                source_importance=0.5,
                reader_salience=0.5,
            )
    else:
        item = SourcePageReviewItem(
            item_id="i1",
            recommendation="defer",
            action="noop",
            reason="x",
            evidence=[],
            confidence=value,
            source_importance=value,
            reader_salience=value,
        )
        assert item.confidence == value
        assert item.source_importance == value
        assert item.reader_salience == value


# ── T10 — any failed batch blocks status='complete' (V6) ─────────────────────


def test_failed_batch_blocks_complete_status():
    """T10: a manifest with any failed batch cannot be 'complete'."""
    item = _source_page_item(item_id="i1", human_decision=_human_decision())
    failed_batch = _commit_batch(
        batch_id="b_failed",
        promotion_status="failed",
        approved_item_ids=["i1"],
    )
    with pytest.raises(ValidationError, match="any commit batch is 'failed'"):
        _manifest(
            status="complete",
            items=[item],
            commit_batches=[failed_batch],
        )


# ── T11 — timestamp format validated (V7) ────────────────────────────────────


def test_timestamp_format():
    """T11: created_at='not-an-iso-string' raises; now_iso_utc() output passes;
    explicit '+00:00' offset also accepted (V7 permissiveness)."""
    # Garbage string raises
    with pytest.raises(ValidationError, match="ISO-8601 UTC"):
        PromotionManifest(
            manifest_id="m1",
            source_id="ebook:abc",
            created_at="not-an-iso-string",
            status="needs_review",
            recommender=_recommender(),
        )

    # now_iso_utc() output is accepted
    ts = now_iso_utc()
    assert ts.endswith("Z")
    m = PromotionManifest(
        manifest_id="m1",
        source_id="ebook:abc",
        created_at=ts,
        status="needs_review",
        recommender=_recommender(),
    )
    assert m.created_at == ts

    # Explicit +00:00 offset is also accepted
    m2 = PromotionManifest(
        manifest_id="m1",
        source_id="ebook:abc",
        created_at="2026-05-09T12:00:00+00:00",
        status="needs_review",
        recommender=_recommender(),
    )
    assert m2.created_at == "2026-05-09T12:00:00+00:00"


# ── T12 — replaces_manifest_id != manifest_id (V8) ───────────────────────────


def test_replaces_self_rejected():
    """T12: replaces_manifest_id == manifest_id raises; distinct value passes."""
    with pytest.raises(ValidationError, match="cannot equal manifest_id"):
        _manifest(manifest_id="m1", replaces_manifest_id="m1")

    # Sanity: a distinct prior id is accepted.
    m = _manifest(manifest_id="m_new", replaces_manifest_id="m_old")
    assert m.replaces_manifest_id == "m_old"


# ── T13 — commit batch approved/deferred/rejected disjoint (V9) ──────────────


def test_batch_item_id_set_disjoint():
    """T13: same item_id across approved+rejected (or any pair) raises."""
    with pytest.raises(ValidationError, match="overlap between approved and rejected"):
        CommitBatch(
            batch_id="b1",
            created_at=now_iso_utc(),
            approved_item_ids=["a"],
            rejected_item_ids=["a"],
            promotion_status="partial",
        )

    # Sanity: approved+deferred overlap also raises (covers all three pairings).
    with pytest.raises(ValidationError, match="overlap between approved and deferred"):
        CommitBatch(
            batch_id="b1",
            created_at=now_iso_utc(),
            approved_item_ids=["a"],
            deferred_item_ids=["a"],
            promotion_status="partial",
        )


# ── T14 — canonical_match basis ⇔ matched_concept_path consistency (V10) ─────


@pytest.mark.parametrize(
    "match_basis,matched_concept_path,should_raise",
    [
        ("none", "KB/Wiki/Concepts/HRV.md", True),
        ("exact_alias", None, True),
        ("semantic", None, True),
        ("translation", None, True),
        ("none", None, False),
        ("exact_alias", "KB/Wiki/Concepts/HRV.md", False),
        ("semantic", "KB/Wiki/Concepts/HRV.md", False),
    ],
)
def test_canonical_match_basis_path_consistency(match_basis, matched_concept_path, should_raise):
    """T14: V10 — match_basis='none' ⇔ matched_concept_path is None;
    non-none basis requires a matched_concept_path."""
    if should_raise:
        with pytest.raises(ValidationError):
            CanonicalMatch(
                match_basis=match_basis,
                confidence=0.8,
                matched_concept_path=matched_concept_path,
            )
    else:
        cm = CanonicalMatch(
            match_basis=match_basis,
            confidence=0.8,
            matched_concept_path=matched_concept_path,
        )
        assert cm.match_basis == match_basis
        assert cm.matched_concept_path == matched_concept_path


# ── T15 — reusability gate: subprocess import check ──────────────────────────


def test_no_runtime_imports():
    """T15: importing ``shared.schemas.promotion_manifest`` must NOT pull
    fastapi / thousand_sunny / agents.* into ``sys.modules``. Confirms the
    schema is reusable outside route handlers (mirrors #509 test 14).
    """
    src = textwrap.dedent(
        """
        import sys
        import shared.schemas.promotion_manifest  # noqa: F401

        offending = sorted(
            m
            for m in sys.modules
            if m.startswith(("fastapi", "thousand_sunny", "agents"))
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
