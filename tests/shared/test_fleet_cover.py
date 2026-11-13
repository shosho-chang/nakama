"""shared.fleet_cover：封面 renderer＋插圖生成 helper。

測試環境沒有系統 CJK 字型：把 CJK Black / Bold 都指到 repo 內附的 Plus Jakarta
（測試用 Latin 文字即可）。OpenAI 呼叫全部 mock，不打網路。
"""

from __future__ import annotations

import base64
import io

import httpx
import pytest
from PIL import Image

from shared.fleet_cover import (
    CoverFontError,
    CoverSpec,
    IllustrationError,
    fonts,
    generate_illustration,
    illustration,
    render_cover,
)
from shared.fleet_cover.render import (
    HEADLINE_SIZE_1,
    HEADLINE_SIZE_2,
    SLOT_H,
    SLOT_LEFT,
    SLOT_TOP,
    SLOT_W,
    TEXT_LEFT,
    TEXT_MAX_W,
    _layout,
)


@pytest.fixture(autouse=True)
def _latin_as_cjk(monkeypatch):
    path = str(fonts.LATIN_FONT_PATH)
    monkeypatch.setenv(fonts.ENV_CJK_BLACK, path)
    monkeypatch.setenv(fonts.ENV_CJK_BOLD, path)


def _png(color=(200, 30, 30, 255), size=(300, 400)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", size, color).save(out, format="PNG")
    return out.getvalue()


def _open(png: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(png))
    img.load()
    return img


# ── render_cover ──


def test_render_cover_returns_png_1600x800():
    png = render_cover(CoverSpec("Captain's Book Club", ["June Pick"], "6/28 20:00"))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    img = _open(png)
    assert img.format == "PNG"
    assert img.size == (1600, 800)
    assert img.mode == "RGB"


def test_headline_one_line_uses_146():
    assert _layout(CoverSpec("Club", ["June"])).headline_size == HEADLINE_SIZE_1 == 146


def test_headline_two_lines_uses_110():
    assert _layout(CoverSpec("Club", ["Ultra", "Food"])).headline_size == HEADLINE_SIZE_2 == 110


def test_long_headline_shrinks_to_fit_660():
    lay = _layout(CoverSpec("Club", ["WWWWWWWWWWWW"]))
    assert lay.headline_size < HEADLINE_SIZE_1
    assert max(lay.headline_widths) <= TEXT_MAX_W


def test_long_eyebrow_and_date_shrink_to_fit():
    lay = _layout(CoverSpec("W" * 30, ["Hi"], "W" * 22))
    assert lay.eyebrow_size < 46 and lay.eyebrow_width <= TEXT_MAX_W
    assert lay.pill_size is not None and lay.pill_size < 38 and lay.pill_width <= TEXT_MAX_W


def test_text_block_is_centred_in_band():
    lay = _layout(CoverSpec("Club", ["June"], "6/28"))
    assert lay.pill_box is not None
    bottom = lay.pill_box[3]
    assert lay.top == pytest.approx(600 - bottom, abs=0.01)
    assert lay.pill_box[0] == TEXT_LEFT


def test_empty_date_omits_pill():
    lay = _layout(CoverSpec("Club", ["June"], ""))
    assert lay.pill_box is None and lay.pill_size is None
    with_pill = _layout(CoverSpec("Club", ["June"], "6/28"))
    # 沒膠囊時文字區變矮，往下移以維持置中
    assert lay.top > with_pill.top


@pytest.mark.parametrize("lines", [["a", "b", "c"], [], ["ok", "  "], ["ok", 3]])
def test_invalid_headline_lines_raise(lines):
    with pytest.raises(ValueError):
        render_cover(CoverSpec("Club", lines))


def test_invalid_main_kind_raises():
    with pytest.raises(ValueError):
        render_cover(CoverSpec("Club", ["June"], main_image=_png(), main_kind="poster"))  # type: ignore[arg-type]


def _slot_center() -> tuple[int, int]:
    return SLOT_LEFT + SLOT_W // 2, SLOT_TOP + SLOT_H // 2


def test_main_kind_none_ignores_image():
    plain = _open(render_cover(CoverSpec("Club", ["June"])))
    with_img = _open(render_cover(CoverSpec("Club", ["June"], main_image=_png(), main_kind="none")))
    assert plain.getpixel(_slot_center()) == with_img.getpixel(_slot_center())


def test_main_kind_book_draws_image_with_shadow():
    plain = _open(render_cover(CoverSpec("Club", ["June"])))
    img = _open(render_cover(CoverSpec("Club", ["June"], main_image=_png(), main_kind="book")))
    assert img.getpixel(_slot_center()) == (200, 30, 30)
    # 300x400 contain 進 520x640 → 480x640，置中；圖下緣正下方有陰影
    below = (SLOT_LEFT + SLOT_W // 2, SLOT_TOP + SLOT_H + 10)
    assert img.getpixel(below) != plain.getpixel(below)


def test_main_kind_illustration_drops_faint_halo_and_has_no_shadow():
    art = Image.new("RGBA", (400, 400), (200, 30, 30, 30))  # 半透明毛邊
    art.paste((20, 160, 20, 255), (150, 150, 250, 250))  # 中間不透明主體
    buf = io.BytesIO()
    art.save(buf, format="PNG")

    plain = _open(render_cover(CoverSpec("Club", ["June"])))
    img = _open(
        render_cover(
            CoverSpec("Club", ["June"], main_image=buf.getvalue(), main_kind="illustration")
        )
    )
    assert img.getpixel(_slot_center()) == (20, 160, 20)
    # 400x400 contain 進 520x640 → 520x520，上緣 y=130；毛邊區應被清掉
    halo = (SLOT_LEFT + 20, SLOT_TOP + 60 + 20)
    assert img.getpixel(halo) == plain.getpixel(halo)
    # 沒有陰影：圖下方維持底圖
    below = (SLOT_LEFT + SLOT_W // 2, SLOT_TOP + 60 + 520 + 15)
    assert img.getpixel(below) == plain.getpixel(below)


def test_undecodable_main_image_raises():
    with pytest.raises(ValueError):
        render_cover(CoverSpec("Club", ["June"], main_image=b"not an image", main_kind="book"))


# ── fonts ──


def test_font_error_names_env_var_and_apt_package(monkeypatch):
    monkeypatch.delenv(fonts.ENV_CJK_BLACK, raising=False)
    monkeypatch.delenv(fonts.ENV_CJK_BOLD, raising=False)
    monkeypatch.setattr(fonts, "DEFAULT_CJK_BLACK_PATHS", ["/nonexistent/Black.ttc"])
    monkeypatch.setattr(fonts, "DEFAULT_CJK_BOLD_PATHS", ["/nonexistent/Bold.ttc"])
    with pytest.raises(CoverFontError) as exc:
        render_cover(CoverSpec("Club", ["June"]))
    msg = str(exc.value)
    assert fonts.ENV_CJK_BLACK in msg
    assert "fonts-noto-cjk-extra" in msg


def test_font_error_when_env_points_to_missing_file(monkeypatch):
    monkeypatch.setenv(fonts.ENV_CJK_BOLD, "/nonexistent/x.ttf")
    with pytest.raises(CoverFontError, match=fonts.ENV_CJK_BOLD):
        fonts.cjk_bold()


def test_ttc_picks_traditional_chinese_face(monkeypatch):
    families = [
        "Noto Sans CJK JP",
        "Noto Sans CJK KR",
        "Noto Sans CJK SC",
        "Noto Sans CJK TC",
        "Noto Sans CJK HK",
    ]

    class _Face:
        def __init__(self, i):
            self.i = i

        def getname(self):
            return families[self.i], "Black"

    def fake_truetype(path, size, index=0):
        if index >= len(families):
            raise OSError("no such face")
        return _Face(index)

    fonts._ttc_tc_index.cache_clear()
    monkeypatch.setattr(fonts.ImageFont, "truetype", fake_truetype)
    try:
        assert fonts._ttc_tc_index("/x/NotoSansCJK-Black.ttc") == 3
    finally:
        fonts._ttc_tc_index.cache_clear()


# ── generate_illustration ──


_PNG_BYTES = b"\x89PNG\r\n\x1a\nfake"


def _fake_post(status=200, payload=None, text=None, calls=None):
    def post(url, **kwargs):
        if calls is not None:
            calls.append((url, kwargs))
        req = httpx.Request("POST", url)
        if text is not None:
            return httpx.Response(status, text=text, request=req)
        return httpx.Response(status, json=payload, request=req)

    return post


def test_generate_illustration_success_and_request_shape(monkeypatch):
    calls: list = []
    payload = {"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode()}]}
    monkeypatch.setattr(illustration.httpx, "post", _fake_post(payload=payload, calls=calls))

    out = generate_illustration("A sailor reading a book on deck.", api_key="sk-test")

    assert out == _PNG_BYTES
    url, kwargs = calls[0]
    assert url == "https://api.openai.com/v1/images/edits"
    data = kwargs["data"]
    assert data["model"] == "chatgpt-image-latest"
    assert data["background"] == "transparent"
    assert data["size"] == "1024x1024"
    assert data["quality"] == "medium"
    assert data["output_format"] == "png"
    assert data["prompt"].startswith("A sailor reading a book on deck.")
    assert illustration.STYLE in data["prompt"]
    name, (filename, content, mime) = kwargs["files"][0]
    assert name == "image[]" and mime == "image/jpeg"
    assert content == illustration.STYLE_REF_PATH.read_bytes()
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"


def test_generate_illustration_reads_env_key(monkeypatch):
    calls: list = []
    payload = {"data": [{"b64_json": base64.b64encode(_PNG_BYTES).decode()}]}
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setattr(illustration.httpx, "post", _fake_post(payload=payload, calls=calls))
    assert generate_illustration("Topic.") == _PNG_BYTES
    assert calls[0][1]["headers"]["Authorization"] == "Bearer sk-env"


def test_generate_illustration_missing_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(illustration.httpx, "post", lambda *a, **k: pytest.fail("不該送出請求"))
    with pytest.raises(IllustrationError):
        generate_illustration("Topic.")


def test_generate_illustration_http_error(monkeypatch):
    body = '{"error": {"message": "' + "x" * 1000 + '"}}'
    monkeypatch.setattr(illustration.httpx, "post", _fake_post(status=400, text=body))
    with pytest.raises(IllustrationError) as exc:
        generate_illustration("Topic.", api_key="sk-secret")
    msg = str(exc.value)
    assert "400" in msg
    assert "sk-secret" not in msg
    assert len(msg) < 400


def test_generate_illustration_timeout(monkeypatch):
    def boom(url, **kwargs):
        raise httpx.ReadTimeout("slow", request=httpx.Request("POST", url))

    monkeypatch.setattr(illustration.httpx, "post", boom)
    with pytest.raises(IllustrationError):
        generate_illustration("Topic.", api_key="sk-test", timeout_s=1)


def test_generate_illustration_bad_payload(monkeypatch):
    monkeypatch.setattr(illustration.httpx, "post", _fake_post(payload={"data": []}))
    with pytest.raises(IllustrationError):
        generate_illustration("Topic.", api_key="sk-test")
