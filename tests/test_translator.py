"""shared/translator.py 單元測試。

測試不需要 Claude API 的純邏輯函式。
翻譯 API 整合測試需要 ANTHROPIC_API_KEY，標記為 slow。
"""

from unittest.mock import patch

from shared.translator import (
    add_glossary_term,
    format_bilingual_markdown,
    load_glossary,
    split_off_reference_section,
    split_paragraphs,
    translate_document,
    translate_segments,
)

# ── split_paragraphs ──


def test_split_paragraphs_basic():
    text = "First paragraph.\n\nSecond paragraph."
    assert split_paragraphs(text) == ["First paragraph.", "Second paragraph."]


def test_split_paragraphs_multiple_blanks():
    text = "Para one.\n\n\n\nPara two."
    assert split_paragraphs(text) == ["Para one.", "Para two."]


def test_split_paragraphs_empty():
    assert split_paragraphs("") == []
    assert split_paragraphs("   \n\n   ") == []


def test_split_paragraphs_single():
    assert split_paragraphs("Only one paragraph.") == ["Only one paragraph."]


def test_split_paragraphs_strips_whitespace():
    text = "  Para one.  \n\n  Para two.  "
    result = split_paragraphs(text)
    assert result == ["Para one.", "Para two."]


# ── format_bilingual_markdown ──


def test_format_bilingual_markdown_basic():
    originals = ["Hello world.", "Second paragraph."]
    translations = ["你好世界。", "第二段落。"]
    result = format_bilingual_markdown(originals, translations)
    assert "Hello world." in result
    assert "> 你好世界。" in result
    assert "Second paragraph." in result
    assert "> 第二段落。" in result


def test_format_bilingual_markdown_empty_translation():
    originals = ["Hello.", "World."]
    translations = ["你好。", ""]
    result = format_bilingual_markdown(originals, translations)
    assert "> 你好。" in result
    assert "World." in result
    # 無譯文的段落不產生 blockquote
    lines = result.split("\n")
    blockquotes = [line for line in lines if line.startswith(">")]
    assert len(blockquotes) == 1


def test_format_bilingual_markdown_multiline_translation():
    originals = ["Para."]
    translations = ["第一行。\n第二行。"]
    result = format_bilingual_markdown(originals, translations)
    assert "> 第一行。" in result
    assert "> 第二行。" in result


def test_format_bilingual_markdown_preserves_order():
    originals = ["A", "B", "C"]
    translations = ["甲", "乙", "丙"]
    result = format_bilingual_markdown(originals, translations)
    a_pos = result.index("A")
    b_pos = result.index("B")
    c_pos = result.index("C")
    assert a_pos < b_pos < c_pos


# ── load_glossary & add_glossary_term ──


def test_load_glossary_returns_dict(tmp_path):
    glossary_file = tmp_path / "glossary.yaml"
    glossary_file.write_text(
        "terms:\n  mitochondria: 粒線體\n  ribosome: 核糖體\n", encoding="utf-8"
    )
    with patch("shared.translator._GLOSSARY_PATH", glossary_file):
        result = load_glossary()
    assert result["mitochondria"] == "粒線體"
    assert result["ribosome"] == "核糖體"


def test_load_glossary_missing_file(tmp_path):
    missing = tmp_path / "nonexistent.yaml"
    with patch("shared.translator._GLOSSARY_PATH", missing):
        result = load_glossary()
    assert result == {}


def test_add_glossary_term_new(tmp_path):
    glossary_file = tmp_path / "glossary.yaml"
    glossary_file.write_text("terms:\n  mitochondria: 粒線體\n", encoding="utf-8")
    with patch("shared.translator._GLOSSARY_PATH", glossary_file):
        add_glossary_term("autophagy", "自噬作用")
        result = load_glossary()
    assert result["autophagy"] == "自噬作用"
    assert result["mitochondria"] == "粒線體"


def test_add_glossary_term_preserves_comments(tmp_path):
    """add_glossary_term 必須保留 terms: 區塊的所有注釋。"""
    glossary_file = tmp_path / "glossary.yaml"
    original = "# 台灣學術術語對照表\nterms:\n  # 細胞與分子生物學\n  mitochondria: 粒線體\n"
    glossary_file.write_text(original, encoding="utf-8")
    with patch("shared.translator._GLOSSARY_PATH", glossary_file):
        add_glossary_term("cortisol", "皮質醇")
        written = glossary_file.read_text(encoding="utf-8")
    assert "# 台灣學術術語對照表" in written
    assert "# 細胞與分子生物學" in written
    assert "mitochondria: 粒線體" in written
    assert "cortisol: 皮質醇" in written


def test_add_glossary_term_writes_to_user_terms(tmp_path):
    """add_glossary_term 應寫入 user_terms 區塊，不修改 terms 區塊。"""
    glossary_file = tmp_path / "glossary.yaml"
    glossary_file.write_text("terms:\n  mitochondria: 粒線體\n", encoding="utf-8")
    with patch("shared.translator._GLOSSARY_PATH", glossary_file):
        add_glossary_term("cortisol", "皮質醇")
        written = glossary_file.read_text(encoding="utf-8")
    assert "user_terms:" in written
    assert "cortisol: 皮質醇" in written
    assert written.index("terms:") < written.index("user_terms:")


def test_add_glossary_term_overwrite(tmp_path):
    glossary_file = tmp_path / "glossary.yaml"
    glossary_file.write_text("terms:\n  mitochondria: 線粒體\nuser_terms: {}\n", encoding="utf-8")
    with patch("shared.translator._GLOSSARY_PATH", glossary_file):
        add_glossary_term("Mitochondria", "粒線體")
        result = load_glossary()
    assert result["mitochondria"] == "粒線體"


def test_add_glossary_term_creates_file(tmp_path):
    glossary_file = tmp_path / "new_glossary.yaml"
    with patch("shared.translator._GLOSSARY_PATH", glossary_file):
        add_glossary_term("cortisol", "皮質醇")
        result = load_glossary()
    assert result["cortisol"] == "皮質醇"


def test_load_glossary_merges_user_terms(tmp_path):
    """load_glossary 應合併 terms 和 user_terms，user_terms 優先。"""
    glossary_file = tmp_path / "glossary.yaml"
    glossary_file.write_text(
        "terms:\n  mitochondria: 粒線體\nuser_terms:\n  cortisol: 皮質醇\n",
        encoding="utf-8",
    )
    with patch("shared.translator._GLOSSARY_PATH", glossary_file):
        result = load_glossary()
    assert result["mitochondria"] == "粒線體"
    assert result["cortisol"] == "皮質醇"


# ── translate_segments (mocked) ──


def test_translate_segments_empty():
    assert translate_segments([]) == []


def test_translate_segments_uses_llm(tmp_path):
    glossary_file = tmp_path / "g.yaml"
    glossary_file.write_text("terms: {}\n", encoding="utf-8")
    mock_response = '[{"index": 1, "translation": "粒線體是細胞的發電廠。"}]'
    with (
        patch("shared.translator.ask", return_value=mock_response),
        patch("shared.translator._GLOSSARY_PATH", glossary_file),
    ):
        result = translate_segments(["Mitochondria are the powerhouses of the cell."])
    assert result == ["粒線體是細胞的發電廠。"]


def test_translate_segments_json_fallback(tmp_path):
    """當批次 JSON 解析失敗時，應降級為逐段翻譯。"""
    glossary_file = tmp_path / "g.yaml"
    glossary_file.write_text("terms: {}\n", encoding="utf-8")
    call_count = 0

    def mock_ask(prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "not valid json at all"
        return "降級譯文"

    with (
        patch("shared.translator.ask", side_effect=mock_ask),
        patch("shared.translator._GLOSSARY_PATH", glossary_file),
    ):
        result = translate_segments(["Original text."])
    assert result == ["降級譯文"]
    assert call_count == 2


def test_translate_segments_partial_json(tmp_path):
    """JSON 缺少某 index 時，對應段落回傳空字串。"""
    glossary_file = tmp_path / "g.yaml"
    glossary_file.write_text("terms: {}\n", encoding="utf-8")
    mock_response = '[{"index": 1, "translation": "第一段"}]'
    with (
        patch("shared.translator.ask", return_value=mock_response),
        patch("shared.translator._GLOSSARY_PATH", glossary_file),
    ):
        result = translate_segments(["Seg one.", "Seg two."])
    assert result[0] == "第一段"
    assert result[1] == ""


# ── translate_document (mocked) ──


def test_translate_document_empty():
    result = translate_document("")
    assert result == ""


def test_translate_document_integrates(tmp_path):
    """translate_document 應分段、翻譯、組合雙語 MD。"""
    glossary_file = tmp_path / "g.yaml"
    glossary_file.write_text("terms: {}\n", encoding="utf-8")

    def mock_translate(segments, **kwargs):
        return [f"譯：{s}" for s in segments]

    text = "Paragraph one.\n\nParagraph two."
    with (
        patch("shared.translator.translate_segments", side_effect=mock_translate),
        patch("shared.translator._GLOSSARY_PATH", glossary_file),
    ):
        result = translate_document(text)

    assert "Paragraph one." in result
    assert "> 譯：Paragraph one." in result
    assert "Paragraph two." in result
    assert "> 譯：Paragraph two." in result


# ── split_off_reference_section ──


def test_split_off_reference_section_basic():
    """## References heading splits body from refs."""
    text = "Body paragraph.\n\n## References\n\n1. Foo\n2. Bar"
    body, ref = split_off_reference_section(text)
    assert body == "Body paragraph."
    assert ref.startswith("## References")
    assert "1. Foo" in ref


def test_split_off_reference_section_no_heading():
    """Doc without reference heading returns empty ref section."""
    text = "Body paragraph one.\n\nBody paragraph two."
    body, ref = split_off_reference_section(text)
    assert body == text
    assert ref == ""


def test_split_off_reference_section_case_insensitive():
    """REFERENCES / references both match."""
    for variant in ("REFERENCES", "References", "references", "ReFeReNcEs"):
        text = f"Body.\n\n## {variant}\n\n[1] cite"
        body, ref = split_off_reference_section(text)
        assert body == "Body."
        assert ref.startswith(f"## {variant}")


def test_split_off_reference_section_h3_heading():
    """### Bibliography also matches (not just H2)."""
    text = "Body.\n\n### Bibliography\n\n* cite"
    body, ref = split_off_reference_section(text)
    assert body == "Body."
    assert ref.startswith("### Bibliography")


def test_split_off_reference_section_chinese_heading():
    """Traditional Chinese reference heading variants."""
    for variant in ("參考文獻", "文獻", "註釋", "注釋"):
        text = f"內文段落。\n\n## {variant}\n\n1. 引用"
        body, ref = split_off_reference_section(text)
        assert body == "內文段落。"
        assert ref.startswith(f"## {variant}")


def test_split_off_reference_section_whitelist_variants():
    """All mid-broad whitelist variants are recognised."""
    for variant in (
        "Bibliography",
        "Works Cited",
        "Literature Cited",
        "Sources",
        "Citations",
        "Notes",
        "Further Reading",
    ):
        text = f"Body.\n\n## {variant}\n\nentry"
        body, ref = split_off_reference_section(text)
        assert body == "Body.", f"Failed on heading: {variant}"
        assert ref.startswith(f"## {variant}")


def test_split_off_reference_section_trailing_punct_tolerated():
    """## References: / ## References. both match."""
    for trailing in (":", "：", ".", "。"):
        text = f"Body.\n\n## References{trailing}\n\n1. cite"
        body, ref = split_off_reference_section(text)
        assert body == "Body."
        assert ref.startswith("## References")


def test_split_off_reference_section_first_match_wins():
    """If multiple ref headings somehow exist, the first one wins."""
    text = "Body.\n\n## References\n\nA\n\n## Bibliography\n\nB"
    body, ref = split_off_reference_section(text)
    assert body == "Body."
    assert ref.startswith("## References")
    assert "## Bibliography" in ref


def test_split_off_reference_section_non_reference_heading_ignored():
    """## Results / ## Methods etc must NOT trigger the split."""
    text = "Intro.\n\n## Methods\n\nMethod body.\n\n## Results\n\nFindings."
    body, ref = split_off_reference_section(text)
    assert body == text
    assert ref == ""


def test_split_off_reference_section_compound_headings():
    """``References and Notes`` (Science default) and similar compound forms
    must match — every token is either a reference word or a connective."""
    for heading in (
        "References and Notes",
        "Notes and References",
        "Bibliography and Further Reading",
        "Works Cited and Notes",
        "References & Notes",
        "References, Notes",
        "Sources and Citations",
    ):
        text = f"Body.\n\n## {heading}\n\n1. cite"
        body, ref = split_off_reference_section(text)
        assert body == "Body.", f"Failed on compound heading: {heading}"
        assert ref.startswith(f"## {heading}")


def test_split_off_reference_section_partial_reference_word_no_match():
    """Headings with a reference word + a non-reference, non-connective word
    must NOT match (avoids false positives like ``Notes on Methodology``)."""
    for heading in (
        "Notes on Methodology",
        "References to Future Work",
        "Sources of Data",
        "Reading List of Articles",
    ):
        text = f"Body.\n\n## {heading}\n\n* item"
        body, ref = split_off_reference_section(text)
        assert body == text, f"False positive on heading: {heading}"
        assert ref == ""


def test_translate_document_skips_reference_section(tmp_path):
    """References heading and everything after must pass through verbatim
    without LLM call; body before it is translated as normal."""
    glossary_file = tmp_path / "g.yaml"
    glossary_file.write_text("terms: {}\n", encoding="utf-8")

    translated_inputs = []

    def mock_translate(segments, **kwargs):
        translated_inputs.extend(segments)
        return [f"譯：{s}" for s in segments]

    text = (
        "Intro paragraph.\n\n"
        "Body paragraph.\n\n"
        "## References\n\n"
        "1. Smith et al. 2024. Nature.\n"
        "2. Doe et al. 2025. Science."
    )
    with (
        patch("shared.translator.translate_segments", side_effect=mock_translate),
        patch("shared.translator._GLOSSARY_PATH", glossary_file),
    ):
        result = translate_document(text)

    assert translated_inputs == ["Intro paragraph.", "Body paragraph."]
    assert "## References" in result
    assert "1. Smith et al. 2024. Nature." in result
    assert "> 譯：1. Smith" not in result
    assert "> 譯：Intro paragraph." in result


def test_translate_document_pure_reference_returns_text(tmp_path):
    """A doc that is ONLY a reference list (no body) returns as-is, no LLM."""
    glossary_file = tmp_path / "g.yaml"
    glossary_file.write_text("terms: {}\n", encoding="utf-8")

    def mock_translate(segments, **kwargs):
        raise AssertionError("translate_segments should NOT be called")

    text = "## References\n\n1. Foo\n2. Bar"
    with (
        patch("shared.translator.translate_segments", side_effect=mock_translate),
        patch("shared.translator._GLOSSARY_PATH", glossary_file),
    ):
        result = translate_document(text)

    assert result == text
