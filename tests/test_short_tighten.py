"""run_short_tighten 純函數測試（短片 jump-cut 緊湊化）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_short_tighten import (
    _keep_segments,
    _master_words,
    _strip_cut_word,
    _subtitle_source_config,
)


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


def test_subtitle_source_config_keeps_corrected_text_and_raw_word_clock(tmp_path):
    """Resolve 校正版文字可換鐘；raw words 仍留在 media clock，不能跟著平移。"""
    episode = tmp_path / "episode"
    highlights = episode / "highlights"
    highlights.mkdir(parents=True)
    corrected = highlights / "resolve_latest.srt"
    corrected.write_text("1\n00:00:01,000 --> 00:00:02,000\n快的泛科學新聞\n", encoding="utf-8")
    (highlights / "candidates.json").write_text(
        """{
          "candidates": [{
            "id": "KS2",
            "subtitle_source": "highlights/resolve_latest.srt",
            "subtitle_clock_offset": -289.333333,
            "words_clock_offset": 0.0
          }]
        }""",
        encoding="utf-8",
    )

    source, subtitle_offset, words_offset = _subtitle_source_config(episode, "KS2")

    assert source == corrected
    assert subtitle_offset == -289.333333
    assert words_offset == 0.0


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


# ── 長片格式（修修 2026-08-03：長片緊湊化刻意比短片鬆）─────────────────────


def test_long_tighten_keeps_filler_and_backchannel():
    from run_short_tighten import FORMAT_TIGHTEN

    short, long_ = FORMAT_TIGHTEN["short"], FORMAT_TIGHTEN["long"]
    assert short["cut_filler"] and short["cut_backchannel"]
    assert short["fine_subtitles"] and not long_["fine_subtitles"]
    # 長片保留連接詞與附和——那是訪談的自然感，不是贅肉
    assert not long_["cut_filler"] and not long_["cut_backchannel"]
    # 每個門檻都比短片鬆（實測依據見 FORMAT_TIGHTEN 註解）
    for k in ("min_pause", "keep_head", "keep_tail", "min_cut", "min_keep_seg"):
        assert long_[k] > short[k], k


def test_long_detect_uses_editorial_master_without_legacy_aliases(tmp_path, monkeypatch):
    import run_short_tighten as tighten

    highlights = tmp_path / "highlights"
    highlights.mkdir()
    (highlights / "candidates.json").write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "id": "value-L01",
                        "format": "long",
                        "t_start": 10.0,
                        "t_end": 20.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (highlights / "winners.json").write_text(
        json.dumps({"winners": [{"id": "value-L01", "rank": 1}]}),
        encoding="utf-8",
    )
    official_srt = tmp_path / "subtitle-release" / "memo-dual-audit-v1" / "release.srt"
    official_srt.parent.mkdir(parents=True)
    official_srt.write_text("1\n00:00:10,000 --> 00:00:20,000\n正式字幕\n", encoding="utf-8")
    lineage = {"contract": "podcast-editorial-master-v1", "content_hash": "a" * 64}
    for path in (highlights / "candidates.json", highlights / "winners.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["editorial_master_lineage"] = lineage
        path.write_text(json.dumps(payload), encoding="utf-8")
    selection = SimpleNamespace(
        media_path=tmp_path / "editorial-master/v1/master.mp4",
        srt_path=official_srt,
        identity=lambda: lineage,
    )
    monkeypatch.setattr(tighten, "_open_editorial_master", lambda episode_dir: selection)
    monkeypatch.setattr(tighten, "_detect_silences", lambda *args, **kwargs: [(12.0, 13.2)])

    result = tighten.detect(tmp_path, "value-L01")

    assert result["status"] == "detected"
    payload = json.loads(Path(result["file"]).read_text(encoding="utf-8"))
    assert payload["editorial_master_lineage"] == lineage
    assert payload["cuts"] == [{"t0": 12.2, "t1": 13.05, "kind": "pause", "dur": 1.2, "keep": True}]
    assert not (tmp_path / "transcript.srt").exists()
    assert not (tmp_path / "subs" / "words.json").exists()


def test_cut_lineage_mismatch_requires_redetect():
    import pytest
    from run_short_tighten import _assert_cut_subtitle_lineage

    with pytest.raises(SystemExit, match="已過期"):
        _assert_cut_subtitle_lineage(
            {"editorial_master_lineage": {"content_hash": "old"}},
            {"content_hash": "new"},
        )


def test_retime_without_word_timing_preserves_release_cue_boundaries(tmp_path, monkeypatch):
    import run_short_tighten as tighten

    import shared.subtitle_reboundary as rebounding

    official_srt = tmp_path / "release.srt"
    official_srt.write_text(
        "1\n00:00:10,000 --> 00:00:12,000\n正式字幕第一句\n\n"
        "2\n00:00:12,000 --> 00:00:14,000\n正式字幕第二句\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(tighten, "_tight_pause_map", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        rebounding,
        "repair_cues",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not guess boundaries")),
    )

    result, cue_count = tighten._retime_srt(
        tmp_path,
        "value-L01",
        [(10.0, 14.0)],
        [],
        transcript=official_srt,
    )

    assert cue_count == 2
    rendered = result.read_text(encoding="utf-8")
    assert "00:00:00,000 --> 00:00:02,000\n正式字幕第一句" in rendered
    assert "00:00:02,000 --> 00:00:04,000\n正式字幕第二句" in rendered


def test_long_keep_segments_absorbs_bigger_islands():
    # 0.4s 孤島在短片是合法保留段，在長片（min_keep_seg=0.5）要被吸收
    cuts = [_cut(2.0, 3.0), _cut(3.4, 5.0)]
    assert _keep_segments(0.0, 10.0, cuts, 0.30) == [(0.0, 2.0), (3.0, 3.4), (5.0, 10.0)]
    assert _keep_segments(0.0, 10.0, cuts, 0.50) == [(0.0, 2.0), (5.0, 10.0)]


def test_passage_cut_needs_no_special_casing():
    """語意刪段（kind=passage）走同一套補集運算，不必改 code。"""
    cuts = [{"t0": 100.0, "t1": 160.0, "kind": "passage", "keep": True}]
    assert _keep_segments(0.0, 300.0, cuts, 0.50) == [(0.0, 100.0), (160.0, 300.0)]


def test_review_long_format_params():
    """長片自檢：960×540、縮圖牆分段、缺口門檻放寬。"""
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "scripts"))
    from run_short_review import FORMAT_REVIEW

    short, long_ = FORMAT_REVIEW["short"], FORMAT_REVIEW["long"]
    assert short["preview"] == (540, 960) and long_["preview"] == (960, 540)
    assert short["chunk_sec"] is None  # 短片一張縮圖牆看得完
    assert long_["chunk_sec"] == 180.0  # 752s 單張是 1800×7676px，盲審讀不到細節
    assert long_["gap_sec"] > short["gap_sec"]  # 長片密度目標較低，同尺會誤判死區


def test_titles_long_format_params():
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "scripts"))
    from run_short_titles import FORMAT_TITLES

    assert FORMAT_TITLES["short"]["comp"] == "punch_card.html"
    assert FORMAT_TITLES["long"]["comp"] == "punch_card_wide.html"
    # 16:9 有寬度沒高度 → 每行字數放寬
    assert FORMAT_TITLES["long"]["max_line"] > FORMAT_TITLES["short"]["max_line"]
    assert FORMAT_TITLES["long"]["max_line_hero"] > FORMAT_TITLES["short"]["max_line_hero"]


def test_short_title_motion_grammar_accepts_animation_and_size_variety():
    from run_short_titles import _validate_short_motion_grammar

    _validate_short_motion_grammar(
        [
            {"tier": 2, "animation": "swipe", "line1_scale": 0.88},
            {"tier": 2, "animation": "wipe", "line2_scale": 1.12},
            {"tier": 1, "animation": "slam", "card_scale": 0.98},
            {"tier": 2, "animation": "word", "line1_scale": 1.08},
        ]
    )


def test_short_title_motion_grammar_rejects_three_identical_entries():
    import pytest
    from run_short_titles import _validate_short_motion_grammar

    with pytest.raises(SystemExit, match="連續三張"):
        _validate_short_motion_grammar(
            [
                {"animation": "swipe", "line1_scale": 0.88},
                {"animation": "swipe", "line1_scale": 1.00},
                {"animation": "swipe", "line1_scale": 1.12},
                {"animation": "word", "line2_scale": 1.08},
            ]
        )


def test_short_title_motion_grammar_rejects_uniform_size_template():
    import pytest
    from run_short_titles import _validate_short_motion_grammar

    with pytest.raises(SystemExit, match="至少要有兩組"):
        _validate_short_motion_grammar(
            [
                {"animation": "swipe"},
                {"animation": "wipe"},
                {"animation": "word"},
                {"tier": 1, "animation": "slam"},
            ]
        )


def test_kinetic_title_text_must_be_an_exact_transcript_span():
    import pytest
    from run_short_titles import _validate_exact_transcript_span

    source = "那個消費者入口的就是那一個壽司"
    _validate_exact_transcript_span(1, "那個消費者\n入口的就是", source)
    _validate_exact_transcript_span(1, "那一個壽司", source)

    with pytest.raises(SystemExit, match="不是逐字稿的原文連續片段"):
        _validate_exact_transcript_span(1, "消費者入口就是一個壽司", source)


def test_brand_pattern_is_official_single_use_gold_quote_only():
    import pytest
    from run_short_titles import _validate_brand_pattern_usage

    pattern = {
        "t0": 10.0,
        "t1": 13.0,
        "brand_pattern": {
            "asset": "shards-gray-on-orange",
            "role": "gold_quote",
            "at": 1.8,
            "duration": 0.8,
        },
    }
    _validate_brand_pattern_usage([pattern, {}])

    with pytest.raises(SystemExit, match="全片只能出現一次"):
        _validate_brand_pattern_usage([pattern, pattern])

    with pytest.raises(SystemExit, match="臨時繪製"):
        _validate_brand_pattern_usage([{"stage": "rails"}])


def test_full_transcript_choreography_requires_every_cue_exactly_once():
    import pytest
    from run_short_titles import _validate_full_transcript_coverage

    cues = {
        1: {"t0": 0.0, "t1": 1.0, "text": "第一句"},
        2: {"t0": 1.0, "t1": 2.0, "text": "第二句"},
        3: {"t0": 2.0, "t1": 3.0, "text": "第三句"},
    }
    titles = [
        {"source_cues": [1, 2], "states": [{"source_cues": [1]}, {"source_cues": [2]}]},
        {"source_cues": [3], "states": [{"source_cues": [3]}]},
    ]
    _validate_full_transcript_coverage(titles, cues)

    with pytest.raises(SystemExit, match="逐字稿覆蓋不完整"):
        _validate_full_transcript_coverage(titles[:1], cues)


def test_selective_transcript_titles_keep_legacy_state_schema_compatible():
    from run_short_titles import _validate_transcript_driven_title

    cues = {
        1: {"t0": 0.0, "t1": 1.0, "text": "一般字幕保留"},
        2: {"t0": 1.0, "t1": 2.0, "text": "只有這句放大"},
    }
    title = {
        "t0": 0.0,
        "t1": 2.0,
        "source_cues": [1, 2],
        "states": [
            {
                "at": 1.0,
                "trigger_cue": 2,
                "lines": ["只有這句放大"],
            }
        ],
    }

    _validate_transcript_driven_title(0, title, cues)


def test_kinetic_renderer_has_stable_non_elastic_exits():
    html = (
        Path(__file__).resolve().parent.parent
        / "video/compositions/punch_card/compositions/kinetic_sequence.html"
    ).read_text(encoding="utf-8")

    assert "back.out" not in html
    assert 'config.exit === "shrink"' not in html


def test_full_transcript_display_rejects_punctuation():
    import pytest
    from run_short_titles import _validate_full_transcript_display

    titles = [
        {
            "states": [
                {"lines": ["會一直想，當然"]},
            ]
        }
    ]

    with pytest.raises(SystemExit, match="短片顯示文字不可含標點"):
        _validate_full_transcript_display(titles)


def test_kinetic_caption_rejects_three_simultaneous_lines():
    import pytest
    from run_short_titles import FORMAT_TITLES, _validate_kinetic_sequence

    title = {
        "states": [
            {
                "at": 0.0,
                "role": "caption",
                "lines": ["但是對那一個", "觀眾或那一個", "讀者來說那可能是"],
            },
            {"at": 1.5, "role": "caption", "lines": ["下一個節拍"]},
        ]
    }

    with pytest.raises(SystemExit, match="caption 最多 2 行"):
        _validate_kinetic_sequence(0, title, 3.0, FORMAT_TITLES["short"])


def test_kinetic_renderer_centres_each_caption_line_and_supports_hard_cuts():
    html = (
        Path(__file__).resolve().parent.parent
        / "video/compositions/punch_card/compositions/kinetic_sequence.html"
    ).read_text(encoding="utf-8")

    assert "lineOffsets" not in html
    assert "state.line_roles" in html
    assert "line-emphasis" in html
    assert "captionGroupWidth" not in html
    assert 'transition === "cut"' in html
    assert "text-align: center" in html


def test_split_opener_title_rejects_the_lower_panel_face_zone():
    import pytest
    from run_short_titles import _validate_split_opener_face_clearance

    unsafe = [{"t0": 0.0, "t1": 7.5, "pos_y": 0.65}]
    with pytest.raises(SystemExit, match="上下分割開場.*遮住下半格人臉"):
        _validate_split_opener_face_clearance(unsafe, opener_sec=2.668)

    safe = [{"t0": 0.0, "t1": 7.5, "pos_y": 0.86}]
    _validate_split_opener_face_clearance(safe, opener_sec=2.668)


def test_ks1_r010_fixture_limits_caption_density_and_passes_full_display_contract():
    import json
    import re

    from run_short_titles import (
        FORMAT_TITLES,
        _validate_full_transcript_coverage,
        _validate_full_transcript_display,
        _validate_kinetic_sequence,
        _validate_transcript_driven_title,
    )

    root = Path(__file__).resolve().parent.parent
    srt = (root / "tests/fixtures/short_reference/KS1_tight_r010.srt").read_text(encoding="utf-8")
    plan = json.loads(
        (root / "tests/fixtures/short_reference/KS1_titles_editorial_v4.json").read_text(
            encoding="utf-8"
        )
    )
    cues = {}
    timestamp = re.compile(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)")
    for block in re.split(r"\n\s*\n", srt.strip()):
        lines = block.splitlines()
        match = timestamp.fullmatch(lines[1])
        parts = [int(value) for value in match.groups()]
        t0 = parts[0] * 3600 + parts[1] * 60 + parts[2] + parts[3] / 1000
        t1 = parts[4] * 3600 + parts[5] * 60 + parts[6] + parts[7] / 1000
        cues[int(lines[0])] = {"t0": t0, "t1": t1, "text": "".join(lines[2:])}

    assert cues[8]["text"] == "壽司所以"
    assert cues[28]["text"] == "對於壽司"
    assert plan["transition_mode"] == "cut"
    caption_states = [
        state
        for title in plan["titles"]
        for state in title["states"]
        if state.get("role") == "caption"
    ]
    assert caption_states
    assert max(len(state["lines"]) for state in caption_states) <= 2
    sushi_state = next(
        state
        for title in plan["titles"]
        for state in title["states"]
        if state.get("source_cues") == [8, 9]
    )
    assert sushi_state["lines"] == ["壽司", "所以那時候老師傅"]
    _validate_full_transcript_coverage(plan["titles"], cues)
    _validate_full_transcript_display(plan["titles"])
    for index, title in enumerate(plan["titles"]):
        show_sec = float(title["t1"]) - float(title["t0"])
        _validate_kinetic_sequence(index, title, show_sec, FORMAT_TITLES["short"])
        _validate_transcript_driven_title(
            index,
            title,
            cues,
            require_full_state_coverage=True,
        )


def test_review_skips_bottom_subtitles_for_full_transcript_choreography(tmp_path):
    from run_short_review import _uses_transcript_choreography

    plan = tmp_path / "highlights/tighten/KS1_titles.json"
    plan.parent.mkdir(parents=True)
    plan.write_text('{"covers_full_transcript": true, "titles": []}', encoding="utf-8")

    assert _uses_transcript_choreography(tmp_path, "KS1") is True


# --- 詞級時間戳投影到 Master 時鐘（修修 2026-08-30） ------------------------


def _write_conform(tmp_path: Path, master_hash: str) -> None:
    """最小 conform map：來源 0–4s → 成片 0–4s，來源 7–10s → 成片 4–7s（中間被剪掉）。"""
    path = tmp_path / "editorial-master" / "v1" / "conform-map.v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "contract": "podcast-editorial-conform-map-v1",
                "schema_version": 1,
                "fps": 30.0,
                "editorial_master_lineage": {"content_hash": master_hash},
                "sources": {"audio": {"path": "normalized.wav", "offset_sec": 0.0}},
                "segments": [
                    {"master_start_sec": 0.0, "master_end_sec": 4.0, "source_start_sec": 0.0},
                    {"master_start_sec": 4.0, "master_end_sec": 7.0, "source_start_sec": 7.0},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_words(tmp_path: Path, words: list[dict]) -> None:
    path = tmp_path / "subs" / "words.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"words": words}, ensure_ascii=False), encoding="utf-8")


def _master(hash_: str = "abc"):
    return SimpleNamespace(identity=lambda: {"content_hash": hash_})


def test_master_words_projects_and_drops_removed_spans(tmp_path):
    """被剪掉的話在成片裡不存在——那些詞必須丟掉，不能拿去下刀。"""
    _write_conform(tmp_path, "abc")
    _write_words(
        tmp_path,
        [
            {"word": "那", "start": 1.0, "end": 1.4},
            {"word": "剪", "start": 5.0, "end": 5.4},  # 落在被剪掉的 4–7s
            {"word": "後", "start": 8.0, "end": 8.4},
        ],
    )
    out = _master_words(tmp_path, _master())
    assert [w["word"] for w in out] == ["那", "後"]
    assert out[0]["start"] == 1.0  # 第一段 1:1
    assert out[1]["start"] == 5.0  # 8.0 - 3.0（被剪掉的長度）


def test_master_words_needs_the_edit_map(tmp_path):
    """沒有 conform map 就回空——那正是 2026-08-30 凌晨停用它的理由。"""
    _write_words(tmp_path, [{"word": "那", "start": 1.0, "end": 1.4}])
    assert _master_words(tmp_path, _master()) == []


def test_master_words_refuses_a_stale_edit_map(tmp_path):
    _write_conform(tmp_path, "old-hash")
    _write_words(tmp_path, [{"word": "那", "start": 1.0, "end": 1.4}])
    assert _master_words(tmp_path, _master("abc")) == []


def test_master_words_without_word_timings(tmp_path):
    """本集走 memo dual-audit，只有句級時間戳——安全退回只產 pause 刀。"""
    _write_conform(tmp_path, "abc")
    assert _master_words(tmp_path, _master()) == []


# --- noise 候選的生死判 ---------------------------------------------------
# 2026-08-30：修修聽到「那美甲不只是，呃，這個，這後面…」的贅音沒被剪掉。
# 偵測器其實抓到了，是複審判錯——WhisperX 把「是」對成 1.74s，整個把候選包住，
# 而複審視圖只顯示「結束於候選前／開始於候選後」的詞，那個「是」在畫面上消失，
# 看起來像 ASR 漏字、候選是語音。以下四條把當時的判斷過程釘死。

from run_short_tighten import _enclosing_word, _judge_noise  # noqa: E402


def _noise_fixture(tmp_path, corrected: str):
    (tmp_path / "m.srt").write_text(
        f"1\n00:00:00,000 --> 00:00:03,000\n{corrected}\n",
        encoding="utf-8",
    )
    chars = "美甲不只是要把它弄好"
    words = []
    for i, ch in enumerate(chars):
        start = round(i * 0.2, 3)
        # 「是」被 WhisperX 拉長到 1.2s，把候選整個包住
        end = 2.0 if ch == "是" else round(start + 0.2, 3)
        words.append({"word": ch, "start": start, "end": end})
    return SimpleNamespace(srt_path=tmp_path / "m.srt"), words


def _noise_cut(peak: float = -4.0):
    return {"t0": 1.2, "t1": 1.6, "kind": "noise", "dur": 0.4, "peak_db": peak, "keep": None}


def test_judge_noise_cuts_sound_no_transcript_accounts_for(tmp_path):
    master, words = _noise_fixture(tmp_path, "美甲不只是要把它弄好")
    cuts = [_noise_cut()]
    _judge_noise(master, cuts, words)
    assert cuts[0]["keep"] is True
    # 包住候選的那個詞一定要出現在複審資料裡，否則又會重蹈覆轍
    assert cuts[0]["enclosed_by"]["word"] == "是"


def test_enclosing_word_finds_stretched_token():
    words = [{"word": "是", "start": 0.8, "end": 2.0}]
    assert _enclosing_word(words, 1.2, 1.6)["word"] == "是"
    assert _enclosing_word(words, 2.1, 2.3) is None


def test_judge_noise_keeps_breath(tmp_path):
    master, words = _noise_fixture(tmp_path, "美甲不只是要把它弄好")
    cuts = [_noise_cut(peak=-12.0)]
    _judge_noise(master, cuts, words)
    assert cuts[0]["keep"] is False
    assert "換氣" in cuts[0]["review"]


def test_judge_noise_skips_span_already_cut(tmp_path):
    master, words = _noise_fixture(tmp_path, "美甲不只是要把它弄好")
    cuts = [_noise_cut(), {"t0": 1.0, "t1": 2.0, "kind": "manual", "keep": True}]
    _judge_noise(master, cuts, words)
    assert cuts[0]["keep"] is False


def test_judge_noise_spares_span_where_corrected_has_extra_char(tmp_path):
    # 校正稿有「得」、ASR 沒有 → 那個字可能就落在候選裡，剪了會斷字
    master, words = _noise_fixture(tmp_path, "美甲不只是要把它弄得好")
    cuts = [_noise_cut()]
    _judge_noise(master, cuts, words)
    assert cuts[0]["keep"] is False
    assert "校正稿" in cuts[0]["review"]


def test_carry_reviewed_keeps_human_calls_but_rejudges_noise():
    """--detect 換的是偵測邏輯，不該把人審過的機械類判斷清成 null；
    noise 反過來，規則是權威，舊結論（包含判錯的）不沿用。"""
    from run_short_tighten import _carry_reviewed

    prev = [
        {"t0": 1.0, "t1": 1.3, "kind": "redundant", "keep": True, "review": "審過"},
        {"t0": 2.0, "t1": 2.3, "kind": "noise", "keep": False},
        {"t0": 3.0, "t1": 3.3, "kind": "filler", "keep": False},
    ]
    fresh = [
        {"t0": 1.0, "t1": 1.3, "kind": "redundant", "keep": None},
        {"t0": 2.0, "t1": 2.3, "kind": "noise", "keep": None},
        {"t0": 3.0, "t1": 3.9, "kind": "filler", "keep": None},  # 區間變了 = 不同段音檔
    ]
    assert _carry_reviewed(fresh, prev) == 1
    assert fresh[0]["keep"] is True and fresh[0]["review"] == "審過"
    assert fresh[1]["keep"] is None
    assert fresh[2]["keep"] is None
