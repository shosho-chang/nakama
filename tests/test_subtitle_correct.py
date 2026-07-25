"""shared/subtitle_correct.py 測試（零外呼：LLM 走 monkeypatch）。"""

from __future__ import annotations

import json

from scripts.run_subtitle_correct import find_script_file
from shared.subtitle_correct import (
    build_correction_report,
    correct_srt_llm,
    correct_srt_scripted,
    load_refs_text,
)


def _srt(cues: list[str]) -> str:
    blocks = []
    for i, text in enumerate(cues, start=1):
        start = f"00:00:{(i - 1) * 2:02d},000"
        end = f"00:00:{i * 2:02d},000"
        blocks.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(blocks)


# ── scripted 模式 ──


def test_scripted_fixes_homophone_from_script():
    srt = _srt(["我們今天請到謝柏讓老師", "來談大腦簡史這本書"])
    script = "我們今天請到謝伯讓老師，來談《大腦簡史》這本書。"
    corrected, flagged, stats = correct_srt_scripted(srt, script)
    assert "謝伯讓" in corrected
    assert "謝柏讓" not in corrected
    assert flagged == []
    assert stats["mode"] == "scripted"
    assert stats["coverage"] > 0.9


def test_scripted_keeps_timestamps_and_cue_count():
    srt = _srt(["我們今天請到謝柏讓老師", "來談大腦簡史這本書"])
    script = "我們今天請到謝伯讓老師，來談《大腦簡史》這本書。"
    corrected, _, _ = correct_srt_scripted(srt, script)
    assert corrected.count("-->") == 2
    assert "00:00:00,000 --> 00:00:02,000" in corrected
    assert "00:00:02,000 --> 00:00:04,000" in corrected


def test_scripted_flags_off_script_cue():
    # 第二個 cue 完全不在稿上（臨場發揮）→ 保留原文 + 進 flagged
    srt = _srt(["我們今天請到謝伯讓老師", "廠商贊助時間感謝收聽"])
    script = "我們今天請到謝伯讓老師。"
    corrected, flagged, stats = correct_srt_scripted(srt, script)
    assert "廠商贊助時間感謝收聽" in corrected
    assert len(flagged) == 1
    assert flagged[0]["line"] == 2
    assert stats["flagged"] == 1


def test_scripted_punctuation_becomes_space():
    srt = _srt(["今天天氣很好我們出發"])
    script = "今天天氣很好，我們出發。"
    corrected, _, _ = correct_srt_scripted(srt, script)
    assert "，" not in corrected
    assert "。" not in corrected


def test_scripted_empty_srt():
    corrected, flagged, stats = correct_srt_scripted("", "隨便的稿")
    assert corrected == ""
    assert stats["cues"] == 0


# ── llm 模式 ──


def _fake_ask_factory(responses: list[str], calls: list[dict]):
    def fake_ask(prompt, *, system=None, model=None, max_tokens=None, **kw):
        calls.append({"prompt": prompt, "system": system})
        return responses[len(calls) - 1]

    return fake_ask


def test_llm_chunked_merge(monkeypatch):
    srt = _srt(["第一句話", "第二句話", "第三句話"])
    unc3 = {"line": 3, "original": "第三句話", "suggestion": "x", "reason": "r", "risk": "low"}
    responses = [
        json.dumps({"corrections": {"1": "第一句話改"}, "uncertain": []}),
        json.dumps({"corrections": {"3": "第三句話改"}, "uncertain": [unc3]}),
    ]
    calls: list[dict] = []
    monkeypatch.setattr("shared.llm.ask", _fake_ask_factory(responses, calls))

    corrected, qc, stats = correct_srt_llm(srt, chunk_size=2, use_arbitration=False)
    assert len(calls) == 2
    assert "第一句話改" in corrected
    assert "第三句話改" in corrected
    assert "第二句話" in corrected
    assert stats["chunks"] == 2
    assert stats["corrections"] == 2
    assert len(qc) == 1


def test_llm_filters_out_of_chunk_seq(monkeypatch):
    # LLM 幻覺回了不在本 chunk 的序號 → 丟棄
    srt = _srt(["第一句話", "第二句話"])
    unc42 = {"line": 42, "original": "?", "suggestion": "?", "reason": "?", "risk": "low"}
    responses = [
        json.dumps({"corrections": {"1": "改一", "99": "亂改"}, "uncertain": [unc42]}),
    ]
    calls: list[dict] = []
    monkeypatch.setattr("shared.llm.ask", _fake_ask_factory(responses, calls))

    corrected, qc, stats = correct_srt_llm(srt, chunk_size=10, use_arbitration=False)
    assert "改一" in corrected
    assert "亂改" not in corrected
    assert qc == []
    assert stats["corrections"] == 1


def test_llm_refs_injected_into_system(monkeypatch, tmp_path):
    ref = tmp_path / "訪綱.md"
    ref.write_text("來賓：謝伯讓，主題：大腦簡史" + "x" * 50, encoding="utf-8")
    srt = _srt(["哈囉大家好"])
    responses = [json.dumps({"corrections": {}, "uncertain": []})]
    calls: list[dict] = []
    monkeypatch.setattr("shared.llm.ask", _fake_ask_factory(responses, calls))

    correct_srt_llm(srt, ref_files=[ref], use_arbitration=False)
    assert "謝伯讓" in calls[0]["system"]
    assert "訪綱.md" in calls[0]["system"]


def test_load_refs_text_cap_marks_truncation(tmp_path):
    ref = tmp_path / "long.md"
    ref.write_text("甲" * 100, encoding="utf-8")
    text = load_refs_text([ref], char_cap=10)
    assert "甲" * 10 in text
    assert "甲" * 11 not in text
    assert "已截斷" in text


# ── 報告 / 輔助 ──


def test_report_contains_stats_and_items():
    stats = {"mode": "llm", "cues": 10, "corrections": 3, "qc": 1}
    qc = [{"line": 5, "original": "原", "suggestion": "建", "reason": "理", "risk": "high"}]
    report = build_correction_report(stats, qc)
    assert "模式：llm" in report
    assert "Line 5" in report
    assert "HIGH" in report


def test_report_empty_qc():
    report = build_correction_report({"mode": "scripted", "cues": 5}, [])
    assert "（無）" in report


def test_find_script_file(tmp_path):
    a = tmp_path / "訪綱.md"
    b = tmp_path / "script.md"
    for p in (a, b):
        p.write_text("x", encoding="utf-8")
    assert find_script_file([a, b]) == b
    assert find_script_file([a]) is None

    c = tmp_path / "逐字稿v2.txt"
    c.write_text("x", encoding="utf-8")
    assert find_script_file([a, c]) == c
