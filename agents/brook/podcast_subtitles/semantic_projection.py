"""Global, text-preserving projection of canonical tokens into subtitle cues.

The optimizer considers the complete token sequence.  It may cut only at legal
token boundaries, never through a hard SemanticUnit, and it fails closed when
speaker, timing, reading-speed, or line-width constraints have no joint solution.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from shared.schemas.podcast_subtitles_v2 import (
    BoundaryConstraintEdge,
    BoundaryConstraintReceipt,
    CanonicalSpan,
    CanonicalToken,
    DisplayCue,
    ProjectionProfile,
    SemanticUnit,
    SubtitleProjection,
    canonical_content_hash,
)

from .display_metrics import display_columns, display_metrics_identity, reading_units
from .errors import NeedsAlignmentError, ProjectionUnsatisfiableError
from .hashing import hash_object


@dataclass(frozen=True)
class ProjectionResult:
    """A validated typed projection plus deterministic render metadata."""

    projection: SubtitleProjection
    total_cost: float
    projection_hash: str
    boundary_constraint_receipt: BoundaryConstraintReceipt | None = None
    selected_boundary_reasons: tuple[dict[str, Any], ...] = ()
    display_metrics_identity_hash: str = ""

    @property
    def cues(self) -> tuple[DisplayCue, ...]:
        return self.projection.cues

    @property
    def token_ids(self) -> tuple[str, ...]:
        return tuple(token_id for cue in self.cues for token_id in cue.token_ids)


@dataclass(frozen=True)
class _Candidate:
    cost: float
    # Diagnostic channels only.  The optimizer minimises ``cost``; it never
    # compares these components lexicographically.
    score: tuple[float, float, float, float, float]
    lines: tuple[str, ...]
    preferred_boundary_strength: float


@dataclass(frozen=True)
class _State:
    cost: float
    score: tuple[float, float, float, float, float]
    cue_count: int
    previous: int
    lines: tuple[str, ...]
    preferred_boundary_strength: float


@dataclass(frozen=True)
class _ProjectionIndex:
    """O(1) monotone bounds used by the global edge generator."""

    display_column_prefix: tuple[float, ...]
    reading_unit_prefix: tuple[float, ...]
    speaker_run_start: tuple[int, ...]
    speech_start_ms: tuple[int, ...]
    speech_end_ms: tuple[int, ...]
    legal_cue_boundaries: frozenset[int]

    def display_columns(self, start: int, end: int) -> float:
        return self.display_column_prefix[end] - self.display_column_prefix[start]

    def reading_units(self, start: int, end: int) -> float:
        return self.reading_unit_prefix[end] - self.reading_unit_prefix[start]


def _build_projection_index(
    tokens: Sequence[CanonicalToken],
    spans: Sequence[CanonicalSpan] = (),
) -> _ProjectionIndex:
    display_column_prefix = [0.0]
    reading_unit_prefix = [0.0]
    speaker_run_start: list[int] = []
    current_run_start = 0
    speech_start_ms = [0] * len(tokens)
    speech_end_ms = [0] * len(tokens)
    legal_cue_boundaries = {0, len(tokens)}
    positions = {token.id: index for index, token in enumerate(tokens)}
    if spans:
        flattened: list[str] = []
        previous_span_end: int | None = None
        for span in spans:
            try:
                indices = [positions[token_id] for token_id in span.token_ids]
            except KeyError as exc:
                raise ValueError(f"CanonicalSpan references unknown token {exc.args[0]!r}") from exc
            if indices != list(range(indices[0], indices[-1] + 1)):
                raise ValueError(f"CanonicalSpan {span.id!r} must be ordered and contiguous")
            if previous_span_end is not None and span.start_ms < previous_span_end:
                raise ValueError("CanonicalSpans must be monotonic and non-overlapping")
            previous_span_end = span.end_ms
            flattened.extend(span.token_ids)
            legal_cue_boundaries.update({indices[0], indices[-1] + 1})
            if span.alignment == "coarse":
                if any(tokens[index].timing_basis != "coarse_span" for index in indices):
                    raise ValueError("coarse CanonicalSpan contains an exact-timed token")
                for index in indices:
                    speech_start_ms[index] = span.start_ms
                    speech_end_ms[index] = span.end_ms
            else:
                if any(tokens[index].timing_basis == "coarse_span" for index in indices):
                    raise ValueError("lexeme_exact CanonicalSpan contains a coarse token")
                legal_cue_boundaries.update(range(indices[0] + 1, indices[-1] + 1))
                previous_token_end: int | None = None
                for index in indices:
                    token = tokens[index]
                    if token.start_ms is None or token.end_ms is None:
                        raise ValueError("lexeme_exact CanonicalSpan contains an untimed token")
                    if previous_token_end is not None and token.start_ms < previous_token_end:
                        raise ValueError("lexeme_exact CanonicalSpan token timing overlaps")
                    previous_token_end = token.end_ms
                    speech_start_ms[index] = token.start_ms
                    speech_end_ms[index] = token.end_ms
                first = tokens[indices[0]]
                last = tokens[indices[-1]]
                if first.start_ms != span.start_ms or last.end_ms != span.end_ms:
                    raise ValueError(
                        "lexeme_exact CanonicalSpan outer timing differs from its tokens"
                    )
        if flattened != [token.id for token in tokens]:
            raise ValueError("CanonicalSpans must exactly partition canonical tokens")
    else:
        if any(token.start_ms is None or token.end_ms is None for token in tokens):
            raise ValueError("coarse canonical lexemes require explicit CanonicalSpans")
        legal_cue_boundaries.update(range(1, len(tokens)))
        for index, token in enumerate(tokens):
            assert token.start_ms is not None and token.end_ms is not None
            speech_start_ms[index] = token.start_ms
            speech_end_ms[index] = token.end_ms

    for index, token in enumerate(tokens):
        display_column_prefix.append(display_column_prefix[-1] + display_columns(token.text))
        reading_unit_prefix.append(reading_unit_prefix[-1] + reading_units(token.text))
        if index and tokens[index - 1].speaker != token.speaker:
            current_run_start = index
        speaker_run_start.append(current_run_start)
    return _ProjectionIndex(
        tuple(display_column_prefix),
        tuple(reading_unit_prefix),
        tuple(speaker_run_start),
        tuple(speech_start_ms),
        tuple(speech_end_ms),
        frozenset(legal_cue_boundaries),
    )


def _validate_semantic_units(
    tokens: Sequence[CanonicalToken],
    units: Sequence[SemanticUnit],
) -> tuple[set[int], set[int], dict[int, float], dict[int, float]]:
    """Return cue/line hard cuts, soft crossing penalties, and end strengths."""

    positions = {token.id: index for index, token in enumerate(tokens)}
    forbidden_cue: set[int] = set()
    forbidden_line: set[int] = set()
    crossing_strength: dict[int, float] = {}
    ending_strength: dict[int, float] = {}
    effects_by_range: dict[tuple[str, ...], tuple[float, bool, bool, str | None, str | None]] = {}
    for unit in units:
        try:
            indices = [positions[token_id] for token_id in unit.token_ids]
        except KeyError as exc:
            raise ValueError(f"SemanticUnit references unknown token {exc.args[0]!r}") from exc
        if indices != list(range(indices[0], indices[-1] + 1)):
            raise ValueError(f"SemanticUnit {unit.id!r} must reference contiguous ordered tokens")
        effect = (
            unit.strength,
            unit.forbid_cue_breaks,
            unit.forbid_line_breaks,
            unit.cue_boundary_relation,
            unit.line_boundary_relation,
        )
        existing_effect = effects_by_range.get(unit.token_ids)
        if existing_effect is not None:
            if existing_effect != effect:
                raise ValueError(f"SemanticUnit range {unit.token_ids!r} has conflicting effects")
            # Adapter packet overlap or a custom Adapter may repeat an
            # equivalent range under a different ID/kind.  Its projection
            # effect is set-like, never multiplicative.
            continue
        effects_by_range[unit.token_ids] = effect
        start = indices[0]
        end = indices[-1] + 1
        internal = range(start + 1, end)
        if unit.kind == "boundary_pair":
            cut = start + 1
            if unit.cue_boundary_relation == "forbidden":
                forbidden_cue.add(cut)
            if unit.line_boundary_relation == "forbidden":
                forbidden_line.add(cut)
            if "discouraged" in {
                unit.cue_boundary_relation,
                unit.line_boundary_relation,
            }:
                crossing_strength[cut] = max(crossing_strength.get(cut, 0.0), unit.strength)
            if (
                unit.line_boundary_relation == "preferred"
                and unit.cue_boundary_relation != "preferred"
            ):
                ending_strength[cut] = max(ending_strength.get(cut, 0.0), unit.strength)
            continue
        if unit.forbid_cue_breaks:
            forbidden_cue.update(internal)
        if unit.forbid_line_breaks:
            forbidden_line.update(internal)
        if not unit.forbid_cue_breaks and not unit.forbid_line_breaks:
            for cut in range(start + 1, end):
                # A boundary's cohesion is a bounded confidence signal, not a
                # vote count.  Max preserves nested units at their unique
                # boundaries while preventing repeated/crossing ranges from
                # increasing the penalty without limit.
                crossing_strength[cut] = max(crossing_strength.get(cut, 0.0), unit.strength)
        ending_strength[end] = max(ending_strength.get(end, 0.0), unit.strength)
    for cut in range(1, len(tokens)):
        if tokens[cut - 1].speaker != tokens[cut].speaker:
            if cut in forbidden_cue:
                raise ProjectionUnsatisfiableError(
                    "a hard SemanticUnit crosses a mandatory speaker boundary"
                )
    return forbidden_cue, forbidden_line, crossing_strength, ending_strength


def _constraints_from_boundary_edges(
    tokens: Sequence[CanonicalToken],
    edges: Sequence[BoundaryConstraintEdge],
) -> tuple[
    set[int],
    set[int],
    dict[int, float],
    dict[int, float],
    dict[int, float],
]:
    """Rebuild optimizer channels from the complete typed edge relation."""

    if len(edges) != max(0, len(tokens) - 1):
        raise ProjectionUnsatisfiableError(
            "Boundary Constraint Receipt does not cover every adjacent token edge"
        )
    forbidden_cue: set[int] = set()
    forbidden_line: set[int] = set()
    crossing_strength: dict[int, float] = {}
    ending_strength: dict[int, float] = {}
    verified_pause_strength: dict[int, float] = {}
    for index, edge in enumerate(edges, start=1):
        if (
            edge.edge_index != index
            or edge.left_token_id != tokens[index - 1].id
            or edge.right_token_id != tokens[index].id
        ):
            raise ProjectionUnsatisfiableError(
                "Boundary Constraint edge order drifted from Canonical truth"
            )
        if edge.material_uncertainty:
            raise ProjectionUnsatisfiableError(
                f"materially uncertain boundary edge blocks projection: {index}"
            )
        if edge.cue_relation == "forbidden":
            forbidden_cue.add(index)
        if edge.line_relation == "forbidden":
            forbidden_line.add(index)
        if edge.cue_relation == "discouraged" or edge.line_relation == "discouraged":
            crossing_strength[index] = max(edge.semantic_strength, 0.5)
        if edge.line_relation == "preferred" and edge.cue_relation != "preferred":
            ending_strength[index] = max(edge.semantic_strength, 0.5)
        if edge.verified_pause_strength is not None:
            verified_pause_strength[index] = edge.verified_pause_strength
        if edge.cue_relation == "mandatory":
            if tokens[index - 1].speaker == tokens[index].speaker:
                raise ProjectionUnsatisfiableError(
                    "non-speaker mandatory cue boundary lacks a typed scheduling contract"
                )
        elif tokens[index - 1].speaker != tokens[index].speaker:
            raise ProjectionUnsatisfiableError(
                "speaker change is not represented as a mandatory boundary"
            )
    return (
        forbidden_cue,
        forbidden_line,
        crossing_strength,
        ending_strength,
        verified_pause_strength,
    )


def _wrap_lines(
    tokens: Sequence[CanonicalToken],
    *,
    start: int,
    end: int,
    forbidden: set[int],
    crossing_strength: dict[int, float],
    ending_strength: dict[int, float],
    profile: ProjectionProfile,
    index: _ProjectionIndex,
) -> tuple[tuple[str, ...], float, float] | None:
    """Find the exact minimum-cost one/two-line wrap in O(cue length).

    ``ProjectionProfile.max_lines`` is a closed schema value in ``[1, 2]``.
    Evaluating every legal two-line seam is therefore equivalent to the old
    nested line DP, while prefix character counts remove repeated substring
    scans.  Equal-cost two-line wraps retain the former smallest-cut tie break.
    """

    total_width = index.display_columns(start, end)
    options: list[tuple[float, float, int, int | None]] = []
    if total_width <= profile.hard_line_display_columns:
        # With a one-line profile there is no physical wrap decision to score.
        # Width remains a hard feasibility constraint above, while cue density
        # is scored independently by ``_cue_candidate``.  Reusing line fullness
        # here would count the same display preference twice and could create a
        # cue boundary solely to make two shorter lines look closer to target.
        one_line_cost = (
            0.0
            if profile.max_lines == 1
            else (
                (total_width - profile.target_line_display_columns)
                / profile.target_line_display_columns
            )
            ** 2
        )
        options.append((0.0, one_line_cost, 1, None))
    # Do not create a second physical line for text that already fits below
    # the profile's preferred width.  A semantic-end reward is a tie-breaker
    # among necessary wraps, not permission to turn a short phrase into two
    # visually staccato lines.
    if profile.max_lines == 2 and total_width > profile.target_line_display_columns:
        best_two: tuple[float, float, int] | None = None
        for cut in range(start + 1, end):
            if cut in forbidden:
                continue
            left_width = index.display_columns(start, cut)
            right_width = total_width - left_width
            if (
                left_width > profile.hard_line_display_columns
                or right_width > profile.hard_line_display_columns
            ):
                continue
            visual_cost = (
                (left_width - profile.target_line_display_columns)
                / profile.target_line_display_columns
            ) ** 2 + (
                (right_width - profile.target_line_display_columns)
                / profile.target_line_display_columns
            ) ** 2
            # A newline is a display boundary just like a cue boundary.  Soft
            # SemanticUnits therefore influence both with the same cohesion
            # policy: discourage cuts through a unit and prefer its natural end.
            semantic_cost = profile.semantic_break_penalty * crossing_strength.get(cut, 0.0)
            semantic_cost -= min(
                profile.semantic_break_penalty * 0.25 * ending_strength.get(cut, 0.0),
                profile.second_line_penalty,
            )
            candidate = (semantic_cost, visual_cost, cut)
            if best_two is None or candidate < best_two:
                best_two = candidate
        if best_two is not None:
            options.append(
                (
                    best_two[0],
                    best_two[1] + profile.second_line_penalty,
                    2,
                    best_two[2],
                )
            )
    if not options:
        return None
    semantic_cost, visual_cost, _, cut = min(
        options, key=lambda option: (option[0], option[1], option[2], option[3] or -1)
    )
    if cut is None:
        lines = ("".join(token.text for token in tokens[start:end]),)
    else:
        lines = (
            "".join(token.text for token in tokens[start:cut]),
            "".join(token.text for token in tokens[cut:end]),
        )
    return lines, semantic_cost, visual_cost


def _cue_candidate(
    tokens: Sequence[CanonicalToken],
    *,
    start: int,
    end: int,
    forbidden: set[int],
    forbidden_line: set[int] | None = None,
    crossing_strength: dict[int, float],
    ending_strength: dict[int, float],
    profile: ProjectionProfile,
    audio_start_ms: int,
    audio_end_ms: int,
    index: _ProjectionIndex,
    verified_pause_strength: Mapping[int, float] | None = None,
    preferred_cue_strength: Mapping[int, float] | None = None,
) -> _Candidate | None:
    if start >= end or start < index.speaker_run_start[end - 1]:
        return None
    cue_reading_units = index.reading_units(start, end)
    if start not in index.legal_cue_boundaries or end not in index.legal_cue_boundaries:
        return None
    speech_duration_ms = index.speech_end_ms[end - 1] - index.speech_start_ms[start]
    if speech_duration_ms > profile.max_cue_duration_ms:
        return None
    lower_seam = (
        audio_start_ms
        if start == 0
        else (index.speech_end_ms[start - 1] + index.speech_start_ms[start]) // 2
    )
    upper_seam = (
        audio_end_ms
        if end == len(tokens)
        else (index.speech_end_ms[end - 1] + index.speech_start_ms[end]) // 2
    )
    reading_duration_ms = math.ceil(cue_reading_units / profile.max_reading_units_per_second * 1000)
    display_duration_ms = max(
        profile.min_cue_duration_ms,
        speech_duration_ms,
        reading_duration_ms,
    )
    if display_duration_ms > profile.max_cue_duration_ms:
        return None
    if upper_seam - lower_seam < display_duration_ms:
        return None
    units_per_second = cue_reading_units / (display_duration_ms / 1000)
    if units_per_second > profile.max_reading_units_per_second:
        return None
    wrapped = _wrap_lines(
        tokens,
        start=start,
        end=end,
        forbidden=forbidden if forbidden_line is None else forbidden_line,
        crossing_strength=crossing_strength,
        ending_strength=ending_strength,
        profile=profile,
        index=index,
    )
    if wrapped is None:
        return None
    lines, line_semantic_cost, line_visual_cost = wrapped

    # Line width and cue density are different controls.  The legacy fallback
    # preserves profiles that do not opt in; horizontal Chinese uses a wider
    # cue target so one natural two-line subtitle is not scored as excessively
    # long merely because it contains more than one line's target.
    target_columns = (
        profile.target_cue_display_columns
        if profile.target_cue_display_columns is not None
        else profile.target_line_display_columns
    )
    cue_columns = index.display_columns(start, end)
    lower_target = (
        profile.minimum_preferred_cue_display_columns
        if profile.minimum_preferred_cue_display_columns is not None
        else target_columns
    )
    upper_target = (
        profile.maximum_preferred_cue_display_columns
        if profile.maximum_preferred_cue_display_columns is not None
        else target_columns
    )
    shortage = max(0.0, lower_target - cue_columns) / max(1.0, lower_target)
    excess = max(0.0, cue_columns - upper_target) / max(1.0, upper_target)
    center_delta = abs(cue_columns - target_columns) / max(1.0, target_columns)
    visual_cost = (
        line_visual_cost
        + profile.short_cue_penalty * shortage * shortage
        + profile.long_cue_penalty * excess * excess
        + profile.cue_density_center_tiebreak_penalty * center_delta * center_delta
    )
    reading_target_ms = round(
        cue_reading_units / (profile.max_reading_units_per_second * 0.65) * 1000
    )
    reading_target_ms = min(
        profile.max_cue_duration_ms,
        max(profile.min_cue_duration_ms, reading_target_ms),
    )
    duration_delta = (display_duration_ms - reading_target_ms) / max(1, reading_target_ms)
    duration_cost = 0.25 * duration_delta * duration_delta
    semantic_cost = line_semantic_cost
    pause_cost = 0.0
    fragmentation_cost = 0.0
    if end < len(tokens) and tokens[end - 1].speaker == tokens[end].speaker:
        semantic_cost += profile.semantic_break_penalty * crossing_strength.get(end, 0.0)
        # A model-proposed ending may waive the generic fragmentation charge,
        # but it may not turn an otherwise unnecessary cut into a net reward.
        # This keeps dense/adversarial ``preferred`` output from manufacturing
        # singleton cues while still letting a real ending win a close choice.
        ending_reward = min(
            profile.semantic_break_penalty * 0.25 * ending_strength.get(end, 0.0),
            profile.cue_boundary_penalty,
        )
        semantic_cost -= ending_reward
        # ASR token gaps prove alignment only.  A pause reward is legal solely
        # when a Boundary Constraint Receipt carries a per-edge value derived
        # from its separately replayable acoustic-prosody artifact.
        pause_cost -= profile.verified_pause_reward * (
            (verified_pause_strength or {}).get(end, 0.0)
        )
        fragmentation_cost = profile.cue_boundary_penalty
    score = (
        semantic_cost,
        pause_cost,
        duration_cost,
        visual_cost,
        fragmentation_cost,
    )
    # A preferred cue seam is deliberately excluded from scalar cost.  It can
    # select an edge only after hard feasibility, ordinary cost, and cue count
    # are equal; therefore dense model preferences can never manufacture more
    # cues than the same projection without those preferences.
    preferred_boundary_strength = (
        0.0 if end == len(tokens) else (preferred_cue_strength or {}).get(end, 0.0)
    )
    return _Candidate(
        cost=sum(score),
        score=score,
        lines=lines,
        preferred_boundary_strength=preferred_boundary_strength,
    )


def _allocate_cue_times(
    slices: Sequence[tuple[int, int, tuple[str, ...]]],
    tokens: Sequence[CanonicalToken],
    profile: ProjectionProfile,
    *,
    audio_start_ms: int,
    audio_end_ms: int,
    index: _ProjectionIndex,
) -> tuple[tuple[int, int], ...]:
    """Allocate display time globally while preserving every speech interval.

    Each cue initially owns its exact canonical speech interval. Short cues may
    consume adjacent silence; every inter-cue gap has one deterministic midpoint
    seam, which prevents overlap and ensures neighbouring cues cannot both claim
    the same silence.
    """

    if audio_start_ms < 0 or audio_end_ms <= audio_start_ms:
        raise ValueError("audio bounds must be a positive ordered interval")
    speech_intervals = [
        (index.speech_start_ms[start], index.speech_end_ms[end - 1]) for start, end, _ in slices
    ]
    if speech_intervals[0][0] < audio_start_ms or speech_intervals[-1][1] > audio_end_ms:
        raise ProjectionUnsatisfiableError("canonical speech timing falls outside audio bounds")
    seams = [audio_start_ms]
    for left, right in zip(speech_intervals, speech_intervals[1:]):
        if right[0] < left[1]:
            raise ProjectionUnsatisfiableError("canonical cue speech intervals overlap")
        seams.append((left[1] + right[0]) // 2)
    seams.append(audio_end_ms)

    allocated: list[tuple[int, int]] = []
    for index, (speech_start, speech_end) in enumerate(speech_intervals):
        lower = seams[index]
        upper = seams[index + 1]
        if lower > speech_start or upper < speech_end:
            raise ProjectionUnsatisfiableError("global timing seams exclude canonical speech")
        start, end, _ = slices[index]
        cue_reading_units = reading_units("".join(token.text for token in tokens[start:end]))
        reading_duration_ms = math.ceil(
            cue_reading_units / profile.max_reading_units_per_second * 1000
        )
        desired = max(
            profile.min_cue_duration_ms,
            speech_end - speech_start,
            reading_duration_ms,
        )
        desired = min(desired, profile.max_cue_duration_ms)
        if upper - lower < desired:
            raise ProjectionUnsatisfiableError(
                "available audio time cannot satisfy minimum cue duration without overlap"
            )
        missing = desired - (speech_end - speech_start)
        left = min(missing // 2, speech_start - lower)
        right = min(missing - left, upper - speech_end)
        remaining = missing - left - right
        if remaining:
            extra_left = min(remaining, speech_start - lower - left)
            left += extra_left
            remaining -= extra_left
        if remaining:
            extra_right = min(remaining, upper - speech_end - right)
            right += extra_right
            remaining -= extra_right
        if remaining:
            raise ProjectionUnsatisfiableError("minimum cue duration allocation is impossible")
        allocated.append((speech_start - left, speech_end + right))

    # Natural silence remains empty.  Cues consume only the neighbouring time
    # required by minimum duration or CPS; extending them merely to fill a long
    # pause would misrepresent when speech is present.
    return tuple(allocated)


def project_semantic_units(
    canonical_tokens: Sequence[CanonicalToken],
    semantic_units: Sequence[SemanticUnit],
    profile: ProjectionProfile,
    *,
    episode_id: str,
    generation_id: str,
    audio_start_ms: int = 0,
    audio_end_ms: int | None = None,
    canonical_spans: Sequence[CanonicalSpan] = (),
    boundary_constraints: BoundaryConstraintReceipt | None = None,
    mandatory_cue_boundaries: Sequence[int] = (),
    allowed_cue_boundaries: Sequence[int] | None = None,
    authoritative_cues: Sequence[tuple[int, int, int, int]] = (),
) -> ProjectionResult:
    """Return the globally minimum-cost legal display projection.

    ``mandatory_cue_boundaries`` contains token-edge indices in
    ``1..len(tokens)-1``.  A cue may start or end at one of these edges but may
    never cross it.  The default is empty so existing callers retain the same
    optimisation behaviour.
    """

    tokens = tuple(canonical_tokens)
    if not tokens:
        raise ValueError("cannot project an empty canonical token stream")
    if len({token.id for token in tokens}) != len(tokens):
        raise ValueError("canonical token IDs must be unique")
    index = _build_projection_index(tokens, tuple(canonical_spans))
    mandatory_boundaries: set[int] = set()
    for edge in mandatory_cue_boundaries:
        if isinstance(edge, bool) or not isinstance(edge, int) or not 1 <= edge < len(tokens):
            raise ValueError(
                f"mandatory cue boundary must be an integer token edge in 1..{len(tokens) - 1}"
            )
        mandatory_boundaries.add(edge)
    allowed_boundaries: set[int] | None = None
    if allowed_cue_boundaries is not None:
        allowed_boundaries = set()
        for edge in allowed_cue_boundaries:
            if isinstance(edge, bool) or not isinstance(edge, int) or not 1 <= edge < len(tokens):
                raise ValueError("allowed cue boundaries must be internal integer token edges")
            allowed_boundaries.add(edge)
        if not mandatory_boundaries.issubset(allowed_boundaries):
            raise ValueError("mandatory cue boundaries must be included in allowed boundaries")
    illegal_alignment_edges = mandatory_boundaries - index.legal_cue_boundaries
    if illegal_alignment_edges:
        raise ProjectionUnsatisfiableError(
            "mandatory cue boundary is not legal under canonical alignment: "
            + ", ".join(str(edge) for edge in sorted(illegal_alignment_edges))
        )
    if audio_end_ms is None:
        audio_end_ms = index.speech_end_ms[-1]
    if boundary_constraints is None:
        forbidden, forbidden_line, crossing_strength, ending_strength = _validate_semantic_units(
            tokens, semantic_units
        )
        verified_pause_strength: dict[int, float] = {}
        positions = {token.id: index for index, token in enumerate(tokens)}
        preferred_cue_strength = {
            positions[unit.token_ids[0]] + 1: unit.strength
            for unit in semantic_units
            if unit.kind == "boundary_pair" and unit.cue_boundary_relation == "preferred"
        }
    else:
        if (
            boundary_constraints.episode_id != episode_id
            or boundary_constraints.generation_id != generation_id
            or boundary_constraints.canonical_content_hash != canonical_content_hash(tokens)
            or boundary_constraints.profile_id != profile.id
            or boundary_constraints.profile_version != profile.profile_version
            or boundary_constraints.profile_hash != hash_object(profile)
            or boundary_constraints.display_metrics.runtime_identity_hash
            != display_metrics_identity().content_hash
        ):
            raise ProjectionUnsatisfiableError(
                "Boundary Constraint Receipt crossed generation/profile/display identity"
            )
        (
            forbidden,
            forbidden_line,
            crossing_strength,
            ending_strength,
            verified_pause_strength,
        ) = _constraints_from_boundary_edges(tokens, boundary_constraints.edges)
        preferred_cue_strength = {
            edge.edge_index: edge.semantic_strength
            for edge in boundary_constraints.edges
            if edge.cue_relation == "preferred"
        }
    token_by_id = {token.id: token for token in tokens}
    for span in canonical_spans:
        if span.alignment != "coarse":
            continue
        span_text = "".join(token_by_id[token_id].text for token_id in span.token_ids)
        span_columns = display_columns(span_text)
        span_reading_units = reading_units(span_text)
        minimum_reading_ms = math.ceil(
            span_reading_units / profile.max_reading_units_per_second * 1000
        )
        speakers = {token_by_id[token_id].speaker for token_id in span.token_ids}
        if (
            span_columns > profile.max_lines * profile.hard_line_display_columns
            or span.end_ms - span.start_ms > profile.max_cue_duration_ms
            or minimum_reading_ms > profile.max_cue_duration_ms
            or len(speakers) != 1
        ):
            raise NeedsAlignmentError(
                f"NeedsAlignment: coarse CanonicalSpan {span.id!r} exceeds one-cue "
                "width, duration, CPS, or speaker-purity capacity"
            )
    count = len(tokens)
    latest_mandatory_before = [0] * (count + 1)
    latest = 0
    for end in range(1, count + 1):
        if end - 1 in mandatory_boundaries:
            latest = end - 1
        latest_mandatory_before[end] = latest
    best: list[_State | None] = [None] * (count + 1)
    best[0] = _State(
        cost=0.0,
        score=(0.0, 0.0, 0.0, 0.0, 0.0),
        cue_count=0,
        previous=-1,
        lines=(),
        preferred_boundary_strength=0.0,
    )

    for end in range(1, count + 1):
        if end not in index.legal_cue_boundaries:
            continue
        if allowed_boundaries is not None and end < count and end not in allowed_boundaries:
            continue
        if end < count and end in forbidden:
            continue
        selected: _State | None = None
        speaker_floor = index.speaker_run_start[end - 1]
        candidate_floor = max(speaker_floor, latest_mandatory_before[end])
        maximum_display_columns = profile.max_lines * profile.hard_line_display_columns
        for start in range(end - 1, -1, -1):
            # These constraints are monotone as ``start`` moves left.  Once
            # one fails, every older edge fails too, so no globally legal
            # candidate is pruned by stopping the scan.
            if start < candidate_floor:
                break
            if start not in index.legal_cue_boundaries:
                continue
            if allowed_boundaries is not None and start > 0 and start not in allowed_boundaries:
                continue
            if index.display_columns(start, end) > maximum_display_columns:
                break
            if (
                index.speech_end_ms[end - 1] - index.speech_start_ms[start]
                > profile.max_cue_duration_ms
            ):
                break
            if start and start in forbidden:
                continue
            prior = best[start]
            if prior is None:
                continue
            candidate = _cue_candidate(
                tokens,
                start=start,
                end=end,
                forbidden=forbidden,
                forbidden_line=forbidden_line,
                crossing_strength=crossing_strength,
                ending_strength=ending_strength,
                profile=profile,
                audio_start_ms=audio_start_ms,
                audio_end_ms=audio_end_ms,
                index=index,
                verified_pause_strength=verified_pause_strength,
                preferred_cue_strength=preferred_cue_strength,
            )
            if candidate is None:
                continue
            state = _State(
                cost=prior.cost + candidate.cost,
                score=tuple(
                    left + right for left, right in zip(prior.score, candidate.score, strict=True)
                ),
                cue_count=prior.cue_count + 1,
                previous=start,
                lines=candidate.lines,
                preferred_boundary_strength=(
                    prior.preferred_boundary_strength + candidate.preferred_boundary_strength
                ),
            )
            if selected is None or (
                state.cost,
                state.cue_count,
                -state.preferred_boundary_strength,
                state.previous,
                state.score,
            ) < (
                selected.cost,
                selected.cue_count,
                -selected.preferred_boundary_strength,
                selected.previous,
                selected.score,
            ):
                selected = state
        best[end] = selected

    terminal = best[count]
    if terminal is None:
        raise ProjectionUnsatisfiableError(
            "no global partition satisfies semantic, speaker, timing, CPS, and width constraints"
        )
    slices: list[tuple[int, int, tuple[str, ...]]] = []
    cursor = count
    while cursor:
        state = best[cursor]
        if state is None:  # pragma: no cover - guarded by successful DP
            raise ProjectionUnsatisfiableError("projection backtracking reached a dead state")
        slices.append((state.previous, cursor, state.lines))
        cursor = state.previous
    slices.reverse()
    if authoritative_cues:
        expected_slices = tuple((item[0], item[1]) for item in authoritative_cues)
        if tuple((start, end) for start, end, _lines in slices) != expected_slices:
            raise ProjectionUnsatisfiableError(
                "projection drifted from authoritative Memo cue partition"
            )
        allocated_times = tuple((item[2], item[3]) for item in authoritative_cues)
        previous_end = audio_start_ms
        for start_ms, end_ms in allocated_times:
            if start_ms < previous_end or end_ms <= start_ms or end_ms > audio_end_ms:
                raise ProjectionUnsatisfiableError(
                    "authoritative Memo cue timing is invalid on normalized clock"
                )
            previous_end = end_ms
    else:
        allocated_times = _allocate_cue_times(
            slices,
            tokens,
            profile,
            audio_start_ms=audio_start_ms,
            audio_end_ms=audio_end_ms,
            index=index,
        )
    cues = tuple(
        DisplayCue(
            id=f"cue-{index:05d}",
            token_ids=tuple(token.id for token in tokens[start:end]),
            start_ms=allocated_times[index - 1][0],
            end_ms=allocated_times[index - 1][1],
            lines=lines,
        )
        for index, (start, end, lines) in enumerate(slices, start=1)
    )
    projection = SubtitleProjection(
        episode_id=episode_id,
        generation_id=generation_id,
        canonical_content_hash=canonical_content_hash(tokens),
        profile_id=profile.id,
        profile_version=profile.profile_version,
        cues=cues,
    )
    projection_hash = hash_object(projection)
    selected_boundaries: list[dict[str, Any]] = []
    receipt_edge_by_index = (
        {edge.edge_index: edge for edge in boundary_constraints.edges}
        if boundary_constraints is not None
        else {}
    )
    for start, end, lines in slices:
        if end < len(tokens):
            edge = receipt_edge_by_index.get(end)
            selected_boundaries.append(
                {
                    "channel": "cue",
                    "edge_index": end,
                    "left_token_id": tokens[end - 1].id,
                    "right_token_id": tokens[end].id,
                    "relation": edge.cue_relation if edge is not None else "legacy",
                    "reason_codes": list(edge.reason_codes) if edge is not None else [],
                }
            )
        scalar_cursor = 0
        for line in lines[:-1]:
            scalar_cursor += len(line)
            accumulated = 0
            for cut in range(start + 1, end):
                accumulated += len(tokens[cut - 1].text)
                if accumulated != scalar_cursor:
                    continue
                edge = receipt_edge_by_index.get(cut)
                selected_boundaries.append(
                    {
                        "channel": "line",
                        "edge_index": cut,
                        "left_token_id": tokens[cut - 1].id,
                        "right_token_id": tokens[cut].id,
                        "relation": edge.line_relation if edge is not None else "legacy",
                        "reason_codes": list(edge.reason_codes) if edge is not None else [],
                    }
                )
                break
    result = ProjectionResult(
        projection=projection,
        total_cost=terminal.cost,
        projection_hash=projection_hash,
        boundary_constraint_receipt=boundary_constraints,
        selected_boundary_reasons=tuple(selected_boundaries),
        display_metrics_identity_hash=display_metrics_identity().content_hash,
    )

    # A successful optimizer result is not publishable until the independent
    # fail-closed verifier checks it again through the public schema.
    from .quality import assert_projection_quality

    assert_projection_quality(
        tokens,
        semantic_units,
        projection,
        profile,
        audio_start_ms=audio_start_ms,
        audio_end_ms=audio_end_ms,
        canonical_spans=canonical_spans,
        boundary_constraints=boundary_constraints,
    )
    return result


def _srt_time(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def render_srt(projection: SubtitleProjection | ProjectionResult) -> str:
    typed = projection.projection if isinstance(projection, ProjectionResult) else projection
    blocks = [
        "\n".join(
            (
                str(index),
                f"{_srt_time(cue.start_ms)} --> {_srt_time(cue.end_ms)}",
                *cue.lines,
            )
        )
        for index, cue in enumerate(typed.cues, start=1)
    ]
    return "\n\n".join(blocks) + "\n"


def projection_sidecar(
    projection: SubtitleProjection | ProjectionResult,
) -> dict[str, Any]:
    typed = projection.projection if isinstance(projection, ProjectionResult) else projection
    boundary_receipt_hash = (
        hash_object(projection.boundary_constraint_receipt)
        if isinstance(projection, ProjectionResult)
        and projection.boundary_constraint_receipt is not None
        else None
    )
    selected_boundary_reasons = (
        list(projection.selected_boundary_reasons)
        if isinstance(projection, ProjectionResult)
        else []
    )
    metrics_identity_hash = (
        projection.display_metrics_identity_hash
        if isinstance(projection, ProjectionResult)
        else display_metrics_identity().content_hash
    )
    return {
        "schema_version": 2,
        "episode_id": typed.episode_id,
        "generation_id": typed.generation_id,
        "canonical_content_hash": typed.canonical_content_hash,
        "profile_id": typed.profile_id,
        "profile_version": typed.profile_version,
        "display_metrics_identity_hash": metrics_identity_hash,
        "boundary_constraint_receipt_hash": boundary_receipt_hash,
        "selected_boundary_reasons": selected_boundary_reasons,
        "cues": [
            {
                "id": cue.id,
                "token_ids": list(cue.token_ids),
                "start_ms": cue.start_ms,
                "end_ms": cue.end_ms,
                "display_columns": display_columns("".join(cue.lines)),
                "reading_units": reading_units("".join(cue.lines)),
                "line_display_columns": [display_columns(line) for line in cue.lines],
            }
            for cue in typed.cues
        ],
    }


def render_sidecar_json(projection: SubtitleProjection | ProjectionResult) -> str:
    return json.dumps(
        projection_sidecar(projection),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def projected_token_ids(projection: SubtitleProjection) -> tuple[str, ...]:
    return tuple(token_id for cue in projection.cues for token_id in cue.token_ids)


def projected_lexemes(
    canonical_tokens: Iterable[CanonicalToken], projection: SubtitleProjection
) -> tuple[str, ...]:
    lexemes = {token.id: token.text for token in canonical_tokens}
    return tuple(lexemes[token_id] for token_id in projected_token_ids(projection))


__all__ = [
    "ProjectionResult",
    "project_semantic_units",
    "projected_lexemes",
    "projected_token_ids",
    "projection_sidecar",
    "render_sidecar_json",
    "render_srt",
]
