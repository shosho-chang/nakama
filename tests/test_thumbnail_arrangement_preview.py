from __future__ import annotations

from pathlib import Path

from PIL import Image

from shared.thumbnail_arrangement_eval import rank_arrangement_candidates
from shared.thumbnail_arrangement_generator import generate_arrangement_candidates
from shared.thumbnail_arrangement_preview import render_arrangement_contact_sheet
from shared.thumbnail_idea import ParsedIdea


def test_render_arrangement_contact_sheet_writes_16x9_png(tmp_path: Path):
    idea = ParsedIdea(
        hook="肌酸不只增肌",
        emotion_key="explaining",
        emotion_input="解釋",
        visual="便條紙上有護腦、抗老、認知三行",
        decoration="6",
        bg="模糊 A-roll 工作室背景",
        reference_template_id="shosho_benefit_list_card",
        title_pairing="肌酸的 6 個健康效益",
        component_type="benefit_list_card",
        component_text=("護腦", "抗老", "認知"),
    )
    candidates = rank_arrangement_candidates(
        generate_arrangement_candidates(
            parsed_idea=idea,
            person_placement={
                "cutout_box": [-90, -40, 650, 820],
                "face_box": [210, 52, 520, 430],
                "right_hand_box": [720, 430, 910, 560],
            },
            layout_blocking={"content_side": "right"},
        )
    )

    out = render_arrangement_contact_sheet(
        out_png=tmp_path / "arrangement.png",
        candidates=candidates,
        selected_candidate_id=candidates[0].candidate_id,
    )

    assert out.is_file()
    with Image.open(out) as img:
        assert img.size == (1280, 720)
        assert img.getpixel((50, 50)) != (242, 244, 247)
        assert img.getpixel((170, 170)) != (242, 244, 247)
