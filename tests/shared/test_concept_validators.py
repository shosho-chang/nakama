"""Tests for shared.concept_validators L2/L3 hard rules (Stage 1.5)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from shared.concept_validators import (
    L2_FORBIDDEN_STRINGS,
    IngestFailError,
    validate_l2_concept,
    validate_l3_concept,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LONG_BODY = " ".join(["word"] * 250)  # 250 words — well above 200 threshold
_SOURCE_PARA = "Creatine phosphate donates a phosphate group to ADP."
_BODY_WITH_PARA = _LONG_BODY + "\n\n" + _SOURCE_PARA


# ---------------------------------------------------------------------------
# L2 — word_count < 200
# ---------------------------------------------------------------------------


def test_l2_word_count_under_200():
    short_body = " ".join(["word"] * 50)  # 50 words
    with pytest.raises(IngestFailError, match="L2 word_count=50 < 200"):
        validate_l2_concept(short_body, [_SOURCE_PARA])


# ---------------------------------------------------------------------------
# L2 — missing source paragraph
# ---------------------------------------------------------------------------


def test_l2_missing_source_paragraph():
    body = _LONG_BODY  # has no source paragraph text in it
    with pytest.raises(IngestFailError, match="L2 missing chapter source paragraph"):
        validate_l2_concept(body, ["This sentence is not in body."])


def test_l2_empty_source_paragraphs():
    with pytest.raises(IngestFailError, match="L2 missing chapter source paragraph"):
        validate_l2_concept(_LONG_BODY, [])


# ---------------------------------------------------------------------------
# L2 — forbidden strings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("forbidden", L2_FORBIDDEN_STRINGS)
def test_l2_forbidden_string(forbidden):
    body = _BODY_WITH_PARA + f"\n\n{forbidden}\n"
    with pytest.raises(IngestFailError, match="L2 forbidden string"):
        validate_l2_concept(body, [_SOURCE_PARA])


# ---------------------------------------------------------------------------
# L2 — pass
# ---------------------------------------------------------------------------


def test_l2_pass():
    validate_l2_concept(_BODY_WITH_PARA, [_SOURCE_PARA])


# ---------------------------------------------------------------------------
# L3 — fewer than 2 chapters match
# ---------------------------------------------------------------------------


def test_l3_min_two_chapters_zero_match():
    with pytest.raises(IngestFailError, match="0 chapters < 2"):
        validate_l3_concept(
            _LONG_BODY,
            {"ch1": ["Not in body at all."], "ch2": ["Also not present."]},
        )


def test_l3_min_two_chapters_one_match():
    ch1_para = "Paragraph from chapter one content."
    body = _LONG_BODY + "\n\n" + ch1_para
    with pytest.raises(IngestFailError, match="1 chapters < 2"):
        validate_l3_concept(
            body,
            {"ch1": [ch1_para], "ch2": ["Not present in body."]},
        )


# ---------------------------------------------------------------------------
# L3 — pass
# ---------------------------------------------------------------------------


def test_l3_pass():
    ch1_para = "Paragraph from chapter one."
    ch2_para = "Paragraph from chapter two."
    body = _LONG_BODY + "\n\n" + ch1_para + "\n\n" + ch2_para
    validate_l3_concept(body, {"ch1": [ch1_para], "ch2": [ch2_para]})
