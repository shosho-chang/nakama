"""shared/cue_builder.py 測試 — 字級時間戳建 cue（jieba 詞邊界 + 停頓優先）。"""

from __future__ import annotations

from shared.cue_builder import aligned_segments_to_srt, segment_to_cues


def _words(
    text: str, *, start: float = 0.0, dur: float = 0.2, gaps: dict[int, float] | None = None
):
    """把字串轉成字級 word dicts；gaps[i] = 第 i 字之後插入的停頓秒數。"""
    words = []
    t = start
    for i, ch in enumerate(text):
        words.append({"word": ch, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur
        if gaps and i in gaps:
            t += gaps[i]
    return words


def test_pause_is_priority_break():
    # 「所以今天是講什麼書」＋大停頓＋「今天是講那個」→ 停頓處必斷
    text = "所以今天是講什麼書今天是講那個"
    words = _words(text, gaps={8: 1.0})  # 「書」之後停 1 秒
    cues = segment_to_cues(words)
    assert cues[0][2] == "所以今天是講什麼書"
    assert cues[1][2].startswith("今天")
    # 時間戳為真實值：第二個 cue 從停頓後開始
    assert cues[1][0] - cues[0][1] >= 1.0 - 1e-6


def test_never_splits_inside_jieba_word():
    # 連續無停頓長句：任何切點都不可把「請教」「哪裡」這類詞切成兩半
    text = "新書之前想要先請教老師對於大腦的興趣是從哪裡開始的呢"
    cues = segment_to_cues(_words(text))
    assert len(cues) >= 2  # 超過 14 字必有切分
    for a, b in zip(cues, cues[1:]):
        boundary = a[2][-1] + b[2][0]
        assert boundary not in {"請教", "哪裡", "興趣", "老師", "開始"}


def test_soft_cap_respected_without_pause():
    text = "一二三四五六七八九十甲乙丙丁戊己庚辛壬癸子丑寅卯"
    cues = segment_to_cues(_words(text))
    assert all(len(c[2].replace(" ", "")) <= 22 for c in cues)  # 硬上限
    assert len(cues) >= 2


def test_ascii_word_kept_whole_with_spacing():
    words = [
        {"word": "跟", "start": 0.0, "end": 0.2},
        {"word": "Paul", "start": 0.2, "end": 0.6},
        {"word": "有", "start": 0.6, "end": 0.8},
        {"word": "一", "start": 0.8, "end": 1.0},
        {"word": "個", "start": 1.0, "end": 1.2},
    ]
    cues = segment_to_cues(words)
    assert cues[0][2] == "跟 Paul 有一個"


def test_real_timestamps_no_interpolation():
    # cue 邊界時間 = 首字 start / 末字 end
    text = "早安大家好我們開始今天的節目吧各位"
    words = _words(text, start=10.0, gaps={4: 0.8})
    cues = segment_to_cues(words)
    assert cues[0][0] == words[0]["start"]
    assert cues[0][1] == words[4]["end"]
    assert cues[1][0] == words[5]["start"]


def test_skips_tokens_without_timestamps():
    words = [
        {"word": "好", "start": 0.0, "end": 0.2},
        {"word": "壞", "start": None, "end": None},
        {"word": "了", "start": 0.4, "end": 0.6},
    ]
    cues = segment_to_cues(words)
    assert cues[0][2] == "好了"


def test_aligned_segments_to_srt_format():
    segs = [
        {"words": _words("大家早安", start=0.0)},
        {"words": _words("我們開始吧", start=5.0)},
    ]
    srt = aligned_segments_to_srt(segs)
    assert "1\n00:00:00,000 --> " in srt
    assert "大家早安" in srt
    assert "00:00:05,000 --> " in srt
    assert srt.count("-->") == 2


def test_empty_segment():
    assert segment_to_cues([]) == []


def test_never_breaks_inside_book_title_brackets():
    # 《升級吧大腦》整組必須在同一 cue，即使軟上限已過
    text = "我想要再聊老師的這本新書《升級吧大腦》之前想要先請教老師"
    cues = segment_to_cues(_words(text))
    joined = [c[2] for c in cues]
    holder = [t for t in joined if "《" in t]
    assert holder, joined
    assert "《升級吧大腦》" in holder[0]


def test_no_break_between_dict_word_bigram():
    # 「對於」在 jieba 詞典 → 即使字數到了也不能切在 對|於
    text = "想要先請教老師對於大腦的興趣是從哪裡開始的呢我很好奇"
    cues = segment_to_cues(_words(text))
    for a, b in zip(cues, cues[1:]):
        assert not (a[2].rstrip().endswith("對") and b[2].lstrip().startswith("於"))


def test_initialism_letters_joined():
    words = [
        {"word": "講", "start": 0.0, "end": 0.2},
        {"word": "A", "start": 0.2, "end": 0.4},
        {"word": "I", "start": 0.4, "end": 0.6},
        {"word": "對", "start": 0.6, "end": 0.8},
        {"word": "大", "start": 0.8, "end": 1.0},
        {"word": "腦", "start": 1.0, "end": 1.2},
    ]
    cues = segment_to_cues(words)
    assert "AI" in cues[0][2]
    assert "A I" not in cues[0][2]
