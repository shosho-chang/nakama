"""Behaviour tests for ``shared.source_mode.detect_book_mode`` — covers
the four-step priority chain documented in the module docstring."""

from __future__ import annotations

import pytest

from shared.source_mode import DEFAULT_MODE, detect_book_mode

# Reuse zh / en bodies from the lang_detect tests via local definition so this
# test file stands alone without cross-test imports.

ZH_BODY_SAMPLE = (
    "粒線體是細胞內負責產生能量的胞器，透過氧化磷酸化將養分轉換為三磷酸腺苷。"
    "近年研究顯示，粒線體功能失調與多種神經退化性疾病的發生密切相關。"
)
EN_BODY_SAMPLE = (
    "Mitochondria are the organelles responsible for ATP production through "
    "oxidative phosphorylation, and recent research has linked mitochondrial "
    "dysfunction to several neurodegenerative diseases."
)


# ── Priority 1: explicit zh metadata ────────────────────────────────────────


@pytest.mark.parametrize(
    "lang",
    ["zh", "zh-TW", "zh-tw", "zh_TW", "zh-Hant", "zh-CN", "zho", "chi", "ZH-tw"],
)
def test_metadata_zh_codes_yield_monolingual_zh(lang: str) -> None:
    assert detect_book_mode(metadata_lang=lang, body_sample=None) == "monolingual-zh"


def test_metadata_zh_overrides_en_body() -> None:
    """Explicit metadata wins over body sample — operator is trusted."""
    assert detect_book_mode("zh-TW", EN_BODY_SAMPLE) == "monolingual-zh"


# ── Priority 2: explicit en metadata ─────────────────────────────────────────


@pytest.mark.parametrize("lang", ["en", "en-US", "en-GB", "EN", "eng"])
def test_metadata_en_codes_yield_default(lang: str) -> None:
    assert detect_book_mode(metadata_lang=lang, body_sample=None) == DEFAULT_MODE


def test_metadata_en_overrides_zh_body() -> None:
    """Even with a Chinese body sample, explicit en metadata pins to default."""
    assert detect_book_mode("en", ZH_BODY_SAMPLE) == DEFAULT_MODE


# ── Priority 3: body sample fallback ─────────────────────────────────────────


def test_no_metadata_zh_body_yields_monolingual_zh() -> None:
    assert detect_book_mode(metadata_lang=None, body_sample=ZH_BODY_SAMPLE) == "monolingual-zh"


def test_no_metadata_en_body_yields_default() -> None:
    assert detect_book_mode(metadata_lang=None, body_sample=EN_BODY_SAMPLE) == DEFAULT_MODE


def test_empty_metadata_zh_body_yields_monolingual_zh() -> None:
    """Empty / whitespace metadata is treated the same as missing."""
    assert detect_book_mode(metadata_lang="", body_sample=ZH_BODY_SAMPLE) == "monolingual-zh"
    assert detect_book_mode(metadata_lang="   ", body_sample=ZH_BODY_SAMPLE) == "monolingual-zh"


# ── Priority 4: final fallback to default ───────────────────────────────────


def test_no_signals_yields_default() -> None:
    assert detect_book_mode(metadata_lang=None, body_sample=None) == DEFAULT_MODE


def test_unknown_metadata_no_body_yields_default() -> None:
    """Non-zh non-en metadata + no body falls through to default rather than
    raising — preserves Phase 1 behaviour for outlier languages (German,
    Japanese, etc.) which Phase 1 doesn't model."""
    assert detect_book_mode(metadata_lang="ja", body_sample=None) == DEFAULT_MODE
    assert detect_book_mode(metadata_lang="de", body_sample=None) == DEFAULT_MODE


def test_short_body_falls_through() -> None:
    """Body too short for langdetect → ``unknown`` → falls through to default."""
    assert detect_book_mode(metadata_lang=None, body_sample="hi") == DEFAULT_MODE


def test_default_is_bilingual_en_zh() -> None:
    """Pin the default value — back-compat behaviour for existing
    English-bilingual books that pre-date the pilot."""
    assert DEFAULT_MODE == "bilingual-en-zh"
