"""run_short_broll 純函式測試（Resolve 疊軌部分靠 --stills 樣張驗證）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from run_short_broll import _data_uri, _fill_zoom  # noqa: E402


class TestFillZoom:
    def test_landscape_4k_fills_vertical(self):
        # 3840x2160 fit 進 1080x1920 是貼寬（1080x607.5）→ 補到 1920 高
        assert _fill_zoom("3840x2160") == pytest.approx(1920 / 607.5, rel=1e-3)

    def test_native_vertical_same_aspect_is_one(self):
        assert _fill_zoom("2160x3840") == pytest.approx(1.0, rel=1e-6)
        assert _fill_zoom("1080x1920") == pytest.approx(1.0, rel=1e-6)

    def test_taller_aspect_needs_width_fill(self):
        # 2160x4096 比 9:16 更瘦長 → fit 貼高後寬不足，補寬
        z = _fill_zoom("2160x4096")
        assert z == pytest.approx(1080 / (2160 * (1920 / 4096)), rel=1e-3)
        assert z > 1.0

    def test_garbage_resolution_falls_back_to_one(self):
        assert _fill_zoom("") == 1.0
        assert _fill_zoom(None) == 1.0
        assert _fill_zoom("weird") == 1.0


class TestDataUri:
    def test_png_mime_and_base64(self, tmp_path):
        p = tmp_path / "x.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")
        uri = _data_uri(p)
        assert uri.startswith("data:image/png;base64,")

    def test_jpg_mime(self, tmp_path):
        p = tmp_path / "x.jpg"
        p.write_bytes(b"\xff\xd8\xff")
        assert _data_uri(p).startswith("data:image/jpeg;base64,")
