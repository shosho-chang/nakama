"""Pinned, deterministic Unicode display and reading metrics.

These metrics deliberately describe *display columns* and *reading units*.
They are not pixel measurements: exact glyph shaping remains a future
HarfBuzz-backed seam.  The implementation uses only Python's pinned Unicode
database so a fresh process can reproduce every value and identity.
"""

from __future__ import annotations

import platform
import sys
import unicodedata
from dataclasses import asdict, dataclass

from .hashing import hash_object

DISPLAY_METRICS_ALGORITHM = "nakama-unicode-display-metrics"
DISPLAY_METRICS_VERSION = 1


@dataclass(frozen=True, slots=True)
class DisplayMetricsIdentity:
    """Executable identity for the approximation used by projection/QC."""

    algorithm: str
    algorithm_version: int
    unicode_version: str
    python_implementation: str
    python_version: str
    python_cache_tag: str
    east_asian_ambiguous_columns: float
    ascii_space_columns: float
    ascii_space_reading_units: float
    shaping_backend: str

    @property
    def content_hash(self) -> str:
        return hash_object(asdict(self))


@dataclass(frozen=True, slots=True)
class TextDisplayMetrics:
    """Deterministic aggregate for a Unicode string."""

    grapheme_count: int
    display_columns: float
    reading_units: float


def display_metrics_identity() -> DisplayMetricsIdentity:
    """Return all runtime/config inputs that can affect the metric result."""

    return DisplayMetricsIdentity(
        algorithm=DISPLAY_METRICS_ALGORITHM,
        algorithm_version=DISPLAY_METRICS_VERSION,
        unicode_version=unicodedata.unidata_version,
        python_implementation=platform.python_implementation(),
        python_version=platform.python_version(),
        python_cache_tag=sys.implementation.cache_tag,
        east_asian_ambiguous_columns=1.0,
        ascii_space_columns=1.0,
        ascii_space_reading_units=0.25,
        shaping_backend="none-not-pixel-exact",
    )


def _is_variation_selector(character: str) -> bool:
    value = ord(character)
    return 0xFE00 <= value <= 0xFE0F or 0xE0100 <= value <= 0xE01EF


def _is_emoji_modifier(character: str) -> bool:
    return 0x1F3FB <= ord(character) <= 0x1F3FF


def _is_regional_indicator(character: str) -> bool:
    return 0x1F1E6 <= ord(character) <= 0x1F1FF


def _is_extender(character: str) -> bool:
    return (
        unicodedata.combining(character) != 0
        or unicodedata.category(character) in {"Mc", "Me"}
        or _is_variation_selector(character)
        or _is_emoji_modifier(character)
        or character == "\u20e3"  # COMBINING ENCLOSING KEYCAP
    )


def grapheme_clusters(text: str) -> tuple[str, ...]:
    """Segment the subset of extended graphemes relevant to subtitle width.

    The algorithm pins combining sequences, variation selectors, emoji skin
    tones, keycaps, regional-indicator flags, and ZWJ emoji sequences.  It is
    intentionally named as a versioned subset rather than claiming complete
    UAX #29 conformance without a dedicated dependency.
    """

    clusters: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        if character == "\r" and index + 1 < len(text) and text[index + 1] == "\n":
            clusters.append("\r\n")
            index += 2
            continue
        cluster = character
        index += 1
        if _is_regional_indicator(character) and index < len(text):
            if _is_regional_indicator(text[index]):
                cluster += text[index]
                index += 1
        while index < len(text) and _is_extender(text[index]):
            cluster += text[index]
            index += 1
        while index < len(text) and text[index] == "\u200d":
            cluster += text[index]
            index += 1
            if index >= len(text):
                break
            cluster += text[index]
            index += 1
            while index < len(text) and _is_extender(text[index]):
                cluster += text[index]
                index += 1
        clusters.append(cluster)
    return tuple(clusters)


def _cluster_is_emoji(cluster: str) -> bool:
    if "\u200d" in cluster or "\u20e3" in cluster:
        return True
    if sum(_is_regional_indicator(character) for character in cluster) >= 2:
        return True
    return any(
        0x1F000 <= ord(character) <= 0x1FAFF or 0x2600 <= ord(character) <= 0x27BF
        for character in cluster
    )


def _base_character(cluster: str) -> str | None:
    for character in cluster:
        if character == "\u200d" or _is_extender(character):
            continue
        return character
    return None


def measure_text(text: str) -> TextDisplayMetrics:
    """Measure text without treating whitespace or combining marks as scalars."""

    display_columns = 0.0
    reading_units = 0.0
    clusters = grapheme_clusters(text)
    for cluster in clusters:
        if cluster in {"\r", "\n", "\r\n"}:
            continue
        if cluster == "\t":
            display_columns += 4.0
            reading_units += 1.0
            continue
        if cluster.isspace():
            display_columns += 1.0
            reading_units += 0.25
            continue
        if _cluster_is_emoji(cluster):
            display_columns += 2.0
            reading_units += 1.0
            continue
        base = _base_character(cluster)
        if base is None:
            # A standalone combining mark occupies no independent column but
            # remains a small, explicit reading burden instead of disappearing.
            reading_units += 0.25
            continue
        width = unicodedata.east_asian_width(base)
        display_columns += 2.0 if width in {"W", "F"} else 1.0
        category = unicodedata.category(base)
        if width in {"W", "F"}:
            reading_units += 1.0
        elif category.startswith(("L", "N")):
            reading_units += 0.5
        elif category.startswith("P") or category.startswith("S"):
            reading_units += 0.25
        else:
            reading_units += 0.5
    return TextDisplayMetrics(
        grapheme_count=len(clusters),
        display_columns=display_columns,
        reading_units=reading_units,
    )


def display_columns(text: str) -> float:
    return measure_text(text).display_columns


def reading_units(text: str) -> float:
    return measure_text(text).reading_units


__all__ = [
    "DISPLAY_METRICS_ALGORITHM",
    "DISPLAY_METRICS_VERSION",
    "DisplayMetricsIdentity",
    "TextDisplayMetrics",
    "display_columns",
    "display_metrics_identity",
    "grapheme_clusters",
    "measure_text",
    "reading_units",
]
