"""Tests for shared.concept_schema (ADR-020 S7 — en_source_terms schema enforcement)."""

from __future__ import annotations

import pytest

from shared.concept_schema import validate_v3_concept_page

# ---------------------------------------------------------------------------
# Valid v3 pages
# ---------------------------------------------------------------------------


def test_valid_v3_with_terms():
    fm = {"schema_version": 3, "en_source_terms": ["gut microbiota", "intestinal flora"]}
    valid, errors = validate_v3_concept_page(fm)
    assert valid
    assert errors == []


def test_valid_v3_empty_list():
    fm = {"schema_version": 3, "en_source_terms": []}
    valid, errors = validate_v3_concept_page(fm)
    assert valid
    assert errors == []


def test_valid_v3_single_term():
    fm = {"schema_version": 3, "en_source_terms": ["ATP resynthesis"]}
    valid, errors = validate_v3_concept_page(fm)
    assert valid


# ---------------------------------------------------------------------------
# Missing / wrong type
# ---------------------------------------------------------------------------


def test_v3_missing_en_source_terms():
    fm = {"schema_version": 3, "title": "腸道菌群"}
    valid, errors = validate_v3_concept_page(fm)
    assert not valid
    assert any("en_source_terms" in e for e in errors)


def test_v3_en_source_terms_not_list():
    fm = {"schema_version": 3, "en_source_terms": "gut microbiota"}
    valid, errors = validate_v3_concept_page(fm)
    assert not valid
    assert any("list" in e.lower() for e in errors)


def test_v3_en_source_terms_non_string_items():
    fm = {"schema_version": 3, "en_source_terms": ["valid", 42, None]}
    valid, errors = validate_v3_concept_page(fm)
    assert not valid
    assert any("string" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# Backward compat — v1 / v2 pages
# ---------------------------------------------------------------------------


def test_v2_without_en_source_terms_is_valid():
    fm = {"schema_version": 2, "aliases": ["glycolysis"]}
    valid, errors = validate_v3_concept_page(fm)
    assert valid


def test_v1_without_en_source_terms_is_valid():
    fm = {"schema_version": 1}
    valid, errors = validate_v3_concept_page(fm)
    assert valid


def test_no_schema_version_is_valid():
    fm = {"title": "ATP"}
    valid, errors = validate_v3_concept_page(fm)
    assert valid


# ---------------------------------------------------------------------------
# Error messages are descriptive
# ---------------------------------------------------------------------------


def test_error_message_mentions_field():
    fm = {"schema_version": 3}
    _, errors = validate_v3_concept_page(fm)
    assert len(errors) == 1
    assert "en_source_terms" in errors[0]


def test_multiple_errors_for_wrong_type_items():
    fm = {"schema_version": 3, "en_source_terms": [1, 2, 3]}
    valid, errors = validate_v3_concept_page(fm)
    assert not valid


# ---------------------------------------------------------------------------
# Return type contract
# ---------------------------------------------------------------------------


def test_returns_tuple():
    result = validate_v3_concept_page({"schema_version": 3, "en_source_terms": []})
    assert isinstance(result, tuple)
    assert len(result) == 2


def test_errors_is_list():
    _, errors = validate_v3_concept_page({"schema_version": 3, "en_source_terms": []})
    assert isinstance(errors, list)


@pytest.mark.parametrize(
    "fm",
    [
        {"schema_version": 3, "en_source_terms": []},
        {"schema_version": 2},
        {"schema_version": 1},
    ],
)
def test_valid_pages_return_empty_errors(fm):
    valid, errors = validate_v3_concept_page(fm)
    assert valid
    assert errors == []
