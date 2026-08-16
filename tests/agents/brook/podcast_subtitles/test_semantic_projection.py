from __future__ import annotations

import json
import math
import time

import pytest

from agents.brook.podcast_subtitles.display_metrics import display_columns as _char_count
from agents.brook.podcast_subtitles.errors import (
    NeedsAlignmentError,
    ProjectionUnsatisfiableError,
    QualityGateError,
)
from agents.brook.podcast_subtitles.quality import (
    assert_projection_quality,
    evaluate_projection,
)
from agents.brook.podcast_subtitles.profiles import HORIZONTAL_16X9
from agents.brook.podcast_subtitles.semantic_projection import (
    _build_projection_index,
    _cue_candidate,
    _validate_semantic_units,
    project_semantic_units,
    projection_sidecar,
    render_sidecar_json,
    render_srt,
)
from shared.schemas.podcast_subtitles_v2 import (
    CanonicalSpan,
    CanonicalToken,
    DisplayCue,
    ProjectionProfile,
    SemanticUnit,
    SubtitleProjection,
    canonical_content_hash,
)


def _coarse_phrase(
    lexemes: tuple[str, ...], *, end_ms: int = 4000
) -> tuple[tuple[CanonicalToken, ...], tuple[CanonicalSpan, ...]]:
    tokens = tuple(
        CanonicalToken(
            id=f"coarse-{index}",
            text=lexeme,
            timing_basis="coarse_span",
            speaker="guest",
            evidence_ids=("reviewed-audio",),
        )
        for index, lexeme in enumerate(lexemes, start=1)
    )
    return tokens, (
        CanonicalSpan(
            id="coarse-span",
            token_ids=tuple(token.id for token in tokens),
            start_ms=0,
            end_ms=end_ms,
            alignment="coarse",
        ),
    )


def _profile(**overrides: object) -> ProjectionProfile:
    values: dict[str, object] = {
        "id": "short-vertical-zh-tw",
        "profile_version": 1,
        "max_lines": 1,
        "target_line_display_columns": 20,
        "hard_line_display_columns": 28,
        "min_cue_duration_ms": 800,
        "max_cue_duration_ms": 5000,
        "max_reading_units_per_second": 25.0,
        "max_intercue_gap_ms": 1200,
        "pause_reward_saturation_ms": 700,
        "semantic_break_penalty": 4.0,
        "short_cue_penalty": 2.0,
    }
    translated = dict(overrides)
    if "target_line_width" in translated:
        translated["target_line_display_columns"] = 2 * float(
            translated.pop("target_line_width")
        )
    if "hard_line_width" in translated:
        translated["hard_line_display_columns"] = 2 * float(
            translated.pop("hard_line_width")
        )
    if "max_chars_per_second" in translated:
        translated["max_reading_units_per_second"] = translated.pop(
            "max_chars_per_second"
        )
    values.update(translated)
    return ProjectionProfile(**values)


def _tokens(words: list[str], *, speakers: list[str] | None = None) -> tuple[CanonicalToken, ...]:
    return tuple(
        CanonicalToken(
            id=f"tok-{index:03d}",
            text=word,
            start_ms=(index - 1) * 500,
            end_ms=(index - 1) * 500 + 450,
            speaker=(speakers or ["guest"] * len(words))[index - 1],
        )
        for index, word in enumerate(words, start=1)
    )


def test_horizontal_profile_is_single_line_without_shrinking_cue_capacity() -> None:
    assert HORIZONTAL_16X9.max_lines == 1
    assert HORIZONTAL_16X9.hard_line_display_columns == 88


def test_single_line_layout_width_cost_does_not_manufacture_a_cue_boundary() -> None:
    tokens = (
        CanonicalToken(
            id="single-line-left",
            text="A" * 44,
            start_ms=0,
            end_ms=2_000,
            speaker="guest",
        ),
        CanonicalToken(
            id="single-line-right",
            text="B" * 44,
            start_ms=2_050,
            end_ms=4_050,
            speaker="guest",
        ),
    )
    natural_unit = SemanticUnit(
        id="single-line-natural-unit",
        token_ids=tuple(token.id for token in tokens),
        kind="sentence",
        strength=0.01,
    )

    result = project_semantic_units(
        tokens,
        (natural_unit,),
        HORIZONTAL_16X9,
        episode_id="single-line-natural-unit",
        generation_id="single-line-natural-unit",
        audio_end_ms=6_500,
    )

    assert len(result.cues) == 1
    assert result.cues[0].lines == ("A" * 44 + "B" * 44,)


def test_single_line_profile_preserves_a_wide_protected_unit_and_renders_one_line() -> None:
    text = "ProtectedSemanticUnit" * 3
    assert 44 < _char_count(text) <= HORIZONTAL_16X9.hard_line_display_columns
    tokens = (
        CanonicalToken(
            id="wide-protected-left",
            text=text[: len(text) // 2],
            start_ms=0,
            end_ms=2_000,
            speaker="guest",
        ),
        CanonicalToken(
            id="wide-protected-right",
            text=text[len(text) // 2 :],
            start_ms=2_050,
            end_ms=4_050,
            speaker="guest",
        ),
    )
    protected_unit = SemanticUnit(
        id="wide-protected-unit",
        token_ids=tuple(token.id for token in tokens),
        kind="name",
        strength=1.0,
        forbid_cue_breaks=True,
    )

    result = project_semantic_units(
        tokens,
        (protected_unit,),
        HORIZONTAL_16X9,
        episode_id="wide-protected-unit",
        generation_id="wide-protected-unit",
        audio_end_ms=6_500,
    )
    rendered_lines = render_srt(result).strip().splitlines()

    assert len(result.cues) == 1
    assert result.cues[0].lines == (text,)
    assert rendered_lines == ["1", "00:00:00,000 --> 00:00:04,050", text]


def _hard_unit(unit_id: str, tokens: tuple[CanonicalToken, ...]) -> SemanticUnit:
    return SemanticUnit(
        id=unit_id,
        token_ids=tuple(token.id for token in tokens),
        kind="phrase",
        strength=1.0,
        forbid_cue_breaks=True,
        forbid_line_breaks=True,
    )


def _brute_force_optima(
    tokens: tuple[CanonicalToken, ...],
    units: tuple[SemanticUnit, ...],
    profile: ProjectionProfile,
) -> tuple[float, set[tuple[tuple[str, ...], ...]]]:
    """Enumerate every partition; used only to prove bounded DP equivalence."""

    forbidden, forbidden_line, crossing, ending = _validate_semantic_units(tokens, units)
    index = _build_projection_index(tokens)
    best_cost = math.inf
    optima: set[tuple[tuple[str, ...], ...]] = set()
    for mask in range(1 << (len(tokens) - 1)):
        cuts = (
            0,
            *(position for position in range(1, len(tokens)) if mask & (1 << (position - 1))),
            len(tokens),
        )
        if any(cut in forbidden for cut in cuts[1:-1]):
            continue
        cost = 0.0
        partition: list[tuple[str, ...]] = []
        legal = True
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
                legal = False
                break
            cost += candidate.cost
            partition.append(tuple(token.id for token in tokens[start:end]))
        if not legal:
            continue
        typed_partition = tuple(partition)
        if cost < best_cost and not math.isclose(cost, best_cost):
            best_cost = cost
            optima = {typed_partition}
        elif math.isclose(cost, best_cost):
            optima.add(typed_partition)
    return best_cost, optima


def test_mandatory_cue_boundary_prevents_merging_adjacent_sentences() -> None:
    tokens = _tokens(["第一句", "結束", "第二句", "開始"])
    profile = _profile(
        max_lines=1,
        target_line_display_columns=20,
        hard_line_display_columns=24,
    )

    unconstrained = project_semantic_units(
        tokens,
        (),
        profile,
        episode_id="sentence-boundary",
        generation_id="generation-sentence-boundary",
    )
    constrained = project_semantic_units(
        tokens,
        (),
        profile,
        episode_id="sentence-boundary",
        generation_id="generation-sentence-boundary",
        mandatory_cue_boundaries=(2,),
    )

    # The display optimiser would otherwise merge both short sentences.
    assert len(unconstrained.cues) == 1
    assert tuple(cue.token_ids for cue in constrained.cues) == (
        tuple(token.id for token in tokens[:2]),
        tuple(token.id for token in tokens[2:]),
    )


def test_empty_mandatory_cue_boundaries_preserve_default_and_soft_projection() -> None:
    tokens = _tokens(["這是", "同一個", "語意單位"])
    profile = _profile()
    soft_unit = SemanticUnit(
        id="soft-sentence",
        token_ids=tuple(token.id for token in tokens),
        kind="sentence",
        strength=0.7,
        forbid_cue_breaks=False,
        forbid_line_breaks=False,
    )

    omitted = project_semantic_units(
        tokens,
        (soft_unit,),
        profile,
        episode_id="default-boundaries",
        generation_id="generation-default-boundaries",
    )
    explicit_empty = project_semantic_units(
        tokens,
        (soft_unit,),
        profile,
        episode_id="default-boundaries",
        generation_id="generation-default-boundaries",
        mandatory_cue_boundaries=(),
    )

    assert explicit_empty == omitted
    assert len(omitted.cues) == 1


def test_conflicting_mandatory_cue_boundary_fails_closed() -> None:
    tokens = _tokens(["不可", "拆開"])
    hard_unit = _hard_unit("indivisible", tokens)

    with pytest.raises(ProjectionUnsatisfiableError, match="no global partition"):
        project_semantic_units(
            tokens,
            (hard_unit,),
            _profile(),
            episode_id="conflicting-boundary",
            generation_id="generation-conflicting-boundary",
            mandatory_cue_boundaries=(1,),
        )


@pytest.mark.parametrize("edge", (-1, 0, 2, True, 1.5))
def test_invalid_mandatory_cue_boundary_is_rejected(edge: object) -> None:
    tokens = _tokens(["左", "右"])

    with pytest.raises(ValueError, match="mandatory cue boundary"):
        project_semantic_units(
            tokens,
            (),
            _profile(),
            episode_id="invalid-boundary",
            generation_id="generation-invalid-boundary",
            mandatory_cue_boundaries=(edge,),  # type: ignore[arg-type]
        )


def test_known_and_synthetic_hard_phrases_are_never_split() -> None:
    words = [
        "看到",
        "學業",
        "經歷",
        "之後",
        "我覺得",
        "在",
        "女中",
        "之前",
        "才發現",
        "心理",
        "健康",
        "有問題",
        "那就是",
        "人生",
        "真實",
        "樣貌",
        "的一個",
        "展現",
    ]
    tokens = _tokens(words)
    units = (
        _hard_unit("學業經歷", tokens[1:3]),
        _hard_unit("在女中之前", tokens[5:8]),
        # Synthetic invariant only: the corresponding Anji audio remains needs-review.
        _hard_unit("synthetic-心理健康有問題", tokens[9:12]),
        _hard_unit("人生真實樣貌的一個展現", tokens[13:18]),
    )

    result = project_semantic_units(
        tokens,
        units,
        _profile(),
        episode_id="anji",
        generation_id="generation-a",
    )

    cue_by_token = {token_id: cue.id for cue in result.cues for token_id in cue.token_ids}
    for unit in units:
        assert len({cue_by_token[token_id] for token_id in unit.token_ids}) == 1
        cue = next(cue for cue in result.cues if unit.token_ids[0] in cue.token_ids)
        assert len(cue.lines) == 1
    assert all(cue.end_ms - cue.start_ms >= 800 for cue in result.cues)
    assert result.token_ids == tuple(token.id for token in tokens)
    assert "".join(line for cue in result.cues for line in cue.lines) == "".join(words)


def test_global_dp_avoids_locally_perfect_cut_that_strands_last_token() -> None:
    tokens = (
        CanonicalToken(id="t1", text="甲乙丙丁戊", start_ms=0, end_ms=900, speaker="a"),
        CanonicalToken(id="t2", text="己庚辛", start_ms=900, end_ms=1200, speaker="a"),
        CanonicalToken(id="t3", text="壬癸子丑寅卯辰", start_ms=1200, end_ms=1900, speaker="a"),
    )
    units = tuple(
        _hard_unit(f"unit-{index}", (token,)) for index, token in enumerate(tokens, start=1)
    )
    profile = _profile(target_line_width=8, hard_line_width=12)

    result = project_semantic_units(
        tokens,
        units,
        profile,
        episode_id="dp",
        generation_id="generation-dp",
    )

    # A local target-width greedy choice would emit t1+t2 (exactly 8 chars),
    # stranding the 700ms t3. Global DP sees the future and chooses a legal path.
    assert [cue.token_ids for cue in result.cues] == [("t1",), ("t2", "t3")]


@pytest.mark.parametrize(
    "profile",
    (
        _profile(max_lines=1, target_line_width=6, hard_line_width=9),
        _profile(max_lines=2, target_line_width=5, hard_line_width=7),
    ),
)
def test_bounded_global_dp_matches_exhaustive_small_case(
    profile: ProjectionProfile,
) -> None:
    tokens = _tokens(["甲", "乙乙", "丙", "丁丁丁", "戊", "己己", "庚"])
    units = (
        SemanticUnit(
            id="soft-middle",
            token_ids=tuple(token.id for token in tokens[1:5]),
            kind="clause",
            strength=0.8,
        ),
        _hard_unit("hard-tail", tokens[5:]),
    )

    result = project_semantic_units(
        tokens,
        units,
        profile,
        episode_id="brute-force",
        generation_id="generation-brute-force",
    )
    brute_cost, brute_partitions = _brute_force_optima(tokens, units, profile)
    optimized_partition = tuple(cue.token_ids for cue in result.cues)

    assert result.total_cost == pytest.approx(brute_cost)
    assert optimized_partition in brute_partitions


@pytest.mark.parametrize(
    ("token_count", "max_seconds"),
    ((5_000, 3.0), (24_769, 10.0)),
)
def test_bounded_edge_generation_scales_linearly_on_long_episode(
    token_count: int,
    max_seconds: float,
) -> None:
    tokens = tuple(
        CanonicalToken(
            id=f"long-{index:05d}",
            text="字",
            start_ms=index * 60,
            end_ms=index * 60 + 50,
            speaker="guest",
        )
        for index in range(token_count)
    )
    profile = _profile(
        max_lines=2,
        target_line_width=9,
        hard_line_width=13,
        min_cue_duration_ms=200,
        max_cue_duration_ms=2_000,
        max_chars_per_second=50.0,
    )

    started = time.perf_counter()
    result = project_semantic_units(
        tokens,
        (),
        profile,
        episode_id="anji-scale",
        generation_id=f"generation-{token_count}",
    )
    elapsed = time.perf_counter() - started

    assert result.token_ids == tuple(token.id for token in tokens)
    assert elapsed < max_seconds


def test_renderers_preserve_srt_text_and_sidecar_token_identity() -> None:
    tokens = _tokens(["心理", "健康", "有問題"])
    result = project_semantic_units(
        tokens,
        (_hard_unit("心理健康有問題", tokens),),
        _profile(),
        episode_id="anji",
        generation_id="generation-a",
    )

    assert "心理健康有問題" in render_srt(result)
    sidecar = projection_sidecar(result)
    assert sidecar["generation_id"] == "generation-a"
    assert sidecar["cues"][0]["token_ids"] == [token.id for token in tokens]
    assert json.loads(render_sidecar_json(result)) == sidecar


def test_short_interjection_uses_adjacent_silence_for_minimum_display_time() -> None:
    token = CanonicalToken(
        id="yes",
        text="對",
        start_ms=200,
        end_ms=650,
        speaker="guest",
    )

    result = project_semantic_units(
        (token,),
        (_hard_unit("interjection", (token,)),),
        _profile(),
        episode_id="short",
        generation_id="generation-short",
        audio_start_ms=0,
        audio_end_ms=1000,
    )

    cue = result.cues[0]
    assert cue.start_ms <= token.start_ms
    assert cue.end_ms >= token.end_ms
    assert cue.end_ms - cue.start_ms == 800


def test_short_interjection_without_free_audio_fails_closed() -> None:
    token = CanonicalToken(
        id="yes",
        text="對",
        start_ms=0,
        end_ms=450,
        speaker="guest",
    )

    with pytest.raises(ProjectionUnsatisfiableError):
        project_semantic_units(
            (token,),
            (_hard_unit("interjection", (token,)),),
            _profile(),
            episode_id="packed",
            generation_id="generation-packed",
            audio_start_ms=0,
            audio_end_ms=700,
        )


def test_available_silence_extends_cue_until_cps_is_legal() -> None:
    token = CanonicalToken(
        id="dense",
        text="人生真實樣貌的一個展現",
        start_ms=500,
        end_ms=950,
        speaker="guest",
    )
    profile = _profile(max_chars_per_second=10.0)

    result = project_semantic_units(
        (token,),
        (_hard_unit("dense-phrase", (token,)),),
        profile,
        episode_id="dense",
        generation_id="generation-dense",
        audio_start_ms=0,
        audio_end_ms=2000,
    )

    cue = result.cues[0]
    assert len(token.text) / ((cue.end_ms - cue.start_ms) / 1000) <= 10.0
    assert evaluate_projection((token,), (), result.projection, profile).passed


def test_natural_sixty_second_silence_stays_empty_and_passes_quality() -> None:
    tokens = (
        CanonicalToken(
            id="before-pause",
            text="前",
            start_ms=1_000,
            end_ms=1_400,
            speaker="guest",
        ),
        CanonicalToken(
            id="after-pause",
            text="後",
            start_ms=61_400,
            end_ms=61_800,
            speaker="guest",
        ),
    )
    profile = _profile()

    result = project_semantic_units(
        tokens,
        (),
        profile,
        episode_id="natural-silence",
        generation_id="generation-natural-silence",
        audio_start_ms=0,
        audio_end_ms=63_000,
    )
    report = evaluate_projection(
        tokens,
        (),
        result.projection,
        profile,
        audio_start_ms=0,
        audio_end_ms=63_000,
    )

    assert [cue.end_ms - cue.start_ms for cue in result.cues] == [800, 800]
    assert result.cues[1].start_ms - result.cues[0].end_ms == 59_600
    assert all(
        cue.end_ms - cue.start_ms < profile.max_cue_duration_ms for cue in result.cues
    )
    assert report.passed
    assert report.metrics["long_intercue_gap_count"] == 1.0
    assert report.metrics["max_observed_intercue_gap_ms"] == 59_600.0
    assert "intercue_gap_exceeded" not in {finding.code for finding in report.findings}


def test_line_wrap_prefers_soft_semantic_seam_over_balanced_internal_cut() -> None:
    tokens = _tokens(["甲乙丙", "丁戊己", "庚辛壬", "癸子丑"])
    soft_unit = SemanticUnit(
        id="soft-middle",
        token_ids=(tokens[1].id, tokens[2].id),
        kind="phrase",
        strength=1.0,
        forbid_cue_breaks=False,
        forbid_line_breaks=False,
    )
    profile = _profile(
        max_lines=2,
        target_line_width=6,
        hard_line_width=9,
        semantic_break_penalty=4.0,
    )
    forbidden, forbidden_line, crossing, ending = _validate_semantic_units(
        tokens, (soft_unit,)
    )

    candidate = _cue_candidate(
        tokens,
        start=0,
        end=len(tokens),
        forbidden=forbidden,
        forbidden_line=forbidden_line,
        crossing_strength=crossing,
        ending_strength=ending,
        profile=profile,
        audio_start_ms=0,
        audio_end_ms=tokens[-1].end_ms,
        index=_build_projection_index(tokens),
    )

    assert candidate is not None
    # Six/six characters is geometrically perfect but cuts the soft unit.
    # Nine/three is less balanced and preserves its semantic cohesion.
    assert candidate.lines == ("甲乙丙丁戊己庚辛壬", "癸子丑")


def test_two_line_profile_uses_separate_cue_density_target() -> None:
    tokens = (
        CanonicalToken(
            id="cue-density-1",
            text="甲乙丙丁戊己",
            start_ms=0,
            end_ms=900,
            speaker="guest",
        ),
        CanonicalToken(
            id="cue-density-2",
            text="庚辛壬癸子丑",
            start_ms=1000,
            end_ms=1900,
            speaker="guest",
        ),
    )
    legacy = _profile(
        max_lines=2,
        target_line_width=6,
        hard_line_width=8,
        cue_boundary_penalty=0.5,
    )
    wider_cue = legacy.model_copy(update={"target_cue_display_columns": 24.0})

    legacy_result = project_semantic_units(
        tokens,
        (),
        legacy,
        episode_id="legacy-cue-density",
        generation_id="legacy-cue-density",
    )
    wider_result = project_semantic_units(
        tokens,
        (),
        wider_cue,
        episode_id="two-line-cue-density",
        generation_id="two-line-cue-density",
    )

    assert len(legacy_result.cues) == 2
    assert len(wider_result.cues) == 1
    assert wider_result.cues[0].lines == ("甲乙丙丁戊己", "庚辛壬癸子丑")


def test_cue_density_band_does_not_split_legal_semantic_length() -> None:
    tokens = tuple(
        CanonicalToken(
            id=f"band-{index}",
            text=text,
            start_ms=index * 500,
            end_ms=index * 500 + 450,
            speaker="guest",
        )
        for index, text in enumerate(("甲乙丙丁戊己", "庚辛壬癸子丑"))
    )
    profile = _profile(
        max_lines=2,
        target_line_width=6,
        hard_line_width=8,
        target_cue_display_columns=12.0,
        minimum_preferred_cue_display_columns=12.0,
        maximum_preferred_cue_display_columns=24.0,
        cue_boundary_penalty=0.5,
    )

    result = project_semantic_units(
        tokens,
        (),
        profile,
        episode_id="cue-density-band",
        generation_id="cue-density-band",
    )

    assert len(result.cues) == 1


def test_preferred_cue_seam_does_not_manufacture_an_extra_cue() -> None:
    left = "前半句已經完整說明一個自然意思而且到此結束"
    right = "後半句接著展開另一個自然意思而且主詞不同"
    tokens = (
        CanonicalToken(
            id="preferred-left",
            text=left,
            start_ms=0,
            end_ms=3000,
            speaker="guest",
        ),
        CanonicalToken(
            id="preferred-right",
            text=right,
            start_ms=3100,
            end_ms=6100,
            speaker="guest",
        ),
    )
    seam = SemanticUnit(
        id="preferred-cue-seam",
        token_ids=(tokens[0].id, tokens[1].id),
        kind="boundary_pair",
        strength=1.0,
        cue_boundary_relation="preferred",
        line_boundary_relation="preferred",
    )

    baseline = project_semantic_units(
        tokens,
        (),
        HORIZONTAL_16X9,
        episode_id="preferred-cue-seam-baseline",
        generation_id="preferred-cue-seam-baseline",
        audio_end_ms=6100,
    )
    result = project_semantic_units(
        tokens,
        (seam,),
        HORIZONTAL_16X9,
        episode_id="preferred-cue-seam",
        generation_id="preferred-cue-seam",
        audio_end_ms=6100,
    )

    assert len(result.cues) == len(baseline.cues)
    assert all(len(cue.lines) == 1 for cue in result.cues)


def test_preferred_cue_seam_selects_the_edge_only_when_cue_count_is_equal() -> None:
    tokens = _tokens(["甲乙", "丙丁", "戊己"])
    preferred = SemanticUnit(
        id="preferred-equal-count-edge",
        token_ids=(tokens[1].id, tokens[2].id),
        kind="boundary_pair",
        strength=1.0,
        cue_boundary_relation="preferred",
        line_boundary_relation="preferred",
    )
    profile = _profile(
        max_lines=1,
        target_line_width=4,
        hard_line_width=4,
        min_cue_duration_ms=400,
        max_cue_duration_ms=5_000,
    )

    result = project_semantic_units(
        tokens,
        (preferred,),
        profile,
        episode_id="preferred-equal-count-edge",
        generation_id="preferred-equal-count-edge",
    )

    assert [cue.token_ids for cue in result.cues] == [
        (tokens[0].id, tokens[1].id),
        (tokens[2].id,),
    ]


def test_duplicate_soft_units_have_one_bounded_projection_effect() -> None:
    tokens = _tokens(["甲乙丙", "丁戊己", "庚辛壬", "癸子丑"])

    def unit(unit_id: str) -> SemanticUnit:
        return SemanticUnit(
            id=unit_id,
            token_ids=(tokens[1].id, tokens[2].id),
            kind="phrase",
            strength=0.8,
            forbid_cue_breaks=False,
            forbid_line_breaks=False,
        )

    single = project_semantic_units(
        tokens,
        (unit("soft-1"),),
        _profile(max_lines=2, target_line_width=6, hard_line_width=9),
        episode_id="bounded-soft",
        generation_id="generation-bounded-soft",
    )
    repeated = project_semantic_units(
        tokens,
        tuple(unit(f"soft-{index}") for index in range(20)),
        _profile(max_lines=2, target_line_width=6, hard_line_width=9),
        episode_id="bounded-soft",
        generation_id="generation-bounded-soft",
    )

    single_effects = _validate_semantic_units(tokens, (unit("single-effect"),))
    repeated_effects = _validate_semantic_units(
        tokens, tuple(unit(f"repeated-effect-{index}") for index in range(20))
    )

    assert repeated.projection == single.projection
    assert repeated.total_cost == single.total_cost
    assert repeated_effects == single_effects


def test_nested_soft_units_use_per_boundary_max_without_losing_each_level() -> None:
    tokens = _tokens(["一", "二", "三", "四", "五"])
    units = (
        SemanticUnit(
            id="outer",
            token_ids=tuple(token.id for token in tokens),
            kind="sentence",
            strength=0.4,
        ),
        SemanticUnit(
            id="inner",
            token_ids=tuple(token.id for token in tokens[1:4]),
            kind="phrase",
            strength=0.9,
        ),
    )

    _, _, crossing, ending = _validate_semantic_units(tokens, units)

    assert crossing == {1: 0.4, 2: 0.9, 3: 0.9, 4: 0.4}
    assert ending == {4: 0.9, 5: 0.4}


def test_same_range_with_conflicting_semantic_effect_fails_closed() -> None:
    tokens = _tokens(["一", "二", "三"])
    baseline = SemanticUnit(
        id="baseline",
        token_ids=tuple(token.id for token in tokens),
        kind="phrase",
        strength=0.4,
    )
    conflicting = baseline.model_copy(update={"id": "conflict", "strength": 0.9})

    with pytest.raises(ValueError, match="conflicting effects"):
        _validate_semantic_units(tokens, (baseline, conflicting))


def test_long_name_is_one_cue_but_may_wrap_across_physical_lines() -> None:
    tokens = _tokens(["國際", "心理", "健康", "教育", "促進", "研究", "協會"])
    long_name = SemanticUnit(
        id="long-name",
        token_ids=tuple(token.id for token in tokens),
        kind="name",
        strength=1.0,
        forbid_cue_breaks=True,
        forbid_line_breaks=False,
    )
    profile = _profile(
        max_lines=2,
        target_line_width=7,
        hard_line_width=8,
    )

    result = project_semantic_units(
        tokens,
        (long_name,),
        profile,
        episode_id="long-name",
        generation_id="generation-long-name",
    )

    assert len(result.cues) == 1
    assert len(result.cues[0].lines) == 2
    assert "".join(result.cues[0].lines) == "".join(token.text for token in tokens)
    assert all(
        _char_count(line) <= profile.hard_line_display_columns
        for line in result.cues[0].lines
    )


def test_acoustic_pause_reward_has_its_own_short_saturation_horizon() -> None:
    tokens = (
        CanonicalToken(id="pause-left", text="前句", start_ms=0, end_ms=900, speaker="guest"),
        CanonicalToken(
            id="pause-right",
            text="後句",
            start_ms=1400,
            end_ms=2300,
            speaker="guest",
        ),
    )
    index = _build_projection_index(tokens)

    def candidate(max_gap: int):
        return _cue_candidate(
            tokens,
            start=0,
            end=1,
            forbidden=set(),
            crossing_strength={},
            ending_strength={},
            profile=_profile(
                max_intercue_gap_ms=max_gap,
                pause_reward_saturation_ms=700,
            ),
            audio_start_ms=0,
            audio_end_ms=2300,
            index=index,
        )

    short_gap_policy = candidate(1200)
    long_gap_policy = candidate(10_000)

    assert short_gap_policy is not None and long_gap_policy is not None
    assert short_gap_policy.cost == long_gap_policy.cost


def test_cue_hard_phrase_can_wrap_inside_one_cue_without_becoming_unsatisfiable() -> None:
    tokens = _tokens(["人生真實樣貌", "的一個展現"])
    cue_only_unit = SemanticUnit(
        id="complete-meaning",
        token_ids=tuple(token.id for token in tokens),
        kind="phrase",
        strength=1.0,
        forbid_cue_breaks=True,
        forbid_line_breaks=False,
    )
    profile = _profile(
        max_lines=2,
        target_line_width=6,
        hard_line_width=8,
    )

    result = project_semantic_units(
        tokens,
        (cue_only_unit,),
        profile,
        episode_id="semantic-hierarchy",
        generation_id="generation-semantic-hierarchy",
    )

    assert len(result.cues) == 1
    assert result.cues[0].lines == ("人生真實樣貌", "的一個展現")


def test_overlapping_nested_semantic_units_protect_each_level() -> None:
    tokens = _tokens(["受訪者", "提到", "不正常人類研究所", "新書"])
    units = (
        SemanticUnit(
            id="clause",
            token_ids=tuple(token.id for token in tokens),
            kind="clause",
            strength=0.9,
            forbid_cue_breaks=True,
            forbid_line_breaks=False,
        ),
        SemanticUnit(
            id="proper-name",
            token_ids=(tokens[2].id,),
            kind="name",
            strength=1.0,
            forbid_cue_breaks=True,
            forbid_line_breaks=True,
        ),
    )

    forbidden_cue, forbidden_line, _, _ = _validate_semantic_units(tokens, units)

    assert forbidden_cue == {1, 2, 3}
    assert forbidden_line == set()


def test_quality_independently_rejects_unknown_speaker() -> None:
    token = CanonicalToken(
        id="unknown-speaker",
        text="心理健康",
        start_ms=100,
        end_ms=900,
        speaker=None,
    )
    profile = _profile()
    projection = SubtitleProjection(
        episode_id="anji",
        generation_id="generation-unknown-speaker",
        canonical_content_hash=canonical_content_hash((token,)),
        profile_id=profile.id,
        profile_version=profile.profile_version,
        cues=(
            DisplayCue(
                id="cue-unknown-speaker",
                token_ids=(token.id,),
                start_ms=100,
                end_ms=900,
                lines=(token.text,),
            ),
        ),
    )

    report = evaluate_projection((token,), (), projection, profile)

    assert not report.passed
    assert "speaker_unresolved" in {finding.code for finding in report.findings}


def test_quality_independently_blocks_editorial_policy_drift() -> None:
    token = CanonicalToken(
        id="editorial-drift",
        text="这是错误。",
        start_ms=100,
        end_ms=1100,
        speaker="guest",
    )
    profile = _profile()
    projection = SubtitleProjection(
        episode_id="anji",
        generation_id="generation-editorial-drift",
        canonical_content_hash=canonical_content_hash((token,)),
        profile_id=profile.id,
        profile_version=profile.profile_version,
        cues=(
            DisplayCue(
                id="cue-editorial-drift",
                token_ids=(token.id,),
                start_ms=100,
                end_ms=1100,
                lines=(token.text,),
            ),
        ),
    )

    report = evaluate_projection((token,), (), projection, profile)

    assert not report.passed
    assert {"simplified_chinese_suspected", "forbidden_punctuation"}.issubset(
        finding.code for finding in report.findings
    )


def test_quality_gate_reports_all_required_blocking_codes() -> None:
    tokens = _tokens(["心理", "健康", "有問題"], speakers=["host", "guest", "guest"])
    hard_unit = _hard_unit("心理健康有問題", tokens)
    profile = _profile(max_chars_per_second=2.0)
    projection = SubtitleProjection(
        episode_id="anji",
        generation_id="generation-a",
        canonical_content_hash=canonical_content_hash(tokens),
        profile_id=profile.id,
        profile_version=profile.profile_version,
        cues=(
            DisplayCue(
                id="bad-1",
                token_ids=(tokens[0].id, tokens[1].id),
                start_ms=0,
                end_ms=500,
                lines=("心理被改",),
            ),
            DisplayCue(
                id="bad-2",
                token_ids=(tokens[2].id,),
                start_ms=1000,
                end_ms=1450,
                lines=("有問題",),
            ),
        ),
    )

    report = evaluate_projection(
        tokens,
        (hard_unit,),
        projection,
        profile,
        artifact_hashes={"projection.srt": ("expected", "tampered")},
    )
    codes = {finding.code for finding in report.findings}
    assert not report.passed
    assert {
        "token_sequence_mismatch",
        "speaker_crossing",
        "cue_too_short",
        "reading_rate_exceeded",
        "forbidden_semantic_split",
        "artifact_hash_mismatch",
    }.issubset(codes)
    with pytest.raises(QualityGateError):
        assert_projection_quality(
            tokens,
            (hard_unit,),
            projection,
            profile,
            artifact_hashes={"projection.srt": ("expected", "tampered")},
        )


def test_quality_rejects_timing_that_clips_canonical_speech_and_internal_token_cut() -> None:
    token = CanonicalToken(
        id="phrase",
        text="學業經歷",
        start_ms=100,
        end_ms=1000,
        speaker="guest",
    )
    profile = _profile(max_lines=2)
    projection = SubtitleProjection(
        episode_id="anji",
        generation_id="generation-a",
        canonical_content_hash=canonical_content_hash((token,)),
        profile_id=profile.id,
        profile_version=profile.profile_version,
        cues=(
            DisplayCue(
                id="bad-cut",
                token_ids=(token.id,),
                start_ms=200,
                end_ms=1000,
                lines=("學業", "經歷"),
            ),
        ),
    )

    report = evaluate_projection(
        (token,),
        (_hard_unit("學業經歷", (token,)),),
        projection,
        profile,
    )

    assert {finding.code for finding in report.findings}.issuperset(
        {"timeline_invalid", "forbidden_semantic_split"}
    )


def test_quality_independently_rejects_cue_beyond_audio_duration() -> None:
    token = CanonicalToken(
        id="bounded",
        text="學業經歷",
        start_ms=100,
        end_ms=700,
        speaker="guest",
    )
    profile = _profile()
    projection = SubtitleProjection(
        episode_id="anji",
        generation_id="generation-a",
        canonical_content_hash=canonical_content_hash((token,)),
        profile_id=profile.id,
        profile_version=profile.profile_version,
        cues=(
            DisplayCue(
                id="outside-audio",
                token_ids=(token.id,),
                start_ms=0,
                end_ms=1_200,
                lines=(token.text,),
            ),
        ),
    )

    report = evaluate_projection(
        (token,),
        (_hard_unit("bounded", (token,)),),
        projection,
        profile,
        audio_start_ms=0,
        audio_end_ms=1_000,
    )

    assert not report.passed
    assert "cue_timing_out_of_bounds" in {finding.code for finding in report.findings}


def test_quality_rejects_canonical_timing_beyond_audio_duration() -> None:
    token = CanonicalToken(
        id="bad-canonical-clock",
        text="學業",
        start_ms=900,
        end_ms=1_100,
        speaker="guest",
    )
    profile = _profile(min_cue_duration_ms=100)
    projection = SubtitleProjection(
        episode_id="anji",
        generation_id="generation-a",
        canonical_content_hash=canonical_content_hash((token,)),
        profile_id=profile.id,
        profile_version=profile.profile_version,
        cues=(
            DisplayCue(
                id="bad-canonical-clock",
                token_ids=(token.id,),
                start_ms=900,
                end_ms=1_100,
                lines=(token.text,),
            ),
        ),
    )

    report = evaluate_projection(
        (token,),
        (),
        projection,
        profile,
        audio_start_ms=0,
        audio_end_ms=1_000,
    )

    codes = {finding.code for finding in report.findings}
    assert {"canonical_timing_out_of_bounds", "cue_timing_out_of_bounds"}.issubset(codes)


@pytest.mark.parametrize(
    ("lexemes", "profile"),
    (
        (
            ("甲乙丙丁戊己庚辛壬", "癸子丑寅卯辰巳午未申"),
            _profile(id="16x9", max_lines=2, target_line_width=13, hard_line_width=18),
        ),
        (
            ("甲乙丙丁戊己庚", "辛壬癸子丑寅卯"),
            _profile(id="9x16", max_lines=2, target_line_width=9, hard_line_width=13),
        ),
    ),
)
def test_coarse_replacement_wraps_at_lexeme_edge_but_never_crosses_cue(
    lexemes: tuple[str, ...], profile: ProjectionProfile
) -> None:
    tokens, spans = _coarse_phrase(lexemes)
    units = tuple(
        SemanticUnit(
            id=f"unit-{token.id}",
            token_ids=(token.id,),
            kind="token",
            strength=0.0,
        )
        for token in tokens
    )

    result = project_semantic_units(
        tokens,
        units,
        profile,
        episode_id="coarse-wrap",
        generation_id="generation-coarse-wrap",
        audio_end_ms=5000,
        canonical_spans=spans,
    )

    assert len(result.cues) == 1
    assert result.cues[0].lines == lexemes
    assert evaluate_projection(
        tokens,
        units,
        result.projection,
        profile,
        audio_start_ms=0,
        audio_end_ms=5000,
        canonical_spans=spans,
    ).passed


def test_coarse_replacement_over_single_cue_capacity_needs_alignment() -> None:
    tokens, spans = _coarse_phrase(("一二三四五六七八九十",) * 3, end_ms=4000)
    profile = _profile(max_lines=2, target_line_width=9, hard_line_width=13)

    with pytest.raises(NeedsAlignmentError, match="NeedsAlignment"):
        project_semantic_units(
            tokens,
            (),
            profile,
            episode_id="coarse-long",
            generation_id="generation-coarse-long",
            audio_end_ms=5000,
            canonical_spans=spans,
        )

    malicious = SubtitleProjection(
        episode_id="coarse-long",
        generation_id="generation-coarse-long",
        canonical_content_hash=canonical_content_hash(tokens),
        profile_id=profile.id,
        profile_version=profile.profile_version,
        cues=tuple(
            DisplayCue(
                id=f"cue-{index}",
                token_ids=(token.id,),
                start_ms=(index - 1) * 1000,
                end_ms=index * 1000,
                lines=(token.text,),
            )
            for index, token in enumerate(tokens, start=1)
        ),
    )
    report = evaluate_projection(
        tokens,
        (),
        malicious,
        profile,
        audio_start_ms=0,
        audio_end_ms=5000,
        canonical_spans=spans,
    )
    assert not report.passed
    assert "needs_alignment" in {finding.code for finding in report.findings}


def test_forced_aligned_replacement_may_cross_cues_only_at_exact_boundaries() -> None:
    tokens = tuple(
        CanonicalToken(
            id=f"aligned-{index}",
            text=text,
            start_ms=(index - 1) * 1000,
            end_ms=index * 1000,
            timing_basis="forced_alignment",
            alignment_evidence_ids=("forced-aligner-output",),
            speaker="guest",
            evidence_ids=("reviewed-audio",),
        )
        for index, text in enumerate(("甲乙丙丁", "戊己庚辛", "壬癸子丑"), start=1)
    )
    spans = (
        CanonicalSpan(
            id="aligned-span",
            token_ids=tuple(token.id for token in tokens),
            start_ms=0,
            end_ms=3000,
            alignment="lexeme_exact",
            alignment_evidence_ids=("forced-aligner-output",),
        ),
    )
    profile = _profile(
        max_lines=1,
        target_line_width=4,
        hard_line_width=6,
        min_cue_duration_ms=500,
        max_cue_duration_ms=1500,
    )

    result = project_semantic_units(
        tokens,
        (),
        profile,
        episode_id="forced",
        generation_id="generation-forced",
        audio_end_ms=3000,
        canonical_spans=spans,
    )

    assert len(result.cues) == 3
    assert tuple(cue.token_ids for cue in result.cues) == tuple(
        (token.id,) for token in tokens
    )
