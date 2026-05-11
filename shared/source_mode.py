"""Source mode resolution for monolingual-zh pilot (PRD #507 Phase 1).

A ``Mode`` value identifies which reading-pipeline a source belongs to:

- ``"monolingual-zh"`` — single-language zh source (台版中譯 EPUB or
  pure-zh web article); no English original; no translation toggle; no
  cross-lingual ingest.
- ``"bilingual-en-zh"`` — historical default: paired EN original +
  Immersive-Translate-derived bilingual rendering.

This module owns the **detection** policy. EPUB metadata ``lang`` is the
strongest signal; body sample (via ``shared.lang_detect``) is the
fallback; absent both, we preserve the existing default
(``bilingual-en-zh``) so back-compat for the existing English-textbook
pipeline holds.

Schemas / DB / route consumers import the ``Mode`` Literal and the
``detect_book_mode`` function only — keep this module dependency-free
beyond ``shared.lang_detect``.
"""

from __future__ import annotations

from typing import Literal

from shared.lang_detect import detect_lang

Mode = Literal["monolingual-zh", "bilingual-en-zh"]

DEFAULT_MODE: Mode = "bilingual-en-zh"
"""Fallback when neither ``metadata.lang`` nor body sample yields a
zh signal. Matches existing pre-pilot behaviour so back-compat holds."""


def _is_zh_lang_code(lang: str | None) -> bool:
    """Match permissive zh codes: ``zh``, ``zh-TW``, ``zh_TW``, ``zh-Hant``,
    ``zho``, ``chi``. Underscore-form normalised to hyphen first.

    The EPUB metadata ``<dc:language>`` field is wild — Project Gutenberg
    uses ``zh``, 台版中譯 publishers use ``zh-TW``, 中國 publishers use
    ``zh-CN``, some older toolchains emit ISO 639-2 ``zho`` or ``chi``.
    All of them are zh as far as Phase 1 is concerned.
    """
    if not lang:
        return False
    normalised = lang.strip().lower().replace("_", "-")
    return (
        normalised.startswith("zh")
        or normalised == "zho"
        or normalised == "chi"
    )


def _is_en_lang_code(lang: str | None) -> bool:
    if not lang:
        return False
    normalised = lang.strip().lower().replace("_", "-")
    return normalised.startswith("en") or normalised == "eng"


def detect_book_mode(
    metadata_lang: str | None,
    body_sample: str | None,
) -> Mode:
    """Resolve the ``Mode`` for an EPUB upload.

    Priority:

    1. ``metadata_lang`` zh-anything → ``"monolingual-zh"`` (台版中譯
       publishers reliably tag this)
    2. ``metadata_lang`` en-anything → ``"bilingual-en-zh"`` (existing
       English-source default; treats English EPUB as bilingual-pipeline
       candidate even though the bilingual rendering is produced
       out-of-band)
    3. ``body_sample`` → ``shared.lang_detect.detect_lang``; ``zh-Hant``
       → ``"monolingual-zh"``, otherwise fall through
    4. Final fallback → ``DEFAULT_MODE`` (preserve back-compat behaviour)

    The function is pure and deterministic given its inputs — used both
    in production wiring and in tests.
    """
    if _is_zh_lang_code(metadata_lang):
        return "monolingual-zh"
    if _is_en_lang_code(metadata_lang):
        return DEFAULT_MODE
    if body_sample:
        body_lang = detect_lang(body_sample)
        if body_lang == "zh-Hant":
            return "monolingual-zh"
    return DEFAULT_MODE
