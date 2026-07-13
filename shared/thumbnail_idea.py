"""Parse thumbnail idea text → render variables (ADR-033 D3 + ADR-036).

The LLM brainstorm step writes each idea as a Markdown block with a fixed
5-line render shape plus optional workflow metadata:

    archetype: [T-A2, T-V4, JP-3]   (optional)
    lane: Jeff Clean Tutorial       (optional)
    recipe: jeff_clean_tutorial...  (optional)
    title_pairing: ...              (optional)

    大字：{3-5 字 hook}
    我的表情：{emotion — accepts English key, zh-Hant label, or alias}
    視覺：{free-form description}
    數字/圖示：{free-form description, may be "無"}
    背景：{free-form description, used as Unsplash query or gradient cue}

修修 is allowed to edit the textarea freely. The parser is forgiving on
whitespace + colon variants (full-width ``：`` vs ASCII ``:``) and tolerates
extra prose between lines (only the first match per label wins). The render
endpoint only requires the 5 legacy render lines; metadata is preserved for
workflow traceability and future asset sourcing.

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
    "lane": re.compile(
        r"^[ \t]*(?:lane|風格路線|路線)\s*[：:]\s*(.+?)\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    "recipe_id": re.compile(
        r"^[ \t]*(?:recipe|recipe_id|縮圖配方|配方)\s*[：:]\s*(.+?)\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    "reference_template_id": re.compile(
        (
            r"^[ \t]*(?:reference_template|reference template|template_id|"
            r"template|參考模板|參考版型|版型)\s*[：:]\s*(.+?)\s*$"
        ),
        re.MULTILINE | re.IGNORECASE,
    ),
    "title_pairing": re.compile(
        (
            r"^[ \t]*(?:title_pairing|title pairing|publish_title|publish title|"
            r"標題搭配|搭配標題|發布標題|title)\s*[：:]\s*(.+?)\s*$"
        ),
        re.MULTILINE | re.IGNORECASE,
    ),
    "asset_queries": re.compile(
        r"^[ \t]*(?:素材需求|asset_plan|asset_queries|assets|素材)\s*[：:]\s*(.+?)\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    "component_type": re.compile(
        r"^[ \t]*(?:component|component_type|payload_component|元件|主元件)\s*[：:]\s*(.+?)\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    "component_text": re.compile(
        (
            r"^[ \t]*(?:component_text|component text|元件文字|卡片文字|"
            r"payload_text)\s*[：:]\s*(.+?)\s*$"
        ),
        re.MULTILINE | re.IGNORECASE,
    ),
    "host_directive": re.compile(
        r"^[ \t]*(?:host|person|portrait|人像|人物|主體)\s*[：:]\s*(.+?)\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    "viewer_promise": re.compile(
        r"^[ \t]*(?:viewer_promise|viewer promise|觀眾承諾|觀眾期待)\s*[：:]\s*(.+?)\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    "evidence_fit": re.compile(
        r"^[ \t]*(?:evidence_fit|evidence fit|證據貼合|證據適配)\s*[：:]\s*(.+?)\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
    "trust_risk": re.compile(
        r"^[ \t]*(?:trust_risk|trust risk|信任風險|信任檢查)\s*[：:]\s*(.+?)\s*$",
        re.MULTILINE | re.IGNORECASE,
    ),
}

# Optional archetype-tags line (ADR-033 D4 playbook integration, v1.1+).
# Examples accepted:
#   archetype: [T-A2, T-V4, JP-3]
#   archetype: T-A2, T-V4
#   archetypes: [T-A1]
#   架構: T-A2 / T-V4 / JP-3
_ARCHETYPE_LINE_PATTERN = re.compile(
    r"^[ \t]*(?:archetype|archetypes|架構|archetype_tags|tags)\s*[：:]\s*\[?([^\]\n]+?)\]?\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_ARCHETYPE_ID_PATTERN = re.compile(r"\b(T-[AV]\d+|JP-\d+|M-\d+)\b", re.IGNORECASE)
_KNOWN_REFERENCE_TEMPLATE_IDS = (
    "jeff_tool_header_panel",
    "jeff_command_panel",
    "ali_metric_arrow",
    "ali_social_quote_card",
    "shosho_benefit_list_card",
)


@dataclass(frozen=True)
class ParsedIdea:
    """Structured form of a thumbnail idea — feeds the render endpoint."""

    hook: str
    emotion_key: str  # canonical English key (post-resolve)
    emotion_input: str  # original text 修修 typed (kept for echo back)
    visual: str
    decoration: str  # may be "" or "無"
    bg: str
    archetype_tags: tuple[str, ...] = ()  # ADR-033 D4 playbook IDs; empty = legacy/no-tag
    lane: str = ""  # e.g. "Ali Warm Explainer" / "Jeff Clean Tutorial"
    recipe_id: str = ""  # focused workflow recipe card id
    reference_template_id: str = ""  # concrete Ali/Jeff/Shosho visual template to match
    title_pairing: str = ""  # title candidate this thumbnail is designed to pair with
    asset_queries: tuple[str, ...] = ()  # stock/Envato search needs; no download yet
    component_type: str = ""  # primary component slot chosen from the reference template
    component_text: tuple[str, ...] = ()  # short labels rendered inside the primary component
    host_directive: str = ""  # compact portrait/pose placement direction
    viewer_promise: str = ""  # why this title/thumbnail pair earns the click
    evidence_fit: str = ""  # why the visual promise matches the script evidence
    trust_risk: str = ""  # health/longevity overclaim or credibility risk check


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

    # Optional archetype-tags line (post-v1.1 playbook integration).
    archetype_tags = _extract_archetype_tags(text)

    return ParsedIdea(
        hook=captured["hook"],
        emotion_key=emotion_key,
        emotion_input=emotion_input,
        visual=captured["visual"],
        decoration=decoration,
        bg=captured["bg"],
        archetype_tags=archetype_tags,
        lane=captured.get("lane", "").strip(),
        recipe_id=captured.get("recipe_id", "").strip(),
        reference_template_id=_clean_reference_template_id(
            captured.get("reference_template_id", "")
        ),
        title_pairing=captured.get("title_pairing", "").strip(),
        asset_queries=_split_asset_queries(captured.get("asset_queries", "")),
        component_type=captured.get("component_type", "").strip(),
        component_text=_split_component_text(captured.get("component_text", "")),
        host_directive=captured.get("host_directive", "").strip(),
        viewer_promise=captured.get("viewer_promise", "").strip(),
        evidence_fit=captured.get("evidence_fit", "").strip(),
        trust_risk=captured.get("trust_risk", "").strip(),
    )


def _extract_archetype_tags(text: str) -> tuple[str, ...]:
    """Pull `archetype: [T-A1, T-V3, JP-2]` line if present.

    Tolerant of brackets, separators, and missing colons. Returns empty tuple
    when no archetype line found (backward-compatible with legacy ideas).
    """
    m = _ARCHETYPE_LINE_PATTERN.search(text)
    if not m:
        return ()
    raw = m.group(1)
    found = [tag.upper().replace("M-", "M-") for tag in _ARCHETYPE_ID_PATTERN.findall(raw)]
    # Dedup while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for tag in found:
        norm = tag.upper()
        if norm not in seen:
            seen.add(norm)
            out.append(norm)
    return tuple(out)


def _split_asset_queries(raw: str) -> tuple[str, ...]:
    """Split an optional one-line asset plan into compact query strings."""
    if not raw:
        return ()
    if raw.strip() in ("無", "none", "None", "n/a", "N/A"):
        return ()
    parts = re.split(r"[;；、]\s*|,\s*(?=[A-Za-z0-9一-龥])", raw)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = part.strip(" -\t\r\n")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return tuple(out)


def _split_component_text(raw: str) -> tuple[str, ...]:
    """Split primary component labels into short readable chunks."""
    if not raw:
        return ()
    if raw.strip() in ("無", "none", "None", "n/a", "N/A"):
        return ()
    parts = re.split(r"\s*/\s*|[;；、]\s*|,\s*", raw)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cleaned = part.strip(" -\t\r\n")
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return tuple(out)


def _clean_reference_template_id(raw: str) -> str:
    """Normalize common LLM suffixes like ``shosho_benefit_list_card (T01)``."""

    cleaned = (raw or "").strip()
    if not cleaned:
        return ""
    for template_id in _KNOWN_REFERENCE_TEMPLATE_IDS:
        if template_id in cleaned:
            return template_id
    return re.split(r"\s+", cleaned, maxsplit=1)[0].strip()


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
