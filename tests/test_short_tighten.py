"""run_short_tighten 純函數測試（短片 jump-cut 緊湊化）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_short_tighten import _keep_segments, _strip_cut_word


def _cut(t0, t1, keep=True):
    return {"t0": t0, "t1": t1, "keep": keep}


def test_keep_segments_basic_complement():
    segs = _keep_segments(0.0, 10.0, [_cut(3.0, 4.0), _cut(7.0, 7.5)])
    assert segs == [(0.0, 3.0), (4.0, 7.0), (7.5, 10.0)]


def test_keep_segments_cut_at_clip_start():
    # 開頭口吃「那、那」整刀（punch-S1 案例）：不留 0 長度開頭段
    segs = _keep_segments(625.188, 692.508, [_cut(625.188, 626.129)])
    assert segs[0] == (626.129, 692.508)
    assert len(segs) == 1


def test_keep_segments_short_island_absorbed():
    # 兩刀之間 0.085s 孤島（pause 刀 + filler 刀相鄰）→ 併成一刀，不閃屏
    segs = _keep_segments(0.0, 10.0, [_cut(2.0, 2.5), _cut(2.585, 3.3)])
    assert segs == [(0.0, 2.0), (3.3, 10.0)]


def test_keep_segments_ignores_unkept_and_null():
    segs = _keep_segments(0.0, 10.0, [_cut(3.0, 4.0, keep=False), _cut(5.0, 6.0, keep=None)])
    assert segs == [(0.0, 10.0)]


def test_strip_cut_word_leading():
    # 「那 現在的這個時代」開頭贅詞 → 移除 + 空格收乾淨
    assert _strip_cut_word("那 現在的這個時代喔", "那", 0.0) == "現在的這個時代喔"


def test_strip_cut_word_picks_nearest_occurrence():
    # 文字有兩個「那」，剪點在後段 → 移除後面那個
    text = "那一旦習慣了 那它其實"
    assert _strip_cut_word(text, "那", 0.9) == "那一旦習慣了 它其實"
    assert _strip_cut_word(text, "那", 0.0) == "一旦習慣了 那它其實"


def test_strip_cut_word_absent_is_noop():
    assert _strip_cut_word("完全無關的句子", "那", 0.5) == "完全無關的句子"


# --- 字幕細切（5–9 字呼吸單元） ---

from run_short_tighten import _FINE_MAX, _fine_spans, _fine_units  # noqa: E402


def _mkwords(s, e, text):
    """均勻鋪詞（每字一詞）供時間對齊。"""
    step = (e - s) / len(text)
    return [
        {"word": ch, "start": s + i * step, "end": s + (i + 1) * step} for i, ch in enumerate(text)
    ]


def test_fine_spans_split_on_spaces():
    text = "非常短效的「多巴胺」的獎勵 就是它可能"
    spans = _fine_spans(text)
    parts = [text[a:b] for a, b in spans]
    assert all(len(p) <= _FINE_MAX + 4 for p in parts)  # 括號保護/短行合併容忍 +4
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")


def test_fine_spans_no_split_inside_brackets():
    text = "有一點「數位排毒」的概念在裡面喔"
    for a, b in _fine_spans(text):
        seg = text[a:b]
        assert seg.count("「") == seg.count("」")  # 括號不被拆開


def test_fine_spans_short_text_untouched():
    assert _fine_spans("對就是") == [(0, 3)]


def test_fine_units_timing_monotone():
    text = "被社群媒體公司來綁架我們的大腦跟注意力"
    words = _mkwords(632.0, 636.0, text)
    units = _fine_units(632.0, 636.0, text, words)
    assert len(units) >= 2
    assert units[0][0] == 632.0 and units[-1][1] == 636.0
    for (s1, e1, _), (s2, e2, _) in zip(units, units[1:]):
        assert e1 == s2  # 單元首尾相接不閃屏
        assert e1 > s1
    assert "".join(t for _, _, t in units).replace(" ", "") == text.replace(" ", "")


def test_fine_units_fallback_proportional():
    # 詞級資料空 → 字數比例分配，仍要覆蓋整段
    text = "第一個部分 第二個部分 第三個部分"
    units = _fine_units(10.0, 16.0, text, [])
    assert len(units) == 3
    assert units[0][0] == 10.0 and units[-1][1] == 16.0


def test_fine_spans_never_severs_words():
    # 2026-07-26 回歸：字數切割把雙字詞攔腰砍（產品/短效/長期/改變/聯考/我們）
    cases = [
        ("這種 比較科技化的產品它都會提供", "產品"),
        ("非常短效的「多巴胺」的獎勵", "短效"),
        ("那一旦我們長期習慣了短 小", "長期"),
        ("原則上沒有改變我們注意力的本質啦", "改變"),
        ("只是它會改變你的耐心", "改變"),
        ("我們去這個聯考可能要準備個十二三年", "聯考"),
    ]
    for text, word in cases:
        parts = [text[a:b] for a, b in _fine_spans(text)]
        assert any(word in p for p in parts), f"{word} 被切斷: {parts}"


def test_fine_spans_hard_limit_10():
    # 修修 2026-07-26 十輪：中文 10 字 hard limit——實砍案例「效果量」句
    from run_short_tighten import _disp_len

    for text in (
        "就發現這個「效果量」其實不是很大",
        "有一點「數位排毒」的概念在裡面喔",
        "培養自覺的這個「後設認知」能力",
    ):
        parts = [text[a:b] for a, b in _fine_spans(text)]
        assert all(_disp_len(p) <= 10 for p in parts), parts
        assert "".join(parts).replace(" ", "") == text.replace(" ", "")


def test_fine_spans_number_classifier_glued():
    # 修修十一輪：「禁掉就好 16」「歲以前就要禁掉」——16 與 歲 絕不可分行
    text = "禁掉就好 16歲以前就要禁掉 所以"
    parts = [text[a:b] for a, b in _fine_spans(text)]
    assert any("16歲" in p for p in parts), parts


def test_merge_blocks_joins_continuous_cues():
    from run_short_tighten import _merge_blocks

    cues = [
        (10.0, 11.5, "禁掉就好 16"),
        (11.5, 13.0, "歲以前就要禁掉 所以"),
        (14.0, 15.0, "下一段"),
    ]
    groups = _merge_blocks(cues, [(0.0, 20.0)])
    assert [len(g) for g in groups] == [2, 1]  # 前兩 cue 同群、末 cue 獨立
    assert "".join(x[2] for x in groups[0]) == "禁掉就好 16歲以前就要禁掉 所以"


def test_merge_blocks_uses_collapsed_time():
    from run_short_tighten import _merge_blocks

    # 兩 cue 源時間差 1.0s，但中間全是被剪掉的停頓（不在保留段）——
    # 成品音軌連續，必須併（16|歲 實案）
    segs = [(10.0, 11.5), (12.5, 15.0)]
    cues = [(10.0, 11.5, "禁掉就好 16"), (12.5, 14.0, "歲以前就要禁掉")]
    groups = _merge_blocks(cues, segs)
    assert len(groups) == 1 and len(groups[0]) == 2
    assert "".join(x[2] for x in groups[0]) == "禁掉就好 16歲以前就要禁掉"


class TestMinCueDuration:
    """一行最短顯示 0.8s（二十四輪盲審：0.16s 閃現 cue 讀不到）。"""

    def test_extends_when_gap_available(self):
        from run_short_tighten import _enforce_min_duration

        out = _enforce_min_duration([(0.0, 0.16, "容易確定")], 5.0)
        assert out == [(0.0, 0.8, "容易確定")]

    def test_merges_with_neighbour_when_within_hard_limit(self):
        from run_short_tighten import _enforce_min_duration

        # 合併後 ≤10 字才可併（修修的 hard limit 不可破）
        units = [(0.0, 1.0, "他就想說"), (1.0, 1.16, "沒事了")]
        out = _enforce_min_duration(units, 1.16)
        assert len(out) == 1
        assert out[0][2] == "他就想說沒事了"

    def test_does_not_merge_past_hard_limit(self):
        from run_short_tighten import _enforce_min_duration

        # 合併會變 11 字 → 不可併，只能盡量延長
        units = [(0.0, 1.0, "因果關係其實不"), (1.0, 1.16, "容易確定")]
        out = _enforce_min_duration(units, 1.16)
        assert len(out) == 2

    def test_balance_pass_avoids_short_orphan(self):
        """病根修正：平衡切點後就不會產生 0.16s 的孤兒行。"""
        from run_short_tighten import _fine_spans

        s = "因果關係其實不容易確定"
        assert [s[a:b] for a, b in _fine_spans(s)] == ["因果關係其實", "不容易確定"]

    def test_keeps_long_units_untouched(self):
        from run_short_tighten import _enforce_min_duration

        units = [(0.0, 1.2, "第一行"), (1.2, 2.5, "第二行")]
        assert _enforce_min_duration(units, 2.5) == units

    def test_never_exceeds_next_start(self):
        from run_short_tighten import _enforce_min_duration

        # 下一行 0.5s 就開始且合併會爆行寬 → 只能延長到 0.5，不可蓋過去
        units = [(0.0, 0.2, "短"), (0.5, 2.0, "這是一個很長的下一行文字")]
        out = _enforce_min_duration(units, 2.0)
        assert out[0][1] <= 0.5 + 1e-6
