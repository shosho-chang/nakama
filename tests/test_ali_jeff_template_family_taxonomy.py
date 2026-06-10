from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
TAXONOMY_PATH = (
    REPO_ROOT / "prompts" / "thumbnail" / "ali_jeff_template_family_taxonomy_v1.json"
)
DECONSTRUCTION_SCHEMA_PATH = (
    REPO_ROOT / "prompts" / "thumbnail" / "reference_template_deconstruction_schema_v1.json"
)
ALI_DIR = Path("E:/thumbnail-example/Ali Abdaal")
JEFF_DIR = Path("E:/thumbnail-example/Jeff Su")


def _load_taxonomy() -> dict[str, Any]:
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))


def _families_by_id() -> dict[str, dict[str, Any]]:
    taxonomy = _load_taxonomy()
    return {family["family_id"]: family for family in taxonomy["families"]}


def _component_enum_from_deconstruction_schema() -> set[str]:
    schema = json.loads(DECONSTRUCTION_SCHEMA_PATH.read_text(encoding="utf-8"))
    return set(schema["$defs"]["component"]["properties"]["type"]["enum"])


def _folder_image_paths() -> set[str]:
    return {
        path.as_posix()
        for folder in (ALI_DIR, JEFF_DIR)
        for path in sorted(folder.glob("*.jpg"))
    }


def test_ali_jeff_taxonomy_loads_and_covers_current_reference_folders():
    taxonomy = _load_taxonomy()
    assignments = taxonomy["image_family_assignments"]

    assert taxonomy["schema_version"] == "ali_jeff_template_family_taxonomy_v1"
    assert taxonomy["scope"]["image_count"] == 54
    assert len(assignments) == 54
    assert {item["image_path"] for item in assignments} == _folder_image_paths()
    assert len({item["reference_id"] for item in assignments}) == 54


def test_every_assignment_targets_a_known_family_and_existing_file():
    families = _families_by_id()
    assignments = _load_taxonomy()["image_family_assignments"]

    for item in assignments:
        assert item["family_id"] in families
        assert Path(item["image_path"]).is_file()
        assert item["priority"] in {"gold", "silver", "archive"}
        assert isinstance(item["renderable_v1"], bool)


def test_family_ids_are_unique_and_include_expected_ali_jeff_patterns():
    taxonomy = _load_taxonomy()
    family_ids = [family["family_id"] for family in taxonomy["families"]]

    assert len(family_ids) == len(set(family_ids))
    assert {
        "ali_metric_arrow",
        "ali_social_quote_card",
        "ali_before_after_split",
        "jeff_tool_header_panel",
        "jeff_command_panel",
        "jeff_logo_cluster_dark",
        "jeff_ui_panel_host_side",
        "jeff_roadmap_board",
    }.issubset(set(family_ids))


def test_family_component_types_are_backed_by_the_deconstruction_schema():
    taxonomy = _load_taxonomy()
    registry = set(taxonomy["component_type_registry"])
    schema_component_types = _component_enum_from_deconstruction_schema()

    assert registry.issubset(schema_component_types)
    for family in taxonomy["families"]:
        slot_model = family["slot_model"]
        allowed = set(slot_model["primary_payload"]["allowed_component_types"])
        assert allowed
        assert allowed.issubset(registry)


def test_family_contracts_have_renderer_relevant_fields():
    for family in _load_taxonomy()["families"]:
        assert family["production_status"] in {"renderable_v1", "reference_only", "archive"}
        assert family["priority"] in {"gold", "silver", "archive"}
        assert family["best_for"]
        assert family["avoid"]
        assert family["example_paths"]
        assert family["slot_model"]["host"]["preferred_placements"]
        assert family["slot_model"]["host"]["face_height_ratio"][0] <= (
            family["slot_model"]["host"]["face_height_ratio"][1]
        )
        assert family["slot_model"]["primary_payload"]["preferred_zones"]
        assert family["template_breakers"]
        assert family["shot_needs"]
        for path in family["example_paths"]:
            assert Path(path).is_file()


def test_taxonomy_encodes_current_quality_lessons_from_visual_review():
    families = _families_by_id()

    metric = families["ali_metric_arrow"]
    assert metric["slot_model"]["host"]["face_height_ratio"][0] >= 0.34
    assert "center" in metric["slot_model"]["host"]["preferred_placements"]

    jeff_tool = families["jeff_tool_header_panel"]
    gaze_policy = jeff_tool["slot_model"]["host"]["gaze_policy"]
    assert "side-gaze" in gaze_policy
    assert "camera-facing" in gaze_policy

    assert families["ali_whiteboard_diagram"]["production_status"] == "reference_only"
    assert families["jeff_trend_collage"]["production_status"] == "archive"
    assert not any(
        item["renderable_v1"]
        for item in _load_taxonomy()["image_family_assignments"]
        if item["family_id"] == "jeff_trend_collage"
    )


def test_taxonomy_shot_needs_call_out_situational_photos_to_reshoot():
    all_shot_needs = " | ".join(
        need
        for family in _load_taxonomy()["families"]
        for need in family["shot_needs"]
    )

    assert "pointing left" in all_shot_needs
    assert "pointing right" in all_shot_needs
    assert "reading with headphones" in all_shot_needs
    assert "phone distracted" in all_shot_needs
    assert "focused writing" in all_shot_needs
