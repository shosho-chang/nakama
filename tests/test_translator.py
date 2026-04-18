"""shared/translator.py 單元測試。

測試不需要 Claude API 的純邏輯函式。
翻譯 API 整合測試需要 ANTHROPIC_API_KEY，標記為 slow。
"""

from unittest.mock import patch

from shared.translator import (
    add_glossary_term,
    format_bilingual_markdown,
    load_glossary,
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


def test_add_glossary_term_overwrite(tmp_path):
    glossary_file = tmp_path / "glossary.yaml"
    glossary_file.write_text("terms:\n  mitochondria: 線粒體\n", encoding="utf-8")
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


# ── translate_segments (mocked) ──


def test_translate_segments_empty():
    assert translate_segments([]) == []


def test_translate_segments_uses_claude(tmp_path):
    glossary_file = tmp_path / "g.yaml"
    glossary_file.write_text("terms: {}\n", encoding="utf-8")
    mock_response = '[{"index": 1, "translation": "粒線體是細胞的發電廠。"}]'
    with (
        patch("shared.translator.ask_claude", return_value=mock_response),
        patch("shared.translator._GLOSSARY_PATH", glossary_file),
    ):
        result = translate_segments(["Mitochondria are the powerhouses of the cell."])
    assert result == ["粒線體是細胞的發電廠。"]


def test_translate_segments_json_fallback(tmp_path):
    """當批次 JSON 解析失敗時，應降級為逐段翻譯。"""
    glossary_file = tmp_path / "g.yaml"
    glossary_file.write_text("terms: {}\n", encoding="utf-8")
    call_count = 0

    def mock_claude(prompt, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return "not valid json at all"
        return "降級譯文"

    with (
        patch("shared.translator.ask_claude", side_effect=mock_claude),
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
        patch("shared.translator.ask_claude", return_value=mock_response),
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
