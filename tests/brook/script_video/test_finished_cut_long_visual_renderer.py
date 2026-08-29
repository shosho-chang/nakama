from __future__ import annotations

import ast
from pathlib import Path

import pytest

from agents.brook.script_video.finished_cut_production import _active_store as active_store_module
from agents.brook.script_video.finished_cut_production import (
    _hyperframes_renderer as hyperframes_renderer_module,
)
from agents.brook.script_video.finished_cut_production import (
    _long_visual_renderer as renderer_module,
)
from agents.brook.script_video.finished_cut_production import (
    _visual_assets as visual_assets_module,
)
from agents.brook.script_video.finished_cut_production._long_visual_renderer import (
    BrowserRenderResult,
    LongVisualRenderer,
    LongVisualRenderError,
    LongVisualRenderRequest,
)


class _Browser:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.recipes = []
        self.duration_delta = 0.0

    def render(self, recipe):
        self.recipes.append(recipe)
        path = self.root / f"render-{len(self.recipes)}{recipe.extension}"
        path.write_bytes(recipe.recipe_identity.encode("utf-8"))
        return BrowserRenderResult(
            path=path,
            width=recipe.canvas_width,
            height=recipe.canvas_height,
            duration_sec=recipe.duration_sec + self.duration_delta,
            has_alpha=recipe.has_alpha,
            codec_name=recipe.codec_name,
            pixel_format=recipe.pixel_format,
        )


class _BrokenBrowser:
    def render(self, recipe):
        raise RuntimeError("browser process failed")


def test_chapter_restores_approved_paper_hand_recipe(tmp_path: Path) -> None:
    browser = _Browser(tmp_path)
    renderer = LongVisualRenderer(browser=browser)

    outputs = tuple(
        renderer.render(
            LongVisualRenderRequest(
                recipe_identity=f"recipe:{role}:current",
                event_id=f"event-{role}",
                role=role,
                display=display,
                duration_sec=3.0,
                target_width=1920,
                target_height=1080,
                layout_identity=layout_identity,
            )
        )
        for role, display, layout_identity in (
            (
                "chapter",
                "第二章｜工作與家庭的雙重壓力",
                "fullscreen_transition:v4",
            ),
            ("hero_title", "真正的選擇不是二選一", "hero_title:v1"),
        )
    )

    chapter, hero = (output.recipe for output in outputs)
    assert chapter.full_frame is True
    assert chapter.style_name == "paper_hand"
    assert chapter.has_alpha is False
    assert 'data-composition-id="transition_title_wide"' in chapter.html_document
    assert 'data-style="paper_hand"' in chapter.html_document
    assert "paper-grain" in chapter.html_document
    assert '<div class="kicker">章節</div>' in chapter.html_document
    assert 'class="kbar"' in chapter.html_document
    assert 'class="uline"' in chapter.html_document
    assert "font-size: 128px" in chapter.html_document
    assert "translateY(108%)" in chapter.html_document
    assert hero.full_frame is False
    assert hero.style_name == "compact_paper"
    assert hero.font_size_px <= 64
    assert hero.content_width_ratio <= 0.60
    assert hero.safe_region == "lower"
    assert chapter.style_name != hero.style_name


def test_long_visual_recipe_is_self_contained_and_escapes_display_text(tmp_path: Path) -> None:
    browser = _Browser(tmp_path)
    renderer = LongVisualRenderer(browser=browser)

    output = renderer.render(
        LongVisualRenderRequest(
            recipe_identity="recipe:hero:escaped",
            event_id="event-hero",
            role="hero_title",
            display='<script>alert("unsafe")</script>',
            duration_sec=2.5,
            target_width=1920,
            target_height=1080,
            layout_identity="hero_title:v1",
        )
    )

    document = output.recipe.html_document
    assert "<script>alert" not in document
    assert "&lt;script&gt;alert" in document
    assert "<script src=" not in document
    assert f"font-size: {output.recipe.font_size_px}px" in document
    assert f"max-width: {output.recipe.content_width_ratio * 100:.0f}%" in document


def test_production_visual_modules_have_no_retired_visual_execution_imports() -> None:
    forbidden_import_fragments = (
        "run_short_",
        "highlight_visual_pipeline",
        "podcast_highlight_visual",
    )
    for module in (
        active_store_module,
        hyperframes_renderer_module,
        renderer_module,
        visual_assets_module,
    ):
        source = Path(module.__file__).read_text(encoding="utf-8")
        imported_names = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported_names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported_names.append(node.module or "")
        assert not any(
            fragment in imported
            for fragment in forbidden_import_fragments
            for imported in imported_names
        )
    renderer_source = Path(renderer_module.__file__).read_text(encoding="utf-8").lower()
    assert '"orange"' not in renderer_source
    assert '"ink"' not in renderer_source
    assert '"short"' not in renderer_source


def test_browser_duration_allows_one_frame_of_container_rounding(tmp_path: Path) -> None:
    browser = _Browser(tmp_path)
    browser.duration_delta = -(1 / 30)
    renderer = LongVisualRenderer(browser=browser)

    output = renderer.render(
        LongVisualRenderRequest(
            recipe_identity="recipe:hero:rounding",
            event_id="event-hero",
            role="hero_title",
            display="保留調整空間",
            duration_sec=2.0,
            target_width=1920,
            target_height=1080,
            layout_identity="hero_title:v1",
        )
    )

    assert output.media.duration_sec == 2.0 - (1 / 30)


def test_browser_process_failure_is_normalized_at_the_adapter_seam() -> None:
    renderer = LongVisualRenderer(browser=_BrokenBrowser())

    with pytest.raises(LongVisualRenderError, match="browser rendering failed"):
        renderer.render(
            LongVisualRenderRequest(
                recipe_identity="recipe:hero:broken-browser",
                event_id="event-hero",
                role="hero_title",
                display="真正的選擇",
                duration_sec=2.0,
                target_width=1920,
                target_height=1080,
                layout_identity="hero_title:v1",
            )
        )
