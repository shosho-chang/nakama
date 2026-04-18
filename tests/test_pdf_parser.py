"""Tests for shared.pdf_parser — PDF → Markdown 轉換。"""

from unittest.mock import MagicMock, patch

import pytest

from shared.pdf_parser import (
    _table_to_markdown,
    extract_tables,
    get_pdf_page_count,
    parse_pdf,
    parse_pdf_url,
)

# ── parse_pdf (local) ────────────────────────────────────────────────────────


class TestParsePdf:
    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="不存在"):
            parse_pdf(tmp_path / "nonexistent.pdf")

    def test_not_pdf_file(self, tmp_path):
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("hello")
        with pytest.raises(ValueError, match="非 PDF"):
            parse_pdf(txt_file)

    @patch.dict("sys.modules", {"pymupdf4llm": MagicMock()})
    def test_basic_parse(self, tmp_path):
        import sys

        mock_mod = sys.modules["pymupdf4llm"]
        mock_mod.to_markdown.return_value = "# Title\n\nSome content here."

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")
        result = parse_pdf(pdf_file)

        mock_mod.to_markdown.assert_called_once_with(str(pdf_file))
        assert "# Title" in result
        assert "Some content here." in result

    @patch.dict("sys.modules", {"pymupdf4llm": MagicMock()})
    def test_cleans_excessive_blank_lines(self, tmp_path):
        import sys

        mock_mod = sys.modules["pymupdf4llm"]
        mock_mod.to_markdown.return_value = "Line 1\n\n\n\n\n\nLine 2"

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")
        result = parse_pdf(pdf_file)

        assert "\n\n\n\n" not in result
        assert "Line 1" in result
        assert "Line 2" in result

    @patch.dict("sys.modules", {"pymupdf4llm": MagicMock()})
    def test_parse_failure_raises_runtime_error(self, tmp_path):
        import sys

        mock_mod = sys.modules["pymupdf4llm"]
        mock_mod.to_markdown.side_effect = Exception("corrupt PDF")

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")
        with pytest.raises(RuntimeError, match="解析失敗"):
            parse_pdf(pdf_file)

    @patch.dict("sys.modules", {"pymupdf4llm": MagicMock()})
    def test_returns_stripped_result(self, tmp_path):
        import sys

        mock_mod = sys.modules["pymupdf4llm"]
        mock_mod.to_markdown.return_value = "\n\n  Content  \n\n"

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")
        result = parse_pdf(pdf_file)
        assert result == "Content"

    @patch.dict("sys.modules", {"pymupdf4llm": MagicMock()})
    def test_with_tables_appends_table_section(self, tmp_path):
        import sys

        mock_mod = sys.modules["pymupdf4llm"]
        mock_mod.to_markdown.return_value = "# Research Paper\n\nText content."

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        mock_table = [["Gene", "Expression"], ["BRCA1", "2.3x"], ["TP53", "0.8x"]]
        mock_page = MagicMock()
        mock_page.extract_tables.return_value = [mock_table]
        mock_pdf_ctx = MagicMock()
        mock_pdf_ctx.__enter__ = MagicMock(return_value=mock_pdf_ctx)
        mock_pdf_ctx.__exit__ = MagicMock(return_value=False)
        mock_pdf_ctx.pages = [mock_page]
        mock_pdfplumber = MagicMock()
        mock_pdfplumber.open.return_value = mock_pdf_ctx

        with patch.dict("sys.modules", {"pdfplumber": mock_pdfplumber}):
            result = parse_pdf(pdf_file, with_tables=True)

        assert "## 表格" in result
        assert "Gene" in result
        assert "BRCA1" in result

    @patch.dict("sys.modules", {"pymupdf4llm": MagicMock()})
    def test_with_tables_false_no_table_section(self, tmp_path):
        import sys

        mock_mod = sys.modules["pymupdf4llm"]
        mock_mod.to_markdown.return_value = "Just text."

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")
        result = parse_pdf(pdf_file, with_tables=False)

        assert "## 表格" not in result


# ── _table_to_markdown ────────────────────────────────────────────────────────


class TestTableToMarkdown:
    def test_basic_table(self):
        table = [["Name", "Value"], ["A", "1"], ["B", "2"]]
        result = _table_to_markdown(table)
        assert "| Name | Value |" in result
        assert "| --- | --- |" in result
        assert "| A | 1 |" in result
        assert "| B | 2 |" in result

    def test_none_cells_replaced_with_empty(self):
        table = [["Col1", "Col2"], [None, "data"], ["val", None]]
        result = _table_to_markdown(table)
        assert result != ""
        assert "None" not in result

    def test_empty_table_returns_empty(self):
        assert _table_to_markdown([]) == ""

    def test_single_row_returns_empty(self):
        assert _table_to_markdown([["Header only"]]) == ""

    def test_newlines_in_cells_replaced(self):
        table = [["Header"], ["multi\nline"]]
        result = _table_to_markdown(table)
        assert "\n" not in result.split("|")[1]

    def test_uneven_rows_padded(self):
        table = [["A", "B", "C"], ["only_one"]]
        result = _table_to_markdown(table)
        assert result != ""
        assert "only_one" in result


# ── extract_tables ────────────────────────────────────────────────────────────


class TestExtractTables:
    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            extract_tables(tmp_path / "missing.pdf")

    def test_returns_markdown_tables(self, tmp_path):
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        mock_table = [["Metric", "Value"], ["VO2max", "58 mL/kg/min"]]
        mock_page = MagicMock()
        mock_page.extract_tables.return_value = [mock_table]
        mock_pdf_ctx = MagicMock()
        mock_pdf_ctx.__enter__ = MagicMock(return_value=mock_pdf_ctx)
        mock_pdf_ctx.__exit__ = MagicMock(return_value=False)
        mock_pdf_ctx.pages = [mock_page]
        mock_pdfplumber = MagicMock()
        mock_pdfplumber.open.return_value = mock_pdf_ctx

        with patch.dict("sys.modules", {"pdfplumber": mock_pdfplumber}):
            result = extract_tables(pdf_file)

        assert "Metric" in result
        assert "VO2max" in result
        assert "第 1 頁" in result

    def test_no_tables_returns_empty(self, tmp_path):
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        mock_page = MagicMock()
        mock_page.extract_tables.return_value = []
        mock_pdf_ctx = MagicMock()
        mock_pdf_ctx.__enter__ = MagicMock(return_value=mock_pdf_ctx)
        mock_pdf_ctx.__exit__ = MagicMock(return_value=False)
        mock_pdf_ctx.pages = [mock_page]
        mock_pdfplumber = MagicMock()
        mock_pdfplumber.open.return_value = mock_pdf_ctx

        with patch.dict("sys.modules", {"pdfplumber": mock_pdfplumber}):
            result = extract_tables(pdf_file)

        assert result == ""

    def test_exception_returns_empty_string(self, tmp_path):
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        mock_pdfplumber = MagicMock()
        mock_pdfplumber.open.side_effect = Exception("corrupt")

        with patch.dict("sys.modules", {"pdfplumber": mock_pdfplumber}):
            result = extract_tables(pdf_file)

        assert result == ""


# ── parse_pdf_url (Firecrawl) ────────────────────────────────────────────────


class TestParsePdfUrl:
    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="FIRECRAWL_API_KEY"):
            parse_pdf_url("https://example.com/doc.pdf")

    def test_basic_url_parse(self, monkeypatch):
        monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")

        mock_response = MagicMock()
        mock_response.markdown = "# Remote PDF\n\nContent"
        mock_app = MagicMock()
        mock_app.scrape_url.return_value = mock_response
        mock_cls = MagicMock(return_value=mock_app)

        mock_firecrawl = MagicMock()
        mock_firecrawl.FirecrawlApp = mock_cls

        with patch.dict("sys.modules", {"firecrawl": mock_firecrawl}):
            result = parse_pdf_url("https://example.com/doc.pdf")

        mock_cls.assert_called_once_with(api_key="fc-test-key")
        assert "# Remote PDF" in result

    def test_url_parse_with_ocr_mode(self, monkeypatch):
        monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")

        mock_response = MagicMock()
        mock_response.markdown = "OCR content"
        mock_app = MagicMock()
        mock_app.scrape_url.return_value = mock_response
        mock_cls = MagicMock(return_value=mock_app)

        mock_firecrawl = MagicMock()
        mock_firecrawl.FirecrawlApp = mock_cls

        with patch.dict("sys.modules", {"firecrawl": mock_firecrawl}):
            parse_pdf_url("https://example.com/scan.pdf", mode="ocr")

        call_args = mock_app.scrape_url.call_args
        parsers = call_args[1]["params"]["parsers"]
        assert parsers[0]["mode"] == "ocr"

    def test_url_parse_failure(self, monkeypatch):
        monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")

        mock_app = MagicMock()
        mock_app.scrape_url.side_effect = Exception("API error")
        mock_cls = MagicMock(return_value=mock_app)

        mock_firecrawl = MagicMock()
        mock_firecrawl.FirecrawlApp = mock_cls

        with patch.dict("sys.modules", {"firecrawl": mock_firecrawl}):
            with pytest.raises(RuntimeError, match="Firecrawl PDF 解析失敗"):
                parse_pdf_url("https://example.com/doc.pdf")


# ── get_pdf_page_count ───────────────────────────────────────────────────────


class TestGetPdfPageCount:
    def test_page_count(self, tmp_path):
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 fake")

        mock_doc = MagicMock()
        mock_doc.__len__ = MagicMock(return_value=42)
        mock_pymupdf = MagicMock()
        mock_pymupdf.open.return_value = mock_doc

        with patch.dict("sys.modules", {"pymupdf": mock_pymupdf}):
            count = get_pdf_page_count(pdf_file)

        assert count == 42
        mock_doc.close.assert_called_once()

    def test_nonexistent_file_returns_zero(self, tmp_path):
        assert get_pdf_page_count(tmp_path / "nope.pdf") == 0
