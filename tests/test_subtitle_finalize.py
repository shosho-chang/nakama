"""字幕定版兩規則（修修 2026-08-05）：句尾零標點 + cue 間零空隙。"""

from shared.subtitle_finalize import (
    finalize_cues,
    format_srt,
    parse_srt_text,
    strip_tail_punct,
)


class TestStripTailPunct:
    def test_common_fullwidth(self):
        assert strip_tail_punct("微調平庸。") == "微調平庸"
        assert strip_tail_punct("等一下，") == "等一下"
        assert strip_tail_punct("為什麼？") == "為什麼"
        assert strip_tail_punct("好…") == "好"
        assert strip_tail_punct("走——") == "走"

    def test_halfwidth(self):
        assert strip_tail_punct("AI.") == "AI"
        assert strip_tail_punct("so,") == "so"

    def test_closer_kept_inner_punct_stripped(self):
        assert strip_tail_punct("「微調平庸。」") == "「微調平庸」"
        assert strip_tail_punct("叫《書名》。") == "叫《書名》"
        assert strip_tail_punct("《書名》") == "《書名》"

    def test_no_punct_unchanged(self):
        assert strip_tail_punct("你的指紋") == "你的指紋"

    def test_idempotent(self):
        for s in ("微調平庸。", "「微調平庸。」", "你的指紋"):
            once = strip_tail_punct(s)
            assert strip_tail_punct(once) == once


class TestFinalizeCues:
    def test_gap_closed_and_stripped(self):
        cues = [(0.0, 1.0, "第一句。"), (1.5, 2.0, "第二句")]
        out, stats = finalize_cues(cues)
        assert out[0] == (0.0, 1.5, "第一句")
        assert (stats["stripped"], stats["closed"], stats["true_silences"]) == (1, 1, [])

    def test_true_silence_not_closed(self):
        cues = [(0.0, 1.0, "a"), (5.5, 6.0, "b")]
        out, stats = finalize_cues(cues)
        assert out[0][1] == 1.0  # 4.5s > 3.0 → 不補
        assert stats["true_silences"] == [(1, 4.5)]

    def test_touching_untouched(self):
        cues = [(0.0, 1.0, "a"), (1.0, 2.0, "b")]
        out, stats = finalize_cues(cues)
        assert stats["closed"] == 0
        assert [c[:2] for c in out] == [(0.0, 1.0), (1.0, 2.0)]

    def test_multiline_stripped_per_line(self):
        out, _ = finalize_cues([(0.0, 1.0, "- 上一句，\n- 下一句。")])
        assert out[0][2] == "- 上一句\n- 下一句"

    def test_last_cue_end_kept(self):
        out, _ = finalize_cues([(0.0, 1.0, "a"), (1.2, 2.0, "b")])
        assert out[-1][1] == 2.0


class TestBadBoundaries:
    def test_word_split_across_cues_flagged(self):
        _, stats = finalize_cues([(0.0, 1.0, "這正是我們要練的判"), (1.0, 2.0, "斷力肌肉")])
        assert any("被切開" in f["reason"] for f in stats["bad_boundaries"])

    def test_sticky_tail_flagged(self):
        _, stats = finalize_cues([(0.0, 1.0, "是當我的"), (1.0, 2.0, "第一個讀者")])
        assert any("結尾" in f["reason"] for f in stats["bad_boundaries"])

    def test_sticky_head_flagged(self):
        _, stats = finalize_cues([(0.0, 1.0, "就是那個緊抓"), (1.0, 2.0, "著單一職涯不放的人")])
        assert any("開頭" in f["reason"] for f in stats["bad_boundaries"])

    def test_clean_boundary_not_flagged(self):
        _, stats = finalize_cues([(0.0, 1.0, "跌進了不少坑"), (1.0, 2.0, "但學到了超多東西")])
        assert stats["bad_boundaries"] == []


class TestSrtRoundtrip:
    def test_parse_format_roundtrip(self):
        srt = (
            "1\n00:00:00,031 --> 00:00:01,500\n第一句\n\n2\n00:00:01,500 --> 00:00:03,000\n第二句\n"
        )
        cues = parse_srt_text(srt)
        assert cues == [(0.031, 1.5, "第一句"), (1.5, 3.0, "第二句")]
        assert parse_srt_text(format_srt(cues)) == cues
