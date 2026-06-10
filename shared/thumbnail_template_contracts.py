"""Executable template contracts for thumbnail arrangement planning.

The reference-template catalog says what a template is. Contracts say how that
template may be arranged: slots, protected areas, allowed overlap, and cheap
layout gates. Later arrangement generators and critics should consume this
module instead of re-inventing per-template geometry.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from shared.thumbnail_reference_templates import REFERENCE_TEMPLATE_IDS

OverlapStatus = Literal["allowed", "tolerated", "forbidden"]


@dataclass(frozen=True)
class PercentBox:
    """A percent-based box, independent of final render resolution."""

    x0: float
    y0: float
    x1: float
    y1: float

    def to_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    def to_pixel_box(self, canvas_size: tuple[int, int] = (1280, 720)) -> tuple[int, int, int, int]:
        width, height = canvas_size
        return (
            round(self.x0 * width),
            round(self.y0 * height),
            round(self.x1 * width),
            round(self.y1 * height),
        )

    def to_dict(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


@dataclass(frozen=True)
class SlotContract:
    slot_id: str
    role: str
    box: PercentBox
    priority: int = 1
    component_types: tuple[str, ...] = ()
    optional: bool = False
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "role": self.role,
            "box_pct": self.box.to_dict(),
            "priority": self.priority,
            "component_types": list(self.component_types),
            "optional": self.optional,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class OverlapRule:
    foreground_role: str
    background_role: str
    status: OverlapStatus
    max_iou: float
    max_protected_text_iou: float = 0.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "foreground_role": self.foreground_role,
            "background_role": self.background_role,
            "status": self.status,
            "max_iou": self.max_iou,
            "max_protected_text_iou": self.max_protected_text_iou,
            "note": self.note,
        }


@dataclass(frozen=True)
class TemplateContract:
    template_id: str
    creator_target: str
    intent_tags: tuple[str, ...]
    host_slots: tuple[SlotContract, ...]
    payload_slots: tuple[SlotContract, ...]
    headline_slots: tuple[SlotContract, ...]
    asset_slots: tuple[SlotContract, ...]
    visual_weight: str
    z_order_policy: tuple[str, ...]
    overlap_policy: tuple[OverlapRule, ...]
    style_policy: tuple[str, ...]
    cheap_eval_gates: dict[str, float]
    vision_eval_rubric: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "creator_target": self.creator_target,
            "intent_tags": list(self.intent_tags),
            "host_slots": [slot.to_dict() for slot in self.host_slots],
            "payload_slots": [slot.to_dict() for slot in self.payload_slots],
            "headline_slots": [slot.to_dict() for slot in self.headline_slots],
            "asset_slots": [slot.to_dict() for slot in self.asset_slots],
            "visual_weight": self.visual_weight,
            "z_order_policy": list(self.z_order_policy),
            "overlap_policy": [rule.to_dict() for rule in self.overlap_policy],
            "style_policy": list(self.style_policy),
            "cheap_eval_gates": dict(self.cheap_eval_gates),
            "vision_eval_rubric": list(self.vision_eval_rubric),
        }


_BASE_CHEAP_GATES: dict[str, float] = {
    "min_face_height_ratio": 0.38,
    "min_payload_area_ratio": 0.08,
    "max_payload_area_ratio": 0.32,
    "min_edge_margin_ratio": 0.018,
    "max_primary_payloads": 1,
    "min_text_contrast_proxy": 0.55,
    "max_protected_text_overlap_iou": 0.02,
}

_BASE_RUBRIC: tuple[str, ...] = (
    "first_glance_focal_clarity",
    "reference_template_fit",
    "face_and_expression_salience",
    "component_clarity",
    "asset_relevance",
    "small_thumbnail_readability",
    "not_debug_or_ppt_mock",
    "trust_and_claim_risk",
)


def _box(x0: float, y0: float, x1: float, y1: float) -> PercentBox:
    return PercentBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _slot(
    slot_id: str,
    role: str,
    box: PercentBox,
    *,
    priority: int = 1,
    component_types: tuple[str, ...] = (),
    optional: bool = False,
    notes: tuple[str, ...] = (),
) -> SlotContract:
    return SlotContract(
        slot_id=slot_id,
        role=role,
        box=box,
        priority=priority,
        component_types=component_types,
        optional=optional,
        notes=notes,
    )


def _common_overlap_policy() -> tuple[OverlapRule, ...]:
    return (
        OverlapRule(
            foreground_role="host_face",
            background_role="payload_card",
            status="forbidden",
            max_iou=0.0,
            note="never cover the face with payload",
        ),
        OverlapRule(
            foreground_role="host_hand",
            background_role="payload_card",
            status="allowed",
            max_iou=0.42,
            max_protected_text_iou=0.02,
            note="hand-over-card can add depth, but protected text must remain readable",
        ),
        OverlapRule(
            foreground_role="host_hand",
            background_role="payload_text",
            status="forbidden",
            max_iou=0.02,
            note="hands may touch the card, not hide the keywords",
        ),
        OverlapRule(
            foreground_role="headline_text",
            background_role="host_face",
            status="forbidden",
            max_iou=0.0,
            note="headline cannot cover expression",
        ),
    )


CONTRACTS: tuple[TemplateContract, ...] = (
    TemplateContract(
        template_id="shosho_benefit_list_card",
        creator_target="ali_abdaal_shosho",
        intent_tags=("health", "evidence", "benefit_list", "warm_explainer"),
        host_slots=(
            _slot(
                "host_left_third",
                "host",
                _box(-0.08, -0.05, 0.58, 1.16),
                notes=("face should land near left third", "body crop may exceed canvas"),
            ),
            _slot(
                "host_right_third",
                "host",
                _box(0.42, -0.05, 1.08, 1.16),
                priority=2,
                notes=("use only when gaze points left",),
            ),
        ),
        payload_slots=(
            _slot(
                "card_right_third",
                "payload_card",
                _box(0.57, 0.16, 0.96, 0.68),
                component_types=("benefit_list_card",),
            ),
            _slot(
                "card_left_third",
                "payload_card",
                _box(0.04, 0.16, 0.43, 0.68),
                priority=2,
                component_types=("benefit_list_card",),
            ),
        ),
        headline_slots=(
            _slot(
                "bottom_headline",
                "headline_text",
                _box(0.03, 0.66, 0.97, 0.96),
                optional=True,
            ),
        ),
        asset_slots=(
            _slot(
                "small_object_near_card",
                "supporting_asset",
                _box(0.70, 0.47, 0.95, 0.78),
                priority=2,
                component_types=("object_cutout", "small_badge"),
                optional=True,
            ),
        ),
        visual_weight="host_left_payload_right_or_mirrored",
        z_order_policy=("background", "payload_card", "supporting_asset", "host", "headline_text"),
        overlap_policy=_common_overlap_policy(),
        style_policy=(
            "warm A-roll background",
            "paper or note texture, not web-panel chrome",
            "three short benefit labels",
            "avoid medical miracle language",
        ),
        cheap_eval_gates={**_BASE_CHEAP_GATES, "min_face_height_ratio": 0.40},
        vision_eval_rubric=_BASE_RUBRIC,
    ),
    TemplateContract(
        template_id="ali_metric_arrow",
        creator_target="ali_abdaal",
        intent_tags=("before_after", "metric", "transformation", "simple_arrow"),
        host_slots=(
            _slot("host_center", "host", _box(0.25, -0.04, 0.75, 1.12)),
            _slot("host_left_support", "host", _box(-0.03, -0.04, 0.52, 1.12), priority=2),
        ),
        payload_slots=(
            _slot(
                "metric_left",
                "payload_metric",
                _box(0.04, 0.18, 0.30, 0.50),
                component_types=("metric_badge_pair",),
            ),
            _slot(
                "metric_right",
                "payload_metric",
                _box(0.70, 0.18, 0.96, 0.50),
                component_types=("metric_badge_pair",),
            ),
            _slot(
                "arrow_top_arc",
                "payload_arrow",
                _box(0.30, 0.03, 0.72, 0.28),
                component_types=("arrow",),
            ),
        ),
        headline_slots=(),
        asset_slots=(
            _slot(
                "small_topic_icon",
                "supporting_asset",
                _box(0.43, 0.12, 0.57, 0.30),
                component_types=("simple_icon",),
                optional=True,
            ),
        ),
        visual_weight="two_metrics_host_center",
        z_order_policy=("background", "payload_metric", "payload_arrow", "host"),
        overlap_policy=_common_overlap_policy(),
        style_policy=(
            "large simple numbers",
            "one arrow only",
            "no dense explanatory labels",
        ),
        cheap_eval_gates={**_BASE_CHEAP_GATES, "max_primary_payloads": 2},
        vision_eval_rubric=_BASE_RUBRIC,
    ),
    TemplateContract(
        template_id="ali_social_quote_card",
        creator_target="ali_abdaal",
        intent_tags=("advice", "quote_card", "soft_contrarian", "personal"),
        host_slots=(
            _slot("host_right_third", "host", _box(0.42, -0.04, 1.05, 1.12)),
            _slot("host_left_third", "host", _box(-0.05, -0.04, 0.58, 1.12), priority=2),
        ),
        payload_slots=(
            _slot(
                "quote_card_left",
                "payload_card",
                _box(0.04, 0.12, 0.52, 0.55),
                component_types=("quote_card", "social_post_card"),
            ),
            _slot(
                "quote_card_right",
                "payload_card",
                _box(0.48, 0.12, 0.96, 0.55),
                priority=2,
                component_types=("quote_card", "social_post_card"),
            ),
        ),
        headline_slots=(),
        asset_slots=(),
        visual_weight="one_card_one_host",
        z_order_policy=("background", "payload_card", "host"),
        overlap_policy=_common_overlap_policy(),
        style_policy=(
            "one clean card",
            "short readable phrase",
            "no dense social text",
        ),
        cheap_eval_gates=_BASE_CHEAP_GATES,
        vision_eval_rubric=_BASE_RUBRIC,
    ),
    TemplateContract(
        template_id="jeff_tool_header_panel",
        creator_target="jeff_su",
        intent_tags=("tool", "tutorial", "ui_panel", "productivity"),
        host_slots=(
            _slot("host_left_third", "host", _box(0.02, -0.02, 0.48, 1.08)),
            _slot("host_right_third", "host", _box(0.52, -0.02, 0.98, 1.08), priority=2),
        ),
        payload_slots=(
            _slot(
                "ui_panel_right",
                "payload_card",
                _box(0.46, 0.18, 0.96, 0.64),
                component_types=("ui_panel",),
            ),
            _slot(
                "tool_label_top",
                "payload_label",
                _box(0.08, 0.04, 0.92, 0.18),
                component_types=("tool_label",),
            ),
        ),
        headline_slots=(),
        asset_slots=(
            _slot(
                "logo_tile",
                "supporting_asset",
                _box(0.70, 0.48, 0.93, 0.78),
                component_types=("logo_tile",),
                optional=True,
            ),
        ),
        visual_weight="host_one_third_ui_payload_opposite",
        z_order_policy=("background", "payload_card", "supporting_asset", "host", "payload_label"),
        overlap_policy=_common_overlap_policy(),
        style_policy=(
            "clean UI grammar",
            "one product label",
            "no tiny real screenshots",
        ),
        cheap_eval_gates=_BASE_CHEAP_GATES,
        vision_eval_rubric=_BASE_RUBRIC,
    ),
    TemplateContract(
        template_id="jeff_command_panel",
        creator_target="jeff_su",
        intent_tags=("mistake", "command_panel", "do_this_instead", "tutorial"),
        host_slots=(
            _slot("host_right_pointing", "host", _box(0.46, -0.02, 1.04, 1.08)),
            _slot("host_left_pointing", "host", _box(-0.04, -0.02, 0.54, 1.08), priority=2),
        ),
        payload_slots=(
            _slot(
                "dark_command_panel_left",
                "payload_card",
                _box(0.05, 0.10, 0.62, 0.66),
                component_types=("command_panel", "action_label"),
            ),
            _slot(
                "dark_command_panel_right",
                "payload_card",
                _box(0.38, 0.10, 0.95, 0.66),
                priority=2,
                component_types=("command_panel", "action_label"),
            ),
        ),
        headline_slots=(
            _slot(
                "bottom_action_label",
                "headline_text",
                _box(0.05, 0.70, 0.72, 0.92),
                optional=True,
            ),
        ),
        asset_slots=(
            _slot(
                "tool_logo_corner",
                "supporting_asset",
                _box(0.05, 0.08, 0.18, 0.25),
                component_types=("tool_logo",),
                optional=True,
            ),
        ),
        visual_weight="dark_panel_host_adjacent",
        z_order_policy=("background", "payload_card", "supporting_asset", "host", "headline_text"),
        overlap_policy=_common_overlap_policy(),
        style_policy=(
            "dark command panel",
            "one action phrase",
            "avoid dense code blocks",
        ),
        cheap_eval_gates=_BASE_CHEAP_GATES,
        vision_eval_rubric=_BASE_RUBRIC,
    ),
)

_CONTRACT_BY_ID = {contract.template_id: contract for contract in CONTRACTS}


def get_template_contract(template_id: str) -> TemplateContract:
    try:
        return _CONTRACT_BY_ID[template_id]
    except KeyError as exc:
        raise KeyError(f"unknown thumbnail template contract: {template_id}") from exc


def list_template_contracts() -> tuple[TemplateContract, ...]:
    return CONTRACTS


def find_overlap_rule(
    contract: TemplateContract,
    *,
    foreground_role: str,
    background_role: str,
) -> OverlapRule | None:
    for rule in contract.overlap_policy:
        if rule.foreground_role == foreground_role and rule.background_role == background_role:
            return rule
    return None


def format_template_contract_for_prompt(template_id: str) -> str:
    contract = get_template_contract(template_id)
    primary_payloads = [
        slot
        for slot in sorted(contract.payload_slots, key=lambda item: item.priority)
        if slot.priority == 1
    ]
    lines = [
        f"template_id: {contract.template_id}",
        f"creator_target: {contract.creator_target}",
        f"intent_tags: {', '.join(contract.intent_tags)}",
        f"visual_weight: {contract.visual_weight}",
        "primary_host_slots:",
    ]
    lines.extend(
        f"- {slot.slot_id}: {slot.box.to_dict()} ({'; '.join(slot.notes)})"
        for slot in sorted(contract.host_slots, key=lambda item: item.priority)
        if slot.priority == 1
    )
    lines.append("primary_payload_slots:")
    lines.extend(
        f"- {slot.slot_id}: {slot.box.to_dict()} components={', '.join(slot.component_types)}"
        for slot in primary_payloads
    )
    if contract.headline_slots:
        lines.append("headline_slots:")
        lines.extend(
            f"- {slot.slot_id}: {slot.box.to_dict()} optional={slot.optional}"
            for slot in contract.headline_slots
        )
    lines.append("overlap_policy:")
    lines.extend(
        (
            f"- {rule.foreground_role} over {rule.background_role}: {rule.status}; "
            f"max_iou={rule.max_iou}; max_text_iou={rule.max_protected_text_iou}"
        )
        for rule in contract.overlap_policy
    )
    lines.append(f"style_policy: {'; '.join(contract.style_policy)}")
    return "\n".join(lines)


def missing_contract_ids() -> set[str]:
    return set(REFERENCE_TEMPLATE_IDS) - set(_CONTRACT_BY_ID)
