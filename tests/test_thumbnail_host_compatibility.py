from __future__ import annotations

from shared.thumbnail_host_compatibility import (
    build_cast_request_for_reference,
    check_host_compatibility,
)
from shared.thumbnail_reference_corpus import best_deconstruction_examples


def _candidate(**tags):
    face_height_ratio = tags.pop("face_height_ratio", None)
    defaults = {
        "gaze": "camera",
        "hands": "none",
        "expression_family": "soft_smile",
        "intensity": "mild",
        "credibility": "high",
    }
    defaults.update(tags)
    return {
        "cutout_id": "C1",
        "tags": defaults,
        "picker_policy": "eligible",
        "face_height_ratio": face_height_ratio,
    }


def test_center_host_template_rejects_side_gaze_cutout():
    record = best_deconstruction_examples("ali_metric_arrow", limit=1)[0]

    result = check_host_compatibility(
        reference_record=record,
        cutout_candidate=_candidate(
            gaze="screen_right",
            hands="open_palms_both",
            expression_family="confident",
        ),
    )

    assert not result.ok
    assert result.needs_new_photo
    assert any("gaze_mismatch" in reason for reason in result.reasons)
    assert "camera-facing" in " ".join(result.repair_hints)


def test_center_host_template_accepts_camera_facing_comparison_pose():
    record = best_deconstruction_examples("ali_metric_arrow", limit=1)[0]

    result = check_host_compatibility(
        reference_record=record,
        cutout_candidate=_candidate(
            gaze="camera",
            hands="open_palms_both",
            expression_family="confident",
            face_height_ratio=0.42,
        ),
    )

    assert result.ok
    assert result.score >= 90
    assert "gaze_ok:camera" in result.reasons
    assert "gesture_ok:open_palms_both" in result.reasons


def test_command_panel_requires_pointing_toward_panel():
    record = best_deconstruction_examples("jeff_command_panel", limit=1)[0]

    wrong = check_host_compatibility(
        reference_record=record,
        cutout_candidate=_candidate(
            gaze="screen_left",
            hands="point_screen_right",
            expression_family="mild_surprise",
        ),
    )
    right = check_host_compatibility(
        reference_record=record,
        cutout_candidate=_candidate(
            gaze="screen_left",
            hands="point_screen_left",
            expression_family="mild_surprise",
        ),
    )

    assert not wrong.ok
    assert any("gesture_mismatch" in reason for reason in wrong.reasons)
    assert right.ok
    assert "gesture_ok:point_screen_left" in right.reasons


def test_non_renderable_reference_fails_before_pose_checks():
    record = best_deconstruction_examples(
        "jeff_trend_collage",
        limit=1,
        renderable_only=False,
    )[0]

    result = check_host_compatibility(
        reference_record=record,
        cutout_candidate=_candidate(),
    )

    assert not result.ok
    assert result.reasons == ("reference_not_renderable",)
    assert not result.needs_new_photo


def test_face_too_small_is_a_hard_failure_when_ratio_is_known():
    record = best_deconstruction_examples("jeff_tool_header_panel", limit=1)[0]

    result = check_host_compatibility(
        reference_record=record,
        cutout_candidate=_candidate(
            gaze="screen_right",
            hands="chin",
            expression_family="soft_smile",
            face_height_ratio=0.18,
        ),
    )

    assert not result.ok
    assert any("face_too_small" in reason for reason in result.reasons)


def test_build_cast_request_for_reference_translates_host_needs_to_pose_manifest_axes():
    record = best_deconstruction_examples("jeff_command_panel", limit=1)[0]

    request = build_cast_request_for_reference(record)

    assert "mild_surprise" in request.expression_families
    assert "point_screen_left" in request.hands
    assert "screen_left" in request.gazes
    assert "jeff_clean_tutorial" in request.use_contexts
    assert request.max_intensity == "medium"
    assert request.min_credibility == "medium"
