"""Long-format visual recipes rendered through an injected browser seam."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Literal, Protocol

from ._projection import BROWSER_ROLE_BY_IMPLEMENTATION, layout_identity

LongVisualRole = Literal[
    "chapter",
    "hero_title",
    "identity_card",
    "visual_effect",
]


class LongVisualRenderError(ValueError):
    """A long-format visual cannot satisfy its canonical render contract."""


@dataclass(frozen=True, slots=True)
class LongVisualRenderRequest:
    recipe_identity: str
    event_id: str
    role: LongVisualRole
    display: str
    duration_sec: float
    target_width: int
    target_height: int
    layout_identity: str


@dataclass(frozen=True, slots=True)
class LongVisualRecipe:
    recipe_identity: str
    event_id: str
    role: LongVisualRole
    layout_identity: str
    display: str
    style_name: str
    canvas_width: int
    canvas_height: int
    content_width_ratio: float
    font_size_px: int
    safe_region: Literal["full", "lower"]
    full_frame: bool
    has_alpha: bool
    duration_sec: float
    extension: str
    codec_name: str
    pixel_format: str
    html_document: str


@dataclass(frozen=True, slots=True)
class BrowserRenderResult:
    path: Path
    width: int
    height: int
    duration_sec: float
    has_alpha: bool
    codec_name: str
    pixel_format: str


class BrowserRenderPort(Protocol):
    """External browser media-rendering seam."""

    def render(self, recipe: LongVisualRecipe) -> BrowserRenderResult: ...


@dataclass(frozen=True, slots=True)
class RenderedLongVisual:
    recipe: LongVisualRecipe
    media: BrowserRenderResult


#: 版位版本是契約，不是渲染器的私有常數——兩邊各寫一份就會漂移（見 _projection）。
_RECIPES: dict[LongVisualRole, dict[str, object]] = {
    "chapter": {
        "layout_identity": layout_identity("fullscreen_transition"),
        "style_name": "paper_hand",
        "content_width_ratio": 0.84,
        "font_size_px": 128,
        "safe_region": "full",
        "full_frame": True,
        "has_alpha": False,
        "extension": ".mp4",
        "codec_name": "h264",
        "pixel_format": "yuv420p",
    },
    "hero_title": {
        "layout_identity": layout_identity("hero_title"),
        "style_name": "paper",
        "content_width_ratio": 0.72,
        "font_size_px": 96,
        "safe_region": "lower",
        "full_frame": False,
        "has_alpha": True,
        "extension": ".mov",
        "codec_name": "prores",
        "pixel_format": "yuva444p12le",
    },
    "identity_card": {
        "layout_identity": layout_identity("identity_card"),
        "style_name": "paper",
        "content_width_ratio": 0.34,
        "font_size_px": 50,
        "safe_region": "lower",
        "full_frame": False,
        "has_alpha": True,
        "extension": ".mov",
        "codec_name": "prores",
        "pixel_format": "yuva444p12le",
    },
    "visual_effect": {
        "layout_identity": layout_identity("visual_effect"),
        "style_name": "concept_accent",
        "content_width_ratio": 0.48,
        "font_size_px": 44,
        "safe_region": "lower",
        "full_frame": False,
        "has_alpha": True,
        "extension": ".mov",
        "codec_name": "prores",
        "pixel_format": "yuva444p12le",
    },
}
_DURATION_TOLERANCE_SEC = (1 / 24) + 1e-6


class LongVisualRenderer:
    """Keep all long-format title geometry behind one browser-backed Interface."""

    def __init__(self, *, browser: BrowserRenderPort) -> None:
        self._browser = browser

    def render(self, request: LongVisualRenderRequest) -> RenderedLongVisual:
        if (
            not request.recipe_identity.strip()
            or not request.event_id.strip()
            or not request.display.strip()
            or not math.isfinite(request.duration_sec)
            or request.duration_sec <= 0
        ):
            raise LongVisualRenderError("long visual request is incomplete")
        values = _RECIPES[request.role]
        if (
            request.target_width <= 0
            or request.target_height <= 0
            or request.layout_identity != values["layout_identity"]
        ):
            raise LongVisualRenderError("long visual geometry does not match its canonical layout")
        recipe = LongVisualRecipe(
            recipe_identity=request.recipe_identity,
            event_id=request.event_id,
            role=request.role,
            layout_identity=request.layout_identity,
            display=request.display,
            style_name=str(values["style_name"]),
            canvas_width=request.target_width,
            canvas_height=request.target_height,
            content_width_ratio=float(values["content_width_ratio"]),
            font_size_px=int(values["font_size_px"]),
            safe_region=values["safe_region"],  # type: ignore[arg-type]
            full_frame=bool(values["full_frame"]),
            has_alpha=bool(values["has_alpha"]),
            duration_sec=request.duration_sec,
            extension=str(values["extension"]),
            codec_name=str(values["codec_name"]),
            pixel_format=str(values["pixel_format"]),
            html_document=_html_document(
                display=request.display,
                role=request.role,
                style_name=str(values["style_name"]),
                font_size_px=int(values["font_size_px"]),
                content_width_ratio=float(values["content_width_ratio"]),
                full_frame=bool(values["full_frame"]),
                canvas_width=request.target_width,
                canvas_height=request.target_height,
                duration_sec=request.duration_sec,
            ),
        )
        try:
            media = self._browser.render(recipe)
        except Exception as exc:
            raise LongVisualRenderError("browser rendering failed") from exc
        if (
            Path(media.path).suffix.lower() != recipe.extension
            or not Path(media.path).is_file()
            or media.width != recipe.canvas_width
            or media.height != recipe.canvas_height
            or not math.isclose(
                media.duration_sec,
                recipe.duration_sec,
                rel_tol=0,
                abs_tol=_DURATION_TOLERANCE_SEC,
            )
            or media.has_alpha is not recipe.has_alpha
            or media.codec_name != recipe.codec_name
            or media.pixel_format != recipe.pixel_format
        ):
            raise LongVisualRenderError("browser result violates the exact visual recipe")
        return RenderedLongVisual(recipe=recipe, media=media)


def recipe_document_digest(
    *,
    implementation_kind: str,
    display: str,
    target_width: int,
    target_height: int,
    duration_sec: float,
) -> str | None:
    """這筆指令**現在**會被畫成什麼樣——回傳該 HTML 的 sha256。

    `_engine._derived_asset_request` 的 recipe identity 立過一條規矩：「必須是畫面
    的函數，只放真的會改變輸出像素的欄位」。它漏了最直接的那一項——渲染器本身。
    後果不是壞掉，是**靜默地什麼都不會發生**：改了卡片設計、跑完整條 run，
    `find_exact_recipe` 照樣命中舊 identity，舊 bytes 原封不動再上片一次，
    沒有任何 diagnostic。

    2026-09-16 實測：章節卡字級從三階梯改成定值 104px 之後，蘇予昕長2 五張卡的
    recipe identity 一個字都沒變，全部命中 9/10 那批 168px／128px 的舊 MOV。

    把文件雜湊放進 identity，這件事就不必再靠人記得——`layout_version` 那個旋鈕
    還在，但忘了轉不再等於改動消失。同 bytes 不同 identity 由
    `ActiveAssetStore.publish` 接住（兩個配方算出同一個畫面就共用那份媒體），
    所以沒改到像素的卡只會多 render 一次，不會衝突。
    """

    role = BROWSER_ROLE_BY_IMPLEMENTATION.get(implementation_kind)
    if role is None:
        return None
    values = _RECIPES[role]  # type: ignore[index]
    document = _html_document(
        display=display,
        role=role,  # type: ignore[arg-type]
        style_name=str(values["style_name"]),
        font_size_px=int(values["font_size_px"]),  # type: ignore[arg-type]
        content_width_ratio=float(values["content_width_ratio"]),  # type: ignore[arg-type]
        full_frame=bool(values["full_frame"]),
        canvas_width=target_width,
        canvas_height=target_height,
        duration_sec=duration_sec,
    )
    return hashlib.sha256(document.encode("utf-8")).hexdigest()


_HERO_LINE_BREAKS = "，、。：；！？"


def _hero_lines(display: str) -> tuple[str, ...]:
    """把一行 Hero 文案拆成定版的錯位雙行。

    頂多三行（定版 punch_card_wide 的上限），優先在標點斷；沒標點又太長就從中間斷。
    短句（≤ 6 字）保持單行——強拆會把詞組切開。
    """
    text = display.strip()
    for index, char in enumerate(text):
        if char in _HERO_LINE_BREAKS and 1 < index < len(text) - 2:
            return (text[:index].strip(), text[index + 1 :].strip())
    # 沒有標點就保持單行。**絕不從中間硬拆**——按字數對半切會把詞組切開
    # （「喔我爸就／是這樣」「一天六七／千個念頭」）。斷行是導演的決定，定版
    # composition 因此給的是 line1/line2/line3 三個獨立欄位；這裡只有一個
    # `display`，所以唯一可靠的斷點是它自己帶的標點。
    return (text,)


_NAMECARD_SEPARATORS = "／｜/|"


def _paper_namecard_document(
    *,
    display: str,
    canvas_width: int,
    canvas_height: int,
    duration_sec: float,
) -> str:
    """來賓名牌——半透明紙卡＋手繪橘豎筆觸，落在左下。

    這份 HTML 是 `video/compositions/chapter_label/compositions/chapter_label_wide.html`
    （`align:"left"` + `style:"paper"`）的第二份實作——跟轉場卡、Hero 卡同一個
    結構問題，改一份就要同步另一份。ADR-066 原本自己造了一個 `identity_plaque`
    36px 置中藥丸，跟手冊寫的不是同一個東西。

    設計 token 取自定版：左 4% / 上 76%、紙白 rgba(251,250,247,.85)、
    橘筆觸 #e98965、姓名 50px/700、頭銜 29px/400 #6f6a62。

    `display` 形如「蘇予昕／諮商心理師」，以分隔號拆成姓名與頭銜。
    """
    text = display.strip()
    label, sub = text, ""
    for separator in _NAMECARD_SEPARATORS:
        if separator in text:
            head, _, tail = text.partition(separator)
            label, sub = head.strip(), tail.strip()
            break
    sub_html = f'      <div id="sub">{escape(sub)}</div>' + chr(10) if sub else ""
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={canvas_width},height={canvas_height}">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: {canvas_width}px; height: {canvas_height}px;
  overflow: hidden; background: transparent; }}
#root {{ position: relative; width: {canvas_width}px; height: {canvas_height}px;
  overflow: hidden; font-family: "LINE Seed TW", "Noto Sans TC", sans-serif; }}
#tag {{ position: absolute; left: 4%; top: 76%; transform: translateY(-50%);
  display: inline-flex; align-items: center; gap: 14px;
  background: rgba(251, 250, 247, 0.85);
  border: 1px solid rgba(217, 213, 207, 0.55); border-radius: 10px;
  padding: 14px 32px 17px 24px;
  box-shadow: 0 2px 10px rgba(20, 18, 15, 0.14);
  animation: tag-enter 420ms cubic-bezier(.2,.8,.2,1) both; }}
.tick-svg {{ flex: none; width: 19px; align-self: stretch; overflow: visible; }}
.tick-svg path {{ fill: none; stroke: #e98965; stroke-width: 8;
  stroke-linecap: round; opacity: .92; }}
#col {{ display: flex; flex-direction: column; }}
#text {{ white-space: nowrap; font-weight: 700; font-size: 50px;
  line-height: 1.2; color: #1c1915;
  animation: text-enter 380ms 120ms ease-out both; }}
#sub {{ white-space: nowrap; font-weight: 400; font-size: 29px;
  line-height: 1.35; color: #6f6a62; margin-top: 5px;
  animation: sub-enter 380ms 220ms ease-out both; }}
@keyframes tag-enter {{ from {{ opacity: 0; transform: translateY(-38%); }}
  to {{ opacity: 1; transform: translateY(-50%); }} }}
@keyframes text-enter {{ from {{ opacity: 0; transform: translateX(-14px); }}
  to {{ opacity: 1; transform: translateX(0); }} }}
@keyframes sub-enter {{ from {{ opacity: 0; }} to {{ opacity: 1; }} }}
</style>
</head>
<body data-role="identity_card" data-style="paper">
<main id="root" data-root="true" data-composition-id="chapter_label_wide" data-no-timeline
  data-width="{canvas_width}" data-height="{canvas_height}" data-start="0"
  data-duration="{duration_sec:.6f}">
  <div id="tag" class="style-paper">
    <svg class="tick-svg" viewBox="0 0 19 100" preserveAspectRatio="none">
      <path d="M9,4 C12,26 6,52 10,74 S8,92 9,96"/>
    </svg>
    <div id="col">
      <div id="text">{escape(label)}</div>
{sub_html}    </div>
  </div>
</main>
</body>
</html>"""


def _paper_hero_document(
    *,
    display: str,
    font_size_px: int,
    content_width_ratio: float,
    canvas_width: int,
    canvas_height: int,
    duration_sec: float,
) -> str:
    """Hero 大字卡——錯位紙卡、手繪橘底線、落在說話者負空間。

    這份 HTML 是 `video/compositions/punch_card/compositions/punch_card_wide.html`
    （tier1 + style:paper）的第二份實作——與轉場卡同一個結構問題，不是理想狀態；
    改其中一份就要同步另一份。ADR-066 一開始沒沿用定版配方，自己造了一個
    64px 的單行藥丸放在畫面正中（compact_paper），不但小、還正好壓在臉上——
    而手冊寫的是「長片**唯一配方**：punch_card_wide tier1 + style:paper，
    每行字級上限 96px；紙卡放在說話者負空間，避免壓迫臉部」。
    設計 token 一律取自定版檔：紙白 rgba(251,250,247,.86)、ink #1c1915、
    橘線 #e98965、錯位 32px / -24px、pos-y 66%。
    """
    # 字級與寬度取自配方，不在 HTML 裡另寫一份數字——兩份數字遲早會漂移
    # （2026-09-09：版位版本就是這樣裂成兩個真相來源，害 27 個測試一起紅）。
    lines = _hero_lines(display)
    offsets = ("0px", "32px", "-24px")
    blocks = "".join(
        f'    <div class="line" style="margin-left: {offsets[index]}; '
        f'animation-delay: {index * 90}ms">{escape(line)}'
        '<svg class="uline" viewBox="0 0 100 22" preserveAspectRatio="none">'
        '<path d="M2,9 C18,12 30,8 46,13 S62,8 74,14 S90,9 98,12"/></svg></div>' + chr(10)
        for index, line in enumerate(lines)
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={canvas_width},height={canvas_height}">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: {canvas_width}px; height: {canvas_height}px;
  overflow: hidden; background: transparent; }}
#root {{ position: relative; width: {canvas_width}px; height: {canvas_height}px;
  overflow: hidden; font-family: "LINE Seed TW", sans-serif; }}
#card {{ position: absolute; left: 50%; top: 66%; transform: translate(-50%, -50%);
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  max-width: {content_width_ratio * 100:.0f}%; }}
.line {{ position: relative; display: inline-block; white-space: nowrap;
  background: rgba(251, 250, 247, 0.86); color: #1c1915;
  border: 1px solid rgba(217, 213, 207, 0.55); border-radius: 10px;
  box-shadow: 0 2px 10px rgba(20, 18, 15, 0.14);
  font-weight: 900; font-size: {font_size_px}px; line-height: 1.1; padding: 8px 24px 15px;
  animation: hero-enter 420ms cubic-bezier(.2,.8,.2,1) both; }}
.line svg.uline {{ position: absolute; left: 22px; right: 22px; bottom: 10px;
  width: calc(100% - 44px); height: 22px; overflow: visible; pointer-events: none; }}
.line svg.uline path {{ fill: none; stroke: #e98965; stroke-width: 9;
  stroke-linecap: round; opacity: .92; }}
@keyframes hero-enter {{ from {{ opacity: 0; transform: translateY(18px); }}
  to {{ opacity: 1; transform: translateY(0); }} }}
</style>
</head>
<body data-role="hero_title" data-style="paper">
<main id="root" data-root="true" data-composition-id="punch_card_wide" data-no-timeline
  data-width="{canvas_width}" data-height="{canvas_height}" data-start="0"
  data-duration="{duration_sec:.6f}">
  <div id="card" class="tier1 style-paper">
{blocks}  </div>
</main>
</body>
</html>"""


def _html_document(
    *,
    display: str,
    role: LongVisualRole,
    style_name: str,
    font_size_px: int,
    content_width_ratio: float,
    full_frame: bool,
    canvas_width: int,
    canvas_height: int,
    duration_sec: float,
) -> str:
    if role == "chapter":
        return _paper_hand_chapter_document(
            display=display,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            duration_sec=duration_sec,
        )
    if role == "identity_card":
        return _paper_namecard_document(
            display=display,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            duration_sec=duration_sec,
        )
    if role == "hero_title":
        return _paper_hero_document(
            display=display,
            font_size_px=font_size_px,
            content_width_ratio=content_width_ratio,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            duration_sec=duration_sec,
        )
    background = "#f4efe7" if full_frame else "transparent"
    panel = {
        "hero_title": (
            "background: rgba(250, 248, 243, 0.92); border: 1px solid rgba(68, 59, 50, 0.20);"
        ),
        "identity_card": (
            "background: rgba(250, 248, 243, 0.94); border: 1px solid rgba(68, 59, 50, 0.18);"
        ),
    }.get(role, "background: transparent;")
    top = {
        "chapter": "50%",
        "hero_title": "67%",
        "identity_card": "72%",
        "visual_effect": "68%",
    }[role]
    text_color = "#f8f5ef" if role == "visual_effect" else "#27231f"
    shadow = "0 2px 10px rgba(20, 18, 16, 0.72)" if role == "visual_effect" else "none"
    width_rule = f"width: {content_width_ratio * 100:.0f}%;" if role == "chapter" else ""
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={canvas_width},height={canvas_height}">
<style>
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; width: {canvas_width}px; height: {canvas_height}px;
  overflow: hidden; background: {background}; }}
#stage {{ position: relative; width: {canvas_width}px; height: {canvas_height}px;
  background: {background}; font-family: "LINE Seed TW", sans-serif; }}
#copy {{ position: absolute; left: 50%; top: {top}; transform: translate(-50%, -50%);
  {width_rule} max-width: {content_width_ratio * 100:.0f}%; padding: 14px 28px; {panel}
  color: {text_color}; font-size: {font_size_px}px; font-weight: 800; line-height: 1.18;
  text-align: center; text-shadow: {shadow}; border-radius: 12px;
  animation: visual-enter 420ms cubic-bezier(.2,.8,.2,1) both; }}
#mark {{ width: 42%; height: 8px; margin: 12px auto 0; border-radius: 6px;
  background: #d96f4b; transform-origin: left center;
  animation: mark-enter 360ms 360ms ease-out both; }}
@keyframes visual-enter {{ from {{ opacity: 0; transform: translate(-50%, -42%) scale(.96); }}
  to {{ opacity: 1; transform: translate(-50%, -50%) scale(1); }} }}
@keyframes mark-enter {{ from {{ opacity: 0; transform: scaleX(0); }}
  to {{ opacity: 1; transform: scaleX(1); }} }}
</style>
</head>
<body data-role="{role}" data-style="{style_name}">
<main id="stage" data-root="true" data-composition-id="long_visual" data-no-timeline
  data-width="{canvas_width}" data-height="{canvas_height}" data-start="0"
  data-duration="{duration_sec:.6f}">
  <div id="copy">{escape(display)}<div id="mark"></div></div>
</main>
</body>
</html>"""


# 章節卡的字級是定值，不照字數降級。
#
# 2026-09-08 之前這裡是三階梯（>12 字 104px、>9 字 128px、其餘 168px），本意是
# 「長標題不要斷成孤字」。它確實擋掉了孤字，但也讓同一支影片裡的卡片差 62%：
# 蘇予昕長2（punch-L03）五張章節卡落在 168 / 128 / 128 / 104 / 104 三個字級，
# 168px 那張的字橫跨 1920 裡的約 1510px，手繪底線跟字等長，底線就不再讀成底線。
# 修修 2026-09-16：「有 transition 的字型還是太大，不是已經統一了嗎？」——當初
# 統一的是這份與 transition_title_wide.html 兩份程式碼，不是視覺大小。
#
# 104px 一行載得下，所以字數不必再換字級：.stage 扣掉左右 160px 之後有 1600px，
# LINE Seed TW 900 在 Chromium 實測（2026-09-16）13 字 1366px、14 字 1471px、
# 15 字 1576px，都還沒換行。蘇予昕那一集的章節標題落在 9–13 字。
_CHAPTER_TITLE_FONT_PX = 104


def _paper_hand_chapter_document(
    *,
    display: str,
    canvas_width: int,
    canvas_height: int,
    duration_sec: float,
) -> str:
    """Render the approved B2 Big Title Transition visual language."""

    title = escape(display)
    # ⚠️ 這份 HTML 是 `video/compositions/transition_title/compositions/
    # transition_title_wide.html` 的第二份實作。兩邊的字級規則必須一致——2026-09-08
    # 修好了那一份，這一份沒動，於是 pipeline 渲出來的卡照樣斷成孤字，visual_review
    # 退了三張，人卻看不出兩份的差別在哪。
    title_font_px = _CHAPTER_TITLE_FONT_PX
    # 手繪底線跟字等長（修修六輪：「跟字等長才像畫線」），寬度用字數×字級算，
    # 不量 DOM——字型載入時序在 hyperframes capture 下不可靠。
    # transition_title_wide.html 一直是這樣算的；這一份先前寫死 min(92%, 1460px)，
    # 於是 168px 的卡底線比字短、104px 的卡底線比字長兩百多 px，兩份畫出來不是同一張卡。
    underline_width_px = round(len(display) * title_font_px * 1.01)
    paper_texture = (
        "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'"
        " width='360' height='360' viewBox='0 0 360 360'%3E"
        "%3Cfilter id='paper-grain'%3E%3CfeTurbulence type='fractalNoise'"
        " baseFrequency='.018 .13' numOctaves='4' seed='17'/%3E"
        "%3CfeColorMatrix type='saturate' values='0'/%3E%3C/filter%3E"
        "%3Crect width='100%25' height='100%25' filter='url(%23paper-grain)'"
        " opacity='.32'/%3E%3C/svg%3E"
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width={canvas_width},height={canvas_height}">
<style>
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; width: {canvas_width}px; height: {canvas_height}px;
  overflow: hidden; background: #e8d9c8; }}
#root {{ position: relative; width: {canvas_width}px; height: {canvas_height}px;
  overflow: hidden; font-family: "LINE Seed TW", sans-serif; color: #1c1915; }}
#paper {{ position: absolute; inset: 0; background: #e8d9c8; }}
#paper::before {{ content: ""; position: absolute; inset: 0; opacity: .48;
  background-image: url("{paper_texture}");
  background-size: 360px 360px; mix-blend-mode: multiply; }}
.stage {{ position: absolute; inset: 0 160px 200px; display: flex;
  flex-direction: column; align-items: center; justify-content: center; gap: 36px; }}
.kicker-row {{ display: flex; align-items: center; gap: 24px;
  animation: kicker-enter .42s .08s ease-out both; }}
.kbar {{ width: 96px; height: 22px; overflow: visible; }}
.kbar path, .uline path {{ fill: none; stroke: #e98965; stroke-width: 9;
  stroke-linecap: round; }}
.kbar path {{ stroke-width: 8; }}
.kicker {{ color: #6f6a62; font-size: 52px; font-weight: 700;
  letter-spacing: .18em; }}
.title {{ max-width: 1600px; color: #1c1915; font-size: {title_font_px}px;
  font-weight: 900; line-height: 1.12; letter-spacing: .01em; text-align: center;
  animation: title-enter .55s .10s cubic-bezier(.22,.75,.2,1) both; }}
.uline {{ width: {underline_width_px}px; height: 28px; overflow: visible;
  transform-origin: left center; animation: underline-enter .42s .28s ease-out both; }}
@keyframes kicker-enter {{
  from {{ opacity: 0; transform: translateX(-18px); }}
  to {{ opacity: 1; transform: translateX(0); }}
}}
@keyframes title-enter {{
  from {{ opacity: 0; transform: translateY(108%); }}
  to {{ opacity: 1; transform: translateY(0); }}
}}
@keyframes underline-enter {{
  from {{ opacity: 0; transform: scaleX(0); }}
  to {{ opacity: 1; transform: scaleX(1); }}
}}
</style>
</head>
<body data-role="chapter" data-style="paper_hand">
<main id="root" data-root="true" data-composition-id="transition_title_wide" data-no-timeline
  data-width="{canvas_width}" data-height="{canvas_height}" data-start="0"
  data-duration="{duration_sec:.6f}">
  <div id="paper"></div>
  <section class="stage">
    <div class="kicker-row">
      <svg class="kbar" viewBox="0 0 100 22" preserveAspectRatio="none">
        <path d="M3,12 C30,9.5 62,14 97,11"/>
      </svg>
    </div>
    <div class="title">{title}</div>
    <svg class="uline" viewBox="0 0 100 22" preserveAspectRatio="none">
      <path d="M2,9 C18,12 30,8 46,13 S62,8 74,14 S90,9 98,12"/>
    </svg>
  </section>
</main>
</body>
</html>"""
