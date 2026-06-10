"""Serializable arrangement manifest for thumbnail candidates.

This module defines the geometry contract that sits between template matching,
variant generation, deterministic layout gates, and later vision critique.
It intentionally stores concrete pixel boxes, not percentages, because each
candidate is a proposed 1280x720 composition that can be rendered or inspected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

EvalStatus = Literal["unscored", "pass", "fail"]


@dataclass(frozen=True)
class PixelBox:
    """A pixel-space box.

    Coordinates may sit outside the canvas for deliberate crops, but a box must
    always have positive width and height.
    """

    x0: int
    y0: int
    x1: int
    y1: int

    def __post_init__(self) -> None:
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError(f"invalid PixelBox with non-positive size: {self.to_tuple()}")

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    @property
    def area(self) -> int:
        return self.width * self.height

    def to_tuple(self) -> tuple[int, int, int, int]:
        return (self.x0, self.y0, self.x1, self.y1)

    def to_dict(self) -> dict[str, int]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}

    def to_xywh_dict(self) -> dict[str, int]:
        return {"x": self.x0, "y": self.y0, "w": self.width, "h": self.height}

    def intersection(self, other: PixelBox) -> int:
        ix0 = max(self.x0, other.x0)
        iy0 = max(self.y0, other.y0)
        ix1 = min(self.x1, other.x1)
        iy1 = min(self.y1, other.y1)
        if ix1 <= ix0 or iy1 <= iy0:
            return 0
        return (ix1 - ix0) * (iy1 - iy0)

    def iou(self, other: PixelBox) -> float:
        intersection = self.intersection(other)
        if intersection == 0:
            return 0.0
        return intersection / (self.area + other.area - intersection)

    def overlap_ratio(self, other: PixelBox) -> float:
        """Return the share of this box covered by another box."""

        return self.intersection(other) / max(1, self.area)

    @classmethod
    def from_any(cls, value: Any) -> PixelBox:
        if isinstance(value, PixelBox):
            return value
        if isinstance(value, Mapping):
            if {"x0", "y0", "x1", "y1"} <= set(value):
                return cls(
                    x0=int(value["x0"]),
                    y0=int(value["y0"]),
                    x1=int(value["x1"]),
                    y1=int(value["y1"]),
                )
            if {"x", "y", "w", "h"} <= set(value):
                x0 = int(value["x"])
                y0 = int(value["y"])
                return cls(x0=x0, y0=y0, x1=x0 + int(value["w"]), y1=y0 + int(value["h"]))
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 4:
            x0, y0, x1, y1 = value
            return cls(x0=int(x0), y0=int(y0), x1=int(x1), y1=int(y1))
        raise ValueError(f"cannot parse PixelBox from {value!r}")


@dataclass(frozen=True)
class LayerSpec:
    """One renderable layer in an arrangement candidate."""

    layer_id: str
    kind: str
    role: str
    box: PixelBox
    z: int
    slot_id: str = ""
    content: tuple[str, ...] = ()
    style: str = ""
    asset_ref: str = ""
    protected_text_boxes: tuple[PixelBox, ...] = ()
    face_box: PixelBox | None = None
    hand_boxes: tuple[PixelBox, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.layer_id.strip():
            raise ValueError("layer_id is required")
        if not self.kind.strip():
            raise ValueError(f"kind is required for layer {self.layer_id}")
        if not self.role.strip():
            raise ValueError(f"role is required for layer {self.layer_id}")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "layer_id": self.layer_id,
            "kind": self.kind,
            "role": self.role,
            "box": self.box.to_dict(),
            "z": self.z,
        }
        if self.slot_id:
            data["slot_id"] = self.slot_id
        if self.content:
            data["content"] = list(self.content)
        if self.style:
            data["style"] = self.style
        if self.asset_ref:
            data["asset_ref"] = self.asset_ref
        if self.protected_text_boxes:
            data["protected_text_boxes"] = [box.to_dict() for box in self.protected_text_boxes]
        if self.face_box:
            data["face_box"] = self.face_box.to_dict()
        if self.hand_boxes:
            data["hand_boxes"] = [box.to_dict() for box in self.hand_boxes]
        if self.metadata:
            data["metadata"] = _jsonable_mapping(self.metadata)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LayerSpec:
        return cls(
            layer_id=str(data["layer_id"]),
            kind=str(data["kind"]),
            role=str(data["role"]),
            box=PixelBox.from_any(data["box"]),
            z=int(data["z"]),
            slot_id=str(data.get("slot_id", "") or ""),
            content=_string_tuple(data.get("content", ())),
            style=str(data.get("style", "") or ""),
            asset_ref=str(data.get("asset_ref", "") or ""),
            protected_text_boxes=_box_tuple(data.get("protected_text_boxes", ())),
            face_box=PixelBox.from_any(data["face_box"]) if data.get("face_box") else None,
            hand_boxes=_box_tuple(data.get("hand_boxes", ())),
            metadata=_mapping(data.get("metadata", {})),
        )


@dataclass(frozen=True)
class CheapEvalSummary:
    """Result of deterministic layout gates before vision review."""

    status: EvalStatus = "unscored"
    score: float = 0.0
    reasons: tuple[str, ...] = ()
    repairs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"unscored", "pass", "fail"}:
            raise ValueError(f"invalid cheap eval status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "score": float(self.score),
            "reasons": list(self.reasons),
            "repairs": list(self.repairs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> CheapEvalSummary:
        if not data:
            return cls()
        return cls(
            status=str(data.get("status", "unscored")),  # type: ignore[arg-type]
            score=float(data.get("score", 0.0)),
            reasons=_string_tuple(data.get("reasons", ())),
            repairs=_string_tuple(data.get("repairs", ())),
        )


@dataclass(frozen=True)
class ArrangementCandidate:
    """A complete proposed thumbnail arrangement before final rendering."""

    candidate_id: str
    template_id: str
    layers: tuple[LayerSpec, ...]
    hypothesis_id: str = ""
    title: str = ""
    canvas_size: tuple[int, int] = (1280, 720)
    cheap_eval: CheapEvalSummary = field(default_factory=CheapEvalSummary)
    notes: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.candidate_id.strip():
            raise ValueError("candidate_id is required")
        if not self.template_id.strip():
            raise ValueError("template_id is required")
        width, height = self.canvas_size
        if width <= 0 or height <= 0:
            raise ValueError(f"invalid canvas size: {self.canvas_size}")
        layer_ids = [layer.layer_id for layer in self.layers]
        duplicate_ids = sorted(
            {layer_id for layer_id in layer_ids if layer_ids.count(layer_id) > 1}
        )
        if duplicate_ids:
            raise ValueError(f"duplicate layer ids: {', '.join(duplicate_ids)}")
        object.__setattr__(self, "layers", self.layers_in_z_order())

    def layers_in_z_order(self) -> tuple[LayerSpec, ...]:
        return tuple(sorted(self.layers, key=lambda layer: (layer.z, layer.layer_id)))

    def layer_by_id(self, layer_id: str) -> LayerSpec:
        for layer in self.layers:
            if layer.layer_id == layer_id:
                return layer
        raise KeyError(f"unknown layer: {layer_id}")

    def layers_by_role(self, role: str) -> tuple[LayerSpec, ...]:
        return tuple(layer for layer in self.layers if layer.role == role)

    def protected_text_boxes(self) -> tuple[PixelBox, ...]:
        return tuple(box for layer in self.layers for box in layer.protected_text_boxes)

    def face_boxes(self) -> tuple[PixelBox, ...]:
        return tuple(layer.face_box for layer in self.layers if layer.face_box is not None)

    def hand_boxes(self) -> tuple[PixelBox, ...]:
        return tuple(box for layer in self.layers for box in layer.hand_boxes)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "candidate_id": self.candidate_id,
            "template_id": self.template_id,
            "canvas_size": list(self.canvas_size),
            "layers": [layer.to_dict() for layer in self.layers],
            "cheap_eval": self.cheap_eval.to_dict(),
        }
        if self.hypothesis_id:
            data["hypothesis_id"] = self.hypothesis_id
        if self.title:
            data["title"] = self.title
        if self.notes:
            data["notes"] = list(self.notes)
        if self.metadata:
            data["metadata"] = _jsonable_mapping(self.metadata)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ArrangementCandidate:
        canvas = data.get("canvas_size", (1280, 720))
        if not isinstance(canvas, Sequence) or isinstance(canvas, (str, bytes)) or len(canvas) != 2:
            raise ValueError(f"invalid canvas size: {canvas!r}")
        return cls(
            candidate_id=str(data["candidate_id"]),
            template_id=str(data["template_id"]),
            layers=tuple(LayerSpec.from_dict(layer) for layer in data.get("layers", ())),
            hypothesis_id=str(data.get("hypothesis_id", "") or ""),
            title=str(data.get("title", "") or ""),
            canvas_size=(int(canvas[0]), int(canvas[1])),
            cheap_eval=CheapEvalSummary.from_dict(data.get("cheap_eval")),
            notes=_string_tuple(data.get("notes", ())),
            metadata=_mapping(data.get("metadata", {})),
        )


def make_candidate_id(idea_index: int, hypothesis_index: int, variant_index: int) -> str:
    if idea_index < 1 or hypothesis_index < 1 or variant_index < 1:
        raise ValueError("candidate id indexes are 1-based")
    return f"idea{idea_index:02d}-hyp{hypothesis_index:02d}-cand{variant_index:02d}"


def _box_tuple(value: Any) -> tuple[PixelBox, ...]:
    if not value:
        return ()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(PixelBox.from_any(item) for item in value)
    raise ValueError(f"expected sequence of boxes, got {value!r}")


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    raise ValueError(f"expected string sequence, got {value!r}")


def _mapping(value: Any) -> Mapping[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return {str(key): _pythonable_metadata(item) for key, item in value.items()}
    raise ValueError(f"expected mapping, got {value!r}")


def _jsonable_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): _jsonable(item)
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, PixelBox):
        return value.to_dict()
    if isinstance(value, Mapping):
        return _jsonable_mapping(value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _pythonable_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _pythonable_metadata(item) for key, item in value.items()}
    if isinstance(value, list):
        return tuple(_pythonable_metadata(item) for item in value)
    return value
