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
        assert stats == {"stripped": 1, "closed": 1, "true_silences": []}

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


class TestSrtRoundtrip:
    def test_parse_format_roundtrip(self):
        srt = "1\n00:00:00,031 --> 00:00:01,500\n第一句\n\n2\n00:00:01,500 --> 00:00:03,000\n第二句\n"
        cues = parse_srt_text(srt)
        assert cues == [(0.031, 1.5, "第一句"), (1.5, 3.0, "第二句")]
        assert parse_srt_text(format_srt(cues)) == cues
