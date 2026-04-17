"""shared.multimodal_arbiter 測試。

全 mock — 不打 Gemini 也不真的切音檔。
驗證：
- SRT 時間戳解析（序號 / ,ms 與 .ms / 多行文字）
- 動態 padding（短片段補 2s，長片段 1s）
- happy path：3 個 uncertain → 3 個 verdict，保序
- 單一失敗隔離：1 個 raise，其他照常，失敗那個 confidence=0
- 找不到 line：skip（不在結果中）
- 空 uncertainties：早 return []
- 音檔不存在 raise
- tempfile 清理：即使 Gemini 失敗也呼叫 unlink
"""

from __future__ import annotations

from pathlib import Path

import pytest

import shared.multimodal_arbiter as arb
from shared.multimodal_arbiter import (
    ArbitrationVerdict,
    _choose_padding,
    _parse_srt_index,
    arbitrate_uncertain,
)

SAMPLE_SRT = """1
00:00:00,000 --> 00:00:02,500
第一行文字

2
00:00:02,500 --> 00:00:05,000
第二行文字

3
00:00:05,000 --> 00:00:05,800
短片段

4
00:00:05,800 --> 00:00:10,000
第四行文字
換行繼續
"""


@pytest.fixture
def fake_audio(tmp_path: Path) -> Path:
    path = tmp_path / "audio.wav"
    path.write_bytes(b"RIFFfake")
    return path


def test_parse_srt_basic():
    index = _parse_srt_index(SAMPLE_SRT)
    assert set(index.keys()) == {1, 2, 3, 4}
    assert index[1] == (0.0, 2.5, "第一行文字")
    assert index[2] == (2.5, 5.0, "第二行文字")
    assert index[3] == (5.0, 5.8, "短片段")
    # 多行文字合併
    assert index[4][2] == "第四行文字 換行繼續"


def test_parse_srt_accepts_dot_ms():
    """WebVTT 風格的 . 毫秒分隔符也要吃。"""
    srt = "1\n00:00:01.500 --> 00:00:02.000\n文字\n"
    index = _parse_srt_index(srt)
    assert 1 in index
    assert index[1][0] == 1.5


def test_parse_srt_empty():
    assert _parse_srt_index("") == {}


def test_choose_padding_short():
    # 0.8 秒片段 → 2 秒 padding
    assert _choose_padding(5.0, 5.8) == 2.0


def test_choose_padding_long():
    # 2.5 秒片段 → 1 秒 padding
    assert _choose_padding(0.0, 2.5) == 1.0


def test_choose_padding_boundary():
    # 剛好 2 秒 → 1 秒 padding（< 門檻）
    assert _choose_padding(0.0, 2.0) == 1.0
    # 1.99 秒 → 2 秒 padding
    assert _choose_padding(0.0, 1.99) == 2.0


def test_empty_uncertainties_returns_empty(fake_audio):
    result = arbitrate_uncertain(fake_audio, SAMPLE_SRT, [])
    assert result == []


def test_missing_audio_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        arbitrate_uncertain(tmp_path / "nope.wav", SAMPLE_SRT, [{"line": 1}])


def test_happy_path(monkeypatch, fake_audio, tmp_path):
    """3 個 uncertain，全部成功，回傳按原順序。"""
    calls: list[dict] = []

    def fake_extract_clip(audio, start, end, *, padding, **kwargs):
        clip = tmp_path / f"clip_{start}.wav"
        clip.write_bytes(b"clip")
        calls.append({"start": start, "end": end, "padding": padding})
        return clip

    def fake_ask_gemini(clip_path, prompt, *, response_schema, model, system, temperature):
        # 從 prompt 找序號（靠時間戳字串識別）
        return response_schema(
            final_text="修正後",
            verdict="accept_suggestion",
            confidence=0.85,
            reasoning="聽起來是 B",
        )

    monkeypatch.setattr(arb, "extract_clip", fake_extract_clip)
    monkeypatch.setattr(arb, "ask_gemini_audio", fake_ask_gemini)
    monkeypatch.setattr(arb, "set_current_agent", lambda *a, **kw: None)

    uncertainties = [
        {"line": 1, "original": "A1", "suggestion": "B1", "reason": "r1"},
        {"line": 2, "original": "A2", "suggestion": "B2", "reason": "r2"},
        {"line": 3, "original": "A3", "suggestion": "B3", "reason": "r3"},
    ]
    result = arbitrate_uncertain(fake_audio, SAMPLE_SRT, uncertainties)

    assert len(result) == 3
    # 保序
    assert [v.line for v in result] == [1, 2, 3]
    assert all(v.verdict == "accept_suggestion" for v in result)
    assert all(v.confidence == 0.85 for v in result)
    # 短片段 line=3 拿到 2s padding，其他 1s
    paddings_by_start = {c["start"]: c["padding"] for c in calls}
    assert paddings_by_start[5.0] == 2.0  # line 3
    assert paddings_by_start[0.0] == 1.0  # line 1


def test_single_failure_isolated(monkeypatch, fake_audio, tmp_path):
    """line=2 的 Gemini 失敗，其他 2 個照常。失敗那個 confidence=0 + 保留 ASR 原文。"""
    clips: list[Path] = []

    def fake_extract_clip(audio, start, end, **kwargs):
        clip = tmp_path / f"clip_{start}.wav"
        clip.write_bytes(b"clip")
        clips.append(clip)
        return clip

    def fake_ask_gemini(clip_path, prompt, *, response_schema, **kwargs):
        if "時間區段：2.50" in prompt:
            raise RuntimeError("Gemini 壞了")
        return response_schema(
            final_text="ok", verdict="keep_original", confidence=0.9, reasoning="ok"
        )

    monkeypatch.setattr(arb, "extract_clip", fake_extract_clip)
    monkeypatch.setattr(arb, "ask_gemini_audio", fake_ask_gemini)
    monkeypatch.setattr(arb, "set_current_agent", lambda *a, **kw: None)

    uncertainties = [
        {"line": 1, "original": "A1", "suggestion": "B1", "reason": "r1"},
        {"line": 2, "original": "A2", "suggestion": "B2", "reason": "r2"},
        {"line": 3, "original": "A3", "suggestion": "B3", "reason": "r3"},
    ]
    result = arbitrate_uncertain(fake_audio, SAMPLE_SRT, uncertainties)

    assert len(result) == 3
    by_line = {v.line: v for v in result}
    assert by_line[1].confidence == 0.9
    assert by_line[2].confidence == 0.0
    assert by_line[2].verdict == "uncertain"
    assert by_line[2].final_text == "A2"  # 失敗時保留 ASR 原文
    assert "仲裁失敗" in by_line[2].reasoning
    assert by_line[3].confidence == 0.9

    # tempfile 全部被清掉（含失敗那個）
    assert all(not c.exists() for c in clips)


def test_line_not_in_srt_skipped(monkeypatch, fake_audio, tmp_path):
    def fake_extract_clip(audio, start, end, **kwargs):
        clip = tmp_path / "clip.wav"
        clip.write_bytes(b"clip")
        return clip

    def fake_ask_gemini(clip_path, prompt, *, response_schema, **kwargs):
        return response_schema(
            final_text="ok", verdict="keep_original", confidence=0.9, reasoning="ok"
        )

    monkeypatch.setattr(arb, "extract_clip", fake_extract_clip)
    monkeypatch.setattr(arb, "ask_gemini_audio", fake_ask_gemini)
    monkeypatch.setattr(arb, "set_current_agent", lambda *a, **kw: None)

    uncertainties = [
        {"line": 1, "original": "A1", "suggestion": "B1", "reason": "r1"},
        {"line": 999, "original": "X", "suggestion": "Y", "reason": "z"},  # SRT 沒有
    ]
    result = arbitrate_uncertain(fake_audio, SAMPLE_SRT, uncertainties)

    assert len(result) == 1
    assert result[0].line == 1


def test_prev_next_context_in_prompt(monkeypatch, fake_audio, tmp_path):
    """line=2 的 prompt 應該包含 line=1 的前文與 line=3 的後文。"""
    captured = {}

    def fake_extract_clip(audio, start, end, **kwargs):
        clip = tmp_path / "clip.wav"
        clip.write_bytes(b"clip")
        return clip

    def fake_ask_gemini(clip_path, prompt, *, response_schema, **kwargs):
        captured["prompt"] = prompt
        return response_schema(
            final_text="ok", verdict="keep_original", confidence=0.9, reasoning="ok"
        )

    monkeypatch.setattr(arb, "extract_clip", fake_extract_clip)
    monkeypatch.setattr(arb, "ask_gemini_audio", fake_ask_gemini)
    monkeypatch.setattr(arb, "set_current_agent", lambda *a, **kw: None)

    arbitrate_uncertain(
        fake_audio,
        SAMPLE_SRT,
        [{"line": 2, "original": "A2", "suggestion": "B2", "reason": "r2"}],
    )

    prompt = captured["prompt"]
    assert "第一行文字" in prompt  # 前文
    assert "短片段" in prompt  # 後文
    assert "A2" in prompt
    assert "B2" in prompt


def test_first_line_no_prev_context(monkeypatch, fake_audio, tmp_path):
    """line=1 沒有前文，prompt 應顯示「（無）」。"""
    captured = {}

    def fake_extract_clip(audio, start, end, **kwargs):
        clip = tmp_path / "clip.wav"
        clip.write_bytes(b"clip")
        return clip

    def fake_ask_gemini(clip_path, prompt, *, response_schema, **kwargs):
        captured["prompt"] = prompt
        return response_schema(
            final_text="ok", verdict="keep_original", confidence=0.9, reasoning="ok"
        )

    monkeypatch.setattr(arb, "extract_clip", fake_extract_clip)
    monkeypatch.setattr(arb, "ask_gemini_audio", fake_ask_gemini)
    monkeypatch.setattr(arb, "set_current_agent", lambda *a, **kw: None)

    arbitrate_uncertain(
        fake_audio,
        SAMPLE_SRT,
        [{"line": 1, "original": "A1", "suggestion": "B1", "reason": "r1"}],
    )

    assert "【前文】（無）" in captured["prompt"]


def test_tempfile_cleaned_on_extract_failure(monkeypatch, fake_audio):
    """extract_clip 自己失敗時，仲裁應回 fail verdict 不崩潰。"""

    def boom_extract(audio, start, end, **kwargs):
        raise RuntimeError("ffmpeg 掛了")

    monkeypatch.setattr(arb, "extract_clip", boom_extract)
    monkeypatch.setattr(arb, "ask_gemini_audio", lambda *a, **kw: None)
    monkeypatch.setattr(arb, "set_current_agent", lambda *a, **kw: None)

    result = arbitrate_uncertain(
        fake_audio,
        SAMPLE_SRT,
        [{"line": 1, "original": "A1", "suggestion": "B1", "reason": "r1"}],
    )
    assert len(result) == 1
    assert result[0].confidence == 0.0
    assert result[0].final_text == "A1"


def test_run_id_passed_to_cost_tracking(monkeypatch, fake_audio, tmp_path):
    captured = {}

    def fake_set_agent(agent, run_id=None):
        captured["agent"] = agent
        captured["run_id"] = run_id

    def fake_extract_clip(audio, start, end, **kwargs):
        clip = tmp_path / "clip.wav"
        clip.write_bytes(b"clip")
        return clip

    def fake_ask_gemini(clip_path, prompt, *, response_schema, **kwargs):
        return response_schema(
            final_text="ok", verdict="keep_original", confidence=0.9, reasoning="ok"
        )

    monkeypatch.setattr(arb, "set_current_agent", fake_set_agent)
    monkeypatch.setattr(arb, "extract_clip", fake_extract_clip)
    monkeypatch.setattr(arb, "ask_gemini_audio", fake_ask_gemini)

    arbitrate_uncertain(
        fake_audio,
        SAMPLE_SRT,
        [{"line": 1, "original": "A", "suggestion": "B", "reason": "r"}],
        run_id=42,
    )

    assert captured["agent"] == "transcriber-arbiter"
    assert captured["run_id"] == 42


def test_arbitration_verdict_schema():
    """Pydantic schema 驗證：confidence 必須 0–1。"""
    with pytest.raises(Exception):
        ArbitrationVerdict(
            line=1, final_text="x", verdict="keep_original", confidence=1.5, reasoning="x"
        )
    # 合法值
    v = ArbitrationVerdict(
        line=1, final_text="x", verdict="keep_original", confidence=0.5, reasoning="x"
    )
    assert v.confidence == 0.5
