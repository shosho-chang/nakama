"""Tests for shared.compliance.medical_claim_vocab (ADR-005b §10)."""

from __future__ import annotations

from datetime import datetime, timezone

from shared import compliance, gutenberg_builder
from shared.compliance.medical_claim_vocab import (
    ABSOLUTE_ASSERTION_TERMS,
    MEDICAL_CLAIM_TERMS,
    scan_text,
)
from shared.schemas.publishing import (
    BlockNodeV1,
    DraftComplianceV1,
    DraftV1,
)


def _draft(title: str, body: str) -> DraftV1:
    ast = [BlockNodeV1(block_type="paragraph", content=body)]
    return DraftV1(
        draft_id="draft_20260423T120000_abc123",
        created_at=datetime.now(timezone.utc),
        agent="brook",
        operation_id="op_12345678",
        title=title,
        slug_candidates=["test-slug"],
        content=gutenberg_builder.build(ast),
        excerpt="An excerpt of at least twenty characters to pass validator.",
        primary_category="blog",
        focus_keyword="test",
        meta_description=(
            "A meta description that is at least fifty chars long to pass validator."
        ),
        compliance=DraftComplianceV1(
            schema_version=1,
            claims_no_therapeutic_effect=True,
            has_disclaimer=False,
        ),
        style_profile_id="blog@0.1.0",
    )


# ---------------------------------------------------------------------------
# scan_text — core matching
# ---------------------------------------------------------------------------


class TestScanText:
    def test_clean_text_returns_no_flags(self):
        result = scan_text("這是一篇關於幫助入睡的好文章")
        assert result.medical_claim is False
        assert result.absolute_assertion is False
        assert result.matched_terms == []

    def test_therapeutic_term_hits_medical_claim(self):
        result = scan_text("這個方法可以治癒失眠")
        assert result.medical_claim is True
        assert result.absolute_assertion is False
        assert "治癒" in result.matched_terms

    def test_diagnostic_term_hits_medical_claim(self):
        result = scan_text("診斷為慢性失眠的人適合")
        assert result.medical_claim is True
        assert "診斷" in result.matched_terms

    def test_drug_analog_term_hits_medical_claim(self):
        result = scan_text("這款產品有特效")
        assert result.medical_claim is True
        assert "特效" in result.matched_terms

    def test_absolute_assertion_terms(self):
        result = scan_text("百分之百保證有效")
        assert result.absolute_assertion is True
        assert "百分之百" in result.matched_terms
        assert "保證" in result.matched_terms

    def test_both_categories_can_trigger_simultaneously(self):
        result = scan_text("百分之百治癒失眠")
        assert result.medical_claim is True
        assert result.absolute_assertion is True

    def test_ascii_match_is_case_insensitive(self):
        result = scan_text("Effective 100% guaranteed")
        assert result.absolute_assertion is True
        assert "100%" in result.matched_terms

    def test_matched_terms_are_sorted_and_deduplicated(self):
        # Two occurrences of the same term shouldn't duplicate in output.
        result = scan_text("治癒了多少人 治癒是可能的")
        assert result.matched_terms.count("治癒") == 1


# ---------------------------------------------------------------------------
# scan(draft) — integrates title + AST content
# ---------------------------------------------------------------------------


class TestScanDraft:
    def test_scan_sees_title(self):
        d = _draft(title="治癒失眠的方法", body="This body has no hits")
        result = compliance.scan(d)
        assert result.medical_claim is True

    def test_scan_sees_ast_content(self):
        d = _draft(title="Neutral Title", body="這方法可以治癒失眠")
        result = compliance.scan(d)
        assert result.medical_claim is True

    def test_scan_ignores_html_markup(self):
        # raw_html includes <p class="..."> tags; we scan AST content not raw_html.
        d = _draft(title="Neutral Title", body="Nothing risky here at all")
        result = compliance.scan(d)
        assert result.medical_claim is False
        assert result.absolute_assertion is False


# ---------------------------------------------------------------------------
# Vocab structure sanity checks
# ---------------------------------------------------------------------------


class TestVocabStructure:
    def test_medical_claim_categories_are_nonempty(self):
        assert set(MEDICAL_CLAIM_TERMS.keys()) >= {
            "therapeutic",
            "diagnostic",
            "drug_analog",
        }
        for cat, terms in MEDICAL_CLAIM_TERMS.items():
            assert terms, f"category {cat} is empty"

    def test_absolute_assertion_terms_are_nonempty(self):
        assert len(ABSOLUTE_ASSERTION_TERMS) > 0

    def test_no_overlap_between_vocab_categories(self):
        all_medical = {t for terms in MEDICAL_CLAIM_TERMS.values() for t in terms}
        overlap = all_medical & set(ABSOLUTE_ASSERTION_TERMS)
        assert overlap == set(), f"medical/absolute vocab overlap: {overlap}"
