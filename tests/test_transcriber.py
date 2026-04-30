"""shared/transcriber.py 單元測試。

測試不需要 GPU 或 FunASR 安裝的輔助函式。
整合測試（實際轉寫）需要 GPU 環境，標記為 slow。
"""

from unittest.mock import MagicMock, patch

import pytest

from shared.transcriber import (
    _add_pinyin,
    _build_initial_prompt,
    _correct_with_llm,
    _extract_hotwords,
    _extract_project_context,
    _extract_srt_texts,
    _parse_llm_response,
    _process_srt_line,
    _remove_punctuation,
    _replace_srt_texts,
    _seconds_to_srt_ts,
    _split_sentences,
    _to_traditional,
    _whisperx_to_srt,
    _write_qc_report,
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
    assert _remove_punctuation("你好，世界！") == "你好 世界"


def test_remove_punctuation_english_in_mixed():
    """code-switch 文字裡的 ASCII 標點視為子句斷點 / 句尾，一律移除（per LLM prompt
    `輸出文字不要包含任何標點符號`）。"""
    assert _remove_punctuation("Hello, world!") == "Hello world"


def test_remove_punctuation_mixed():
    """中英混合：中文 + ASCII 標點都清掉，`+` 等運算符保留（不是標點）。"""
    assert _remove_punctuation("NAD+是一種，重要的coenzyme。") == "NAD+是一種 重要的coenzyme"
    assert _remove_punctuation("跟Paul,有一個聚會") == "跟Paul 有一個聚會"
    assert _remove_punctuation("Traveling Village.") == "Traveling Village"


# ── 簡轉繁 ──


def test_to_traditional():
    # s2twp mode：除了字形也轉台灣慣用詞彙 — 「软件」→「軟體」（非大陸式「軟件」）
    assert _to_traditional("软件开发") == "軟體開發"


def test_to_traditional_taiwan_vocab():
    """s2twp 應做台灣詞彙轉換（不只字形）。"""
    # 大陸：信息 / 台灣：資訊
    assert _to_traditional("信息") == "資訊"
    # 大陸：网络 / 台灣：網路
    assert _to_traditional("网络") == "網路"


def test_to_traditional_already_traditional():
    result = _to_traditional("繁體中文")
    assert "繁體中文" == result


# ── SRT 行處理 ──


def test_process_srt_line_text_removes_punctuation():
    result = _process_srt_line("这是简体中文，测试。")
    assert "這是簡體中文" in result
    assert "，" not in result
    assert "。" not in result


def test_process_srt_line_timestamp():
    line = "00:01:05,500 --> 00:01:10,000"
    assert _process_srt_line(line) == line


def test_process_srt_line_sequence_number():
    assert _process_srt_line("42") == "42"


def test_process_srt_line_empty():
    assert _process_srt_line("") == ""


def test_process_srt_line_punctuation_only():
    result = _process_srt_line("……——")
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
    """測試 LLM 校正的完整流程（mock shared.llm.ask，JSON 格式回傳）。"""
    mock_response = '{"corrections": {"2": "NMN是一種重要的分子"}, "uncertain": []}'

    with patch("shared.llm.ask", return_value=mock_response):
        result, uncertainties = _correct_with_llm(
            _SAMPLE_SRT, context_files=[], model="claude-opus-4-7"
        )

    assert "NMN是一種重要的分子" in result
    assert "00:00:05,000 --> 00:00:10,000" in result
    assert uncertainties == []


def test_correct_with_llm_empty_srt():
    """空 SRT 應直接回傳，不呼叫 Claude。"""
    result, uncertainties = _correct_with_llm("", context_files=[])
    assert result == ""
    assert uncertainties == []


def test_correct_with_llm_with_context(tmp_path):
    """帶 context 檔案的校正。"""
    ctx = tmp_path / "book.md"
    ctx.write_text("《NMN革命》作者李大華博士", encoding="utf-8")

    mock_response = '{"corrections": {"2": "NMN是一種重要的分子"}, "uncertain": []}'

    with patch("shared.llm.ask", return_value=mock_response) as mock_ask:
        _correct_with_llm(_SAMPLE_SRT, context_files=[str(ctx)])
        call_kwargs = mock_ask.call_args
        assert "李大華" in call_kwargs.kwargs["system"]


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


# ── WhisperX → SRT 轉換 ──


def test_whisperx_to_srt_basic():
    """WhisperX segments 直接轉 SRT。"""
    segments = [
        {"start": 0.38, "end": 1.56, "text": "今天天氣真好"},
        {"start": 1.78, "end": 3.2, "text": "我們去散步吧"},
    ]
    srt = _whisperx_to_srt(segments)

    assert "1\n00:00:00,380 --> 00:00:01,560\n今天天氣真好" in srt
    assert "2\n00:00:01,780 --> 00:00:03,200\n我們去散步吧" in srt


def test_whisperx_to_srt_with_speakers():
    """diarization 後 segment 帶 speaker，SRT 加 [SPEAKER_XX] prefix。"""
    segments = [
        {"start": 0.0, "end": 1.0, "text": "你好嗎", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "我很好", "speaker": "SPEAKER_01"},
    ]
    srt = _whisperx_to_srt(segments, with_speakers=True)

    assert "[SPEAKER_00] 你好嗎" in srt
    assert "[SPEAKER_01] 我很好" in srt


def test_whisperx_to_srt_speaker_missing_fallback():
    """diarization 漏 assign 某 segment 時用 SPEAKER_?? 佔位。"""
    segments = [{"start": 0.0, "end": 1.0, "text": "未知說話人"}]
    srt = _whisperx_to_srt(segments, with_speakers=True)
    assert "[SPEAKER_??] 未知說話人" in srt


def test_whisperx_to_srt_empty():
    assert _whisperx_to_srt([]) == ""
    assert _whisperx_to_srt([{"start": 0, "end": 1, "text": ""}]) == ""


def test_whisperx_to_srt_strips_whitespace():
    segments = [{"start": 0.0, "end": 1.0, "text": "  含前後空白  "}]
    srt = _whisperx_to_srt(segments)
    assert "含前後空白" in srt
    assert "  含前後空白" not in srt


# ── _build_initial_prompt ──


def test_build_initial_prompt_full():
    prompt = _build_initial_prompt(
        hotwords=["Traveling Village", "Paul"],
        project_context={"guest_name": "張安吉", "topic": "數位遊牧"},
        host_name="張修修",
        show_name="不正常人類研究所",
    )
    assert "節目：不正常人類研究所" in prompt
    assert "主持人：張修修" in prompt
    assert "來賓：張安吉" in prompt
    assert "主題：數位遊牧" in prompt
    assert "Traveling Village" in prompt
    assert "Paul" in prompt
    assert prompt.endswith("。")


def test_build_initial_prompt_empty():
    """全部 None / 空 → 空字串。"""
    assert _build_initial_prompt([], None) == ""


def test_build_initial_prompt_partial():
    """只有 hotwords 也應產出。"""
    prompt = _build_initial_prompt(["Foo Bar"], None)
    assert "Foo Bar" in prompt


# ── transcribe() 主函式（mock WhisperX）──


def _mock_whisperx_model(segments: list[dict], language: str = "zh") -> MagicMock:
    """組一個 mock WhisperX model：`.transcribe()` 回 {segments, language} dict。"""
    m = MagicMock()
    m.transcribe.return_value = {"segments": segments, "language": language}
    return m


def test_transcribe_basic(tmp_path):
    """測試 transcribe() 的整合流程（mock WhisperX + 跳過 Auphonic + 跳過 diar）。"""
    pytest.importorskip("whisperx")
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    mock_model = _mock_whisperx_model(
        [
            {"start": 1.0, "end": 3.0, "text": "这是简体中文的测试。"},
            {"start": 3.5, "end": 6.0, "text": "软件开发很有趣。"},
        ]
    )

    with (
        patch("shared.transcriber._get_asr_model", return_value=mock_model),
        patch("whisperx.load_audio", return_value=b"fake audio array"),
    ):
        from shared.transcriber import transcribe

        result = transcribe(
            str(audio),
            output_dir=str(tmp_path),
            normalize_audio=False,
            use_diarization=False,
        )

    assert result.suffix == ".srt"
    assert result.exists()

    content = result.read_text(encoding="utf-8")
    # 應已轉為繁體（s2twp 模式）
    assert "這是簡體中文的測試" in content
    # 預設無標點 — 中文標點應被移除
    assert "，" not in content
    assert "。" not in content


def test_transcribe_with_normalize(tmp_path):
    """normalize_audio=True 時呼叫 Auphonic。"""
    pytest.importorskip("whisperx")
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    normalized = tmp_path / "test_normalized.wav"
    normalized.write_bytes(b"normalized audio")

    mock_model = _mock_whisperx_model([{"start": 0, "end": 1, "text": "测试。"}])

    with (
        patch("shared.transcriber._get_asr_model", return_value=mock_model),
        patch("whisperx.load_audio", return_value=b"fake"),
        patch("shared.auphonic.normalize", return_value=normalized) as mock_normalize,
    ):
        from shared.transcriber import transcribe

        transcribe(
            str(audio),
            output_dir=str(tmp_path),
            normalize_audio=True,
            use_diarization=False,
        )

    mock_normalize.assert_called_once()


def test_transcribe_normalize_failure_continues(tmp_path):
    """Auphonic 失敗時應繼續用原始音檔。"""
    pytest.importorskip("whisperx")
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    mock_model = _mock_whisperx_model([{"start": 0, "end": 1, "text": "测试。"}])

    with (
        patch("shared.transcriber._get_asr_model", return_value=mock_model),
        patch("whisperx.load_audio", return_value=b"fake"),
        patch("shared.auphonic.normalize", side_effect=ValueError("No credits")),
    ):
        from shared.transcriber import transcribe

        result = transcribe(
            str(audio),
            output_dir=str(tmp_path),
            normalize_audio=True,
            use_diarization=False,
        )

    # 即使 Auphonic 失敗，仍應產出 SRT
    assert result.exists()


def test_transcribe_with_llm_correction_writes_qc(tmp_path):
    """transcribe() 開 LLM 校正 + uncertainties 時產出 .qc.md。"""
    pytest.importorskip("whisperx")
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    mock_model = _mock_whisperx_model([{"start": 0, "end": 1, "text": "测试文字。"}])

    mock_response = (
        '{"corrections": {}, "uncertain": ['
        '{"line": 1, "original": "測試文字", "suggestion": "測試問題",'
        ' "reason": "語意不確定", "risk": "medium"}'
        "]}"
    )

    with (
        patch("shared.transcriber._get_asr_model", return_value=mock_model),
        patch("whisperx.load_audio", return_value=b"fake"),
        patch("shared.llm.ask", return_value=mock_response),
    ):
        from shared.transcriber import transcribe

        result = transcribe(
            str(audio),
            output_dir=str(tmp_path),
            normalize_audio=False,
            use_llm_correction=True,
            use_diarization=False,
        )

    assert result.exists()
    qc_path = tmp_path / "test.qc.md"
    assert qc_path.exists()
    qc_content = qc_path.read_text(encoding="utf-8")
    assert "MEDIUM" in qc_content
    assert "測試問題" in qc_content


def test_transcribe_file_not_found():
    """音檔不存在應 raise FileNotFoundError。"""
    from shared.transcriber import transcribe

    with pytest.raises(FileNotFoundError, match="音檔不存在"):
        transcribe("/nonexistent/audio.mp3")


def test_transcribe_strips_llm_reintroduced_punctuation(tmp_path):
    """LLM 校正若加回標點，Pass 2 必須把它清掉（防止標點誤導下游 LLM 語氣判斷）。"""
    pytest.importorskip("whisperx")
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    mock_model = _mock_whisperx_model([{"start": 0, "end": 2.0, "text": "这是第一句。"}])

    # LLM 把一整段帶標點回傳（含逗號、句號、問號、驚嘆號）
    mock_response = '{"corrections": {"1": "這是第一句，真的嗎？太好了！"}, "uncertain": []}'

    with (
        patch("shared.transcriber._get_asr_model", return_value=mock_model),
        patch("whisperx.load_audio", return_value=b"fake"),
        patch("shared.llm.ask", return_value=mock_response),
    ):
        from shared.transcriber import transcribe

        result = transcribe(
            str(audio),
            output_dir=str(tmp_path),
            normalize_audio=False,
            use_llm_correction=True,
            use_multimodal_arbitration=False,
            use_diarization=False,
        )

    content = result.read_text(encoding="utf-8")
    for punct in ["，", "。", "？", "！", "、", "；", "："]:
        assert punct not in content, f"標點 {punct} 應被 Pass 2 過濾掉"


# ── Pinyin 輔助 ──


def test_add_pinyin_chinese():
    result = _add_pinyin("你好")
    assert "你好" in result
    assert "(" in result
    assert "nǐ hǎo" in result


def test_add_pinyin_english_only():
    assert _add_pinyin("Hello World") == "Hello World"


def test_add_pinyin_mixed():
    result = _add_pinyin("NAD+是好的")
    assert "NAD+是好的" in result
    assert "(" in result


def test_add_pinyin_empty():
    assert _add_pinyin("") == ""


# ── LLM 回傳解析 ──


def test_parse_llm_response_json():
    raw = '{"corrections": {"1": "修正文字", "3": "另一個修正"}, "uncertain": []}'
    corrections, uncertainties = _parse_llm_response(raw, 5)
    assert corrections == {1: "修正文字", 3: "另一個修正"}
    assert uncertainties == []


def test_parse_llm_response_json_with_uncertain():
    raw = (
        '{"corrections": {"2": "數位行銷"}, '
        '"uncertain": [{"line": 5, "original": "原文", '
        '"suggestion": "建議", "reason": "不確定", "risk": "high"}]}'
    )
    corrections, uncertainties = _parse_llm_response(raw, 10)
    assert corrections == {2: "數位行銷"}
    assert len(uncertainties) == 1
    assert uncertainties[0]["risk"] == "high"


def test_parse_llm_response_json_with_code_fence():
    raw = '```json\n{"corrections": {"1": "修正"}, "uncertain": []}\n```'
    corrections, uncertainties = _parse_llm_response(raw, 3)
    assert corrections == {1: "修正"}


def test_parse_llm_response_fallback_regex():
    """JSON 解析失敗時 fallback 到 regex。"""
    raw = "[1] 這是第一句\n[2] NMN是一種重要的分子"
    corrections, uncertainties = _parse_llm_response(raw, 3)
    assert corrections == {1: "這是第一句", 2: "NMN是一種重要的分子"}
    assert uncertainties == []


def test_parse_llm_response_unparseable():
    """完全無法解析時回傳空。"""
    raw = "這是一段無法解析的回傳"
    corrections, uncertainties = _parse_llm_response(raw, 3)
    assert corrections == {}
    assert uncertainties == []


# ── LLM 校正進階 ──


def test_correct_with_llm_with_uncertainties():
    """測試 uncertain 項目正確回傳。"""
    mock_response = (
        '{"corrections": {"2": "NMN是一種重要的分子"}, '
        '"uncertain": [{"line": 3, "original": "李大華博士的研究", '
        '"suggestion": "李大花博士的研究", "reason": "人名不確定", "risk": "high"}]}'
    )

    with patch("shared.llm.ask", return_value=mock_response):
        result, uncertainties = _correct_with_llm(_SAMPLE_SRT, context_files=[])

    assert "NMN是一種重要的分子" in result
    assert len(uncertainties) == 1
    assert uncertainties[0]["line"] == 3
    assert uncertainties[0]["risk"] == "high"


def test_correct_with_llm_host_show_name():
    """host_name/show_name 出現在 system prompt。"""
    mock_response = '{"corrections": {}, "uncertain": []}'

    with patch("shared.llm.ask", return_value=mock_response) as mock_ask:
        _correct_with_llm(
            _SAMPLE_SRT,
            context_files=[],
            host_name="張修修",
            show_name="不正常人類研究所",
        )
        call_kwargs = mock_ask.call_args
        system = call_kwargs.kwargs["system"]
        assert "張修修" in system
        assert "不正常人類研究所" in system


def test_correct_with_llm_pinyin_in_prompt():
    """確認 prompt 中包含拼音標注。"""
    mock_response = '{"corrections": {}, "uncertain": []}'

    with patch("shared.llm.ask", return_value=mock_response) as mock_ask:
        _correct_with_llm(_SAMPLE_SRT, context_files=[])
        call_args = mock_ask.call_args
        prompt = call_args.args[0] if call_args.args else call_args.kwargs.get("prompt", "")
        # SRT 包含中文，prompt 應有拼音
        assert "(" in prompt


def test_correct_with_llm_project_context():
    """帶 project_context 的校正。"""
    mock_response = '{"corrections": {}, "uncertain": []}'
    proj_ctx = {
        "guest_name": "陳博士",
        "topic": "NMN 抗老化",
        "context_text": "NMN 是一種 NAD+ 前驅物",
    }

    with patch("shared.llm.ask", return_value=mock_response) as mock_ask:
        _correct_with_llm(_SAMPLE_SRT, context_files=[], project_context=proj_ctx)
        call_kwargs = mock_ask.call_args
        system = call_kwargs.kwargs["system"]
        assert "陳博士" in system
        assert "NMN 抗老化" in system


# ── QC 報告 ──


def test_write_qc_report(tmp_path):
    qc_path = tmp_path / "test.qc.md"
    uncertainties = [
        {
            "line": 5,
            "original": "蘇味行銷",
            "suggestion": "數位行銷",
            "reason": "同音字",
            "risk": "high",
        },
        {
            "line": 12,
            "original": "某個詞",
            "suggestion": "另一個詞",
            "reason": "語意推測",
            "risk": "low",
        },
    ]
    _write_qc_report(qc_path, uncertainties)

    content = qc_path.read_text(encoding="utf-8")
    assert "# QC 報告" in content
    assert "[HIGH] Line 5" in content
    assert "蘇味行銷" in content
    assert "數位行銷" in content
    assert "[LOW] Line 12" in content


# ── Project Context 提取 ──


def test_extract_project_context_podcast(tmp_path):
    """從 podcast project 檔案正確提取 guest、topic、sections。"""
    proj = tmp_path / "NMN抗老化.md"
    proj.write_text(
        "---\n"
        "type: project\n"
        "content_type: podcast\n"
        "guest: 陳博士\n"
        "category: 健康\n"
        "---\n"
        "\n"
        "# NMN抗老化\n"
        "\n"
        "## Research Dropbox\n"
        "\n"
        "NMN 是 NAD+ 的前驅物，可延緩老化。\n"
        "\n"
        "## Script\n"
        "\n"
        "Q1: 什麼是 NMN？\n"
        "Q2: 誰適合補充？\n"
        "\n"
        "## 不相關的區塊\n"
        "\n"
        "這段不應該被提取。\n",
        encoding="utf-8",
    )

    result = _extract_project_context(str(proj))
    assert result["guest_name"] == "陳博士"
    assert result["topic"] == "NMN抗老化"
    assert "NAD+" in result["context_text"]
    assert "什麼是 NMN" in result["context_text"]
    assert "不應該被提取" not in result["context_text"]


def test_extract_project_context_skips_codeblocks(tmp_path):
    """DataviewJS code blocks 被跳過。"""
    proj = tmp_path / "test.md"
    proj.write_text(
        "---\n"
        "guest: 王醫師\n"
        "---\n"
        "\n"
        "## Script\n"
        "\n"
        "重要的訪談大綱\n"
        "\n"
        "```dataviewjs\n"
        "const x = dv.current();\n"
        "這不應該被提取\n"
        "```\n"
        "\n"
        "大綱繼續\n",
        encoding="utf-8",
    )

    result = _extract_project_context(str(proj))
    assert result["guest_name"] == "王醫師"
    assert "重要的訪談大綱" in result["context_text"]
    assert "dv.current" not in result["context_text"]
    assert "大綱繼續" in result["context_text"]


def test_extract_project_context_missing_file():
    """檔案不存在時回傳空 dict。"""
    result = _extract_project_context("/nonexistent/project.md")
    assert result["guest_name"] == ""
    assert result["topic"] == ""
    assert result["context_text"] == ""


# ── 多模態仲裁整合（PR-D）──


def _make_verdict(line, verdict, final_text, confidence=0.9, reasoning="仲裁理由"):
    """組 ArbitrationVerdict 的測試輔助。"""
    from shared.multimodal_arbiter import ArbitrationVerdict

    return ArbitrationVerdict(
        line=line,
        final_text=final_text,
        verdict=verdict,
        confidence=confidence,
        reasoning=reasoning,
    )


def test_correct_with_llm_no_uncertainties_skips_arbitration(tmp_path):
    """零 uncertain 時不呼叫仲裁器。"""
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake")

    mock_response = '{"corrections": {"2": "NMN是一種重要的分子"}, "uncertain": []}'

    with (
        patch("shared.llm.ask", return_value=mock_response),
        patch("shared.multimodal_arbiter.arbitrate_uncertain") as mock_arb,
    ):
        result, qc_items = _correct_with_llm(
            _SAMPLE_SRT, context_files=[], audio_path=audio, use_arbitration=True
        )

    assert mock_arb.call_count == 0
    assert qc_items == []
    assert "NMN是一種重要的分子" in result


def test_correct_with_llm_arbitration_disabled(tmp_path):
    """use_arbitration=False 時不呼叫仲裁器，uncertainties 直接進 qc_items。"""
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake")

    mock_response = (
        '{"corrections": {}, "uncertain": ['
        '{"line": 3, "original": "李大華博士的研究", '
        '"suggestion": "李大花博士的研究", "reason": "人名", "risk": "high"}]}'
    )

    with (
        patch("shared.llm.ask", return_value=mock_response),
        patch("shared.multimodal_arbiter.arbitrate_uncertain") as mock_arb,
    ):
        _, qc_items = _correct_with_llm(
            _SAMPLE_SRT, context_files=[], audio_path=audio, use_arbitration=False
        )

    assert mock_arb.call_count == 0
    assert len(qc_items) == 1
    assert "verdict" not in qc_items[0]


def test_correct_with_llm_no_audio_skips_arbitration():
    """audio_path=None 時不呼叫仲裁器。"""
    mock_response = (
        '{"corrections": {}, "uncertain": ['
        '{"line": 3, "original": "李大華博士的研究", '
        '"suggestion": "李大花博士的研究", "reason": "人名", "risk": "high"}]}'
    )

    with (
        patch("shared.llm.ask", return_value=mock_response),
        patch("shared.multimodal_arbiter.arbitrate_uncertain") as mock_arb,
    ):
        _, qc_items = _correct_with_llm(
            _SAMPLE_SRT, context_files=[], audio_path=None, use_arbitration=True
        )

    assert mock_arb.call_count == 0
    assert len(qc_items) == 1


def test_correct_with_llm_applies_verdicts(tmp_path):
    """四種 verdict 各覆蓋一條，驗 corrections dict 結果。"""
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake")

    # Pass 1：四條 uncertain（line 1/2/3 在 _SAMPLE_SRT 內，加一條模擬多行）
    # _SAMPLE_SRT 只有 3 行，我們測 keep_original / accept_suggestion / other
    mock_response = (
        '{"corrections": {'
        '"1": "這是第一句修改版",'  # accept_suggestion，Pass 1 已套
        '"2": "NMN是一種重要的分子",'  # other，仲裁改掉
        '"3": "李大花博士的研究"'  # keep_original，仲裁撤銷 Pass 1 修改
        "}, "
        '"uncertain": ['
        '{"line": 1, "original": "這是第一句", '
        '"suggestion": "這是第一句修改版", "reason": "r1", "risk": "low"},'
        '{"line": 2, "original": "NMM是一種重要的分子", '
        '"suggestion": "NMN是一種重要的分子", "reason": "r2", "risk": "medium"},'
        '{"line": 3, "original": "李大華博士的研究", '
        '"suggestion": "李大花博士的研究", "reason": "r3", "risk": "high"}'
        "]}"
    )

    verdicts = [
        _make_verdict(1, "accept_suggestion", "這是第一句修改版", confidence=0.9),
        _make_verdict(2, "other", "NMN才是正確的分子", confidence=0.95),
        _make_verdict(3, "keep_original", "李大華博士的研究", confidence=0.85),
    ]

    with (
        patch("shared.llm.ask", return_value=mock_response),
        patch("shared.multimodal_arbiter.arbitrate_uncertain", return_value=verdicts),
    ):
        result, qc_items = _correct_with_llm(
            _SAMPLE_SRT, context_files=[], audio_path=audio, use_arbitration=True
        )

    # line 1: Pass 1 的 suggestion 被保留
    assert "這是第一句修改版" in result
    # line 2: Gemini 的 final_text 覆蓋
    assert "NMN才是正確的分子" in result
    assert "NMN是一種重要的分子" not in result
    # line 3: ASR 原文還原
    assert "李大華博士的研究" in result
    assert "李大花博士" not in result
    # 所有 confidence >= 0.6 且非 uncertain → qc_items 空
    assert qc_items == []


def test_correct_with_llm_low_confidence_goes_to_qc(tmp_path):
    """低信心 verdict 即便套用了也進 QC。"""
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake")

    mock_response = (
        '{"corrections": {"2": "NMN是一種重要的分子"}, '
        '"uncertain": [{"line": 2, "original": "NMM是一種重要的分子", '
        '"suggestion": "NMN是一種重要的分子", "reason": "r", "risk": "medium"}]}'
    )

    verdicts = [
        _make_verdict(2, "accept_suggestion", "NMN是一種重要的分子", confidence=0.3),
    ]

    with (
        patch("shared.llm.ask", return_value=mock_response),
        patch("shared.multimodal_arbiter.arbitrate_uncertain", return_value=verdicts),
    ):
        _, qc_items = _correct_with_llm(
            _SAMPLE_SRT, context_files=[], audio_path=audio, use_arbitration=True
        )

    assert len(qc_items) == 1
    assert qc_items[0]["line"] == 2
    assert qc_items[0]["confidence"] == 0.3
    assert qc_items[0]["verdict"] == "accept_suggestion"


def test_correct_with_llm_accept_suggestion_not_in_pass1_corrections(tmp_path):
    """accept_suggestion 時即便 Pass 1 沒把 suggestion 放進 corrections 也要套用。

    Pass 1 的 prompt 要求 Opus「uncertain 項目不要硬改」— 所以 suggestion 通常
    只在 uncertainties dict，而非 corrections。若 Gemini 仲裁 accept_suggestion，
    必須顯式寫入 corrections，否則最終 SRT 會用 ASR 原文。
    """
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake")

    # Pass 1：line 3 被標 uncertain，但 Opus 沒放進 corrections（符合 prompt 指示）
    mock_response = (
        '{"corrections": {}, '
        '"uncertain": [{"line": 3, "original": "李大華博士的研究", '
        '"suggestion": "李大花博士的研究", "reason": "人名", "risk": "high"}]}'
    )
    verdicts = [_make_verdict(3, "accept_suggestion", "李大花博士的研究", confidence=0.9)]

    with (
        patch("shared.llm.ask", return_value=mock_response),
        patch("shared.multimodal_arbiter.arbitrate_uncertain", return_value=verdicts),
    ):
        result, qc_items = _correct_with_llm(
            _SAMPLE_SRT, context_files=[], audio_path=audio, use_arbitration=True
        )

    # Gemini 仲裁的 suggestion 必須出現在最終 SRT
    assert "李大花博士的研究" in result
    assert "李大華博士的研究" not in result
    assert qc_items == []


def test_correct_with_llm_uncertain_verdict_drops_correction(tmp_path):
    """verdict=uncertain 時撤銷 Pass 1 修改，還原 ASR 原文，並進 QC。"""
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake")

    mock_response = (
        '{"corrections": {"2": "NMN是一種重要的分子"}, '
        '"uncertain": [{"line": 2, "original": "NMM是一種重要的分子", '
        '"suggestion": "NMN是一種重要的分子", "reason": "r", "risk": "high"}]}'
    )
    verdicts = [_make_verdict(2, "uncertain", "NMM是一種重要的分子", confidence=0.0)]

    with (
        patch("shared.llm.ask", return_value=mock_response),
        patch("shared.multimodal_arbiter.arbitrate_uncertain", return_value=verdicts),
    ):
        result, qc_items = _correct_with_llm(
            _SAMPLE_SRT, context_files=[], audio_path=audio, use_arbitration=True
        )

    # Pass 1 的修改被撤銷、ASR 原文保留
    assert "NMM是一種重要的分子" in result
    assert "NMN是一種重要的分子" not in result
    assert len(qc_items) == 1
    assert qc_items[0]["verdict"] == "uncertain"


def test_correct_with_llm_refused_verdict_drops_correction_and_qc(tmp_path):
    """verdict=refused（拒答）行為同 uncertain：撤銷 Pass 1 修改 + 進 QC。"""
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake")

    mock_response = (
        '{"corrections": {"2": "NMN是一種重要的分子"}, '
        '"uncertain": [{"line": 2, "original": "NMM是一種重要的分子", '
        '"suggestion": "NMN是一種重要的分子", "reason": "r", "risk": "high"}]}'
    )
    # 模擬 arbiter 偵測到拒答後回傳的 verdict
    verdicts = [_make_verdict(2, "refused", "NMM是一種重要的分子", confidence=0.0)]

    with (
        patch("shared.llm.ask", return_value=mock_response),
        patch("shared.multimodal_arbiter.arbitrate_uncertain", return_value=verdicts),
    ):
        result, qc_items = _correct_with_llm(
            _SAMPLE_SRT, context_files=[], audio_path=audio, use_arbitration=True
        )

    # Pass 1 的修改被撤銷、ASR 原文保留
    assert "NMM是一種重要的分子" in result
    assert "NMN是一種重要的分子" not in result
    assert len(qc_items) == 1
    assert qc_items[0]["verdict"] == "refused"


def test_correct_with_llm_arbitration_raises_fallback(tmp_path):
    """arbitrate_uncertain 整批 raise → 退回舊流程（uncertainties 直接進 QC）。"""
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake")

    mock_response = (
        '{"corrections": {}, "uncertain": ['
        '{"line": 3, "original": "李大華博士的研究", '
        '"suggestion": "李大花博士的研究", "reason": "人名", "risk": "high"}]}'
    )

    with (
        patch("shared.llm.ask", return_value=mock_response),
        patch(
            "shared.multimodal_arbiter.arbitrate_uncertain",
            side_effect=FileNotFoundError("音檔不見了"),
        ),
    ):
        _, qc_items = _correct_with_llm(
            _SAMPLE_SRT, context_files=[], audio_path=audio, use_arbitration=True
        )

    # 退回舊流程：原始 uncertainties 直接進 qc_items（無 verdict 欄位）
    assert len(qc_items) == 1
    assert "verdict" not in qc_items[0]
    assert qc_items[0]["line"] == 3


def test_write_qc_report_new_format(tmp_path):
    """新版 QC 報告含仲裁欄位。"""
    qc_path = tmp_path / "test.qc.md"
    items = [
        {
            "line": 5,
            "original": "蘇味行銷",
            "suggestion": "數位行銷",
            "reason": "同音字",
            "risk": "high",
            "verdict": "other",
            "final_text": "蘇味的祕密",
            "gemini_reasoning": "音訊清楚為秘密",
            "confidence": 0.82,
        },
    ]
    _write_qc_report(qc_path, items)

    content = qc_path.read_text(encoding="utf-8")
    assert "HIGH" in content
    assert "other" in content
    assert "conf 0.82" in content
    assert "ASR 原文" in content
    assert "Opus 建議" in content
    assert "Gemini 仲裁" in content
    assert "蘇味的祕密" in content
    assert "音訊清楚為秘密" in content
