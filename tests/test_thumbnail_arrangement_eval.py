from __future__ import annotations

from dataclasses import replace

from shared.thumbnail_arrangement import LayerSpec, PixelBox
from shared.thumbnail_arrangement_eval import (
    attach_cheap_eval,
    rank_arrangement_candidates,
    score_arrangement_candidate,
)
from shared.thumbnail_arrangement_generator import generate_arrangement_candidates
from shared.thumbnail_idea import ParsedIdea


def _idea() -> ParsedIdea:
    return ParsedIdea(
        hook="肌酸不只增肌",
        emotion_key="explaining",
        emotion_input="解釋",
        visual="便條紙上有護腦、抗老、認知三行",
        decoration="6",
        bg="模糊 A-roll 工作室背景",
        recipe_id="ali_warm_evidence_list",
        reference_template_id="shosho_benefit_list_card",
        title_pairing="肌酸的 6 個健康效益",
        component_type="benefit_list_card",
        component_text=("護腦", "抗老", "認知"),
    )


def _candidate(*, hand_box: PixelBox | None = None, face_box: PixelBox | None = None):
    candidates = generate_arrangement_candidates(
        parsed_idea=_idea(),
        person_placement={
            "cutout_box": [-90, -40, 650, 820],
            "face_box": (face_box or PixelBox(210, 52, 520, 430)).to_tuple(),
            **({"right_hand_box": hand_box.to_tuple()} if hand_box else {}),
        },
        layout_blocking={"content_side": "right"},
        limit=1,
    )
    return candidates[0]


def test_cheap_eval_passes_good_benefit_layout():
    candidate = _candidate(hand_box=PixelBox(720, 430, 910, 560))

    result = score_arrangement_candidate(candidate)

    assert result.status == "pass"
    assert result.score >= 90
    assert result.reasons == ("passes deterministic arrangement gates",)


def test_cheap_eval_fails_when_hand_covers_protected_text():
    candidate = _candidate(hand_box=PixelBox(850, 240, 1040, 410))

    result = score_arrangement_candidate(candidate)

    assert result.status == "fail"
    assert any("protected text" in reason for reason in result.reasons)
    assert any(repair.startswith("move_hand_or_text") for repair in result.repairs)


def test_cheap_eval_fails_when_face_overlaps_payload_card():
    candidate = _candidate(face_box=PixelBox(800, 160, 1110, 470))

    result = score_arrangement_candidate(candidate)

    assert result.status == "fail"
    assert any("host_face overlaps" in reason for reason in result.reasons)
    assert any(repair.startswith("resolve_overlap:host_face") for repair in result.repairs)


def test_cheap_eval_fails_when_face_is_too_small():
    candidate = _candidate(face_box=PixelBox(260, 120, 430, 320))

    result = score_arrangement_candidate(candidate)

    assert result.status == "fail"
    assert any("face height ratio" in reason for reason in result.reasons)
    assert "increase_or_replace_host_cutout" in result.repairs


def test_attach_and_rank_candidates():
    good = _candidate(hand_box=PixelBox(720, 430, 910, 560))
    bad = _candidate(hand_box=PixelBox(850, 240, 1040, 410))
    bad = replace(bad, candidate_id="idea01-hyp01-cand02")

    ranked = rank_arrangement_candidates((bad, good))

    assert ranked[0].candidate_id == good.candidate_id
    assert ranked[0].cheap_eval.status == "pass"
    assert ranked[1].cheap_eval.status == "fail"

    attached = attach_cheap_eval(good)
    assert attached.candidate_id == good.candidate_id
    assert attached.cheap_eval.status == "pass"


def test_cheap_eval_counts_too_many_primary_payloads():
    candidate = _candidate()
    extra_layer = LayerSpec(
        layer_id="extra-card",
        kind="benefit_list_card",
        role="payload_card",
        box=PixelBox(60, 130, 430, 440),
        z=10,
        protected_text_boxes=(PixelBox(110, 190, 380, 380),),
    )
    overloaded = replace(candidate, layers=(*candidate.layers, extra_layer))

    result = score_arrangement_candidate(overloaded)

    assert result.status == "fail"
    assert any("too many primary payloads" in reason for reason in result.reasons)


def test_cheap_eval_fails_fake_metric_arrow_labels():
    idea = ParsedIdea(
        hook="補充前後",
        emotion_key="surprised",
        emotion_input="驚訝",
        visual=(
            "template=ali_metric_arrow; component=metric_badge_pair; "
            "text=Before/6效益; host=center"
        ),
        decoration="",
        bg="模糊 A-roll 工作室背景",
        reference_template_id="ali_metric_arrow",
        title_pairing="肌酸的 6 個健康效益：不只是增肌，更是護腦與抗老",
        component_type="metric_badge_pair",
        component_text=("Before", "6效益"),
    )
    candidate = generate_arrangement_candidates(
        parsed_idea=idea,
        person_placement={"cutout_box": [300, -30, 980, 800], "face_box": [460, 54, 780, 430]},
        layout_blocking={"content_side": "right"},
        limit=1,
    )[0]

    result = score_arrangement_candidate(candidate)

    assert result.status == "fail"
    assert any("not concrete numeric contrast" in reason for reason in result.reasons)
    assert "replace_template_or_metric_labels" in result.repairs
