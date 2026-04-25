"""Tests for `.claude/skills/textbook-ingest/scripts/parse_book.py`.

Loaded via `importlib.util` because the skill path contains hyphens
(same pattern as `tests/skills/kb_search/test_search_pipeline.py`).

Coverage:

* EPUB happy path — synthetic EPUB built in-memory with `ebooklib.epub`,
  asserts metadata, chapter count, section_anchors, page estimation.
* EPUB chapter export — per-chapter `ch{n}.md` files written.
* Format dispatch — unsupported extension raises ValueError.
* PDF path is exercised by smoke `--help` only here; a full PDF fixture
  test requires a real PDF and is deferred to a manual smoke run.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_parse_book():
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / ".claude" / "skills" / "textbook-ingest" / "scripts" / "parse_book.py"
    spec = importlib.util.spec_from_file_location("parse_book_under_test", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


parse_book_mod = _load_parse_book()


# ---------------------------------------------------------------------------
# EPUB fixture
# ---------------------------------------------------------------------------


def _build_synthetic_epub(tmp_path: Path) -> Path:
    """Build a 3-chapter EPUB for testing."""
    pytest.importorskip("ebooklib")
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("urn:isbn:9781234567890")
    book.set_title("Synthetic Medical Textbook")
    book.set_language("en")
    book.add_author("Test Author A")
    book.add_author("Test Author B")
    book.add_metadata("DC", "publisher", "Test Publisher")
    book.add_metadata("DC", "date", "2026-04-01")

    chapters = []
    for i, (title, sections) in enumerate(
        [
            ("Cardiovascular Examination", ["1.1 Inspection", "1.2 Auscultation"]),
            (
                "Respiratory Examination",
                ["2.1 Breath sounds", "2.2 Adventitious sounds"],
            ),
            ("Abdominal Examination", ["3.1 Palpation"]),
        ],
        start=1,
    ):
        body_html = f"<h1>{title}</h1>" + "".join(
            f"<h2>{anchor}</h2><p>{anchor} body content. "
            + ("Lorem ipsum dolor sit amet. " * 80)
            + "</p>"
            for anchor in sections
        )
        ch = epub.EpubHtml(
            title=title,
            file_name=f"chap_{i:02d}.xhtml",
            lang="en",
        )
        ch.content = f"<html><body>{body_html}</body></html>"
        book.add_item(ch)
        chapters.append(ch)

    book.toc = tuple(epub.Link(c.file_name, c.title, c.file_name) for c in chapters)
    book.spine = ["nav"] + chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub_path = tmp_path / "synthetic.epub"
    epub.write_epub(str(epub_path), book)
    return epub_path


# ---------------------------------------------------------------------------
# EPUB tests
# ---------------------------------------------------------------------------


def test_epub_metadata_extracted(tmp_path: Path):
    pytest.importorskip("ebooklib")
    epub_path = _build_synthetic_epub(tmp_path)

    outline = parse_book_mod.parse_book(epub_path)

    assert outline.status == "ok"
    assert outline.strategy == "epub_nav"
    md = outline.book_metadata
    assert md.title == "Synthetic Medical Textbook"
    assert md.authors == ["Test Author A", "Test Author B"]
    assert md.language == "en"
    assert md.publisher == "Test Publisher"
    assert md.pub_year == 2026


def test_epub_chapters_in_spine_order(tmp_path: Path):
    pytest.importorskip("ebooklib")
    epub_path = _build_synthetic_epub(tmp_path)

    outline = parse_book_mod.parse_book(epub_path)

    assert len(outline.chapters) == 3
    titles = [c.title for c in outline.chapters]
    assert titles == [
        "Cardiovascular Examination",
        "Respiratory Examination",
        "Abdominal Examination",
    ]
    indices = [c.index for c in outline.chapters]
    assert indices == [1, 2, 3]


def test_epub_section_anchors_extracted_from_h2(tmp_path: Path):
    pytest.importorskip("ebooklib")
    epub_path = _build_synthetic_epub(tmp_path)

    outline = parse_book_mod.parse_book(epub_path)

    ch1 = outline.chapters[0]
    assert ch1.section_anchors == ["1.1 Inspection", "1.2 Auscultation"]
    ch2 = outline.chapters[1]
    assert ch2.section_anchors == ["2.1 Breath sounds", "2.2 Adventitious sounds"]
    ch3 = outline.chapters[2]
    assert ch3.section_anchors == ["3.1 Palpation"]


def test_epub_page_estimation_monotonic(tmp_path: Path):
    """Page numbers should be monotonic across chapters (cumulative word count)."""
    pytest.importorskip("ebooklib")
    epub_path = _build_synthetic_epub(tmp_path)

    outline = parse_book_mod.parse_book(epub_path)

    for prev, curr in zip(outline.chapters, outline.chapters[1:]):
        assert curr.page_start >= prev.page_end
    # Each chapter has at least one estimated page
    for ch in outline.chapters:
        assert ch.page_end >= ch.page_start
        assert ch.page_start >= 1


def test_epub_export_chapters_dir_writes_files(tmp_path: Path):
    pytest.importorskip("ebooklib")
    epub_path = _build_synthetic_epub(tmp_path)
    out_dir = tmp_path / "chapter-md"

    outline = parse_book_mod.parse_book(epub_path, export_chapters_dir=out_dir)

    assert outline.status == "ok"
    written = sorted(out_dir.glob("ch*.md"))
    assert [p.name for p in written] == ["ch1.md", "ch2.md", "ch3.md"]
    ch1_text = (out_dir / "ch1.md").read_text(encoding="utf-8")
    assert "Chapter 1 — Cardiovascular Examination" in ch1_text
    assert "Lorem ipsum" in ch1_text  # body content survived html → text


def test_epub_toc_yaml_rejected(tmp_path: Path):
    """--toc-yaml is PDF-only; EPUB has authoritative nav."""
    pytest.importorskip("ebooklib")
    epub_path = _build_synthetic_epub(tmp_path)
    toc_yaml = tmp_path / "toc.yaml"
    toc_yaml.write_text("chapters: []", encoding="utf-8")

    with pytest.raises(ValueError, match="EPUB has authoritative nav"):
        parse_book_mod.parse_book(epub_path, toc_yaml=toc_yaml)


# ---------------------------------------------------------------------------
# Dispatch tests
# ---------------------------------------------------------------------------


def test_unsupported_extension_raises(tmp_path: Path):
    bad = tmp_path / "not-a-book.docx"
    bad.write_bytes(b"")
    with pytest.raises(ValueError, match="unsupported file extension"):
        parse_book_mod.parse_book(bad)


def test_missing_epub_raises_filenotfound(tmp_path: Path):
    pytest.importorskip("ebooklib")
    missing = tmp_path / "does-not-exist.epub"
    with pytest.raises(FileNotFoundError):
        parse_book_mod.parse_book(missing)
