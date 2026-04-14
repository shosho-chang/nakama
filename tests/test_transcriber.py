"""shared/transcriber.py 單元測試。

測試不需要 GPU 或 openlrc 安裝的輔助函式。
整合測試（實際轉寫）需要 GPU 環境，標記為 slow。
"""

from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock

import pytest

from shared.transcriber import (
    _build_initial_prompt,
    _correct_with_llm,
    _extract_hotwords,
    _extract_srt_texts,
    _lrc_to_srt,
    _process_srt_line,
    _remove_punctuation,
    _replace_srt_texts,
    _seconds_to_srt_ts,
    _to_traditional,
)


# ── 時間戳轉換 ──


def test_seconds_to_srt_ts_zero():
    assert _seconds_to_srt_ts(0) == "00:00:00,000"


def test_seconds_to_srt_ts_simple():
    assert _seconds_to_srt_ts(65.5) == "00:01:05,500"


def test_seconds_to_srt_ts_hours():
    assert _seconds_to_srt_ts(3661.123) == "01:01:01,123"


# ── 標點移除 ──


def test_remove_punctuation_chinese():
    assert _remove_punctuation("你好，世界！") == "你好世界"


def test_remove_punctuation_keeps_english():
    assert _remove_punctuation("Hello, world!") == "Hello, world!"


def test_remove_punctuation_mixed():
    assert _remove_punctuation("NAD+是一種，重要的coenzyme。") == "NAD+是一種重要的coenzyme"


# ── 簡轉繁 ──


def test_to_traditional():
    assert _to_traditional("软件开发") == "軟件開發"


def test_to_traditional_already_traditional():
    result = _to_traditional("繁體中文")
    assert "繁體中文" == result


# ── SRT 行處理 ──


def test_process_srt_line_text_no_punctuation():
    result = _process_srt_line("这是简体中文，测试。", use_punctuation=False)
    assert "這是簡體中文" in result
    assert "，" not in result
    assert "。" not in result


def test_process_srt_line_text_with_punctuation():
    result = _process_srt_line("这是简体中文，测试。", use_punctuation=True)
    assert "，" in result


def test_process_srt_line_timestamp():
    line = "00:01:05,500 --> 00:01:10,000"
    assert _process_srt_line(line, use_punctuation=False) == line


def test_process_srt_line_sequence_number():
    assert _process_srt_line("42", use_punctuation=False) == "42"


def test_process_srt_line_empty():
    assert _process_srt_line("", use_punctuation=False) == ""


def test_process_srt_line_punctuation_only():
    result = _process_srt_line("……——", use_punctuation=False)
    assert result.strip() == ""  # 變成空格，不是完全空


# ── Context 處理 ──


def test_build_initial_prompt_no_files():
    result = _build_initial_prompt([])
    assert "繁體中文" in result


def test_build_initial_prompt_with_files(tmp_path):
    ctx = tmp_path / "context.txt"
    ctx.write_text("這本書討論了 NAD+ 和 NMN 的作用", encoding="utf-8")

    result = _build_initial_prompt([str(ctx)])
    assert "NAD+" in result
    assert "繁體中文" in result


def test_build_initial_prompt_missing_file():
    result = _build_initial_prompt(["/nonexistent/file.txt"])
    assert "繁體中文" in result


def test_extract_hotwords(tmp_path):
    ctx = tmp_path / "context.txt"
    ctx.write_text("《人體簡史》是一本好書，「NMN」是重要的分子", encoding="utf-8")

    hotwords = _extract_hotwords([str(ctx)])
    assert "人體簡史" in hotwords
    assert "NMN" in hotwords


def test_extract_hotwords_empty():
    assert _extract_hotwords([]) == []


# ── SRT 文字提取與替換 ──

_SAMPLE_SRT = """\
1
00:00:01,000 --> 00:00:05,000
這是第一句

2
00:00:05,000 --> 00:00:10,000
NMM是一種重要的分子

3
00:00:10,000 --> 00:00:15,000
李大華博士的研究
"""


def test_extract_srt_texts():
    entries = _extract_srt_texts(_SAMPLE_SRT)
    assert len(entries) == 3
    assert entries[0] == (1, "這是第一句")
    assert entries[1] == (2, "NMM是一種重要的分子")
    assert entries[2] == (3, "李大華博士的研究")


def test_extract_srt_texts_empty():
    assert _extract_srt_texts("") == []


def test_replace_srt_texts():
    corrected = {2: "NMN是一種重要的分子", 3: "李大華博士的研究成果"}
    result = _replace_srt_texts(_SAMPLE_SRT, corrected)
    assert "NMN是一種重要的分子" in result
    assert "李大華博士的研究成果" in result
    # 未修改的行保留
    assert "這是第一句" in result
    # 時間戳不變
    assert "00:00:05,000 --> 00:00:10,000" in result


def test_replace_srt_texts_no_changes():
    result = _replace_srt_texts(_SAMPLE_SRT, {})
    assert "這是第一句" in result
    assert "NMM是一種重要的分子" in result


# ── LLM 校正（mock Claude）──


def test_correct_with_llm_basic():
    """測試 LLM 校正的完整流程（mock ask_claude）。"""
    from unittest.mock import patch

    # Claude 回傳校正後的文字
    mock_response = "[1] 這是第一句\n[2] NMN是一種重要的分子\n[3] 李大華博士的研究"

    with patch("shared.anthropic_client.ask_claude", return_value=mock_response):
        result = _correct_with_llm(_SAMPLE_SRT, context_files=[], model="claude-haiku-4-5-20251001")

    assert "NMN是一種重要的分子" in result
    # 時間戳應保留
    assert "00:00:05,000 --> 00:00:10,000" in result


def test_correct_with_llm_empty_srt():
    """空 SRT 應直接回傳，不呼叫 Claude。"""
    result = _correct_with_llm("", context_files=[])
    assert result == ""


def test_correct_with_llm_with_context(tmp_path):
    """帶 context 檔案的校正。"""
    from unittest.mock import patch

    ctx = tmp_path / "book.md"
    ctx.write_text("《NMN革命》作者李大華博士", encoding="utf-8")

    mock_response = "[1] 這是第一句\n[2] NMN是一種重要的分子\n[3] 李大華博士的研究"

    with patch("shared.anthropic_client.ask_claude", return_value=mock_response) as mock_ask:
        _correct_with_llm(_SAMPLE_SRT, context_files=[str(ctx)])
        # 確認 system prompt 包含 context
        call_kwargs = mock_ask.call_args
        assert "李大華" in call_kwargs.kwargs["system"]


# ── LRC → SRT 轉換 ──


def test_lrc_to_srt(tmp_path):
    lrc = tmp_path / "test.lrc"
    lrc.write_text(
        dedent("""\
            [00:05.00] 第一句話
            [00:10.50] 第二句話
            [00:15.00] 第三句話
        """),
        encoding="utf-8",
    )

    srt = _lrc_to_srt(lrc)
    assert "1\n00:00:05,000 --> 00:00:10,500\n第一句話" in srt
    assert "2\n00:00:10,500 --> 00:00:15,000\n第二句話" in srt
    assert "3\n00:00:15,000 --> 00:00:18,000\n第三句話" in srt  # 最後一句 +3s


def test_lrc_to_srt_empty(tmp_path):
    lrc = tmp_path / "empty.lrc"
    lrc.write_text("", encoding="utf-8")
    assert _lrc_to_srt(lrc) == ""


# ── transcribe() 主函式（mock openlrc）──


def test_transcribe_basic(tmp_path):
    """測試 transcribe() 的整合流程（mock openlrc）。"""
    import sys
    import types

    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    # openlrc 會產生 .lrc 檔案
    lrc_output = tmp_path / "test.lrc"
    lrc_output.write_text(
        "[00:01.00] 这是简体中文的测试\n[00:05.00] 软件开发很有趣\n",
        encoding="utf-8",
    )

    # 建立 fake openlrc module
    fake_openlrc = types.ModuleType("openlrc")
    mock_lrcer = MagicMock()
    # run() 回傳路徑列表
    mock_lrcer.run.return_value = [str(lrc_output)]
    fake_openlrc.LRCer = MagicMock(return_value=mock_lrcer)
    fake_openlrc.TranscriptionConfig = MagicMock()
    fake_openlrc.TranslationConfig = MagicMock()
    fake_openlrc.ModelConfig = MagicMock()
    fake_openlrc.ModelProvider = MagicMock()
    sys.modules["openlrc"] = fake_openlrc

    try:
        import importlib

        import shared.transcriber

        importlib.reload(shared.transcriber)
        from shared.transcriber import transcribe

        result = transcribe(str(audio), output_dir=str(tmp_path))

        assert result.suffix == ".srt"
        assert result.exists()

        content = result.read_text(encoding="utf-8")
        # 應已轉為繁體
        assert "這是簡體中文的測試" in content
        # 預設無標點 — 中文標點應被移除
        assert "，" not in content
        assert "。" not in content
    finally:
        del sys.modules["openlrc"]


def test_transcribe_file_not_found():
    """音檔不存在應 raise FileNotFoundError（不需要 openlrc）。"""
    from shared.transcriber import transcribe

    with pytest.raises(FileNotFoundError, match="音檔不存在"):
        transcribe("/nonexistent/audio.mp3")
