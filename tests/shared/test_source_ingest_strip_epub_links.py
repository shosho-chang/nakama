"""Tests for EPUB internal link stripping in walker (Patch 1, 2026-05-08)."""

from __future__ import annotations

from pathlib import Path

from shared.source_ingest import _strip_epub_internal_links, walk_book_to_chapters


def test_strip_chapter_xhtml_link():
    text = "See ([Chapter 12](chapter12.xhtml)) for details."
    assert _strip_epub_internal_links(text) == "See (Chapter 12) for details."


def test_strip_chapter_xhtml_link_with_anchor():
    text = "See [Chapter 12](chapter12.xhtml#sec-1) for details."
    assert _strip_epub_internal_links(text) == "See Chapter 12 for details."


def test_strip_in_page_anchor_link():
    text = "Recent work (see [Smith 2020](#c10-bib-0048)) shows..."
    assert _strip_epub_internal_links(text) == "Recent work (see Smith 2020) shows..."


def test_strip_html_extension_too():
    text = "Refer to [Appendix A](appendixA.html) here."
    assert _strip_epub_internal_links(text) == "Refer to Appendix A here."


def test_preserves_image_syntax():
    text = "![figure caption](Attachments/Books/bse-2024/fig1-1.png)"
    assert _strip_epub_internal_links(text) == text


def test_preserves_external_url_links():
    text = "Visit [the journal site](https://example.org/article)."
    assert _strip_epub_internal_links(text) == text


def test_preserves_wikilinks():
    text = "See [[ATP]] and [[glycogen phosphorylase]]."
    assert _strip_epub_internal_links(text) == text


def test_multiple_links_in_one_paragraph():
    text = "See [ch1](chapter1.xhtml), [ch2](chapter2.xhtml#a), and [bib](#c-0001)."
    assert _strip_epub_internal_links(text) == "See ch1, ch2, and bib."


def test_walker_strips_links_in_verbatim_body(tmp_path: Path):
    raw = tmp_path / "test-book.md"
    raw.write_text(
        "---\nbook_id: test-book\n---\n"
        "# Chapter 1\n\n"
        "See [Chapter 12](chapter12.xhtml) and [Smith 2020](#c1-bib-0001).\n\n"
        "## Section 1.1\n\nBody.\n",
        encoding="utf-8",
    )
    chapters = walk_book_to_chapters(raw)
    assert len(chapters) == 1
    body = chapters[0].verbatim_body
    assert "chapter12.xhtml" not in body
    assert "c1-bib-0001" not in body
    assert "See Chapter 12 and Smith 2020." in body
