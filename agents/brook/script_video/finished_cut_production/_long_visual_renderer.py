"""Long-format visual recipes rendered through an injected browser seam."""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Literal, Protocol

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


_RECIPES: dict[LongVisualRole, dict[str, object]] = {
    "chapter": {
        "layout_identity": "fullscreen_transition:v4",
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
        "layout_identity": "hero_title:v1",
        "style_name": "compact_paper",
        "content_width_ratio": 0.60,
        "font_size_px": 64,
        "safe_region": "lower",
        "full_frame": False,
        "has_alpha": True,
        "extension": ".mov",
        "codec_name": "prores",
        "pixel_format": "yuva444p12le",
    },
    "identity_card": {
        "layout_identity": "identity_card:v1",
        "style_name": "identity_plaque",
        "content_width_ratio": 0.34,
        "font_size_px": 36,
        "safe_region": "lower",
        "full_frame": False,
        "has_alpha": True,
        "extension": ".mov",
        "codec_name": "prores",
        "pixel_format": "yuva444p12le",
    },
    "visual_effect": {
        "layout_identity": "visual_effect:v1",
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
<main id="stage" data-root="true" data-composition-id="long_visual"
  data-width="{canvas_width}" data-height="{canvas_height}" data-start="0"
  data-duration="{duration_sec:.6f}">
  <div id="copy">{escape(display)}<div id="mark"></div></div>
</main>
</body>
</html>"""


def _paper_hand_chapter_document(
    *,
    display: str,
    canvas_width: int,
    canvas_height: int,
    duration_sec: float,
) -> str:
    """Render the approved B2 Big Title Transition visual language."""

    title = escape(display)
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
.title {{ max-width: 1600px; color: #1c1915; font-size: 128px;
  font-weight: 900; line-height: 1.12; letter-spacing: .01em; text-align: center;
  animation: title-enter .55s .10s cubic-bezier(.22,.75,.2,1) both; }}
.uline {{ width: min(92%, 1460px); height: 28px; overflow: visible;
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
<main id="root" data-root="true" data-composition-id="transition_title_wide"
  data-width="{canvas_width}" data-height="{canvas_height}" data-start="0"
  data-duration="{duration_sec:.6f}">
  <div id="paper"></div>
  <section class="stage">
    <div class="kicker-row">
      <svg class="kbar" viewBox="0 0 100 22" preserveAspectRatio="none">
        <path d="M3,12 C30,9.5 62,14 97,11"/>
      </svg>
      <div class="kicker">章節</div>
    </div>
    <div class="title">{title}</div>
    <svg class="uline" viewBox="0 0 100 22" preserveAspectRatio="none">
      <path d="M2,9 C18,12 30,8 46,13 S62,8 74,14 S90,9 98,12"/>
    </svg>
  </section>
</main>
</body>
</html>"""
