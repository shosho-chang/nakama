"""Pydantic models for foundry storyboard.yaml (ADR-032 §6).

extra="forbid" on every model per ADR-019 lesson.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class Timing(BaseModel):
    model_config = {"extra": "forbid"}

    start: float
    duration: float


class Transitions(BaseModel):
    model_config = {"extra": "forbid"}

    in_transition: str | None = None
    out_transition: str | None = None


class BRollSpec(BaseModel):
    model_config = {"extra": "forbid"}

    render_target: Literal["hyperframes", "reader-playwright", "web-playwright"]
    component: str
    params: dict[str, Any]
    transitions: Transitions


class BeatStatus(BaseModel):
    model_config = {"extra": "forbid"}

    text_approved: bool = False
    render_status: Literal["pending", "rendering", "done", "failed"] = "pending"
    visual_approved: bool = False
    # ADR-038 §D2: 16-char sha256 prefix used in `out/b_roll_<cached_hash>.mp4`
    # filename. Filled by render_dispatcher when the beat is queued (or cache-
    # hit) and read by fcpxml_emitter to resolve the rendered mp4. None until
    # the beat has been considered for rendering at least once.
    cached_hash: str | None = None


class UserNote(BaseModel):
    model_config = {"extra": "forbid"}

    timestamp: str
    note: str


class Beat(BaseModel):
    model_config = {"extra": "forbid"}

    beat_id: int
    start_quote: str
    end_quote: str
    timing: Timing | None = None
    srt_line_ids: list[int] | None = None
    broll_decision: Literal["none", "cutaway"]
    layout: str
    broll: BRollSpec | None = None
    status: BeatStatus
    user_notes: list[UserNote]
