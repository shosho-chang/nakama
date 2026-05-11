"""Behavior tests for ``shared.promotion_acceptance_gate`` (ADR-024 Slice 7 / #515).

7 tests covering Brief §5 GT1-GT7 — the Acceptance Gate's defense-in-depth
validation of items prior to write. Tests inject minimal in-memory write
adapters so no real filesystem is touched (parallel to #511 / #513 / #514
test discipline).

- GT1 clean source page item → passed=True
- GT2 target_kb_path traversal → finding
- GT3 target_kb_path absolute outside vault → finding
- GT4 evidence anchor locator empty → finding
- GT5 human_decision missing → finding
- GT6 concept canonical match path invalid → finding (defense in depth for #512 V10)
- GT7 duplicate target_kb_path within one batch → second item finding
"""

from __future__ import annotations

from pathlib import Path

from shared.promotion_acceptance_gate import AcceptanceGate
from shared.schemas.promotion_manifest import (
    CanonicalMatch,
    ConceptReviewItem,
    EvidenceAnchor,
    HumanDecision,
    PromotionManifest,
    RecommenderMetadata,
    SourcePageReviewItem,
)

# ── Fixture helpers ───────────────────────────────────────────────────────────


class _MemoryAdapter:
    """Minimal in-memory adapter for gate tests. The gate uses only
    ``read_file`` / ``hash_file`` (read-only); writes are out of scope for
    gate validation."""

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self._files = files or {}

    def read_file(self, vault_path: str) -> bytes | None:
        return self._files.get(vault_path)

    def hash_file(self, vault_path: str) -> str | None:
        import hashlib

        data = self._files.get(vault_path)
        if data is None:
            return None
        return hashlib.sha256(data).hexdigest()

    def write_file(self, vault_path, content, *, backup_path) -> None:  # pragma: no cover
        raise AssertionError("gate must not call write_file")

    def make_backup(self, vault_path):  # pragma: no cover
        raise AssertionError("gate must not call make_backup")


def _empty_manifest() -> PromotionManifest:
    return PromotionManifest(
        manifest_id="mfst_gate_001",
        source_id="ebook:gate-test",
        created_at="2026-05-10T12:00:00Z",
        status="needs_review",
        recommender=RecommenderMetadata(
            model_name="claude-opus-4-7",
            model_version="2026-04",
            recommended_at="2026-05-10T12:00:00Z",
        ),
        items=[],
        commit_batches=[],
    )


def _approved_decision() -> HumanDecision:
    return HumanDecision(
        decision="approve",
        decided_at="2026-05-10T13:00:00Z",
        decided_by="tester",
    )


def _evidence_clean() -> list[EvidenceAnchor]:
    return [
        EvidenceAnchor(
            kind="chapter_quote",
            source_path="data/books/x/original.epub",
            locator="epubcfi(/6/4[ch1]!/4/2/16/1:0,/6/4[ch1]!/4/2/16/1:200)",
            excerpt="HRV reflects autonomic balance.",
            confidence=0.9,
        )
    ]


_UNSET = object()


def _src_item(
    *,
    item_id: str = "src_ch1_001",
    target_kb_path: str | None = "KB/Wiki/Sources/x/chapter-1.md",
    evidence: list[EvidenceAnchor] | None = None,
    human_decision=_UNSET,
) -> SourcePageReviewItem:
    return SourcePageReviewItem(
        item_id=item_id,
        recommendation="include",
        action="create",
        reason="r",
        evidence=evidence if evidence is not None else _evidence_clean(),
        risk=[],
        confidence=0.9,
        source_importance=0.9,
        reader_salience=0.8,
        target_kb_path=target_kb_path,
        chapter_ref="ch-1",
        human_decision=_approved_decision() if human_decision is _UNSET else human_decision,
    )


def _concept_item(
    *,
    item_id: str = "concept_hrv_001",
    canonical_match: CanonicalMatch | None = None,
    human_decision=_UNSET,
) -> ConceptReviewItem:
    cm = (
        canonical_match
        if canonical_match is not None
        else CanonicalMatch(
            match_basis="exact_alias",
            confidence=0.97,
            matched_concept_path="KB/Wiki/Concepts/HRV.md",
        )
    )
    return ConceptReviewItem(
        item_id=item_id,
        recommendation="include",
        action="create_global_concept",
        reason="r",
        evidence=_evidence_clean(),
        risk=[],
        confidence=0.9,
        source_importance=0.9,
        reader_salience=0.8,
        concept_label="HRV",
        evidence_language="en",
        canonical_match=cm,
        human_decision=_approved_decision() if human_decision is _UNSET else human_decision,
    )


# ── GT1 — clean source page item passes ─────────────────────────────────────


def test_gate_passes_clean_source_page_item(tmp_path: Path):
    """GT1: all fields valid → passed=True, findings empty."""
    gate = AcceptanceGate()
    item = _src_item()
    result = gate.validate(item, tmp_path, _empty_manifest(), _MemoryAdapter())

    assert result.passed is True
    assert result.findings == []
    assert result.item_id == "src_ch1_001"


# ── GT2 — target_kb_path traversal ──────────────────────────────────────────


def test_gate_fails_target_kb_path_traversal(tmp_path: Path):
    """GT2: target_kb_path with '..' segment → finding target_kb_path_traversal."""
    gate = AcceptanceGate()
    item = _src_item(target_kb_path="../etc/passwd")
    result = gate.validate(item, tmp_path, _empty_manifest(), _MemoryAdapter())

    assert result.passed is False
    codes = [f.code for f in result.findings]
    assert "target_kb_path_traversal" in codes


# ── GT3 — target_kb_path absolute outside vault ─────────────────────────────


def test_gate_fails_target_kb_path_outside_vault(tmp_path: Path):
    """GT3: absolute path outside vault_root → finding target_kb_path_outside_vault."""
    gate = AcceptanceGate()
    # An absolute path under a different root.
    other_root = tmp_path.parent / "outside-vault" / "evil.md"
    item = _src_item(target_kb_path=str(other_root))
    result = gate.validate(item, tmp_path, _empty_manifest(), _MemoryAdapter())

    assert result.passed is False
    codes = [f.code for f in result.findings]
    assert "target_kb_path_outside_vault" in codes


# ── GT4 — evidence anchor locator empty ─────────────────────────────────────


def test_gate_fails_evidence_anchor_locator_empty(tmp_path: Path):
    """GT4: EvidenceAnchor with empty locator → finding evidence_anchor_locator_invalid.

    Note: #512 EvidenceAnchor schema does not enforce locator non-emptiness.
    The gate enforces it as defense in depth — so this test exercises the
    gate's redundant check on a schema-legal but semantically-invalid input.
    """
    gate = AcceptanceGate()
    bad_evidence = [
        EvidenceAnchor(
            kind="chapter_quote",
            source_path="data/books/x/original.epub",
            locator="   ",  # whitespace-only — schema allows; gate refuses
            excerpt="Some excerpt text.",
            confidence=0.9,
        )
    ]
    item = _src_item(evidence=bad_evidence)
    result = gate.validate(item, tmp_path, _empty_manifest(), _MemoryAdapter())

    assert result.passed is False
    codes = [f.code for f in result.findings]
    assert "evidence_anchor_locator_invalid" in codes


# ── GT5 — human_decision missing ────────────────────────────────────────────


def test_gate_fails_human_decision_missing(tmp_path: Path):
    """GT5: item without human_decision → finding human_decision_missing."""
    gate = AcceptanceGate()
    item = _src_item(human_decision=None)
    # SourcePageReviewItem schema allows human_decision=None.
    assert item.human_decision is None
    result = gate.validate(item, tmp_path, _empty_manifest(), _MemoryAdapter())

    assert result.passed is False
    codes = [f.code for f in result.findings]
    assert "human_decision_missing" in codes


# ── GT6 — concept canonical match path invalid (defense in depth #512 V10) ──


def test_gate_fails_concept_canonical_match_path_invalid(tmp_path: Path):
    """GT6: concept item with match_basis='exact_alias' but matched_concept_path
    None — though already #512 V10, gate double-checks (defense in depth).

    Because #512 V10 prevents constructing such a CanonicalMatch directly
    via the validator, we use ``model_construct`` to bypass validation and
    simulate a corrupted-in-memory item (e.g. coming from a buggy callable
    upstream that mutated state). The gate MUST still refuse.
    """
    gate = AcceptanceGate()
    # Construct an invalid CanonicalMatch via Pydantic's model_construct
    # (validation bypass — used by callers that already know the data is
    # validated; abused here to simulate a corrupted state for the gate's
    # defense-in-depth check).
    bad_cm = CanonicalMatch.model_construct(
        match_basis="exact_alias",
        confidence=0.97,
        matched_concept_path=None,
    )
    # Build a valid concept item with a valid match; then swap canonical_match
    # to the corrupted one via object.__setattr__ (parent ConceptReviewItem is
    # NOT frozen — its inner CanonicalMatch IS, but assignment to the outer
    # field is allowed).
    item = _concept_item()
    object.__setattr__(item, "canonical_match", bad_cm)
    result = gate.validate(item, tmp_path, _empty_manifest(), _MemoryAdapter())

    assert result.passed is False
    codes = [f.code for f in result.findings]
    assert "concept_canonical_match_path_invalid" in codes


# ── GT7 — duplicate target_kb_path within one batch ────────────────────────


def test_gate_fails_duplicate_target_in_batch(tmp_path: Path):
    """GT7: two items in batch with same target_kb_path → finding on second."""
    gate = AcceptanceGate()
    item_a = _src_item(item_id="src_a", target_kb_path="KB/Wiki/Sources/x/dup.md")
    item_b = _src_item(item_id="src_b", target_kb_path="KB/Wiki/Sources/x/dup.md")

    result_a = gate.validate(item_a, tmp_path, _empty_manifest(), _MemoryAdapter())
    result_b = gate.validate(item_b, tmp_path, _empty_manifest(), _MemoryAdapter())

    assert result_a.passed is True
    assert result_b.passed is False
    codes_b = [f.code for f in result_b.findings]
    assert "duplicate_target_in_batch" in codes_b
