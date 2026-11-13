"""自由艦隊活動封面（1600×800 JPEG）的 Pillow renderer。

VPS 是無頭 Linux、沒有瀏覽器，所以不走 HTML 截圖，直接用 Pillow 疊圖層。
版面照品牌設計稿（原型是 HTML 的 covers_v3）：

1. 底圖 `assets/cover_plate_1600x800.png`（天空漸層＋左下海浪＋右上星點）原封不動貼上。
2. 左側文字區：x=196 起、最寬 660px，在 y∈[0,600] 垂直置中（下方是海浪）。
   - eyebrow：46px，Latin 字用 Plus Jakarta Sans ExtraBold、中文／全形字逐字改用 CJK Bold。
   - 大標：1 行 146px／2 行 110px，CJK Black，行高 1.08、字距 +0.01em，
     整塊往前斜 8°（CSS `skewX(-8deg)`，原點在大標區塊左下）。
   - 日期膠囊（可省略）：CJK Bold 38px 白字、深藍全圓角底，整顆繞中心斜 8°。
   任何一行放不下 660px 就逐步縮字級。
3. 主圖（可省略）：在 left=880, top=70, 520×640 的格子裡 contain 置中。
   `book` 加兩層深藍柔陰影；`illustration` 先把 alpha < 40 的半透明毛邊清掉、不加陰影。
4. 右下角 logo：高 36px，距右 44px、距下 34px。

斜體是先畫在透明圖層、再用 affine transform 錯切；為了邊緣銳利，斜的圖層用 2 倍
解析度畫完再縮回來。放不放得下的判斷已經把錯切多出來的寬度算進去。
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

from PIL import Image, ImageDraw, ImageFilter

from shared.fleet_cover import fonts
from shared.fleet_cover.fonts import FontRef

# ── 版面常數（設計稿數值）──
CANVAS_W, CANVAS_H = 1600, 800
DEEP = (0x23, 0x62, 0x97)
WHITE = (255, 255, 255)

TEXT_LEFT = 196
TEXT_MAX_W = 660
BAND_TOP, BAND_BOTTOM = 0, 600

EYEBROW_SIZE = 46
EYEBROW_LINE_HEIGHT = 1.1
EYEBROW_GAP = 10

HEADLINE_SIZE_1 = 146
HEADLINE_SIZE_2 = 110
HEADLINE_LINE_HEIGHT = 1.08
HEADLINE_TRACKING = 0.01  # em

PILL_GAP = 26
PILL_SIZE = 38
PILL_PAD_Y = 8
PILL_PAD_X = 32

SKEW_DEG = 8
SKEW_T = math.tan(math.radians(SKEW_DEG))

SLOT_LEFT, SLOT_TOP, SLOT_W, SLOT_H = 880, 70, 520, 640
ILLUSTRATION_ALPHA_FLOOR = 40
# (alpha 比例, 模糊 std, y 位移)；CSS drop-shadow 的 blur 半徑 ≈ 2×std
BOOK_SHADOWS = [(0.28, 14, 18), (0.14, 4, 4)]

LOGO_H = 36  # 修修：logo 不是重點，只要右下角小小的
LOGO_MARGIN_RIGHT = 44
LOGO_MARGIN_BOTTOM = 34

MIN_FONT_SIZE = 20
SHRINK_STEP = 2
SUPERSAMPLE = 2

MainKind = Literal["book", "illustration", "none"]
_MAIN_KINDS = ("book", "illustration", "none")


@dataclass
class CoverSpec:
    eyebrow: str
    headline_lines: list[str]  # 1 或 2 行
    date_label: str = ""  # 例："12/6（六）20:00・線上直播"；空字串＝不畫膠囊
    main_image: bytes | None = None
    main_kind: MainKind = "none"


@dataclass
class _Layout:
    """文字區的計算結果（座標都是 1x 畫布座標）。"""

    eyebrow_size: int
    headline_size: int
    pill_size: int | None
    top: float
    eyebrow_baseline: float | None
    headline_baselines: list[float]
    headline_origin_y: float  # 大標錯切原點（區塊底）
    pill_box: tuple[float, float, float, float] | None  # 錯切前的 x0, y0, x1, y1
    eyebrow_width: float
    headline_widths: list[float] = field(default_factory=list)  # 含錯切多出的寬度
    pill_width: float = 0.0  # 含錯切多出的寬度


# ── 驗證 ──


def _validate(spec: CoverSpec) -> list[str]:
    lines = spec.headline_lines
    if not isinstance(lines, (list, tuple)) or not 1 <= len(lines) <= 2:
        raise ValueError("headline_lines 必須是 1–2 行")
    cleaned = []
    for line in lines:
        if not isinstance(line, str) or not line.strip():
            raise ValueError("headline_lines 每一行都必須是非空字串")
        cleaned.append(line.strip())
    if spec.main_kind not in _MAIN_KINDS:
        raise ValueError(f"main_kind 必須是 {_MAIN_KINDS} 之一：{spec.main_kind!r}")
    return cleaned


# ── 量字 ──


def _is_cjk(ch: str) -> bool:
    """CJK、假名、全形標點（含「・」「（」）都在 U+2E80 之後。"""
    return ord(ch) >= 0x2E80


def _runs(text: str, latin: FontRef, cjk: FontRef) -> list[tuple[str, FontRef]]:
    """把混排字串切成同字型的連續段落。"""
    runs: list[tuple[str, FontRef]] = []
    for ch in text:
        ref = cjk if _is_cjk(ch) else latin
        if runs and runs[-1][1] == ref:
            runs[-1] = (runs[-1][0] + ch, ref)
        else:
            runs.append((ch, ref))
    return runs


def _runs_width(runs: list[tuple[str, FontRef]], size: int) -> float:
    return sum(fonts.load(ref, size).getlength(text) for text, ref in runs)


def _content_height(ref: FontRef, size: int) -> tuple[int, int]:
    """CSS 的 content area：(ascent, descent)。"""
    return fonts.load(ref, size).getmetrics()


def _tracked_width(text: str, ref: FontRef, size: int) -> float:
    """加了字距的寬度（最後一個字後面的字距看不到，不算）。"""
    font = fonts.load(ref, size)
    advance = sum(font.getlength(ch) for ch in text)
    return advance + HEADLINE_TRACKING * size * (len(text) - 1)


def _headline_metrics(lines: list[str], ref: FontRef, size: int):
    """回傳 (各行 baseline 相對區塊頂, 區塊高, 各行含錯切寬度)。"""
    asc, desc = _content_height(ref, size)
    lh = HEADLINE_LINE_HEIGHT * size
    half_leading = (lh - (asc + desc)) / 2
    block_h = lh * len(lines)
    font = fonts.load(ref, size)
    baselines, widths = [], []
    for i, line in enumerate(lines):
        baseline = i * lh + half_leading + asc
        ink_top = baseline + font.getbbox(line, anchor="ls")[1]
        lean = max(0.0, block_h - ink_top) * SKEW_T
        baselines.append(baseline)
        widths.append(_tracked_width(line, ref, size) + lean)
    return baselines, block_h, widths


def _pill_dims(text: str, ref: FontRef, size: int) -> tuple[float, float, float]:
    """回傳 (錯切前寬, 高, 含錯切的寬)。膠囊繞中心錯切，右緣多出 h/2·tan。"""
    asc, desc = _content_height(ref, size)
    h = asc + desc + 2 * PILL_PAD_Y
    w = fonts.load(ref, size).getlength(text) + 2 * PILL_PAD_X
    return w, h, w + h / 2 * SKEW_T


def _shrink_to_fit(start: int, width_at) -> int:
    size = start
    while size > MIN_FONT_SIZE and width_at(size) > TEXT_MAX_W:
        size -= SHRINK_STEP
    return max(size, MIN_FONT_SIZE)


def _layout(spec: CoverSpec) -> _Layout:
    lines = _validate(spec)
    latin, black, bold = fonts.latin(), fonts.cjk_black(), fonts.cjk_bold()

    # eyebrow
    eyebrow = spec.eyebrow.strip()
    eb_runs = _runs(eyebrow, latin, bold) if eyebrow else []
    eb_size = _shrink_to_fit(EYEBROW_SIZE, lambda s: _runs_width(eb_runs, s)) if eyebrow else 0
    eb_lh = EYEBROW_LINE_HEIGHT * eb_size if eyebrow else 0.0

    # headline
    hl_start = HEADLINE_SIZE_1 if len(lines) == 1 else HEADLINE_SIZE_2
    hl_size = _shrink_to_fit(hl_start, lambda s: max(_headline_metrics(lines, black, s)[2]))
    hl_baselines, hl_block_h, hl_widths = _headline_metrics(lines, black, hl_size)

    # pill
    date = spec.date_label.strip()
    pill_size = _shrink_to_fit(PILL_SIZE, lambda s: _pill_dims(date, bold, s)[2]) if date else None
    pill_w = pill_h = pill_full_w = 0.0
    if pill_size:
        pill_w, pill_h, pill_full_w = _pill_dims(date, bold, pill_size)

    total = eb_lh + (EYEBROW_GAP if eyebrow else 0) + hl_block_h
    if pill_size:
        total += PILL_GAP + pill_h
    top = BAND_TOP + (BAND_BOTTOM - BAND_TOP - total) / 2

    y = top
    eb_baseline = None
    if eyebrow:
        asc, desc = _content_height(latin, eb_size)
        eb_baseline = y + (eb_lh - (asc + desc)) / 2 + asc
        y += eb_lh + EYEBROW_GAP

    headline_top = y
    y += hl_block_h

    pill_box = None
    if pill_size:
        y += PILL_GAP
        pill_box = (TEXT_LEFT, y, TEXT_LEFT + pill_w, y + pill_h)

    return _Layout(
        eyebrow_size=eb_size,
        headline_size=hl_size,
        pill_size=pill_size,
        top=top,
        eyebrow_baseline=eb_baseline,
        headline_baselines=[headline_top + b for b in hl_baselines],
        headline_origin_y=headline_top + hl_block_h,
        pill_box=pill_box,
        eyebrow_width=_runs_width(eb_runs, eb_size) if eyebrow else 0.0,
        headline_widths=hl_widths,
        pill_width=pill_full_w,
    )


# ── 畫圖 ──


def _skew(layer: Image.Image, origin_y: float) -> Image.Image:
    """CSS skewX(-8deg)：origin_y 以上往右傾。PIL 的 affine 是「輸出→輸入」反向對應。"""
    data = (1, SKEW_T, -SKEW_T * origin_y, 0, 1, 0)
    return layer.transform(
        layer.size, Image.Transform.AFFINE, data, resample=Image.Resampling.BICUBIC
    )


def _ss_layer() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    layer = Image.new("RGBA", (CANVAS_W * SUPERSAMPLE, CANVAS_H * SUPERSAMPLE), (0, 0, 0, 0))
    return layer, ImageDraw.Draw(layer)


def _downsample(layer: Image.Image) -> Image.Image:
    return layer.resize((CANVAS_W, CANVAS_H), Image.Resampling.LANCZOS)


def _draw_eyebrow(canvas: Image.Image, spec: CoverSpec, lay: _Layout) -> None:
    if lay.eyebrow_baseline is None:
        return
    draw = ImageDraw.Draw(canvas)
    x = float(TEXT_LEFT)
    for text, ref in _runs(spec.eyebrow.strip(), fonts.latin(), fonts.cjk_bold()):
        font = fonts.load(ref, lay.eyebrow_size)
        draw.text((x, lay.eyebrow_baseline), text, font=font, fill=DEEP, anchor="ls")
        x += font.getlength(text)


def _draw_headline(canvas: Image.Image, lines: list[str], lay: _Layout) -> None:
    ss = SUPERSAMPLE
    layer, draw = _ss_layer()
    font = fonts.load(fonts.cjk_black(), lay.headline_size * ss)
    tracking = HEADLINE_TRACKING * lay.headline_size * ss
    for line, baseline in zip(lines, lay.headline_baselines):
        x = TEXT_LEFT * ss
        for ch in line:
            draw.text((x, baseline * ss), ch, font=font, fill=DEEP, anchor="ls")
            x += font.getlength(ch) + tracking
    canvas.alpha_composite(_downsample(_skew(layer, lay.headline_origin_y * ss)))


def _draw_pill(canvas: Image.Image, text: str, lay: _Layout) -> None:
    if lay.pill_box is None or lay.pill_size is None:
        return
    ss = SUPERSAMPLE
    x0, y0, x1, y1 = (v * ss for v in lay.pill_box)
    layer, draw = _ss_layer()
    draw.rounded_rectangle((x0, y0, x1, y1), radius=(y1 - y0) / 2, fill=DEEP)
    ref = fonts.cjk_bold()
    font = fonts.load(ref, lay.pill_size * ss)
    asc, _desc = font.getmetrics()
    draw.text(
        (x0 + PILL_PAD_X * ss, y0 + PILL_PAD_Y * ss + asc), text, font=font, fill=WHITE, anchor="ls"
    )
    canvas.alpha_composite(_downsample(_skew(layer, (y0 + y1) / 2)))


def _contain(img: Image.Image) -> tuple[Image.Image, int, int]:
    scale = min(SLOT_W / img.width, SLOT_H / img.height)
    w, h = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    fitted = img.resize((w, h), Image.Resampling.LANCZOS)
    return fitted, SLOT_LEFT + (SLOT_W - w) // 2, SLOT_TOP + (SLOT_H - h) // 2


def _clean_halo(img: Image.Image) -> Image.Image:
    r, g, b, a = img.split()
    a = a.point(lambda v: 0 if v < ILLUSTRATION_ALPHA_FLOOR else v)
    return Image.merge("RGBA", (r, g, b, a))


def _draw_shadow(canvas: Image.Image, img: Image.Image, x: int, y: int) -> None:
    pad = 64
    for opacity, blur, dy in BOOK_SHADOWS:
        alpha = Image.new("L", (img.width + 2 * pad, img.height + 2 * pad), 0)
        alpha.paste(img.getchannel("A").point(lambda v, o=opacity: round(v * o)), (pad, pad))
        alpha = alpha.filter(ImageFilter.GaussianBlur(blur))
        shadow = Image.new("RGBA", alpha.size, DEEP + (0,))
        shadow.putalpha(alpha)
        _composite_clipped(canvas, shadow, x - pad, y - pad + dy)


def _composite_clipped(canvas: Image.Image, layer: Image.Image, x: int, y: int) -> None:
    """alpha_composite 不接受負座標或超出畫布，先自己裁掉。"""
    left, top = max(0, x), max(0, y)
    right, bottom = min(canvas.width, x + layer.width), min(canvas.height, y + layer.height)
    if right <= left or bottom <= top:
        return
    crop = layer.crop((left - x, top - y, right - x, bottom - y))
    canvas.alpha_composite(crop, (left, top))


def _draw_main(canvas: Image.Image, spec: CoverSpec) -> None:
    if spec.main_kind == "none" or not spec.main_image:
        return
    try:
        img = Image.open(io.BytesIO(spec.main_image))
        img.load()
    except Exception as exc:  # noqa: BLE001 — Pillow 解碼失敗的例外種類很多
        raise ValueError(f"main_image 無法解碼成圖片：{exc}") from exc
    img = img.convert("RGBA")
    if spec.main_kind == "illustration":
        img = _clean_halo(img)
    fitted, x, y = _contain(img)
    if spec.main_kind == "book":
        _draw_shadow(canvas, fitted, x, y)
    _composite_clipped(canvas, fitted, x, y)


@lru_cache(maxsize=1)
def _plate() -> Image.Image:
    img = Image.open(fonts.ASSETS_DIR / "cover_plate_1600x800.png").convert("RGBA")
    if img.size != (CANVAS_W, CANVAS_H):
        img = img.resize((CANVAS_W, CANVAS_H), Image.Resampling.LANCZOS)
    return img


@lru_cache(maxsize=1)
def _logo() -> Image.Image:
    img = Image.open(fonts.ASSETS_DIR / "logo_compact.png").convert("RGBA")
    w = round(img.width * LOGO_H / img.height)
    return img.resize((w, LOGO_H), Image.Resampling.LANCZOS)


def _draw_logo(canvas: Image.Image) -> None:
    logo = _logo()
    x = CANVAS_W - LOGO_MARGIN_RIGHT - logo.width
    y = CANVAS_H - LOGO_MARGIN_BOTTOM - logo.height
    canvas.alpha_composite(logo, (x, y))


# JPEG：同一張圖 PNG 130–460 KB → JPEG 約 90–180 KB（2026-11-13 修修裁決改 JPG）。
# subsampling=0（4:4:4）讓深藍字邊緣不糊；quality 88 目檢看不出壓縮痕跡。
JPEG_QUALITY = 88


def render_cover(spec: CoverSpec, *, fmt: str = "jpeg") -> bytes:
    """畫一張 1600×800 的活動封面，預設回傳 JPEG bytes（`fmt="png"` 給測試做像素比對）。"""
    lines = _validate(spec)
    lay = _layout(spec)
    canvas = _plate().copy()
    _draw_eyebrow(canvas, spec, lay)
    _draw_headline(canvas, lines, lay)
    _draw_pill(canvas, spec.date_label.strip(), lay)
    _draw_main(canvas, spec)
    _draw_logo(canvas)
    out = io.BytesIO()
    rgb = canvas.convert("RGB")
    if fmt == "png":
        rgb.save(out, format="PNG", optimize=True)
    elif fmt == "jpeg":
        rgb.save(
            out, format="JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True, progressive=True
        )
    else:
        raise ValueError(f"fmt must be 'jpeg' or 'png', got {fmt!r}")
    return out.getvalue()
