"""社群直播字幕顯示層（shared/livestream_subtitles.py）與燒字幕指令。"""

from __future__ import annotations

from pathlib import Path

from scripts import livestream_subtitle_burn as burn
from shared.livestream_subtitles import (
    build_display_cues,
    build_display_srt,
    clean_punctuation,
    display_width,
    strip_annotations,
    wrap_line,
)


def _same(text: str) -> str:
    return text


def test_clean_punctuation_keeps_book_and_term_marks_and_word_joiners() -> None:
    assert (
        clean_punctuation("他寫了《原子習慣》，講「Agent Skill」。")
        == "他寫了《原子習慣》 講「Agent Skill」"
    )
    assert clean_punctuation("哈囉~聽得到嗎?") == "哈囉 聽得到嗎"
    assert clean_punctuation("Claude 3.5 跟 Q&A，有 50% 的人") == "Claude 3.5 跟 Q&A 有 50% 的人"
    assert clean_punctuation("co-pilot 很好用!") == "co-pilot 很好用"


def test_clean_punctuation_is_idempotent() -> None:
    once = clean_punctuation("好，我們來看：第一個、第二個……")
    assert clean_punctuation(once) == once


def test_strip_annotations() -> None:
    assert strip_annotations("(音量注意)") == ""
    assert strip_annotations("( 字幕組:我現在在看的就是我自己的影片,所以我不會翻譯)") == ""
    assert strip_annotations("好(笑)我們開始") == "好 我們開始"


def test_wrap_line_prefers_space_and_never_splits_marks_or_latin() -> None:
    text = "所以我們今天要講的就是 「Context Engineering」到底是什麼東西以及它怎麼用"
    wrapped = wrap_line(text, 20)
    lines = wrapped.split("\n")
    assert len(lines) >= 2
    assert all(display_width(line) <= 20 for line in lines)
    assert "「Context Engineering」" in wrapped.replace("\n", "")
    assert "Context\nEngineering" not in wrapped


def test_wrap_line_short_text_untouched() -> None:
    assert wrap_line("今天天氣很好", 24) == "今天天氣很好"


def test_build_display_cues_window_drop_and_shift() -> None:
    cues = [
        (0.0, 2.0, "(音量注意)"),
        (2.0, 4.0, "哈囉~聽得到嗎?"),
        (10.0, 12.0, "我們開始，今天講 AI Agent。"),
        (20.0, 22.0, "在這裏,我們可以看到一座古老的建築物"),
        (30.0, 33.0, "呃"),
        (40.0, 45.0, "最後一句"),
    ]
    out, stats = build_display_cues(
        cues, start=8.0, end=42.0, drop_ranges=[(19.0, 23.0)], to_traditional=_same
    )
    assert out == [(2.0, 4.0, "我們開始 今天講 AI Agent"), (32.0, 34.0, "最後一句")]
    assert stats.dropped_in_ranges == 1
    assert stats.dropped_filler == 1
    assert stats.outside_window == 2  # (音量注意)、哈囉 在剪接點之前（括號那條先被 window 擋掉）
    assert stats.output_cues == 2


def test_build_display_srt_converts_simplified() -> None:
    srt = "1\n00:00:01,000 --> 00:00:02,000\n你没有开麦\n"
    text, _ = build_display_srt(srt)
    assert "你沒有開麥" in text


def test_burn_command_shape(tmp_path: Path) -> None:
    cmd = burn.burn_command(
        video=tmp_path / "v.mp4",
        srt_name="display.srt",
        start=373.0,
        end=7700.0,
        out=tmp_path / "o.mp4",
        fonts_dir=Path("C:/Users/x/Fonts"),
    )
    assert cmd[cmd.index("-ss") + 1] == "373.000"
    assert cmd[cmd.index("-to") + 1] == "7700.000"
    vf = cmd[cmd.index("-vf") + 1]
    assert vf.startswith(r"subtitles=display.srt:fontsdir='C\:/Users/x/Fonts'")
    assert "LINE Seed TW_TTF Bold" in vf
    assert cmd[cmd.index("-c:a") + 1] == "copy"


def test_wrap_line_avoids_splitting_measure_words() -> None:
    wrapped = wrap_line("讓他們大概一半以上的人都做出一個馬上幫他們解決問題的 AI agent", 24)
    assert "一\n個" not in wrapped
    assert all(display_width(line) <= 24 for line in wrapped.split("\n"))


def test_wrap_line_keeps_traditional_words_whole() -> None:
    wrapped = wrap_line("這個怎麼辦 所以這個我們在釋出的時候還是要經過人工先看一次", 24)
    assert "釋\n出" not in wrapped
