"""Generate deterministic thumbnail arrangement candidates from template contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from shared.thumbnail_arrangement import (
    ArrangementCandidate,
    CheapEvalSummary,
    LayerSpec,
    PixelBox,
    make_candidate_id,
)
from shared.thumbnail_template_contracts import (
    SlotContract,
    TemplateContract,
    get_template_contract,
)

_CANVAS = (1280, 720)
_MULTI_SLOT_ROLES = {"payload_metric", "payload_arrow"}


@dataclass(frozen=True)
class ArrangementVariantSpec:
    variant_id: str
    content_side: str
    include_optional_assets: bool = False


def generate_arrangement_candidates(
    *,
    parsed_idea: Any,
    person_placement: Mapping[str, Any],
    layout_blocking: Mapping[str, Any] | None = None,
    canvas_size: tuple[int, int] = _CANVAS,
    idea_index: int = 1,
    hypothesis_index: int = 1,
    limit: int = 3,
) -> tuple[ArrangementCandidate, ...]:
    """Create slot-based arrangement candidates for a parsed thumbnail idea."""

    template_id = _template_id(parsed_idea)
    contract = get_template_contract(template_id)
    preferred_side = _content_side(person_placement, layout_blocking, canvas_size)
    specs = _variant_specs(preferred_side)[: max(1, limit)]

    return tuple(
        _candidate_from_spec(
            contract=contract,
            spec=spec,
            parsed_idea=parsed_idea,
            person_placement=person_placement,
            canvas_size=canvas_size,
            idea_index=idea_index,
            hypothesis_index=hypothesis_index,
            variant_index=index,
        )
        for index, spec in enumerate(specs, start=1)
    )


def candidate_to_component_plan(candidate: ArrangementCandidate) -> dict[str, Any]:
    """Adapt an arrangement candidate to the legacy component_plan dict shape."""

    component_layers = [
        layer
        for layer in candidate.layers
        if layer.role not in {"background", "host", "headline_text"}
    ]
    selected = {
        "variant_id": candidate.candidate_id,
        "reference_template_id": candidate.template_id,
        "score": round(candidate.cheap_eval.score, 2),
        "critique": list(candidate.cheap_eval.reasons),
        "repair_patches": list(candidate.cheap_eval.repairs),
        "components": [_component_dict(layer) for layer in component_layers],
    }
    return {
        "canvas_size": list(candidate.canvas_size),
        "selected_variant": selected,
        "variants": [selected],
        "arrangement_candidate": candidate.to_dict(),
    }


def _candidate_from_spec(
    *,
    contract: TemplateContract,
    spec: ArrangementVariantSpec,
    parsed_idea: Any,
    person_placement: Mapping[str, Any],
    canvas_size: tuple[int, int],
    idea_index: int,
    hypothesis_index: int,
    variant_index: int,
) -> ArrangementCandidate:
    layers: list[LayerSpec] = [
        LayerSpec(
            layer_id="background",
            kind="background_plate",
            role="background",
            box=PixelBox(0, 0, canvas_size[0], canvas_size[1]),
            z=_z_for_role(contract, "background"),
            style=_background_style(parsed_idea),
        ),
        _host_layer(contract, person_placement, canvas_size),
    ]
    payload_slots = _select_payload_slots(contract, spec.content_side)
    for slot in payload_slots:
        layers.append(_layer_from_slot(contract, slot, parsed_idea, canvas_size))

    if _should_include_headline(parsed_idea):
        for slot in contract.headline_slots:
            layers.append(_layer_from_slot(contract, slot, parsed_idea, canvas_size))

    if spec.include_optional_assets:
        for slot in _select_asset_slots(contract, spec.content_side):
            layers.append(_layer_from_slot(contract, slot, parsed_idea, canvas_size))

    return ArrangementCandidate(
        candidate_id=make_candidate_id(idea_index, hypothesis_index, variant_index),
        template_id=contract.template_id,
        hypothesis_id=str(_get(parsed_idea, "recipe_id", "") or ""),
        title=str(_get(parsed_idea, "title_pairing", "") or ""),
        canvas_size=canvas_size,
        layers=tuple(layers),
        cheap_eval=CheapEvalSummary(
            status="unscored",
            score=0.0,
            reasons=(f"generated from {contract.template_id} slots",),
        ),
        notes=(f"variant:{spec.variant_id}", f"content_side:{spec.content_side}"),
        metadata={
            "creator_target": contract.creator_target,
            "visual_weight": contract.visual_weight,
            "include_optional_assets": spec.include_optional_assets,
        },
    )


def _variant_specs(preferred_side: str) -> tuple[ArrangementVariantSpec, ...]:
    opposite = "left" if preferred_side == "right" else "right"
    return (
        ArrangementVariantSpec("primary", preferred_side),
        ArrangementVariantSpec("with_optional_asset", preferred_side, include_optional_assets=True),
        ArrangementVariantSpec("mirrored_probe", opposite),
    )


def _host_layer(
    contract: TemplateContract,
    person_placement: Mapping[str, Any],
    canvas_size: tuple[int, int],
) -> LayerSpec:
    fallback_slot = sorted(contract.host_slots, key=lambda slot: slot.priority)[0]
    box = _optional_box(person_placement.get("cutout_box")) or _slot_box(fallback_slot, canvas_size)
    return LayerSpec(
        layer_id="host",
        kind="cutout",
        role="host",
        box=box,
        z=_z_for_role(contract, "host"),
        slot_id=fallback_slot.slot_id,
        asset_ref=str(person_placement.get("cutout_path") or ""),
        face_box=_optional_box(person_placement.get("face_box")),
        hand_boxes=_hand_boxes(person_placement),
        metadata={"source": "person_placement"},
    )


def _layer_from_slot(
    contract: TemplateContract,
    slot: SlotContract,
    parsed_idea: Any,
    canvas_size: tuple[int, int],
) -> LayerSpec:
    box = _slot_box(slot, canvas_size)
    content = _content_for_slot(slot, parsed_idea)
    return LayerSpec(
        layer_id=slot.slot_id,
        kind=_kind_for_slot(slot, parsed_idea),
        role=slot.role,
        box=box,
        z=_z_for_role(contract, slot.role),
        slot_id=slot.slot_id,
        content=content,
        style=_style_for_slot(slot),
        protected_text_boxes=_protected_text_boxes(slot, box, content),
        metadata={
            "priority": slot.priority,
            "optional": slot.optional,
            "component_types": slot.component_types,
        },
    )


def _select_payload_slots(
    contract: TemplateContract, content_side: str
) -> tuple[SlotContract, ...]:
    selected: list[SlotContract] = []
    roles = tuple(dict.fromkeys(slot.role for slot in contract.payload_slots))
    for role in roles:
        slots = tuple(slot for slot in contract.payload_slots if slot.role == role)
        if role in _MULTI_SLOT_ROLES:
            selected.extend(sorted(slots, key=lambda slot: (slot.priority, slot.slot_id)))
            continue
        selected.append(_choose_side_slot(slots, content_side))
    return tuple(selected)


def _select_asset_slots(contract: TemplateContract, content_side: str) -> tuple[SlotContract, ...]:
    if not contract.asset_slots:
        return ()
    roles = tuple(dict.fromkeys(slot.role for slot in contract.asset_slots))
    selected: list[SlotContract] = []
    for role in roles:
        slots = tuple(slot for slot in contract.asset_slots if slot.role == role)
        selected.append(_choose_side_slot(slots, content_side))
    return tuple(selected)


def _choose_side_slot(slots: tuple[SlotContract, ...], content_side: str) -> SlotContract:
    side_matches = [slot for slot in slots if _slot_side(slot.slot_id) == content_side]
    if side_matches:
        return sorted(side_matches, key=lambda slot: (slot.priority, slot.slot_id))[0]
    neutral = [slot for slot in slots if _slot_side(slot.slot_id) == ""]
    if neutral:
        return sorted(neutral, key=lambda slot: (slot.priority, slot.slot_id))[0]
    return sorted(slots, key=lambda slot: (slot.priority, slot.slot_id))[0]


def _slot_side(slot_id: str) -> str:
    lowered = slot_id.lower()
    if "left" in lowered:
        return "left"
    if "right" in lowered:
        return "right"
    return ""


def _slot_box(slot: SlotContract, canvas_size: tuple[int, int]) -> PixelBox:
    return PixelBox.from_any(slot.box.to_pixel_box(canvas_size))


def _optional_box(value: Any) -> PixelBox | None:
    if not value:
        return None
    return PixelBox.from_any(value)


def _hand_boxes(person_placement: Mapping[str, Any]) -> tuple[PixelBox, ...]:
    boxes: list[PixelBox] = []
    for key in ("hand_boxes", "hand_box", "left_hand_box", "right_hand_box"):
        value = person_placement.get(key)
        if not value:
            continue
        if key == "hand_boxes":
            boxes.extend(PixelBox.from_any(item) for item in value)
        else:
            boxes.append(PixelBox.from_any(value))
    return tuple(boxes)


def _z_for_role(contract: TemplateContract, role: str) -> int:
    try:
        return contract.z_order_policy.index(role) * 10
    except ValueError:
        return (len(contract.z_order_policy) + 1) * 10


def _kind_for_slot(slot: SlotContract, parsed_idea: Any) -> str:
    component_type = str(_get(parsed_idea, "component_type", "") or "").strip()
    if component_type and component_type in slot.component_types:
        return component_type
    if slot.component_types:
        return slot.component_types[0]
    return slot.role


def _style_for_slot(slot: SlotContract) -> str:
    kind = slot.component_types[0] if slot.component_types else slot.role
    if kind == "benefit_list_card":
        return "warm_paper_card"
    if kind in {"command_panel", "action_label"}:
        return "dark_ui_panel"
    if kind in {"tool_label", "ui_panel", "logo_tile"}:
        return "jeff_clean_ui"
    if kind in {"metric_badge_pair", "arrow"}:
        return "ali_big_metric"
    if kind in {"quote_card", "social_post_card"}:
        return "clean_white_card"
    return "supporting_asset"


def _content_for_slot(slot: SlotContract, parsed_idea: Any) -> tuple[str, ...]:
    labels = _component_text(parsed_idea)
    kind = slot.component_types[0] if slot.component_types else slot.role
    if slot.role == "headline_text":
        return _tuple_or_default(_get(parsed_idea, "hook", ""), ("",))
    if kind == "benefit_list_card":
        return _pad(labels, ("護腦", "抗老", "認知"), 3)
    if kind == "metric_badge_pair":
        metrics = _pad(labels or _metric_labels(parsed_idea), ("Before", "After"), 2)
        return (metrics[1],) if "right" in slot.slot_id else (metrics[0],)
    if kind == "arrow":
        return ()
    if kind == "tool_label":
        return (_tool_label(parsed_idea, labels),)
    if kind in {"ui_panel", "command_panel", "action_label"}:
        return labels[:3] or _tuple_or_default(_get(parsed_idea, "hook", ""), ("Start Here",))
    if kind in {"quote_card", "social_post_card"}:
        return _tuple_or_default(_get(parsed_idea, "hook", ""), labels[:1] or ("Start Here",))
    if kind in {"logo_tile", "tool_logo", "simple_icon"}:
        return (_tool_label(parsed_idea, labels)[:8],)
    return labels[:2]


def _component_text(parsed_idea: Any) -> tuple[str, ...]:
    raw = _get(parsed_idea, "component_text", ())
    if isinstance(raw, str):
        return tuple(item.strip() for item in re.split(r"\s*/\s*|[,;；、]\s*", raw) if item.strip())
    if isinstance(raw, (list, tuple)):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    return ()


def _metric_labels(parsed_idea: Any) -> tuple[str, ...]:
    text = " ".join(
        str(_get(parsed_idea, key, "") or "")
        for key in ("title_pairing", "hook", "visual", "decoration")
    )
    found = re.findall(r"\$?\d+[A-Za-z%]*", text)
    return tuple(found[:2])


def _tool_label(parsed_idea: Any, labels: tuple[str, ...]) -> str:
    if labels:
        first = labels[0]
        if re.search(r"[A-Za-z]", first):
            return first
    text = " ".join(
        [
            str(_get(parsed_idea, "title_pairing", "") or ""),
            str(_get(parsed_idea, "visual", "") or ""),
            " ".join(str(item) for item in (_get(parsed_idea, "asset_queries", ()) or ())),
        ]
    )
    for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,24}", text):
        if token.lower() not in {"learn", "master", "guide", "under", "minutes", "wrong"}:
            return token
    return "Tool"


def _tuple_or_default(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    cleaned = str(value or "").strip()
    return (cleaned,) if cleaned else default


def _pad(values: tuple[str, ...], defaults: tuple[str, ...], count: int) -> tuple[str, ...]:
    out = list(values)
    for item in defaults:
        if len(out) >= count:
            break
        if item not in out:
            out.append(item)
    return tuple(out[:count])


def _protected_text_boxes(
    slot: SlotContract,
    box: PixelBox,
    content: tuple[str, ...],
) -> tuple[PixelBox, ...]:
    if not content or slot.role in {"payload_arrow", "supporting_asset"}:
        return ()
    if slot.role == "headline_text":
        return (_inset(box, x_ratio=0.04, y_ratio=0.12),)
    if slot.component_types and slot.component_types[0] == "benefit_list_card":
        return (_inset(box, x_ratio=0.16, y_ratio=0.18),)
    return (_inset(box, x_ratio=0.10, y_ratio=0.18),)


def _inset(box: PixelBox, *, x_ratio: float, y_ratio: float) -> PixelBox:
    dx = round(box.width * x_ratio)
    dy = round(box.height * y_ratio)
    return PixelBox(box.x0 + dx, box.y0 + dy, box.x1 - dx, box.y1 - dy)


def _should_include_headline(parsed_idea: Any) -> bool:
    hook = str(_get(parsed_idea, "hook", "") or "").strip()
    return bool(hook and hook.lower() not in {"none", "n/a", "無"})


def _background_style(parsed_idea: Any) -> str:
    bg = str(_get(parsed_idea, "bg", "") or "").strip()
    return bg or "blurred branded A-roll background"


def _content_side(
    person_placement: Mapping[str, Any],
    layout_blocking: Mapping[str, Any] | None,
    canvas_size: tuple[int, int],
) -> str:
    if isinstance(layout_blocking, Mapping):
        side = str(layout_blocking.get("content_side") or "").strip().lower()
        if side in {"left", "right"}:
            return side
    person_box = _optional_box(person_placement.get("cutout_box"))
    if not person_box:
        return "right"
    person_center_x = (person_box.x0 + person_box.x1) / 2
    return "right" if person_center_x < canvas_size[0] / 2 else "left"


def _template_id(parsed_idea: Any) -> str:
    return str(_get(parsed_idea, "reference_template_id", "") or "shosho_benefit_list_card").strip()


def _component_dict(layer: LayerSpec) -> dict[str, Any]:
    return {
        "component_type": layer.kind,
        "role": layer.role,
        "box": layer.box.to_xywh_dict(),
        "content": list(layer.content),
        "style": layer.style,
        "slot_id": layer.slot_id,
    }


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)
