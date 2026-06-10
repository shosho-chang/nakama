from __future__ import annotations

from pathlib import Path

from PIL import Image

from shared.thumbnail_final_render import (
    _benefit_content,
    _should_draw_global_typography,
    render_staged_youtube_thumbnail,
)
from shared.thumbnail_idea import ParsedIdea


def _idea() -> ParsedIdea:
    return ParsedIdea(
        hook="BRAIN BENEFITS",
        emotion_key="explaining",
        emotion_input="explaining",
        visual="warm desk background with a benefit list card",
        decoration="",
        bg="warm desk bookshelf",
        archetype_tags=("T-A8", "T-V3"),
        lane="Ali Warm Explainer",
        recipe_id="ali_warm_evidence_list",
        reference_template_id="shosho_benefit_list_card",
        title_pairing="Creatine brain benefits",
        asset_queries=("sticky note benefit list",),
        component_type="benefit_list_card",
        component_text=("Brain", "Aging", "Focus"),
        host_directive="host left, looking to the right",
        viewer_promise="quickly understand three non-gym benefits",
        evidence_fit="research backed",
        trust_risk="avoid medical miracle claims",
    )


def test_final_render_consumes_staged_component_plan(tmp_path: Path):
    out_png = tmp_path / "v0.png"
    render_staged_youtube_thumbnail(
        out_png=out_png,
        parsed_idea=_idea(),
        person_placement={
            "cutout_path": str(tmp_path / "missing-cutout.png"),
            "cutout_box": [0, 0, 760, 720],
            "face_box": [210, 54, 520, 430],
        },
        layout_blocking={
            "content_side": "right",
            "layout_spec": {
                "asset_region": {"x": 738, "y": 166, "w": 480, "h": 320},
                "headline_region": {"x": 34, "y": 474, "w": 1212, "h": 220},
                "content_side": "right",
            },
        },
        component_plan={
            "selected_variant": {
                "reference_template_id": "shosho_benefit_list_card",
                "components": [
                    {
                        "component_type": "benefit_list_card",
                        "role": "primary_payload",
                        "box": {"x": 738, "y": 166, "w": 480, "h": 320},
                        "content": ["Brain", "Aging", "Focus"],
                        "style": "warm_paper_card",
                    }
                ],
            }
        },
    )

    with Image.open(out_png) as img:
        assert img.size == (1280, 720)
        card_pixel = img.getpixel((1000, 250))
        assert card_pixel[0] > 240
        assert card_pixel[1] > 220
        assert card_pixel[2] > 150

        old_badge_center = img.getpixel((640, 370))
        assert not (
            old_badge_center[0] > 220 and old_badge_center[1] > 160 and old_badge_center[2] < 120
        )


def test_benefit_content_backfills_from_visual_text_slot():
    idea = _idea()
    idea = idea.__class__(
        **{
            **idea.__dict__,
            "component_text": ("Brain", "Aging", "Brain"),
            "visual": "template=x; component=benefit_list_card; text=Brain/Aging/Focus; host=left",
        }
    )

    content = _benefit_content({"content": ["Brain", "Aging"]}, idea)

    assert content == ["Brain", "Aging", "Focus"]


def test_final_render_suppresses_global_headline_for_metric_template(tmp_path: Path):
    idea = ParsedIdea(
        hook="補充前後",
        emotion_key="surprised",
        emotion_input="surprised",
        visual="template=ali_metric_arrow; component=metric_badge_pair; text=$0/$1M; host=center",
        decoration="",
        bg="warm desk bookshelf",
        reference_template_id="ali_metric_arrow",
        title_pairing="Creatine before after",
        component_type="metric_badge_pair",
        component_text=("$0", "$1M"),
    )
    component_plan = {
        "selected_variant": {
            "reference_template_id": "ali_metric_arrow",
            "components": [
                {
                    "component_type": "metric_badge_pair",
                    "box": {"x": 80, "y": 170, "w": 254, "h": 150},
                    "content": ["$0"],
                },
                {
                    "component_type": "metric_badge_pair",
                    "box": {"x": 946, "y": 170, "w": 254, "h": 150},
                    "content": ["$1M"],
                },
                {
                    "component_type": "arrow",
                    "box": {"x": 398, "y": 92, "w": 486, "h": 168},
                    "content": [],
                },
            ],
        }
    }

    assert not _should_draw_global_typography(component_plan, idea)

    out_png = tmp_path / "metric.png"
    render_staged_youtube_thumbnail(
        out_png=out_png,
        parsed_idea=idea,
        person_placement={
            "cutout_path": str(tmp_path / "missing-cutout.png"),
            "cutout_box": [300, -30, 980, 800],
            "face_box": [460, 54, 780, 430],
        },
        layout_blocking={"content_side": "right"},
        component_plan=component_plan,
    )

    with Image.open(out_png) as img:
        assert max(img.getpixel((640, y))[0] for y in range(560, 680, 10)) < 180
