"""Build v1 per-image thumbnail reference deconstruction records.

The output is a normalized JSONL layer derived from the Ali/Jeff family
taxonomy. It is intentionally conservative: boxes are family-level estimates,
with explicit notes that a later vision pass can refine exact coordinates.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parent.parent
TAXONOMY_PATH = (
    REPO_ROOT / "prompts" / "thumbnail" / "ali_jeff_template_family_taxonomy_v1.json"
)
OUT_PATH = REPO_ROOT / "prompts" / "thumbnail" / "reference_corpus" / (
    "ali_jeff_deconstructions_v1.jsonl"
)

BOXES = {
    "left_host": [-0.04, -0.04, 0.48, 1.08],
    "right_host": [0.52, -0.04, 1.04, 1.08],
    "center_host": [0.25, -0.04, 0.75, 1.08],
    "left_face": [0.10, 0.06, 0.34, 0.40],
    "right_face": [0.66, 0.06, 0.90, 0.40],
    "center_face": [0.40, 0.06, 0.60, 0.40],
    "left_payload": [0.05, 0.16, 0.46, 0.62],
    "right_payload": [0.54, 0.16, 0.95, 0.62],
    "center_payload": [0.24, 0.16, 0.76, 0.66],
    "top_label": [0.08, 0.04, 0.55, 0.18],
    "bottom_banner": [0.04, 0.68, 0.72, 0.92],
    "left_metric": [0.05, 0.22, 0.30, 0.46],
    "right_metric": [0.70, 0.22, 0.95, 0.46],
    "top_arrow": [0.30, 0.04, 0.72, 0.28],
}


def main() -> None:
    taxonomy = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    families = {family["family_id"]: family for family in taxonomy["families"]}
    records = [
        build_record(assignment, families[assignment["family_id"]])
        for assignment in taxonomy["image_family_assignments"]
    ]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records)
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(records)} records to {OUT_PATH}")


def build_record(assignment: dict[str, Any], family: dict[str, Any]) -> dict[str, Any]:
    family_id = assignment["family_id"]
    host = host_record(family_id, family)
    components = component_records(family_id, assignment, family)
    return {
        "schema_version": "ali_jeff_template_corpus_v1",
        "reference_id": assignment["reference_id"],
        "creator": assignment["creator"],
        "title": assignment["title"],
        "image_path": assignment["image_path"],
        "image_size": image_size(assignment["image_path"]),
        "priority": assignment["priority"],
        "renderable_v1": assignment["renderable_v1"],
        "template_family_candidate": family_id,
        "family_confidence": "medium" if family["production_status"] == "archive" else "high",
        "title_intent_tags": title_intent_tags(family_id, family),
        "composition": composition_record(family_id, family),
        "background": background_record(family_id, family),
        "host": host,
        "components": components,
        "typography": typography_records(family_id, assignment, family),
        "overlap_rules": overlap_rules(host["present"]),
        "style_tokens": family["style_tokens"],
        "generator_constraints": generator_constraints(family),
        "evaluation_targets": evaluation_targets(family_id, family),
        "notes": [
            "v1 normalized from family taxonomy and contact-sheet pass",
            "bounding boxes are family-level estimates; refine with vision annotation before pixel-perfect matching",
        ],
    }


def image_size(path_text: str) -> list[int]:
    with Image.open(path_text) as img:
        return [int(img.width), int(img.height)]


def title_intent_tags(family_id: str, family: dict[str, Any]) -> list[str]:
    tags = [family_id, family["creator_style"].lower().replace(" ", "_")]
    tags.extend(normalize_tag(item) for item in family["best_for"][:3])
    return list(dict.fromkeys(tags))


def normalize_tag(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "visual_reference"


def composition_record(family_id: str, family: dict[str, Any]) -> dict[str, Any]:
    balance = "centered"
    zones = ["center_host", "surrounding_payload"]
    if "split" in family_id:
        balance = "split"
        zones = ["left_half", "right_half"]
    elif "jeff_" in family_id and "command" not in family_id:
        balance = "mixed"
        zones = ["host_third", "ui_payload"]
    elif "social_quote" in family_id or "ui_panel" in family_id:
        balance = "right_heavy"
        zones = ["left_payload", "right_host"]
    elif "command_text" in family_id:
        balance = "left_heavy"
        zones = ["left_text", "right_host"]

    return {
        "layout_summary": family["label"],
        "focal_hierarchy": focal_hierarchy(family_id),
        "reading_path": reading_path(family_id),
        "visual_balance": balance,
        "negative_space": "medium" if family["production_status"] != "archive" else "low",
        "dominant_zones": zones,
    }


def focal_hierarchy(family_id: str) -> list[str]:
    if "metric" in family_id:
        return ["metric contrast", "host face", "arrow"]
    if "tool" in family_id or "ui_panel" in family_id:
        return ["host face", "tool or UI panel", "product label"]
    if "command" in family_id:
        return ["command panel", "host face", "action label"]
    if "social_quote" in family_id:
        return ["quote card", "host face"]
    if "logo_cluster" in family_id or "icon_cluster" in family_id:
        return ["host face", "icon cluster"]
    return ["primary payload", "host face"]


def reading_path(family_id: str) -> str:
    if "metric" in family_id:
        return "left metric -> host face -> right metric"
    if "tool" in family_id:
        return "tool label -> host face -> UI panel"
    if "command" in family_id:
        return "command panel -> host face -> action label"
    if "social_quote" in family_id:
        return "quote card -> host face"
    return "host face -> primary payload"


def background_record(family_id: str, family: dict[str, Any]) -> dict[str, Any]:
    if "before_after" in family_id:
        bg_type = "split_screen"
        temperature = "mixed"
    elif family_id in {"jeff_command_panel", "jeff_logo_cluster_dark", "jeff_trend_collage"}:
        bg_type = "dark_ui"
        temperature = "cool"
    elif "whiteboard" in family_id:
        bg_type = "light_ui"
        temperature = "neutral"
    elif family_id.startswith("jeff_"):
        bg_type = "blurred_aroll"
        temperature = "cool"
    else:
        bg_type = "studio_room"
        temperature = "warm"
    return {
        "type": bg_type,
        "detail_level": "low" if family["production_status"] != "archive" else "high",
        "color_temperature": temperature,
        "brand_reuse_potential": "high" if bg_type in {"blurred_aroll", "studio_room"} else "medium",
        "notes": [family["slot_model"]["background"]["policy"]],
    }


def host_record(family_id: str, family: dict[str, Any]) -> dict[str, Any]:
    if family_id == "ali_whiteboard_diagram":
        return host_none(family)

    if family_id in {"ali_metric_arrow", "jeff_dual_tool_cards", "jeff_logo_cluster_dark"}:
        return host_present(
            family,
            placement="center",
            gaze="camera",
            expression="smiling" if family_id.startswith("jeff_") else "confident",
            gesture="chin_touch" if family_id.startswith("jeff_") else "two_hands",
            crop="torso",
            box_key="center_host",
            face_key="center_face",
        )
    if family_id in {"ali_social_quote_card", "ali_toggle_metaphor", "jeff_command_panel",
                     "jeff_metric_time_saving", "jeff_roadmap_board", "jeff_text_overlay_panel"}:
        return host_present(
            family,
            placement="right_third",
            gaze="left",
            expression=expression_for(family_id),
            gesture=gesture_for(family_id, default="pointing_left"),
            crop="torso",
            box_key="right_host",
            face_key="right_face",
        )
    return host_present(
        family,
        placement="left_third",
        gaze="right",
        expression=expression_for(family_id),
        gesture=gesture_for(family_id, default="pointing_right"),
        crop="torso",
        box_key="left_host",
        face_key="left_face",
    )


def host_none(family: dict[str, Any]) -> dict[str, Any]:
    return {
        "present": False,
        "box_pct": None,
        "face_box_pct": None,
        "face_height_ratio": 0.0,
        "placement": "none",
        "gaze": "none",
        "expression": "n/a",
        "gesture": "n/a",
        "crop": "none",
        "occlusion_role": "n/a",
        "placement_rule": family["slot_model"]["host"]["gaze_policy"],
    }


def host_present(
    family: dict[str, Any],
    *,
    placement: str,
    gaze: str,
    expression: str,
    gesture: str,
    crop: str,
    box_key: str,
    face_key: str,
) -> dict[str, Any]:
    min_face, max_face = family["slot_model"]["host"]["face_height_ratio"]
    return {
        "present": True,
        "box_pct": BOXES[box_key],
        "face_box_pct": BOXES[face_key],
        "face_height_ratio": round((float(min_face) + float(max_face)) / 2, 2),
        "placement": placement,
        "gaze": gaze,
        "expression": expression,
        "gesture": gesture,
        "crop": crop,
        "occlusion_role": "separate",
        "placement_rule": family["slot_model"]["host"]["gaze_policy"],
    }


def expression_for(family_id: str) -> str:
    if "before_after" in family_id:
        return "serious_thoughtful"
    if "metric" in family_id:
        return "surprised"
    if "command" in family_id:
        return "confident" if family_id.startswith("ali_") else "surprised"
    if "social_quote" in family_id:
        return "neutral_smile"
    if "evidence" in family_id:
        return "focused"
    if family_id.startswith("jeff_"):
        return "smiling_explaining"
    return "smiling"


def gesture_for(family_id: str, *, default: str) -> str:
    if "social_quote" in family_id or "text_overlay" in family_id:
        return "none"
    if "evidence" in family_id:
        return "holding_object"
    if "icon_cluster" in family_id:
        return "open_hands"
    if "toggle" in family_id:
        return "holding_object"
    return default


def component_records(
    family_id: str,
    assignment: dict[str, Any],
    family: dict[str, Any],
) -> list[dict[str, Any]]:
    title = assignment["title"]
    if family_id in {"ali_metric_arrow"}:
        left, right = metric_pair(title)
        return [
            component("metric_left", "metric", "metric_badge_pair", BOXES["left_metric"], [left],
                      "large white number with dark stroke", "shows the starting metric", 20),
            component("metric_right", "metric", "metric_badge_pair", BOXES["right_metric"], [right],
                      "large white number with dark stroke", "shows the desired metric", 20),
            component("arrow", "connector", "arrow", BOXES["top_arrow"], [],
                      "green curved arrow", "connects before to after", 22),
        ]
    if family_id == "jeff_dual_tool_cards":
        tools = product_tokens(title, fallback=["Tool A", "Tool B"])[:2]
        return [
            component("left_tool_card", "primary_payload", "ui_panel", BOXES["left_payload"], [tools[0]],
                      "rounded white tool card", "shows the first compared tool", 20),
            component("right_tool_card", "primary_payload", "ui_panel", BOXES["right_payload"],
                      [tools[1] if len(tools) > 1 else "Tool B"],
                      "rounded white tool card", "shows the second compared tool", 20),
        ]
    if family_id == "jeff_tool_header_panel":
        tool = product_tokens(title, fallback=["Tool"])[0]
        return [
            component("tool_label", "embedded_label", "tool_label", BOXES["top_label"], [tool],
                      "black rounded product label", "names the tool at first glance", 25),
            component("ui_panel", "primary_payload", "ui_panel", BOXES["right_payload"],
                      panel_lines(title), "clean rounded UI panel", "shows the tutorial starting point", 20),
            component("logo_tile", "brand_logo", "logo_tile", [0.72, 0.46, 0.93, 0.78], [tool],
                      "white logo tile", "anchors the product visually", 23, required=False),
        ]
    if family_id == "jeff_command_panel":
        return [
            component("command_panel", "primary_payload", "command_panel", BOXES["left_payload"],
                      command_lines(title), "dark command panel with blue accent",
                      "shows the corrected action", 20),
            component("tool_logo", "brand_logo", "tool_logo", [0.06, 0.08, 0.18, 0.24],
                      product_tokens(title, fallback=["AI"])[:1], "small tool logo", "names the tool", 22,
                      required=False),
        ]
    if family_id in {"jeff_ui_panel_host_side", "ali_ui_panel_host_side"}:
        return [
            component("ui_panel", "primary_payload", "ui_panel", BOXES["right_payload"],
                      panel_lines(title), "large clean UI or workflow panel",
                      "visualizes the workflow promised by the title", 20),
        ]
    if family_id in {"jeff_logo_cluster_dark", "ali_icon_cluster_host"}:
        return [
            component("icon_cluster", "primary_payload", "app_icon_cluster", BOXES["right_payload"],
                      product_tokens(title, fallback=["Tools", "Apps", "System"])[:4],
                      "floating icon cluster", "represents the tool or concept set", 20),
        ]
    if family_id == "jeff_metric_time_saving":
        left, right = metric_pair(title)
        return [
            component("metric", "metric", "metric_badge_pair", BOXES["left_metric"], [left or "8"],
                      "huge productivity number", "creates the numbered promise", 20),
            component("app_icon", "brand_logo", "app_icon", [0.32, 0.20, 0.48, 0.42],
                      product_tokens(title, fallback=["ChatGPT"])[:1], "tool icon", "ties metric to tool", 22),
        ]
    if family_id == "jeff_roadmap_board":
        return [
            component("roadmap", "primary_payload", "whiteboard_diagram", BOXES["left_payload"],
                      ["Start", "Build", "Use"], "dark roadmap board", "shows a learning path", 20),
        ]
    if family_id == "jeff_step_cards":
        return [
            component("step_1", "primary_payload", "step_card", [0.08, 0.16, 0.46, 0.38],
                      ["Step 1"], "rounded checklist card", "first system step", 20),
            component("step_2", "primary_payload", "step_card", [0.08, 0.42, 0.46, 0.64],
                      ["Step 2"], "rounded checklist card", "second system step", 21),
        ]
    if family_id == "ali_social_quote_card":
        return [
            component("quote_card", "primary_payload", "social_post_card", BOXES["left_payload"],
                      [short_phrase(title, 32)], "white social quote card", "turns the title into advice",
                      20),
        ]
    if family_id == "ali_before_after_split":
        return [
            component("before_after", "primary_payload", "before_after_panel", [0.04, 0.10, 0.96, 0.64],
                      ["Before", "After"], "split contrast panel", "shows the transformation", 18),
        ]
    if family_id == "ali_evidence_stack":
        return [
            component("evidence_stack", "primary_payload", "object_cutout", BOXES["right_payload"],
                      evidence_labels(title), "book or proof object stack", "shows evidence volume", 20),
        ]
    if family_id == "ali_income_cards":
        return [
            component("income_cards", "primary_payload", "social_post_card", BOXES["right_payload"],
                      metric_pair(title), "floating money/platform cards", "shows income opportunity",
                      20),
        ]
    if family_id == "ali_year_hero":
        return [
            component("year", "primary_payload", "text_banner", BOXES["left_payload"],
                      [year_or_number(title)], "giant year or number", "anchors the future promise", 20),
        ]
    if family_id == "ali_command_text_host" or family_id == "jeff_text_overlay_panel":
        return [
            component("main_text", "primary_payload", "text_banner", BOXES["left_payload"],
                      [short_phrase(title, 24)], "large bold text", "states the core claim", 20),
        ]
    if family_id == "ali_toggle_metaphor":
        return [
            component("toggle", "primary_payload", "tool_label", BOXES["left_payload"],
                      [short_phrase(title, 14)], "white rounded switch metaphor",
                      "turns the abstract idea into one visual control", 20),
        ]
    if family_id == "ali_whiteboard_diagram":
        return [
            component("diagram", "primary_payload", "whiteboard_diagram", BOXES["center_payload"],
                      ["Cue", "Action", "Reward"], "whiteboard mechanism diagram",
                      "explains the system visually", 20),
        ]
    return [
        component("primary_payload", "primary_payload",
                  family["slot_model"]["primary_payload"]["allowed_component_types"][0],
                  BOXES["left_payload"], [short_phrase(title, 24)], "family primary payload",
                  "carries the thumbnail promise", 20)
    ]


def component(
    component_id: str,
    role: str,
    component_type: str,
    box_pct: list[float],
    text: list[str],
    visual_style: str,
    semantic_job: str,
    z_index: int,
    *,
    required: bool = True,
) -> dict[str, Any]:
    return {
        "component_id": component_id,
        "role": role,
        "type": component_type,
        "box_pct": box_pct,
        "text": text,
        "visual_style": visual_style,
        "semantic_job": semantic_job,
        "asset_needs": asset_needs(component_type, text),
        "z_index": z_index,
        "required": required,
    }


def asset_needs(component_type: str, text: list[str]) -> list[str]:
    if component_type in {"app_icon", "app_icon_cluster", "logo_tile", "tool_logo"}:
        return [item for item in text if item]
    if component_type == "object_cutout":
        return ["book/object cutout"]
    return []


def typography_records(
    family_id: str,
    assignment: dict[str, Any],
    family: dict[str, Any],
) -> list[dict[str, Any]]:
    if family_id not in {"ali_command_text_host", "jeff_text_overlay_panel", "ali_year_hero"}:
        return []
    return [
        {
            "text_id": "main_headline",
            "text": short_phrase(assignment["title"], 28),
            "box_pct": BOXES["bottom_banner"] if family_id == "ali_command_text_host" else BOXES["left_payload"],
            "style": "bold_sans",
            "case": "mixed",
            "max_words": int(family["slot_model"]["typography"]["max_words"]),
            "color": "white",
            "stroke_or_shadow": "dark stroke and drop shadow",
            "readability": "high",
        }
    ]


def overlap_rules(host_present: bool) -> list[dict[str, str]]:
    if not host_present:
        return []
    return [
        {
            "foreground": "host_face",
            "background": "primary_payload",
            "status": "forbidden",
            "protected_region": "face",
            "note": "payload may not cover the face or expression",
        },
        {
            "foreground": "host_hand",
            "background": "primary_payload",
            "status": "tolerated",
            "protected_region": "keyword_text",
            "note": "hand may create depth over a card edge, but not over keywords or logos",
        },
    ]


def generator_constraints(family: dict[str, Any]) -> dict[str, Any]:
    max_primary = int(family["slot_model"]["primary_payload"]["max_count"])
    return {
        "must_preserve": [
            f"template family: {family['family_id']}",
            f"allowed component types: {', '.join(family['slot_model']['primary_payload']['allowed_component_types'])}",
            family["slot_model"]["host"]["gaze_policy"],
        ],
        "must_avoid": family["template_breakers"] + family["avoid"][:2],
        "max_primary_components": max_primary,
        "max_total_components": max(max_primary + 2, 2),
        "minimum_face_height_ratio": float(family["cheap_eval_gates"]["min_face_height_ratio"]),
        "template_breakers": family["template_breakers"],
    }


def evaluation_targets(family_id: str, family: dict[str, Any]) -> dict[str, Any]:
    return {
        "first_glance_should_read_as": family["label"],
        "small_size_checks": [
            "host expression visible" if "whiteboard" not in family_id else "diagram labels readable",
            "primary payload readable",
            "no more visual elements than the family allows",
        ],
        "reference_fit_checks": [
            f"uses {family_id}",
            "payload component count matches the reference family",
            "host placement and gaze follow the family policy",
        ],
        "repair_hints": [
            "increase host scale if face salience is below target",
            "move payload opposite host gaze when the family requires interaction",
            "shorten text to the family typography limit",
        ],
    }


def metric_pair(title: str) -> list[str]:
    tokens = re.findall(r"\$?\d+[A-Za-z%]*|[０-９]+", title)
    if len(tokens) >= 2:
        return [tokens[0], tokens[1]]
    if len(tokens) == 1:
        return ["0", tokens[0]]
    return ["Before", "After"]


def product_tokens(title: str, *, fallback: list[str]) -> list[str]:
    stop = {"learn", "master", "guide", "under", "minutes", "wrong", "right", "correct"}
    tokens = [
        token
        for token in re.findall(r"[A-Za-z][A-Za-z0-9-]{2,24}", title)
        if token.lower() not in stop
    ]
    return tokens or fallback


def panel_lines(title: str) -> list[str]:
    products = product_tokens(title, fallback=[])
    if products:
        return [products[0], "Start Here"]
    return [short_phrase(title, 18)]


def command_lines(title: str) -> list[str]:
    if "Wrong" in title or "Fail" in title or "Rejected" in title:
        return ["Wrong", "Do this instead"]
    if "CORRECT" in title:
        return ["Correct way"]
    return [short_phrase(title, 18)]


def evidence_labels(title: str) -> list[str]:
    tokens = metric_pair(title)
    if tokens != ["Before", "After"]:
        return tokens
    return ["Books", "Tools", "Evidence"]


def year_or_number(title: str) -> str:
    tokens = re.findall(r"20\d\d|\d+", title)
    return tokens[0] if tokens else "Next"


def short_phrase(title: str, max_chars: int) -> str:
    cleaned = re.sub(r"\([^)]*\)", "", title)
    cleaned = re.sub(r"[:|].*$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_chars] or "Key idea"


if __name__ == "__main__":
    main()
