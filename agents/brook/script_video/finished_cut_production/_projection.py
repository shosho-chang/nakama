"""Canonical active visual vocabulary for Finished Cut Production.

Historical Release receipts may contain retired values, but they are deliberately
absent from this module so no current worker, acceptance, plan, or materializer can
discover or mint them through the production vocabulary.
"""

from __future__ import annotations

from typing import Literal

ComponentLane = Literal[
    "b_roll",
    "identity_card",
    "hero_title",
    "fullscreen_transition",
    "visual_effect",
]

_WORKER_PROJECTION_COMBINATIONS = (
    ("chapter", "fullscreen_transition", "fullscreen_transition"),
    ("hero_title", "hero_title", "hero_title"),
    ("b_roll", "stock_video", "b_roll"),
    ("b_roll", "photo", "b_roll"),
    ("b_roll", "non_editorial_clip", "b_roll"),
    ("b_roll", "person_inset", "b_roll"),
    ("identity_card", "identity_card", "identity_card"),
    ("visual_effect", "visual_effect", "visual_effect"),
)
_ACTIVE_PROJECTION_COMBINATIONS = frozenset(
    (*_WORKER_PROJECTION_COMBINATIONS, ("b_roll", "camera_correction", "b_roll"))
)
_ACTIVE_SEMANTIC_KINDS = frozenset(
    {row[0] for row in _ACTIVE_PROJECTION_COMBINATIONS} | {"intentional_aroll"}
)
_ACTIVE_COMPONENT_LANES = (
    "b_roll",
    "identity_card",
    "hero_title",
    "fullscreen_transition",
    "visual_effect",
)


def _is_active_semantic_kind(value: str) -> bool:
    return value in _ACTIVE_SEMANTIC_KINDS


def _is_active_projection(
    semantic_kind: str,
    implementation_kind: str,
    lane: str,
) -> bool:
    return (semantic_kind, implementation_kind, lane) in _ACTIVE_PROJECTION_COMBINATIONS


def _event_has_active_projection(
    *,
    semantic_kind: str,
    implementation_kind: str,
    lane: str | None,
    intentional_aroll: bool,
) -> bool:
    if intentional_aroll:
        return (
            semantic_kind == "intentional_aroll"
            and implementation_kind == "intentional_aroll"
            and lane is None
        )
    return lane is not None and _is_active_projection(
        semantic_kind,
        implementation_kind,
        lane,
    )
