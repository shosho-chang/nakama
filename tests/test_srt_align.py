"""Unit tests for shared.srt_align — 聚焦 char-level retime 的新行為。

特別測：
- substring 碰撞不再讓多 cue 塌成同一時間（舊 bug）
- 時間單調遞增
- fuzzy 匹配與 fallback 內插
- AsrCharTimeline 建構（sentence_info / char-level timestamp 兩路）
"""

from __future__ import annotations

import pytest

from shared.srt_align import (
    Cue,
    _normalize_with_map,
    apply_linear,
    apply_shift,
    fit_linear,
    format_srt,
    parse_srt,
    retime_cues_from_asr,
)
from shared.transcriber import AsrCharTimeline, build_char_timeline

# ─── parse / format ───────────────────────────────────────────────────


def test_parse_srt_basic() -> None:
    srt = "1\n00:00:01,000 --> 00:00:02,500\nHello\n\n2\n00:00:03,000 --> 00:00:04,000\nWorld\n"
    cues = parse_srt(srt)
    assert len(cues) == 2
    assert cues[0].start_s == 1.0 and cues[0].end_s == 2.5
    assert cues[0].text == "Hello"
    assert cues[1].start_s == 3.0 and cues[1].text == "World"


def test_apply_shift_and_linear_symmetric() -> None:
    cues = [Cue(1, 1.0, 2.0, "a")]
    shifted = apply_shift(cues, 0.5)
    linear = apply_linear(cues, 1.0, 0.5)
    assert shifted[0].start_s == linear[0].start_s == 1.5


def test_format_parse_roundtrip() -> None:
    cues = [Cue(1, 0.0, 1.5, "你好"), Cue(2, 1.5, 3.0, "世界")]
    roundtrip = parse_srt(format_srt(cues))
    assert [(c.start_s, c.end_s, c.text) for c in roundtrip] == [
        (0.0, 1.5, "你好"),
        (1.5, 3.0, "世界"),
    ]


# ─── normalize with map ──────────────────────────────────────────────


def test_normalize_strips_punctuation_and_maps_indices() -> None:
    norm, idx_map = _normalize_with_map("你好，世界！Hello")
    assert norm == "你好世界hello"
    # orig text[idx_map[i]] 對應 norm[i]（忽略大小寫）
    for i, ch in enumerate(norm):
        assert "你好，世界！Hello"[idx_map[i]].lower() == ch


def test_normalize_strips_html_tags() -> None:
    norm, idx_map = _normalize_with_map("<b>你好</b>")
    assert norm == "你好"
    # idx_map should point to 你 (index 3) and 好 (index 4)
    assert "<b>你好</b>"[idx_map[0]] == "你"
    assert "<b>你好</b>"[idx_map[1]] == "好"


# ─── AsrCharTimeline ─────────────────────────────────────────────────


def test_timeline_from_sentence_info_distributes_proportionally() -> None:
    results = [
        {
            "sentence_info": [
                {"text": "你好世界", "start": 1000, "end": 3000},
                {"text": "再見", "start": 3500, "end": 4500},
            ]
        }
    ]
    tl = build_char_timeline(results)
    assert tl.text == "你好世界再見"
    # 第一句 2000ms 均分 4 字 → 500ms each
    assert tl.char_times[0] == (1000, 1500)
    assert tl.char_times[3] == (2500, 3000)
    # 第二句從 3500 開始
    assert tl.char_times[4][0] == 3500


def test_timeline_from_char_level_timestamp() -> None:
    results = [
        {
            "text": "你好，世界",
            "timestamp": [[1000, 1500], [1500, 2000], [2000, 2500], [2500, 3000]],
        }
    ]
    tl = build_char_timeline(results)
    assert tl.text == "你好，世界"
    # 逗號不發音，繼承前一字 end
    assert tl.char_times[2] == (2000, 2000)
    # 發音字對上 timestamp
    assert tl.char_times[0] == (1000, 1500)
    assert tl.char_times[4] == (2500, 3000)


def test_timeline_length_matches_text() -> None:
    results = [{"sentence_info": [{"text": "一二三", "start": 0, "end": 300}]}]
    tl = build_char_timeline(results)
    assert len(tl.char_times) == len(tl.text)


def test_timeline_construction_rejects_mismatched_lengths() -> None:
    with pytest.raises(ValueError):
        AsrCharTimeline(text="abc", char_times=[(0, 1)])


# ─── retime_cues_from_asr ────────────────────────────────────────────


def _uniform_timeline(text: str, duration_ms: int) -> AsrCharTimeline:
    """等時間寬度的測試用 timeline（每字均分）。"""
    n = len(text)
    return AsrCharTimeline(
        text=text,
        char_times=[(int(duration_ms * i / n), int(duration_ms * (i + 1) / n)) for i in range(n)],
    )


def test_retime_splits_collision_cues_to_distinct_times() -> None:
    """舊版 bug 重現：多個短 cue 都 substring-match 一個 ASR 句，
    舊演算法會讓它們全部塌到 ASR 句起點、產生零時長。新演算法應該分開。"""
    timeline = _uniform_timeline("我記得第一次見面是在你們家", 5000)
    cues = [
        Cue(1, 0.5, 1.5, "我記得"),
        Cue(2, 1.5, 2.5, "第一次見面"),
        Cue(3, 2.5, 3.5, "是在你們家"),
    ]
    new_cues, matches, stats = retime_cues_from_asr(cues, timeline, ratio_threshold=0.5)

    assert stats.matched == 3
    # 三個 cue 起點應互不相同
    starts = [round(c.start_s, 3) for c in new_cues]
    assert len(set(starts)) == 3, f"塌到同起點：{starts}"
    # 單調遞增
    assert starts == sorted(starts)
    # 沒零時長
    for c in new_cues:
        assert c.end_s > c.start_s


def test_retime_enforces_monotonicity_even_when_matches_disorder() -> None:
    """造一個「後面 cue 先匹配到早的字」的場景，
    演算法應強制單調而非倒退。"""
    # ASR 文字裡「A」出現兩次，時間各異；hand cue 順序期望後者對應晚的「A」
    timeline = AsrCharTimeline(
        text="你Axxxx我Ayyyy",
        char_times=[
            (0, 100),
            (100, 200),
            (200, 300),
            (300, 400),
            (400, 500),
            (500, 600),
            (600, 700),
            (700, 800),
            (800, 900),
            (900, 1000),
            (1000, 1100),
            (1100, 1200),
        ],
    )
    cues = [
        Cue(1, 0.0, 0.5, "你A"),
        Cue(2, 0.5, 1.0, "我A"),
    ]
    new_cues, _, _ = retime_cues_from_asr(cues, timeline, ratio_threshold=0.5)
    assert new_cues[0].start_s < new_cues[1].start_s
    assert new_cues[0].end_s <= new_cues[1].start_s + 0.001


def test_retime_fuzzy_matches_when_text_imperfect() -> None:
    """Hand cue 跟 ASR 有小幅文字差異（例：錯字），仍能匹配。"""
    timeline = _uniform_timeline("我想吃蘋果派", 3000)
    cues = [Cue(1, 0.5, 1.5, "想吃蘋")]  # substring match should find it
    new_cues, matches, _ = retime_cues_from_asr(cues, timeline, ratio_threshold=0.5)
    assert len(matches) == 1
    assert matches[0].ratio == 1.0


def test_retime_interpolates_unmatched_between_anchors() -> None:
    """沒匹配到的 cue 走前後鄰居線性內插。"""
    timeline = _uniform_timeline("我記得第一次見面是在你們家那時候很開心", 10000)
    cues = [
        Cue(1, 0.0, 1.0, "我記得"),  # match
        Cue(2, 1.0, 2.0, "zzzzzz"),  # no match
        Cue(3, 2.0, 3.0, "你們家"),  # match
    ]
    new_cues, _, stats = retime_cues_from_asr(cues, timeline, ratio_threshold=0.5)
    assert stats.matched == 2
    assert stats.interpolated == 1
    # #2 應在 #1 與 #3 之間
    assert new_cues[0].start_s < new_cues[1].start_s < new_cues[2].start_s


def test_retime_picks_closest_occurrence_when_phrase_repeats() -> None:
    """常見短語在 ASR window 內多處出現時，該挑離 guess time 最近的那個。
    舊演算法用 .find() 取最左匹配，可能挑到 window 內較早的同字。"""
    # 「你知道」在 5s（char 0-2）與 15s（char ~30-32）各出現一次
    text = "你知道abc" + "x" * 30 + "你知道xyz"  # total ~40 chars
    duration_ms = 20_000  # 20 秒 → 每字 ~500ms
    n = len(text)
    char_times = [(int(duration_ms * i / n), int(duration_ms * (i + 1) / n)) for i in range(n)]
    timeline = AsrCharTimeline(text=text, char_times=char_times)

    # guess 在 13s 附近，window ±10s 應該涵蓋兩個出現，挑第二個
    cues = [Cue(1, 13.0, 14.0, "你知道")]
    new_cues, matches, _ = retime_cues_from_asr(
        cues,
        timeline,
        window_s=20.0,  # 兩個 occurrence 都在 window 內
        ratio_threshold=0.5,
        max_offset_deviation_s=0,  # 關閉 outlier filter 免影響單 match
    )

    assert len(matches) == 1
    # 第二個出現在 ~16-17s（chars 33-35 的 time ≈ 33*500ms=16500ms）
    assert 12.0 < new_cues[0].start_s < 20.0, (
        f"expected later occurrence close to 13s guess, got {new_cues[0].start_s:.2f}s"
    )


def test_retime_filters_offset_outliers() -> None:
    """若某 cue 的 match offset 遠離其他 cue 的 median offset，應丟棄該 match、走內插。
    場景：一個短詞「對」在時間軸兩處（5s 與 60s）出現，hand 在 7s 標這個「對」，
    其他 cue 的 median offset 約 +2s，但「對」配到 60s 的話 offset +53s → outlier。"""
    text = "你好我想說對吃飯喝茶" + "零一二三四五" * 8 + "對啦完成"
    duration_ms = 120_000
    n = len(text)
    char_times = [(int(duration_ms * i / n), int(duration_ms * (i + 1) / n)) for i in range(n)]
    timeline = AsrCharTimeline(text=text, char_times=char_times)

    # 6 個 cue 均勻對到 ASR 開頭前 10 字（offset median ~ +2s）
    cues = [
        Cue(1, 0.0, 1.0, "你好"),
        Cue(2, 1.0, 2.0, "我想"),
        Cue(3, 2.0, 3.0, "想說"),
        # 這個「對」在 window ±15s 內應該在 5s 位置
        Cue(4, 3.0, 4.0, "對"),
        Cue(5, 4.0, 5.0, "吃飯"),
        Cue(6, 5.0, 6.0, "喝茶"),
    ]
    new_cues, matches, stats = retime_cues_from_asr(
        cues, timeline, window_s=15.0, ratio_threshold=0.5, min_cue_chars=1
    )
    # #4「對」應該對到附近的對（~5s 位置），不是遠處那個
    assert 4.0 < new_cues[3].start_s < 10.0, f"got {new_cues[3].start_s:.2f}s"


def test_retime_raises_on_legacy_arg_shape() -> None:
    """防禦：傳入舊 list[AsrSegment] 時該報錯指路。"""
    from shared.srt_align import AsrSegment

    with pytest.raises(TypeError, match="run_asr_char_timeline"):
        retime_cues_from_asr([Cue(1, 0.0, 1.0, "x")], [AsrSegment(0, 1, "x")])


def test_retime_empty_cues() -> None:
    timeline = _uniform_timeline("abcd", 1000)
    new_cues, matches, stats = retime_cues_from_asr([], timeline)
    assert new_cues == []
    assert matches == []
    assert stats.total == 0


def test_retime_preserves_cue_count_and_text() -> None:
    """新時間套回去但 cue 數量和 text 不變。"""
    timeline = _uniform_timeline("我想吃飯喝茶聊天休息", 10000)
    cues = [
        Cue(1, 0.0, 1.0, "我想"),
        Cue(2, 1.0, 2.0, "吃飯"),
        Cue(3, 2.0, 3.0, "喝茶"),
        Cue(4, 3.0, 4.0, "聊天"),
        Cue(5, 4.0, 5.0, "休息"),
    ]
    new_cues, _, _ = retime_cues_from_asr(cues, timeline, ratio_threshold=0.5)
    assert len(new_cues) == 5
    assert [c.text for c in new_cues] == [c.text for c in cues]


# ─── fit_linear ─────────────────────────────────────────────────────


def test_fit_linear_pure_shift() -> None:
    from shared.srt_align import AsrSegment, Match

    matches = [
        Match(
            cue=Cue(i, float(i), float(i + 1), "x"),
            asr=AsrSegment(float(i) + 0.5, float(i + 1) + 0.5, "x"),
            ratio=1.0,
        )
        for i in range(5)
    ]
    fit = fit_linear(matches)
    assert abs(fit.a - 1.0) < 1e-6
    assert abs(fit.b - 0.5) < 1e-6
    assert fit.is_pure_shift
