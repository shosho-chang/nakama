"""Tests for shared.coverage_classifier (ADR-020 S4).

Acceptance gate tests are deterministic. LLM-calling functions
(extract_claims, check_claim_in_page) accept _ask_llm stub to avoid real API calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.coverage_classifier import (
    ClaimUnit,
    ConceptDispatchEntry,
    CoverageManifest,
    check_claim_in_page,
    extract_claims,
    run_acceptance_gate,
    write_coverage_manifest,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _manifest(**kwargs) -> CoverageManifest:
    defaults = dict(
        chapter_index=1,
        book_id="bse-2024",
        claims_extracted_by_llm=[],
        figures_count=0,
        figures_embedded=0,
        tables_transcluded=0,
        verbatim_paragraph_match_pct=99.0,
        concept_dispatch_log=[],
    )
    defaults.update(kwargs)
    return CoverageManifest(**defaults)


# ---------------------------------------------------------------------------
# primary_claims_missing_pct property
# ---------------------------------------------------------------------------


def test_primary_missing_pct_zero_when_all_found():
    m = _manifest(
        claims_extracted_by_llm=[
            ClaimUnit("A", "primary", found_in_page=True),
            ClaimUnit("B", "primary", found_in_page=True),
        ]
    )
    assert m.primary_claims_missing_pct == pytest.approx(0.0)


def test_primary_missing_pct_100_when_none_found():
    m = _manifest(claims_extracted_by_llm=[ClaimUnit("A", "primary", found_in_page=False)])
    assert m.primary_claims_missing_pct == pytest.approx(100.0)


def test_primary_missing_pct_zero_when_no_primary_claims():
    m = _manifest(claims_extracted_by_llm=[ClaimUnit("N", "nuance", found_in_page=False)])
    assert m.primary_claims_missing_pct == pytest.approx(0.0)


def test_secondary_missing_pct():
    m = _manifest(
        claims_extracted_by_llm=[
            ClaimUnit("A", "secondary", found_in_page=False),
            ClaimUnit("B", "secondary", found_in_page=True),
        ]
    )
    assert m.secondary_claims_missing_pct == pytest.approx(50.0)


def test_nuance_missing_pct():
    m = _manifest(
        claims_extracted_by_llm=[
            ClaimUnit("A", "nuance", found_in_page=False),
            ClaimUnit("B", "nuance", found_in_page=False),
            ClaimUnit("C", "nuance", found_in_page=True),
        ]
    )
    assert m.nuance_claims_missing_pct == pytest.approx(200.0 / 3.0)


# ---------------------------------------------------------------------------
# run_acceptance_gate — pass conditions
# ---------------------------------------------------------------------------


def test_gate_passes_all_conditions_met():
    m = _manifest(
        claims_extracted_by_llm=[
            ClaimUnit("P1", "primary", found_in_page=True),
            ClaimUnit("S1", "secondary", found_in_page=True),
        ],
        figures_count=2,
        figures_embedded=2,
    )
    passed, reasons = run_acceptance_gate(m)
    assert passed
    assert reasons == []


def test_gate_passes_no_claims():
    passed, reasons = run_acceptance_gate(_manifest(figures_count=0, figures_embedded=0))
    assert passed


# ---------------------------------------------------------------------------
# run_acceptance_gate — fail conditions
# ---------------------------------------------------------------------------


def test_gate_fails_primary_missing_gt_5pct():
    claims = [
        ClaimUnit("P1", "primary", found_in_page=False),
        ClaimUnit("P2", "primary", found_in_page=False),
        ClaimUnit("P3", "primary", found_in_page=True),
    ]
    passed, reasons = run_acceptance_gate(_manifest(claims_extracted_by_llm=claims))
    assert not passed
    assert any("primary" in r for r in reasons)


def test_gate_fails_primary_exactly_at_threshold_does_not_fail():
    # exactly 5% missing = allowed
    claims = [ClaimUnit(f"P{i}", "primary", found_in_page=(i < 19)) for i in range(20)]
    # 1/20 = 5.0% — boundary: 5% is NOT > 5%, so should pass
    passed, _ = run_acceptance_gate(_manifest(claims_extracted_by_llm=claims))
    assert passed


def test_gate_fails_figures_count_mismatch():
    passed, reasons = run_acceptance_gate(_manifest(figures_count=5, figures_embedded=3))
    assert not passed
    assert any("figure" in r.lower() for r in reasons)


def test_gate_fails_tables_transcluded():
    passed, reasons = run_acceptance_gate(_manifest(tables_transcluded=2))
    assert not passed
    assert any("tables_transcluded" in r for r in reasons)


def test_gate_fails_phase_b_stub_in_dispatch_log():
    log = [ConceptDispatchEntry("creatine", "phase-b-style-stub")]
    passed, reasons = run_acceptance_gate(_manifest(concept_dispatch_log=log))
    assert not passed
    assert any("phase-b-style-stub" in r for r in reasons)


def test_gate_normal_dispatch_actions_do_not_fail():
    log = [
        ConceptDispatchEntry("atp", "create"),
        ConceptDispatchEntry("glycogen", "update_merge"),
        ConceptDispatchEntry("lactate", "noop"),
    ]
    passed, _ = run_acceptance_gate(_manifest(concept_dispatch_log=log))
    assert passed


# ---------------------------------------------------------------------------
# run_acceptance_gate — warn-only (secondary missing > 25%)
# ---------------------------------------------------------------------------


def test_secondary_missing_gt_25pct_does_not_fail_gate():
    claims = [
        ClaimUnit("S1", "secondary", found_in_page=False),
        ClaimUnit("S2", "secondary", found_in_page=False),
        ClaimUnit("S3", "secondary", found_in_page=False),
        ClaimUnit("S4", "secondary", found_in_page=True),
    ]
    passed, reasons = run_acceptance_gate(_manifest(claims_extracted_by_llm=claims))
    assert passed  # secondary is warn-only


def test_nuance_missing_does_not_gate_at_all():
    claims = [ClaimUnit("N", "nuance", found_in_page=False)]
    passed, _ = run_acceptance_gate(_manifest(claims_extracted_by_llm=claims))
    assert passed


# ---------------------------------------------------------------------------
# Multiple fail reasons accumulate
# ---------------------------------------------------------------------------


def test_multiple_fail_conditions_all_reported():
    log = [ConceptDispatchEntry("creatine", "phase-b-style-stub")]
    claims = [ClaimUnit("P1", "primary", found_in_page=False)]
    m = _manifest(
        claims_extracted_by_llm=claims,
        figures_count=3,
        figures_embedded=1,
        tables_transcluded=1,
        concept_dispatch_log=log,
    )
    passed, reasons = run_acceptance_gate(m)
    assert not passed
    assert len(reasons) >= 3


# ---------------------------------------------------------------------------
# extract_claims (LLM-stubbed)
# ---------------------------------------------------------------------------

_EXTRACT_STUB = json.dumps(
    [
        {"type": "primary", "text": "ATP is the universal energy currency."},
        {"type": "secondary", "text": "ADP is reformed via oxidative phosphorylation."},
        {"type": "nuance", "text": "Free energy change for ATP hydrolysis is -30.5 kJ/mol."},
    ]
)


def test_extract_claims_returns_claim_units():
    claims = extract_claims("Chapter text.", _ask_llm=lambda p: _EXTRACT_STUB)
    assert len(claims) == 3
    assert all(isinstance(c, ClaimUnit) for c in claims)


def test_extract_claims_primary_type():
    claims = extract_claims("Chapter text.", _ask_llm=lambda p: _EXTRACT_STUB)
    types = {c.claim_type for c in claims}
    assert "primary" in types
    assert "secondary" in types
    assert "nuance" in types


def test_extract_claims_found_in_page_defaults_false():
    claims = extract_claims("Chapter text.", _ask_llm=lambda p: _EXTRACT_STUB)
    assert all(not c.found_in_page for c in claims)


def test_extract_claims_invalid_json_returns_empty():
    claims = extract_claims("Chapter text.", _ask_llm=lambda p: "garbage not json")
    assert claims == []


def test_extract_claims_empty_list_response():
    claims = extract_claims("Chapter text.", _ask_llm=lambda p: "[]")
    assert claims == []


# ---------------------------------------------------------------------------
# check_claim_in_page (LLM-stubbed)
# ---------------------------------------------------------------------------


def test_check_claim_true_response():
    claim = ClaimUnit("ATP is the energy currency.", "primary")
    result = check_claim_in_page(claim, "Page text.", _ask_llm=lambda p: "true")
    assert result is True


def test_check_claim_false_response():
    claim = ClaimUnit("Missing concept.", "primary")
    result = check_claim_in_page(claim, "Page text.", _ask_llm=lambda p: "false")
    assert result is False


def test_check_claim_defaults_false_on_invalid():
    claim = ClaimUnit("Some claim.", "primary")
    result = check_claim_in_page(claim, "Page.", _ask_llm=lambda p: "UNKNOWN XYZ")
    assert result is False


def test_check_claim_case_insensitive_true():
    claim = ClaimUnit("ATP.", "primary")
    result = check_claim_in_page(claim, "Page.", _ask_llm=lambda p: "Found: True")
    assert result is True


# ---------------------------------------------------------------------------
# write_coverage_manifest
# ---------------------------------------------------------------------------


def test_write_creates_json_file(tmp_path: Path):
    m = _manifest()
    m.acceptance_status = "pass"
    out = tmp_path / "ch1.coverage.json"
    write_coverage_manifest(m, out)
    assert out.exists()


def test_write_json_structure(tmp_path: Path):
    claims = [ClaimUnit("ATP is key.", "primary", found_in_page=True)]
    log = [ConceptDispatchEntry("atp", "create")]
    m = _manifest(
        chapter_index=3,
        book_id="sport-nutrition-2024",
        claims_extracted_by_llm=claims,
        figures_count=5,
        figures_embedded=5,
        concept_dispatch_log=log,
    )
    m.acceptance_status = "pass"
    out = tmp_path / "ch3.coverage.json"
    write_coverage_manifest(m, out)

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["chapter_index"] == 3
    assert data["book_id"] == "sport-nutrition-2024"
    assert data["acceptance_status"] == "pass"
    assert data["figures_count"] == 5
    assert len(data["claims_extracted_by_llm"]) == 1
    assert data["claims_extracted_by_llm"][0]["claim_type"] == "primary"
    assert len(data["concept_dispatch_log"]) == 1
    assert data["concept_dispatch_log"][0]["slug"] == "atp"


def test_write_includes_missing_pcts(tmp_path: Path):
    claims = [
        ClaimUnit("P1", "primary", found_in_page=False),
        ClaimUnit("P2", "primary", found_in_page=True),
    ]
    m = _manifest(claims_extracted_by_llm=claims)
    m.acceptance_status = "fail"
    out = tmp_path / "ch1.coverage.json"
    write_coverage_manifest(m, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["primary_claims_missing_pct"] == pytest.approx(50.0)
