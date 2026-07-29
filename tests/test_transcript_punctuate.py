# ruff: noqa: E501  — 斷言含 CJK 長行，折行反而更難讀。
"""run_transcript_punctuate 測試——重點全在**驗證擋不擋得住**。

這一層的價值不是 prompt 寫得多好，而是「subagent 改了字就整塊退回」。
所以測的是各種改字方式有沒有被抓到，以及合法的改動有沒有被誤殺。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_transcript_punctuate import (  # noqa: E402
    apply,
    emit,
    parse_prose,
    render_prose,
    verify,
)

ORIG = [
    ("張修修", "今天是講什麼書？"),
    ("謝伯讓", "就是你反芻了過去好的事情，我們要延續不好的事情，我們要作為警惕。"),
]


def test_parse_and_render_round_trip():
    md = render_prose(ORIG)
    assert parse_prose(md) == ORIG


def test_parse_ignores_non_paragraph_lines():
    md = "> 這是警語，不是段落。\n\n**張修修**：真正的段落。"
    assert parse_prose(md) == [("張修修", "真正的段落。")]


# --- 合法的改動：只搬標點 ---------------------------------------------------


def test_moving_punctuation_passes():
    # 正是那句意思被斷反的話——把逗號搬到正確位置
    fixed = [
        ("張修修", "今天是講什麼書？"),
        ("謝伯讓", "就是你反芻了過去，好的事情我們要延續，不好的事情我們要作為警惕。"),
    ]
    assert verify(ORIG, fixed) is None


def test_splitting_a_long_turn_passes():
    # 允許把同一個人的長段落再切成兩段（可讀性）
    split = [
        ("張修修", "今天是講什麼書？"),
        ("謝伯讓", "就是你反芻了過去，好的事情我們要延續。"),
        ("謝伯讓", "不好的事情我們要作為警惕。"),
    ]
    assert verify(ORIG, split) is None


# --- 該擋下來的 -------------------------------------------------------------


def test_changed_word_is_rejected():
    bad = [
        ("張修修", "今天是講什麼書？"),
        ("謝伯讓", "就是你回想了過去，好的事情我們要延續，不好的事情我們要作為警惕。"),
    ]
    reason = verify(ORIG, bad)
    assert reason and "文字被改動" in reason


def test_added_word_is_rejected():
    bad = [("張修修", "今天老師是講什麼書？"), ORIG[1]]
    assert "文字被改動" in verify(ORIG, bad)


def test_deleted_filler_is_rejected():
    # 刪贅詞也不行——這是逐字稿，不是潤稿
    bad = [("張修修", "今天講什麼書？"), ORIG[1]]
    assert "文字被改動" in verify(ORIG, bad)


def test_text_moved_to_the_other_speaker_is_rejected():
    bad = [
        ("張修修", "今天是講什麼書？就是你反芻了過去，"),
        ("謝伯讓", "好的事情我們要延續，不好的事情我們要作為警惕。"),
    ]
    # 整體字元流與講者序列都沒變——只有逐 turn 比對抓得到（自家測試抓到的漏洞）
    reason = verify(ORIG, bad)
    assert reason and "搬動" in reason


def test_reordered_speakers_is_rejected():
    bad = [ORIG[1], ORIG[0]]
    assert verify(ORIG, bad) is not None


def test_empty_rewrite_is_rejected():
    assert "沒有任何段落" in verify(ORIG, [])


# --- emit / apply 流程 ------------------------------------------------------


def _episode(tmp_path, paragraphs):
    (tmp_path / "transcript_prose.md").write_text(render_prose(paragraphs) + "\n", encoding="utf-8")
    return tmp_path


def test_emit_chunks_at_paragraph_boundaries(tmp_path):
    paras = [("張修修", "問" * 100), ("謝伯讓", "答" * 2600), ("張修修", "再問" * 50)]
    out = emit(_episode(tmp_path, paras))
    assert out["paragraphs"] == 3
    files = sorted((tmp_path / "punct_work").glob("*.md"))
    assert len(files) == out["chunks"] >= 2
    # 每個 chunk 都要能被解析回段落（不會切在段落中間）
    for f in files:
        assert parse_prose(f.read_text(encoding="utf-8"))


def test_apply_writes_and_backs_up(tmp_path):
    ep = _episode(tmp_path, ORIG)
    emit(ep)
    chunk = tmp_path / "punct_work" / "001.md"
    fixed = [
        ("張修修", "今天是講什麼書？"),
        ("謝伯讓", "就是你反芻了過去，好的事情我們要延續，不好的事情我們要作為警惕。"),
    ]
    (chunk.parent / "001.done.md").write_text(render_prose(fixed), encoding="utf-8")
    out = apply(ep)
    assert out["status"] == "applied"
    assert (tmp_path / "transcript_prose.pre_punct.md").exists()
    assert "反芻了過去，好的事情" in (tmp_path / "transcript_prose.md").read_text(encoding="utf-8")


def test_apply_refuses_whole_batch_when_one_chunk_fails(tmp_path):
    ep = _episode(tmp_path, ORIG)
    emit(ep)
    bad = [("張修修", "今天是講哪本書？"), ORIG[1]]
    (tmp_path / "punct_work" / "001.done.md").write_text(render_prose(bad), encoding="utf-8")
    out = apply(ep)
    assert out["status"] == "rejected"
    assert out["failures"] and "001.done.md" == out["failures"][0]["chunk"]
    # 原稿不能被動到，備份也不該產生
    assert not (tmp_path / "transcript_prose.pre_punct.md").exists()
    assert "今天是講什麼書" in (tmp_path / "transcript_prose.md").read_text(encoding="utf-8")


def test_apply_keeps_original_for_chunks_without_a_rewrite(tmp_path):
    ep = _episode(tmp_path, ORIG)
    emit(ep)
    out = apply(ep, dry_run=True)
    assert out["chunks_missing"] == ["001.md"]
    assert out["paragraphs"] == len(ORIG)


def test_apply_can_run_twice(tmp_path):
    # 備份檔已存在時不可以炸（Windows 的 Path.rename 會）
    ep = _episode(tmp_path, ORIG)
    emit(ep)
    fixed = [
        ("張修修", "今天是講什麼書？"),
        ("謝伯讓", "就是你反芻了過去，好的事情我們要延續，不好的事情我們要作為警惕。"),
    ]
    (tmp_path / "punct_work" / "001.done.md").write_text(render_prose(fixed), encoding="utf-8")
    assert apply(ep)["status"] == "applied"
    assert apply(ep)["status"] == "applied"
