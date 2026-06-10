"""Compatibility gates between generated components and one reference image.

The template contract says what a family can render. A concrete reference
record says what this thumbnail is trying to imitate. This module keeps Step 5
from passing layouts that technically satisfy a broad contract but clearly
break the chosen Ali/Jeff reference grammar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from shared.thumbnail_arrangement import ArrangementCandidate, LayerSpec, PixelBox


@dataclass(frozen=True)
class ComponentCompatibilityResult:
    ok: bool
    score: float
    reasons: tuple[str, ...]
    repair_hints: tuple[str, ...]
    reference_id: str
    family_id: str
    expected_component_types: tuple[str, ...]
    observed_component_types: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "score": round(self.score, 2),
            "reasons": list(self.reasons),
            "repair_hints": list(self.repair_hints),
            "reference_id": self.reference_id,
            "family_id": self.family_id,
            "expected_component_types": list(self.expected_component_types),
            "observed_component_types": list(self.observed_component_types),
        }


def score_arrangement_against_reference(
    *,
    candidate: ArrangementCandidate,
    reference_record: Mapping[str, Any],
) -> ComponentCompatibilityResult:
    """Score a generated arrangement against a concrete deconstruction record."""

    family_id = str(reference_record.get("template_family_candidate") or "")
    reference_id = str(reference_record.get("reference_id") or "")
    expected_types = _allowed_component_types(reference_record)
    layers = _renderable_component_layers(candidate)
    observed_types = tuple(layer.kind for layer in layers)

    score = 100.0
    reasons: list[str] = []
    repairs: list[str] = []

    def penalize(reason: str, repair: str, points: float) -> None:
        nonlocal score
        score -= points
        reasons.append(reason)
        repairs.append(repair)

    if not bool(reference_record.get("renderable_v1")):
        penalize("reference is not renderable_v1", "choose_renderable_reference", 100)

    if family_id and candidate.template_id != family_id:
        penalize(
            f"candidate template {candidate.template_id} differs from reference {family_id}",
            "regenerate_with_selected_reference_template",
            46,
        )

    unknown = [kind for kind in observed_types if kind not in expected_types]
    if unknown:
        penalize(
            f"component type not in concrete reference grammar: {', '.join(sorted(set(unknown)))}",
            "replace_component_types_to_match_reference",
            34,
        )

    max_total = _max_total_components(reference_record)
    if len(layers) > max_total:
        penalize(
            f"too many renderable components: {len(layers)} > {max_total}",
            "reduce_component_count_to_reference",
            24,
        )

    required_components = [
        component
        for component in _reference_components(reference_record)
        if bool(component.get("required"))
    ]
    for component in required_components:
        compatible = _compatible_types(str(component.get("type") or ""))
        matches = [layer for layer in layers if layer.kind in compatible]
        if not matches:
            penalize(
                f"missing required reference component: {component.get('component_id')}",
                f"add_required_component:{component.get('component_id')}",
                30,
            )
            continue
        ref_box = _reference_box(component, candidate.canvas_size)
        best = max(matches, key=lambda layer: layer.box.iou(ref_box))
        iou = best.box.iou(ref_box)
        if iou < 0.12:
            penalize(
                (
                    f"{best.kind} is in the wrong zone for reference "
                    f"{component.get('component_id')}: iou {iou:.2f}"
                ),
                f"move_component_to_reference_zone:{best.layer_id}",
                22,
            )

    max_primary = _max_primary_components(reference_record)
    primary_count = len([layer for layer in layers if layer.role != "supporting_asset"])
    if primary_count > max_primary:
        penalize(
            f"too many primary components: {primary_count} > {max_primary}",
            "reduce_primary_payload_count_to_reference",
            20,
        )

    for layer in layers:
        for item in layer.content:
            text = str(item).strip()
            if len(text) > 24:
                penalize(
                    f"{layer.layer_id} text is too long for reference density: {text[:28]}",
                    f"shorten_text:{layer.layer_id}",
                    10,
                )
                break
        if len([item for item in layer.content if str(item).strip()]) > 3:
            penalize(
                f"{layer.layer_id} has too many text items for reference density",
                f"reduce_text_items:{layer.layer_id}",
                10,
            )

    if not reasons:
        reasons.append(f"matches concrete reference {reference_id}")

    final_score = max(0.0, score)
    return ComponentCompatibilityResult(
        ok=final_score >= 80 and not repairs,
        score=final_score,
        reasons=tuple(reasons),
        repair_hints=tuple(dict.fromkeys(repairs)),
        reference_id=reference_id,
        family_id=family_id,
        expected_component_types=expected_types,
        observed_component_types=observed_types,
    )


def reference_summary(reference_record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the compact reference metadata we persist in render manifests."""

    return {
        "reference_id": str(reference_record.get("reference_id") or ""),
        "family_id": str(reference_record.get("template_family_candidate") or ""),
        "title": str(reference_record.get("title") or ""),
        "image_path": str(reference_record.get("image_path") or ""),
    }


def _renderable_component_layers(candidate: ArrangementCandidate) -> tuple[LayerSpec, ...]:
    return tuple(
        layer
        for layer in candidate.layers
        if layer.role not in {"background", "host", "headline_text"}
    )


def _reference_components(reference_record: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = reference_record.get("components", ())
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(component for component in raw if isinstance(component, Mapping))


def _allowed_component_types(reference_record: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for component in _reference_components(reference_record):
        values.extend(_compatible_types(str(component.get("type") or "")))

    constraints = reference_record.get("generator_constraints")
    if isinstance(constraints, Mapping):
        for item in constraints.get("must_preserve", ()) or ():
            text = str(item)
            match = re.search(r"allowed component types:\s*(.+)$", text, flags=re.IGNORECASE)
            if not match:
                continue
            values.extend(
                token.strip()
                for token in re.split(r"\s*,\s*", match.group(1))
                if token.strip()
            )

    return tuple(dict.fromkeys(value for value in values if value))


def _compatible_types(component_type: str) -> tuple[str, ...]:
    aliases = {
        "quote_card": ("quote_card", "social_post_card"),
        "social_post_card": ("social_post_card", "quote_card"),
        "tool_logo": ("tool_logo", "logo_tile"),
        "logo_tile": ("logo_tile", "tool_logo"),
        "text_banner": ("text_banner", "action_label"),
        "action_label": ("action_label", "text_banner"),
    }
    return aliases.get(component_type, (component_type,))


def _reference_box(
    component: Mapping[str, Any],
    canvas_size: tuple[int, int],
) -> PixelBox:
    raw = component.get("box_pct")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return PixelBox(0, 0, canvas_size[0], canvas_size[1])
    width, height = canvas_size
    x0, y0, x1, y1 = (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    return PixelBox(
        round(x0 * width),
        round(y0 * height),
        round(x1 * width),
        round(y1 * height),
    )


def _max_total_components(reference_record: Mapping[str, Any]) -> int:
    constraints = reference_record.get("generator_constraints")
    if isinstance(constraints, Mapping):
        try:
            return int(constraints.get("max_total_components") or 99)
        except (TypeError, ValueError):
            return 99
    return 99


def _max_primary_components(reference_record: Mapping[str, Any]) -> int:
    constraints = reference_record.get("generator_constraints")
    if isinstance(constraints, Mapping):
        try:
            return int(constraints.get("max_primary_components") or 99)
        except (TypeError, ValueError):
            return 99
    return 99
