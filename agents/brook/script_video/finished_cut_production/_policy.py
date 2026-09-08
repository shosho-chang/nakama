"""Format-specific, deterministic gates before Finished Cut materialization."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal, Protocol

from ._context import EditorialCutContext
from ._derived_assets import _PLACEMENT_DURATION_CEILINGS_SEC

PolicyStatus = Literal["accepted", "needs_review"]
PolicyDiagnosticCode = Literal[
    "long_duration_below_minimum",
    "source_range_sum_mismatch",
    "canonical_sections_missing",
    "first_section_not_zero",
    "chapter_transition_projection_mismatch",
    "visual_placement_duration_exceeded",
    "hero_title_limit_exceeded",
    "title_like_density_exceeded",
    "title_placement_overlap",
    "title_cluster_exceeded",
    "distinct_stock_video_minimum_not_met",
    "stock_video_asset_reused",
    "stock_video_metadata_missing",
    "stock_video_not_native_landscape",
    "b_roll_cadence_gap_exceeded",
    "visual_gap_exceeded",
    "short_duration_exceeded",
    "short_title_limit_exceeded",
]
LONG_MIN_DURATION_SEC = 8 * 60.0
CUT_DURATION_TOLERANCE_SEC = 0.05
SECTION_TIMESTAMP_TOLERANCE_SEC = 0.05
LONG_MAX_HERO_TITLES = 4
LONG_MAX_TITLE_LIKE_PER_MINUTE = 2.0
LONG_TITLE_CLUSTER_WINDOW_SEC = 15.0
LONG_TITLE_CLUSTER_MAX_CARDS = 2
LONG_MAX_NONSTRUCTURAL_VISUAL_GAP_SEC = 75.0
LONG_MAX_ASSET_BACKED_BROLL_GAP_SEC = 75.0
LONG_MIN_DISTINCT_STOCK_VIDEO_EVENTS = 3
SHORT_MAX_DURATION_SEC = 60.0
SHORT_MAX_TITLE_LIKE_CARDS = 2
TITLE_LIKE_LANES = frozenset({"hero_title", "fullscreen_transition"})
VISUAL_COVERAGE_BROLL_IMPLEMENTATIONS = frozenset(
    {"stock_video", "photo", "non_editorial_clip", "person_inset"}
)


class PolicyComponent(Protocol):
    component_id: str
    event_id: str
    semantic_kind: str
    implementation_kind: str
    lane: str
    display: str
    t0: float
    t1: float
    asset_ref: str | None


@dataclass(frozen=True, slots=True)
class StockVideoMetadata:
    asset_ref: str
    native_width: int
    native_height: int


@dataclass(frozen=True, slots=True)
class CutPolicyInput:
    context: EditorialCutContext
    components: tuple[PolicyComponent, ...]
    stock_video_metadata: tuple[StockVideoMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicyDiagnostic:
    code: PolicyDiagnosticCode
    message: str
    component_ids: tuple[str, ...] = ()
    section_ids: tuple[str, ...] = ()
    asset_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    status: PolicyStatus
    diagnostics: tuple[PolicyDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class _CoverageGap:
    t0: float
    t1: float
    previous: PolicyComponent | None
    next: PolicyComponent | None


def _chapter_projection_mismatch(
    sections: "tuple[object, ...]",
    expected: "tuple[object, ...]",
    projected: "tuple[PolicyComponent, ...]",
) -> PolicyDiagnostic | None:
    """說出章節轉場到底是哪裡對不上，而不是只說「對不上」。

    這個 gate 以前把四種完全不同的違規擠進同一句 "must map one-to-one"：第一段自帶
    轉場、數量對不上、Director 把 canonical 標題改寫掉、落點漂掉。2026-09-08 蘇予昕
    那一集，Director 把「情緒像粽子，主管底下是一整串」改寫成「情緒像繩子」，gate 確實
    擋下來了，但訊息看起來像數量或落點問題，沒有人看得出是文字被改，於是繞過 gate 直接
    鋪 timeline——錯字就這樣上了片。訊息要能直接指出「期待 X、拿到 Y」。
    """
    if sections[0].transition_before is not False:
        return PolicyDiagnostic(
            "chapter_transition_projection_mismatch",
            "The first canonical section must not carry a chapter transition",
            section_ids=(sections[0].section_id,),
        )
    if len(expected) != len(projected):
        return PolicyDiagnostic(
            "chapter_transition_projection_mismatch",
            (
                f"The canonical section map declares {len(expected)} chapter transitions "
                f"but the cut projects {len(projected)}"
            ),
            component_ids=tuple(component.component_id for component in projected),
            section_ids=tuple(section.section_id for section in expected),
        )
    for section, component in zip(expected, projected, strict=True):
        if (
            component.semantic_kind != "chapter"
            or component.implementation_kind != "fullscreen_transition"
        ):
            return PolicyDiagnostic(
                "chapter_transition_projection_mismatch",
                (
                    f"{section.section_id} must project as chapter/fullscreen_transition; "
                    f"got {component.semantic_kind}/{component.implementation_kind}"
                ),
                component_ids=(component.component_id,),
                section_ids=(section.section_id,),
            )
        if section.transition_title is None:
            return PolicyDiagnostic(
                "chapter_transition_projection_mismatch",
                f"{section.section_id} projects a chapter transition without a canonical title",
                component_ids=(component.component_id,),
                section_ids=(section.section_id,),
            )
        if component.display != section.transition_title:
            return PolicyDiagnostic(
                "chapter_transition_projection_mismatch",
                (
                    "Chapter transition text must repeat the canonical title verbatim; "
                    f"{section.section_id} expects {section.transition_title!r} "
                    f"but the cut carries {component.display!r}"
                ),
                component_ids=(component.component_id,),
                section_ids=(section.section_id,),
            )
        if abs(component.t0 - section.t0) > SECTION_TIMESTAMP_TOLERANCE_SEC:
            return PolicyDiagnostic(
                "chapter_transition_projection_mismatch",
                (
                    f"{section.section_id} starts at {section.t0:.3f}s but its chapter "
                    f"transition lands at {component.t0:.3f}s"
                ),
                component_ids=(component.component_id,),
                section_ids=(section.section_id,),
            )
    return None


class FormatPolicy(Protocol):
    def validate(self, candidate: CutPolicyInput) -> PolicyDecision: ...


class LongV2Policy:
    """Long-only production policy; it never delegates to Short policy."""

    def validate(self, candidate: CutPolicyInput) -> PolicyDecision:
        selected_duration = sum(
            source_range.t1 - source_range.t0 for source_range in candidate.context.source_ranges
        )
        if (
            candidate.context.duration_sec < LONG_MIN_DURATION_SEC
            or selected_duration < LONG_MIN_DURATION_SEC
        ):
            return PolicyDecision(
                "needs_review",
                (
                    PolicyDiagnostic(
                        "long_duration_below_minimum",
                        "Long selected ranges and cut duration must both be at least 480 seconds",
                    ),
                ),
            )
        if abs(candidate.context.duration_sec - selected_duration) > CUT_DURATION_TOLERANCE_SEC:
            return PolicyDecision(
                "needs_review",
                (
                    PolicyDiagnostic(
                        "source_range_sum_mismatch",
                        "Cut duration must equal the selected source-range sum",
                    ),
                ),
            )
        if not candidate.context.sections:
            return PolicyDecision(
                "needs_review",
                (
                    PolicyDiagnostic(
                        "canonical_sections_missing",
                        "Long requires a non-empty canonical section map",
                    ),
                ),
            )
        if abs(candidate.context.sections[0].t0) > SECTION_TIMESTAMP_TOLERANCE_SEC:
            return PolicyDecision(
                "needs_review",
                (
                    PolicyDiagnostic(
                        "first_section_not_zero",
                        "The first canonical section must start at 00:00",
                        section_ids=(candidate.context.sections[0].section_id,),
                    ),
                ),
            )
        expected_transitions = tuple(
            section
            for index, section in enumerate(candidate.context.sections)
            if index > 0 and section.transition_before
        )
        projected_transitions = tuple(
            component
            for component in candidate.components
            if component.lane == "fullscreen_transition"
        )
        chapter_mismatch = _chapter_projection_mismatch(
            candidate.context.sections, expected_transitions, projected_transitions
        )
        if chapter_mismatch is not None:
            return PolicyDecision("needs_review", (chapter_mismatch,))
        for component in sorted(
            candidate.components,
            key=lambda row: (row.t0, row.t1, row.component_id),
        ):
            ceiling_sec = _PLACEMENT_DURATION_CEILINGS_SEC.get(component.implementation_kind)
            show_sec = component.t1 - component.t0
            if ceiling_sec is not None and show_sec > ceiling_sec:
                return PolicyDecision(
                    "needs_review",
                    (
                        PolicyDiagnostic(
                            "visual_placement_duration_exceeded",
                            (
                                f"Long {component.implementation_kind} visual placement lasts "
                                f"{show_sec:.6f} seconds; maximum is {ceiling_sec:.6f} seconds"
                            ),
                            component_ids=(component.component_id,),
                        ),
                    ),
                )
        hero_titles = tuple(
            component
            for component in candidate.components
            if component.semantic_kind == "hero_title"
        )
        if len(hero_titles) > LONG_MAX_HERO_TITLES:
            return PolicyDecision(
                "needs_review",
                (
                    PolicyDiagnostic(
                        "hero_title_limit_exceeded",
                        "Long permits at most four Hero Titles",
                        component_ids=tuple(component.component_id for component in hero_titles),
                    ),
                ),
            )
        title_like = tuple(
            component for component in candidate.components if component.lane in TITLE_LIKE_LANES
        )
        title_density = len(title_like) / (candidate.context.duration_sec / 60.0)
        if title_density > LONG_MAX_TITLE_LIKE_PER_MINUTE:
            return PolicyDecision(
                "needs_review",
                (
                    PolicyDiagnostic(
                        "title_like_density_exceeded",
                        "Long title-like density must not exceed two cards per minute",
                        component_ids=tuple(component.component_id for component in title_like),
                    ),
                ),
            )
        ordered_titles = tuple(
            sorted(
                title_like,
                key=lambda component: (component.t0, component.t1, component.component_id),
            )
        )
        for previous, current in zip(ordered_titles, ordered_titles[1:], strict=False):
            if current.t0 < previous.t1:
                return PolicyDecision(
                    "needs_review",
                    (
                        PolicyDiagnostic(
                            "title_placement_overlap",
                            (
                                "Title-like visual placements overlap: "
                                f"{previous.component_id} "
                                f"[{previous.t0:.6f}, {previous.t1:.6f}]s; "
                                f"{current.component_id} "
                                f"[{current.t0:.6f}, {current.t1:.6f}]s"
                            ),
                            component_ids=(
                                previous.component_id,
                                current.component_id,
                            ),
                        ),
                    ),
                )
        for index in range(len(ordered_titles) - LONG_TITLE_CLUSTER_MAX_CARDS):
            cluster = ordered_titles[index : index + LONG_TITLE_CLUSTER_MAX_CARDS + 1]
            if cluster[-1].t0 - cluster[0].t0 <= LONG_TITLE_CLUSTER_WINDOW_SEC:
                return PolicyDecision(
                    "needs_review",
                    (
                        PolicyDiagnostic(
                            "title_cluster_exceeded",
                            "Long permits at most two title-like cards in any 15 seconds",
                            component_ids=tuple(component.component_id for component in cluster),
                        ),
                    ),
                )
        stock_video_components = tuple(
            component
            for component in candidate.components
            if component.semantic_kind == "b_roll"
            and component.implementation_kind == "stock_video"
            and component.lane == "b_roll"
        )
        asset_backed_stock_components = tuple(
            component for component in stock_video_components if component.asset_ref
        )
        distinct_stock_event_ids = frozenset(
            component.event_id for component in asset_backed_stock_components
        )
        distinct_stock_assets = frozenset(
            component.asset_ref for component in asset_backed_stock_components
        )
        if (
            len(distinct_stock_event_ids) < LONG_MIN_DISTINCT_STOCK_VIDEO_EVENTS
            or len(distinct_stock_assets) < LONG_MIN_DISTINCT_STOCK_VIDEO_EVENTS
        ):
            return PolicyDecision(
                "needs_review",
                (
                    PolicyDiagnostic(
                        "distinct_stock_video_minimum_not_met",
                        "Long requires three distinct asset-backed Stock Video events",
                        component_ids=tuple(
                            component.component_id for component in stock_video_components
                        ),
                        asset_refs=tuple(sorted(distinct_stock_assets)),
                    ),
                ),
            )
        # 修修 2026-08-29 在 long3 的 finished review 抓到：同一支 stock 素材在一支
        # 八分鐘的片裡用了兩次（185.7s 與 376.2s 都是 …a81599624c7bd39e）。上面那條
        # 只管「至少三支不同」，重複用完全不擋。
        reused_assets = tuple(
            sorted(
                asset
                for asset, count in Counter(
                    component.asset_ref for component in asset_backed_stock_components
                ).items()
                if count > 1
            )
        )
        if reused_assets:
            return PolicyDecision(
                "needs_review",
                (
                    PolicyDiagnostic(
                        "stock_video_asset_reused",
                        "Every Stock Video event must use a different asset",
                        component_ids=tuple(
                            component.component_id
                            for component in asset_backed_stock_components
                            if component.asset_ref in reused_assets
                        ),
                        asset_refs=reused_assets,
                    ),
                ),
            )
        metadata_by_asset = {row.asset_ref: row for row in candidate.stock_video_metadata}
        missing_stock_metadata = tuple(sorted(distinct_stock_assets.difference(metadata_by_asset)))
        if missing_stock_metadata:
            return PolicyDecision(
                "needs_review",
                (
                    PolicyDiagnostic(
                        "stock_video_metadata_missing",
                        "Every Stock Video event requires native source dimensions",
                        asset_refs=missing_stock_metadata,
                    ),
                ),
            )
        non_landscape_stock = tuple(
            sorted(
                asset_ref
                for asset_ref in distinct_stock_assets
                if metadata_by_asset[asset_ref].native_width
                <= metadata_by_asset[asset_ref].native_height
                or metadata_by_asset[asset_ref].native_height <= 0
            )
        )
        if non_landscape_stock:
            return PolicyDecision(
                "needs_review",
                (
                    PolicyDiagnostic(
                        "stock_video_not_native_landscape",
                        "Long Stock Video must be natively landscape",
                        asset_refs=non_landscape_stock,
                    ),
                ),
            )
        visual_coverage = tuple(
            sorted(
                (
                    component
                    for component in candidate.components
                    if component.lane in TITLE_LIKE_LANES
                    or (
                        component.semantic_kind == "b_roll"
                        and component.lane == "b_roll"
                        and component.implementation_kind in VISUAL_COVERAGE_BROLL_IMPLEMENTATIONS
                        and component.asset_ref is not None
                    )
                ),
                key=lambda component: component.t0,
            )
        )
        visual_gap = _first_coverage_gap(
            visual_coverage,
            duration_sec=candidate.context.duration_sec,
            max_gap_sec=LONG_MAX_NONSTRUCTURAL_VISUAL_GAP_SEC,
        )
        if visual_gap is not None:
            return PolicyDecision(
                "needs_review",
                (
                    _coverage_gap_diagnostic(
                        "visual_gap_exceeded",
                        "Long non-structural visual gap",
                        visual_gap,
                    ),
                ),
            )
        asset_backed_broll = tuple(
            component
            for component in candidate.components
            if component.semantic_kind == "b_roll"
            and component.lane == "b_roll"
            and component.implementation_kind in VISUAL_COVERAGE_BROLL_IMPLEMENTATIONS
            and component.asset_ref is not None
        )
        broll_gap = _first_coverage_gap(
            asset_backed_broll,
            duration_sec=candidate.context.duration_sec,
            max_gap_sec=LONG_MAX_ASSET_BACKED_BROLL_GAP_SEC,
        )
        if broll_gap is not None:
            return PolicyDecision(
                "needs_review",
                (
                    _coverage_gap_diagnostic(
                        "b_roll_cadence_gap_exceeded",
                        "Long asset-backed B-roll cadence gap",
                        broll_gap,
                        max_gap_sec=LONG_MAX_ASSET_BACKED_BROLL_GAP_SEC,
                    ),
                ),
            )
        return PolicyDecision("accepted")


def _first_coverage_gap(
    components: tuple[PolicyComponent, ...],
    *,
    duration_sec: float,
    max_gap_sec: float,
) -> _CoverageGap | None:
    ordered = tuple(
        sorted(
            components,
            key=lambda component: (component.t0, component.t1, component.component_id),
        )
    )
    coverage_end = 0.0
    previous: PolicyComponent | None = None
    for component in ordered:
        if component.t0 - coverage_end > max_gap_sec:
            return _CoverageGap(coverage_end, component.t0, previous, component)
        if component.t1 > coverage_end:
            coverage_end = component.t1
            previous = component
    if duration_sec - coverage_end > max_gap_sec:
        return _CoverageGap(coverage_end, duration_sec, previous, None)
    return None


def _coverage_gap_diagnostic(
    code: PolicyDiagnosticCode,
    label: str,
    gap: _CoverageGap,
    *,
    max_gap_sec: float = LONG_MAX_NONSTRUCTURAL_VISUAL_GAP_SEC,
) -> PolicyDiagnostic:
    previous_event_id = gap.previous.event_id if gap.previous is not None else "cut_start"
    next_event_id = gap.next.event_id if gap.next is not None else "cut_end"
    adjacent_components = tuple(
        component.component_id for component in (gap.previous, gap.next) if component is not None
    )
    return PolicyDiagnostic(
        code,
        (
            f"{label} [{gap.t0:.6f}, {gap.t1:.6f}]s exceeds "
            f"{max_gap_sec:g} seconds; "
            f"previous_event_id={previous_event_id}; next_event_id={next_event_id}"
        ),
        component_ids=adjacent_components,
    )


class ShortPolicy:
    """Short-only production policy; it never delegates to Long policy."""

    def validate(self, candidate: CutPolicyInput) -> PolicyDecision:
        if candidate.context.duration_sec > SHORT_MAX_DURATION_SEC:
            return PolicyDecision(
                "needs_review",
                (
                    PolicyDiagnostic(
                        "short_duration_exceeded",
                        "Short duration must not exceed 60 seconds",
                    ),
                ),
            )
        title_like = tuple(
            component for component in candidate.components if component.lane in TITLE_LIKE_LANES
        )
        if len(title_like) > SHORT_MAX_TITLE_LIKE_CARDS:
            return PolicyDecision(
                "needs_review",
                (
                    PolicyDiagnostic(
                        "short_title_limit_exceeded",
                        "Short permits at most two title-like cards",
                        component_ids=tuple(component.component_id for component in title_like),
                    ),
                ),
            )
        return PolicyDecision("accepted")
