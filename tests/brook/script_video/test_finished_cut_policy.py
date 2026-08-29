from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production import _policy as policy_module
from agents.brook.script_video.finished_cut_production._context import (
    CanonicalSection,
    CueAnchor,
    CutSourceRange,
    EditorialCutContext,
    InMemoryEditorialCutContextResolver,
)
from agents.brook.script_video.finished_cut_production._policy import (
    CutPolicyInput,
    LongV2Policy,
    ShortPolicy,
    StockVideoMetadata,
)


@dataclass(frozen=True, slots=True)
class _Component:
    component_id: str
    event_id: str
    semantic_kind: str
    implementation_kind: str
    lane: str
    display: str
    t0: float
    t1: float
    asset_ref: str | None = None


def _context() -> EditorialCutContext:
    return EditorialCutContext(
        episode_id="episode-001",
        cut_id="value-L02",
        format="long",
        editorial_master_id="master-current",
        tight_cut_id="tight-current",
        duration_sec=500.0,
        source_ranges=(CutSourceRange(100.0, 600.0),),
        cues=(
            CueAnchor("cue-001", "第一句", 0.0, 2.0, "section-01"),
            CueAnchor("cue-002", "第二句", 2.0, 4.5, "section-01"),
            CueAnchor("cue-003", "下一段", 4.5, 6.0, "section-02"),
        ),
        sections=(
            CanonicalSection("section-01", "開場", 0.0),
            CanonicalSection(
                "section-02",
                "下一個完整論點",
                4.5,
                transition_before=True,
                transition_title="下一個完整論點",
            ),
        ),
    )


def test_editorial_cut_context_derives_event_anchor_from_current_cue_ids() -> None:
    context = _context()

    anchor = context.derive_anchor(("cue-001", "cue-002"))

    assert anchor.master_cue_ids == ("cue-001", "cue-002")
    assert anchor.text == "第一句\n第二句"
    assert anchor.text_hash == hashlib.sha256("第一句\n第二句".encode()).hexdigest()
    assert (anchor.t0, anchor.t1, anchor.section_id) == (0.0, 4.5, "section-01")


def test_context_resolver_requires_exact_current_master_and_tight_cut_identity() -> None:
    context = _context()
    resolver = InMemoryEditorialCutContextResolver((context,))

    resolved = resolver.resolve(
        episode_id="episode-001",
        cut_id="value-L02",
        editorial_master_id="master-current",
        tight_cut_id="tight-current",
    )
    stale = resolver.resolve(
        episode_id="episode-001",
        cut_id="value-L02",
        editorial_master_id="master-current",
        tight_cut_id="tight-stale",
    )

    assert resolved is context
    assert stale is None


def test_worker_timing_cannot_enter_the_authoritative_anchor_interface() -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 't0'"):
        _context().derive_anchor(("cue-001",), t0=99.0, t1=100.0)  # type: ignore[call-arg]


def _long_context(duration_sec: float = 540.0) -> EditorialCutContext:
    return EditorialCutContext(
        episode_id="episode-001",
        cut_id="value-L02",
        format="long",
        editorial_master_id="master-current",
        tight_cut_id="tight-current",
        duration_sec=duration_sec,
        source_ranges=(CutSourceRange(100.0, 100.0 + duration_sec),),
        cues=(
            CueAnchor("cue-001", "開場", 0.0, 2.0, "section-01"),
            CueAnchor("cue-002", "第二章", 180.0, 182.0, "section-02"),
            CueAnchor("cue-003", "第三章", 360.0, 362.0, "section-03"),
        ),
        sections=(
            CanonicalSection("section-01", "開場", 0.0),
            CanonicalSection(
                "section-02",
                "第二章",
                180.0,
                transition_before=True,
                transition_title="第二章",
            ),
            CanonicalSection(
                "section-03",
                "第三章",
                360.0,
                transition_before=True,
                transition_title="第三章",
            ),
        ),
    )


def _long_components() -> tuple[_Component, ...]:
    rows = [
        _Component(
            f"stock-{index}",
            f"event-stock-{index}",
            "b_roll",
            "stock_video",
            "b_roll",
            f"Stock {index}",
            t0,
            t0 + 10.0,
            f"asset-stock-{index}",
        )
        for index, t0 in enumerate((0.0, 60.0, 120.0), start=1)
    ]
    rows.extend(
        (
            _Component(
                "chapter-2",
                "event-chapter-2",
                "chapter",
                "fullscreen_transition",
                "fullscreen_transition",
                "第二章",
                180.0,
                183.0,
            ),
            _Component(
                "broll-cadence-180",
                "event-cadence-180",
                "b_roll",
                "photo",
                "b_roll",
                "Asset-backed example",
                180.0,
                190.0,
                "asset-photo-180",
            ),
            _Component(
                "broll-240",
                "event-240",
                "b_roll",
                "non_editorial_clip",
                "b_roll",
                "Concrete example",
                240.0,
                250.0,
                "asset-neutral-240",
            ),
            _Component(
                "broll-300",
                "event-300",
                "b_roll",
                "photo",
                "b_roll",
                "Workplace",
                300.0,
                310.0,
                "asset-photo-300",
            ),
            _Component(
                "chapter-3",
                "event-chapter-3",
                "chapter",
                "fullscreen_transition",
                "fullscreen_transition",
                "第三章",
                360.0,
                363.0,
            ),
            _Component(
                "broll-cadence-360",
                "event-cadence-360",
                "b_roll",
                "non_editorial_clip",
                "b_roll",
                "Asset-backed example",
                360.0,
                370.0,
                "asset-neutral-360",
            ),
            _Component(
                "broll-420",
                "event-420",
                "b_roll",
                "person_inset",
                "b_roll",
                "Expert",
                420.0,
                430.0,
                "asset-person-420",
            ),
            _Component(
                "broll-480",
                "event-480",
                "b_roll",
                "non_editorial_clip",
                "b_roll",
                "Closing example",
                480.0,
                490.0,
                "asset-neutral-480",
            ),
        )
    )
    return tuple(rows)


def _stock_metadata() -> tuple[StockVideoMetadata, ...]:
    return tuple(StockVideoMetadata(f"asset-stock-{index}", 1920, 1080) for index in range(1, 4))


def _long_input() -> CutPolicyInput:
    return CutPolicyInput(_long_context(), _long_components(), _stock_metadata())


def _components_with_implementation_duration(
    implementation_kind: str,
    duration_sec: float,
) -> tuple[_Component, ...]:
    existing_component_id = {
        "fullscreen_transition": "chapter-2",
        "stock_video": "stock-1",
        "photo": "broll-cadence-180",
        "non_editorial_clip": "broll-cadence-360",
        "person_inset": "broll-420",
    }.get(implementation_kind)
    if existing_component_id is not None:
        return tuple(
            replace(
                component,
                t1=component.t0 + duration_sec,
                asset_ref=component.asset_ref or f"asset-built-{component.component_id}",
            )
            if component.component_id == existing_component_id
            else component
            for component in _long_components()
        )
    semantic_kind = implementation_kind
    return (
        *_long_components(),
        _Component(
            f"built-{implementation_kind}",
            f"event-built-{implementation_kind}",
            semantic_kind,
            implementation_kind,
            semantic_kind,
            "完整命題",
            30.0,
            30.0 + duration_sec,
            f"asset-built-{implementation_kind}",
        ),
    )


def test_true_nine_minute_l2_density_with_sections_and_landscape_stock_passes() -> None:
    decision = LongV2Policy().validate(_long_input())

    assert decision.status == "accepted"
    assert decision.diagnostics == ()


def test_four_minute_nineteen_second_long_needs_review() -> None:
    candidate = replace(_long_input(), context=_long_context(259.0))

    decision = LongV2Policy().validate(candidate)

    assert decision.status == "needs_review"
    assert "long_duration_below_minimum" in {diagnostic.code for diagnostic in decision.diagnostics}


def test_long_cut_duration_must_equal_selected_source_range_sum() -> None:
    context = replace(_long_context(), source_ranges=(CutSourceRange(100.0, 600.0),))

    decision = LongV2Policy().validate(replace(_long_input(), context=context))

    assert decision.status == "needs_review"
    assert "source_range_sum_mismatch" in {diagnostic.code for diagnostic in decision.diagnostics}


def test_long_without_canonical_sections_needs_review() -> None:
    context = replace(_long_context(), sections=())

    decision = LongV2Policy().validate(replace(_long_input(), context=context))

    assert decision.status == "needs_review"
    assert "canonical_sections_missing" in {diagnostic.code for diagnostic in decision.diagnostics}


def test_first_canonical_section_must_start_at_zero() -> None:
    context = _long_context()
    shifted_first = replace(context.sections[0], t0=1.0)
    context = replace(context, sections=(shifted_first, *context.sections[1:]))

    decision = LongV2Policy().validate(replace(_long_input(), context=context))

    assert decision.status == "needs_review"
    assert "first_section_not_zero" in {diagnostic.code for diagnostic in decision.diagnostics}


def test_chapter_transition_projection_drift_needs_review() -> None:
    components = tuple(
        replace(component, t0=181.0) if component.component_id == "chapter-2" else component
        for component in _long_components()
    )

    decision = LongV2Policy().validate(replace(_long_input(), components=components))

    assert decision.status == "needs_review"
    assert "chapter_transition_projection_mismatch" in {
        diagnostic.code for diagnostic in decision.diagnostics
    }


def test_already_built_oversized_chapter_placement_needs_review() -> None:
    components = tuple(
        replace(
            component,
            t1=184.001,
            asset_ref="asset-chapter-built",
        )
        if component.component_id == "chapter-2"
        else component
        for component in _long_components()
    )

    decision = LongV2Policy().validate(replace(_long_input(), components=components))

    assert decision.status == "needs_review"
    assert decision.diagnostics[0].code == "visual_placement_duration_exceeded"
    assert decision.diagnostics[0].component_ids == ("chapter-2",)


@pytest.mark.parametrize(
    ("implementation_kind", "maximum_sec"),
    (
        ("fullscreen_transition", 4.0),
        ("hero_title", 8.0),
        ("identity_card", 8.0),
        ("stock_video", 12.0),
        ("photo", 12.0),
        ("non_editorial_clip", 12.0),
        ("person_inset", 12.0),
    ),
)
def test_already_built_component_over_duration_ceiling_needs_review(
    implementation_kind: str,
    maximum_sec: float,
) -> None:
    components = _components_with_implementation_duration(
        implementation_kind,
        maximum_sec + 0.001,
    )

    decision = LongV2Policy().validate(replace(_long_input(), components=components))

    assert decision.status == "needs_review"
    assert decision.diagnostics[0].code == "visual_placement_duration_exceeded"


@pytest.mark.parametrize(
    ("implementation_kind", "maximum_sec"),
    (
        ("fullscreen_transition", 4.0),
        ("hero_title", 8.0),
        ("identity_card", 8.0),
        ("stock_video", 12.0),
        ("photo", 12.0),
        ("non_editorial_clip", 12.0),
        ("person_inset", 12.0),
    ),
)
def test_component_at_exact_duration_ceiling_is_allowed(
    implementation_kind: str,
    maximum_sec: float,
) -> None:
    components = _components_with_implementation_duration(
        implementation_kind,
        maximum_sec,
    )

    decision = LongV2Policy().validate(replace(_long_input(), components=components))

    assert decision.status == "accepted"


def _title_component(
    component_id: str,
    t0: float,
    *,
    semantic_kind: str = "hero_title",
) -> _Component:
    # A chapter is title-like without consuming the hero limit, which is the role
    # the retired supporting_title used to fill in these fixtures.
    implementation_kind = "fullscreen_transition" if semantic_kind == "chapter" else semantic_kind
    return _Component(
        component_id,
        f"event-{component_id}",
        semantic_kind,
        implementation_kind,
        implementation_kind,
        component_id,
        t0,
        t0 + 3.0,
    )


def test_five_hero_titles_need_review() -> None:
    heroes = tuple(
        _title_component(f"hero-{index}", t0)
        for index, t0 in enumerate((30.0, 150.0, 270.0, 390.0, 510.0), start=1)
    )

    decision = LongV2Policy().validate(
        replace(_long_input(), components=(*_long_components(), *heroes))
    )

    assert decision.status == "needs_review"
    assert "hero_title_limit_exceeded" in {diagnostic.code for diagnostic in decision.diagnostics}


def test_chapters_do_not_consume_the_hero_title_limit() -> None:
    context = _chaptered_context(8, 540.0)
    chapters = _chapters_for(context)
    heroes = tuple(
        _title_component(f"hero-{index}", t0)
        for index, t0 in enumerate((300.0, 350.0, 400.0, 450.0), start=1)
    )
    stock_only = tuple(
        component for component in _long_components() if component.lane != "fullscreen_transition"
    )

    decision = LongV2Policy().validate(
        replace(
            _long_input(),
            context=context,
            components=(*stock_only, *chapters, *heroes),
        )
    )

    assert decision.status == "accepted"
    assert "hero_title_limit_exceeded" not in {
        diagnostic.code for diagnostic in decision.diagnostics
    }


def _chaptered_context(transition_count: int, duration_sec: float) -> EditorialCutContext:
    """A context whose canonical transitions a legal chapter set can map onto.

    Chapters are section-bound, so density can only be built from chapters that
    actually match canonical transitions plus Hero Titles within their limit.
    """
    sections = [CanonicalSection("section-01", "開場", 0.0)]
    cues = [CueAnchor("cue-001", "開場", 0.0, 2.0, "section-01")]
    for index in range(1, transition_count + 1):
        section_id = f"section-{index + 1:02d}"
        title = f"第{index + 1}章"
        t0 = 30.0 * index
        sections.append(
            CanonicalSection(section_id, title, t0, transition_before=True, transition_title=title)
        )
        cues.append(CueAnchor(f"cue-{index + 1:03d}", title, t0, t0 + 2.0, section_id))
    return replace(
        _long_context(duration_sec),
        sections=tuple(sections),
        cues=tuple(cues),
    )


def _chapters_for(context: EditorialCutContext) -> tuple[_Component, ...]:
    return tuple(
        _Component(
            f"chapter-{section.section_id}",
            f"event-{section.section_id}",
            "chapter",
            "fullscreen_transition",
            "fullscreen_transition",
            section.transition_title,
            section.t0,
            section.t0 + 3.0,
        )
        for section in context.sections[1:]
        if section.transition_before
    )


def test_l3_title_like_density_near_three_point_six_per_minute_needs_review() -> None:
    context = _chaptered_context(14, 510.0)
    chapters = _chapters_for(context)
    heroes = tuple(
        _title_component(f"hero-{index}", 400.0 + index * 20.0)
        for index in range(1, 5)
    )
    # 14 chapters + 4 Hero Titles over 8.5 minutes is 2.12 cards per minute,
    # just past the two-per-minute ceiling, with the Hero limit still respected.
    stock_only = tuple(
        component for component in _long_components() if component.lane != "fullscreen_transition"
    )
    candidate = replace(
        _long_input(),
        context=context,
        components=(*stock_only, *chapters, *heroes),
    )

    decision = LongV2Policy().validate(candidate)

    assert decision.status == "needs_review"
    assert "title_like_density_exceeded" in {diagnostic.code for diagnostic in decision.diagnostics}


def test_three_title_cards_inside_fifteen_seconds_need_review() -> None:
    clustered = tuple(
        _title_component(f"cluster-{index}", t0)
        for index, t0 in enumerate((30.0, 38.0, 45.0), start=1)
    )

    decision = LongV2Policy().validate(
        replace(_long_input(), components=(*_long_components(), *clustered))
    )

    assert decision.status == "needs_review"
    assert "title_cluster_exceeded" in {diagnostic.code for diagnostic in decision.diagnostics}


def test_title_like_visual_placements_must_not_overlap() -> None:
    overlapping_hero = _title_component("overlapping-hero", 180.0)

    decision = LongV2Policy().validate(
        replace(_long_input(), components=(*_long_components(), overlapping_hero))
    )

    assert decision.status == "needs_review"
    assert decision.diagnostics == (
        policy_module.PolicyDiagnostic(
            "title_placement_overlap",
            (
                "Title-like visual placements overlap: chapter-2 [180.000000, 183.000000]s; "
                "overlapping-hero [180.000000, 183.000000]s"
            ),
            component_ids=("chapter-2", "overlapping-hero"),
        ),
    )


def test_l2_observed_one_hundred_thirty_five_second_visual_gap_needs_review() -> None:
    components = tuple(
        replace(component, t0=145.334, t1=155.334)
        if component.component_id == "stock-2"
        else replace(component, t0=210.0, t1=220.0)
        if component.component_id == "stock-3"
        else component
        for component in _long_components()
    )
    structural = (
        _Component(
            "namecard",
            "event-namecard",
            "identity_card",
            "identity_card",
            "identity_card",
            "Guest",
            40.0,
            45.0,
        ),
        _Component(
            "badge",
            "event-badge",
            "visual_effect",
            "visual_effect",
            "visual_effect",
            "Badge",
            70.0,
            75.0,
        ),
        _Component(
            "camera-correction",
            "event-camera-correction",
            "b_roll",
            "camera_correction",
            "b_roll",
            "Camera correction",
            100.0,
            105.0,
        ),
    )

    decision = LongV2Policy().validate(
        replace(_long_input(), components=(*components, *structural))
    )

    assert decision.status == "needs_review"
    assert decision.diagnostics == (
        policy_module.PolicyDiagnostic(
            "visual_gap_exceeded",
            (
                "Long non-structural visual gap [10.000000, 145.334000]s exceeds 75 seconds; "
                "previous_event_id=event-stock-1; next_event_id=event-stock-2"
            ),
            component_ids=("stock-1", "stock-2"),
        ),
    )


def test_title_cards_cannot_mask_an_asset_backed_broll_cadence_gap() -> None:
    gap_components = tuple(
        component
        for component in _long_components()
        if component.component_id not in {"broll-cadence-180", "broll-cadence-360"}
    )

    decision = LongV2Policy().validate(replace(_long_input(), components=gap_components))

    assert decision.status == "needs_review"
    assert decision.diagnostics == (
        policy_module.PolicyDiagnostic(
            "b_roll_cadence_gap_exceeded",
            (
                "Long asset-backed B-roll cadence gap [130.000000, 240.000000]s exceeds "
                "75 seconds; previous_event_id=event-stock-3; next_event_id=event-240"
            ),
            component_ids=("stock-3", "broll-240"),
        ),
    )


def test_sixty_second_semantic_anchor_with_four_second_title_cannot_mask_broll_gap() -> None:
    context = replace(
        _long_context(),
        cues=(
            CueAnchor("cue-semantic", "完整語意證據", 0.0, 56.0, "section-01"),
            CueAnchor("cue-placement", "四秒視覺位置", 56.0, 60.0, "section-01"),
            CueAnchor("cue-002", "第二章", 180.0, 182.0, "section-02"),
            CueAnchor("cue-003", "第三章", 360.0, 362.0, "section-03"),
        ),
    )
    semantic_cue_ids = ("cue-semantic", "cue-placement")
    semantic_anchor = context.derive_anchor(semantic_cue_ids)
    placement = context.derive_visual_placement(
        semantic_cue_ids=semantic_cue_ids,
        placement_cue_ids=("cue-placement",),
        semantic_kind="hero_title",
    )
    hero = replace(
        _title_component("semantic-long-placement-short", placement.t0),
        t1=placement.t1,
    )
    gap_components = tuple(
        component
        for component in _long_components()
        if component.component_id not in {"broll-cadence-180", "broll-cadence-360"}
    )

    decision = LongV2Policy().validate(
        replace(
            _long_input(),
            context=context,
            components=(*gap_components, hero),
        )
    )

    assert semantic_anchor.t1 - semantic_anchor.t0 == 60.0
    assert placement.t1 - placement.t0 == 4.0
    assert decision.diagnostics[0].code == "b_roll_cadence_gap_exceeded"
    assert "visual_placement_duration_exceeded" not in {
        diagnostic.code for diagnostic in decision.diagnostics
    }


def test_observed_long3_head_broll_gap_needs_review_at_exact_boundary() -> None:
    components = tuple(
        replace(component, t0=148.333, t1=158.333)
        if component.component_id == "stock-1"
        else replace(component, t0=210.0, t1=220.0)
        if component.component_id == "stock-2"
        else replace(component, t0=270.0, t1=280.0)
        if component.component_id == "stock-3"
        else component
        for component in _long_components()
    )
    title_fillers = (
        _title_component("head-support-30", 30.0),
        _title_component("head-support-90", 90.0),
    )

    decision = LongV2Policy().validate(
        replace(_long_input(), components=(*components, *title_fillers))
    )

    assert decision.diagnostics == (
        policy_module.PolicyDiagnostic(
            "b_roll_cadence_gap_exceeded",
            (
                "Long asset-backed B-roll cadence gap [0.000000, 148.333000]s exceeds "
                "75 seconds; previous_event_id=cut_start; next_event_id=event-stock-1"
            ),
            component_ids=("stock-1",),
        ),
    )


def test_observed_long3_internal_broll_gap_needs_review_at_exact_boundary() -> None:
    components = tuple(
        replace(component, t0=134.100, t1=144.100)
        if component.component_id == "stock-2"
        else replace(component, t0=220.0, t1=230.0)
        if component.component_id == "stock-3"
        else component
        for component in _long_components()
    )
    title_fillers = (
        _title_component("gap-support-60", 60.0),
        _title_component("gap-support-120", 120.0),
    )

    decision = LongV2Policy().validate(
        replace(_long_input(), components=(*components, *title_fillers))
    )

    assert decision.diagnostics == (
        policy_module.PolicyDiagnostic(
            "b_roll_cadence_gap_exceeded",
            (
                "Long asset-backed B-roll cadence gap [10.000000, 134.100000]s exceeds "
                "75 seconds; previous_event_id=event-stock-1; next_event_id=event-stock-2"
            ),
            component_ids=("stock-1", "stock-2"),
        ),
    )


def test_asset_backed_broll_gap_of_exactly_seventy_five_seconds_passes() -> None:
    components = tuple(
        replace(component, t0=85.0, t1=95.0) if component.component_id == "stock-2" else component
        for component in _long_components()
    )

    decision = LongV2Policy().validate(replace(_long_input(), components=components))

    assert decision.status == "accepted"


def test_tail_visual_gap_reports_previous_event_and_cut_end() -> None:
    components = tuple(
        component for component in _long_components() if component.component_id != "broll-480"
    )

    decision = LongV2Policy().validate(replace(_long_input(), components=components))

    assert decision.diagnostics == (
        policy_module.PolicyDiagnostic(
            "visual_gap_exceeded",
            (
                "Long non-structural visual gap [430.000000, 540.000000]s exceeds 75 seconds; "
                "previous_event_id=event-420; next_event_id=cut_end"
            ),
            component_ids=("broll-420",),
        ),
    )


def test_nested_broll_window_cannot_move_coverage_end_backward() -> None:
    components = tuple(
        replace(component, t1=252.0)
        if component.component_id == "broll-240"
        else replace(component, t0=241.0, t1=242.0)
        if component.component_id == "broll-300"
        else replace(component, t0=326.0, t1=336.0)
        if component.component_id == "broll-cadence-360"
        else replace(component, t0=400.0, t1=410.0)
        if component.component_id == "broll-420"
        else component
        for component in _long_components()
    )

    decision = LongV2Policy().validate(replace(_long_input(), components=components))

    assert decision.status == "accepted"


def test_intentional_aroll_does_not_count_as_broll_cadence_coverage() -> None:
    gap_components = tuple(
        component
        for component in _long_components()
        if component.component_id not in {"broll-cadence-180", "broll-cadence-360"}
    )
    intentional_aroll = _Component(
        "intentional-aroll",
        "event-intentional-aroll",
        "b_roll",
        "intentional_aroll",
        "b_roll",
        "Keep the speaker visible",
        150.0,
        230.0,
    )

    decision = LongV2Policy().validate(
        replace(_long_input(), components=(*gap_components, intentional_aroll))
    )

    assert decision.diagnostics[0].code == "b_roll_cadence_gap_exceeded"
    assert decision.diagnostics[0].component_ids == ("stock-3", "broll-240")


def test_long_requires_three_distinct_asset_backed_stock_video_events() -> None:
    components = tuple(
        replace(
            component,
            semantic_kind="b_roll",
            implementation_kind="photo",
            lane="b_roll",
            asset_ref="asset-photo-not-stock",
        )
        if component.component_id == "stock-3"
        else component
        for component in _long_components()
    )
    non_stock_cards = (
        _Component(
            "identity-not-stock",
            "event-identity",
            "identity_card",
            "identity_card",
            "identity_card",
            "Guest",
            200.0,
            205.0,
            "asset-identity-not-stock",
        ),
        _Component(
            "badge-not-stock",
            "event-badge",
            "visual_effect",
            "visual_effect",
            "visual_effect",
            "Badge",
            205.0,
            210.0,
            "asset-badge-not-stock",
        ),
    )

    decision = LongV2Policy().validate(
        replace(_long_input(), components=(*components, *non_stock_cards))
    )

    assert decision.status == "needs_review"
    assert "distinct_stock_video_minimum_not_met" in {
        diagnostic.code for diagnostic in decision.diagnostics
    }


def test_three_stock_assets_on_one_event_cannot_satisfy_distinct_event_minimum() -> None:
    components = tuple(
        replace(component, event_id="event-shared-stock")
        if component.implementation_kind == "stock_video"
        else component
        for component in _long_components()
    )

    decision = LongV2Policy().validate(replace(_long_input(), components=components))

    assert decision.status == "needs_review"
    assert "distinct_stock_video_minimum_not_met" in {
        diagnostic.code for diagnostic in decision.diagnostics
    }


def test_the_same_stock_asset_twice_in_one_cut_needs_review() -> None:
    """修修 2026-08-29 在 long3 抓到的：同一支素材出現在 185.7s 與 376.2s。

    重點是這支片的**不同素材數量仍然達標**——long3 有 8 個 b-roll、7 支不同素材，
    「至少三支不同」那條完全攔不住它。所以這裡刻意保留三支不同的 stock，只把第四
    個 b-roll 換成重複使用 asset-stock-1。
    """
    components = tuple(
        replace(component, implementation_kind="stock_video", asset_ref="asset-stock-1")
        if component.component_id == "broll-300"
        else component
        for component in _long_components()
    )

    decision = LongV2Policy().validate(replace(_long_input(), components=components))

    assert decision.status == "needs_review"
    diagnostic = next(d for d in decision.diagnostics if d.code == "stock_video_asset_reused")
    assert diagnostic.asset_refs == ("asset-stock-1",)
    assert set(diagnostic.component_ids) == {"stock-1", "broll-300"}


def test_distinct_stock_assets_do_not_trip_the_reuse_rule() -> None:
    decision = LongV2Policy().validate(_long_input())

    assert "stock_video_asset_reused" not in {d.code for d in decision.diagnostics}


def test_vertical_native_stock_video_needs_review() -> None:
    metadata = tuple(
        replace(row, native_width=1080, native_height=1920)
        if row.asset_ref == "asset-stock-2"
        else row
        for row in _stock_metadata()
    )

    decision = LongV2Policy().validate(replace(_long_input(), stock_video_metadata=metadata))

    assert decision.status == "needs_review"
    assert "stock_video_not_native_landscape" in {
        diagnostic.code for diagnostic in decision.diagnostics
    }


def _short_input(duration_sec: float = 45.0) -> CutPolicyInput:
    context = EditorialCutContext(
        episode_id="episode-001",
        cut_id="short-K01",
        format="short",
        editorial_master_id="master-current",
        tight_cut_id="tight-short-current",
        duration_sec=duration_sec,
        source_ranges=(CutSourceRange(200.0, 200.0 + duration_sec),),
        cues=(CueAnchor("cue-short", "短片", 0.0, 2.0),),
        sections=(),
    )
    vertical_stock = _Component(
        "short-stock",
        "event-short-stock",
        "b_roll",
        "stock_video",
        "b_roll",
        "Short vertical stock",
        5.0,
        10.0,
        "asset-short-vertical",
    )
    return CutPolicyInput(
        context,
        (vertical_stock,),
        (StockVideoMetadata("asset-short-vertical", 1080, 1920),),
    )


def test_short_uses_its_own_rules_without_long_fallback() -> None:
    decision = ShortPolicy().validate(_short_input())

    assert decision.status == "accepted"
    assert decision.diagnostics == ()


def test_short_over_sixty_seconds_needs_review_under_short_rules() -> None:
    decision = ShortPolicy().validate(_short_input(61.0))

    assert decision.status == "needs_review"
    assert "short_duration_exceeded" in {diagnostic.code for diagnostic in decision.diagnostics}


def test_short_uses_a_fixed_two_title_limit_not_long_per_minute_density() -> None:
    titles = tuple(
        _title_component(f"short-title-{index}", t0)
        for index, t0 in enumerate((1.0, 12.0, 24.0), start=1)
    )
    candidate = replace(_short_input(), components=(*_short_input().components, *titles))

    decision = ShortPolicy().validate(candidate)

    assert decision.status == "needs_review"
    assert "short_title_limit_exceeded" in {diagnostic.code for diagnostic in decision.diagnostics}


def test_bad_plan_validation_is_deterministic_and_has_no_semantic_retry_call() -> None:
    policy = LongV2Policy()
    candidate = replace(_long_input(), context=_long_context(259.0))

    first = policy.validate(candidate)
    second = policy.validate(candidate)

    source = Path(policy_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    calls.update(
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    )
    assert first == second
    assert first.status == "needs_review"
    assert {"proposal_for", "advance", "request_revision"}.isdisjoint(calls)
    assert "SemanticAdapter" not in source
