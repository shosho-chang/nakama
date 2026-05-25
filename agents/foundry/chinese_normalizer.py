"""Mandarin text normalization layer (ADR-032 §5b).

Handles:
- Full-width / half-width punctuation unification
- Numeric formats: 一萬一千 / 11000 / 11,000 / 一·一萬 → canonical Arabic integer
- Smart SRT cue joining at sentence boundaries (。？！) vs prosody hint
- Taiwan-standard 「」 brackets enforced; Western/mainland forms rewritten + warned
"""

from __future__ import annotations

import logging
import re

import cn2an

logger = logging.getLogger(__name__)

# Punctuation normalization: half-width → full-width
_HALF_TO_FULL = str.maketrans(
    {
        ",": "，",
        "?": "？",
        "!": "！",
        ";": "；",
        ":": "：",
    }
)

# Taiwan quote targets
_QUOTE_MAP: dict[str, str] = {
    "“": "「",  # "  left double quotation mark
    "”": "」",  # "  right double quotation mark
    "‘": "「",  # '  left single quotation mark
    "’": "」",  # '  right single quotation mark
}
_QUOTE_RE = re.compile("[" + re.escape("".join(_QUOTE_MAP)) + "]")

_SENTENCE_END = frozenset("。？！")
_FULL_WIDTH_SPACE = "　"  #

# Thousands-separator commas inside digit runs: 11,000 → 11000
_COMMA_THOUSANDS_RE = re.compile(r"(\d),(\d)")

# Chinese decimal-unit pattern: 一·一萬 → 1.1 × 10000 = 11000
# Covers middle dot U+00B7 (·) and katakana middle dot U+30FB (・)
_CN_DECIMAL_UNIT_RE = re.compile(
    r"([零一二三四五六七八九十百千]+)"
    r"[·・]"
    r"([零一二三四五六七八九]+)"
    r"([萬億])"
)
_UNIT_VALUE: dict[str, int] = {"萬": 10_000, "億": 100_000_000}


def normalize_punctuation(text: str) -> str:
    """Unify full-width punctuation; promote half-width clones to full-width."""
    return text.translate(_HALF_TO_FULL)


def _cn_decimal_unit(m: re.Match) -> str:
    """Replace e.g. 一·一萬 with its canonical Arabic integer string."""
    int_part = cn2an.cn2an(m.group(1), "normal")
    dec_chars = m.group(2)
    dec_val = cn2an.cn2an(dec_chars, "normal")
    dec_frac = dec_val / (10 ** len(dec_chars))
    unit = _UNIT_VALUE[m.group(3)]
    return str(int(round((int_part + dec_frac) * unit)))


def normalize_numbers(text: str) -> str:
    """Normalize numeric formats to canonical Arabic integers embedded in text.

    Processing order:
    1. Strip thousands-separator commas (11,000 → 11000)
    2. Resolve Chinese decimal-unit patterns (一·一萬 → 11000)
    3. Convert remaining Chinese numerals via cn2an (一萬一千 → 11000)
    """
    text = _COMMA_THOUSANDS_RE.sub(r"\1\2", text)
    text = _CN_DECIMAL_UNIT_RE.sub(_cn_decimal_unit, text)
    text = cn2an.transform(text, "cn2an")
    return text


def smart_join_cues(cues: list[str]) -> str:
    """Join SRT cue strings into prose.

    At sentence-ending punctuation (。？！): hard break (no separator).
    Otherwise: insert full-width space to preserve prosody hint.
    """
    if not cues:
        return ""
    parts: list[str] = []
    for i, cue in enumerate(cues):
        text = cue.replace("\n", " ").strip()
        if i > 0 and parts:
            last_char = parts[-1][-1] if parts[-1] else ""
            if last_char not in _SENTENCE_END:
                parts.append(_FULL_WIDTH_SPACE)
        parts.append(text)
    return "".join(parts)


def enforce_taiwan_quotes(text: str) -> str:
    """Replace Western/mainland curly quotes with Taiwan 「」 brackets.

    Logs a warning for each replaced occurrence.
    """

    def _replace(m: re.Match) -> str:
        replacement = _QUOTE_MAP[m.group()]
        logger.warning(
            "Western/mainland quote replaced at position %d: %r → %r",
            m.start(),
            m.group(),
            replacement,
        )
        return replacement

    return _QUOTE_RE.sub(_replace, text)
