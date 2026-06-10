from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.thumbnail_reference_corpus import (
    best_reference_examples,
    best_deconstruction_examples,
    family_ids,
    format_deconstruction_record_for_prompt,
    format_template_family_for_prompt,
    get_template_family,
    list_deconstruction_records,
    list_reference_assignments,
    list_template_families,
    load_deconstruction_records,
    load_family_taxonomy,
    missing_deconstruction_image_paths,
    missing_reference_image_paths,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "prompts" / "thumbnail" / "reference_template_deconstruction_schema_v1.json"


def _record_schema() -> dict:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return schema["$defs"]["record"]


def test_load_family_taxonomy_reads_scoped_ali_jeff_corpus():
    taxonomy = load_family_taxonomy()

    assert taxonomy["scope"]["image_count"] == 54
    assert taxonomy["scope"]["creators"] == ["Ali Abdaal", "Jeff Su"]
    assert len(taxonomy["families"]) >= 10


def test_load_deconstruction_records_reads_54_per_image_records():
    records = load_deconstruction_records()

    assert len(records) == 54
    assert {record["schema_version"] for record in records} == {"ali_jeff_template_corpus_v1"}
    assert len({record["reference_id"] for record in records}) == 54


def test_list_template_families_filters_by_creator_status_and_priority():
    ali_gold = list_template_families(
        creator_style="Ali Abdaal",
        production_status="renderable_v1",
        priority="gold",
    )
    jeff_archive = list_template_families(
        creator_style="Jeff Su",
        production_status="archive",
    )

    assert "ali_metric_arrow" in family_ids(ali_gold)
    assert family_ids(jeff_archive) == {"jeff_trend_collage"}


def test_get_template_family_raises_for_unknown_id():
    with pytest.raises(KeyError, match="unknown thumbnail template family"):
        get_template_family("missing_family")


def test_list_reference_assignments_filters_renderable_examples():
    all_assignments = list_reference_assignments()
    renderable = list_reference_assignments(renderable_v1=True)
    non_renderable = list_reference_assignments(renderable_v1=False)

    assert len(all_assignments) == 54
    assert len(renderable) == 52
    assert {item["family_id"] for item in non_renderable} == {
        "ali_whiteboard_diagram",
        "jeff_trend_collage",
    }


def test_deconstruction_records_match_taxonomy_assignments_one_to_one():
    taxonomy = load_family_taxonomy()
    assignments = {item["reference_id"]: item for item in taxonomy["image_family_assignments"]}
    records = {item["reference_id"]: item for item in load_deconstruction_records()}

    assert set(records) == set(assignments)
    for reference_id, record in records.items():
        assignment = assignments[reference_id]
        assert record["template_family_candidate"] == assignment["family_id"]
        assert record["image_path"] == assignment["image_path"]
        assert record["renderable_v1"] is assignment["renderable_v1"]
        assert record["priority"] == assignment["priority"]


def test_best_reference_examples_prefers_gold_records_for_family():
    examples = best_reference_examples("jeff_tool_header_panel", limit=3)

    assert len(examples) == 3
    assert all(item["family_id"] == "jeff_tool_header_panel" for item in examples)
    assert all(item["priority"] == "gold" for item in examples)
    assert examples[0]["reference_id"] == "jeff_su_008"


def test_best_deconstruction_examples_prefers_renderable_gold_records_for_family():
    examples = best_deconstruction_examples("jeff_tool_header_panel", limit=3)

    assert len(examples) == 3
    assert all(item["template_family_candidate"] == "jeff_tool_header_panel" for item in examples)
    assert all(item["renderable_v1"] for item in examples)
    assert examples[0]["reference_id"] == "jeff_su_008"


def test_missing_reference_image_paths_is_empty_for_current_folders():
    assert missing_reference_image_paths() == []


def test_missing_deconstruction_image_paths_is_empty_for_current_folders():
    assert missing_deconstruction_image_paths() == []


def test_deconstruction_records_follow_required_schema_shape_and_enums():
    schema = _record_schema()
    required = set(schema["required"])
    component_schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))["$defs"]["component"]
    component_types = set(component_schema["properties"]["type"]["enum"])
    component_roles = set(component_schema["properties"]["role"]["enum"])

    for record in load_deconstruction_records():
        assert required.issubset(record)
        assert record["schema_version"] == "ali_jeff_template_corpus_v1"
        assert Path(record["image_path"]).is_file()
        assert len(record["image_size"]) == 2
        assert all(value > 0 for value in record["image_size"])
        assert record["components"]
        assert record["generator_constraints"]["template_breakers"]
        assert record["evaluation_targets"]["reference_fit_checks"]
        for component in record["components"]:
            assert component["type"] in component_types
            assert component["role"] in component_roles
            assert len(component["box_pct"]) == 4
            assert isinstance(component["required"], bool)


def test_list_deconstruction_records_filters_and_rejects_unknown_family():
    command_records = list_deconstruction_records(
        family_id="jeff_command_panel",
        renderable_v1=True,
    )

    assert {record["reference_id"] for record in command_records} == {
        "jeff_su_003",
        "jeff_su_023",
        "jeff_su_027",
        "jeff_su_029",
        "jeff_su_032",
    }
    with pytest.raises(KeyError, match="unknown thumbnail template family"):
        list_deconstruction_records(family_id="missing_family")


def test_format_template_family_for_prompt_is_compact_and_slot_based():
    text = format_template_family_for_prompt("jeff_tool_header_panel", max_examples=2)

    assert "family_id: jeff_tool_header_panel" in text
    assert "host_placements:" in text
    assert "face_height_ratio:" in text
    assert "gaze_policy:" in text
    assert "payload_types:" in text
    assert "examples:" in text
    assert "jeff_su_008" in text
    assert len(text) < 2600


def test_format_deconstruction_record_for_prompt_is_concrete_and_compact():
    record = best_deconstruction_examples("ali_metric_arrow", limit=1)[0]
    text = format_deconstruction_record_for_prompt(record)

    assert "reference_id: ali_abdaal_016" in text
    assert "family: ali_metric_arrow" in text
    assert "host:" in text
    assert "components:" in text
    assert "metric_left" in text
    assert len(text) < 1800
