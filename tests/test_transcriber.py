"""shared/transcriber.py 單元測試。

測試不需要 GPU 或 FunASR 安裝的輔助函式。
整合測試（實際轉寫）需要 GPU 環境，標記為 slow。
"""

from unittest.mock import MagicMock, patch

import pytest

from shared.transcriber import (
    _BUF_TRAILING_ASCII_RE,
    _MAX_SUBTITLE_HARD,
    _add_pinyin,
    _build_initial_prompt,
    _correct_with_llm,
    _dedupe_adjacent_repeats,
    _extract_hotwords,
    _extract_project_context,
    _extract_srt_texts,
    _force_break,
    _parse_llm_response,
    _process_srt_line,
    _redistribute_boundary_cuts,
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


def test_force_break_chinese_word_boundary():
    """jieba 走詞邊界切，不切常見雙字詞（PR #271 觀察 38 處詞被切到 cue 邊界）。

    句子刻意 28 字（>20 上限）構成。每一刀都不該切「然後 / 怎麼 / 我們 / 因為」。
    """
    text = "然後我的直覺是對的因為光是第一個禮拜我們就看到太多生活方式"
    chunks = _force_break(text, 20)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 20
    # 重組後字元應一致（順序保留）
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")
    # 不該切常見雙字詞
    for bigram in ["然後", "因為", "我們", "怎麼"]:
        if bigram in text:
            # 如果原文有，切完拼回來也要保留
            cut_separated = any(
                chunks[i].endswith(bigram[0]) and chunks[i + 1].startswith(bigram[1])
                for i in range(len(chunks) - 1)
            )
            assert not cut_separated, f"「{bigram}」被切到 chunk 邊界"


def test_force_break_short_text():
    """≤max_chars 的文字應原樣回傳（不必拆）。"""
    chunks = _force_break("簡短句子", 20)
    assert chunks == ["簡短句子"]


def test_force_break_long_english_token():
    """超長英文 token（>max_chars）應獨立成 chunk 不被破壞。"""
    text = "看 https://example.com/very-long-url-path-that-exceeds-limit 連結"
    chunks = _force_break(text, 20)
    # URL token 不該被切成兩半
    full = "".join(chunks)
    assert "https://example.com/very-long-url-path-that-exceeds-limit" in full


def test_force_break_ascii_compound_overflows_to_hard():
    """soft/hard 雙閾值：ASCII 英文 compound name（如「Traveling Village」17 字）
    超過 soft 14 但 ≤ hard 22 時應整體保留同一 chunk 不被切。
    """
    chunks = _force_break("Traveling Village然後它是由丹麥的一對夫婦", 14, 22)
    # 「Traveling Village」必須整段在某個 chunk 內，不能跨 chunk 邊界切開
    assert any("Traveling Village" in c for c in chunks)
    # 該 chunk 確實 overflow 過 soft 14
    assert any(len(c) > 14 and "Traveling Village" in c for c in chunks)


def test_force_break_chinese_english_no_space_kept_together():
    """iter3 fix：buf 結尾「個Hell」（中英連寫無空格）+ 下個 token「Yes」應走
    trailing-ASCII regex search（不是 split(' ')[-1]）→ 兩個 ASCII token 連住保留。
    對應觀察 case：「我覺得就是個Hell Yes然後這是」(max=14)。
    """
    chunks = _force_break("我覺得就是個Hell Yes然後這是", 14, 22)
    # 「Hell Yes」必須在同一 chunk 內，不能被切到 chunk 邊界
    cut_separated = any(
        "Hell" in chunks[i]
        and chunks[i].rstrip().endswith("Hell")
        and chunks[i + 1].lstrip().startswith("Yes")
        for i in range(len(chunks) - 1)
    )
    assert not cut_separated, "「Hell Yes」被切到 chunk 邊界"


def test_buf_trailing_ascii_regex_detects_cases():
    """`_BUF_TRAILING_ASCII_RE` 須抓 buf 結尾連續 ASCII 英文，含中英連寫無空格。"""
    cases = [
        ("以後對我們來說就是個Hell", True),  # 中英連寫無空格
        ("Hello World ", True),  # 純英文 + trailing space
        ("Traveling Village", True),  # 純英文 compound
        ("以後對我們來說就是個", False),  # 純中文
        ("純中文無英文", False),
    ]
    for text, expected in cases:
        got = bool(_BUF_TRAILING_ASCII_RE.search(text))
        assert got is expected, f"trailing-ascii({text!r}) = {got}, expected {expected}"


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


def test_max_subtitle_hard_accommodates_known_compound_names():
    """`_MAX_SUBTITLE_HARD` 必須 > soft 上限，且能容下既知英文 compound name
    （「Traveling Village」17 字）。改值前先確認新 hard 沒擠掉這些 case。"""
    from shared.transcriber import _MAX_SUBTITLE_CHARS

    assert _MAX_SUBTITLE_HARD > _MAX_SUBTITLE_CHARS
    assert _MAX_SUBTITLE_HARD >= len("Traveling Village")


def test_split_sentences_passes_hard_max_to_force_break():
    """`_split_sentences` 走長句拆 _force_break 路徑時必須傳 _MAX_SUBTITLE_HARD，
    讓 ASCII compound 能 overflow 到 hard ceiling（不依賴 _force_break 的 default）。
    句子刻意做成「無句尾標點 + 無逗號 + > _MAX_SUBTITLE_CHARS」走第三條路徑。
    """
    text = "Traveling Village然後它是由丹麥的一對夫婦所創立"
    chunks = _split_sentences(text)
    # ASCII compound 不該被切成 Traveling / Village
    for i in range(len(chunks) - 1):
        assert not (
            chunks[i].rstrip().endswith("Traveling")
            and chunks[i + 1].lstrip().startswith("Village")
        ), "「Traveling Village」被切到 chunk 邊界（hard 沒傳到 _force_break）"


# ── _dedupe_adjacent_repeats（within-segment Whisper 重複 hallucination）──


def test_dedupe_adjacent_repeats_cjk_2_to_4_chars():
    """CJK 2-4 char unit 連續重複，留一份。"""
    assert _dedupe_adjacent_repeats("花蓮花蓮回來") == "花蓮回來"
    assert _dedupe_adjacent_repeats("超級超級棒") == "超級棒"
    # 4 char unit
    assert _dedupe_adjacent_repeats("數位遊牧數位遊牧") == "數位遊牧"
    # 3 char unit
    assert _dedupe_adjacent_repeats("不正常不正常人") == "不正常人"


def test_dedupe_adjacent_repeats_no_false_positive():
    """非重複文字應原樣保留（含 ASCII compound 不被誤食）。"""
    assert _dedupe_adjacent_repeats("數位遊牧") == "數位遊牧"
    assert _dedupe_adjacent_repeats("Traveling Village") == "Traveling Village"


def test_dedupe_adjacent_repeats_skips_single_char_doubling():
    """1-char doubling（「對對」「好好」）刻意不動 — 自動 collapse 會誤殺正當疊字
    （口語「對對對」「好好好」是有意義的）。已知 bug 走 _DEDUPE_KNOWN_BUGS whitelist。
    """
    assert _dedupe_adjacent_repeats("對對對 好") == "對對對 好"
    assert _dedupe_adjacent_repeats("好好") == "好好"


def test_dedupe_adjacent_repeats_skips_ascii():
    """ASCII 英文不在 dedupe 範圍（避免「OK OK OK」「ll/ee/oo」誤食）。"""
    assert _dedupe_adjacent_repeats("OK OK OK") == "OK OK OK"
    assert _dedupe_adjacent_repeats("hello hello") == "hello hello"


def test_dedupe_adjacent_repeats_known_bug_whitelist():
    """已知 1-char doubling bug（如「本本尊」）走 whitelist 點殺。"""
    assert _dedupe_adjacent_repeats("本本尊") == "本尊"
    assert _dedupe_adjacent_repeats("所以本本尊來分享") == "所以本尊來分享"


# ── _redistribute_boundary_cuts（cue 邊界詞被切回收）──


def test_redistribute_boundary_cuts_bigram_shift():
    """cue N 結尾「然」+ cue N+1 開頭「後」→ 「然」shift 到 cue N+1（「然後」是高頻 bigram）。"""
    cues = [(0.0, 1.0, "我們在越南的會安然"), (1.0, 2.0, "後結束以後我們就")]
    out = _redistribute_boundary_cuts(cues)
    assert out[0][2] == "我們在越南的會安"
    assert out[1][2] == "然後結束以後我們就"


def test_redistribute_boundary_cuts_trigram_shift():
    """trigram「為什麼」「那時候」拼回偵測 → shift。"""
    cues = [(0.0, 1.0, "我說那你為"), (1.0, 2.0, "什麼後來要去")]
    out = _redistribute_boundary_cuts(cues)
    assert out[0][2] == "我說那你"
    assert out[1][2] == "為什麼後來要去"

    cues2 = [(0.0, 1.0, "我那"), (1.0, 2.0, "時候會進")]
    out2 = _redistribute_boundary_cuts(cues2)
    assert out2[0][2] == "我"
    assert out2[1][2] == "那時候會進"


def test_redistribute_boundary_cuts_no_match_unchanged():
    """邊界 bigram/trigram 不在 set 時 cue 文字不變。"""
    cues = [(0.0, 1.0, "今天天氣"), (1.0, 2.0, "真的很好")]
    out = _redistribute_boundary_cuts(cues)
    assert out[0][2] == "今天天氣"
    assert out[1][2] == "真的很好"


def test_redistribute_boundary_cuts_short_input():
    """0/1 cue 不動，timestamp 也保留。"""
    assert _redistribute_boundary_cuts([]) == []
    cues = [(0.0, 1.0, "單一 cue")]
    assert _redistribute_boundary_cuts(cues) == cues


def test_redistribute_boundary_cuts_preserves_timestamps():
    """shift char 不應動 timestamp（誤差 <0.5s 接受，spec follow-up）。"""
    cues = [(0.0, 1.5, "去花蓮然"), (1.5, 3.0, "後接下來")]
    out = _redistribute_boundary_cuts(cues)
    assert (out[0][0], out[0][1]) == (0.0, 1.5)
    assert (out[1][0], out[1][1]) == (1.5, 3.0)


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


def test_whisperx_to_srt_empty():
    assert _whisperx_to_srt([]) == ""
    assert _whisperx_to_srt([{"start": 0, "end": 1, "text": ""}]) == ""


def test_whisperx_to_srt_strips_whitespace():
    segments = [{"start": 0.0, "end": 1.0, "text": "  含前後空白  "}]
    srt = _whisperx_to_srt(segments)
    assert "含前後空白" in srt
    assert "  含前後空白" not in srt


def test_whisperx_to_srt_applies_dedupe_within_segment():
    """`_whisperx_to_srt` 必須對每段呼叫 `_dedupe_adjacent_repeats` 去 within-segment
    Whisper 重複 hallucination（觀察 case「花蓮花蓮回來」「不正常人類不正常人類」）。
    """
    segments = [{"start": 0.0, "end": 2.0, "text": "花蓮花蓮回來嗎"}]
    srt = _whisperx_to_srt(segments)
    assert "花蓮回來嗎" in srt
    assert "花蓮花蓮" not in srt


def test_whisperx_to_srt_applies_boundary_redistribute():
    """`_whisperx_to_srt` 應在 cue 拆完後跑 `_redistribute_boundary_cuts`，
    把高頻 bigram/trigram 拼回（觀察 case「然 | 後」拆兩 cue）。
    """
    segments = [
        {"start": 0.0, "end": 2.0, "text": "我們在越南的會安然"},
        {"start": 2.0, "end": 4.0, "text": "後結束以後我們就走了"},
    ]
    srt = _whisperx_to_srt(segments)
    # 「然」應 shift 到第二 cue 開頭
    assert "我們在越南的會安\n" in srt or "我們在越南的會安 " in srt
    assert "然後結束" in srt


def test_whisperx_to_srt_with_speakers_prefix():
    """`with_speakers=True` 時每 cue 文字前加 [SPEAKER_XX] prefix。"""
    segments = [
        {"start": 0.0, "end": 2.0, "text": "今天天氣真好", "speaker": "SPEAKER_00"},
        {"start": 2.0, "end": 4.0, "text": "我們去散步吧", "speaker": "SPEAKER_01"},
    ]
    srt = _whisperx_to_srt(segments, with_speakers=True)
    assert "[SPEAKER_00] 今天天氣真好" in srt
    assert "[SPEAKER_01] 我們去散步吧" in srt


def test_whisperx_to_srt_with_speakers_missing_speaker_uses_placeholder():
    """`with_speakers=True` 但 segment 沒 speaker 欄（diar 沒指派）→ [SPEAKER_??]。"""
    segments = [{"start": 0.0, "end": 1.0, "text": "未指派 speaker"}]
    srt = _whisperx_to_srt(segments, with_speakers=True)
    assert "[SPEAKER_??] 未指派 speaker" in srt


def test_whisperx_to_srt_with_speakers_skips_redistribute():
    """`with_speakers=True` 跳過 `_redistribute_boundary_cuts`（避免 cross-speaker
    char shift）。觀察 case：speaker 0 結尾「然」+ speaker 1 開頭「後」應**保持原狀**，
    不該把「然」shift 到 speaker 1 的 cue（會把字錯派 speaker）。
    """
    segments = [
        {"start": 0.0, "end": 2.0, "text": "我們在越南的會安然", "speaker": "SPEAKER_00"},
        {
            "start": 2.0,
            "end": 4.0,
            "text": "後結束以後我們就走了",
            "speaker": "SPEAKER_01",
        },
    ]
    srt = _whisperx_to_srt(segments, with_speakers=True)
    # 「然」應留在 speaker 0 的 cue（未跨 speaker 邊界 shift）
    assert "[SPEAKER_00] 我們在越南的會安然" in srt
    assert "[SPEAKER_01] 後結束以後" in srt


def test_whisperx_to_srt_with_speakers_default_off():
    """default with_speakers=False 不該加 prefix（既有 caller 不變）。"""
    segments = [{"start": 0.0, "end": 1.0, "text": "純字幕", "speaker": "SPEAKER_00"}]
    srt = _whisperx_to_srt(segments)
    assert "[SPEAKER_00]" not in srt
    assert "純字幕" in srt


# ── _build_initial_prompt ──


def test_build_initial_prompt_full():
    """所有來源詞都 inline 進 prompt，不分 label。"""
    prompt = _build_initial_prompt(
        hotwords=["Traveling Village", "Paul"],
        project_context={"guest_name": "張安吉", "topic": "數位遊牧"},
        host_name="張修修",
        show_name="不正常人類研究所",
    )
    for term in ["不正常人類研究所", "張修修", "張安吉", "數位遊牧", "Traveling Village", "Paul"]:
        assert term in prompt


def test_build_initial_prompt_no_label_hallucination_pattern():
    """不含「主持人：X」「節目：Y」等 label 結構 — Whisper 會在低 SNR 段
    echo 整段 label 文字（PR #271 觀察到 cue 70 等 10 處輸出「主持人 張修修」
    吃掉~110s 真實內容）。"""
    prompt = _build_initial_prompt(
        hotwords=["NMN"],
        project_context={"guest_name": "張安吉", "topic": "數位遊牧"},
        host_name="張修修",
        show_name="不正常人類研究所",
    )
    for label in ["節目：", "主持人：", "來賓：", "主題：", "專名："]:
        assert label not in prompt, f"prompt 仍含 label「{label}」可能觸發 hallucination"


def test_build_initial_prompt_dedupe():
    """重複詞只保留一份（host == guest 等邊界情境）。"""
    prompt = _build_initial_prompt(
        hotwords=["張修修"],
        project_context={"guest_name": "張修修"},
        host_name="張修修",
        show_name="show",
    )
    assert prompt.count("張修修") == 1


def test_build_initial_prompt_empty():
    """全部 None / 空 → 空字串。"""
    assert _build_initial_prompt([], None) == ""


def test_build_initial_prompt_partial():
    """只有 hotwords 也應產出。"""
    prompt = _build_initial_prompt(["Foo Bar"], None)
    assert "Foo Bar" in prompt


def test_get_asr_model_passes_anti_hallucination_options():
    """`_get_asr_model` 必須把三件 anti-hallucination guard 傳給 whisperx.load_model
    （condition_on_previous_text=False / compression_ratio_threshold / no_speech_threshold）。
    PR #271 觀察到 cue 70 等 10 處 prompt-leak hallucination，三件套缺一就退化。
    """
    pytest.importorskip("whisperx")
    import shared.transcriber as t

    # reset singleton 避免被 cache 命中
    t._asr_model = None
    t._asr_model_id = None

    with patch("whisperx.load_model") as mock_load:
        mock_load.return_value = MagicMock()
        t._get_asr_model("large-v3", initial_prompt="測試詞")

    assert mock_load.called
    asr_options = mock_load.call_args.kwargs["asr_options"]
    assert asr_options["condition_on_previous_text"] is False
    assert asr_options["compression_ratio_threshold"] == 2.4
    assert asr_options["no_speech_threshold"] == 0.6
    assert asr_options["initial_prompt"] == "測試詞"


def test_get_align_model_caches_per_language():
    """`_get_align_model` lazy singleton：同 language 不重 load，換 language 則重 load。"""
    pytest.importorskip("whisperx")
    import shared.transcriber as t

    t._align_model = None
    t._align_metadata = None
    t._align_language = None

    with patch("whisperx.load_align_model") as mock_load:
        mock_load.return_value = (MagicMock(name="model"), {"meta": "data"})
        m1, meta1 = t._get_align_model("zh")
        m2, meta2 = t._get_align_model("zh")  # cache hit
        assert mock_load.call_count == 1
        assert m1 is m2
        assert meta1 == meta2 == {"meta": "data"}

        # 換 language 重 load
        mock_load.return_value = (MagicMock(name="model_en"), {"meta": "en"})
        m3, _ = t._get_align_model("en")
        assert mock_load.call_count == 2
        assert m3 is not m1


def test_get_diarize_pipeline_passes_hf_token():
    """`_get_diarize_pipeline` 把 hf_token 傳進 DiarizationPipeline；singleton 不重建。"""
    pytest.importorskip("whisperx")
    import shared.transcriber as t

    t._diarize_pipeline = None

    with patch("whisperx.diarize.DiarizationPipeline") as mock_pipeline_cls:
        mock_pipeline_cls.return_value = MagicMock(name="pipeline")
        p1 = t._get_diarize_pipeline("hf_xxx_token")
        p2 = t._get_diarize_pipeline("hf_xxx_token")  # singleton hit
        assert mock_pipeline_cls.call_count == 1
        assert p1 is p2
        assert mock_pipeline_cls.call_args.kwargs["use_auth_token"] == "hf_xxx_token"


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
        )

    assert result.exists()
    qc_path = tmp_path / "test.qc.md"
    assert qc_path.exists()
    qc_content = qc_path.read_text(encoding="utf-8")
    assert "MEDIUM" in qc_content
    assert "測試問題" in qc_content


def test_transcribe_default_no_diar_srt(tmp_path):
    """default use_diarization=False → 不出 .diar.srt（純 SRT 才出）。"""
    pytest.importorskip("whisperx")
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    mock_model = _mock_whisperx_model([{"start": 0, "end": 1, "text": "测试"}])

    with (
        patch("shared.transcriber._get_asr_model", return_value=mock_model),
        patch("whisperx.load_audio", return_value=b"fake"),
    ):
        from shared.transcriber import transcribe

        result = transcribe(
            str(audio),
            output_dir=str(tmp_path),
            normalize_audio=False,
        )

    assert result.exists()
    assert not (tmp_path / "test.diar.srt").exists()


def test_transcribe_diarize_dual_output(tmp_path, monkeypatch):
    """use_diarization=True + HF token + diar 成功 → 純 SRT + .diar.srt 兩份。"""
    pytest.importorskip("whisperx")
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_test_token")

    mock_model = _mock_whisperx_model([{"start": 0.0, "end": 2.0, "text": "受訪者答覆"}])

    aligned = {"segments": [{"start": 0.0, "end": 2.0, "text": "受訪者答覆"}]}
    diar_assigned = {
        "segments": [{"start": 0.0, "end": 2.0, "text": "受訪者答覆", "speaker": "SPEAKER_01"}]
    }
    mock_pipeline = MagicMock()
    mock_pipeline.return_value = MagicMock(name="diarize_segs")

    with (
        patch("shared.transcriber._get_asr_model", return_value=mock_model),
        patch("whisperx.load_audio", return_value=b"fake"),
        patch(
            "shared.transcriber._get_align_model",
            return_value=(MagicMock(name="align_model"), {"meta": "data"}),
        ),
        patch("whisperx.align", return_value=aligned),
        patch("shared.transcriber._get_diarize_pipeline", return_value=mock_pipeline),
        patch("whisperx.assign_word_speakers", return_value=diar_assigned),
    ):
        from shared.transcriber import transcribe

        result = transcribe(
            str(audio),
            output_dir=str(tmp_path),
            normalize_audio=False,
            use_diarization=True,
        )

    # 純 SRT 仍出
    assert result.exists()
    assert result.suffix == ".srt"
    assert ".diar" not in result.stem  # transcribe() return 純 SRT 路徑

    # diar SRT 多出一份，含 speaker prefix
    diar_path = tmp_path / "test.diar.srt"
    assert diar_path.exists()
    diar_content = diar_path.read_text(encoding="utf-8")
    assert "[SPEAKER_01]" in diar_content
    assert "受訪者答覆" in diar_content


def test_transcribe_diarize_no_hf_token_skips_diar(tmp_path, monkeypatch):
    """use_diarization=True 但 HF_TOKEN 未設定 → 純 SRT 出 + 沒 .diar.srt（warn）。"""
    pytest.importorskip("whisperx")
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)

    mock_model = _mock_whisperx_model([{"start": 0, "end": 1, "text": "純字幕"}])

    with (
        patch("shared.transcriber._get_asr_model", return_value=mock_model),
        patch("whisperx.load_audio", return_value=b"fake"),
        patch(
            "shared.transcriber._get_align_model",
            return_value=(MagicMock(), {"meta": "data"}),
        ),
        patch("whisperx.align", return_value={"segments": []}),
    ):
        from shared.transcriber import transcribe

        result = transcribe(
            str(audio),
            output_dir=str(tmp_path),
            normalize_audio=False,
            use_diarization=True,
        )

    assert result.exists()  # 純 SRT 仍出
    assert not (tmp_path / "test.diar.srt").exists()  # 沒 .diar.srt


def test_transcribe_diarize_pipeline_failure_keeps_pure_srt(tmp_path, monkeypatch):
    """diarize pipeline raise → 跳過 .diar.srt，純 SRT 不受影響。"""
    pytest.importorskip("whisperx")
    audio = tmp_path / "test.mp3"
    audio.write_bytes(b"fake audio data")

    monkeypatch.setenv("HUGGINGFACE_TOKEN", "hf_test_token")

    mock_model = _mock_whisperx_model([{"start": 0, "end": 1, "text": "純字幕"}])

    failing_pipeline = MagicMock(side_effect=RuntimeError("EULA not accepted"))

    with (
        patch("shared.transcriber._get_asr_model", return_value=mock_model),
        patch("whisperx.load_audio", return_value=b"fake"),
        patch(
            "shared.transcriber._get_align_model",
            return_value=(MagicMock(), {"meta": "data"}),
        ),
        patch("whisperx.align", return_value={"segments": []}),
        patch("shared.transcriber._get_diarize_pipeline", return_value=failing_pipeline),
    ):
        from shared.transcriber import transcribe

        result = transcribe(
            str(audio),
            output_dir=str(tmp_path),
            normalize_audio=False,
            use_diarization=True,
        )

    assert result.exists()  # 純 SRT 仍出
    assert not (tmp_path / "test.diar.srt").exists()


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
