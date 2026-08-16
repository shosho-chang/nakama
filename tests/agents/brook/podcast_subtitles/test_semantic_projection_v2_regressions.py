from __future__ import annotations

import itertools
import math

import pytest

from agents.brook.podcast_subtitles.profiles import (
    HORIZONTAL_16X9,
    VERTICAL_9X16,
)
from agents.brook.podcast_subtitles.semantic_projection import (
    _build_projection_index,
    _cue_candidate,
    _validate_semantic_units,
    project_semantic_units,
)
from shared.schemas.podcast_subtitles_v2 import (
    CanonicalToken,
    ProjectionProfile,
    SemanticUnit,
)

_TRADITIONAL_LEXEMES = tuple("我們一起重新設計這套字幕系統確保語意完整自然流暢")


def _continuous_tokens(
    count: int,
    *,
    gap_ms: int = 20,
    speech_ms: int = 900,
) -> tuple[CanonicalToken, ...]:
    assert count <= len(_TRADITIONAL_LEXEMES)
    return tuple(
        CanonicalToken(
            id=f"continuous-{index:02d}",
            text=_TRADITIONAL_LEXEMES[index],
            start_ms=index * (speech_ms + gap_ms),
            end_ms=index * (speech_ms + gap_ms) + speech_ms,
            speaker="guest",
        )
        for index in range(count)
    )


def _partition(result: object) -> tuple[tuple[str, ...], ...]:
    return tuple(cue.token_ids for cue in result.cues)  # type: ignore[attr-defined]


def _exhaustive_minimum(
    tokens: tuple[CanonicalToken, ...], profile: ProjectionProfile
) -> tuple[float, set[tuple[tuple[str, ...], ...]]]:
    forbidden, forbidden_line, crossing, ending = _validate_semantic_units(tokens, ())
    index = _build_projection_index(tokens)
    best_cost = math.inf
    best: set[tuple[tuple[str, ...], ...]] = set()
    for cut_flags in itertools.product((False, True), repeat=len(tokens) - 1):
        cuts = (
            0,
            *(index for index, selected in enumerate(cut_flags, start=1) if selected),
            len(tokens),
        )
        cost = 0.0
        partition: list[tuple[str, ...]] = []
        for start, end in zip(cuts, cuts[1:]):
            candidate = _cue_candidate(
                tokens,
                start=start,
                end=end,
                forbidden=forbidden,
                forbidden_line=forbidden_line,
                crossing_strength=crossing,
                ending_strength=ending,
                profile=profile,
                audio_start_ms=0,
                audio_end_ms=tokens[-1].end_ms,
                index=index,
            )
            if candidate is None:
                break
            cost += candidate.cost
            partition.append(tuple(token.id for token in tokens[start:end]))
        else:
            typed = tuple(partition)
            if cost < best_cost and not math.isclose(cost, best_cost):
                best_cost = cost
                best = {typed}
            elif math.isclose(cost, best_cost):
                best.add(typed)
    return best_cost, best


def test_three_lexeme_counterexample_matches_the_declared_global_objective() -> None:
    tokens = _continuous_tokens(3)

    result = project_semantic_units(
        tokens,
        (),
        HORIZONTAL_16X9,
        episode_id="three-lexeme-regression",
        generation_id="generation-three-lexeme-regression",
    )
    oracle_cost, oracle_partitions = _exhaustive_minimum(tokens, HORIZONTAL_16X9)

    assert result.total_cost == pytest.approx(oracle_cost)
    assert _partition(result) in oracle_partitions
    assert len(result.cues) == 1


@pytest.mark.parametrize("count", (3, 5, 10, 20))
@pytest.mark.parametrize("profile", (HORIZONTAL_16X9, VERTICAL_9X16))
def test_continuous_traditional_chinese_never_degenerates_to_singleton_only_cues(
    count: int, profile: ProjectionProfile
) -> None:
    tokens = _continuous_tokens(count)

    result = project_semantic_units(
        tokens,
        (),
        profile,
        episode_id="continuous-zh-hant",
        generation_id=f"generation-{profile.id}-{count}",
    )

    assert len(result.cues) < count
    assert any(len(cue.token_ids) > 1 for cue in result.cues)


def test_unverified_positive_asr_gaps_have_exactly_zero_pause_effect() -> None:
    short_gap = _continuous_tokens(3, gap_ms=1)
    long_gap = _continuous_tokens(3, gap_ms=699)

    def first_candidate(tokens: tuple[CanonicalToken, ...]):
        forbidden, forbidden_line, crossing, ending = _validate_semantic_units(tokens, ())
        return _cue_candidate(
            tokens,
            start=0,
            end=1,
            forbidden=forbidden,
            forbidden_line=forbidden_line,
            crossing_strength=crossing,
            ending_strength=ending,
            profile=HORIZONTAL_16X9,
            audio_start_ms=0,
            audio_end_ms=tokens[-1].end_ms,
            index=_build_projection_index(tokens),
        )

    short = first_candidate(short_gap)
    long = first_candidate(long_gap)

    assert short is not None and long is not None
    assert short.score[1] == 0.0
    assert long.score[1] == 0.0


def test_global_optimizer_matches_exhaustive_oracle_across_small_shapes() -> None:
    for count in range(2, 8):
        tokens = _continuous_tokens(count, gap_ms=1 + count * 3, speech_ms=450)
        profile = HORIZONTAL_16X9.model_copy(
            update={
                "id": f"oracle-shape-{count}",
                "target_line_display_columns": float(10 + count),
                "hard_line_display_columns": float(16 + count),
                "min_cue_duration_ms": 300,
                "max_cue_duration_ms": 4_000,
                "max_reading_units_per_second": 30.0,
            }
        )

        result = project_semantic_units(
            tokens,
            (),
            profile,
            episode_id="oracle-shapes",
            generation_id=f"generation-oracle-{count}",
        )
        oracle_cost, oracle_partitions = _exhaustive_minimum(tokens, profile)

        assert result.total_cost == pytest.approx(oracle_cost)
        assert _partition(result) in oracle_partitions


def test_unverified_small_gap_changes_do_not_change_partition() -> None:
    near = _continuous_tokens(10, gap_ms=1, speech_ms=400)
    farther = _continuous_tokens(10, gap_ms=30, speech_ms=400)

    near_result = project_semantic_units(
        near,
        (),
        HORIZONTAL_16X9,
        episode_id="gap-authority",
        generation_id="generation-gap-near",
    )
    farther_result = project_semantic_units(
        farther,
        (),
        HORIZONTAL_16X9,
        episode_id="gap-authority",
        generation_id="generation-gap-farther",
    )

    assert tuple(len(cue.token_ids) for cue in near_result.cues) == tuple(
        len(cue.token_ids) for cue in farther_result.cues
    )


def test_relaxing_only_hard_width_does_not_increase_cue_count() -> None:
    tokens = _continuous_tokens(20, gap_ms=10, speech_ms=250)
    narrow = VERTICAL_9X16.model_copy(
        update={
            "id": "width-monotonic-narrow",
            "target_line_display_columns": 18.0,
            "hard_line_display_columns": 20.0,
        }
    )
    wide = narrow.model_copy(
        update={
            "id": "width-monotonic-wide",
            "hard_line_display_columns": 32.0,
        }
    )

    narrow_result = project_semantic_units(
        tokens,
        (),
        narrow,
        episode_id="width-monotonic",
        generation_id="generation-width-narrow",
    )
    wide_result = project_semantic_units(
        tokens,
        (),
        wide,
        episode_id="width-monotonic",
        generation_id="generation-width-wide",
    )

    assert len(wide_result.cues) <= len(narrow_result.cues)


def test_only_explicit_verified_pause_strength_can_reward_a_boundary() -> None:
    tokens = _continuous_tokens(3, gap_ms=20)
    forbidden, forbidden_line, crossing, ending = _validate_semantic_units(tokens, ())
    index = _build_projection_index(tokens)
    common = {
        "start": 0,
        "end": 1,
        "forbidden": forbidden,
        "forbidden_line": forbidden_line,
        "crossing_strength": crossing,
        "ending_strength": ending,
        "profile": HORIZONTAL_16X9,
        "audio_start_ms": 0,
        "audio_end_ms": tokens[-1].end_ms,
        "index": index,
    }

    unavailable = _cue_candidate(tokens, **common)
    verified = _cue_candidate(tokens, **common, verified_pause_strength={1: 0.75})

    assert unavailable is not None and verified is not None
    assert unavailable.score[1] == 0.0
    assert verified.score[1] == -0.75


@pytest.mark.parametrize("count", (3, 5, 10, 20))
@pytest.mark.parametrize("profile", (HORIZONTAL_16X9, VERTICAL_9X16))
def test_dense_adversarial_preferred_edges_cannot_manufacture_singleton_cues(
    count: int, profile: ProjectionProfile
) -> None:
    tokens = _continuous_tokens(count)
    dense_preferred = tuple(
        SemanticUnit(
            id=f"preferred-edge-{edge_index}",
            token_ids=(tokens[edge_index - 1].id, tokens[edge_index].id),
            kind="boundary_pair",
            strength=1.0,
            cue_boundary_relation="preferred",
            line_boundary_relation="preferred",
        )
        for edge_index in range(1, len(tokens))
    )

    baseline = project_semantic_units(
        tokens,
        (),
        profile,
        episode_id="dense-preferred-baseline",
        generation_id=f"generation-dense-baseline-{profile.id}-{count}",
    )
    result = project_semantic_units(
        tokens,
        dense_preferred,
        profile,
        episode_id="dense-preferred-hostile",
        generation_id=f"generation-dense-preferred-{profile.id}-{count}",
    )

    assert all(len(cue.token_ids) > 1 for cue in result.cues)
    assert len(result.cues) == len(baseline.cues)
