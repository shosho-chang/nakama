from __future__ import annotations

from pathlib import Path

from PIL import Image

from shared.thumbnail_component_plan import (
    plan_component_variants,
    render_component_plan_preview,
)
from shared.thumbnail_idea import ParsedIdea


def _idea(reference_template_id: str = "shosho_benefit_list_card") -> ParsedIdea:
    return ParsedIdea(
        hook="護腦三招",
        emotion_key="explaining",
        emotion_input="解釋",
        visual="便條紙上有護腦、抗老、認知三行",
        decoration="3",
        bg="模糊 A-roll 工作室背景",
        archetype_tags=("T-A8", "T-V3"),
        lane="Ali Warm Explainer",
        recipe_id="ali_warm_evidence_list",
        reference_template_id=reference_template_id,
        title_pairing="肌酸不只練肌肉：3 個你沒聽過的妙用",
        asset_queries=("sticky note benefit list", "creatine jar cutout"),
        viewer_promise="快速知道肌酸的三個非運動好處",
        evidence_fit="對應研究整理",
        trust_risk="避免醫療奇蹟化",
    )


def test_component_plan_uses_reference_template_benefit_card():
    variants = plan_component_variants(
        parsed_idea=_idea(),
        person_placement={
            "cutout_box": [0, 0, 760, 720],
            "face_box": [210, 54, 520, 430],
        },
        layout_blocking={"content_side": "right"},
    )

    selected = variants[0]
    assert selected.reference_template_id == "shosho_benefit_list_card"
    assert selected.score >= 90
    assert selected.components[0].component_type == "benefit_list_card"
    assert "護腦" in "".join(selected.components[0].content)


def test_component_plan_missing_template_does_not_treat_ali_as_ai_tool():
    variants = plan_component_variants(
        parsed_idea=_idea(reference_template_id=""),
        person_placement={
            "cutout_box": [0, 0, 760, 720],
            "face_box": [210, 54, 520, 430],
        },
        layout_blocking={"content_side": "right"},
    )

    assert variants[0].reference_template_id == "shosho_benefit_list_card"
    assert variants[0].components[0].component_type == "benefit_list_card"


def test_component_plan_flags_face_overlap():
    variants = plan_component_variants(
        parsed_idea=_idea(),
        person_placement={
            "cutout_box": [0, 0, 760, 720],
            "face_box": [720, 40, 1230, 660],
        },
        layout_blocking={"content_side": "right"},
    )

    selected = variants[0]
    assert selected.score < 100
    assert any("overlaps the face" in item for item in selected.critique)
    assert any(patch.startswith("move_away_from_face") for patch in selected.repair_patches)


def test_component_plan_preview_writes_16x9_png(tmp_path: Path):
    preview = render_component_plan_preview(
        out_png=tmp_path / "components.png",
        parsed_idea=_idea("jeff_tool_header_panel"),
        person_placement={
            "cutout_box": [0, 0, 760, 720],
            "face_box": [210, 54, 520, 430],
        },
        layout_blocking={"content_side": "right"},
    )

    assert preview.out_png.is_file()
    assert preview.selected_variant.reference_template_id == "jeff_tool_header_panel"
    with Image.open(preview.out_png) as img:
        assert img.size == (1280, 720)
        assert img.getpixel((40, 680)) != (128, 128, 128)
        assert img.getpixel((800, 250))[0] > 180


def test_component_plan_has_command_panel_branch():
    variants = plan_component_variants(
        parsed_idea=_idea("jeff_command_panel"),
        person_placement={
            "cutout_box": [620, 0, 1280, 720],
            "face_box": [800, 54, 1110, 430],
        },
        layout_blocking={"content_side": "left"},
    )

    selected = variants[0]
    assert selected.reference_template_id == "jeff_command_panel"
    assert selected.components[0].component_type == "command_panel"
    assert selected.score >= 80
