from __future__ import annotations

import ast
import hashlib
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
from agents.brook.script_video.finished_cut_production._projection import (
    layout_identity,
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
                layout_identity("fullscreen_transition"),
            ),
            ("hero_title", "真正的選擇不是二選一", layout_identity("hero_title")),
        )
    )

    chapter, hero = (output.recipe for output in outputs)
    assert chapter.full_frame is True
    assert chapter.style_name == "paper_hand"
    assert chapter.has_alpha is False
    assert 'data-composition-id="transition_title_wide"' in chapter.html_document
    assert 'data-style="paper_hand"' in chapter.html_document
    assert "paper-grain" in chapter.html_document
    # 修修 2026-09-08 人眼驗過後把「章節」拿掉了——卡片上只留章節標題本身。
    # 這一條反過來鎖住那個決定，避免下次改配方又把它加回來。
    assert "章節" not in chapter.html_document
    # kicker 還在，但它是手繪短槓（svg），不是「章節」兩個字。
    assert '<div class="kicker-row">' in chapter.html_document
    assert '<svg class="kbar"' in chapter.html_document
    assert 'class="kbar"' in chapter.html_document
    assert 'class="uline"' in chapter.html_document
    # 章節標題字級是定值 104px，不是配方裡那個字級，也不再照字數降級。
    # 這一條守的是長標題那一端：14 字在 104px 下實測 1471px，還在 max-width
    # 1600px 內，一行載得下（舊的 128px 會撞破，斷成孤字掉第二行）。
    # 短標題那一端由 test_chapter_title_font_size_does_not_track_length 守。
    assert "font-size: 104px" in chapter.html_document
    assert "translateY(108%)" in chapter.html_document
    assert hero.full_frame is False
    # Hero 是定版 punch_card_wide tier1 + style:paper。ADR-066 原本自創的
    # compact_paper（64px 單行藥丸置中、壓在臉上）2026-09-08 被修修退掉，
    # 手冊寫的是「每行字級上限 96px、紙卡放在說話者負空間」。
    assert hero.style_name == "paper"
    assert hero.font_size_px == 96
    assert hero.content_width_ratio == 0.72
    assert hero.safe_region == "lower"
    assert 'data-composition-id="punch_card_wide"' in hero.html_document
    assert chapter.style_name != hero.style_name


def test_chapter_title_font_size_does_not_track_length(tmp_path: Path) -> None:
    """同一支影片裡的章節卡必須一樣大，短標題不准放大回去。

    舊規則是三階梯（>12 字 104px、>9 字 128px、其餘 168px），本意是擋孤字，
    但也讓蘇予昕長2（punch-L03）的五張章節卡落在 168 / 128 / 128 / 104 / 104
    三個字級。修修 2026-09-16 看成品：「有 transition 的字型還是太大，不是已經
    統一了嗎？」——那次統一的是兩份程式碼，不是視覺大小。
    """
    browser = _Browser(tmp_path)
    renderer = LongVisualRenderer(browser=browser)

    # 舊階梯的兩個分界（9/10 字、12/13 字），外加一個明顯更短的標題。
    displays = (
        "先不要做",  # 4 字
        "孩子才是父母的老師",  # 9 字 — 舊規則的 168px
        "每天回抓你的限制信念",  # 10 字 — 舊規則的 128px
        "今年撐過就好，然後永遠在撐",  # 13 字 — 舊規則的 104px
    )
    documents = tuple(
        renderer.render(
            LongVisualRenderRequest(
                recipe_identity=f"recipe:chapter:{index}",
                event_id=f"event-chapter-{index}",
                role="chapter",
                display=display,
                duration_sec=3.0,
                target_width=1920,
                target_height=1080,
                layout_identity=layout_identity("fullscreen_transition"),
            )
        ).recipe.html_document
        for index, display in enumerate(displays)
    )

    for display, document in zip(displays, documents, strict=True):
        assert "font-size: 104px" in document, display
        # 手繪底線跟字等長（transition_title_wide.html 一直是這樣算的）。這一份
        # 先前寫死 min(92%, 1460px)：168px 的卡底線比字短、104px 的卡底線比字長
        # 兩百多 px，兩份畫出來不是同一張卡。
        assert f".uline {{ width: {round(len(display) * 104 * 1.01)}px;" in document, display
    assert "font-size: 168px" not in "".join(documents)
    assert "font-size: 128px" not in "".join(documents)
    assert "min(92%, 1460px)" not in "".join(documents)


def test_recipe_document_digest_describes_the_document_that_gets_rendered(tmp_path: Path) -> None:
    """摘要必須是**真的會被畫出來的那份** HTML，否則 identity 又在說謊。

    `_engine._derived_asset_request` 把這個摘要放進 recipe identity，好讓「改了卡片
    設計」能真的走到螢幕上。要是摘要跟渲染器實際用的文件對不上（參數接錯、時長
    取整方式不同），identity 就會在該變的時候不變、或在不該變的時候亂變。
    """
    browser = _Browser(tmp_path)
    renderer = LongVisualRenderer(browser=browser)

    for implementation_kind, display in (
        ("fullscreen_transition", "你誤植了快樂的因果"),
        ("identity_card", "蘇予昕｜諮商心理師"),
    ):
        role = renderer_module.BROWSER_ROLE_BY_IMPLEMENTATION[implementation_kind]
        output = renderer.render(
            LongVisualRenderRequest(
                recipe_identity=f"recipe:{role}:digest",
                event_id=f"event-{role}",
                role=role,
                display=display,
                duration_sec=3.0,
                target_width=1920,
                target_height=1080,
                layout_identity=layout_identity(implementation_kind),
            )
        )
        expected = hashlib.sha256(output.recipe.html_document.encode("utf-8")).hexdigest()
        assert (
            renderer_module.recipe_document_digest(
                implementation_kind=implementation_kind,
                display=display,
                target_width=1920,
                target_height=1080,
                duration_sec=3.0,
            )
            == expected
        ), implementation_kind

    # 吃現成素材的實作不經渲染器，沒有文件可摘要。
    assert (
        renderer_module.recipe_document_digest(
            implementation_kind="stock_video",
            display="又要上班了",
            target_width=1920,
            target_height=1080,
            duration_sec=3.0,
        )
        is None
    )


def test_recipe_document_digest_moves_when_the_card_design_moves(monkeypatch) -> None:
    """改了卡片設計，摘要就要變——這是「改了設計成品卻沒動」的機械擋。

    2026-09-16 實測：章節卡字級從三階梯改成定值 104px 之後，蘇予昕長2 五張卡的
    recipe identity 一個字都沒變，`find_exact_recipe` 全數命中 9/10 那批 168px／
    128px 的舊 MOV——整個改動靜默地沒有到達螢幕，而且沒有任何 diagnostic。
    """
    kwargs = {
        "implementation_kind": "fullscreen_transition",
        "display": "你誤植了快樂的因果",
        "target_width": 1920,
        "target_height": 1080,
        "duration_sec": 3.0,
    }
    before = renderer_module.recipe_document_digest(**kwargs)
    monkeypatch.setattr(renderer_module, "_CHAPTER_TITLE_FONT_PX", 168)
    after = renderer_module.recipe_document_digest(**kwargs)

    assert before is not None
    assert before != after


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
            layout_identity=layout_identity("hero_title"),
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
            layout_identity=layout_identity("hero_title"),
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
                layout_identity=layout_identity("hero_title"),
            )
        )
