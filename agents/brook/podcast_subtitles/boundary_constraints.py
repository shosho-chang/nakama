"""Deterministic zh-TW boundary rules independent of Semantic model output.

The model may contribute soft/hard Semantic Units, but it is never the sole
authority for speaker, typography, protected terminology, number/unit, or
closed-class-word legality.  This module emits one typed relation for every
adjacent Canonical token edge and can be rerun by QC after a fresh process.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from shared.schemas.podcast_subtitles_v2 import (
    BoundaryChannelRelation,
    BoundaryConstraintEdge,
    BoundaryConstraintReceipt,
    BoundaryReasonCode,
    CanonicalToken,
    CanonicalTranscript,
    DisplayMetricsIdentitySnapshot,
    ProjectionProfile,
    ProtectedSemanticKind,
    ProtectedSemanticSource,
    ProtectedTokenRange,
    SemanticUnit,
)

from .display_metrics import display_columns, display_metrics_identity
from .errors import ProjectionUnsatisfiableError
from .hashing import hash_file, hash_object, sha256_bytes

RULE_SET_ID = "nakama-zh-hant-boundaries"
RULE_SET_VERSION = 1
_LONG_SPEAKER_RUN_MIN_TOKENS = 6
_MAX_BOUNDED_STRUCTURE_SCALARS = 16

_CLOSED_CLASS = frozenset(
    {
        "的",
        "了",
        "嗎",
        "呢",
        "吧",
        "把",
        "被",
        "在",
        "從",
        "向",
        "對",
        "跟",
        "與",
        "和",
        "而",
        "也",
        "就",
        "才",
        "都",
        "又",
    }
)
_NEGATIONS = frozenset({"不", "沒", "沒有", "無", "非", "未", "別"})
_CONNECTORS = frozenset({"但是", "所以", "因為", "如果", "而且", "然後", "不過", "可是", "其實"})
_FILLERS = frozenset({"嗯", "呃", "額", "欸", "誒", "啊", "那個", "就是"})
_CLASSIFIERS = (
    "個|位|名|本|篇|份|項|種|次|件|顆|支|張|台|部|套|組|杯|碗|公斤|"
    "公克|克|毫克|微克|公升|毫升|公里|公尺|公分|毫米|小時|分鐘|秒|天|週|月|年"
)
_UNITS = "kg|g|mg|μg|ug|km|m|cm|mm|L|mL|ml|kcal|cal|Hz|kHz|MHz|GB|MB|%|％|" + _CLASSIFIERS
_OPEN_TO_CLOSE = {
    "(": ")",
    "（": "）",
    "[": "]",
    "［": "］",
    "{": "}",
    "「": "」",
    "『": "』",
    "《": "》",
    "〈": "〉",
    "“": "”",
}
_CLOSE_TO_OPEN = {closer: opener for opener, closer in _OPEN_TO_CLOSE.items()}
_BOUNDARY_PUNCTUATION = "，。！？!?；;：:\n"


class BoundaryConstraintError(ProjectionUnsatisfiableError):
    """Boundary Evidence is incomplete, contradictory, or degenerate."""


@dataclass(frozen=True, slots=True)
class ProtectedTermMetadata:
    """Least-context metadata; it never asserts that reference prose was spoken."""

    value: str
    kind: ProtectedSemanticKind = "term"
    source: ProtectedSemanticSource = "policy_vocabulary"
    reference_evidence_ids: tuple[str, ...] = ()
    scope_token_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise ValueError("protected term metadata requires a non-blank value")
        if self.source == "retrieved_reference_metadata" and not self.reference_evidence_ids:
            raise ValueError("reference-derived term metadata requires Evidence IDs")
        if self.source == "policy_vocabulary" and self.reference_evidence_ids:
            raise ValueError("policy vocabulary cannot claim Reference Evidence")
        if len(set(self.reference_evidence_ids)) != len(self.reference_evidence_ids):
            raise ValueError("protected term metadata Evidence IDs must be unique")
        if len(set(self.scope_token_ids)) != len(self.scope_token_ids):
            raise ValueError("protected term metadata scope token IDs must be unique")
        if self.source == "retrieved_reference_metadata" and not self.scope_token_ids:
            raise ValueError("reference-derived term metadata requires a token scope")


@dataclass(slots=True)
class _MutableEdge:
    cue_relation: BoundaryChannelRelation = "neutral"
    line_relation: BoundaryChannelRelation = "neutral"
    semantic_strength: float = 0.0
    reasons: set[BoundaryReasonCode] | None = None

    def __post_init__(self) -> None:
        if self.reasons is None:
            self.reasons = {"no_semantic_signal"}


def _casefold_occurrences(value: str, needle: str) -> tuple[tuple[int, int], ...]:
    folded_parts: list[str] = []
    original_ranges: list[tuple[int, int]] = []
    for index, character in enumerate(value):
        folded = character.casefold()
        folded_parts.append(folded)
        original_ranges.extend((index, index + 1) for _ in folded)
    folded_value = "".join(folded_parts)
    folded_needle = needle.casefold()
    if not folded_needle:
        return ()
    matches: list[tuple[int, int]] = []
    start = 0
    while (position := folded_value.find(folded_needle, start)) >= 0:
        end = position + len(folded_needle)
        matches.append((original_ranges[position][0], original_ranges[end - 1][1]))
        start = position + 1
    return tuple(matches)


def _token_offsets(tokens: Sequence[CanonicalToken]) -> tuple[tuple[int, ...], str]:
    offsets = [0]
    for token in tokens:
        offsets.append(offsets[-1] + len(token.text))
    return tuple(offsets), "".join(token.text for token in tokens)


def _aligned_token_range(offsets: Sequence[int], start: int, end: int) -> tuple[int, int] | None:
    starts = {offset: index for index, offset in enumerate(offsets[:-1])}
    ends = {offset: index for index, offset in enumerate(offsets[1:], start=1)}
    if start not in starts or end not in ends:
        return None
    left, right = starts[start], ends[end]
    if left >= right:
        return None
    return left, right


def derive_protected_token_ranges(
    tokens: Sequence[CanonicalToken],
    metadata: Sequence[ProtectedTermMetadata],
) -> tuple[ProtectedTokenRange, ...]:
    """Project only exact, already-spoken metadata matches onto token IDs."""

    token_tuple = tuple(tokens)
    offsets, canonical_text = _token_offsets(token_tuple)
    grouped: dict[tuple[tuple[str, ...], ProtectedSemanticKind], list[ProtectedTermMetadata]] = {}
    for item in metadata:
        for start, end in _casefold_occurrences(canonical_text, item.value):
            aligned = _aligned_token_range(offsets, start, end)
            if aligned is None:
                continue
            left, right = aligned
            token_ids = tuple(token.id for token in token_tuple[left:right])
            if item.scope_token_ids and not set(token_ids).issubset(item.scope_token_ids):
                continue
            grouped.setdefault((token_ids, item.kind), []).append(item)

    protected: list[ProtectedTokenRange] = []
    for (token_ids, kind), sources in sorted(
        grouped.items(), key=lambda entry: (entry[0][0], entry[0][1])
    ):
        reference_ids = tuple(
            sorted({evidence_id for item in sources for evidence_id in item.reference_evidence_ids})
        )
        source: ProtectedSemanticSource = (
            "retrieved_reference_metadata" if reference_ids else "policy_vocabulary"
        )
        values = tuple(sorted({item.value for item in sources}, key=str.casefold))
        source_value_hash = hash_object(values)
        token_by_id = {token.id: token for token in token_tuple}
        matched_text = "".join(token_by_id[token_id].text for token_id in token_ids)
        identity = {
            "token_ids": token_ids,
            "canonical_text": matched_text,
            "kind": kind,
            "source": source,
            "source_value_hash": source_value_hash,
            "reference_evidence_ids": reference_ids,
        }
        protected.append(
            ProtectedTokenRange(
                id="protected-" + hash_object(identity),
                token_ids=token_ids,
                canonical_text=matched_text,
                kind=kind,
                source=source,
                source_value_hash=source_value_hash,
                reference_evidence_ids=reference_ids,
            )
        )
    return tuple(protected)


def _semantic_ranges(
    tokens: Sequence[CanonicalToken], units: Sequence[SemanticUnit]
) -> tuple[tuple[SemanticUnit, int, int], ...]:
    positions = {token.id: index for index, token in enumerate(tokens)}
    covered: set[str] = set()
    seen_ranges: dict[tuple[str, ...], tuple[float, bool, bool, str | None, str | None]] = {}
    result: list[tuple[SemanticUnit, int, int]] = []
    for unit in units:
        try:
            indices = [positions[token_id] for token_id in unit.token_ids]
        except KeyError as exc:
            raise BoundaryConstraintError(
                f"Semantic Unit references unknown token {exc.args[0]!r}"
            ) from exc
        if indices != list(range(indices[0], indices[-1] + 1)):
            raise BoundaryConstraintError("Semantic Unit range is not ordered and contiguous")
        effect = (
            unit.strength,
            unit.forbid_cue_breaks,
            unit.forbid_line_breaks,
            unit.cue_boundary_relation,
            unit.line_boundary_relation,
        )
        existing = seen_ranges.get(unit.token_ids)
        if existing is not None:
            relation = "duplicate" if existing == effect else "conflicting"
            raise BoundaryConstraintError(f"Semantic Unit range is {relation}")
        seen_ranges[unit.token_ids] = effect
        covered.update(unit.token_ids)
        result.append((unit, indices[0], indices[-1] + 1))
    expected = {token.id for token in tokens}
    if covered != expected:
        missing = tuple(sorted(expected - covered))
        raise BoundaryConstraintError(f"Semantic Unit coverage is incomplete: {missing!r}")
    return tuple(result)


def validate_semantic_non_degeneracy(
    tokens: Sequence[CanonicalToken], units: Sequence[SemanticUnit]
) -> None:
    """Reject shape-correct model output that carries no boundary information."""

    token_tuple = tuple(tokens)
    ranged = _semantic_ranges(token_tuple, tuple(units))
    if len(token_tuple) >= 4 and len(ranged) == 1:
        _unit, start, end = ranged[0]
        if start == 0 and end == len(token_tuple):
            raise BoundaryConstraintError("Semantic Analyzer emitted one whole-transcript unit")

    run_start = 0
    while run_start < len(token_tuple):
        run_end = run_start + 1
        while (
            run_end < len(token_tuple)
            and token_tuple[run_end].speaker == token_tuple[run_start].speaker
        ):
            run_end += 1
        if run_end - run_start >= _LONG_SPEAKER_RUN_MIN_TOKENS:
            informative = [
                (unit, start, end)
                for unit, start, end in ranged
                if run_start <= start < end <= run_end
                and end - start > 1
                and (
                    unit.strength > 0
                    or unit.forbid_cue_breaks
                    or unit.forbid_line_breaks
                    or unit.cue_boundary_relation not in {None, "neutral"}
                    or unit.line_boundary_relation not in {None, "neutral"}
                )
            ]
            if not informative:
                raise BoundaryConstraintError(
                    "Semantic Analyzer emitted singleton-only/all-neutral output "
                    "for a long same-speaker run"
                )
            if len(informative) == 1 and informative[0][1:] == (run_start, run_end):
                raise BoundaryConstraintError(
                    "Semantic Analyzer emitted one undifferentiated whole speaker-run unit"
                )
        run_start = run_end


_RELATION_PRIORITY = {
    "neutral": 0,
    "preferred": 1,
    "discouraged": 2,
    "uncertain": 3,
    "forbidden": 4,
    "mandatory": 5,
}


def _merge_relation(
    current: BoundaryChannelRelation,
    incoming: BoundaryChannelRelation,
) -> BoundaryChannelRelation:
    if {current, incoming} == {"mandatory", "forbidden"}:
        raise BoundaryConstraintError("one edge is both mandatory and forbidden")
    if current == "mandatory" or incoming == "mandatory":
        return "mandatory"
    return incoming if _RELATION_PRIORITY[incoming] > _RELATION_PRIORITY[current] else current


def _apply_edge(
    edges: list[_MutableEdge],
    edge_index: int,
    *,
    cue: BoundaryChannelRelation,
    line: BoundaryChannelRelation,
    reason: BoundaryReasonCode,
    strength: float = 0.0,
) -> None:
    if not 1 <= edge_index <= len(edges):
        return
    edge = edges[edge_index - 1]
    edge.cue_relation = _merge_relation(edge.cue_relation, cue)
    edge.line_relation = _merge_relation(edge.line_relation, line)
    edge.semantic_strength = max(edge.semantic_strength, strength)
    assert edge.reasons is not None
    edge.reasons.discard("no_semantic_signal")
    edge.reasons.add(reason)


def _apply_range(
    edges: list[_MutableEdge],
    start: int,
    end: int,
    *,
    cue: BoundaryChannelRelation,
    line: BoundaryChannelRelation,
    reason: BoundaryReasonCode,
    strength: float = 0.0,
) -> None:
    for edge_index in range(start + 1, end):
        _apply_edge(
            edges,
            edge_index,
            cue=cue,
            line=line,
            reason=reason,
            strength=strength,
        )


def _reason_for_protected(kind: ProtectedSemanticKind) -> BoundaryReasonCode:
    return {
        "name": "protected_name",
        "term": "protected_term",
        "book_title": "protected_book_title",
        "report_title": "protected_report_title",
        "code_switch": "protected_code_switch",
        "url": "protected_url",
    }[kind]  # type: ignore[return-value]


def _profile_hard_columns(profile: ProjectionProfile) -> float:
    return profile.hard_line_display_columns


def _regex_ranges(
    text: str, offsets: Sequence[int], pattern: str, *, flags: int = 0
) -> Iterable[tuple[int, int]]:
    for match in re.finditer(pattern, text, flags):
        aligned = _aligned_token_range(offsets, match.start(), match.end())
        if aligned is not None:
            yield aligned


def assess_boundary_edges(
    tokens: Sequence[CanonicalToken],
    units: Sequence[SemanticUnit],
    profile: ProjectionProfile,
    *,
    protected_ranges: Sequence[ProtectedTokenRange] = (),
) -> tuple[BoundaryConstraintEdge, ...]:
    """Return complete cue/line relations for all ``N-1`` token edges."""

    token_tuple = tuple(tokens)
    if not token_tuple:
        raise BoundaryConstraintError("cannot assess an empty token stream")
    validate_semantic_non_degeneracy(token_tuple, tuple(units))
    ranged = _semantic_ranges(token_tuple, tuple(units))
    positions = {token.id: index for index, token in enumerate(token_tuple)}
    edges = [_MutableEdge() for _ in range(max(0, len(token_tuple) - 1))]

    for unit, start, end in ranged:
        if unit.kind == "boundary_pair":
            edge_index = start + 1
            assert unit.cue_boundary_relation is not None
            assert unit.line_boundary_relation is not None
            relation_reasons = (
                ("forbidden", "semantic_hard_unit"),
                ("discouraged", "semantic_soft_crossing"),
                ("preferred", "semantic_unit_end"),
            )
            for relation, reason in relation_reasons:
                cue = (
                    unit.cue_boundary_relation
                    if unit.cue_boundary_relation == relation
                    else "neutral"
                )
                line = (
                    unit.line_boundary_relation
                    if unit.line_boundary_relation == relation
                    else "neutral"
                )
                if cue == "neutral" and line == "neutral":
                    continue
                _apply_edge(
                    edges,
                    edge_index,
                    cue=cue,
                    line=line,
                    reason=reason,  # type: ignore[arg-type]
                    strength=unit.strength,
                )
            continue
        if unit.forbid_cue_breaks or unit.forbid_line_breaks:
            _apply_range(
                edges,
                start,
                end,
                cue="forbidden" if unit.forbid_cue_breaks else "discouraged",
                line="forbidden" if unit.forbid_line_breaks else "discouraged",
                reason="semantic_hard_unit",
                strength=unit.strength,
            )
        elif end - start > 1 and unit.strength > 0:
            _apply_range(
                edges,
                start,
                end,
                cue="discouraged",
                line="discouraged",
                reason="semantic_soft_crossing",
                strength=unit.strength,
            )
        if end < len(token_tuple):
            _apply_edge(
                edges,
                end,
                cue="preferred",
                line="preferred",
                reason="semantic_unit_end",
                strength=unit.strength,
            )

    for protected in protected_ranges:
        try:
            indices = [positions[token_id] for token_id in protected.token_ids]
        except KeyError as exc:
            raise BoundaryConstraintError(
                f"protected range references unknown token {exc.args[0]!r}"
            ) from exc
        if indices != list(range(indices[0], indices[-1] + 1)):
            raise BoundaryConstraintError("protected token range is not contiguous")
        line_relation: BoundaryChannelRelation = (
            "forbidden"
            if display_columns(protected.canonical_text) <= _profile_hard_columns(profile)
            else "discouraged"
        )
        _apply_range(
            edges,
            indices[0],
            indices[-1] + 1,
            cue="forbidden",
            line=line_relation,
            reason=_reason_for_protected(protected.kind),
            strength=1.0,
        )

    offsets, text = _token_offsets(token_tuple)

    def apply_regex(
        pattern: str,
        reason: BoundaryReasonCode,
        *,
        flags: int = 0,
        cue: BoundaryChannelRelation = "forbidden",
        line: BoundaryChannelRelation = "forbidden",
    ) -> None:
        for start, end in _regex_ranges(text, offsets, pattern, flags=flags):
            _apply_range(edges, start, end, cue=cue, line=line, reason=reason, strength=1.0)

    number = r"(?:\d+(?:[.,]\d+)?|[零〇一二兩三四五六七八九十百千萬億幾多]+)"
    apply_regex(r"\d+[.,]\d+", "numeric_decimal")
    apply_regex(rf"(?:{number}\s*(?:%|％)|百分之{number})", "numeric_percent")
    apply_regex(
        rf"{number}\s*(?:-|–|—|~|～|至|到)\s*{number}(?:\s*(?:{_UNITS}))?",
        "numeric_range",
        flags=re.IGNORECASE,
    )
    apply_regex(
        r"(?:\d{4}[年/-]\d{1,2}(?:[月/-]\d{1,2}日?)?|\d{1,2}[:：]\d{2}(?::\d{2})?)",
        "numeric_date_time",
    )
    apply_regex(r"(?:[vV]\d+(?:\.\d+)+|\d+(?:\.\d+){2,})", "numeric_version")
    apply_regex(rf"{number}\s*(?:{_UNITS})", "numeric_unit", flags=re.IGNORECASE)
    apply_regex(rf"{number}(?:{_CLASSIFIERS})", "classifier_phrase")
    apply_regex(
        rf"的{number}(?:{_CLASSIFIERS})[\u3400-\u9fff]{{1,4}}(?=$|[{_BOUNDARY_PUNCTUATION}])",
        "classifier_phrase",
    )
    apply_regex(
        rf"(?:在|從|向|對|把|被|跟)[^{_BOUNDARY_PUNCTUATION}]{{1,{_MAX_BOUNDED_STRUCTURE_SCALARS}}}?(?:之前|之後|以前|以後|當中|裡面|上面|下面)",
        "bounded_preposition_structure",
    )
    apply_regex(r"(?:https?://|www\.)[^\s，。！？!?；;]+", "protected_url", flags=re.IGNORECASE)
    apply_regex(
        r"[A-Za-z][A-Za-z0-9]*(?:[-+._/][A-Za-z0-9]+)+|[A-Za-z]{2,}",
        "protected_code_switch",
    )

    # Pair punctuation with a real stack.  Short pairs are fully atomic;
    # longer pairs keep opener/closer attached but may wrap internally.
    stack: list[tuple[str, int]] = []
    paired_ranges: list[tuple[int, int]] = []
    unmatched_positions: list[int] = []
    for scalar_index, character in enumerate(text):
        if character in _OPEN_TO_CLOSE:
            stack.append((character, scalar_index))
        elif character in _CLOSE_TO_OPEN:
            expected = _CLOSE_TO_OPEN[character]
            if not stack or stack[-1][0] != expected:
                unmatched_positions.append(scalar_index)
                continue
            _opener, opener_index = stack.pop()
            paired_ranges.append((opener_index, scalar_index + 1))
    unmatched_positions.extend(position for _opener, position in stack)
    for scalar_start, scalar_end in paired_ranges:
        aligned = _aligned_token_range(offsets, scalar_start, scalar_end)
        if aligned is None:
            continue
        start, end = aligned
        short = display_columns(text[scalar_start:scalar_end]) <= _profile_hard_columns(profile)
        _apply_range(
            edges,
            start,
            end,
            cue="forbidden" if short else "discouraged",
            line="forbidden" if short else "discouraged",
            reason="paired_punctuation",
            strength=1.0,
        )
        _apply_edge(
            edges,
            start + 1,
            cue="forbidden",
            line="forbidden",
            reason="punctuation_opener",
            strength=1.0,
        )
        _apply_edge(
            edges,
            end - 1,
            cue="forbidden",
            line="forbidden",
            reason="punctuation_closer",
            strength=1.0,
        )
    for scalar_position in unmatched_positions:
        # Mark only adjacent aligned edges as uncertain.  A speaker-mandatory
        # edge remains mandatory; uncertainty cannot erase speaker purity.
        nearest = min(
            range(1, len(offsets) - 1),
            key=lambda edge_index: abs(offsets[edge_index] - scalar_position),
            default=0,
        )
        if nearest and token_tuple[nearest - 1].speaker == token_tuple[nearest].speaker:
            _apply_edge(
                edges,
                nearest,
                cue="uncertain",
                line="uncertain",
                reason="unmatched_punctuation",
            )

    for index, token in enumerate(token_tuple):
        token_text = token.text.strip()
        if token_text in _CLOSED_CLASS:
            # In 「的一個展現」, 的 opens a following classifier phrase.  The
            # production classifier rule owns the right side; forbidding the
            # left edge as a generic orphan rule would reproduce the Anji bug.
            de_classifier_start = token_text == "的" and re.match(
                rf"^{number}(?:{_CLASSIFIERS})",
                "".join(item.text for item in token_tuple[index + 1 : index + 4]),
            )
            adjacent_edges = (index + 1,) if de_classifier_start else (index, index + 1)
            for edge_index in adjacent_edges:
                if 1 <= edge_index < len(token_tuple) and (
                    token_tuple[edge_index - 1].speaker == token_tuple[edge_index].speaker
                ):
                    _apply_edge(
                        edges,
                        edge_index,
                        cue="forbidden",
                        line="discouraged",
                        reason="closed_class_no_orphan",
                        strength=0.8,
                    )
        if token_text in _NEGATIONS and index + 1 < len(token_tuple):
            if token.speaker == token_tuple[index + 1].speaker:
                _apply_edge(
                    edges,
                    index + 1,
                    cue="forbidden",
                    line="forbidden",
                    reason="negation_no_orphan",
                    strength=1.0,
                )
        if token_text in _CONNECTORS:
            for edge_index in (index, index + 1):
                if 1 <= edge_index < len(token_tuple) and (
                    token_tuple[edge_index - 1].speaker == token_tuple[edge_index].speaker
                ):
                    _apply_edge(
                        edges,
                        edge_index,
                        cue="forbidden",
                        line="discouraged",
                        reason="connector_no_orphan",
                        strength=0.8,
                    )
        if token_text in _FILLERS:
            for edge_index in (index, index + 1):
                if 1 <= edge_index < len(token_tuple) and (
                    token_tuple[edge_index - 1].speaker == token_tuple[edge_index].speaker
                ):
                    _apply_edge(
                        edges,
                        edge_index,
                        cue="forbidden",
                        line="discouraged",
                        reason="filler_no_independent_cue",
                        strength=0.8,
                    )
                    assert edges[edge_index - 1].reasons is not None
                    edges[edge_index - 1].reasons.add("asr_timestamp_gap_not_prosody")
        if (
            index + 1 < len(token_tuple)
            and token_text
            and token_text == token_tuple[index + 1].text.strip()
            and token.speaker == token_tuple[index + 1].speaker
        ):
            _apply_edge(
                edges,
                index + 1,
                cue="forbidden",
                line="discouraged",
                reason="self_repair_cohesion",
                strength=0.8,
            )

    for edge_index in range(1, len(token_tuple)):
        if token_tuple[edge_index - 1].speaker != token_tuple[edge_index].speaker:
            _apply_edge(
                edges,
                edge_index,
                cue="mandatory",
                line="mandatory",
                reason="speaker_change",
                strength=1.0,
            )

    typed: list[BoundaryConstraintEdge] = []
    for index, edge in enumerate(edges, start=1):
        reasons = tuple(sorted(edge.reasons or {"no_semantic_signal"}))
        material_uncertainty = "uncertain" in {
            edge.cue_relation,
            edge.line_relation,
        }
        typed.append(
            BoundaryConstraintEdge(
                edge_index=index,
                left_token_id=token_tuple[index - 1].id,
                right_token_id=token_tuple[index].id,
                cue_relation=edge.cue_relation,
                line_relation=edge.line_relation,
                semantic_strength=edge.semantic_strength,
                reason_codes=reasons,
                material_uncertainty=material_uncertainty,
            )
        )
    return tuple(typed)


def _display_identity_snapshot() -> DisplayMetricsIdentitySnapshot:
    identity = display_metrics_identity()
    return DisplayMetricsIdentitySnapshot(
        algorithm=identity.algorithm,
        algorithm_version=identity.algorithm_version,
        unicode_version=identity.unicode_version,
        python_implementation=identity.python_implementation,
        python_version=identity.python_version,
        python_cache_tag=identity.python_cache_tag,
        east_asian_ambiguous_columns=identity.east_asian_ambiguous_columns,
        ascii_space_columns=identity.ascii_space_columns,
        ascii_space_reading_units=identity.ascii_space_reading_units,
        shaping_backend=identity.shaping_backend,
        runtime_identity_hash=identity.content_hash,
    )


def boundary_rule_set_hash() -> str:
    return hash_object(
        {
            "rule_set_id": RULE_SET_ID,
            "rule_set_version": RULE_SET_VERSION,
            "code_hash": hash_file(Path(__file__)),
            "closed_class": tuple(sorted(_CLOSED_CLASS)),
            "negations": tuple(sorted(_NEGATIONS)),
            "connectors": tuple(sorted(_CONNECTORS)),
            "fillers": tuple(sorted(_FILLERS)),
            "long_speaker_run_min_tokens": _LONG_SPEAKER_RUN_MIN_TOKENS,
            "max_bounded_structure_scalars": _MAX_BOUNDED_STRUCTURE_SCALARS,
        }
    )


def build_boundary_constraint_receipt(
    transcript: CanonicalTranscript,
    units: Sequence[SemanticUnit],
    profile: ProjectionProfile,
    *,
    protected_ranges: Sequence[ProtectedTokenRange],
    semantic_adapter_identity_hash: str,
) -> BoundaryConstraintReceipt:
    """Seal all deterministic/model constraints into one replayable receipt."""

    protected = tuple(protected_ranges)
    edges = assess_boundary_edges(
        transcript.tokens,
        units,
        profile,
        protected_ranges=protected,
    )
    reference_ids = tuple(
        sorted({evidence_id for item in protected for evidence_id in item.reference_evidence_ids})
    )
    payload = {
        "schema_version": 2,
        "episode_id": transcript.episode_id,
        "generation_id": transcript.generation_id,
        "canonical_content_hash": transcript.content_hash,
        "token_ids": tuple(token.id for token in transcript.tokens),
        "policy_hash": transcript.policy_hash,
        "profile_id": profile.id,
        "profile_version": profile.profile_version,
        "profile_hash": hash_object(profile),
        "rule_set_id": RULE_SET_ID,
        "rule_set_version": RULE_SET_VERSION,
        "rule_set_hash": boundary_rule_set_hash(),
        "display_metrics": _display_identity_snapshot(),
        "protected_ranges": protected,
        "protected_range_set_hash": hash_object(protected),
        "reference_evidence_ids": reference_ids,
        "reference_provenance_hash": hash_object(
            {
                "reference_evidence_ids": reference_ids,
                "protected_ranges": protected,
            }
        ),
        "semantic_units_hash": hash_object(tuple(units)),
        "semantic_adapter_identity_hash": semantic_adapter_identity_hash,
        "prosody_status": "unavailable",
        "prosody_artifact_hash": None,
        "edges": edges,
    }
    return BoundaryConstraintReceipt(**payload, content_hash=hash_object(payload))


def assert_boundary_constraint_receipt(
    receipt: BoundaryConstraintReceipt,
    transcript: CanonicalTranscript,
    units: Sequence[SemanticUnit],
    profile: ProjectionProfile,
    *,
    protected_ranges: Sequence[ProtectedTokenRange],
    semantic_adapter_identity_hash: str,
) -> BoundaryConstraintReceipt:
    """Rebuild the receipt; any policy/runtime/reference drift fails closed."""

    expected = build_boundary_constraint_receipt(
        transcript,
        units,
        profile,
        protected_ranges=protected_ranges,
        semantic_adapter_identity_hash=semantic_adapter_identity_hash,
    )
    if receipt != expected:
        raise BoundaryConstraintError(
            "Boundary Constraint Receipt is not reproducible from current truth"
        )
    return expected


def classify_protected_kind(value: str) -> ProtectedSemanticKind:
    """Conservative kind inference for untyped policy vocabulary."""

    stripped = value.strip()
    if re.fullmatch(r"(?:https?://|www\.).+", stripped, re.IGNORECASE):
        return "url"
    if stripped.startswith("《") and stripped.endswith("》"):
        return "book_title"
    if re.search(r"[A-Za-z]", stripped):
        return "code_switch"
    return "term"


def sha256_text(value: str) -> str:
    """Public helper for callers constructing typed metadata receipts."""

    return sha256_bytes(value.encode("utf-8"))


__all__ = [
    "BoundaryConstraintError",
    "ProtectedTermMetadata",
    "RULE_SET_ID",
    "RULE_SET_VERSION",
    "assess_boundary_edges",
    "assert_boundary_constraint_receipt",
    "boundary_rule_set_hash",
    "build_boundary_constraint_receipt",
    "classify_protected_kind",
    "derive_protected_token_ranges",
    "sha256_text",
    "validate_semantic_non_degeneracy",
]
