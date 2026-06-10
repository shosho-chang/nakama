from __future__ import annotations

import pytest

from shared.thumbnail_arrangement import (
    ArrangementCandidate,
    CheapEvalSummary,
    LayerSpec,
    PixelBox,
    make_candidate_id,
)


def test_pixel_box_round_trip_and_iou():
    box = PixelBox.from_any({"x": -20, "y": 10, "w": 220, "h": 190})
    other = PixelBox.from_any([100, 80, 300, 260])

    assert box.to_tuple() == (-20, 10, 200, 200)
    assert box.to_xywh_dict() == {"x": -20, "y": 10, "w": 220, "h": 190}
    assert round(box.iou(other), 3) == 0.182


def test_candidate_serialization_preserves_text_face_and_hand_boxes():
    candidate = ArrangementCandidate(
        candidate_id="idea01-hyp01-cand01",
        template_id="shosho_benefit_list_card",
        hypothesis_id="h1",
        title="肌酸的 6 個健康效益",
        layers=(
            LayerSpec(
                layer_id="payload",
                kind="benefit_list_card",
                role="payload_card",
                box=PixelBox(720, 140, 1180, 520),
                z=20,
                slot_id="card_right_third",
                content=("護腦", "抗老", "認知"),
                protected_text_boxes=(PixelBox(780, 220, 1040, 420),),
                metadata={"asset_score": 0.82, "tags": ("health", "benefits")},
            ),
            LayerSpec(
                layer_id="host",
                kind="cutout",
                role="host",
                box=PixelBox(-90, -40, 650, 820),
                z=30,
                face_box=PixelBox(210, 52, 520, 430),
                hand_boxes=(PixelBox(525, 320, 710, 560),),
                asset_ref="cutouts/explaining.png",
            ),
        ),
        cheap_eval=CheapEvalSummary(
            status="pass",
            score=92.5,
            reasons=("passes overlap gates",),
            repairs=(),
        ),
        notes=("hand may overlap card but not protected text",),
    )

    restored = ArrangementCandidate.from_dict(candidate.to_dict())

    assert restored == candidate
    assert restored.protected_text_boxes() == (PixelBox(780, 220, 1040, 420),)
    assert restored.face_boxes() == (PixelBox(210, 52, 520, 430),)
    assert restored.hand_boxes() == (PixelBox(525, 320, 710, 560),)
    assert restored.layer_by_id("payload").content == ("護腦", "抗老", "認知")


def test_layers_in_z_order_is_deterministic():
    candidate = ArrangementCandidate(
        candidate_id="idea01-hyp01-cand02",
        template_id="jeff_command_panel",
        layers=(
            LayerSpec("host", "cutout", "host", PixelBox(620, -20, 1300, 760), 30),
            LayerSpec("background", "background_plate", "background", PixelBox(0, 0, 1280, 720), 0),
            LayerSpec("panel-b", "command_panel", "payload_card", PixelBox(80, 100, 720, 470), 10),
            LayerSpec("panel-a", "tool_logo", "supporting_asset", PixelBox(90, 90, 200, 200), 10),
        ),
    )

    assert [layer.layer_id for layer in candidate.layers] == [
        "background",
        "panel-a",
        "panel-b",
        "host",
    ]


def test_candidate_id_is_stable_and_1_based():
    assert make_candidate_id(2, 3, 4) == "idea02-hyp03-cand04"

    with pytest.raises(ValueError):
        make_candidate_id(0, 1, 1)


def test_from_dict_rejects_invalid_box():
    with pytest.raises(ValueError, match="non-positive size"):
        PixelBox.from_any({"x0": 10, "y0": 20, "x1": 10, "y1": 50})


def test_layer_lookup_and_duplicate_ids():
    candidate = ArrangementCandidate(
        candidate_id="idea01-hyp02-cand01",
        template_id="ali_metric_arrow",
        layers=(
            LayerSpec(
                "left-metric", "metric_badge", "payload_metric", PixelBox(70, 180, 360, 360), 10
            ),
            LayerSpec(
                "right-metric", "metric_badge", "payload_metric", PixelBox(910, 180, 1210, 360), 10
            ),
        ),
    )

    assert len(candidate.layers_by_role("payload_metric")) == 2
    assert candidate.layer_by_id("right-metric").box.x0 == 910
    with pytest.raises(KeyError):
        candidate.layer_by_id("missing")

    with pytest.raises(ValueError, match="duplicate layer ids"):
        ArrangementCandidate(
            candidate_id="idea01-hyp02-cand02",
            template_id="ali_metric_arrow",
            layers=(
                LayerSpec(
                    "metric", "metric_badge", "payload_metric", PixelBox(70, 180, 360, 360), 10
                ),
                LayerSpec(
                    "metric", "metric_badge", "payload_metric", PixelBox(910, 180, 1210, 360), 20
                ),
            ),
        )
