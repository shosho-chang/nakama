"""Tests for shared.concept_canonicalize (issue #497).

Five required case categories: NFKC, casefold, plural, acronym, seed dict.
"""

from __future__ import annotations

import pytest

from shared.concept_canonicalize import canonicalize, report_collisions

# ---------- NFKC normalization ----------


def test_nfkc_normalization():
    # Fullwidth chars must be NFKC-normalized to ASCII equivalents
    assert canonicalize("ｅｎｅｒｇｙ") == "energy"


# ---------- casefold ----------


def test_casefold_uppercase():
    assert canonicalize("ATP") == "atp"


def test_casefold_mixed():
    assert canonicalize("LaCTaTe") == "lactate"


# ---------- plural stripping ----------


def test_plural_strip_s_via_seed_dict():
    # "phospholipids" is in the seed dict; result must match "phospholipid"
    assert canonicalize("phospholipids") == "phospholipid"


def test_plural_strip_s_generic():
    # generic word not in dict — strip the trailing -s
    assert canonicalize("enzymes") == "enzyme"


def test_plural_strip_ies():
    assert canonicalize("antibodies") == "antibody"


def test_plural_strip_s_only_not_es():
    # "enzymes" ends in "es" but correct de-plural is strip-s not strip-es
    result = canonicalize("enzymes")
    assert result == "enzyme", f"expected 'enzyme', got {result!r}"


# ---------- acronym ↔ full-name (seed dict) ----------


def test_acronym_alias_long_to_short():
    assert canonicalize("Adenosine Triphosphate") == "atp"


def test_acronym_alias_variant():
    # plural acronym
    assert canonicalize("atps") == "atp"


# ---------- seed dict: all three pairs resolve to the same canonical ----------


@pytest.mark.parametrize(
    "surface,expected",
    [
        ("ATP", "atp"),
        ("Adenosine Triphosphate", "atp"),
        ("atps", "atp"),
        ("Phospholipid", "phospholipid"),
        ("phospholipids", "phospholipid"),
        ("lactate", "lactate"),
        ("Lactic Acid", "lactate"),
    ],
)
def test_seed_dict_all_variants(surface, expected):
    assert canonicalize(surface) == expected


# ---------- idempotency ----------


def test_idempotent():
    for term in ("atp", "phospholipid", "lactate", "nadph oxidase"):
        assert canonicalize(term) == canonicalize(canonicalize(term))


# ---------- report_collisions ----------


def test_report_collisions_detects_atp_variants():
    terms = ["ATP", "Adenosine Triphosphate", "atps"]
    collisions = report_collisions(terms)
    # All three map to "atp"; expect 2 collision pairs
    assert len(collisions) == 2


def test_report_collisions_no_collisions():
    terms = ["atp", "nadph oxidase", "lactate"]
    assert report_collisions(terms) == []


def test_report_collisions_returns_original_surface_forms():
    terms = ["ATP", "Adenosine Triphosphate"]
    pairs = report_collisions(terms)
    assert len(pairs) == 1
    a, b = pairs[0]
    assert {a, b} == {"ATP", "Adenosine Triphosphate"}
