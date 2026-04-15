"""shared/transcriber.py 單元測試。

測試不需要 GPU 或 FunASR 安裝的輔助函式。
整合測試（實際轉寫）需要 GPU 環境，標記為 slow。
"""

from unittest.mock import MagicMock, patch

import pytest

from shared.transcriber import (
    _correct_with_llm,
    _extract_hotwords,
    _extract_srt_texts,
    _funasr_to_srt,
    _get_ts_values,
    _process_srt_line,
    _remove_punctuation,
    _replace_srt_texts,
    _seconds_to_srt_ts,
    _split_sentences,
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
    ctx = tmp_path / "book.md"
    ctx.write_text("《NMN革命》作者李大華博士", encoding="utf-8")

    mock_response = "[1] 這是第一句\n[2] NMN是一種重要的分子\n[3] 李大華博士的研究"

    with patch("shared.anthropic_client.ask_claude", return_value=mock_response) as mock_ask:
        _correct_with_llm(_SAMPLE_SRT, context_files=[str(ctx)])
        # 確認 system prompt 包含 context
        call_kwargs = mock_ask.call_args
        assert "李大華" in call_kwargs.kwargs["system"]


# ── FunASR 時間戳解析 ──


def test_get_ts_values_list():
    assert _get_ts_values([100, 200]) == (100, 200)


def test_get_ts_values_tuple():
    assert _get_ts_values((100, 200)) == (100, 200)


def test_get_ts_values_dict():
    assert _get_ts_values({"start_time": 100, "end_time": 200}) == (100, 200)


# ── 句子拆分 ──


def test_split_sentences_basic():
    sentences = _split_sentences("今天天氣真好。我們去散步吧。")
    assert sentences == ["今天天氣真好。", "我們去散步吧。"]


def test_split_sentences_mixed_punctuation():
    sentences = _split_sentences("你好嗎？我很好！謝謝。")
    assert sentences == ["你好嗎？", "我很好！", "謝謝。"]


def test_split_sentences_no_punctuation():
    sentences = _split_sentences("沒有句尾標點的文字")
    assert sentences == ["沒有句尾標點的文字"]


def test_split_sentences_empty():
    assert _split_sentences("") == []


# ── FunASR → SRT 轉換 ──


def test_funasr_to_srt_with_sentence_info():
    """有 sentence_info 時，直接用它。"""
    results = [
        {
            "text": "今天天氣真好。我們去散步吧。",
            "sentence_info": [
                {"text": "今天天氣真好。", "start": 380, "end": 1560},
                {"text": "我們去散步吧。", "start": 1780, "end": 3200},
            ],
        }
    ]
    srt = _funasr_to_srt(results)

    assert "1\n00:00:00,380 --> 00:00:01,560\n今天天氣真好。" in srt
    assert "2\n00:00:01,780 --> 00:00:03,200\n我們去散步吧。" in srt


def test_funasr_to_srt_with_aligned_timestamps():
    """沒有 sentence_info，但 timestamps 與句子數量一致。"""
    results = [
        {
            "text": "今天天氣真好。我們去散步吧。",
            "timestamp": [[380, 1560], [1780, 3200]],
        }
    ]
    srt = _funasr_to_srt(results)

    assert "1\n00:00:00,380 --> 00:00:01,560\n今天天氣真好。" in srt
    assert "2\n00:00:01,780 --> 00:00:03,200\n我們去散步吧。" in srt


def test_funasr_to_srt_with_dict_timestamps():
    """時間戳是 dict 格式。"""
    results = [
        {
            "text": "測試文字。",
            "timestamp": [{"start_time": 500, "end_time": 2000}],
        }
    ]
    srt = _funasr_to_srt(results)

    assert "00:00:00,500 --> 00:00:02,000" in srt
    assert "測試文字。" in srt


def test_funasr_to_srt_unaligned_timestamps():
    """timestamps 數量與句子數不一致，用首尾包整段。"""
    results = [
        {
            "text": "一段很長的文字沒有標點",
            "timestamp": [[100, 200], [300, 400], [500, 1000]],
        }
    ]
    srt = _funasr_to_srt(results)

    assert "00:00:00,100 --> 00:00:01,000" in srt
    assert "一段很長的文字沒有標點" in srt


def test_funasr_to_srt_no_timestamps():
    """沒有時間戳，用 00:00:00 佔位。"""
    results = [{"text": "只有文字沒有時間戳"}]
    srt = _funasr_to_srt(results)

    assert "00:00:00,000 --> 00:00:00,000" in srt
    assert "只有文字沒有時間戳" in srt


def test_funasr_to_srt_empty():
    assert _funasr_to_srt([]) == ""
    assert _funasr_to_srt([{"text": ""}]) == ""


def test_funasr_to_srt_multiple_items():
    """多個結果項（VAD 切出的多段）。"""
    results = [
        {
            "text": "第一段。",
            "timestamp": [[0, 2000]],
        },
        {
            "text": "第二段。",
            "timestamp": [[3000, 5000]],
        },
    ]
    srt = _funasr_to_srt(results)

    assert "1\n" in srt
    assert "2\n" in srt
    assert "第一段。" in srt
    assert "第二段。" in srt


# ── transcribe() 主函式（mock FunASR）──


def test_transcribe_basic(tmp_path):
    """測試 transcribe() 的整合流程（mock FunASR + 跳過 Auphonic）。"""
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    # Mock FunASR model
    mock_model = MagicMock()
    mock_model.generate.return_value = [
        {
            "text": "这是简体中文的测试。软件开发很有趣。",
            "timestamp": [[1000, 3000], [3500, 6000]],
        }
    ]

    with patch("shared.transcriber._get_asr_model", return_value=mock_model):
        from shared.transcriber import transcribe

        result = transcribe(
            str(audio),
            output_dir=str(tmp_path),
            normalize_audio=False,  # 跳過 Auphonic
        )

    assert result.suffix == ".srt"
    assert result.exists()

    content = result.read_text(encoding="utf-8")
    # 應已轉為繁體
    assert "這是簡體中文的測試" in content
    # 預設無標點 — 中文標點應被移除
    assert "，" not in content
    assert "。" not in content


def test_transcribe_with_punctuation(tmp_path):
    """use_punctuation=True 時保留標點。"""
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    mock_model = MagicMock()
    mock_model.generate.return_value = [
        {
            "text": "今天天气真好。",
            "timestamp": [[0, 2000]],
        }
    ]

    with patch("shared.transcriber._get_asr_model", return_value=mock_model):
        from shared.transcriber import transcribe

        result = transcribe(
            str(audio),
            output_dir=str(tmp_path),
            normalize_audio=False,
            use_punctuation=True,
        )

    content = result.read_text(encoding="utf-8")
    assert "。" in content


def test_transcribe_with_normalize(tmp_path):
    """normalize_audio=True 時呼叫 Auphonic。"""
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    normalized = tmp_path / "test_normalized.wav"
    normalized.write_bytes(b"normalized audio")

    mock_model = MagicMock()
    mock_model.generate.return_value = [{"text": "测试。", "timestamp": [[0, 1000]]}]

    with (
        patch("shared.transcriber._get_asr_model", return_value=mock_model),
        patch("shared.auphonic.normalize", return_value=normalized) as mock_normalize,
    ):
        from shared.transcriber import transcribe

        transcribe(str(audio), output_dir=str(tmp_path), normalize_audio=True)

    mock_normalize.assert_called_once()


def test_transcribe_normalize_failure_continues(tmp_path):
    """Auphonic 失敗時應繼續用原始音檔。"""
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    mock_model = MagicMock()
    mock_model.generate.return_value = [{"text": "测试。", "timestamp": [[0, 1000]]}]

    with (
        patch("shared.transcriber._get_asr_model", return_value=mock_model),
        patch("shared.auphonic.normalize", side_effect=ValueError("No credits")),
    ):
        from shared.transcriber import transcribe

        result = transcribe(str(audio), output_dir=str(tmp_path), normalize_audio=True)

    # 即使 Auphonic 失敗，仍應產出 SRT
    assert result.exists()


def test_transcribe_file_not_found():
    """音檔不存在應 raise FileNotFoundError。"""
    from shared.transcriber import transcribe

    with pytest.raises(FileNotFoundError, match="音檔不存在"):
        transcribe("/nonexistent/audio.mp3")
