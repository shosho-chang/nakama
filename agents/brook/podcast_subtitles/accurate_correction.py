"""Conservative, local correction for the focused subtitle pipeline.

This module deliberately does less than the full V2 canonicalisation path.  It
keeps the primary recogniser's clock, uses a second recogniser only as
corroboration, and treats reference text as spelling evidence rather than as a
transcript.  A disagreement that cannot be justified is emitted for review;
it never prevents callers from rendering the corrected token stream.
"""

from __future__ import annotations

import json
import re
import unicodedata
from bisect import bisect_right
from dataclasses import asdict, dataclass, replace
from difflib import SequenceMatcher
from hashlib import sha256
from typing import Any, Literal, Mapping, Sequence

from opencc import OpenCC
from pypinyin import Style, lazy_pinyin

from shared.schemas.podcast_subtitles_v2 import (
    EvidenceToken,
    RecognitionEvidence,
    recognition_evidence_content_hash,
)

_SCHEMA_VERSION = 1
_MAX_REFERENCE_TERM_LENGTH = 16
_MAX_ALIGNMENT_WINDOW_MS = 12_000
_ALIGNMENT_GAP_MS = 700
_SHORT_EQUAL_ISLAND = 4
_ALIGNMENT_ANCHOR_LENGTH = 6
_OPENCC = OpenCC("s2tw")
_TERM_RUN_RE = re.compile(r"[A-Za-z0-9\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_HAN_TEXT_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_ASCII_TEXT_RE = re.compile(r"[A-Za-z]+")
_CJK_NUMERAL_RE = re.compile(r"[零〇一二兩两三四五六七八九十]+")
_ATOMIC_RE = re.compile(
    r"\s+|[A-Za-z0-9]+(?:['’.-][A-Za-z0-9]+)*|"
    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]|[^\s]"
)


ReferenceKind = Literal["book", "outline", "glossary"]
DecisionStatus = Literal["applied", "unresolved"]
CorrectionStatus = Literal["completed", "completed_with_review"]
ReviewPriority = Literal["normal", "high"]
ReviewChoice = Literal["current", "candidate", "defer"]


@dataclass(frozen=True, slots=True)
class CorrectionReferenceSource:
    """Explicit local reference text and its human-readable origin."""

    source_id: str
    kind: ReferenceKind
    locator: str
    text: str
    title: str | None = None

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("reference source_id must not be blank")
        if not self.locator.strip():
            raise ValueError("reference locator must not be blank")
        if not self.text.strip():
            raise ValueError("reference text must not be blank")


@dataclass(frozen=True, slots=True)
class RecognitionLineage:
    evidence_hash: str
    adapter: str
    model: str
    token_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CorrectionReference:
    source_id: str
    kind: Literal["book", "outline", "glossary", "tool"]
    locator: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class CorrectionDecision:
    id: str
    status: DecisionStatus
    category: str
    start_ms: int
    end_ms: int
    current: str
    candidates: tuple[str, ...]
    selected: str | None
    reason: str
    recognition_lineage: tuple[RecognitionLineage, ...]
    references: tuple[CorrectionReference, ...] = ()
    # Absolute character coordinates in ``AccurateCorrectionResult.text``.
    # Older correction JSON did not carry these additive fields; ``None`` is
    # therefore retained only for backwards-compatible parsing.  New
    # unresolved lexical decisions always bind their exact local subspan.
    target_start_char: int | None = None
    target_end_char: int | None = None


@dataclass(frozen=True, slots=True)
class CorrectedTimedToken:
    id: str
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None
    speaker: str | None
    source_primary_token_ids: tuple[str, ...]
    recognition_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AccurateCorrectionResult:
    schema_version: int
    episode_id: str
    normalized_audio_hash: str
    status: CorrectionStatus
    tokens: tuple[CorrectedTimedToken, ...]
    applied: tuple[CorrectionDecision, ...]
    unresolved: tuple[CorrectionDecision, ...]

    @property
    def text(self) -> str:
        return "".join(token.text for token in self.tokens)


@dataclass(frozen=True, slots=True)
class CorrectionReviewPacket:
    """One unresolved decision plus bounded primary-transcript context."""

    decision_id: str
    start_ms: int
    end_ms: int
    before: str
    current: str
    after: str
    candidates: tuple[str, ...]
    reason: str
    references: tuple[CorrectionReference, ...]
    priority: ReviewPriority = "normal"
    allowed_choices: tuple[ReviewChoice, ...] = ("current", "candidate", "defer")
    window_current: str = ""
    window_candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CorrectionReviewSelection:
    """One exact, closed-vocabulary response to a stored review packet."""

    decision_id: str
    choice: ReviewChoice
    candidate_index: int | None = None

    def __post_init__(self) -> None:
        if not self.decision_id.strip():
            raise ValueError("review selection decision_id must not be blank")
        if self.choice == "candidate":
            if self.candidate_index is None or self.candidate_index < 0:
                raise ValueError("candidate review selection requires a non-negative index")
        elif self.candidate_index is not None:
            raise ValueError("only candidate review selection accepts candidate_index")


@dataclass(frozen=True, slots=True)
class _ReferenceRun:
    source: CorrectionReferenceSource
    text: str
    phonetics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ReferenceCompactSource:
    source: CorrectionReferenceSource
    text: str
    compact: _CompactText
    match_key: str


@dataclass(frozen=True, slots=True)
class _ExactReferenceSupport:
    length: int
    term: str
    start: int
    end: int
    references: tuple[CorrectionReference, ...]


class _ReferenceIndex:
    def __init__(self, sources: Sequence[CorrectionReferenceSource]) -> None:
        self.sources = tuple(sources)
        self.literal_terms = _literal_terms(self.sources)
        self.allowed_outline_literals = {
            literal.match_key
            for literal in self.literal_terms
            if literal.source.kind == "outline"
        }
        self.runs = tuple(
            _ReferenceRun(
                source=source,
                text=match.group(0),
                phonetics=_phonetic_signature(match.group(0)),
            )
            for source in self.sources
            for match in _TERM_RUN_RE.finditer(_traditional(source.text))
        )
        self.compact_sources = tuple(
            _ReferenceCompactSource(
                source=source,
                text=traditional,
                compact=(compact := _compact_text(traditional)),
                match_key=compact.text.casefold(),
            )
            for source in self.sources
            for traditional in (_traditional(source.text),)
        )
        self._positions_by_prefix: dict[
            tuple[ReferenceKind, str, str],
            list[tuple[int, int]],
        ] = {}
        for run_index, run in enumerate(self.runs):
            for start in range(max(0, len(run.phonetics) - 1)):
                key = (run.source.kind, run.phonetics[start], run.phonetics[start + 1])
                self._positions_by_prefix.setdefault(key, []).append((run_index, start))
        self._cache: dict[
            tuple[ReferenceKind, tuple[str, ...]],
            tuple[tuple[str, CorrectionReferenceSource], ...],
        ] = {}
        self._exact_cache: dict[tuple[str, bool], tuple[CorrectionReference, ...]] = {}

    def homophones(
        self,
        *,
        kind: ReferenceKind,
        signature: tuple[str, ...],
    ) -> tuple[tuple[str, CorrectionReferenceSource], ...]:
        key = (kind, signature)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if kind == "glossary":
            # A curated glossary is a closed vocabulary.  Index only complete
            # caller-enrolled terms; arbitrary n-grams such as ``遊牧`` from
            # ``數位遊牧`` are not independent spelling authority.
            found = {
                (literal.display_text, literal.source.source_id): (
                    literal.display_text,
                    literal.source,
                )
                for literal in self.literal_terms
                if literal.source.kind == "glossary"
                and _phonetic_signature(literal.match_key) == signature
            }
            result = tuple(
                found[item]
                for item in sorted(found, key=lambda value: (value[0], value[1]))
            )
            self._cache[key] = result
            return result
        length = len(signature)
        if length < 2:
            return ()
        found: dict[tuple[str, str], tuple[str, CorrectionReferenceSource]] = {}
        prefix = (kind, signature[0], signature[1])
        for run_index, start in self._positions_by_prefix.get(prefix, ()):
            run = self.runs[run_index]
            if start + length > len(run.phonetics):
                continue
            if run.phonetics[start : start + length] != signature:
                continue
            term = run.text[start : start + length]
            found[(term, run.source.source_id)] = (term, run.source)
        result = tuple(found[key] for key in sorted(found, key=lambda item: (item[0], item[1])))
        self._cache[key] = result
        return result

    def exact(
        self,
        term_key: str,
        *,
        allow_contextual_outline: bool = False,
    ) -> tuple[CorrectionReference, ...]:
        """Return mechanically exact source occurrences for one compact literal."""

        cache_key = (term_key, allow_contextual_outline)
        cached = self._exact_cache.get(cache_key)
        if cached is not None:
            return cached
        found: dict[tuple[str, str, str], CorrectionReference] = {}
        for item in self.compact_sources:
            if (
                item.source.kind == "outline"
                and not allow_contextual_outline
                and term_key not in self.allowed_outline_literals
            ):
                continue
            start = item.match_key.find(term_key)
            if start < 0:
                continue
            raw_start, raw_end = item.compact.raw_span(start, start + len(term_key))
            excerpt = item.text[raw_start:raw_end]
            reference = CorrectionReference(
                source_id=item.source.source_id,
                kind=item.source.kind,
                locator=item.source.locator,
                excerpt=excerpt,
            )
            found[(reference.source_id, reference.locator, excerpt)] = reference
        result = tuple(found[key] for key in sorted(found))
        self._exact_cache[cache_key] = result
        return result


@dataclass(frozen=True, slots=True)
class _Replacement:
    start: int
    end: int
    current: str
    selected: str
    category: str
    reason: str
    references: tuple[CorrectionReference, ...]
    resolves_difference: bool = False
    confirmation_supported: bool = False


def _replacement_positions(replacement: _Replacement) -> set[int]:
    """Represent a zero-width insertion as occupying its boundary."""

    return set(range(replacement.start, max(replacement.end, replacement.start + 1)))


@dataclass(frozen=True, slots=True)
class _CompactText:
    text: str
    raw_starts: tuple[int, ...]
    raw_ends: tuple[int, ...]

    def raw_span(self, start: int, end: int) -> tuple[int, int]:
        if not 0 <= start < end <= len(self.text):
            raise ValueError("compact text span must be non-empty and in range")
        return self.raw_starts[start], self.raw_ends[end - 1]


@dataclass(frozen=True, slots=True)
class _AlignedDifference:
    primary_start: int
    primary_end: int
    corroborating_start: int
    corroborating_end: int
    left_anchor: str
    right_anchor: str


@dataclass(frozen=True, slots=True)
class _AlignedWindow:
    index: int
    primary_tokens: tuple[EvidenceToken, ...]
    corroborating_tokens: tuple[EvidenceToken, ...]
    confirmation_tokens: tuple[EvidenceToken, ...]
    start_ms: int
    end_ms: int
    primary_text: str
    corroborating_text: str
    confirmation_text: str
    primary_compact: _CompactText
    corroborating_compact: _CompactText
    differences: tuple[_AlignedDifference, ...]


@dataclass(frozen=True, slots=True)
class _LiteralTerm:
    match_key: str
    display_text: str
    source: CorrectionReferenceSource


def _traditional(text: str) -> str:
    return _OPENCC.convert(text)


def _canonical_recognition_text(text: str) -> str:
    """Project provider text to spoken orthography without losing Latin words.

    ASR sentence punctuation is a boundary signal, not transcript truth.  Keep
    only punctuation that is internal to one ASCII lexical item (for example
    ``we're``, ``co-working`` or ``angiecreates.io``); sentence commas,
    question marks and list punctuation must not leak into canonical text.
    """

    converted = _traditional(text)
    output: list[str] = []
    for index, character in enumerate(converted):
        if character.isspace():
            output.append(character)
            continue
        if not unicodedata.category(character).startswith("P"):
            output.append(character)
            continue
        if character not in {"'", "’", "-", "."}:
            continue
        previous = converted[index - 1] if index else ""
        following = converted[index + 1] if index + 1 < len(converted) else ""
        if (
            previous.isascii()
            and previous.isalnum()
            and following.isascii()
            and following.isalnum()
        ):
            output.append("'" if character == "’" else character)
    return "".join(output)


def _compact_text(text: str) -> _CompactText:
    compact: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for index, character in enumerate(text):
        if _TERM_RUN_RE.fullmatch(character) is None:
            continue
        compact.append(character)
        starts.append(index)
        ends.append(index + 1)
    return _CompactText("".join(compact), tuple(starts), tuple(ends))


def _strip_reference_wrapper(text: str) -> str:
    stripped = re.sub(r"^\s*(?:[-*\u2022]|\d+[.)\u3001])\s*", "", text).strip()
    pairs = (
        ("\u300c", "\u300d"),
        ("\u300e", "\u300f"),
        ("\u300a", "\u300b"),
        ('"', '"'),
        ("'", "'"),
    )
    for left, right in pairs:
        if stripped.startswith(left) and stripped.endswith(right):
            stripped = stripped[len(left) : -len(right)].strip()
    return stripped


def _literal_terms(sources: Sequence[CorrectionReferenceSource]) -> tuple[_LiteralTerm, ...]:
    """Extract only caller-demarcated literals, never arbitrary reference n-grams."""

    found: dict[tuple[str, str, str], _LiteralTerm] = {}
    quoted_pattern = re.compile(
        r"[\u300c\u300e\u300a\"']([^\u300d\u300f\u300b\"']{1,64})"
        r"[\u300d\u300f\u300b\"']"
    )
    for source in sources:
        if source.kind not in {"outline", "glossary"}:
            continue
        candidates: list[str] = []
        traditional = _traditional(source.text)
        for line in traditional.splitlines():
            stripped = _strip_reference_wrapper(line)
            if not stripped:
                continue
            if source.kind == "glossary":
                value = re.split(r"[\uff1a:]", stripped, maxsplit=1)[-1]
                candidates.extend(re.split(r"[,\uff0c\u3001;\uff1b]", value))
            else:
                candidates.extend(match.group(1) for match in quoted_pattern.finditer(stripped))
                if re.search(r"[\uff1a:]", stripped):
                    candidates.append(re.split(r"[\uff1a:]", stripped, maxsplit=1)[-1])
                elif not re.search(r"[\u3002\uff01\uff1f!?]", stripped):
                    candidates.append(stripped)
        if not traditional.strip().count("\n") and source.kind == "outline":
            stripped = _strip_reference_wrapper(traditional)
            if not re.search(r"[\u3002\uff01\uff1f!?]", stripped):
                candidates.append(stripped)
        for candidate in candidates:
            display_text = _strip_reference_wrapper(candidate).strip()
            match_key = _compact_text(display_text).text.casefold()
            if not 2 <= len(match_key) <= 64:
                continue
            found[(match_key, display_text, source.source_id)] = _LiteralTerm(
                match_key=match_key,
                display_text=display_text,
                source=source,
            )
    return tuple(found[key] for key in sorted(found, key=lambda item: (item[0], item[1], item[2])))


def _occurrences(text: str, term: str) -> tuple[tuple[int, int], ...]:
    found: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(term, start)
        if index < 0:
            return tuple(found)
        found.append((index, index + len(term)))
        start = index + 1


def _phonetic_signature(text: str) -> tuple[str, ...]:
    if not text:
        return ()
    syllables = lazy_pinyin(
        text,
        style=Style.NORMAL,
        strict=False,
        errors=lambda characters: list(characters),
    )
    return tuple(syllable.casefold().replace("u:", "v").replace("ü", "v") for syllable in syllables)


def _is_term_text(text: str) -> bool:
    return bool(text) and _TERM_RUN_RE.fullmatch(text) is not None


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_character in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_character != right_character),
                )
            )
        previous = current
    return previous[-1]


def _strict_near_phonetic(left: str, right: str, *, allow_identity: bool = False) -> bool:
    if left == right:
        return allow_identity
    if len(left) != len(right) or not 2 <= len(left) <= _MAX_REFERENCE_TERM_LENGTH:
        return False
    if not _is_term_text(left) or not _is_term_text(right):
        return False
    if _phonetic_signature(left) != _phonetic_signature(right):
        return False
    maximum_edits = 1 if len(left) <= 3 else 2
    return _edit_distance(left, right) <= maximum_edits


def _lineage(
    evidence: RecognitionEvidence,
    token_ids: Sequence[str],
) -> RecognitionLineage:
    return RecognitionLineage(
        evidence_hash=recognition_evidence_content_hash(evidence),
        adapter=evidence.adapter,
        model=evidence.model,
        token_ids=tuple(token_ids),
    )


def _reference_record(
    term: str,
    source: CorrectionReferenceSource,
) -> CorrectionReference:
    return CorrectionReference(
        source_id=source.source_id,
        kind=source.kind,
        locator=source.locator,
        excerpt=term,
    )


def _tool_reference(current: str, selected: str) -> CorrectionReference:
    return CorrectionReference(
        source_id="opencc-python-reimplemented",
        kind="tool",
        locator="opencc:s2tw",
        excerpt=f"{current} -> {selected}",
    )


def _decision_interval(
    start_ms: int,
    end_ms: int,
    text_length: int,
    start: int,
    end: int,
) -> tuple[int, int]:
    if text_length <= 0:
        return start_ms, end_ms
    duration = end_ms - start_ms
    decision_start = start_ms + (duration * start // text_length)
    decision_end = start_ms + (duration * end // text_length)
    return decision_start, max(decision_start + 1, min(end_ms, decision_end))


def _atomic_units(text: str) -> tuple[str, ...]:
    raw_units = _ATOMIC_RE.findall(text)
    units: list[str] = []
    pending_space = ""
    for unit in raw_units:
        if unit.isspace():
            pending_space += unit
            continue
        units.append(f"{pending_space}{unit}")
        pending_space = ""
    if pending_space:
        if units:
            units[-1] += pending_space
        else:
            units.append(pending_space)
    return tuple(units)


def _timed_atomic_tokens(
    *,
    text: str,
    start_ms: int,
    end_ms: int,
    confidence: float | None,
    speaker: str | None,
    source_ids: tuple[str, ...],
    recognition_refs: tuple[str, ...],
    first_index: int,
) -> tuple[CorrectedTimedToken, ...]:
    units = _atomic_units(text)
    if not units:
        return ()
    duration = end_ms - start_ms
    if duration < len(units):
        units = (text,)
    tokens: list[CorrectedTimedToken] = []
    for offset, unit in enumerate(units):
        unit_start = start_ms + duration * offset // len(units)
        unit_end = start_ms + duration * (offset + 1) // len(units)
        tokens.append(
            CorrectedTimedToken(
                id=f"corrected-{first_index + offset:08d}",
                text=unit,
                start_ms=unit_start,
                end_ms=max(unit_start + 1, unit_end),
                confidence=confidence,
                speaker=speaker,
                source_primary_token_ids=source_ids,
                recognition_refs=recognition_refs,
            )
        )
    return tuple(tokens)


def _component_output_tokens(
    *,
    primary_tokens: Sequence[EvidenceToken],
    corroborating_tokens: Sequence[EvidenceToken],
    original_traditional: str,
    selected: str,
    primary_evidence_hash: str,
    corroborating_evidence_hash: str,
    recognition_refs: tuple[str, ...],
    first_index: int,
) -> tuple[CorrectedTimedToken, ...]:
    """Preserve primary token intervals when replacement length permits it."""

    lengths = tuple(len(_canonical_recognition_text(token.text)) for token in primary_tokens)
    if sum(lengths) == len(selected) == len(original_traditional):
        output: list[CorrectedTimedToken] = []
        cursor = 0
        for token, length in zip(primary_tokens, lengths, strict=True):
            piece = selected[cursor : cursor + length]
            cursor += length
            local_refs = (
                f"evidence:{primary_evidence_hash}:{token.id}",
                *(
                    f"evidence:{corroborating_evidence_hash}:{corroborating.id}"
                    for corroborating in corroborating_tokens
                    if token.start_ms < corroborating.end_ms
                    and corroborating.start_ms < token.end_ms
                ),
            )
            produced = _timed_atomic_tokens(
                text=piece,
                start_ms=token.start_ms,
                end_ms=token.end_ms,
                confidence=token.confidence,
                speaker=token.speaker,
                source_ids=(token.id,),
                recognition_refs=local_refs,
                first_index=first_index + len(output),
            )
            output.extend(produced)
        return tuple(output)

    confidences = [token.confidence for token in primary_tokens if token.confidence is not None]
    speakers = {token.speaker for token in primary_tokens}
    return _timed_atomic_tokens(
        text=selected,
        start_ms=primary_tokens[0].start_ms,
        end_ms=primary_tokens[-1].end_ms,
        confidence=min(confidences) if confidences else None,
        speaker=next(iter(speakers)) if len(speakers) == 1 else None,
        source_ids=tuple(token.id for token in primary_tokens),
        recognition_refs=recognition_refs,
        first_index=first_index,
    )


def _merged_lexical_differences(
    primary_key: str,
    corroborating_key: str,
) -> tuple[_AlignedDifference, ...]:
    """Align two bounded strings and merge noise separated by tiny equal islands."""

    opcodes = SequenceMatcher(
        None,
        primary_key,
        corroborating_key,
        autojunk=False,
    ).get_opcodes()
    changed = [index for index, opcode in enumerate(opcodes) if opcode[0] != "equal"]
    if not changed:
        return ()

    groups: list[list[int]] = [[changed[0]]]
    for opcode_index in changed[1:]:
        previous = groups[-1][-1]
        between = opcodes[previous + 1 : opcode_index]
        merge = bool(between) and all(
            tag == "equal"
            and (primary_end - primary_start) < _SHORT_EQUAL_ISLAND
            and (other_end - other_start) < _SHORT_EQUAL_ISLAND
            for tag, primary_start, primary_end, other_start, other_end in between
        )
        if merge or opcode_index == previous + 1:
            groups[-1].append(opcode_index)
        else:
            groups.append([opcode_index])

    differences: list[_AlignedDifference] = []
    for group in groups:
        first = opcodes[group[0]]
        last = opcodes[group[-1]]
        primary_start = first[1]
        primary_end = last[2]
        corroborating_start = first[3]
        corroborating_end = last[4]
        differences.append(
            _AlignedDifference(
                primary_start=primary_start,
                primary_end=primary_end,
                corroborating_start=corroborating_start,
                corroborating_end=corroborating_end,
                left_anchor=primary_key[
                    max(0, primary_start - _ALIGNMENT_ANCHOR_LENGTH) : primary_start
                ],
                right_anchor=primary_key[
                    primary_end : primary_end + _ALIGNMENT_ANCHOR_LENGTH
                ],
            )
        )
    return tuple(differences)


def _primary_token_groups(
    tokens: Sequence[EvidenceToken],
) -> tuple[tuple[EvidenceToken, ...], ...]:
    if not tokens:
        raise ValueError("primary recognition evidence must contain at least one token")
    groups: list[list[EvidenceToken]] = []
    current: list[EvidenceToken] = []
    for token in tokens:
        split = bool(current) and (
            token.start_ms - current[-1].end_ms >= _ALIGNMENT_GAP_MS
            or token.end_ms - current[0].start_ms > _MAX_ALIGNMENT_WINDOW_MS
        )
        if split:
            groups.append(current)
            current = []
        current.append(token)
    groups.append(current)
    return tuple(tuple(group) for group in groups)


def _assign_tokens_to_groups(
    tokens: Sequence[EvidenceToken],
    groups: Sequence[Sequence[EvidenceToken]],
) -> tuple[tuple[EvidenceToken, ...], ...]:
    boundaries = tuple(
        (left[-1].end_ms + right[0].start_ms) // 2
        for left, right in zip(groups, groups[1:])
    )
    assigned: list[list[EvidenceToken]] = [[] for _group in groups]
    for token in tokens:
        midpoint = (token.start_ms + token.end_ms) // 2
        assigned[bisect_right(boundaries, midpoint)].append(token)
    return tuple(tuple(group) for group in assigned)


def _build_aligned_windows(
    primary: RecognitionEvidence,
    corroborating: RecognitionEvidence,
    confirmation: RecognitionEvidence | None = None,
) -> tuple[_AlignedWindow, ...]:
    """Build <=12 s lexical comparison windows on the immutable primary clock."""

    primary_groups = _primary_token_groups(primary.tokens)
    corroborating_groups = _assign_tokens_to_groups(corroborating.tokens, primary_groups)
    confirmation_groups = _assign_tokens_to_groups(
        () if confirmation is None else confirmation.tokens,
        primary_groups,
    )
    windows: list[_AlignedWindow] = []
    for index, (primary_tokens, corroborating_tokens, confirmation_tokens) in enumerate(
        zip(
            primary_groups,
            corroborating_groups,
            confirmation_groups,
            strict=True,
        )
    ):
        primary_text = _canonical_recognition_text(
            "".join(token.text for token in primary_tokens)
        )
        corroborating_text = _canonical_recognition_text(
            "".join(token.text for token in corroborating_tokens)
        )
        confirmation_text = _canonical_recognition_text(
            "".join(token.text for token in confirmation_tokens)
        )
        primary_compact = _compact_text(primary_text)
        corroborating_compact = _compact_text(corroborating_text)
        all_tokens = (*primary_tokens, *corroborating_tokens, *confirmation_tokens)
        windows.append(
            _AlignedWindow(
                index=index,
                primary_tokens=primary_tokens,
                corroborating_tokens=corroborating_tokens,
                confirmation_tokens=confirmation_tokens,
                start_ms=min(token.start_ms for token in all_tokens),
                end_ms=max(token.end_ms for token in all_tokens),
                primary_text=primary_text,
                corroborating_text=corroborating_text,
                confirmation_text=confirmation_text,
                primary_compact=primary_compact,
                corroborating_compact=corroborating_compact,
                differences=_merged_lexical_differences(
                    primary_compact.text.casefold(),
                    corroborating_compact.text.casefold(),
                ),
            )
        )
    return tuple(windows)


def _best_exact_reference_support(
    text_key: str,
    start: int,
    end: int,
    index: _ReferenceIndex,
    *,
    allow_contextual_outline: bool = False,
) -> _ExactReferenceSupport | None:
    """Find the longest exact source literal that actually crosses a difference."""

    if start >= end:
        return None
    changed = text_key[start:end]
    if re.search(r"[A-Za-z0-9]", changed) and len(changed) < 6:
        return None
    required_changed_overlap = (
        min(4, len(changed)) if re.search(r"[A-Za-z0-9]", changed) else 1
    )
    maximum = min(_MAX_REFERENCE_TERM_LENGTH, len(text_key))
    for length in range(maximum, 1, -1):
        first_start = max(0, start - length + 1)
        last_start = min(end - 1, len(text_key) - length)
        if first_start > last_start:
            continue
        candidates: list[tuple[str, int, tuple[CorrectionReference, ...]]] = []
        for offset in range(first_start, last_start + 1):
            if not (offset < end and start < offset + length):
                continue
            changed_overlap = max(0, min(offset + length, end) - max(offset, start))
            if changed_overlap < required_changed_overlap:
                continue
            term = text_key[offset : offset + length]
            if re.search(r"[A-Za-z0-9]", term):
                # A book hit for ``hous`` must never arbitrate Rumours vs
                # Roundhouse.  Latin references are lexical tokens, not a bag
                # of arbitrary substrings: require a useful minimum length and
                # the complete contiguous ASCII run on both sides.
                if length < 6:
                    continue
                if offset > 0 and re.fullmatch(r"[A-Za-z0-9]", text_key[offset - 1]):
                    continue
                if offset + length < len(text_key) and re.fullmatch(
                    r"[A-Za-z0-9]", text_key[offset + length]
                ):
                    continue
            references = index.exact(
                term,
                allow_contextual_outline=allow_contextual_outline,
            )
            if not references:
                continue
            candidates.append((term, offset, references))
        if candidates:
            term, offset, references = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
            return _ExactReferenceSupport(
                length=length,
                term=term,
                start=offset,
                end=offset + length,
                references=references,
            )
    return None


def _whole_difference_reference_support(
    text_key: str,
    start: int,
    end: int,
    difference: _AlignedDifference,
    index: _ReferenceIndex,
) -> _ExactReferenceSupport | None:
    """Return support only for the complete ASR difference candidate.

    Book prose is not a replacement dictionary.  In particular, a longer
    phrase that merely crosses a one-character edit cannot expand the edit's
    mutation range.  Very short Han candidates additionally need two stable
    consensus characters on both sides at the same source occurrence.
    """

    if start >= end:
        return None
    term = text_key[start:end]
    if re.search(r"[A-Za-z0-9]", term):
        if start > 0 and re.fullmatch(r"[A-Za-z0-9]", text_key[start - 1]):
            return None
        if end < len(text_key) and re.fullmatch(r"[A-Za-z0-9]", text_key[end]):
            return None
    references = tuple(
        reference
        for reference in index.exact(term)
        if reference.kind in {"book", "glossary"}
    )
    if not references:
        return None
    if _HAN_TEXT_RE.fullmatch(term) is not None and len(term) <= 2:
        left = difference.left_anchor[-2:]
        right = difference.right_anchor[:2]
        if len(left) < 2 or len(right) < 2:
            return None
        contextual_source_ids = {
            item.source.source_id
            for item in index.compact_sources
            if left + term + right in item.match_key
        }
        references = tuple(
            reference
            for reference in references
            if reference.source_id in contextual_source_ids
        )
        if not references:
            return None
    return _ExactReferenceSupport(
        length=len(term),
        term=term,
        start=start,
        end=end,
        references=references,
    )


def _asr_spellings_are_compatible(left: str, right: str) -> bool:
    """Return whether two candidates can plausibly be spellings of one utterance.

    References are allowed to arbitrate spelling, not meaning.  This deliberately
    rejects cross-script substitutions, unequal phrase rewrites, and same-window
    word salad such as ``佩服`` versus ``的就是``.
    """

    left_key = _compact_text(left).text.casefold()
    right_key = _compact_text(right).text.casefold()
    if not left_key or len(left_key) != len(right_key):
        return False
    left_han = _HAN_TEXT_RE.fullmatch(left_key) is not None
    right_han = _HAN_TEXT_RE.fullmatch(right_key) is not None
    if left_han != right_han:
        return False
    if left_han:
        left_phonetics = _phonetic_signature(left_key)
        right_phonetics = _phonetic_signature(right_key)
        if len(left_phonetics) != len(right_phonetics):
            return False
        if any(
            _edit_distance(left_sound, right_sound) > 1
            for left_sound, right_sound in zip(
                left_phonetics,
                right_phonetics,
                strict=True,
            )
        ):
            return False
        maximum_character_edits = 1 if len(left_key) <= 3 else 2
        return _edit_distance(left_key, right_key) <= maximum_character_edits
    if not left_key.isascii() or not right_key.isascii():
        return False
    return _edit_distance(left_key, right_key) <= max(1, len(left_key) // 4)


def _complete_ascii_boundary(text: str, start: int, end: int) -> bool:
    """Keep an ASCII glossary literal on complete lexical-run boundaries."""

    if start < end and text[start].isascii() and text[start].isalnum():
        if start > 0 and text[start - 1].isascii() and text[start - 1].isalnum():
            return False
    if start < end and text[end - 1].isascii() and text[end - 1].isalnum():
        if end < len(text) and text[end].isascii() and text[end].isalnum():
            return False
    return True


def _expand_ascii_run(text: str, start: int, end: int) -> tuple[int, int]:
    """Expand a SequenceMatcher edit to its complete surrounding ASCII token."""

    if start > 0 and start < len(text) and text[start - 1].isascii():
        while start > 0 and text[start - 1].isascii() and text[start - 1].isalnum():
            start -= 1
    while end < len(text) and text[end].isascii() and text[end].isalnum():
        end += 1
    return start, end


def _phonetic_skeleton(text: str) -> str:
    """Return a deliberately coarse cross-script pronunciation skeleton."""

    if _HAN_TEXT_RE.fullmatch(text) is not None:
        romanized = "".join(_phonetic_signature(text))
    else:
        romanized = text.casefold()
    romanized = romanized.replace("c", "k").replace("q", "k").replace("x", "s")
    skeleton = re.sub(r"[^a-z0-9]", "", romanized)
    skeleton = re.sub(r"[aeiouyv]", "", skeleton)
    return re.sub(r"(.)\1+", r"\1", skeleton)


def _cross_script_pronunciation_compatible(current: str, selected: str) -> bool:
    current_han = _HAN_TEXT_RE.fullmatch(current) is not None
    selected_han = _HAN_TEXT_RE.fullmatch(selected) is not None
    if current_han == selected_han:
        return False
    han = current if current_han else selected
    latin = selected if current_han else current
    if len(han) < 2 or _ASCII_TEXT_RE.fullmatch(latin) is None or len(latin) < 5:
        return False
    han_skeleton = _phonetic_skeleton(han)
    latin_skeleton = _phonetic_skeleton(latin)
    if not han_skeleton or not latin_skeleton or han_skeleton[0] != latin_skeleton[0]:
        return False
    distance = _edit_distance(han_skeleton, latin_skeleton)
    return distance <= 2 and distance * 2 <= max(len(han_skeleton), len(latin_skeleton))


def _parse_cjk_number(text: str) -> int | None:
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "兩": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if not text or _CJK_NUMERAL_RE.fullmatch(text) is None:
        return None
    if "十" not in text:
        return int("".join(str(digits[character]) for character in text))
    if text.count("十") != 1 or len(text) > 3:
        return None
    left, right = text.split("十", maxsplit=1)
    if len(left) > 1 or len(right) > 1:
        return None
    tens = 1 if not left else digits.get(left)
    ones = 0 if not right else digits.get(right)
    if tens is None or ones is None:
        return None
    return tens * 10 + ones


def _duplicated_numeral_compatible(current: str, selected: str) -> bool:
    """Recognise a repeated spoken CJK numeral beside an exact digit spelling."""

    changes = [
        opcode
        for opcode in SequenceMatcher(None, current, selected, autojunk=False).get_opcodes()
        if opcode[0] != "equal"
    ]
    if len(changes) != 1:
        return False
    _tag, current_start, current_end, selected_start, selected_end = changes[0]
    current_number = current[current_start:current_end]
    selected_number = selected[selected_start:selected_end]
    if not selected_number.isascii() or not selected_number.isdigit():
        return False
    for repeats in (2, 3):
        if len(current_number) % repeats:
            continue
        width = len(current_number) // repeats
        chunks = tuple(
            current_number[offset : offset + width]
            for offset in range(0, len(current_number), width)
        )
        if len(set(chunks)) == 1 and _parse_cjk_number(chunks[0]) == int(selected_number):
            return True
    return False


def _glossary_spellings_compatible(current: str, selected: str) -> bool:
    current_key = current.casefold()
    selected_key = selected.casefold()
    if current_key == selected_key:
        return True
    current_han = _HAN_TEXT_RE.fullmatch(current_key) is not None
    selected_han = _HAN_TEXT_RE.fullmatch(selected_key) is not None
    if current_han and selected_han:
        if len(current_key) != len(selected_key):
            return False
        current_sound = _phonetic_signature(current_key)
        selected_sound = _phonetic_signature(selected_key)
        return _edit_distance(current_key, selected_key) <= max(
            1,
            len(selected_key) // 3,
        ) and all(
            _edit_distance(left, right) <= 2
            for left, right in zip(current_sound, selected_sound, strict=True)
        )
    if current_key.isascii() and selected_key.isascii():
        return _edit_distance(current_key, selected_key) <= max(
            1,
            len(selected_key) // 4,
        )
    return _cross_script_pronunciation_compatible(current_key, selected_key) or (
        _duplicated_numeral_compatible(current_key, selected_key)
    )


def _consensus_glossary_subreplacements(
    window: _AlignedWindow,
    index: _ReferenceIndex,
) -> tuple[_Replacement, ...]:
    """Recover complete curated terms inside spans on which both ASRs agree."""

    primary_key = window.primary_compact.text.casefold()
    corroborating_key = window.corroborating_compact.text.casefold()
    equal_spans = tuple(
        opcode
        for opcode in SequenceMatcher(
            None,
            primary_key,
            corroborating_key,
            autojunk=False,
        ).get_opcodes()
        if opcode[0] == "equal"
    )
    grouped: dict[tuple[int, int, str], list[_LiteralTerm]] = {}
    for literal in index.literal_terms:
        if literal.source.kind != "glossary":
            continue
        length = len(literal.match_key)
        if not 2 <= length <= _MAX_REFERENCE_TERM_LENGTH:
            continue
        for _tag, primary_start, primary_end, other_start, _other_end in equal_spans:
            for start in range(primary_start, primary_end - length + 1):
                candidate = primary_key[start : start + length]
                corroborating_start = other_start + start - primary_start
                if candidate != corroborating_key[
                    corroborating_start : corroborating_start + length
                ]:
                    continue
                if candidate != literal.match_key and not _strict_near_phonetic(
                    candidate,
                    literal.match_key,
                ):
                    continue
                if not _complete_ascii_boundary(primary_key, start, start + length):
                    continue
                raw_start, raw_end = window.primary_compact.raw_span(start, start + length)
                if window.primary_text[raw_start:raw_end] == literal.display_text:
                    continue
                grouped.setdefault(
                    (raw_start, raw_end, literal.display_text),
                    [],
                ).append(literal)

    proposals: list[_Replacement] = []
    ranges = {(start, end) for start, end, _selected in grouped}
    for start, end in sorted(ranges):
        selected_values = {
            selected
            for candidate_start, candidate_end, selected in grouped
            if (candidate_start, candidate_end) == (start, end)
        }
        if len(selected_values) != 1:
            continue
        selected = next(iter(selected_values))
        literals = grouped[(start, end, selected)]
        proposals.append(
            _Replacement(
                start=start,
                end=end,
                current=window.primary_text[start:end],
                selected=selected,
                category="asr_supported_curated_glossary",
                reason=(
                    "both recognisers agree on one embedded spelling that is "
                    "pronunciation-compatible with a unique complete curated term"
                ),
                references=tuple(
                    _reference_record(item.display_text, item.source)
                    for item in sorted(
                        literals,
                        key=lambda item: (item.source.source_id, item.display_text),
                    )
                ),
            )
        )

    chosen: list[_Replacement] = []
    occupied: set[int] = set()
    for proposal in sorted(
        proposals,
        key=lambda item: (-(item.end - item.start), item.start, item.selected),
    ):
        positions = set(range(proposal.start, proposal.end))
        if positions & occupied:
            continue
        chosen.append(proposal)
        occupied.update(positions)
    return tuple(sorted(chosen, key=lambda item: item.start))


def _whole_window_glossary_replacement(
    window: _AlignedWindow,
    index: _ReferenceIndex,
) -> _Replacement | None:
    """Apply one explicit glossary term only when it covers the whole window.

    Compact-equivalent candidates may recover official spacing/case/punctuation.
    If both recognisers agree on the same homophone, the complete caller-curated
    term may recover its spelling.  No substring alignment or book prose is used.
    """

    primary_key = window.primary_compact.text.casefold()
    corroborating_key = window.corroborating_compact.text.casefold()
    if not primary_key or primary_key != corroborating_key:
        return None
    candidates: list[_LiteralTerm] = []
    for literal in index.literal_terms:
        if literal.source.kind != "glossary":
            continue
        if literal.match_key == primary_key or _strict_near_phonetic(
            primary_key,
            literal.match_key,
        ):
            candidates.append(literal)
    displays = {literal.display_text for literal in candidates}
    if len(displays) != 1:
        return None
    literal = sorted(
        candidates,
        key=lambda item: (item.source.source_id, item.display_text),
    )[0]
    selected = literal.display_text
    if window.primary_text == selected:
        return None
    return _Replacement(
        start=0,
        end=len(window.primary_text),
        current=window.primary_text,
        selected=selected,
        category="asr_supported_curated_glossary",
        reason=(
            "both recognisers cover one complete caller-curated glossary term; "
            "the glossary supplies only its official spelling and formatting"
        ),
        references=tuple(
            _reference_record(item.display_text, item.source)
            for item in candidates
            if item.display_text == selected
        ),
    )


def _aligned_glossary_replacement(
    *,
    window: _AlignedWindow,
    difference: _AlignedDifference,
    index: _ReferenceIndex,
) -> _Replacement | None:
    """Return one evidenced glossary term intersecting this aligned edit.

    Exact secondary spelling is the anchor.  Stable characters beside the edit
    may complete the term; when the edit also contains unrelated material, only
    a unique pronunciation-compatible primary subspan is replaced and the rest
    stays unresolved.
    """

    primary_key = window.primary_compact.text.casefold()
    corroborating_key = window.corroborating_compact.text.casefold()
    proposals: list[_Replacement] = []
    for literal in index.literal_terms:
        if (
            literal.source.kind != "glossary"
            or len(literal.match_key) > _MAX_REFERENCE_TERM_LENGTH
        ):
            continue
        for other_start, other_end in _occurrences(
            corroborating_key,
            literal.match_key,
        ):
            if not _complete_ascii_boundary(
                corroborating_key,
                other_start,
                other_end,
            ):
                continue
            if not (
                other_start < difference.corroborating_end
                and difference.corroborating_start < other_end
            ) and not (
                difference.corroborating_start == difference.corroborating_end
                and other_start <= difference.corroborating_start <= other_end
            ):
                continue

            candidate_spans: set[tuple[int, int]] = set()
            secondary_covers_difference = (
                other_start <= difference.corroborating_start
                and difference.corroborating_end <= other_end
            )
            if secondary_covers_difference:
                left_extension = difference.corroborating_start - other_start
                right_extension = other_end - difference.corroborating_end
                primary_start = difference.primary_start - left_extension
                primary_end = difference.primary_end + right_extension
                if 0 <= primary_start < primary_end <= len(primary_key):
                    if (
                        primary_key[primary_start : difference.primary_start]
                        == corroborating_key[
                            other_start : difference.corroborating_start
                        ]
                        and primary_key[difference.primary_end : primary_end]
                        == corroborating_key[
                            difference.corroborating_end : other_end
                        ]
                    ):
                        candidate_spans.add((primary_start, primary_end))

            for primary_start in range(
                difference.primary_start,
                difference.primary_end,
            ):
                for primary_end in range(primary_start + 1, difference.primary_end + 1):
                    candidate_spans.add((primary_start, primary_end))

            for primary_start, primary_end in sorted(candidate_spans):
                current_key = primary_key[primary_start:primary_end]
                if not _complete_ascii_boundary(
                    primary_key,
                    primary_start,
                    primary_end,
                ):
                    continue
                if not _glossary_spellings_compatible(
                    current_key,
                    literal.match_key,
                ):
                    continue
                raw_start, raw_end = window.primary_compact.raw_span(
                    primary_start,
                    primary_end,
                )
                proposals.append(
                    _Replacement(
                        start=raw_start,
                        end=raw_end,
                        current=window.primary_text[raw_start:raw_end],
                        selected=literal.display_text,
                        category="asr_supported_curated_glossary",
                        reason=(
                            "one complete curated glossary term is exact in the "
                            "corroborating ASR and only its compatible primary subspan "
                            "is replaced"
                        ),
                        references=(
                            _reference_record(literal.display_text, literal.source),
                        ),
                        resolves_difference=(
                            primary_start <= difference.primary_start
                            and difference.primary_end <= primary_end
                            and secondary_covers_difference
                        ),
                    )
                )
    unique = {
        (item.start, item.end, item.selected): item for item in proposals
    }
    if len(unique) != 1:
        return None
    return next(iter(unique.values()))


def _latin_name_near(candidate: str, selected: str) -> bool:
    if (
        _ASCII_TEXT_RE.fullmatch(candidate) is None
        or not candidate
        or not selected
    ):
        return False
    candidate_key = candidate.casefold()
    selected_key = selected.casefold()
    if candidate_key[0] != selected_key[0]:
        return False
    distance = _edit_distance(candidate_key, selected_key)
    similarity = SequenceMatcher(
        None,
        candidate_key,
        selected_key,
        autojunk=False,
    ).ratio()
    return distance <= 3 and similarity >= 0.6


def _authoritative_glossary_name_replacement(
    *,
    current: str,
    corroborating: str,
    start: int,
    end: int,
    index: _ReferenceIndex,
) -> _Replacement | None:
    """Resolve two wrong Latin name hypotheses only with dual spelling authority."""

    current_key = _compact_text(current).text.casefold()
    corroborating_key = _compact_text(corroborating).text.casefold()
    if (
        current_key == corroborating_key
        or _ASCII_TEXT_RE.fullmatch(current_key) is None
        or _ASCII_TEXT_RE.fullmatch(corroborating_key) is None
        or min(len(current_key), len(corroborating_key)) < 4
    ):
        return None
    proposals: list[_Replacement] = []
    for literal in index.literal_terms:
        if literal.source.kind != "glossary":
            continue
        name_parts = literal.display_text.split()
        if len(name_parts) < 2 or not all(
            _ASCII_TEXT_RE.fullmatch(part) is not None for part in name_parts
        ):
            continue
        references = index.exact(literal.match_key)
        if not {"book", "glossary"}.issubset(
            {reference.kind for reference in references}
        ):
            continue
        if not (
            _latin_name_near(current_key, literal.match_key)
            or _latin_name_near(corroborating_key, literal.match_key)
        ):
            continue
        proposals.append(
            _Replacement(
                start=start,
                end=end,
                current=current,
                selected=literal.display_text,
                category="authoritative_book_spelling",
                reason=(
                    "the recognisers supply two bounded Latin name hypotheses; "
                    "one is pronunciation-compatible with the unique name whose exact "
                    "spelling is enrolled in both the glossary and an authoritative book"
                ),
                references=references,
                resolves_difference=True,
            )
        )
    unique = {(item.selected, item.references): item for item in proposals}
    if len(unique) != 1:
        return None
    return next(iter(unique.values()))


def _single_asr_authoritative_glossary_replacement(
    *,
    window: _AlignedWindow,
    difference: _AlignedDifference,
    index: _ReferenceIndex,
    recognition_literals: frozenset[str],
) -> _Replacement | None:
    """Normalise one primary Han spelling without treating omission as agreement.

    The closed glossary literal must also occur exactly in an authoritative book
    and elsewhere in enrolled recognition evidence.  A current spelling that is
    itself book-attested is preserved, protecting ordinary homophones and idioms.
    """

    primary_key = window.primary_compact.text.casefold()
    proposals: list[_Replacement] = []
    for literal in index.literal_terms:
        if (
            literal.source.kind != "glossary"
            or _HAN_TEXT_RE.fullmatch(literal.match_key) is None
            or literal.match_key not in recognition_literals
        ):
            continue
        target_references = index.exact(literal.match_key)
        if not {"book", "glossary"}.issubset(
            {reference.kind for reference in target_references}
        ):
            continue
        length = len(literal.match_key)
        first_start = max(0, difference.primary_start - length + 1)
        last_start = min(difference.primary_end - 1, len(primary_key) - length)
        for start in range(first_start, last_start + 1):
            end = start + length
            if not (start < difference.primary_end and difference.primary_start < end):
                continue
            candidate = primary_key[start:end]
            if not _strict_near_phonetic(candidate, literal.match_key):
                continue
            if any(reference.kind == "book" for reference in index.exact(candidate)):
                continue
            raw_start, raw_end = window.primary_compact.raw_span(start, end)
            proposals.append(
                _Replacement(
                    start=raw_start,
                    end=raw_end,
                    current=window.primary_text[raw_start:raw_end],
                    selected=literal.display_text,
                    category="authoritative_book_spelling",
                    reason=(
                        "one primary ASR subspan is an exact strict homophone of the "
                        "unique glossary spelling, which is exact in an authoritative "
                        "book and elsewhere in recognition evidence; coverage remains "
                        "independently reviewable"
                    ),
                    references=target_references,
                )
            )
    unique = {
        (item.start, item.end, item.selected): item
        for item in proposals
    }
    if len(unique) != 1:
        return None
    return next(iter(unique.values()))


def _compact_insertion_offset(compact: _CompactText, index: int, raw_length: int) -> int:
    if not 0 <= index <= len(compact.text):
        raise ValueError("compact insertion offset must be in range")
    if index == len(compact.text):
        return raw_length
    return compact.raw_starts[index]


def _local_confirmation_replacement(
    *,
    window: _AlignedWindow,
    difference: _AlignedDifference,
) -> _Replacement | None:
    """Use a third ASR only when it exactly repeats this one local secondary edit."""

    if not window.confirmation_tokens:
        return None
    primary_key = window.primary_compact.text.casefold()
    corroborating_key = window.corroborating_compact.text.casefold()
    confirmation_compact = _compact_text(window.confirmation_text)
    confirmation_key = confirmation_compact.text.casefold()
    corroborating_part = corroborating_key[
        difference.corroborating_start : difference.corroborating_end
    ]
    matching = tuple(
        candidate
        for candidate in _merged_lexical_differences(primary_key, confirmation_key)
        if (
            candidate.primary_start,
            candidate.primary_end,
        )
        == (difference.primary_start, difference.primary_end)
        and confirmation_key[
            candidate.corroborating_start : candidate.corroborating_end
        ]
        == corroborating_part
    )
    if len(matching) != 1:
        return None

    if difference.primary_start == difference.primary_end:
        primary_raw_start = primary_raw_end = _compact_insertion_offset(
            window.primary_compact,
            difference.primary_start,
            len(window.primary_text),
        )
    else:
        primary_raw_start, primary_raw_end = window.primary_compact.raw_span(
            difference.primary_start,
            difference.primary_end,
        )
    if difference.corroborating_start == difference.corroborating_end:
        selected = ""
    else:
        corroborating_raw_start, corroborating_raw_end = (
            window.corroborating_compact.raw_span(
                difference.corroborating_start,
                difference.corroborating_end,
            )
        )
        selected = window.corroborating_text[
            corroborating_raw_start:corroborating_raw_end
        ]
    return _Replacement(
        start=primary_raw_start,
        end=primary_raw_end,
        current=window.primary_text[primary_raw_start:primary_raw_end],
        selected=selected,
        category="audio_confirmed_secondary_coverage",
        reason=(
            "a third bounded recognition exactly repeats this local secondary edit; "
            "other window differences remain independent"
        ),
        references=(),
        resolves_difference=True,
        confirmation_supported=True,
    )


def _phonetic_reference_replacement(
    *,
    current: str,
    corroborating: str,
    index: _ReferenceIndex,
) -> tuple[str, tuple[CorrectionReference, ...]] | None:
    if (
        len(current) != len(corroborating)
        or not 2 <= len(current) <= _MAX_REFERENCE_TERM_LENGTH
        or _HAN_TEXT_RE.fullmatch(current) is None
        or _HAN_TEXT_RE.fullmatch(corroborating) is None
    ):
        return None
    signatures = {_phonetic_signature(current), _phonetic_signature(corroborating)}
    matches = tuple(
        match
        for signature in signatures
        for kind in ("glossary",)
        for match in index.homophones(kind=kind, signature=signature)
        if _strict_near_phonetic(current, match[0])
        or _strict_near_phonetic(corroborating, match[0])
    )
    terms = {term for term, _source in matches}
    if len(terms) != 1:
        return None
    selected = next(iter(terms))
    def heard_as(candidate: str) -> bool:
        if len(candidate) != len(selected):
            return False
        if (
            _HAN_TEXT_RE.fullmatch(candidate) is None
            or _HAN_TEXT_RE.fullmatch(selected) is None
        ):
            return False
        candidate_sound = _phonetic_signature(candidate)
        selected_sound = _phonetic_signature(selected)
        return all(
            _edit_distance(left, right) <= 1
            for left, right in zip(candidate_sound, selected_sound, strict=True)
        )

    if not (heard_as(current) and heard_as(corroborating)):
        return None
    references = tuple(
        _reference_record(term, source)
        for term, source in matches
        if term == selected
    )
    return selected, references


def _phonetic_glossary_subreplacements(
    *,
    current: str,
    corroborating: str,
    base_start: int,
    index: _ReferenceIndex,
) -> tuple[_Replacement, ...]:
    """Correct one complete curated term embedded in a larger ASR edit."""

    compact = _compact_text(current)
    corroborating_compact = _compact_text(corroborating)
    proposals: list[_Replacement] = []
    maximum = min(_MAX_REFERENCE_TERM_LENGTH, len(compact.text))
    for length in range(maximum, 1, -1):
        for start in range(len(compact.text) - length + 1):
            candidate = compact.text[start : start + length]
            if _HAN_TEXT_RE.fullmatch(candidate) is None:
                continue
            signature = _phonetic_signature(candidate)
            heard_alternatives = tuple(
                corroborating_compact.text[offset : offset + length]
                for offset in range(len(corroborating_compact.text) - length + 1)
                if len(
                    alternative_signature := _phonetic_signature(
                        corroborating_compact.text[offset : offset + length]
                    )
                )
                == len(signature)
                and all(
                    _edit_distance(left, right) <= 1
                    for left, right in zip(
                        alternative_signature,
                        signature,
                        strict=True,
                    )
                )
            )
            if not heard_alternatives:
                continue
            matches = tuple(
                match
                for kind in ("glossary",)
                for match in index.homophones(kind=kind, signature=signature)
                if _strict_near_phonetic(candidate, match[0])
            )
            terms = {term for term, _source in matches}
            if len(terms) != 1:
                continue
            selected = next(iter(terms))
            if not _strict_near_phonetic(candidate, selected):
                continue
            raw_start, raw_end = compact.raw_span(start, start + length)
            references = tuple(
                _reference_record(term, source)
                for term, source in matches
                if term == selected
            )
            proposals.append(
                _Replacement(
                    start=base_start + raw_start,
                    end=base_start + raw_end,
                    current=current[raw_start:raw_end],
                    selected=selected,
                    category=_reference_category(references),
                    reason=(
                        "one embedded ASR spelling is pronunciation-compatible with a "
                        "unique authoritative term"
                    ),
                    references=references,
                )
            )
    chosen: list[_Replacement] = []
    occupied: set[int] = set()
    for proposal in sorted(
        proposals,
        key=lambda item: (-(item.end - item.start), item.start, item.selected),
    ):
        positions = set(range(proposal.start, proposal.end))
        if positions & occupied:
            continue
        chosen.append(proposal)
        occupied.update(positions)
    return tuple(sorted(chosen, key=lambda item: item.start))


def _reference_category(references: Sequence[CorrectionReference]) -> str:
    kinds = {reference.kind for reference in references}
    if "glossary" in kinds:
        return "asr_supported_curated_glossary"
    if "book" in kinds:
        return "authoritative_book_spelling"
    return "asr_supported_outline_spelling"


def _is_high_priority_difference(
    window: _AlignedWindow,
    unresolved_indices: Sequence[int],
) -> bool:
    for difference_index in unresolved_indices:
        difference = window.differences[difference_index]
        primary = window.primary_compact.text[
            difference.primary_start : difference.primary_end
        ]
        corroborating = window.corroborating_compact.text[
            difference.corroborating_start : difference.corroborating_end
        ]
        if not primary or not corroborating:
            return True
        if re.search(r"[A-Za-z0-9]", primary + corroborating):
            return True
        if max(len(primary), len(corroborating)) >= 8:
            return True
    return False


def _difference_raw_span(
    text: str,
    compact: _CompactText,
    start: int,
    end: int,
) -> tuple[int, int]:
    """Map one compact lexical side to its exact raw-text character span."""

    if start == end:
        offset = _compact_insertion_offset(compact, start, len(text))
        return offset, offset
    return compact.raw_span(start, end)


def _transformed_boundary(
    offset: int,
    replacements: Sequence[_Replacement],
    *,
    end_bias: bool,
) -> int:
    """Map an original-text boundary through ordered non-overlapping edits."""

    delta = 0
    for replacement in replacements:
        if offset <= replacement.start:
            if (
                offset == replacement.start == replacement.end
                and end_bias
            ):
                delta += len(replacement.selected)
            return offset + delta
        if offset >= replacement.end:
            delta += len(replacement.selected) - (replacement.end - replacement.start)
            continue
        # A replacement partially intersects the requested span.  Bind the
        # local review side to the appropriate edge of the selected literal.
        return replacement.start + delta + (len(replacement.selected) if end_bias else 0)
    return offset + delta


def _transformed_span(
    start: int,
    end: int,
    replacements: Sequence[_Replacement],
) -> tuple[int, int]:
    return (
        _transformed_boundary(start, replacements, end_bias=False),
        _transformed_boundary(end, replacements, end_bias=True),
    )


def _local_difference_references(
    *,
    window: _AlignedWindow,
    difference: _AlignedDifference,
    index: _ReferenceIndex,
) -> tuple[CorrectionReference, ...]:
    """Return only reference excerpts crossing this exact lexical edit."""

    found: dict[tuple[str, str, str], CorrectionReference] = {}
    for text_key, start, end in (
        (
            window.primary_compact.text.casefold(),
            difference.primary_start,
            difference.primary_end,
        ),
        (
            window.corroborating_compact.text.casefold(),
            difference.corroborating_start,
            difference.corroborating_end,
        ),
    ):
        support = _best_exact_reference_support(
            text_key,
            start,
            end,
            index,
            allow_contextual_outline=True,
        )
        if support is None:
            continue
        for reference in support.references:
            found[(reference.source_id, reference.locator, reference.excerpt)] = reference
    return tuple(found[key] for key in sorted(found))


def _local_difference_category(
    window: _AlignedWindow,
    difference_index: int,
) -> tuple[str, str]:
    difference = window.differences[difference_index]
    if difference.primary_start == difference.primary_end:
        return (
            "recognition_coverage_gap",
            "corroborating-only text requires an exact bounded audio confirmation before insertion",
        )
    if difference.corroborating_start == difference.corroborating_end:
        return (
            "possible_primary_hallucination",
            "primary-only text is preserved until bounded audio evidence can "
            "confirm that deletion is safe",
        )
    if _is_high_priority_difference(window, (difference_index,)):
        return (
            "recognition_disagreement_high_priority",
            "the local ASR candidates materially disagree; no enrolled reference "
            "selects one exact candidate",
        )
    return (
        "recognition_disagreement",
        "the local ASR candidates disagree and no enrolled reference selects one exact candidate",
    )




def correct_recognition(
    *,
    primary: RecognitionEvidence,
    corroborating: RecognitionEvidence,
    references: Sequence[CorrectionReferenceSource] = (),
    confirmation: RecognitionEvidence | None = None,
) -> AccurateCorrectionResult:
    """Reconcile two ASRs in bounded lexical windows without free-form rewriting."""

    if primary.episode_id != corroborating.episode_id:
        raise ValueError("recognition evidence episode_id values must match")
    if primary.normalized_audio_hash != corroborating.normalized_audio_hash:
        raise ValueError("recognition evidence normalized_audio_hash values must match")
    if confirmation is not None:
        if confirmation.episode_id != primary.episode_id:
            raise ValueError("confirmation evidence episode_id must match")
        if confirmation.normalized_audio_hash != primary.normalized_audio_hash:
            raise ValueError("confirmation evidence normalized_audio_hash must match")

    sources = tuple(references)
    reference_ids = [source.source_id for source in sources]
    if len(reference_ids) != len(set(reference_ids)):
        raise ValueError("reference source_id values must be unique")
    reference_index = _ReferenceIndex(sources)
    recognition_corpus = tuple(
        _compact_text(
            _canonical_recognition_text("".join(token.text for token in evidence.tokens))
        )
        .text.casefold()
        for evidence in (primary, corroborating)
    )
    recognition_literals = frozenset(
        literal.match_key
        for literal in reference_index.literal_terms
        if any(literal.match_key in corpus for corpus in recognition_corpus)
    )
    windows = _build_aligned_windows(primary, corroborating, confirmation)
    primary_hash = recognition_evidence_content_hash(primary)
    corroborating_hash = recognition_evidence_content_hash(corroborating)

    output_tokens: list[CorrectedTimedToken] = []
    applied: list[CorrectionDecision] = []
    unresolved: list[CorrectionDecision] = []
    decision_number = 0

    def next_decision_id() -> str:
        nonlocal decision_number
        decision_number += 1
        return f"decision-{decision_number:08d}"

    def next_local_decision_id(
        *,
        window_index: int,
        difference_index: int,
        current: str,
        candidate: str,
        target_start: int,
        target_end: int,
    ) -> str:
        ordinal = next_decision_id()
        fingerprint = sha256(
            json.dumps(
                {
                    "schema_version": 1,
                    "primary_evidence_hash": primary_hash,
                    "corroborating_evidence_hash": corroborating_hash,
                    "window_index": window_index,
                    "difference_index": difference_index,
                    "current": current,
                    "candidate": candidate,
                    "target_start_char": target_start,
                    "target_end_char": target_end,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"{ordinal}-local-{fingerprint}"

    for window in windows:
        primary_token_ids = tuple(token.id for token in window.primary_tokens)
        corroborating_token_ids = tuple(
            token.id for token in window.corroborating_tokens
        )
        lineage = (
            _lineage(primary, primary_token_ids),
            _lineage(corroborating, corroborating_token_ids),
        )
        recognition_refs = tuple(
            [
                *(f"evidence:{primary_hash}:{token_id}" for token_id in primary_token_ids),
                *(
                    f"evidence:{corroborating_hash}:{token_id}"
                    for token_id in corroborating_token_ids
                ),
            ]
        )
        raw_primary = "".join(token.text for token in window.primary_tokens)
        if raw_primary != window.primary_text:
            applied.append(
                CorrectionDecision(
                    id=next_decision_id(),
                    status="applied",
                    category="traditional_orthography",
                    start_ms=window.start_ms,
                    end_ms=window.end_ms,
                    current=raw_primary,
                    candidates=(window.primary_text,),
                    selected=window.primary_text,
                    reason=(
                        "deterministic OpenCC s2tw orthographic projection and removal "
                        "of provider sentence punctuation"
                    ),
                    recognition_lineage=lineage,
                    references=(_tool_reference(raw_primary, window.primary_text),),
                )
            )

        selected = window.primary_text
        resolved_differences: set[int] = set()
        whole_glossary = _whole_window_glossary_replacement(window, reference_index)
        replacements: list[_Replacement] = (
            [] if whole_glossary is None else [whole_glossary]
        )
        occupied = {
            position
            for replacement in replacements
            for position in _replacement_positions(replacement)
        }
        for consensus in _consensus_glossary_subreplacements(window, reference_index):
            positions = _replacement_positions(consensus)
            if positions & occupied:
                continue
            replacements.append(consensus)
            occupied.update(positions)

        primary_key = window.primary_compact.text.casefold()
        corroborating_key = window.corroborating_compact.text.casefold()
        confirmation_lineage = (
            None
            if confirmation is None or not window.confirmation_tokens
            else _lineage(
                confirmation,
                tuple(token.id for token in window.confirmation_tokens),
            )
        )
        for difference_index, difference in enumerate(window.differences):
            replacement = _local_confirmation_replacement(
                window=window,
                difference=difference,
            )
            if replacement is None:
                replacement = _aligned_glossary_replacement(
                    window=window,
                    difference=difference,
                    index=reference_index,
                )
            if replacement is None:
                replacement = _single_asr_authoritative_glossary_replacement(
                    window=window,
                    difference=difference,
                    index=reference_index,
                    recognition_literals=recognition_literals,
                )

            empty_side = (
                difference.primary_start == difference.primary_end
                or difference.corroborating_start == difference.corroborating_end
            )
            if replacement is None and empty_side:
                continue

            if not empty_side:
                primary_support = _whole_difference_reference_support(
                    primary_key,
                    difference.primary_start,
                    difference.primary_end,
                    difference,
                    reference_index,
                )
                corroborating_support = _whole_difference_reference_support(
                    corroborating_key,
                    difference.corroborating_start,
                    difference.corroborating_end,
                    difference,
                    reference_index,
                )
                primary_length = 0 if primary_support is None else primary_support.length
                corroborating_length = (
                    0 if corroborating_support is None else corroborating_support.length
                )
                primary_part_start, primary_part_end = _expand_ascii_run(
                    primary_key,
                    difference.primary_start,
                    difference.primary_end,
                )
                corroborating_part_start, corroborating_part_end = _expand_ascii_run(
                    corroborating_key,
                    difference.corroborating_start,
                    difference.corroborating_end,
                )
                primary_raw_start, primary_raw_end = window.primary_compact.raw_span(
                    primary_part_start,
                    primary_part_end,
                )
                corroborating_raw_start, corroborating_raw_end = (
                    window.corroborating_compact.raw_span(
                        corroborating_part_start,
                        corroborating_part_end,
                    )
                )
                current_part = window.primary_text[primary_raw_start:primary_raw_end]
                corroborating_part = window.corroborating_text[
                    corroborating_raw_start:corroborating_raw_end
                ]

                candidates_compatible = _asr_spellings_are_compatible(
                    current_part,
                    corroborating_part,
                )
                if replacement is None:
                    replacement = _authoritative_glossary_name_replacement(
                        current=current_part,
                        corroborating=corroborating_part,
                        start=primary_raw_start,
                        end=primary_raw_end,
                        index=reference_index,
                    )
                phonetic = (
                    None
                    if replacement is not None
                    else _phonetic_reference_replacement(
                        current=current_part,
                        corroborating=corroborating_part,
                        index=reference_index,
                    )
                )
                if replacement is not None:
                    pass
                elif phonetic is not None:
                    spelling, spelling_references = phonetic
                    replacement = _Replacement(
                        start=primary_raw_start,
                        end=primary_raw_end,
                        current=current_part,
                        selected=spelling,
                        category=_reference_category(spelling_references),
                        reason=(
                            "both ASR candidates are pronunciation-compatible with one "
                            "complete caller-curated glossary term"
                        ),
                        references=spelling_references,
                        resolves_difference=True,
                    )
                elif primary_length > corroborating_length and candidates_compatible:
                    assert primary_support is not None
                    resolved_differences.add(difference_index)
                    continue

                elif corroborating_length > primary_length and candidates_compatible:
                    assert corroborating_support is not None
                    replacement = _Replacement(
                        start=primary_raw_start,
                        end=primary_raw_end,
                        current=current_part,
                        selected=corroborating_part,
                        category=_reference_category(corroborating_support.references),
                        reason=(
                            "the corroborating ASR has the uniquely longer exact spelling "
                            "attested by an enrolled reference"
                        ),
                        references=corroborating_support.references,
                        resolves_difference=True,
                    )
                if replacement is None:
                    embedded = _phonetic_glossary_subreplacements(
                        current=current_part,
                        corroborating=corroborating_part,
                        base_start=primary_raw_start,
                        index=reference_index,
                    )
                    for embedded_replacement in embedded:
                        positions = set(
                            range(
                                embedded_replacement.start,
                                embedded_replacement.end,
                            )
                        )
                        if positions & occupied:
                            continue
                        replacements.append(embedded_replacement)
                        occupied.update(positions)
                    if embedded and all(
                        any(
                            item.start <= position < item.end
                            for item in embedded
                        )
                        for position in range(primary_raw_start, primary_raw_end)
                    ):
                        resolved_differences.add(difference_index)
            if replacement is None:
                continue
            positions = _replacement_positions(replacement)
            if positions & occupied:
                continue
            replacements.append(replacement)
            occupied.update(positions)
            if replacement.resolves_difference:
                resolved_differences.add(difference_index)

        replacements.sort(key=lambda item: item.start)
        for replacement in replacements:
            interval_start, interval_end = _decision_interval(
                window.start_ms,
                window.end_ms,
                len(window.primary_text),
                replacement.start,
                replacement.end,
            )
            replacement_lineage = lineage
            if replacement.confirmation_supported:
                assert confirmation_lineage is not None
                replacement_lineage = (*lineage, confirmation_lineage)
            applied.append(
                CorrectionDecision(
                    id=next_decision_id(),
                    status="applied",
                    category=replacement.category,
                    start_ms=interval_start,
                    end_ms=interval_end,
                    current=replacement.current,
                    candidates=(replacement.selected,),
                    selected=replacement.selected,
                    reason=replacement.reason,
                    recognition_lineage=replacement_lineage,
                    references=replacement.references,
                )
            )
        for replacement in reversed(replacements):
            selected = (
                selected[: replacement.start]
                + replacement.selected
                + selected[replacement.end :]
            )

        unresolved_indices = tuple(
            index
            for index in range(len(window.differences))
            if index not in resolved_differences
        )
        window_output_start = sum(len(token.text) for token in output_tokens)
        for difference_index in unresolved_indices:
            difference = window.differences[difference_index]
            primary_raw_start, primary_raw_end = _difference_raw_span(
                window.primary_text,
                window.primary_compact,
                difference.primary_start,
                difference.primary_end,
            )
            candidate_raw_start, candidate_raw_end = _difference_raw_span(
                window.corroborating_text,
                window.corroborating_compact,
                difference.corroborating_start,
                difference.corroborating_end,
            )
            local_start, local_end = _transformed_span(
                primary_raw_start,
                primary_raw_end,
                replacements,
            )
            current_part = selected[local_start:local_end]
            candidate_part = window.corroborating_text[
                candidate_raw_start:candidate_raw_end
            ]
            if current_part == candidate_part:
                continue
            category, reason = _local_difference_category(window, difference_index)
            interval_start, interval_end = _decision_interval(
                window.start_ms,
                window.end_ms,
                len(window.primary_text),
                primary_raw_start,
                primary_raw_end,
            )
            unresolved.append(
                CorrectionDecision(
                    id=next_local_decision_id(
                        window_index=window.index,
                        difference_index=difference_index,
                        current=current_part,
                        candidate=candidate_part,
                        target_start=window_output_start + local_start,
                        target_end=window_output_start + local_end,
                    ),
                    status="unresolved",
                    category=category,
                    start_ms=interval_start,
                    end_ms=interval_end,
                    current=current_part,
                    candidates=(candidate_part,),
                    selected=None,
                    reason=reason,
                    recognition_lineage=lineage,
                    references=_local_difference_references(
                        window=window,
                        difference=difference,
                        index=reference_index,
                    ),
                    target_start_char=window_output_start + local_start,
                    target_end_char=window_output_start + local_end,
                )
            )

        output_tokens.extend(
            _component_output_tokens(
                primary_tokens=window.primary_tokens,
                corroborating_tokens=window.corroborating_tokens,
                original_traditional=window.primary_text,
                selected=selected,
                primary_evidence_hash=primary_hash,
                corroborating_evidence_hash=corroborating_hash,
                recognition_refs=recognition_refs,
                first_index=len(output_tokens),
            )
        )

    return AccurateCorrectionResult(
        schema_version=_SCHEMA_VERSION,
        episode_id=primary.episode_id,
        normalized_audio_hash=primary.normalized_audio_hash,
        status="completed_with_review" if unresolved else "completed",
        tokens=tuple(output_tokens),
        applied=tuple(applied),
        unresolved=tuple(unresolved),
    )


def bounded_review_packets(
    result: AccurateCorrectionResult,
    *,
    radius: int = 40,
) -> tuple[CorrectionReviewPacket, ...]:
    """Materialise unresolved items with at most ``radius`` characters per side."""

    if radius < 0:
        raise ValueError("review context radius must not be negative")
    transcript = result.text
    packets: list[CorrectionReviewPacket] = []
    for decision in result.unresolved:
        if decision.target_start_char is not None and decision.target_end_char is not None:
            target_start = decision.target_start_char
            target_end = decision.target_end_char
            if not 0 <= target_start <= target_end <= len(transcript):
                raise ValueError("review decision character target is out of range")
            if transcript[target_start:target_end] != decision.current:
                raise ValueError("review decision character target no longer exact-copies text")
            before = transcript[:target_start][-radius:]
            after = transcript[target_end:][:radius]
        else:
            # Backwards compatibility for already-persisted window-level JSON.
            overlapping = [
                index
                for index, token in enumerate(result.tokens)
                if token.start_ms < decision.end_ms and decision.start_ms < token.end_ms
            ]
            first = overlapping[0] if overlapping else 0
            last = overlapping[-1] + 1 if overlapping else len(result.tokens)
            before = "".join(token.text for token in result.tokens[:first])[-radius:]
            after = "".join(token.text for token in result.tokens[last:])[:radius]
        current = decision.current
        packets.append(
            CorrectionReviewPacket(
                decision_id=decision.id,
                start_ms=decision.start_ms,
                end_ms=decision.end_ms,
                before=before,
                current=current,
                after=after,
                candidates=decision.candidates,
                reason=decision.reason,
                references=decision.references,
                priority=(
                    "high"
                    if decision.category
                    in {
                        "recognition_disagreement_high_priority",
                        "recognition_coverage_gap",
                        "possible_primary_hallucination",
                    }
                    else "normal"
                ),
                allowed_choices=(
                    ("current", "defer")
                    if decision.category
                    in {"recognition_coverage_gap", "possible_primary_hallucination"}
                    else ("current", "candidate", "defer")
                ),
                window_current=before + decision.current + after,
                window_candidates=tuple(
                    before + candidate + after for candidate in decision.candidates
                ),
            )
        )
    return tuple(packets)


def _legacy_decision_character_span(
    tokens: Sequence[CorrectedTimedToken],
    decision: CorrectionDecision,
) -> tuple[int, int]:
    """Recover an old window target only when it exact-copies overlapped text."""

    starts: list[int] = []
    cursor = 0
    for token in tokens:
        if token.start_ms < decision.end_ms and decision.start_ms < token.end_ms:
            starts.append(cursor)
        cursor += len(token.text)
    if not starts:
        raise ValueError("review decision no longer overlaps corrected token evidence")
    start = starts[0]
    end = starts[-1] + len(
        next(
            token
            for token in reversed(tokens)
            if token.start_ms < decision.end_ms and decision.start_ms < token.end_ms
        ).text
    )
    if "".join(token.text for token in tokens)[start:end] != decision.current:
        raise ValueError("legacy review decision does not exact-copy corrected token evidence")
    return start, end


def _replace_local_character_span(
    tokens: list[CorrectedTimedToken],
    *,
    start: int,
    end: int,
    current: str,
    chosen: str,
) -> None:
    """Replace only one exact transcript subspan, preserving surrounding text."""

    if start >= end:
        raise ValueError("zero-width review candidate requires bounded audio confirmation")
    token_ranges: list[tuple[int, int]] = []
    cursor = 0
    for token in tokens:
        token_ranges.append((cursor, cursor + len(token.text)))
        cursor += len(token.text)
    if not 0 <= start < end <= cursor:
        raise ValueError("review decision character target is out of range")
    overlapping = [
        index
        for index, (token_start, token_end) in enumerate(token_ranges)
        if token_start < end and start < token_end
    ]
    if not overlapping:
        raise ValueError("review decision character target has no token evidence")
    first = overlapping[0]
    last = overlapping[-1] + 1
    current_tokens = tokens[first:last]
    union_start = token_ranges[first][0]
    union_text = "".join(token.text for token in current_tokens)
    local_start = start - union_start
    local_end = end - union_start
    if union_text[local_start:local_end] != current:
        raise ValueError("review decision target no longer exact-copies corrected token evidence")
    selected_text = union_text[:local_start] + chosen + union_text[local_end:]
    confidences = [
        token.confidence for token in current_tokens if token.confidence is not None
    ]
    speakers = {token.speaker for token in current_tokens}
    source_ids = tuple(
        dict.fromkeys(
            source_id
            for token in current_tokens
            for source_id in token.source_primary_token_ids
        )
    )
    recognition_refs = tuple(
        dict.fromkeys(
            reference
            for token in current_tokens
            for reference in token.recognition_refs
        )
    )
    tokens[first:last] = _timed_atomic_tokens(
        text=selected_text,
        start_ms=current_tokens[0].start_ms,
        end_ms=current_tokens[-1].end_ms,
        confidence=min(confidences) if confidences else None,
        speaker=next(iter(speakers)) if len(speakers) == 1 else None,
        source_ids=source_ids,
        recognition_refs=recognition_refs,
        first_index=first,
    )


def apply_review_selections(
    result: AccurateCorrectionResult,
    selections: Sequence[CorrectionReviewSelection],
) -> AccurateCorrectionResult:
    """Apply exact stored candidate choices; arbitrary replacement text is impossible."""

    selection_items = tuple(selections)
    selection_by_id = {selection.decision_id: selection for selection in selection_items}
    if len(selection_by_id) != len(selection_items):
        raise ValueError("review selection decision_id values must be unique")
    unresolved_by_id = {decision.id: decision for decision in result.unresolved}
    unknown = sorted(set(selection_by_id) - set(unresolved_by_id))
    if unknown:
        raise ValueError(f"review selection targets unknown decision: {unknown[0]}")

    transcript = result.text
    validated: dict[str, tuple[str, str, str, int, int]] = {}
    mutations: list[tuple[int, int, str, str]] = []
    occupied: list[tuple[int, int]] = []
    for decision in result.unresolved:
        selection = selection_by_id.get(decision.id)
        if selection is None or selection.choice == "defer":
            continue
        if selection.choice not in {"current", "candidate"}:
            raise ValueError(f"unsupported review selection choice: {selection.choice}")
        if decision.target_start_char is None or decision.target_end_char is None:
            target_start, target_end = _legacy_decision_character_span(
                result.tokens,
                decision,
            )
        else:
            target_start = decision.target_start_char
            target_end = decision.target_end_char
        if not 0 <= target_start <= target_end <= len(transcript):
            raise ValueError("review decision character target is out of range")
        if transcript[target_start:target_end] != decision.current:
            raise ValueError("review decision target no longer exact-copies correction text")
        if selection.choice == "candidate":
            if decision.category in {
                "recognition_coverage_gap",
                "possible_primary_hallucination",
            }:
                raise ValueError(
                    "coverage or hallucination candidate requires bounded audio confirmation"
                )
            assert selection.candidate_index is not None
            if selection.candidate_index >= len(decision.candidates):
                raise ValueError("review selection candidate_index is out of range")
            chosen = decision.candidates[selection.candidate_index]
            category = "bounded_review_candidate_selection"
            reason = "bounded review selected one exact stored local ASR candidate"
            for other_start, other_end in occupied:
                if target_start < other_end and other_start < target_end:
                    raise ValueError("review selection character targets must not overlap")
            occupied.append((target_start, target_end))
            if chosen != decision.current:
                mutations.append((target_start, target_end, decision.current, chosen))
        else:
            chosen = decision.current
            category = "bounded_review_primary_selection"
            reason = "bounded review retained the exact stored local current candidate"
        validated[decision.id] = (chosen, category, reason, target_start, target_end)

    tokens = list(result.tokens)
    ordered_mutations = sorted(
        mutations,
        key=lambda item: (item[0], item[1]),
    )
    for target_start, target_end, current, chosen in reversed(ordered_mutations):
        _replace_local_character_span(
            tokens,
            start=target_start,
            end=target_end,
            current=current,
            chosen=chosen,
        )

    def shifted_boundary(offset: int) -> int:
        delta = 0
        for mutation_start, mutation_end, _current, chosen in ordered_mutations:
            if mutation_end <= offset:
                delta += len(chosen) - (mutation_end - mutation_start)
                continue
            if mutation_start < offset < mutation_end:
                raise ValueError("review selection overlaps another unresolved target")
            break
        return offset + delta

    newly_applied: list[CorrectionDecision] = []
    remaining: list[CorrectionDecision] = []
    for decision in result.unresolved:
        selection = selection_by_id.get(decision.id)
        if selection is None or selection.choice == "defer":
            if decision.target_start_char is None or decision.target_end_char is None:
                remaining.append(decision)
            else:
                remaining.append(
                    replace(
                        decision,
                        target_start_char=shifted_boundary(decision.target_start_char),
                        target_end_char=shifted_boundary(decision.target_end_char),
                    )
                )
            continue
        chosen, category, reason, target_start, target_end = validated[decision.id]
        newly_applied.append(
            CorrectionDecision(
                id=decision.id,
                status="applied",
                category=category,
                start_ms=decision.start_ms,
                end_ms=decision.end_ms,
                current=decision.current,
                candidates=decision.candidates,
                selected=chosen,
                reason=reason,
                recognition_lineage=decision.recognition_lineage,
                references=decision.references,
                target_start_char=target_start,
                target_end_char=target_end,
            )
        )

    tokens = [replace(token, id=f"corrected-{index:08d}") for index, token in enumerate(tokens)]
    updated_text = "".join(token.text for token in tokens)
    for decision in remaining:
        if decision.target_start_char is None or decision.target_end_char is None:
            continue
        if (
            updated_text[decision.target_start_char : decision.target_end_char]
            != decision.current
        ):
            raise ValueError("remaining review target no longer exact-copies correction text")
    return AccurateCorrectionResult(
        schema_version=result.schema_version,
        episode_id=result.episode_id,
        normalized_audio_hash=result.normalized_audio_hash,
        status="completed_with_review" if remaining else "completed",
        tokens=tuple(tokens),
        applied=tuple((*result.applied, *newly_applied)),
        unresolved=tuple(remaining),
    )


def render_accurate_correction_json(result: AccurateCorrectionResult) -> str:
    """Render deterministic UTF-8-friendly JSON for integration artifacts."""

    return json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _tupled_lineage(payload: Mapping[str, Any]) -> RecognitionLineage:
    return RecognitionLineage(
        evidence_hash=str(payload["evidence_hash"]),
        adapter=str(payload["adapter"]),
        model=str(payload["model"]),
        token_ids=tuple(str(item) for item in payload["token_ids"]),
    )


def _tupled_reference(payload: Mapping[str, Any]) -> CorrectionReference:
    kind = str(payload["kind"])
    if kind not in {"book", "outline", "glossary", "tool"}:
        raise ValueError(f"unsupported correction reference kind: {kind}")
    return CorrectionReference(
        source_id=str(payload["source_id"]),
        kind=kind,  # type: ignore[arg-type]
        locator=str(payload["locator"]),
        excerpt=str(payload["excerpt"]),
    )


def _tupled_decision(payload: Mapping[str, Any]) -> CorrectionDecision:
    status = str(payload["status"])
    if status not in {"applied", "unresolved"}:
        raise ValueError(f"unsupported correction decision status: {status}")
    lineage = tuple(_tupled_lineage(item) for item in payload["recognition_lineage"])
    if len(lineage) not in {2, 3}:
        raise ValueError("correction decision requires two or three recognition lineages")
    return CorrectionDecision(
        id=str(payload["id"]),
        status=status,  # type: ignore[arg-type]
        category=str(payload["category"]),
        start_ms=int(payload["start_ms"]),
        end_ms=int(payload["end_ms"]),
        current=str(payload["current"]),
        candidates=tuple(str(item) for item in payload["candidates"]),
        selected=None if payload["selected"] is None else str(payload["selected"]),
        reason=str(payload["reason"]),
        recognition_lineage=lineage,
        references=tuple(_tupled_reference(item) for item in payload["references"]),
        target_start_char=(
            None
            if payload.get("target_start_char") is None
            else int(payload["target_start_char"])
        ),
        target_end_char=(
            None
            if payload.get("target_end_char") is None
            else int(payload["target_end_char"])
        ),
    )


def parse_accurate_correction_json(payload: str | bytes) -> AccurateCorrectionResult:
    """Parse the canonical JSON form and reject incompatible schema versions."""

    raw = json.loads(payload)
    if not isinstance(raw, dict):
        raise ValueError("accurate correction JSON root must be an object")
    if raw.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError("unsupported accurate correction schema_version")
    status = str(raw["status"])
    if status not in {"completed", "completed_with_review"}:
        raise ValueError(f"unsupported accurate correction status: {status}")
    tokens = tuple(
        CorrectedTimedToken(
            id=str(item["id"]),
            text=str(item["text"]),
            start_ms=int(item["start_ms"]),
            end_ms=int(item["end_ms"]),
            confidence=None if item["confidence"] is None else float(item["confidence"]),
            speaker=None if item["speaker"] is None else str(item["speaker"]),
            source_primary_token_ids=tuple(
                str(value) for value in item["source_primary_token_ids"]
            ),
            recognition_refs=tuple(str(value) for value in item["recognition_refs"]),
        )
        for item in raw["tokens"]
    )
    return AccurateCorrectionResult(
        schema_version=_SCHEMA_VERSION,
        episode_id=str(raw["episode_id"]),
        normalized_audio_hash=str(raw["normalized_audio_hash"]),
        status=status,  # type: ignore[arg-type]
        tokens=tokens,
        applied=tuple(_tupled_decision(item) for item in raw["applied"]),
        unresolved=tuple(_tupled_decision(item) for item in raw["unresolved"]),
    )


__all__ = [
    "AccurateCorrectionResult",
    "CorrectedTimedToken",
    "CorrectionDecision",
    "CorrectionReference",
    "CorrectionReferenceSource",
    "CorrectionReviewPacket",
    "CorrectionReviewSelection",
    "RecognitionLineage",
    "apply_review_selections",
    "bounded_review_packets",
    "correct_recognition",
    "parse_accurate_correction_json",
    "render_accurate_correction_json",
]
