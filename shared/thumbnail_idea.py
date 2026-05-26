"""Parse 5-line thumbnail idea text → render variables (ADR-033 D3).

The LLM brainstorm step writes each idea as a Markdown block with a fixed
5-line shape:

    大字：{3-5 字 hook}
    我的表情：{emotion — accepts English key, zh-Hant label, or alias}
    視覺：{free-form description}
    數字/圖示：{free-form description, may be "無"}
    背景：{free-form description, used as Unsplash query or gradient cue}

修修 is allowed to edit the textarea freely. The parser is forgiving on
whitespace + colon variants (full-width ``：`` vs ASCII ``:``) and tolerates
extra prose between lines (only the first match per label wins).

Emotion resolution flows through ``shared.cutout_library.resolve_emotion``
which handles the alias map (e.g. 驚訝 → surprised).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shared.cutout_library import EmotionLookupError, resolve_emotion

# Label → regex. Each line is matched independently; we accept either of the
# colons used in the wild (full-width Chinese colon ``：`` and ASCII ``:``).
# Trailing whitespace is normalised. Multi-line values are NOT supported in v1
# — keeping the format strict-enough for round-trip with the LLM prompt.
_LABEL_PATTERNS: dict[str, re.Pattern[str]] = {
    "hook": re.compile(
        r"^[ \t]*(?:大字|大字 hook|hook)\s*[：:]\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE
    ),
    "emotion": re.compile(
        r"^[ \t]*(?:我的表情|表情|emotion)\s*[：:]\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE
    ),
    "visual": re.compile(
        r"^[ \t]*(?:視覺|visual)\s*[：:]\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE
    ),
    "decoration": re.compile(
        r"^[ \t]*(?:數字/圖示|數字圖示|圖示|decoration|accent)\s*[：:]\s*(.+?)\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    "bg": re.compile(
        r"^[ \t]*(?:背景|bg|background)\s*[：:]\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE
    ),
}


@dataclass(frozen=True)
class ParsedIdea:
    """Structured form of a thumbnail idea — feeds the render endpoint."""

    hook: str
    emotion_key: str  # canonical English key (post-resolve)
    emotion_input: str  # original text 修修 typed (kept for echo back)
    visual: str
    decoration: str  # may be "" or "無"
    bg: str


class IdeaParseError(ValueError):
    """Raised when the 5-line idea can't be parsed.

    The exception message lists which lines were missing so the caller
    (Bridge endpoint) can surface a clear inline error to 修修 without an
    extra round-trip.
    """

    def __init__(self, missing: list[str], raw: str) -> None:
        self.missing = missing
        self.raw = raw
        super().__init__(
            f"thumbnail idea missing required lines: {', '.join(missing)}. "
            f"Use the 5-line format: 大字 / 我的表情 / 視覺 / 數字/圖示 / 背景."
        )


def parse_idea(text: str) -> ParsedIdea:
    """Parse a 5-line idea block. Raises :class:`IdeaParseError` if incomplete
    or :class:`EmotionLookupError` if the emotion line can't be resolved.

    The parser is permissive on extra blank lines, comments, or prose between
    the 5 labels — only the labelled lines are extracted.
    """
    captured: dict[str, str] = {}
    for label, pattern in _LABEL_PATTERNS.items():
        m = pattern.search(text)
        if m:
            captured[label] = m.group(1).strip()

    required = {"hook", "emotion", "visual", "bg"}
    missing = sorted(required - captured.keys())
    if missing:
        raise IdeaParseError(missing=missing, raw=text)

    # `decoration` is optional; missing → empty string. Explicit "無" also → "".
    decoration_raw = captured.get("decoration", "").strip()
    decoration = "" if decoration_raw in ("", "無", "none", "None") else decoration_raw

    emotion_input = captured["emotion"]
    # Re-raise EmotionLookupError as-is — the message already lists the
    # canonical zh_tw options, which is exactly what the UI wants to show.
    emotion_key = resolve_emotion(emotion_input)

    return ParsedIdea(
        hook=captured["hook"],
        emotion_key=emotion_key,
        emotion_input=emotion_input,
        visual=captured["visual"],
        decoration=decoration,
        bg=captured["bg"],
    )


def parse_ideas_batch(text: str) -> list[ParsedIdea]:
    """Parse an LLM brainstorm response containing multiple ideas.

    Convention: ideas are separated by a line starting with ``---`` (three
    or more dashes) OR by a line matching ``Idea 1`` / ``候選 1`` heading.
    Returns parsed ideas in the order encountered; raises on the first
    parsing failure with the offending block's text retained on the exception.
    """
    blocks = _split_idea_blocks(text)
    parsed: list[ParsedIdea] = []
    for block in blocks:
        try:
            parsed.append(parse_idea(block))
        except (IdeaParseError, EmotionLookupError):
            raise
    return parsed


_BLOCK_SPLIT = re.compile(
    r"^[ \t]*(?:-{3,}|Idea\s*\d+|候選\s*\d+|#+\s*Idea\s*\d+|#+\s*候選\s*\d+)\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def _split_idea_blocks(text: str) -> list[str]:
    parts = [p.strip() for p in _BLOCK_SPLIT.split(text) if p.strip()]
    # Keep only blocks that contain at least the 大字 label — discards LLM
    # preamble ("Here are 3 ideas in the requested format:") before the first
    # separator.
    return [p for p in parts if _LABEL_PATTERNS["hook"].search(p)]
