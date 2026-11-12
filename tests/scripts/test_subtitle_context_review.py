"""subtitle_context_review（podcast-pipeline S7C）的純邏輯——不跑 ASR、不連 Resolve。

例句取自 2026-11-12 20261111 李惠貞 那次臨時審查的實際字幕（review.jsonl／context-fixes）。
"""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from scripts import resolve_subtitle_rebuild as rsr
from scripts import subtitle_context_review as scr
from scripts.subtitle_context_review import ReviewError

NTSC_30 = Fraction(30000, 1001)


# ------------------------------------------------------------------ frame


@pytest.mark.parametrize(
    ("timestamp", "fps", "frame"),
    [
        ("00:00:00,000", Fraction(30), 0),
        ("00:00:04,067", Fraction(30), 122),  # 李惠貞 cue 2
        ("00:00:04,967", Fraction(30), 149),  # cue 3
        ("01:33:47,000", Fraction(30), 168810),
        # 34.5 幀：float round() 銀行家捨入會給 34；rebuild 的 live 快照用四捨五入 35
        ("00:00:01,150", Fraction(30), 35),
        ("00:00:01,001", NTSC_30, 30),
    ],
)
def test_cue_frame_is_round_half_up_of_seconds_times_fps(timestamp, fps, frame) -> None:
    assert scr.cue_frame(timestamp, fps) == frame


def test_cue_frame_round_trips_every_rebuild_rendered_frame() -> None:
    """rebuild `--fix` 用 live 起始幀比對；align 算出的幀必須和 rebuild 寫的 SRT 完全一致。"""
    for fps in (Fraction(30), NTSC_30, Fraction(25)):
        for frame in [*range(0, 400), 168807, 180000]:
            assert scr.cue_frame(rsr.format_srt_timestamp(frame, fps), fps) == frame


def test_frame_timecode_floors_to_seconds() -> None:
    assert scr.frame_timecode(0, Fraction(30)) == "00:00:00"
    assert scr.frame_timecode(149, Fraction(30)) == "00:00:04"
    assert scr.frame_timecode(168807, Fraction(30)) == "01:33:46"
    assert scr.frame_timecode(108000, Fraction(30)) == "01:00:00"


def test_parse_live_srt_numbers_frames_and_joins_lines() -> None:
    srt = (
        "﻿1\r\n00:00:00,000 --> 00:00:04,067\r\n不好意思一直吸鼻子\r\n\r\n"
        "2\r\n00:00:04,067 --> 00:00:04,967\r\n沒關係\r\n啊\r\n"
    )
    cues = scr.parse_live_srt(srt, Fraction(30))
    assert cues == [
        scr.SrtCue(1, 0, 122, "不好意思一直吸鼻子"),
        scr.SrtCue(2, 122, 149, "沒關係 啊"),
    ]


def test_parse_live_srt_rejects_duplicate_numbers_and_garbage() -> None:
    with pytest.raises(ReviewError):
        scr.parse_live_srt(
            "1\n00:00:00,000 --> 00:00:01,000\na\n\n1\n00:00:01,000 --> 00:00:02,000\nb",
            Fraction(30),
        )
    with pytest.raises(ReviewError):
        scr.parse_live_srt("1\nnot a timestamp\na", Fraction(30))


# ---------------------------------------------------------------- speaker


@pytest.mark.parametrize(
    ("host_db", "guest_db", "speaker"),
    [
        (57.2, 36.8, "主持"),  # 李惠貞 cue 7「反正如果說到時候」
        (45.5, 56.0, "來賓"),  # cue 4「有有有」
        (43.0, 44.4, "兩人"),  # cue 1：兩支麥差不多
        (58.2, 54.1, "兩人"),  # cue 2：差 4.1 dB，不夠
        (48.0, 40.0, "兩人"),  # 剛好 8 dB 不算——要「大於」8
        (48.1, 40.0, "主持"),
        (40.0, 48.1, "來賓"),
        (-99.0, -99.0, "兩人"),  # 兩邊都沒聲音
    ],
)
def test_classify_speaker_needs_more_than_8_db(host_db, guest_db, speaker) -> None:
    assert scr.classify_speaker(host_db, guest_db) == speaker


def test_rms_db_of_known_signals() -> None:
    assert scr.rms_db([]) == -99.0
    assert scr.rms_db([1000, -1000, 1000, -1000]) == pytest.approx(60.0, abs=1e-6)
    assert scr.rms_db([100] * 10) == pytest.approx(40.0, abs=1e-6)
    assert scr.rms_db([0] * 10) < -150


def test_build_review_rows_labels_speaker_from_mic_energy_and_matches_asr2() -> None:
    fps = Fraction(30)
    rate = scr.SAMPLE_RATE
    # 0–1s 主持人講（主持麥大聲）、1–2s 來賓講、2–3s 兩人重疊
    host = [3000] * rate + [100] * rate + [2000] * rate
    guest = [100] * rate + [3000] * rate + [2000] * rate
    cues = [
        scr.SrtCue(1, 0, 30, "好 謝謝修修"),
        scr.SrtCue(2, 30, 60, "謝謝"),
        scr.SrtCue(3, 60, 90, "對"),
    ]
    words = [
        {"start": 0.1, "end": 0.5, "word": "好"},
        {"start": 0.5, "end": 0.9, "word": "谢谢修修"},
        {"start": 1.2, "end": 1.6, "word": "谢谢"},
        {"start": 2.95, "end": 3.2, "word": "啊"},  # 中點 3.075，在 cue 3 的 +0.15s 內
    ]
    rows = scr.build_review_rows(cues, fps, host, guest, words, lambda s: s.replace("谢", "謝"))
    assert [row["speaker"] for row in rows] == ["主持", "來賓", "兩人"]
    assert [row["frame"] for row in rows] == [0, 30, 60]
    assert rows[0]["alt"] == "好謝謝修修" and rows[0]["sim"] == 1.0
    assert rows[1]["tc"] == "00:00:01"
    assert rows[2]["alt"] == "啊" and rows[2]["sim"] == 0.0


def test_words_in_span_uses_word_midpoint_with_pad() -> None:
    words = [
        {"start": 0.0, "end": 0.6, "word": "前"},  # 中點 0.3，cue 從 0.5 起 → pad 0.15 不夠
        {"start": 0.5, "end": 0.9, "word": "中"},
        {"start": 1.9, "end": 2.2, "word": "後"},  # 中點 2.05 ≤ 2.0 + 0.15
    ]
    assert scr.words_in_span(words, 0.5, 2.0) == "中後"


def test_review_line_marks_low_similarity_only() -> None:
    row = {
        "n": 3400,
        "tc": "01:33:46",
        "speaker": "來賓",
        "text": "好 謝謝學生",
        "alt": "好謝謝修修",
        "sim": 0.6,
    }
    assert scr.review_line(row) == "3400|01:33:46|來賓|好 謝謝學生   ⟂ASR2: 好謝謝修修"
    assert scr.review_line({**row, "sim": 0.8}) == "3400|01:33:46|來賓|好 謝謝學生"
    assert scr.review_line({**row, "alt": ""}).endswith("⟂ASR2: （沒聽到）")


def test_chunk_rows_620_gives_six_chunks_for_3659_cues() -> None:
    chunks = scr.chunk_rows(list(range(3659)), scr.DEFAULT_CHUNK_SIZE)
    assert len(chunks) == 6
    assert [len(c) for c in chunks] == [620] * 5 + [559]


def test_match_share_ignores_empty_text_cues() -> None:
    rows = [
        {"text": "一二三", "sim": 0.9},
        {"text": "四五", "sim": 0.2},
        {"text": "。", "sim": 0.0},
    ]
    assert scr.match_share(rows) == 0.5


# ----------------------------------------------------------- punctuation


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("好，謝謝修修。", "好 謝謝修修"),
        ("真的嗎？！", "真的嗎"),
        ("嗯～那個……", "嗯 那個"),
        ("對啊~我覺得…", "對啊 我覺得"),
        ("Yes, I know.", "Yes I know"),
        ("第一點：其實；", "第一點 其實"),
        # 列舉頓號與書名號保留
        ("《成為自由人》、《成為企劃人》", "《成為自由人》、《成為企劃人》"),
        ("閱讀、寫作、企劃", "閱讀、寫作、企劃"),
        # 數字內的 . , : 保留
        ("大概3.5萬本", "大概 3.5 萬本"),
        ("印了1,000本。", "印了 1,000 本"),
        ("10:30開始", "10:30 開始"),
        ("5. 然後", "5 然後"),  # 數字後面不是數字的句點仍是句讀
        # 英數之間的 . 保留（網址、檔名）
        ("上shosho.tw看", "上 shosho.tw 看"),
        ("用Node.js寫的", "用 Node.js 寫的"),
        # 半形英數與漢字之間一個空白（space_han_latin）
        ("我這個I人", "我這個 I 人"),
        ("Shopping Design官網", "Shopping Design 官網"),
        ("3C產品", "3C 產品"),
        # 已經乾淨的不變
        ("好 謝謝修修", "好 謝謝修修"),
        ("  多餘   空白  ", "多餘 空白"),
        ("……", ""),
    ],
)
def test_strip_punctuation(raw, clean) -> None:
    assert scr.strip_punctuation(raw) == clean


# ---------------------------------------------------------------- apply


def _rows(*texts: str) -> dict[int, dict]:
    return {
        n: {
            "n": n,
            "frame": n * 100,
            "tc": f"00:00:{n:02d}",
            "speaker": "來賓",
            "text": text,
            "alt": "",
            "sim": 1.0,
        }
        for n, text in enumerate(texts, 1)
    }


def test_collect_proposals_requires_exact_old_text() -> None:
    rows = _rows("好 謝謝學生")
    doc = {"fixes": [{"n": 1, "old": "好 謝謝學生 ", "new": "好 謝謝修修"}], "needs_ear": []}
    fixes, ears = scr.collect_proposals([("proposals-06", doc)], rows)
    assert fixes[1]["new"] == "好 謝謝修修" and ears == []
    bad = {"fixes": [{"n": 1, "old": "謝謝學生", "new": "謝謝修修"}]}
    with pytest.raises(ReviewError, match="old"):
        scr.collect_proposals([("proposals-06", bad)], rows)
    with pytest.raises(ReviewError, match="no such cue"):
        scr.collect_proposals([("p", {"fixes": [{"n": 9, "old": "x", "new": "y"}]})], rows)


def test_collect_proposals_flags_conflicting_reviewers() -> None:
    rows = _rows("在琴")
    docs = [
        ("proposals-01", {"fixes": [{"n": 1, "old": "在琴", "new": "再興"}]}),
        ("proposals-02", {"fixes": [{"n": 1, "old": "在琴", "new": "在興"}]}),
    ]
    fixes, _ = scr.collect_proposals(docs, rows)
    assert fixes[1]["conflict"] == ["再興", "在興"]


def test_plan_requires_every_proposal_to_be_reviewed() -> None:
    rows = _rows("好 謝謝學生", "我們有贊助")
    proposals = {
        1: {"n": 1, "new": "好 謝謝修修", "source": "proposals-06"},
        2: {"n": 2, "new": "我們沒有贊助", "source": "proposals-03"},
    }
    with pytest.raises(ReviewError, match="proposal 2"):
        scr.plan_fixes(rows, proposals, [], {"accepted": [1]})
    with pytest.raises(ReviewError, match="exactly one"):
        scr.plan_fixes(
            rows, proposals, [], {"accepted": [1, 2], "rejected": [{"n": 2, "why": "x"}]}
        )


def test_plan_conflict_must_be_resolved_by_edit() -> None:
    rows = _rows("在琴")
    proposals = {1: {"n": 1, "new": "再興", "source": "a+b", "conflict": ["再興", "在興"]}}
    with pytest.raises(ReviewError, match="disagree"):
        scr.plan_fixes(rows, proposals, [], {"accepted": [1]})
    plan = scr.plan_fixes(rows, proposals, [], {"edited": [{"n": 1, "new": "再興", "why": "校名"}]})
    assert [(f.n, f.new, f.kind) for f in plan.fixes] == [(1, "再興", "content")]


def test_plan_merges_content_fixes_punctuation_and_listen_list() -> None:
    rows = _rows(
        "好 謝謝學生",  # 1 審查員提、主 agent 接受
        "其實，我覺得。",  # 2 只有標點 → format
        "我們有贊助",  # 3 審查員提、主 agent 拒絕（證據不足）→ 待聽
        "我這個I人",  # 4 英數空白 → format
        "呆呆也是嗎",  # 5 審查員只放 needs_ear
        "進反目",  # 6 主 agent 自己加的修正
        "……",  # 7 清完是空的 → 不改、進待聽
        "已經乾淨",  # 8 不動
        "我記得是一九九九年，",  # 9 主 agent 拒絕（本人口誤照實保留），標點仍清
    )
    proposals = {
        1: {
            "n": 1,
            "new": "好 謝謝修修。",
            "source": "proposals-06",
            "evidence": "來賓說、整集叫修修",
        },
        3: {"n": 3, "new": "我們沒有贊助", "source": "proposals-03"},
        9: {"n": 9, "new": "我記得是二〇〇〇年", "source": "proposals-02"},
    }
    ears = [
        {"n": 5, "why": "被問的人是誰", "maybe": "某人暱稱", "source": "proposals-01"},
        {"n": 1, "why": "重複提報", "source": "proposals-06"},
    ]
    decisions = {
        "accepted": [1],
        "rejected": [
            {"n": 3, "why": "我/我們聽不清", "needs_ear": True},
            {"n": 9, "why": "本人口誤，照實保留"},
        ],
        "added": [{"n": 6, "old": "進反目", "new": "進墳墓", "why": "同音"}],
    }
    plan = scr.plan_fixes(rows, proposals, ears, decisions)
    got = {f.n: (f.new, f.kind) for f in plan.fixes}
    assert got == {
        1: ("好 謝謝修修", "content"),  # 審查員的 new 也過標點清理
        2: ("其實 我覺得", "format"),
        4: ("我這個 I 人", "format"),
        6: ("進墳墓", "content"),
        9: ("我記得是一九九九年", "format"),  # 拒絕內容修正，但標點照清
    }
    assert {f.n: f.frame for f in plan.fixes}[6] == 600
    assert [e["n"] for e in plan.needs_ear] == [3, 5, 7]  # 1 已修好，不進待聽
    assert plan.needs_ear[0]["maybe"] == "我們沒有贊助"
    assert plan.counts() == {"content": 2, "format": 3, "needs_ear": 3, "skipped": 1}


def test_plan_rejects_stray_decisions_and_bad_added() -> None:
    rows = _rows("甲", "乙")
    with pytest.raises(ReviewError, match="no proposal"):
        scr.plan_fixes(rows, {}, [], {"accepted": [1]})
    with pytest.raises(ReviewError, match="old does not match"):
        scr.plan_fixes(rows, {}, [], {"added": [{"n": 2, "old": "丙", "new": "丁"}]})


def test_planned_fixes_feed_rebuild_fix_file(tmp_path: Path) -> None:
    rows = _rows("好 謝謝學生")
    plan = scr.plan_fixes(
        rows, {1: {"n": 1, "new": "好 謝謝修修", "source": "p"}}, [], {"accepted": [1]}
    )
    path = tmp_path / "context-fixes.json"
    path.write_text(
        json.dumps({"fixes": [f.to_json() for f in plan.fixes]}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert rsr.load_fix_file(path) == [rsr.Fix(100, "好 謝謝學生", "好 謝謝修修")]


def test_render_listen_list_table() -> None:
    rows = _rows("呆呆也是嗎", "愛|人")
    text = scr.render_listen_list(
        rows,
        [
            {"n": 2, "why": "在講愛大出新書", "maybe": ""},
            {"n": 1, "why": "被問的人是誰", "maybe": "某人暱稱"},
        ],
        title="李惠貞 字幕待聽清單",
        episode="20261111 李惠貞",
        timeline="20261111 李惠貞",
        created="2026-11-12",
        applied={"content": 52, "format": 103},
    )
    assert text.startswith("---\ntitle: 李惠貞 字幕待聽清單\n")
    assert "前後文修正 52 句、標點／英數空白清理 103 句" in text
    body = text.split("|---|---|---|---|---|\n")[1].splitlines()
    assert body == [
        "| 00:00:01 | 來賓 | 呆呆也是嗎 | 某人暱稱 | 被問的人是誰 |",
        "| 00:00:02 | 來賓 | 愛／人 | ？ | 在講愛大出新書 |",
    ]


# ------------------------------------------------- CLI (no Resolve, no ASR)


def test_apply_plan_only_writes_context_fixes(tmp_path: Path) -> None:
    rows = _rows("好 謝謝學生", "其實，")
    (tmp_path / "review.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows.values()) + "\n", encoding="utf-8"
    )
    (tmp_path / "proposals").mkdir()
    (tmp_path / "proposals" / "proposals-01.json").write_text(
        json.dumps(
            {
                "chunk": "01",
                "fixes": [
                    {
                        "n": 1,
                        "old": "好 謝謝學生",
                        "new": "好 謝謝修修",
                        "confidence": "high",
                        "evidence": "x",
                    }
                ],
                "needs_ear": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "decisions.json").write_text(json.dumps({"accepted": [1]}), encoding="utf-8")
    assert scr.main(["apply", "--work-dir", str(tmp_path), "--plan-only"]) == 0
    data = json.loads((tmp_path / "context-fixes.json").read_text(encoding="utf-8"))
    assert data["counts"] == {"content": 1, "format": 1, "needs_ear": 0, "skipped": 0}
    assert [(f["start"], f["new"]) for f in data["fixes"]] == [(100, "好 謝謝修修"), (200, "其實")]

    out = tmp_path / "vault" / "06-字幕待聽清單.md"
    assert (
        scr.main(
            [
                "listen-list",
                "--work-dir",
                str(tmp_path),
                "--out",
                str(out),
                "--title",
                "t",
                "--episode-name",
                "e",
                "--timeline",
                "tl",
                "--created",
                "2026-11-12",
            ]
        )
        == 0
    )
    assert "（沒有待聽的句子）" in out.read_text(encoding="utf-8")


def test_apply_without_decisions_aborts(tmp_path: Path) -> None:
    rows = _rows("甲")
    (tmp_path / "review.jsonl").write_text(
        json.dumps(rows[1], ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (tmp_path / "proposals").mkdir()
    (tmp_path / "proposals" / "proposals-01.json").write_text('{"fixes": []}', encoding="utf-8")
    assert scr.main(["apply", "--work-dir", str(tmp_path), "--plan-only"]) == scr.EXIT_PRECONDITION
    assert not (tmp_path / "context-fixes.json").exists()


def test_apply_live_requires_project_and_timeline(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        scr.main(["apply", "--work-dir", str(tmp_path), "--live"])
