"""Manifest — Pydantic schema mirroring video/src/parser/types.ts.

The Manifest is the shared contract between the TypeScript DSL parser and the
Python FCPXML/SRT emitters.  It is serialised to ``manifest.json`` inside the
episode data directory.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Discriminator, Field, Tag

from agents.brook.script_video.cuts import CutPoint

# ---------------------------------------------------------------------------
# Scene types
# ---------------------------------------------------------------------------


class SceneBase(BaseModel):
    id: str
    start_frame: int
    duration_frames: int


class ARollFullScene(SceneBase):
    type: Literal["aroll-full"] = "aroll-full"
    aroll_start_sec: float


class ARollPipScene(SceneBase):
    type: Literal["aroll-pip"] = "aroll-pip"
    aroll_start_sec: float
    slide: dict
    pip_position: Literal["top-left", "top-right", "bottom-left", "bottom-right"]


class TransitionTitleScene(SceneBase):
    type: Literal["transition"] = "transition"
    title: str
    subtitle: str = ""


class BBox(BaseModel):
    x0: float
    y0: float
    x1: float
    y1: float


class DocumentQuoteScene(SceneBase):
    type: Literal["document-quote"] = "document-quote"
    page_image_path: str
    image_width: int
    image_height: int
    highlights: list[BBox]
    variant: Literal["highlighter-sweep", "ken-burns", "spotlight"]
    citation: dict  # {title, page, author?}


class QuoteCardScene(SceneBase):
    type: Literal["quote-card"] = "quote-card"
    quote_text: str
    attribution: str = ""


class BigStatScene(SceneBase):
    type: Literal["big-stat"] = "big-stat"
    number: str
    unit: str
    description: str = ""


Scene = Annotated[
    Union[
        Annotated[ARollFullScene, Tag("aroll-full")],
        Annotated[ARollPipScene, Tag("aroll-pip")],
        Annotated[TransitionTitleScene, Tag("transition")],
        Annotated[DocumentQuoteScene, Tag("document-quote")],
        Annotated[QuoteCardScene, Tag("quote-card")],
        Annotated[BigStatScene, Tag("big-stat")],
    ],
    Discriminator("type"),
]


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


class ManifestCutPoint(BaseModel):
    """JSON-serialisable version of CutPoint for manifest.json."""

    type: Literal["razor", "ripple-delete"]
    start_sec: float
    end_sec: float
    reason: Literal["marker", "alignment-detected"]
    confidence: float

    @classmethod
    def from_cut_point(cls, cp: CutPoint) -> "ManifestCutPoint":
        return cls(
            type=cp.type,
            start_sec=cp.start_sec,
            end_sec=cp.end_sec,
            reason=cp.reason,
            confidence=cp.confidence,
        )


class Manifest(BaseModel):
    episode_id: str
    fps: int = Field(default=30, ge=1)
    total_frames: int = Field(ge=0)
    scenes: list[Scene]
    aroll_audio: str  # absolute path to aroll-audio.mp3
    aroll_video: str  # absolute path to aroll-video.mp4
    cuts: list[ManifestCutPoint]
