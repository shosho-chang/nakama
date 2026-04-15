"""Tests for shared.pdf_parser — PDF → Markdown 轉換。"""

from unittest.mock import MagicMock, patch

import pytest

from shared.pdf_parser import get_pdf_page_count, parse_pdf, parse_pdf_url

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

        # 2 blank lines = \n\n\n (3 newlines) is fine; 3+ blank lines should be collapsed
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


# ── parse_pdf_url (Firecrawl) ────────────────────────────────────────────────


class TestParsePdfUrl:
    def test_missing_api_key(self, monkeypatch):
        monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="FIRECRAWL_API_KEY"):
            parse_pdf_url("https://example.com/doc.pdf")

    def test_basic_url_parse(self, monkeypatch):
        monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")

        mock_app = MagicMock()
        mock_app.scrape_url.return_value = {"markdown": "# Remote PDF\n\nContent"}
        mock_cls = MagicMock(return_value=mock_app)

        mock_firecrawl = MagicMock()
        mock_firecrawl.FirecrawlApp = mock_cls

        with patch.dict("sys.modules", {"firecrawl": mock_firecrawl}):
            result = parse_pdf_url("https://example.com/doc.pdf")

        mock_cls.assert_called_once_with(api_key="fc-test-key")
        assert "# Remote PDF" in result

    def test_url_parse_with_ocr_mode(self, monkeypatch):
        monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-test-key")

        mock_app = MagicMock()
        mock_app.scrape_url.return_value = {"markdown": "OCR content"}
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
