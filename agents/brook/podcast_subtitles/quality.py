"""Independent fail-closed quality gates for Subtitle V2 projections."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Iterable

from shared.schemas.podcast_subtitles_v2 import (
    BoundaryConstraintReceipt,
    CanonicalSpan,
    CanonicalToken,
    ProjectionProfile,
    QualityFinding,
    QualityReport,
    SemanticUnit,
    SubtitleProjection,
    canonical_content_hash,
)

from .display_metrics import display_columns, display_metrics_identity, reading_units
from .editorial import inspect_editorial_text
from .errors import QualityGateError
from .hashing import hash_object


def _finding(
    *,
    code: str,
    message: str,
    cue_ids: Iterable[str] = (),
    severity: str = "blocking",
) -> QualityFinding:
    cue_ids_tuple = tuple(cue_ids)
    identity = {"code": code, "message": message, "cue_ids": cue_ids_tuple}
    return QualityFinding(
        id=f"quality-{hash_object(identity)[:16]}",
        severity=severity,
        code=code,
        message=message,
        cue_ids=cue_ids_tuple,
    )


def _line_cut_token_positions(
    cue_token_texts: Sequence[str], lines: Sequence[str]
) -> tuple[set[int], bool]:
    """Map newlines to token edges and report any cut inside a token."""

    token_ends: dict[int, int] = {}
    offset = 0
    for position, text in enumerate(cue_token_texts, start=1):
        offset += len(text)
        token_ends[offset] = position
    line_cuts: set[int] = set()
    cuts_align = True
    offset = 0
    for line in lines[:-1]:
        offset += len(line)
        token_position = token_ends.get(offset)
        if token_position is None:
            cuts_align = False
        else:
            line_cuts.add(token_position)
    return line_cuts, cuts_align


def _canonical_alignment(
    tokens: Sequence[CanonicalToken],
    spans: Sequence[CanonicalSpan],
) -> tuple[tuple[int, ...], tuple[int, ...], frozenset[int]]:
    """Independently derive only Evidence-backed cue boundaries and envelopes."""

    positions = {token.id: index for index, token in enumerate(tokens)}
    starts = [0] * len(tokens)
    ends = [0] * len(tokens)
    legal = {0, len(tokens)}
    if not spans:
        for index, token in enumerate(tokens):
            if token.start_ms is None or token.end_ms is None:
                raise ValueError("coarse canonical lexemes require explicit CanonicalSpans")
            starts[index] = token.start_ms
            ends[index] = token.end_ms
        legal.update(range(1, len(tokens)))
        return tuple(starts), tuple(ends), frozenset(legal)

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
        legal.update({indices[0], indices[-1] + 1})
        if span.alignment == "coarse":
            if any(tokens[index].timing_basis != "coarse_span" for index in indices):
                raise ValueError("coarse CanonicalSpan contains an exact-timed token")
            for index in indices:
                starts[index] = span.start_ms
                ends[index] = span.end_ms
        else:
            if any(tokens[index].timing_basis == "coarse_span" for index in indices):
                raise ValueError("lexeme_exact CanonicalSpan contains a coarse token")
            legal.update(range(indices[0] + 1, indices[-1] + 1))
            previous_token_end: int | None = None
            for index in indices:
                token = tokens[index]
                if token.start_ms is None or token.end_ms is None:
                    raise ValueError("lexeme_exact CanonicalSpan contains an untimed token")
                if previous_token_end is not None and token.start_ms < previous_token_end:
                    raise ValueError("lexeme_exact CanonicalSpan token timing overlaps")
                previous_token_end = token.end_ms
                starts[index] = token.start_ms
                ends[index] = token.end_ms
            first = tokens[indices[0]]
            last = tokens[indices[-1]]
            if first.start_ms != span.start_ms or last.end_ms != span.end_ms:
                raise ValueError("lexeme_exact CanonicalSpan outer timing differs from its tokens")
    if flattened != [token.id for token in tokens]:
        raise ValueError("CanonicalSpans must exactly partition canonical tokens")
    return tuple(starts), tuple(ends), frozenset(legal)


def evaluate_projection(
    canonical_tokens: Sequence[CanonicalToken],
    semantic_units: Sequence[SemanticUnit],
    projection: SubtitleProjection,
    profile: ProjectionProfile,
    *,
    artifact_hashes: Mapping[str, tuple[str, str]] | None = None,
    audio_start_ms: int | None = None,
    audio_end_ms: int | None = None,
    canonical_spans: Sequence[CanonicalSpan] = (),
    boundary_constraints: BoundaryConstraintReceipt | None = None,
) -> QualityReport:
    """Evaluate all release-blocking deterministic projection invariants."""

    if (audio_start_ms is None) != (audio_end_ms is None):
        raise ValueError("audio_start_ms and audio_end_ms must be supplied together")
    if audio_start_ms is not None and (
        audio_start_ms < 0 or audio_end_ms is None or audio_end_ms <= audio_start_ms
    ):
        raise ValueError("audio bounds must be a positive ordered interval")

    tokens = tuple(canonical_tokens)
    canonical_ids = tuple(token.id for token in tokens)
    token_by_id = {token.id: token for token in tokens}
    token_positions = {token.id: index for index, token in enumerate(tokens)}
    speech_starts, speech_ends, legal_cue_boundaries = _canonical_alignment(
        tokens, tuple(canonical_spans)
    )
    projected_ids = tuple(token_id for cue in projection.cues for token_id in cue.token_ids)
    cue_for_token = {token_id: cue.id for cue in projection.cues for token_id in cue.token_ids}
    findings: list[QualityFinding] = []

    if boundary_constraints is not None:
        if (
            boundary_constraints.episode_id != projection.episode_id
            or boundary_constraints.generation_id != projection.generation_id
            or boundary_constraints.canonical_content_hash != canonical_content_hash(tokens)
            or boundary_constraints.token_ids != canonical_ids
            or boundary_constraints.profile_id != profile.id
            or boundary_constraints.profile_version != profile.profile_version
            or boundary_constraints.profile_hash != hash_object(profile)
            or boundary_constraints.display_metrics.runtime_identity_hash
            != display_metrics_identity().content_hash
        ):
            findings.append(
                _finding(
                    code="boundary_constraint_identity_mismatch",
                    message=(
                        "Boundary Constraint Receipt crossed Canonical, profile, or "
                        "Unicode display identity"
                    ),
                )
            )
        if boundary_constraints.prosody_status == "unavailable":
            findings.append(
                _finding(
                    code="prosody_unavailable",
                    message=(
                        "no verified F0/energy/VAD prosody artifact is available; ASR "
                        "timestamp gaps are not treated as audio prosody"
                    ),
                    severity="warning",
                )
            )

    for span in canonical_spans:
        if span.alignment != "coarse":
            continue
        span_text = "".join(token_by_id[token_id].text for token_id in span.token_ids)
        span_columns = display_columns(span_text)
        span_reading_units = reading_units(span_text)
        reading_ms = math.ceil(span_reading_units / profile.max_reading_units_per_second * 1000)
        speakers = {token_by_id[token_id].speaker for token_id in span.token_ids}
        if (
            span_columns > profile.max_lines * profile.hard_line_display_columns
            or span.end_ms - span.start_ms > profile.max_cue_duration_ms
            or reading_ms > profile.max_cue_duration_ms
            or len(speakers) != 1
        ):
            findings.append(
                _finding(
                    code="needs_alignment",
                    message=(
                        f"coarse CanonicalSpan {span.id!r} exceeds one-cue width, "
                        "duration, CPS, or speaker-purity capacity"
                    ),
                )
            )

    if audio_start_ms is not None and audio_end_ms is not None:
        out_of_bounds_tokens = tuple(
            token.id
            for index, token in enumerate(tokens)
            if speech_starts[index] < audio_start_ms or speech_ends[index] > audio_end_ms
        )
        if out_of_bounds_tokens:
            findings.append(
                _finding(
                    code="canonical_timing_out_of_bounds",
                    message=(
                        "canonical token timing falls outside the normalized-audio bounds: "
                        f"{out_of_bounds_tokens!r}"
                    ),
                )
            )

    exact_sequence = projected_ids == canonical_ids
    exact_lexemes = True
    if projection.canonical_content_hash != canonical_content_hash(tokens):
        exact_lexemes = False
    for cue in projection.cues:
        try:
            expected_text = "".join(token_by_id[token_id].text for token_id in cue.token_ids)
        except KeyError:
            exact_lexemes = False
            continue
        if "".join(cue.lines) != expected_text:
            exact_lexemes = False
    if not exact_sequence or not exact_lexemes:
        findings.append(
            _finding(
                code="token_sequence_mismatch",
                message=(
                    "projection must preserve the ordered canonical token IDs and exact lexemes"
                ),
            )
        )

    canonical_text = "".join(token.text for token in tokens)
    scalar_to_token_id = tuple(token.id for token in tokens for _character in token.text)
    emitted_editorial_targets: set[tuple[str, tuple[str, ...]]] = set()
    for editorial in inspect_editorial_text(canonical_text):
        cue_ids = tuple(
            sorted(
                {
                    cue_id
                    for position in editorial.positions
                    if position < len(scalar_to_token_id)
                    for cue_id in (cue_for_token.get(scalar_to_token_id[position]),)
                    if cue_id is not None
                }
            )
        )
        target = (editorial.code, cue_ids)
        if target in emitted_editorial_targets:
            continue
        emitted_editorial_targets.add(target)
        findings.append(
            _finding(
                code=editorial.code,
                message=(
                    "canonical text violates the sealed subtitle editorial policy: "
                    f"{editorial.code}"
                ),
                cue_ids=cue_ids,
            )
        )

    previous_end = -1
    short_count = 0
    cps_failures = 0
    long_intercue_gap_count = 0
    max_observed_intercue_gap_ms = 0
    for cue in projection.cues:
        known_tokens = [
            token_by_id[token_id] for token_id in cue.token_ids if token_id in token_by_id
        ]
        known_positions = [
            token_positions[token_id] for token_id in cue.token_ids if token_id in token_positions
        ]
        if known_positions and (
            known_positions != list(range(known_positions[0], known_positions[-1] + 1))
            or known_positions[0] not in legal_cue_boundaries
            or known_positions[-1] + 1 not in legal_cue_boundaries
        ):
            findings.append(
                _finding(
                    code="needs_alignment",
                    message=(
                        f"cue {cue.id} cuts a coarse CanonicalSpan without an "
                        "Evidence-backed alignment boundary"
                    ),
                    cue_ids=(cue.id,),
                )
            )
        speakers = {token.speaker for token in known_tokens}
        if any(speaker is None or not speaker.strip() for speaker in speakers):
            findings.append(
                _finding(
                    code="speaker_unresolved",
                    message=f"cue {cue.id} contains a canonical token without a speaker",
                    cue_ids=(cue.id,),
                )
            )
        if len(speakers) > 1:
            findings.append(
                _finding(
                    code="speaker_crossing",
                    message=f"cue {cue.id} contains canonical tokens from multiple speakers",
                    cue_ids=(cue.id,),
                )
            )
        duration_ms = cue.end_ms - cue.start_ms
        if (
            audio_start_ms is not None
            and audio_end_ms is not None
            and (cue.start_ms < audio_start_ms or cue.end_ms > audio_end_ms)
        ):
            findings.append(
                _finding(
                    code="cue_timing_out_of_bounds",
                    message=(
                        f"cue {cue.id} falls outside normalized-audio bounds "
                        f"{audio_start_ms}..{audio_end_ms}ms"
                    ),
                    cue_ids=(cue.id,),
                )
            )
        if duration_ms < profile.min_cue_duration_ms:
            short_count += 1
            findings.append(
                _finding(
                    code="cue_too_short",
                    message=(
                        f"cue {cue.id} duration {duration_ms}ms is below "
                        f"{profile.min_cue_duration_ms}ms"
                    ),
                    cue_ids=(cue.id,),
                )
            )
        if duration_ms > profile.max_cue_duration_ms:
            findings.append(
                _finding(
                    code="cue_too_long",
                    message=(
                        f"cue {cue.id} duration {duration_ms}ms exceeds "
                        f"{profile.max_cue_duration_ms}ms"
                    ),
                    cue_ids=(cue.id,),
                )
            )
        cue_reading_units = reading_units("".join(cue.lines))
        reading_rate = cue_reading_units / (duration_ms / 1000) if duration_ms > 0 else float("inf")
        if reading_rate > profile.max_reading_units_per_second:
            cps_failures += 1
            findings.append(
                _finding(
                    code="reading_rate_exceeded",
                    message=(
                        f"cue {cue.id} reads at {reading_rate:.2f} reading units/s, "
                        f"above {profile.max_reading_units_per_second:.2f}"
                    ),
                    cue_ids=(cue.id,),
                )
            )
        if cue.start_ms < previous_end or cue.end_ms <= cue.start_ms:
            findings.append(
                _finding(
                    code="timeline_invalid",
                    message=f"cue {cue.id} is non-monotonic or overlaps its predecessor",
                    cue_ids=(cue.id,),
                )
            )
        elif previous_end >= 0:
            intercue_gap_ms = cue.start_ms - previous_end
            max_observed_intercue_gap_ms = max(max_observed_intercue_gap_ms, intercue_gap_ms)
            if intercue_gap_ms > profile.max_intercue_gap_ms:
                long_intercue_gap_count += 1
            # A long natural pause is not a display-timeline failure.  Missing
            # speech or VAD coverage must be decided by an independent audio
            # coverage gate with access to audio evidence, not by stretching
            # subtitle cues into silence.
        previous_end = cue.end_ms
        if known_tokens:
            first_position = known_positions[0]
            last_position = known_positions[-1]
            if (
                cue.start_ms > speech_starts[first_position]
                or cue.end_ms < speech_ends[last_position]
            ):
                findings.append(
                    _finding(
                        code="timeline_invalid",
                        message=f"cue {cue.id} does not contain its canonical token timing",
                        cue_ids=(cue.id,),
                    )
                )
        if len(cue.lines) > profile.max_lines or any(
            display_columns(line) > profile.hard_line_display_columns for line in cue.lines
        ):
            findings.append(
                _finding(
                    code="profile_width_exceeded",
                    message=f"cue {cue.id} exceeds the profile line-count or hard width",
                    cue_ids=(cue.id,),
                )
            )

    if boundary_constraints is not None:
        cue_cuts: set[int] = set()
        line_cuts: set[int] = set()
        for cue in projection.cues:
            known_positions = [token_positions[token_id] for token_id in cue.token_ids]
            if known_positions and known_positions[-1] + 1 < len(tokens):
                cue_cuts.add(known_positions[-1] + 1)
            cue_texts = [token_by_id[token_id].text for token_id in cue.token_ids]
            relative_line_cuts, cuts_align = _line_cut_token_positions(cue_texts, cue.lines)
            if not cuts_align:
                findings.append(
                    _finding(
                        code="boundary_line_seam_drift",
                        message=f"cue {cue.id} line seam cuts inside a Canonical token",
                        cue_ids=(cue.id,),
                    )
                )
            if known_positions:
                line_cuts.update(known_positions[0] + relative for relative in relative_line_cuts)
        for edge in boundary_constraints.edges:
            if edge.material_uncertainty:
                findings.append(
                    _finding(
                        code="boundary_material_uncertainty",
                        message=(
                            f"edge {edge.edge_index} has material deterministic "
                            "boundary uncertainty"
                        ),
                    )
                )
            if edge.cue_relation == "forbidden" and edge.edge_index in cue_cuts:
                findings.append(
                    _finding(
                        code="forbidden_boundary_split",
                        message=f"cue split violates edge {edge.edge_index}",
                    )
                )
            if edge.cue_relation == "mandatory" and edge.edge_index not in cue_cuts:
                findings.append(
                    _finding(
                        code="mandatory_boundary_missing",
                        message=f"cue split omits mandatory edge {edge.edge_index}",
                    )
                )
            if edge.line_relation == "forbidden" and edge.edge_index in line_cuts:
                findings.append(
                    _finding(
                        code="forbidden_boundary_split",
                        message=f"line split violates edge {edge.edge_index}",
                    )
                )

    cue_by_id = {cue.id: cue for cue in projection.cues}
    canonical_position = {token.id: index for index, token in enumerate(tokens)}
    for unit in semantic_units:
        if not unit.forbid_cue_breaks and not unit.forbid_line_breaks:
            continue
        cue_ids = {cue_for_token.get(token_id) for token_id in unit.token_ids}
        if unit.forbid_cue_breaks and (None in cue_ids or len(cue_ids) != 1):
            findings.append(
                _finding(
                    code="forbidden_semantic_split",
                    message=f"hard SemanticUnit {unit.id} was split across display cues",
                    cue_ids=tuple(sorted(cue_id for cue_id in cue_ids if cue_id)),
                )
            )
            continue
        if None in cue_ids or len(cue_ids) != 1:
            continue
        cue_id = next(iter(cue_ids))
        cue = cue_by_id[cue_id]
        cue_texts = [token_by_id[token_id].text for token_id in cue.token_ids]
        line_cut_positions, cuts_align = _line_cut_token_positions(cue_texts, cue.lines)
        cue_positions = {token_id: index for index, token_id in enumerate(cue.token_ids)}
        try:
            unit_positions = [cue_positions[token_id] for token_id in unit.token_ids]
            canonical_indices = [canonical_position[token_id] for token_id in unit.token_ids]
        except KeyError:
            continue
        if canonical_indices != list(range(canonical_indices[0], canonical_indices[-1] + 1)):
            findings.append(
                _finding(
                    code="forbidden_semantic_split",
                    message=f"hard SemanticUnit {unit.id} is not contiguous in canonical truth",
                    cue_ids=(cue.id,),
                )
            )
            continue
        internal_line_cuts = set(range(unit_positions[0] + 1, unit_positions[-1] + 1))
        if unit.forbid_line_breaks and (
            not cuts_align or line_cut_positions.intersection(internal_line_cuts)
        ):
            findings.append(
                _finding(
                    code="forbidden_semantic_split",
                    message=f"hard SemanticUnit {unit.id} was split across display lines",
                    cue_ids=(cue.id,),
                )
            )

    for name, (expected, actual) in (artifact_hashes or {}).items():
        if expected != actual:
            findings.append(
                _finding(
                    code="artifact_hash_mismatch",
                    message=f"artifact {name!r} hash does not match its manifest",
                )
            )

    blocking = any(finding.severity in {"error", "blocking"} for finding in findings)
    return QualityReport(
        episode_id=projection.episode_id,
        generation_id=projection.generation_id,
        passed=not blocking,
        findings=tuple(findings),
        metrics={
            "cue_count": float(len(projection.cues)),
            "cue_too_short_count": float(short_count),
            "reading_rate_exceeded_count": float(cps_failures),
            "display_metrics_identity_hash_present": 1.0,
            "long_intercue_gap_count": float(long_intercue_gap_count),
            "max_observed_intercue_gap_ms": float(max_observed_intercue_gap_ms),
            "canonical_token_count": float(len(canonical_ids)),
            "projected_token_count": float(len(projected_ids)),
        },
    )


def assert_projection_quality(
    canonical_tokens: Sequence[CanonicalToken],
    semantic_units: Sequence[SemanticUnit],
    projection: SubtitleProjection,
    profile: ProjectionProfile,
    *,
    artifact_hashes: Mapping[str, tuple[str, str]] | None = None,
    audio_start_ms: int | None = None,
    audio_end_ms: int | None = None,
    canonical_spans: Sequence[CanonicalSpan] = (),
    boundary_constraints: BoundaryConstraintReceipt | None = None,
) -> QualityReport:
    report = evaluate_projection(
        canonical_tokens,
        semantic_units,
        projection,
        profile,
        artifact_hashes=artifact_hashes,
        audio_start_ms=audio_start_ms,
        audio_end_ms=audio_end_ms,
        canonical_spans=canonical_spans,
        boundary_constraints=boundary_constraints,
    )
    if not report.passed:
        raise QualityGateError(report)
    return report


__all__ = ["assert_projection_quality", "evaluate_projection"]
