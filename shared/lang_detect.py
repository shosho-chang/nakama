"""Thin language detection wrapper for the monolingual-zh pilot.

Two-language Nakama universe: only ``zh-Hant`` and ``en`` are recognised
(per CONTEXT-MAP — Simplified Chinese / Japanese / Korean are out of scope
and fold into ``zh-Hant`` for Phase 1 detection purposes; user override
exists for the rare miss).

Detection strategy is a two-step ladder:

1. **CJK Han fast-path** — count Han characters vs Hangul / Kana. The
   ``langdetect`` library has a known weakness on shorter Chinese text
   (often misclassifies as Korean despite 0 Hangul characters); the
   Unicode block heuristic is far more reliable for our two-language
   universe and runs in O(n) without a model.
2. **langdetect fallback** — for text without enough Han to be confident,
   defer to the library, which discriminates English well.

Anything < 30 NFC characters returns ``"unknown"`` because language ID
on a phrase is a coin toss.

Used by ``shared.source_mode.detect_book_mode`` when EPUB metadata
``lang`` is missing or ambiguous.
"""

from __future__ import annotations

import unicodedata
from typing import Literal

from langdetect import DetectorFactory, LangDetectException, detect

# Deterministic output — ``langdetect`` seeds a PRNG per detection
# without this. Setting once at import time matches the library's
# documented usage pattern.
DetectorFactory.seed = 0

LangLabel = Literal["zh-Hant", "en", "unknown"]

_MIN_CHARS = 30
"""Below this many NFC-normalised characters, return ``"unknown"`` — the
library's confidence on shorter strings is essentially noise."""

_HAN_RATIO_ZH_THRESHOLD = 0.30
"""Han-character density above which we declare Chinese without consulting
langdetect. Calibrated above the typical Japanese-text density (which
mixes Han with kana) and well above zero so an occasional Han loanword in
English text does not flip the verdict."""


def _is_han(ch: str) -> bool:
    """Match the CJK Unified Ideographs blocks (Han characters used by
    Chinese, Japanese kanji, and Korean hanja). Excludes punctuation."""
    cp = ord(ch)
    return (
        0x4E00 <= cp <= 0x9FFF       # CJK Unified Ideographs
        or 0x3400 <= cp <= 0x4DBF    # CJK Unified Ideographs Extension A
        or 0x20000 <= cp <= 0x2A6DF  # Extension B
    )


def _is_hangul(ch: str) -> bool:
    cp = ord(ch)
    return (
        0xAC00 <= cp <= 0xD7AF        # Hangul syllables
        or 0x1100 <= cp <= 0x11FF     # Hangul Jamo
        or 0x3130 <= cp <= 0x318F     # Hangul Compatibility Jamo
    )


def _is_kana(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x3040 <= cp <= 0x309F  # Hiragana
        or 0x30A0 <= cp <= 0x30FF  # Katakana
    )


def _looks_chinese_by_unicode(text: str) -> bool:
    """Han-heavy and Korean/Japanese-free → almost certainly Chinese."""
    han = sum(1 for ch in text if _is_han(ch))
    if han == 0:
        return False
    # Any Hangul or kana → not Chinese for Phase 1 purposes
    if any(_is_hangul(ch) or _is_kana(ch) for ch in text):
        return False
    return (han / len(text)) >= _HAN_RATIO_ZH_THRESHOLD


def detect_lang(text: str) -> LangLabel:
    """Return ``"zh-Hant"``, ``"en"``, or ``"unknown"`` for the input.

    Any ``zh*`` ISO code (zh, zh-cn, zh-tw, etc.) folds to ``"zh-Hant"`` —
    Nakama's two-language model treats Simplified as the same surface for
    pilot purposes; the rare miss is handled by user override at upload.
    """
    if not text:
        return "unknown"
    normalised = unicodedata.normalize("NFC", text).strip()
    if len(normalised) < _MIN_CHARS:
        return "unknown"
    # Fast path — Han-character density is a much more reliable Chinese
    # signal than langdetect's profiles for our text shapes.
    if _looks_chinese_by_unicode(normalised):
        return "zh-Hant"
    try:
        code = detect(normalised)
    except LangDetectException:
        return "unknown"
    if code.startswith("zh"):
        return "zh-Hant"
    if code.startswith("en"):
        return "en"
    return "unknown"
