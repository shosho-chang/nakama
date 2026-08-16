"""Deterministic, non-mutating editorial checks for Nakama subtitles.

The checker deliberately never returns rewritten canonical text.  OpenCC is
used only as a conservative detector for text that deserves review; accepting
or replacing a lexeme remains an audio-backed Correction Decision.
"""

from __future__ import annotations

import importlib.metadata
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from importlib import resources
from typing import Callable

from opencc import OpenCC

from .hashing import hash_object, sha256_bytes

HOUSE_STYLE_VERSION = "nakama-zh-hant-verbatim-v2"
ALLOWED_PUNCTUATION = frozenset("《》「」")
_OPEN_TO_CLOSE = {"《": "》", "「": "」"}
_CLOSE_TO_OPEN = {value: key for key, value in _OPEN_TO_CLOSE.items()}


@dataclass(frozen=True, slots=True)
class EditorialFinding:
    """One review signal, addressed by Unicode-scalar offsets."""

    code: str
    positions: tuple[int, ...]
    observed: str
    detector_output: str | None = None

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.positions:
            raise ValueError("editorial finding requires a code and positions")
        if tuple(sorted(set(self.positions))) != self.positions:
            raise ValueError("editorial finding positions must be sorted and unique")
        if not self.observed:
            raise ValueError("editorial finding requires observed text")


@lru_cache(maxsize=1)
def _traditional_converter() -> OpenCC:
    # ``s2tw`` applies Simplified/Traditional phrase context plus Taiwan
    # character variants.  Unlike ``s2twp`` it does not rewrite Taiwan lexical
    # phrases (for example 類型→型別 or 對象→物件), while it correctly preserves
    # modern Taiwan Traditional forms such as 群 and 床 instead of proposing the
    # historical variants 羣 and 牀.  Any remaining difference is still only a
    # review signal; this checker never rewrites canonical transcript text.
    return OpenCC("s2tw")


@lru_cache(maxsize=1)
def _opencc_character_dictionary() -> bytes:
    return (
        resources.files("opencc")
        .joinpath("dictionary", "STCharacters.txt")
        .read_bytes()
    )


@lru_cache(maxsize=1)
def _opencc_phrase_dictionary() -> bytes:
    return (
        resources.files("opencc")
        .joinpath("dictionary", "STPhrases.txt")
        .read_bytes()
    )


@lru_cache(maxsize=1)
def _ambiguous_source_characters() -> frozenset[str]:
    """Return characters that are valid in both writing systems.

    OpenCC intentionally maps contextual forms such as ``丑`` to ``醜`` while
    retaining ``丑`` as an alternative.  A raw ``s2t`` difference is therefore
    not, by itself, proof of Simplified Chinese.  Self-mapped alternatives are
    excluded from the fail-closed detector and can still be raised by the
    language/reference review stages when their surrounding context warrants it.
    """

    ambiguous: set[str] = set()
    for line in _opencc_character_dictionary().decode("utf-8").splitlines():
        source, alternatives = line.split("\t", maxsplit=1)
        if len(source) == 1 and source in alternatives.split():
            ambiguous.add(source)
    return frozenset(ambiguous)


@lru_cache(maxsize=1)
def _preferred_self_mapped_source_characters() -> frozenset[str]:
    """Return ambiguous sources OpenCC lists as the preferred first form.

    A phrase-only change away from this preferred form (for example 娘→孃) is
    an orthographic preference rather than sufficient Simplified evidence.
    """

    preferred: set[str] = set()
    for line in _opencc_character_dictionary().decode("utf-8").splitlines():
        source, alternatives = line.split("\t", maxsplit=1)
        choices = alternatives.split()
        if len(source) == 1 and len(choices) > 1 and choices[0] == source:
            preferred.add(source)
    return frozenset(preferred)


@lru_cache(maxsize=1)
def _simplified_phrase_mappings() -> tuple[dict[str, str], int, int]:
    """Load the phrase layer that disambiguates self-mapped characters."""

    mappings: dict[str, str] = {}
    for line in _opencc_phrase_dictionary().decode("utf-8").splitlines():
        source, alternatives = line.split("\t", maxsplit=1)
        if len(source) > 1:
            mappings[source] = alternatives.split()[0]
    lengths = tuple(map(len, mappings))
    return mappings, min(lengths), max(lengths)


def _matched_simplified_phrases(text: str) -> tuple[tuple[int, int, str], ...]:
    """Return the non-overlapping STPhrases matches selected by OpenCC.

    ``opencc-python-reimplemented`` selects the longest dictionary match in a
    segment, breaking ties from left to right, then applies the same maximum
    length to the unmatched regions.  Replaying that selection preserves the
    source spans that supplied phrase-level context without relying on OpenCC's
    private runtime attributes.
    """

    mappings, minimum_length, maximum_length = _simplified_phrase_mappings()
    matches: list[tuple[int, int, str]] = []
    pending = [(0, len(text), maximum_length)]
    while pending:
        segment_start, segment_end, length_hint = pending.pop()
        segment_length = segment_end - segment_start
        selected: tuple[int, int, str] | None = None
        for length in range(min(segment_length, length_hint), minimum_length - 1, -1):
            for start in range(segment_start, segment_end - length + 1):
                source = text[start : start + length]
                target = mappings.get(source)
                if target is not None:
                    selected = (start, start + length, target)
                    break
            if selected is not None:
                break
        if selected is None:
            continue

        start, end, target = selected
        matches.append(selected)
        if segment_start < start:
            pending.append((segment_start, start, end - start))
        if end < segment_end:
            pending.append((end, segment_end, end - start))
    return tuple(sorted(matches))


def editorial_detector_identity() -> str:
    """Stable dependency/config identity to bind into the generation policy."""

    return hash_object(
        {
            "house_style_version": HOUSE_STYLE_VERSION,
            "allowed_punctuation": "".join(sorted(ALLOWED_PUNCTUATION)),
            "traditional_detector": "opencc:s2tw:contextual-self-mappings-v3",
            "opencc_character_dictionary_sha256": sha256_bytes(
                _opencc_character_dictionary()
            ),
            "opencc_phrase_dictionary_sha256": sha256_bytes(
                _opencc_phrase_dictionary()
            ),
            "opencc_version": importlib.metadata.version("opencc-python-reimplemented"),
            "unicode_database_version": unicodedata.unidata_version,
        }
    )


def _traditional_differences(
    original: str,
    converted: str,
) -> tuple[tuple[tuple[int, ...], str], ...]:
    """Address detector differences without treating conversion as truth."""

    differences: list[tuple[tuple[int, ...], str]] = []
    for tag, source_start, source_end, target_start, target_end in SequenceMatcher(
        None,
        original,
        converted,
        autojunk=False,
    ).get_opcodes():
        if tag == "equal":
            continue
        if source_start < source_end:
            positions = tuple(range(source_start, source_end))
        else:
            # An inserted detector character has no exact source character.  Tie
            # the review signal to its nearest scalar; never manufacture a new
            # canonical boundary or timing anchor for it.
            positions = (min(source_start, len(original) - 1),)
        differences.append((positions, converted[target_start:target_end]))
    return tuple(differences)


def _unambiguous_traditional_differences(
    original: str,
    converted: str,
    *,
    converter: Callable[[str], str],
) -> tuple[tuple[tuple[int, ...], str], ...]:
    """Keep unambiguous differences plus phrase-proven ambiguous forms."""

    filtered: list[tuple[tuple[int, ...], str]] = []
    ambiguous = _ambiguous_source_characters()
    preferred_sources = _preferred_self_mapped_source_characters()
    context_positions: set[int] = set()
    for phrase_start, phrase_end, _target in _matched_simplified_phrases(original):
        observed_phrase = original[phrase_start:phrase_end]
        converted_phrase = converter(observed_phrase)
        for positions, detector_output in _traditional_differences(
            observed_phrase,
            converted_phrase,
        ):
            if not any(
                observed_phrase[position] in ambiguous
                and observed_phrase[position] not in preferred_sources
                for position in positions
            ):
                continue
            absolute_positions = tuple(phrase_start + position for position in positions)
            filtered.append((absolute_positions, detector_output))
            context_positions.update(absolute_positions)

    for positions, _detector_output in _traditional_differences(original, converted):
        eligible = [
            position
            for position in positions
            if original[position] not in ambiguous and position not in context_positions
        ]
        if not eligible:
            continue

        run: list[int] = []
        for position in eligible:
            if run and position != run[-1] + 1:
                observed = "".join(original[index] for index in run)
                output = converter(observed)
                if output != observed:
                    filtered.append((tuple(run), output))
                run = []
            run.append(position)
        if run:
            observed = "".join(original[index] for index in run)
            output = converter(observed)
            if output != observed:
                filtered.append((tuple(run), output))
    return tuple(filtered)


def inspect_editorial_text(
    text: str,
    *,
    convert_to_traditional: Callable[[str], str] | None = None,
) -> tuple[EditorialFinding, ...]:
    """Inspect one continuous transcript without changing any character."""

    if not isinstance(text, str) or not text:
        raise ValueError("editorial inspection requires non-empty text")

    findings: list[EditorialFinding] = []
    for position, character in enumerate(text):
        internal_ascii_orthography = False
        if character in {"'", "-", "."} and 0 < position < len(text) - 1:
            previous = text[position - 1]
            following = text[position + 1]
            internal_ascii_orthography = (
                previous.isascii()
                and previous.isalnum()
                and following.isascii()
                and following.isalnum()
            )
        if (
            unicodedata.category(character).startswith("P")
            and character not in ALLOWED_PUNCTUATION
            and not internal_ascii_orthography
        ):
            findings.append(
                EditorialFinding(
                    code="forbidden_punctuation",
                    positions=(position,),
                    observed=character,
                )
            )

    stack: list[tuple[str, int]] = []
    for position, character in enumerate(text):
        if character in _OPEN_TO_CLOSE:
            stack.append((character, position))
        elif character in _CLOSE_TO_OPEN:
            expected_open = _CLOSE_TO_OPEN[character]
            if stack and stack[-1][0] == expected_open:
                stack.pop()
            else:
                mismatch_positions = (position,)
                observed = character
                if stack:
                    opening, opening_position = stack.pop()
                    mismatch_positions = tuple(sorted((opening_position, position)))
                    observed = f"{opening}{character}"
                findings.append(
                    EditorialFinding(
                        code="unbalanced_house_delimiter",
                        positions=mismatch_positions,
                        observed=observed,
                    )
                )
    findings.extend(
        EditorialFinding(
            code="unbalanced_house_delimiter",
            positions=(position,),
            observed=opening,
        )
        for opening, position in stack
    )

    converter = convert_to_traditional or _traditional_converter().convert
    converted = converter(text)
    if converted != text:
        differences = _traditional_differences(text, converted)
        if convert_to_traditional is None:
            differences = _unambiguous_traditional_differences(
                text,
                converted,
                converter=converter,
            )
        findings.extend(
            EditorialFinding(
                code="simplified_chinese_suspected",
                positions=positions,
                observed="".join(text[position] for position in positions),
                detector_output=detector_output,
            )
            for positions, detector_output in differences
        )

    return tuple(
        sorted(
            findings,
            key=lambda item: (item.positions[0], item.code, item.positions),
        )
    )


__all__ = [
    "ALLOWED_PUNCTUATION",
    "EditorialFinding",
    "HOUSE_STYLE_VERSION",
    "editorial_detector_identity",
    "inspect_editorial_text",
]
