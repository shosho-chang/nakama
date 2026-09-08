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
            ("hero_title", "真正的選擇不是二選一", "hero_title:v2"),
        )
    )

    chapter, hero = (output.recipe for output in outputs)
    assert chapter.full_frame is True
    assert chapter.style_name == "paper_hand"
    assert chapter.has_alpha is False
    assert 'data-composition-id="transition_title_wide"' in chapter.html_document
    assert 'data-style="paper_hand"' in chapter.html_document
    assert "paper-grain" in chapter.html_document
    # 修修 2026-09-08：「transition 不用加「章節」這兩個字，一點意義都沒有」——
    # 卡片本身就是章節，再標一次是廢話。只留手繪短槓。
    assert "章節" not in chapter.html_document
    assert 'class="kbar"' in chapter.html_document
    assert 'class="uline"' in chapter.html_document
    # 14 個字用 128px 會撞破 1600px 的可用寬度而斷成孤字，所以降到 104px。
    # 字級是依字數分階的，不是定值——見
    # test_chapter_card_font_shrinks_so_long_titles_do_not_orphan_a_character。
    assert "font-size: 104px" in chapter.html_document
    assert "translateY(108%)" in chapter.html_document
    assert hero.full_frame is False
    # 手冊「Hero 大字卡」定版：punch_card_wide tier1 + style:"paper"，每行上限 96px。
    assert hero.style_name == "paper"
    assert hero.font_size_px == 96
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
            layout_identity="hero_title:v2",
        )
    )

    document = output.recipe.html_document
    assert "<script>alert" not in document
    assert "&lt;script&gt;alert" in document
    assert "<script src=" not in document
    assert f"font-size: {output.recipe.font_size_px}px" in document


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
            layout_identity="hero_title:v2",
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
                layout_identity="hero_title:v2",
            )
        )


def test_chapter_card_font_shrinks_so_long_titles_do_not_orphan_a_character() -> None:
    """13 字以上要降字級——不然第二行只剩一兩個孤字。

    `.stage` 扣掉左右 160px 只剩 1600px，CJK 字寬約 1em：13 字 ×128px = 1664px 就
    換行。2026-09-08 蘇予昕 punch-L04 的 visual_review 就是看到「拖延症不是懶，是
    想法太勤勞」斷成「…太勤／勞」而退件。

    這份 HTML 是 `video/compositions/.../transition_title_wide.html` 的第二份實作，
    兩邊的字級規則必須一致；只修一邊的話 pipeline 渲出來還是斷的。
    """
    import re

    from agents.brook.script_video.finished_cut_production._long_visual_renderer import (
        _paper_hand_chapter_document,
    )

    def font_px(title: str) -> int:
        document = _paper_hand_chapter_document(
            display=title, canvas_width=1920, canvas_height=1080, duration_sec=3.0
        )
        match = re.search(r"\.title \{[^}]*font-size: (\d+)px", document)
        assert match is not None
        return int(match.group(1))

    for title in (
        "不想做，就先不要做",
        "光是看懂，情緒就開始鬆綁",
        "拖延症不是懶，是想法太勤勞",
        "原生家庭不是牽拖，是第一個線索",
    ):
        assert len(title) * font_px(title) <= 1600, title


def test_hero_card_uses_the_house_punch_card_recipe_not_a_reinvented_pill() -> None:
    """Hero 大字卡要照手冊的定版配方，不是 ADR-066 自創的小藥丸。

    `.claude/skills/longform-cut/SKILL.md`「Hero 大字卡」：長片**唯一配方**是
    `punch_card_wide` tier1 + `style:"paper"`，每行字級上限 96px，紙卡放在說話者
    負空間避免壓迫臉部。ADR-066 原本自己造了 `compact_paper` 64px 放在畫面正中，
    比定版小、還正好壓臉。
    """
    from agents.brook.script_video.finished_cut_production._long_visual_renderer import (
        _paper_hero_document,
    )

    document = _paper_hero_document(
        display="喔我爸就是這樣", canvas_width=1920, canvas_height=1080, duration_sec=3.0
    )
    assert "font-size: 96px" in document
    assert 'data-composition-id="punch_card_wide"' in document
    assert 'data-style="paper"' in document
    assert "top: 66%" in document
    assert "rgba(251, 250, 247, 0.86)" in document  # 定版紙白
    assert "#e98965" in document  # 定版手繪橘線
    assert chr(92) + "n" not in document  # 曾經把字面反斜線 n 渲到畫面上


def test_hero_lines_never_split_a_word_in_half() -> None:
    """只在標點斷行；沒有標點就維持單行。

    按字數對半切會把詞組切開——「喔我爸就／是這樣」「一天六七／千個念頭」。斷行是
    導演的決定，定版 composition 因此收 line1/line2/line3 三個獨立欄位。
    """
    from agents.brook.script_video.finished_cut_production._long_visual_renderer import (
        _hero_lines,
    )

    assert _hero_lines("喔我爸就是這樣") == ("喔我爸就是這樣",)
    assert _hero_lines("一天六七千個念頭") == ("一天六七千個念頭",)
    assert _hero_lines("拖延症不是懶，是想法太勤勞") == ("拖延症不是懶", "是想法太勤勞")


def test_title_cards_must_stay_on_screen_long_enough_to_read() -> None:
    """字卡秒數要讀得完；滿版轉場卡由 canonical section 固定鑄造，不在此列。"""
    from agents.brook.script_video.finished_cut_production._derived_assets import (
        readable_floor_sec,
    )

    assert readable_floor_sec("hero_title", "花了快一百萬") > 2.5
    assert readable_floor_sec("hero_title", "一天六七千個念頭") > readable_floor_sec(
        "hero_title", "花了快一百萬"
    )
    assert readable_floor_sec("identity_card", "蘇予昕") == 2.5
    assert readable_floor_sec("fullscreen_transition", "原生家庭不是牽拖，是第一個線索") is None


def test_short_title_placement_is_extended_not_rejected() -> None:
    """字卡太短要自動延長，不是擋下來重跑。

    DP 未必有更多 cue 可挑，擋下來會製造無解狀態——跟素材庫不夠時逼 DP 重試是同一
    種錯。cue 證據不動，只讓卡片多停留一會兒。
    """
    from agents.brook.script_video.finished_cut_production._context import (
        CanonicalSection,
        CueAnchor,
        CutSourceRange,
        EditorialCutContext,
    )
    from agents.brook.script_video.finished_cut_production._derived_assets import (
        readable_floor_sec,
    )

    context = EditorialCutContext(
        episode_id="episode-1",
        cut_id="long-1",
        format="long",
        editorial_master_id="master-1",
        tight_cut_id="tight-1",
        duration_sec=600.0,
        source_ranges=(CutSourceRange(0.0, 600.0),),
        cues=(
            CueAnchor("cue-1", "花了快一百萬", 10.0, 11.07, "section-01"),
            CueAnchor("cue-2", "下一句", 11.07, 14.0, "section-01"),
        ),
        sections=(CanonicalSection("section-01", "第一章", 0.0),),
    )
    floor = readable_floor_sec("hero_title", "花了快一百萬")

    placement = context.derive_visual_placement(
        semantic_cue_ids=("cue-1", "cue-2"),
        placement_cue_ids=("cue-1",),
        semantic_kind="hero_title",
        min_show_sec=floor,
    )

    assert placement.placement_cue_ids == ("cue-1",)  # 證據不變
    assert placement.t1 - placement.t0 == pytest.approx(floor)


def test_namecard_uses_the_house_chapter_label_recipe() -> None:
    """來賓名牌要照手冊定版，不是 ADR-066 自創的置中藥丸。

    手冊「來賓名牌」：`chapter_label_wide` `align:"left"` + `sub` + `style:"paper"`
    ＝半透明紙卡＋手繪橘豎筆觸＋逐元素進退場，落左下。ADR-066 原本自己造了
    `identity_plaque` 36px 置中，跟手冊寫的不是同一個東西。
    """
    from agents.brook.script_video.finished_cut_production._long_visual_renderer import (
        _paper_namecard_document,
    )

    document = _paper_namecard_document(
        display="蘇予昕／諮商心理師", canvas_width=1920, canvas_height=1080, duration_sec=5.0
    )
    assert 'data-composition-id="chapter_label_wide"' in document
    assert "left: 4%" in document and "top: 76%" in document  # 左下，不是置中
    assert "font-size: 50px" in document  # 姓名
    assert "font-size: 29px" in document  # 頭銜
    assert "#e98965" in document  # 手繪橘豎筆觸
    assert ">蘇予昕<" in document
    assert ">諮商心理師<" in document


def test_namecard_without_a_separator_renders_name_only() -> None:
    from agents.brook.script_video.finished_cut_production._long_visual_renderer import (
        _paper_namecard_document,
    )

    document = _paper_namecard_document(
        display="蘇予昕", canvas_width=1920, canvas_height=1080, duration_sec=5.0
    )
    assert ">蘇予昕<" in document
    assert 'id="sub"' not in document
