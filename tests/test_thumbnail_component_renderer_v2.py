from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from shared.thumbnail_component_renderer_v2 import (
    draw_thumbnail_components,
    render_component_style_contact_sheet,
)


def test_component_style_contact_sheet_exercises_core_components(tmp_path: Path):
    out_png = render_component_style_contact_sheet(out_png=tmp_path / "style-lab.png")

    assert out_png.is_file()
    with Image.open(out_png) as img:
        assert img.size == (1280, 720)
        assert len(img.convert("RGB").getcolors(maxcolors=512_000)) > 220
        assert _mean_rgb(img.crop((52, 58, 472, 344)))[0] > 180
        assert _mean_rgb(img.crop((830, 426, 1216, 624)))[0] < 90


def test_renderer_draws_distinct_jeff_tool_icon_and_metric_styles():
    canvas = Image.new("RGBA", (1280, 720), (38, 43, 52, 255))
    before = canvas.copy()

    draw_thumbnail_components(
        canvas,
        {
            "selected_variant": {
                "reference_template_id": "jeff_tool_header_panel",
                "components": [
                    {
                        "component_type": "tool_label",
                        "box": {"x": 60, "y": 46, "w": 420, "h": 78},
                        "content": ["Perplexity"],
                    },
                    {
                        "component_type": "ui_panel",
                        "box": {"x": 60, "y": 150, "w": 500, "h": 260},
                        "content": ["Start Here", "14 sources"],
                    },
                    {
                        "component_type": "logo_tile",
                        "box": {"x": 610, "y": 162, "w": 170, "h": 170},
                        "content": ["Perplexity"],
                    },
                    {
                        "component_type": "metric_badge_pair",
                        "box": {"x": 820, "y": 118, "w": 190, "h": 120},
                        "content": ["$0"],
                    },
                    {
                        "component_type": "arrow",
                        "box": {"x": 910, "y": 234, "w": 270, "h": 112},
                        "content": [],
                    },
                ],
            }
        },
    )

    diff = ImageChops.difference(before.convert("RGB"), canvas.convert("RGB"))
    assert ImageStat.Stat(diff).sum[0] > 250_000
    assert _mean_rgb(canvas.crop((60, 46, 480, 124)))[0] < 80
    assert _mean_rgb(canvas.crop((60, 150, 560, 410)))[0] > 140
    arrow_crop = canvas.crop((910, 234, 1180, 346)).convert("RGB")
    assert arrow_crop.getextrema()[1][1] > 180


def test_renderer_draws_dark_command_panel_and_quote_card():
    canvas = Image.new("RGBA", (1280, 720), (210, 216, 224, 255))

    draw_thumbnail_components(
        canvas,
        {
            "selected_variant": {
                "reference_template_id": "jeff_command_panel",
                "components": [
                    {
                        "component_type": "command_panel",
                        "box": {"x": 80, "y": 80, "w": 560, "h": 300},
                        "content": ["<trigger>", "DO THIS INSTEAD"],
                    },
                    {
                        "component_type": "quote_card",
                        "box": {"x": 720, "y": 96, "w": 460, "h": 260},
                        "content": ["Stop wasting time."],
                    },
                    {
                        "component_type": "tool_logo",
                        "box": {"x": 88, "y": 96, "w": 120, "h": 120},
                        "content": ["ChatGPT"],
                    },
                ],
            }
        },
    )

    command_mean = _mean_rgb(canvas.crop((80, 80, 640, 380)))
    quote_mean = _mean_rgb(canvas.crop((720, 96, 1180, 356)))
    assert command_mean[0] < 80
    assert quote_mean[0] > 210
    assert abs(command_mean[0] - quote_mean[0]) > 130


def _mean_rgb(img: Image.Image) -> tuple[float, float, float]:
    stat = ImageStat.Stat(img.convert("RGB"))
    return tuple(stat.mean[:3])
