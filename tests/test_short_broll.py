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


# ── 長片格式（修修 2026-08-03 長片線）─────────────────────────────────────


class TestLongFormat:
    def test_canvas_and_comp_suffix(self):
        from run_short_broll import FORMAT_BROLL

        assert tuple(FORMAT_BROLL["short"]["canvas"]) == (1080, 1920)
        assert FORMAT_BROLL["short"]["comp_suffix"] == ""  # 短片走原 composition
        assert tuple(FORMAT_BROLL["long"]["canvas"]) == (1920, 1080)
        assert FORMAT_BROLL["long"]["comp_suffix"] == "_wide"

    def test_landscape_source_needs_no_crop_in_wide_canvas(self):
        # 3840x2160 進 16:9 畫布 = 同長寬比，zoom 1.0（不裁）
        assert _fill_zoom("3840x2160", 1.0, (1920, 1080)) == pytest.approx(1.0, rel=1e-6)

    def test_vertical_source_in_wide_canvas_crops_hard(self):
        """直式素材進 16:9 = 只看得到中央橫帶——不是壞掉，是必須逐支驗樣張。"""
        z = _fill_zoom("2160x3840", 1.0, (1920, 1080))
        # fit 後是 607.5×1080，要填滿 1920 寬 → 1920/607.5 ≈ 3.16
        assert z == pytest.approx(1920 / 607.5, rel=1e-3)
        assert 1 / z == pytest.approx(0.316, abs=0.01)  # 只剩約 32% 的源高度可見
        # 2026-08-04 實測：特寫類（手/手機）裁完仍成立，全身鏡頭會變無頭軀幹

    def test_wide_compositions_declare_16_9_canvas(self):
        """*_wide.html 的 data-width/height 是 hyperframes 的輸出解析度來源，
        JS 改不動——寫錯會渲出直式卡片疊到 16:9 timeline 上。"""
        root = Path(__file__).resolve().parent.parent / "video" / "compositions"
        for comp in ("punch_card", "sticker_pair", "concept_card", "chapter_label",
                     "transition_title"):
            html = (root / comp / "compositions" / f"{comp}_wide.html").read_text(encoding="utf-8")
            assert 'data-width="1920"' in html, comp
            assert 'data-height="1080"' in html, comp

    def test_transition_title_paper_needs_texture_composite(self):
        """B2 定版：paper 系轉場卡是透明字卡，必須疊紙紋 motion bg 才滿版——
        漏合成就回到「黑字裸壓實拍」的壓臉 bug（修修五輪）。scrim 自帶底。"""
        from run_short_broll import COMP_MAX_SEC, PAPER_TEXTURE

        assert PAPER_TEXTURE == "paper-texture.mp4"
        # data-duration 4s：3.0s 上軌 + 退場收在 show_sec 內
        assert COMP_MAX_SEC["transition_title"] == 4.0
        root = Path(__file__).resolve().parent.parent / "video" / "compositions"
        html = (root / "transition_title" / "compositions" / "transition_title_wide.html").read_text(
            encoding="utf-8"
        )
        # 滿版底是「元素」不是 body 背景——body 背景在 alpha 渲染下會被丟掉
        assert 'background: transparent' in html
        assert 'id="scrim"' in html
        # 退場動畫存在（原生 transition_title 註解「硬切」已廢）
        assert "yPercent: -112" in html

    def test_wide_compositions_avoid_gsap_transform_double_apply(self):
        """CSS transform + GSAP xPercent/yPercent 會疊加（sticker_pair 二十四輪
        血案；concept_card 直式版至今仍中招）。wide 版一律不寫 CSS transform
        在被 GSAP 動到的元素上。"""
        root = Path(__file__).resolve().parent.parent / "video" / "compositions"
        html = (root / "concept_card" / "compositions" / "concept_card_wide.html").read_text(
            encoding="utf-8"
        )
        card_block = html.split("#card {")[1].split("}")[0]
        assert "transform:" not in card_block
        assert "xPercent: -50" in html  # 水平置中改由 GSAP 負責


class TestContentGaps:
    """素材真空偵測（修修 2026-08-04：「情緒是建構的那段太空，程式應該偵測」）。
    既有 gap 偵測把 cut 算進事件——長片導播每幾秒換鏡，14s 門檻永遠不觸發，
    164 秒無素材照樣回 gaps=[]。content_gaps 只掃強事件。"""

    def _scan(self, events, dur, srt=(), threshold=75.0):
        from run_short_review import _scan_content_gaps

        return _scan_content_gaps(events, dur, list(srt), threshold)

    def test_cuts_do_not_fill_content_gap(self):
        # 0-10s 有素材，之後 170 秒只有換鏡——cut 是弱事件，必須報真空
        events = [{"type": "video", "slug": "stock", "t0": 5.0, "t1": 10.0}] + [
            {"type": "cut", "slug": "", "t0": float(t), "t1": float(t)}
            for t in range(12, 180, 6)
        ]
        gaps = self._scan(events, 180.0)
        assert len(gaps) == 1
        assert gaps[0]["from"] == 10.0 and gaps[0]["to"] == 180.0

    def test_dense_strong_events_report_clean(self):
        events = [
            {"type": "concept", "slug": "tr", "t0": float(t), "t1": float(t + 3)}
            for t in range(0, 300, 60)
        ]
        assert self._scan(events, 300.0) == []

    def test_transcript_attached_to_gap_window(self):
        events = [{"type": "video", "slug": "s", "t0": 0.0, "t1": 5.0}]
        srt = [
            {"t0": 2.0, "t1": 4.0, "text": "素材期間的話"},
            {"t0": 50.0, "t1": 53.0, "text": "真空裡的話"},
        ]
        gaps = self._scan(events, 120.0, srt)
        assert len(gaps) == 1
        assert "真空裡的話" in gaps[0]["transcript"]
        assert "素材期間的話" not in gaps[0]["transcript"]

    def test_punch_zoom_is_not_strong_event(self):
        events = [
            {"type": "punch-ramp", "slug": "", "t0": float(t), "t1": float(t + 2)}
            for t in range(0, 200, 20)
        ]
        gaps = self._scan(events, 200.0)
        assert len(gaps) == 1  # punch zoom 同機位縮放，撐不起素材真空


def test_sfx_chapter_label_maps_to_swish(tmp_path):
    """長片證據驅動語彙：章節籤=swish（導航）、概念卡=pop（重點）、hero=ding。"""
    import json as _json
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from run_short_sfx import build_cues

    td = tmp_path / "highlights" / "tighten"
    td.mkdir(parents=True)
    (td / "x_titles.json").write_text(
        _json.dumps({"titles": [{"text": "把主權\n拿回來", "t0": 10.0, "t1": 12.8, "tier": 1}]}),
        encoding="utf-8",
    )
    (td / "x_broll.json").write_text(
        _json.dumps(
            {
                "items": [
                    {"t0": 20.0, "t1": 23.0, "kind": "concept", "comp": "chapter_label",
                     "vars": {"label": "睡眠"}},
                    {"t0": 30.0, "t1": 33.0, "kind": "concept",
                     "vars": {"title": "相關 ≠ 因果"}},
                ]
            }
        ),
        encoding="utf-8",
    )
    cues = build_cues(tmp_path, "x")
    by_t = {c["t"]: c["sfx"] for c in cues}
    assert by_t[10.0] == "ding"  # hero
    assert by_t[20.0] == "swish"  # 章節籤：導航記號，輕掃
    assert by_t[30.0] == "pop"  # 概念卡維持原映射
