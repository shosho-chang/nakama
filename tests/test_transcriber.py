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
    _extract_hotwords,
    _extract_srt_texts,
    _force_break,
    _parse_llm_response,
    _process_srt_line,
    _remove_punctuation,
    _replace_srt_texts,
    _to_traditional,
)

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


# ── jieba 詞邊界強制斷行 ──


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


def test_max_subtitle_hard_accommodates_known_compound_names():
    """`_MAX_SUBTITLE_HARD` 必須 > soft 上限，且能容下既知英文 compound name
    （「Traveling Village」17 字）。改值前先確認新 hard 沒擠掉這些 case。"""
    from shared.transcriber import _MAX_SUBTITLE_CHARS

    assert _MAX_SUBTITLE_HARD > _MAX_SUBTITLE_CHARS
    assert _MAX_SUBTITLE_HARD >= len("Traveling Village")


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


def test_to_traditional_keeps_video_script_term():
    """s2twp 把「腳本」轉成資訊術語「指令碼」——影音產線要修回來。

    2026-07-29 鄭國威那集全集 32 處（大綱 腳本 分鏡 拍攝 粗剪／影音的腳本），
    語境全是影片腳本，無一是 shell script。
    """
    assert _to_traditional("大綱腳本分鏡") == "大綱腳本分鏡"
    assert _to_traditional("影音的腳本") == "影音的腳本"
    # 簡體輸入同樣要落在「腳本」
    assert _to_traditional("脚本") == "腳本"
    # 既有的隻/只 修正不受影響
    assert _to_traditional("就是只要") == "就是只要"
