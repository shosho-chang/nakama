from __future__ import annotations

from pathlib import Path

import yaml

TAXONOMY_PATH = Path("prompts/thumbnail/cutout_pose_tags.yml")


def test_cutout_pose_tags_taxonomy_is_parseable():
    data = yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8"))

    assert data["schema_version"] == "thumbnail_cutout_pose_tags.v1"
    assert "axes" in data
    assert "required_core_shots" in data


def test_required_core_shots_have_unique_ids_and_valid_axis_values():
    data = yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8"))
    axes = data["axes"]
    shots = data["required_core_shots"]
    seen: set[str] = set()

    for shot in shots:
        shot_id = shot["shot_id"]
        assert shot_id not in seen
        seen.add(shot_id)

        tags = shot["tags"]
        for axis_name, axis_def in axes.items():
            if axis_name in tags:
                assert tags[axis_name] in axis_def["values"], (shot_id, axis_name, tags[axis_name])

    assert sum(1 for shot in shots if shot["priority"] == "must_have") >= 8
