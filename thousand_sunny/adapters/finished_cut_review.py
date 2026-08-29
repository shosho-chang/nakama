"""Read-only exact-current projection for the Finished Cut Review surface."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol

from agents.brook.script_video.finished_cut_production import (
    ArtifactView,
    ComponentView,
    EventView,
    FinishedCutInspection,
)

ProbeValue = str | int | float | bool | None


class ReviewState(str, Enum):
    READY = "ready"
    MISSING = "missing"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ReviewCapability:
    enabled: bool
    reason: Literal["sealed_current", "current_missing", "current_invalid"]


@dataclass(frozen=True, slots=True)
class ReviewArtifactView:
    reference: str
    bytes: int
    sha256: str
    duration_sec: float | None
    probe: tuple[tuple[str, ProbeValue], ...]


@dataclass(frozen=True, slots=True)
class ReviewEventView:
    event_id: str
    master_cue_ids: tuple[str, ...]
    text: str
    text_hash: str
    t0: float
    t1: float
    section_id: str | None
    intent: str
    display: str
    semantic_kind: str
    implementation_kind: str
    lane: (
        Literal[
            "b_roll",
            "identity_card",
            "hero_title",
            "supporting_title",
            "fullscreen_transition",
            "visual_effect",
        ]
        | None
    )
    asset_ref: str | None
    visual_status: str | None
    intentional_aroll: bool


@dataclass(frozen=True, slots=True)
class ReviewComponentView:
    component_id: str
    event_id: str
    semantic_kind: str
    implementation_kind: str
    lane: Literal[
        "b_roll",
        "identity_card",
        "hero_title",
        "supporting_title",
        "fullscreen_transition",
        "visual_effect",
    ]
    display: str
    t0: float
    t1: float
    asset_ref: str | None


@dataclass(frozen=True, slots=True)
class ReviewCutView:
    release_id: str
    cut_id: str
    format: Literal["long", "short"]
    preview: ReviewArtifactView
    subtitle: ReviewArtifactView
    events: tuple[ReviewEventView, ...]
    components: tuple[ReviewComponentView, ...]


@dataclass(frozen=True, slots=True)
class FinishedCutReviewView:
    episode_id: str
    state: ReviewState
    cuts: tuple[ReviewCutView, ...]
    review_capability: ReviewCapability
    error: str | None = None


class CurrentReleaseInspector(Protocol):
    """Agent-owned exact-current read interface used at the Bridge seam."""

    def inspect_current(self, episode_id: str) -> FinishedCutInspection: ...


class FinishedCutReviewAdapter:
    """Project one exact current index without filesystem discovery or mutation."""

    def __init__(self, inspector: CurrentReleaseInspector) -> None:
        self._inspector = inspector

    def load(self, episode_id: str) -> FinishedCutReviewView:
        inspection = self._inspector.inspect_current(episode_id)
        if inspection.episode_id != episode_id:
            return _unavailable_view(
                episode_id,
                ReviewState.INVALID,
                "current_invalid",
                "current_release_invalid",
            )
        if inspection.state == "missing":
            return _unavailable_view(
                episode_id,
                ReviewState.MISSING,
                "current_missing",
                inspection.error_code or "current_release_missing",
            )
        if inspection.state == "invalid":
            return _unavailable_view(
                episode_id,
                ReviewState.INVALID,
                "current_invalid",
                inspection.error_code or "current_release_invalid",
            )
        cuts = tuple(
            ReviewCutView(
                release_id=cut.release_id,
                cut_id=cut.cut_id,
                format=cut.format,
                preview=_artifact_view(cut.preview),
                subtitle=_artifact_view(cut.subtitle),
                events=tuple(_event_view(event) for event in cut.events),
                components=tuple(_component_view(component) for component in cut.components),
            )
            for cut in inspection.cuts
        )
        return FinishedCutReviewView(
            episode_id=episode_id,
            state=ReviewState.READY,
            cuts=cuts,
            review_capability=ReviewCapability(True, "sealed_current"),
        )


def _artifact_view(artifact: ArtifactView) -> ReviewArtifactView:
    return ReviewArtifactView(
        reference=artifact.reference,
        bytes=artifact.bytes,
        sha256=artifact.sha256,
        duration_sec=artifact.duration_sec,
        probe=artifact.probe,
    )


def _event_view(event: EventView) -> ReviewEventView:
    return ReviewEventView(
        event_id=event.event_id,
        master_cue_ids=event.master_cue_ids,
        text=event.text,
        text_hash=event.text_hash,
        t0=event.t0,
        t1=event.t1,
        section_id=event.section_id,
        intent=event.intent,
        display=event.display,
        semantic_kind=event.semantic_kind,
        implementation_kind=event.implementation_kind,
        lane=event.lane,
        asset_ref=event.asset_ref,
        visual_status=event.visual_status,
        intentional_aroll=event.intentional_aroll,
    )


def _component_view(component: ComponentView) -> ReviewComponentView:
    return ReviewComponentView(
        component_id=component.component_id,
        event_id=component.event_id,
        semantic_kind=component.semantic_kind,
        implementation_kind=component.implementation_kind,
        lane=component.lane,
        display=component.display,
        t0=component.t0,
        t1=component.t1,
        asset_ref=component.asset_ref,
    )


def _unavailable_view(
    episode_id: str,
    state: Literal[ReviewState.MISSING, ReviewState.INVALID],
    reason: Literal["current_missing", "current_invalid"],
    error: str,
) -> FinishedCutReviewView:
    return FinishedCutReviewView(
        episode_id=episode_id,
        state=state,
        cuts=(),
        review_capability=ReviewCapability(False, reason),
        error=error,
    )
