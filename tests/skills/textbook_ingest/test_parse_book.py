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


# ---------------------------------------------------------------------------
# v2 walker — figures, tables, math (ADR-011 §3.4.1)
# ---------------------------------------------------------------------------

# 1×1 transparent PNG (binary literal)
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "8900000001735247420aaee91c0000000d49444154789c63f8ff9f0100050001"
    "1f1f1f190000000049454e44ae426082"
)


def _build_synthetic_epub_v2(tmp_path: Path) -> Path:
    """Build a 1-chapter EPUB containing img / table / math elements."""
    pytest.importorskip("ebooklib")
    from ebooklib import epub

    book = epub.EpubBook()
    book.set_identifier("urn:isbn:9789876543210")
    book.set_title("Walker Test Book")
    book.set_language("en")
    book.add_author("Walker Author")
    book.add_metadata("DC", "publisher", "WP")
    book.add_metadata("DC", "date", "2026-04-26")

    img_item = epub.EpubItem(
        uid="img1",
        file_name="Images/fig1.png",
        media_type="image/png",
        content=_TINY_PNG,
    )
    book.add_item(img_item)

    body_html = (
        "<h1>Energy Sources</h1>"
        "<h2>1.1 Introduction</h2>"
        "<p>Lead paragraph before the figure.</p>"
        "<figure>"
        '<img src="../Images/fig1.png" alt="ATP-PCr kinetics curve">'
        "<figcaption>Schematic of ATP-PCr energy system kinetics</figcaption>"
        "</figure>"
        "<p>Following text references the curve.</p>"
        "<h2>1.2 Phosphagen System</h2>"
        "<p>Body before table.</p>"
        "<table><caption>ATP yield per substrate</caption>"
        "<thead><tr><th>System</th><th>ATP/glucose</th></tr></thead>"
        "<tbody><tr><td>Glycolysis</td><td>2</td></tr>"
        "<tr><td>Oxidative</td><td>30</td></tr></tbody></table>"
        "<p>Body after table.</p>"
        '<math alttext="\\frac{1}{2}mv^2"><mfrac><mn>1</mn><mn>2</mn></mfrac>'
        "<mi>m</mi><msup><mi>v</mi><mn>2</mn></msup></math>"
    )
    ch = epub.EpubHtml(title="Energy Sources", file_name="Text/chap_01.xhtml", lang="en")
    ch.content = f"<html><body>{body_html}</body></html>"
    book.add_item(ch)

    book.toc = (epub.Link(ch.file_name, ch.title, ch.file_name),)
    book.spine = ["nav", ch]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub_path = tmp_path / "walker.epub"
    epub.write_epub(str(epub_path), book)
    return epub_path


class TestWalkerEpubFigures:
    def test_img_extracted_with_binary(self, tmp_path: Path):
        pytest.importorskip("ebooklib")
        epub_path = _build_synthetic_epub_v2(tmp_path)
        outline = parse_book_mod.parse_book(epub_path)

        ch = outline.chapters[0]
        assert len(ch.figures) == 1
        fig = ch.figures[0]
        assert fig.ref == "fig-1-1"
        assert fig.binary == _TINY_PNG
        assert fig.extension == ".png"
        # Caption from <figcaption>
        assert "ATP-PCr energy system kinetics" in fig.caption
        # tied_to_section: figure appears under 1.1 Introduction
        assert fig.tied_to_section == "1.1 Introduction"
        # Placeholder marker present
        assert fig.placeholder == "<<FIG:fig-1-1>>"

    def test_placeholder_in_chapter_text(self, tmp_path: Path):
        pytest.importorskip("ebooklib")
        epub_path = _build_synthetic_epub_v2(tmp_path)
        out_dir = tmp_path / "chapters"
        outline = parse_book_mod.parse_book(epub_path, export_chapters_dir=out_dir)
        assert outline.status == "ok"
        ch1_text = (out_dir / "ch1.md").read_text(encoding="utf-8")
        assert "<<FIG:fig-1-1>>" in ch1_text
        assert "<<TAB:tab-1-1>>" in ch1_text


class TestWalkerEpubTables:
    def test_table_to_markdown(self, tmp_path: Path):
        pytest.importorskip("ebooklib")
        epub_path = _build_synthetic_epub_v2(tmp_path)
        outline = parse_book_mod.parse_book(epub_path)

        ch = outline.chapters[0]
        assert len(ch.tables) == 1
        tab = ch.tables[0]
        assert tab.ref == "tab-1-1"
        assert "ATP yield per substrate" in tab.caption
        # Markdown table contains header + body cells
        assert "| System | ATP/glucose |" in tab.markdown
        assert "| Glycolysis | 2 |" in tab.markdown
        assert "| Oxidative | 30 |" in tab.markdown
        # tied_to_section: table appears under 1.2 Phosphagen System
        assert tab.tied_to_section == "1.2 Phosphagen System"


class TestWalkerEpubMath:
    def test_math_uses_alttext(self, tmp_path: Path):
        pytest.importorskip("ebooklib")
        epub_path = _build_synthetic_epub_v2(tmp_path)
        out_dir = tmp_path / "chapters"
        parse_book_mod.parse_book(epub_path, export_chapters_dir=out_dir)
        ch1_text = (out_dir / "ch1.md").read_text(encoding="utf-8")
        # alttext from <math alttext="\frac{1}{2}mv^2"> rendered as $$...$$
        assert r"$$\frac{1}{2}mv^2$$" in ch1_text


class TestAttachmentsExport:
    def test_writes_figure_binary_and_table_md(self, tmp_path: Path):
        pytest.importorskip("ebooklib")
        epub_path = _build_synthetic_epub_v2(tmp_path)
        attachments = tmp_path / "Attachments" / "Books" / "walker"
        out_dir = tmp_path / "chapters"

        parse_book_mod.parse_book(
            epub_path,
            export_chapters_dir=out_dir,
            attachments_base_dir=attachments,
        )

        # Figure binary at expected path
        fig_path = attachments / "ch1" / "fig-1-1.png"
        assert fig_path.exists()
        assert fig_path.read_bytes() == _TINY_PNG

        # Table markdown at expected path with caption header
        tab_path = attachments / "ch1" / "tab-1-1.md"
        assert tab_path.exists()
        tab_md = tab_path.read_text(encoding="utf-8")
        assert "ATP yield per substrate" in tab_md
        assert "| System | ATP/glucose |" in tab_md

    def test_attachments_only_mode_skips_chapter_md(self, tmp_path: Path):
        pytest.importorskip("ebooklib")
        epub_path = _build_synthetic_epub_v2(tmp_path)
        attachments = tmp_path / "Attachments" / "Books" / "walker"

        parse_book_mod.parse_book(epub_path, attachments_base_dir=attachments)
        # Attachments still written
        assert (attachments / "ch1" / "fig-1-1.png").exists()
        assert (attachments / "ch1" / "tab-1-1.md").exists()


class TestOutlineJsonShape:
    def test_figures_and_tables_serialised(self, tmp_path: Path):
        pytest.importorskip("ebooklib")
        epub_path = _build_synthetic_epub_v2(tmp_path)
        outline = parse_book_mod.parse_book(epub_path)
        d = parse_book_mod._outline_to_dict(outline)
        ch = d["chapters"][0]
        assert "figures" in ch and "tables" in ch
        assert ch["figures"][0]["ref"] == "fig-1-1"
        assert ch["figures"][0]["extension"] == ".png"
        # Binary NOT in JSON (would explode size + non-serialisable)
        assert "binary" not in ch["figures"][0]
        assert ch["tables"][0]["ref"] == "tab-1-1"
        # Markdown NOT in JSON (lives on disk under Attachments)
        assert "markdown" not in ch["tables"][0]


class TestBackwardsCompat:
    def test_existing_text_only_export_still_works(self, tmp_path: Path):
        """Old fixture (no img/table/math) should still produce identical
        text-only output — backwards compat for existing tests."""
        pytest.importorskip("ebooklib")
        epub_path = _build_synthetic_epub(tmp_path)
        out_dir = tmp_path / "chapters"
        outline = parse_book_mod.parse_book(epub_path, export_chapters_dir=out_dir)
        assert outline.status == "ok"
        # Chapter md still written
        assert (out_dir / "ch1.md").exists()
        # No figures / tables for the old fixture
        for ch in outline.chapters:
            assert ch.figures == []
            assert ch.tables == []


class TestHtmlTableHelper:
    def test_simple_table(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            "<table><caption>X</caption>"
            "<tr><th>A</th><th>B</th></tr>"
            "<tr><td>1</td><td>2</td></tr></table>",
            "html.parser",
        )
        md, cap = parse_book_mod._html_table_to_markdown(soup.find("table"))
        assert cap == "X"
        assert "| A | B |" in md
        assert "| 1 | 2 |" in md
        assert "| --- | --- |" in md

    def test_pipes_in_cells_escaped(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            "<table><tr><td>a|b</td><td>c</td></tr></table>",
            "html.parser",
        )
        md, _cap = parse_book_mod._html_table_to_markdown(soup.find("table"))
        assert r"a\|b" in md

    def test_uneven_rows_padded(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            "<table><tr><th>A</th><th>B</th><th>C</th></tr><tr><td>1</td></tr></table>",
            "html.parser",
        )
        md, _cap = parse_book_mod._html_table_to_markdown(soup.find("table"))
        # Body row padded to 3 cols
        assert "| 1 |  |  |" in md

    def test_rowspan_replicates_cell_down(self):
        """A cell with rowspan=2 should appear in two consecutive rows."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            "<table>"
            "<tr><th>A</th><th>B</th></tr>"
            "<tr><td rowspan='2'>x</td><td>y</td></tr>"
            "<tr><td>z</td></tr>"
            "</table>",
            "html.parser",
        )
        md, _cap = parse_book_mod._html_table_to_markdown(soup.find("table"))
        # Row 1 of body: x, y; Row 2 of body: x replicated, z
        assert "| x | y |" in md
        assert "| x | z |" in md

    def test_colspan_replicates_cell_right(self):
        """A cell with colspan=2 should fill two columns."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            "<table>"
            "<tr><th>A</th><th>B</th><th>C</th></tr>"
            "<tr><td colspan='2'>span</td><td>solo</td></tr>"
            "</table>",
            "html.parser",
        )
        md, _cap = parse_book_mod._html_table_to_markdown(soup.find("table"))
        # The colspan cell is replicated in both columns it occupies
        assert "| span | span | solo |" in md

    def test_nested_table_rows_not_absorbed_by_outer(self):
        """An inner <table> inside a <td> must not contribute rows to the outer table."""
        from bs4 import BeautifulSoup

        html = (
            "<table>"
            "<tr><th>OuterA</th><th>OuterB</th></tr>"
            "<tr><td>outer1</td>"
            "<td><table><tr><td>inner1</td><td>inner2</td></tr></table></td>"
            "</tr>"
            "<tr><td>outer3</td><td>outer4</td></tr>"
            "</table>"
        )
        soup = BeautifulSoup(html, "html.parser")
        outer = soup.find("table")
        md, _cap = parse_book_mod._html_table_to_markdown(outer)
        # Outer table should have exactly 3 rows (1 header + 2 body), each 2 cols
        body_lines = [ln for ln in md.split("\n") if ln.startswith("| ") and "---" not in ln]
        assert len(body_lines) == 3
        assert "| OuterA | OuterB |" in md
        assert "| outer3 | outer4 |" in md
        # The inner cells should appear inside one of the outer cells (as
        # cell text via get_text(strip=True, separator=" ")), not as their
        # own row in the outer table
        assert "outer3" in md and "outer4" in md

    def test_no_thead_no_th_preserves_first_row_as_data(self):
        """A table with no <thead> and no <th> in row 1 must keep row 1 as data."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            "<table>"
            "<tr><td>data1</td><td>data2</td></tr>"
            "<tr><td>data3</td><td>data4</td></tr>"
            "</table>",
            "html.parser",
        )
        md, _cap = parse_book_mod._html_table_to_markdown(soup.find("table"))
        # Both source rows must appear in body — old behavior silently
        # promoted row 1 to header and dropped data1/data2.
        assert "| data1 | data2 |" in md
        assert "| data3 | data4 |" in md

    def test_explicit_thead_used_as_header(self):
        """Explicit <thead> rows are used as the header even when other rows have <th>."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            "<table>"
            "<thead><tr><th>H1</th><th>H2</th></tr></thead>"
            "<tbody>"
            "<tr><td>d1</td><td>d2</td></tr>"
            "<tr><td>d3</td><td>d4</td></tr>"
            "</tbody>"
            "</table>",
            "html.parser",
        )
        md, _cap = parse_book_mod._html_table_to_markdown(soup.find("table"))
        assert md.splitlines()[0] == "| H1 | H2 |"
        assert "| d1 | d2 |" in md
        assert "| d3 | d4 |" in md


class TestHtmlMathHelper:
    def test_alttext_used(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            '<math alttext="E=mc^2"><mi>E</mi></math>',
            "html.parser",
        )
        out = parse_book_mod._html_math_to_latex(soup.find("math"))
        assert out == "$$E=mc^2$$"

    def test_falls_back_to_text_content(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            "<math><mi>x</mi><mo>+</mo><mn>1</mn></math>",
            "html.parser",
        )
        out = parse_book_mod._html_math_to_latex(soup.find("math"))
        # No alttext → use rendered text content
        assert out.startswith("$$")
        assert "x" in out and "1" in out

    def test_empty_math_returns_empty(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<math></math>", "html.parser")
        assert parse_book_mod._html_math_to_latex(soup.find("math")) == ""

    def test_mfrac_without_alttext_emits_frac(self):
        """`<mfrac><mn>1</mn><mn>2</mn></mfrac>` should yield `\\frac{1}{2}`,
        not the digit-collapsed `12` the old fallback produced."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            "<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>",
            "html.parser",
        )
        out = parse_book_mod._html_math_to_latex(soup.find("math"))
        assert out == "$$\\frac{1}{2}$$"

    def test_msup_without_alttext_emits_caret(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            "<math><msup><mi>x</mi><mn>2</mn></msup></math>",
            "html.parser",
        )
        out = parse_book_mod._html_math_to_latex(soup.find("math"))
        assert out == "$$x^{2}$$"

    def test_msub_without_alttext_emits_underscore(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            "<math><msub><mi>H</mi><mn>2</mn></msub><mi>O</mi></math>",
            "html.parser",
        )
        out = parse_book_mod._html_math_to_latex(soup.find("math"))
        assert out == "$$H_{2}O$$"

    def test_msqrt_without_alttext_emits_sqrt(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            "<math><msqrt><mn>2</mn></msqrt></math>",
            "html.parser",
        )
        out = parse_book_mod._html_math_to_latex(soup.find("math"))
        assert out == "$$\\sqrt{2}$$"

    def test_alttext_still_takes_priority_over_walker(self):
        """alttext takes priority over the structural walker."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(
            '<math alttext="\\frac{a}{b}"><mfrac><mi>x</mi><mi>y</mi></mfrac></math>',
            "html.parser",
        )
        out = parse_book_mod._html_math_to_latex(soup.find("math"))
        # alttext beats the structural walker output
        assert out == "$$\\frac{a}{b}$$"


class TestExportChapterAttachmentsValidation:
    """`_validate_attachment_ref` and `_validate_attachment_extension` block
    path-traversal writes outside the attachments dir.

    Today the walker only emits `fig-{int}-{int}` / `tab-{int}-{int}` refs
    so the surface is zero, but the export entrypoint is now a public-ish
    boundary (skill driver may eventually round-trip via JSON outline) and
    deserves defense-in-depth.
    """

    def test_safe_ref_accepted(self):
        parse_book_mod._validate_attachment_ref("fig-1-3", kind="figure")
        parse_book_mod._validate_attachment_ref("tab-12-7", kind="table")

    def test_path_traversal_ref_rejected(self):
        with pytest.raises(ValueError, match="unsafe figure ref"):
            parse_book_mod._validate_attachment_ref("../../evil", kind="figure")
        with pytest.raises(ValueError, match="unsafe table ref"):
            parse_book_mod._validate_attachment_ref("../../etc/passwd", kind="table")

    def test_path_separator_ref_rejected(self):
        with pytest.raises(ValueError):
            parse_book_mod._validate_attachment_ref("foo/bar", kind="figure")
        with pytest.raises(ValueError):
            parse_book_mod._validate_attachment_ref("foo\\bar", kind="figure")

    def test_empty_ref_rejected(self):
        with pytest.raises(ValueError):
            parse_book_mod._validate_attachment_ref("", kind="figure")

    def test_safe_extension_accepted(self):
        for ext in (".png", ".jpg", ".svg", ".webp"):
            parse_book_mod._validate_attachment_extension(ext)

    def test_unsafe_extension_rejected(self):
        for ext in ("../sh", ".png/../evil", "png", "", ".../etc"):
            with pytest.raises(ValueError, match="unsafe attachment extension"):
                parse_book_mod._validate_attachment_extension(ext)

    def test_export_blocks_traversal_at_runtime(self, tmp_path):
        """End-to-end: a malicious figure ref raises before writing."""
        Chapter = parse_book_mod.Chapter
        ChapterFigure = parse_book_mod.ChapterFigure
        attach = tmp_path / "attach"
        evil_fig = ChapterFigure(
            ref="../../escape",
            extension=".png",
            alt="",
            caption="",
            tied_to_section="",
            placeholder="<<FIG:evil>>",
            binary=b"\x89PNG",
        )
        chapter = Chapter(
            index=1,
            title="Test",
            page_start=1,
            page_end=1,
            section_anchors=[],
            figures=[evil_fig],
            tables=[],
        )
        with pytest.raises(ValueError, match="unsafe figure ref"):
            parse_book_mod._export_chapter_attachments(chapter, attach)
        # No file should have been written outside attach/
        assert not (tmp_path / "escape.png").exists()
