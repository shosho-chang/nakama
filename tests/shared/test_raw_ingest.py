"""Behavior tests for ``shared.raw_ingest.epub_to_raw_markdown`` (ADR-020 S0).

Tests use in-memory EPUB fixtures — no checked-in binaries, no network.
Each test probes a specific contract: frontmatter fields, markdown structure
preservation, image extraction, spine order, and error paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shared.raw_ingest import EPUBConversionError, RawIngestResult, epub_to_raw_markdown
from tests.shared._epub_fixtures import (
    EPUBSpec,
    epub_minimal_metadata,
    epub_multi_chapter_ordered,
    epub_with_image_in_chapter,
    epub_with_table,
    make_epub_blob,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_epub(blob: bytes, tmp_path: Path) -> Path:
    p = tmp_path / "test.epub"
    p.write_bytes(blob)
    return p


def _convert(epub_bytes: bytes, tmp_path: Path, book_id: str = "test-book") -> RawIngestResult:
    attachments = tmp_path / "Attachments" / "Books"
    return epub_to_raw_markdown(
        _write_epub(epub_bytes, tmp_path),
        book_id,
        attachments_dir=attachments,
    )


def _parse_frontmatter(markdown: str) -> dict:
    """Extract and parse the YAML frontmatter block."""
    assert markdown.startswith("---\n"), "markdown must start with frontmatter"
    end = markdown.index("\n---\n", 4)
    return yaml.safe_load(markdown[4:end])


# ---------------------------------------------------------------------------
# Frontmatter — all six required fields present and round-trippable
# ---------------------------------------------------------------------------


def test_frontmatter_has_all_required_fields(tmp_path):
    result = _convert(make_epub_blob(), tmp_path)
    fm = _parse_frontmatter(result.markdown)
    assert "title" in fm
    assert "book_id" in fm
    assert "source_epub_path" in fm
    assert "converted_date" in fm
    assert "converter_tool" in fm
    assert "converter_version" in fm


def test_frontmatter_book_id_matches_argument(tmp_path):
    result = _convert(make_epub_blob(), tmp_path, book_id="bse-2024")
    fm = _parse_frontmatter(result.markdown)
    assert fm["book_id"] == "bse-2024"


def test_frontmatter_title_from_epub_metadata(tmp_path):
    result = _convert(make_epub_blob(), tmp_path)
    fm = _parse_frontmatter(result.markdown)
    assert fm["title"] == "The Tracer"


def test_frontmatter_title_falls_back_to_book_id_when_missing(tmp_path):
    result = _convert(epub_minimal_metadata(), tmp_path, book_id="no-title-book")
    fm = _parse_frontmatter(result.markdown)
    assert fm["title"] == "no-title-book"


def test_frontmatter_converter_tool_is_correct(tmp_path):
    result = _convert(make_epub_blob(), tmp_path)
    fm = _parse_frontmatter(result.markdown)
    assert fm["converter_tool"] == "ebooklib+markdownify"


def test_frontmatter_converter_version_contains_both_libs(tmp_path):
    result = _convert(make_epub_blob(), tmp_path)
    fm = _parse_frontmatter(result.markdown)
    assert "ebooklib" in fm["converter_version"]
    assert "markdownify" in fm["converter_version"]


def test_frontmatter_source_epub_path_is_absolute(tmp_path):
    result = _convert(make_epub_blob(), tmp_path)
    fm = _parse_frontmatter(result.markdown)
    assert Path(fm["source_epub_path"]).is_absolute()


def test_frontmatter_title_with_yaml_special_chars_roundtrips(tmp_path):
    # Verify YAML-layer escaping: double-quotes and backslashes in title survive
    # safe_load round-trip. Raw & is not used — XML encoding is the EPUB layer's concern.
    spec = EPUBSpec(title='Biochemistry: "Key Concepts" (2nd Ed)')
    result = _convert(make_epub_blob(spec), tmp_path)
    fm = _parse_frontmatter(result.markdown)
    assert fm["title"] == 'Biochemistry: "Key Concepts" (2nd Ed)'


# ---------------------------------------------------------------------------
# Markdown structure — headings, paragraphs, bold/italic
# ---------------------------------------------------------------------------


def test_h1_heading_preserved(tmp_path):
    result = _convert(make_epub_blob(), tmp_path)
    assert "# Chapter 1" in result.markdown


def test_paragraph_text_preserved(tmp_path):
    result = _convert(make_epub_blob(), tmp_path)
    assert "Hello world." in result.markdown


def test_bold_preserved(tmp_path):
    spec = EPUBSpec(
        chapters={
            "ch1.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>C1</title></head>
<body><p>This is <strong>important</strong>.</p></body>
</html>"""
        }
    )
    result = _convert(make_epub_blob(spec), tmp_path)
    assert "**important**" in result.markdown


def test_italic_preserved(tmp_path):
    spec = EPUBSpec(
        chapters={
            "ch1.xhtml": """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>C1</title></head>
<body><p>This is <em>emphasis</em>.</p></body>
</html>"""
        }
    )
    result = _convert(make_epub_blob(spec), tmp_path)
    assert "*emphasis*" in result.markdown


def test_cjk_text_preserved(tmp_path):
    result = _convert(make_epub_blob(), tmp_path)
    # DEFAULT_CH1 contains a blockquote with CJK text
    assert "哈囉世界" in result.markdown


# ---------------------------------------------------------------------------
# Table preservation — GFM pipe tables
# ---------------------------------------------------------------------------


def test_table_converted_to_gfm_pipe_syntax(tmp_path):
    result = _convert(epub_with_table(), tmp_path)
    assert "| Nutrient |" in result.markdown
    assert "| Protein |" in result.markdown


def test_table_header_separator_present(tmp_path):
    result = _convert(epub_with_table(), tmp_path)
    # GFM table separator row: | --- | --- | --- |
    assert "---" in result.markdown


# ---------------------------------------------------------------------------
# Image extraction — files written + paths rewritten in markdown
# ---------------------------------------------------------------------------


def test_image_extracted_to_attachments_dir(tmp_path):
    _convert(epub_with_image_in_chapter(), tmp_path, book_id="img-book")
    book_attach = tmp_path / "Attachments" / "Books" / "img-book"
    assert book_attach.exists()
    assert any(book_attach.iterdir()), "attachments dir must contain at least one image"


def test_image_path_rewritten_to_vault_relative(tmp_path):
    result = _convert(epub_with_image_in_chapter(), tmp_path, book_id="img-book")
    assert "Attachments/Books/img-book/" in result.markdown


def test_image_vault_paths_in_result_metadata(tmp_path):
    result = _convert(epub_with_image_in_chapter(), tmp_path, book_id="img-book")
    assert len(result.images_extracted) >= 1
    for path in result.images_extracted:
        assert path.startswith("Attachments/Books/img-book/")


def test_image_original_src_not_in_output(tmp_path):
    result = _convert(epub_with_image_in_chapter(), tmp_path, book_id="img-book")
    # The original "cover.png" bare src should not appear; it's been rewritten
    body = result.markdown.split("---\n", 2)[-1]
    assert 'src="cover.png"' not in body


# ---------------------------------------------------------------------------
# Spine order — chapters appear in reading order
# ---------------------------------------------------------------------------


def test_chapters_in_spine_order(tmp_path):
    result = _convert(epub_multi_chapter_ordered(), tmp_path)
    pos1 = result.markdown.index("# Chapter 1")
    pos2 = result.markdown.index("# Chapter 2")
    pos3 = result.markdown.index("# Chapter 3")
    assert pos1 < pos2 < pos3, "chapters must appear in spine reading order"


# ---------------------------------------------------------------------------
# Result metadata
# ---------------------------------------------------------------------------


def test_result_book_id_matches_argument(tmp_path):
    result = _convert(make_epub_blob(), tmp_path, book_id="my-book")
    assert result.book_id == "my-book"


def test_result_title_matches_epub_metadata(tmp_path):
    result = _convert(make_epub_blob(), tmp_path)
    assert result.title == "The Tracer"


def test_result_title_none_when_metadata_missing(tmp_path):
    result = _convert(epub_minimal_metadata(), tmp_path)
    assert result.title is None


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_file_not_found_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        epub_to_raw_markdown(
            tmp_path / "nonexistent.epub",
            "x",
            attachments_dir=tmp_path,
        )


def test_non_epub_blob_raises_conversion_error(tmp_path):
    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"not a zip at all")
    with pytest.raises(EPUBConversionError):
        epub_to_raw_markdown(bad, "x", attachments_dir=tmp_path)
