# ruff: noqa: E501  — 斷言含 CJK 長行，折行反而更難讀。
"""run_transcript_prose 純函數測試（SRT → 分講者人讀逐字稿）。

音檔相關（mic 偵測 / Viterbi 判定）由 shared/speaker_assign 自己的測試涵蓋；
本檔測的是「拿到詞級講者之後，怎麼把 cue 併成段落、停頓怎麼變標點」。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_transcript_prose import (  # noqa: E402
    _cue_speaker,
    _cue_word_ranges,
    _finish,
    _parse_srt,
    _spaces_to_commas,
    build_paragraphs,
    render_markdown,
)


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


def _cues_from(specs):
    """[(t0, t1, text)] 原樣回傳（可讀性用）。"""
    return list(specs)


# --- SRT 解析 ---------------------------------------------------------------


def test_parse_srt_basic():
    text = "1\n00:00:04,658 --> 00:00:08,679\n我也常 常聽老師\n\n2\n00:00:08,679 --> 00:00:10,859\n去上節目\n"
    assert _parse_srt(text) == [
        (4.658, 8.679, "我也常 常聽老師"),
        (8.679, 10.859, "去上節目"),
    ]


def test_parse_srt_multiline_cue_joined():
    text = "1\n00:00:00,000 --> 00:00:02,000\n第一行\n第二行\n"
    assert _parse_srt(text)[0][2] == "第一行 第二行"


# --- cue ↔ 詞對應 -----------------------------------------------------------


def test_cue_word_ranges_by_time_containment():
    words = [_w("甲", 0.0, 0.5), _w("乙", 0.6, 1.0), _w("丙", 2.0, 2.5)]
    cues = _cues_from([(0.0, 1.0, "甲乙"), (2.0, 2.5, "丙")])
    assert _cue_word_ranges(cues, words) == [[0, 1], [2]]


def test_cue_word_ranges_skips_words_without_timestamps():
    words = [_w("甲", 0.0, 0.5), {"word": "乙", "start": None, "end": None}, _w("丙", 0.6, 1.0)]
    cues = _cues_from([(0.0, 1.0, "甲丙")])
    assert _cue_word_ranges(cues, words) == [[0, 2]]


def test_cue_word_ranges_empty_when_no_word_fits():
    words = [_w("甲", 10.0, 10.5)]
    assert _cue_word_ranges(_cues_from([(0.0, 1.0, "無聲")]), words) == [[]]


# --- 講者判定（時長加權多數決）----------------------------------------------


def test_cue_speaker_duration_weighted_not_word_count():
    # 三個短詞是 speaker1、一個長詞是 speaker0 → 講的時間長的贏
    words = [_w("啊", 0.0, 0.1), _w("嗯", 0.1, 0.2), _w("喔", 0.2, 0.3), _w("長句", 0.3, 3.0)]
    assert _cue_speaker([0, 1, 2, 3], [1, 1, 1, 0], words) == 0


def test_cue_speaker_none_when_no_evidence():
    words = [_w("甲", 0.0, 0.5)]
    assert _cue_speaker([0], [None], words) is None


# --- 停頓 → 標點 -------------------------------------------------------------


def test_spaces_to_commas_only_between_cjk():
    assert _spaces_to_commas("我也常 常聽") == "我也常，常聽"
    assert _spaces_to_commas("用 AI 工具") == "用 AI 工具"  # 中英交界的空格留著
    assert _spaces_to_commas("machine learning") == "machine learning"


def test_finish_adds_period_and_question_mark():
    assert _finish("今天很好") == "今天很好。"
    assert _finish("你懂我意思嗎") == "你懂我意思嗎？"
    assert _finish("是這樣呢") == "是這樣呢？"
    assert _finish("結尾有逗號，") == "結尾有逗號。"
    assert _finish("   ") == ""


def test_finish_detects_question_word_not_only_final_particle():
    # 實跑抓到的漏網：「今天是講什麼書」沒有語氣詞但是問句
    assert _finish("今天是講什麼書") == "今天是講什麼書？"
    assert _finish("小孩怎麼辦") == "小孩怎麼辦？"


def test_finish_ignores_question_word_far_from_the_end():
    # 疑問詞在段落前段、結尾是陳述 → 不能誤判成問句（窗口只看最後 8 字）
    assert (
        _finish("我不知道他在說什麼那件事後來就這樣結束了")
        == "我不知道他在說什麼那件事後來就這樣結束了。"
    )


def _two_cue_case(next_start):
    """一位講者、兩個 cue，第二個 cue 起始時間可調 → 驗停頓門檻。"""
    words = [_w("甲", 0.0, 0.3), _w("乙", next_start, next_start + 0.3)]
    cues = _cues_from([(0.0, 0.3, "前段"), (next_start, next_start + 0.3, "後段")])
    ranges = _cue_word_ranges(cues, words)
    return build_paragraphs(cues, [0, 0], ranges, words)


def test_long_pause_between_cues_becomes_period():
    # est_gap: speak_end = 0.0 + min(0.3, 0.28+0.12) = 0.3 → gap = 1.0-0.3 = 0.7 ≥ 0.6
    assert _two_cue_case(1.0) == [(0, "前段。後段。")]


def test_medium_pause_between_cues_becomes_comma():
    # gap = 0.7 - 0.3 = 0.4 → 落在 0.3–0.6
    assert _two_cue_case(0.7) == [(0, "前段，後段。")]


def test_short_pause_between_cues_joins_directly():
    # gap = 0.35 - 0.3 = 0.05 → cue 是字數上限切的，不是停頓，直接相連
    assert _two_cue_case(0.35) == [(0, "前段後段。")]


# --- 段落結構 ---------------------------------------------------------------


def test_speaker_change_always_starts_new_paragraph():
    words = [_w("問", 0.0, 0.3), _w("答", 0.35, 0.65)]
    cues = _cues_from([(0.0, 0.3, "今天是講什麼書"), (0.35, 0.65, "講 AI 對大腦的影響")])
    ranges = _cue_word_ranges(cues, words)
    # 停頓極短，但換人一定換段（不會被「直接相連」規則併掉）
    assert build_paragraphs(cues, [0, 1], ranges, words) == [
        (0, "今天是講什麼書？"),
        (1, "講 AI 對大腦的影響。"),
    ]


def test_cue_without_speaker_evidence_inherits_previous():
    words = [_w("甲", 0.0, 0.3), _w("乙", 0.35, 0.65)]
    cues = _cues_from([(0.0, 0.3, "有證據"), (0.35, 0.65, "沒證據")])
    ranges = _cue_word_ranges(cues, words)
    assert build_paragraphs(cues, [1, None], ranges, words) == [(1, "有證據沒證據。")]


def test_leading_cue_without_speaker_is_dropped():
    # 第一個 cue 就沒有講者證據（純環境音）→ 沒有人可以掛，跳過而非亂猜
    words = [_w("甲", 0.0, 0.3), _w("乙", 0.35, 0.65)]
    cues = _cues_from([(0.0, 0.3, "環境音"), (0.35, 0.65, "真的話")])
    ranges = _cue_word_ranges(cues, words)
    assert build_paragraphs(cues, [None, 0], ranges, words) == [(0, "真的話。")]


def test_long_turn_splits_on_long_pause_only_when_paragraph_is_thick():
    long_text = "很" * 300
    words = [_w("甲", 0.0, 0.3), _w("乙", 0.5, 0.8), _w("丙", 3.0, 3.3)]
    cues = _cues_from([(0.0, 0.3, long_text), (0.5, 0.8, long_text), (3.0, 3.3, "新段")])
    ranges = _cue_word_ranges(cues, words)
    paras = build_paragraphs(cues, [0, 0, 0], ranges, words)
    # 前兩 cue 併成一段（停頓 0.2s 不夠長），第三個 cue 停頓 2.4s 且前段已 600 字 → 另起
    assert len(paras) == 2
    assert paras[1] == (0, "新段。")


def test_short_paragraph_not_split_even_on_long_pause():
    words = [_w("甲", 0.0, 0.3), _w("乙", 5.0, 5.3)]
    cues = _cues_from([(0.0, 0.3, "短段"), (5.0, 5.3, "後面")])
    ranges = _cue_word_ranges(cues, words)
    # 停頓很長但段落才 2 字 → 用句號接，不碎成兩段
    assert build_paragraphs(cues, [0, 0], ranges, words) == [(0, "短段。後面。")]


def test_empty_cue_text_ignored():
    words = [_w("甲", 0.0, 0.3), _w("乙", 0.35, 0.65)]
    cues = _cues_from([(0.0, 0.3, ""), (0.35, 0.65, "有字")])
    ranges = _cue_word_ranges(cues, words)
    assert build_paragraphs(cues, [0, 0], ranges, words) == [(0, "有字。")]


def test_keep_spaces_leaves_pause_spaces_alone():
    words = [_w("甲", 0.0, 0.3)]
    cues = _cues_from([(0.0, 0.3, "我也常 常聽")])
    ranges = _cue_word_ranges(cues, words)
    assert build_paragraphs(cues, [0], ranges, words, keep_spaces=True) == [(0, "我也常 常聽。")]
    assert build_paragraphs(cues, [0], ranges, words) == [(0, "我也常，常聽。")]


# --- 輸出 -------------------------------------------------------------------


def test_render_markdown_labels_speakers():
    md = render_markdown([(0, "問句。"), (1, "答句。")], {0: "張修修", 1: "謝伯讓"})
    assert md == "**張修修**：問句。\n\n**謝伯讓**：答句。"


def test_render_markdown_falls_back_for_unknown_speaker():
    assert render_markdown([(2, "話。")], {0: "A", 1: "B"}) == "**講者2**：話。"


# --- vault 防護 -------------------------------------------------------------


def test_write_vault_refuses_when_vault_root_missing(tmp_path, monkeypatch):
    """vault 路徑不存在 → 停，不能讓 mkdir(parents=True) 生出影子目錄。

    在 sibling worktree 跑時 .env 讀不到（VAULT_PATH 只在主倉庫），vault_path
    會退回 config.yaml 的 VPS 路徑，Windows 上就變成 E:/home/... 這種影子目錄。
    """
    import run_transcript_prose as rtp

    import shared.config
    import shared.obsidian_writer

    ghost = tmp_path / "home" / "Shosho LifeOS"
    monkeypatch.setattr(shared.config, "get_vault_path", lambda: ghost)

    def _boom(*a, **kw):  # write_page 不該被呼叫到
        raise AssertionError("vault root 不存在時不可以動手寫檔")

    monkeypatch.setattr(shared.obsidian_writer, "write_page", _boom)

    with pytest.raises(SystemExit) as exc:
        rtp._write_vault("body", episode_dir=tmp_path / "ep", host="H", guest="G", slug=None)
    assert "vault 路徑不存在" in str(exc.value)
    assert not ghost.exists()
