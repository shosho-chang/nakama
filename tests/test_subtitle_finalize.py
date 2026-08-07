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
        # 詞典真詞被攔腰切開（繁體詞庫下「社群」「商學院」都在庫裡）
        _, stats = finalize_cues([(0.0, 1.0, "進入這個社"), (1.0, 2.0, "群會完全改觀")])
        assert any("被切開" in f["reason"] for f in stats["bad_boundaries"])

    def test_list_rhythm_boundary_not_flagged(self):
        # 個人層面｜商業層面＝並列句的合法節奏點（掛繁體詞庫後不再誤報成「面商」）
        _, stats = finalize_cues([(0.0, 1.0, "課程會從個人層面"), (1.0, 2.0, "商業層面來看")])
        assert not any("被切開" in f["reason"] for f in stats["bad_boundaries"])

    def test_hmm_fabricated_word_not_flagged(self):
        # HMM 對拼接串會發明「東西現」——繁體詞庫 + HMM=False 不可誤報
        _, stats = finalize_cues([(0.0, 1.0, "但學到了超多東西"), (1.0, 2.0, "現在我真的有股衝動")])
        assert not any("被切開" in f["reason"] for f in stats["bad_boundaries"])

    def test_traditional_compound_word_split_flagged(self):
        # 繁體詞庫掛上前，「判斷力」被當成抓不到的已知限制（安吉集實測）
        _, stats = finalize_cues([(0.0, 1.0, "我們要練的判斷"), (1.0, 2.0, "力肌肉就越強")])
        assert any("被切開" in f["reason"] for f in stats["bad_boundaries"])


class TestPronounSubjectSplit:
    """修修 2026-08-06 安吉集裁決：代名詞被切在上一句尾、其實是下句主語。"""

    def test_pronoun_then_verb_flagged(self):
        cues = [(0.0, 1.0, "就跟保羅說你不用想這麼多 我"), (1.0, 2.0, "覺得進入這個社群")]
        _, stats = finalize_cues(cues)
        assert any("代名詞" in f["reason"] for f in stats["bad_boundaries"])

    def test_pronoun_then_adverb_flagged(self):
        cues = [(0.0, 1.0, "還是要定居 然後我"), (1.0, 2.0, "就跟保羅說")]
        _, stats = finalize_cues(cues)
        assert any("代名詞" in f["reason"] for f in stats["bad_boundaries"])

    def test_plural_pronoun_flagged(self):
        cues = [(0.0, 1.0, "之後需要做什麼 我們"), (1.0, 2.0, "要繼續旅遊嗎")]
        _, stats = finalize_cues(cues)
        assert any("代名詞" in f["reason"] for f in stats["bad_boundaries"])

    def test_complement_verb_tail_flagged(self):
        # 「我覺得」收尾＝賓語子句被切到下一句（安吉集 1:43 實例）
        cues = [(0.0, 1.0, "你不用想這麼多我覺得"), (1.0, 2.0, "進入這個社群會改觀")]
        _, stats = finalize_cues(cues)
        assert any("賓語被切到次句" in f["reason"] for f in stats["bad_boundaries"])

    def test_modal_tail_flagged(self):
        # 助動詞/連接副詞收尾：主要動詞在下一句（我們要｜繼續、怎麼｜做）
        for tail, head in [("之後需要做什麼我們要", "繼續旅遊嗎"), ("我應該怎麼", "做 Podcast")]:
            _, stats = finalize_cues([(0.0, 1.0, tail), (1.0, 2.0, head)])
            assert any("助動詞" in f["reason"] for f in stats["bad_boundaries"]), tail

    def test_complete_word_ending_in_modal_char_not_flagged(self):
        # 詞級判定：「可能」「機會」「需要」以同字收尾但是完整詞，不可誤報
        for tail in ("到當地可能", "這是一個機會", "我覺得不需要"):
            _, stats = finalize_cues([(0.0, 1.0, tail), (1.0, 2.0, "後來就沒有了")])
            assert not any("助動詞" in f["reason"] for f in stats["bad_boundaries"]), tail

    def test_pronoun_as_object_not_flagged(self):
        # 代名詞當受詞、次句以名詞另起主語 → 合法斷點，不可誤報
        cues = [(0.0, 1.0, "這件事情是他告訴我"), (1.0, 2.0, "老闆說沒有問題")]
        _, stats = finalize_cues(cues)
        assert not any("代名詞" in f["reason"] for f in stats["bad_boundaries"])

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
