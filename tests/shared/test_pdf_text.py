"""shared/pdf_text.py — 中文 PDF 抽字的排版清理。"""

from __future__ import annotations

import pytest

from shared.pdf_text import PdfTextError, clean_cjk_text, pdf_to_text


def test_removes_spaces_between_cjk_characters():
    # pypdf 抽中文 PDF 的典型殘渣：字與字之間插空白
    assert clean_cjk_text("訪 綱 ： 大 家 認 識") == "訪綱：大家認識"


def test_joins_one_character_per_line_breaks():
    raw = "身 心\n更\n健\n康\n，\n擁\n有\n53\n萬\n訂\n閱"
    assert clean_cjk_text(raw) == "身心更健康，擁有53萬訂閱"


def test_keeps_spaces_between_latin_words_and_bullet_structure():
    out = clean_cjk_text("你有自己的 me time 嗎？ ● 下一題")
    assert "me time" in out
    assert out.endswith("\n● 下一題")


def test_garbage_bytes_raise_pdf_text_error():
    with pytest.raises(PdfTextError):
        pdf_to_text(b"not a pdf at all")


def test_kangxi_radical_characters_count_as_cjk():
    # 「⼀」是康熙部首 U+2F00，有些 PDF 用它代替「一」
    assert clean_cjk_text("起 ⼀ 個 客 廳") == "起⼀個客廳"
