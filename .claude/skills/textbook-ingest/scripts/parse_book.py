#!/usr/bin/env python3
"""Book outline extractor for the ``textbook-ingest`` skill.

Extracts chapter boundaries from EPUB or PDF and emits a JSON outline
that the LLM-driven skill uses to drive per-chapter ingestion.

EPUB is the **primary path** (modern textbooks ship with ebook editions;
EPUB has authoritative chapter structure via the OPF spine + nav TOC).
PDF is the fallback for textbooks that ship print-only.

Strategies by format:

EPUB (preferred):
1. OPF metadata (title / author / publisher / language / pub_year / ISBN)
2. Nav TOC top-level entries → chapters (in spine reading order)
3. Sub-level TOC entries → ``section_anchors``
4. Per-chapter HTML → plain text via BeautifulSoup

PDF (fallback):
1. PDF outline / bookmarks (``doc.get_toc()``)
2. Manual override via ``--toc-yaml`` (caller-supplied YAML)
3. Heading regex fallback — scan first lines of each page for
   ``^(Chapter|第)\\s*\\d+`` patterns

If none succeed, the script writes ``status: needs_manual`` and exits
non-zero so the skill can surface the failure to the user.

Usage:

    # EPUB (preferred path)
    python .claude/skills/textbook-ingest/scripts/parse_book.py \\
        --path /path/to/textbook.epub \\
        --out /tmp/textbook-outline.json \\
        [--export-chapters-dir /tmp/textbook-chapters/]

    # PDF (fallback)
    python .claude/skills/textbook-ingest/scripts/parse_book.py \\
        --path /path/to/textbook.pdf \\
        --out /tmp/textbook-outline.json \\
        [--toc-yaml /path/to/manual-toc.yaml] \\
        [--export-chapters-dir /tmp/textbook-chapters/]

When ``--export-chapters-dir`` is set, per-chapter text is written as
``ch{n}.md`` files; the skill can then ``Read`` each chapter file
directly into Opus's 1M context.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# sys.path shim for ``shared.*`` imports (per
# memory/claude/feedback_skill_scaffolding_pitfalls.md, pitfall #1)
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@dataclass
class ChapterFigure:
    """Image extracted from chapter HTML (ADR-011 §3.4.1).

    `binary` is the raw image bytes pulled from the EPUB / PDF; the caller
    decides where to write it (typically ``Attachments/Books/{book_id}/ch{n}/``).
    `placeholder` is the inline marker that replaces the original ``<img>`` /
    ``<svg>`` element in the chapter text so downstream LLM passes can splice
    a Vision-generated description back in.
    """

    ref: str  # "fig-{chapter}-{N}"
    binary: bytes
    extension: str  # ".png" / ".jpg" / ".svg" / ".gif" / ".webp"
    alt: str
    caption: str
    tied_to_section: str  # closest preceding h2/h3 anchor; "" if before first
    placeholder: str  # "<<FIG:fig-{chapter}-{N}>>"


@dataclass
class ChapterTable:
    """Table extracted from chapter HTML, normalised to markdown (ADR-011 §3.4.1).

    `markdown` is the GFM table representation, intended to be written to
    ``Attachments/Books/{book_id}/ch{n}/tab-N.md``. The chapter text gets a
    ``<<TAB:...>>`` placeholder where the original ``<table>`` lived.
    """

    ref: str  # "tab-{chapter}-{N}"
    markdown: str
    caption: str
    tied_to_section: str
    placeholder: str  # "<<TAB:tab-{chapter}-{N}>>"


@dataclass
class Chapter:
    index: int
    title: str
    page_start: int  # 1-based
    page_end: int  # 1-based, inclusive
    section_anchors: list[str]
    figures: list[ChapterFigure] = field(default_factory=list)
    tables: list[ChapterTable] = field(default_factory=list)


@dataclass
class BookMetadata:
    title: str
    authors: list[str]
    pub_year: int | None
    publisher: str | None
    language: str
    page_count: int


@dataclass
class Outline:
    status: str  # "ok" | "needs_manual"
    strategy: str  # "pdf_outline" | "manual_toc" | "regex_fallback"
    book_metadata: BookMetadata
    chapters: list[Chapter]
    warnings: list[str]


def _extract_book_metadata(doc) -> BookMetadata:
    """Pull book metadata from PDF document properties."""
    md = doc.metadata or {}
    title = md.get("title") or ""
    author_field = md.get("author") or ""
    authors = [a.strip() for a in re.split(r"[,;]", author_field) if a.strip()]

    pub_year = None
    pub_date = md.get("creationDate") or ""
    year_match = re.search(r"(\d{4})", pub_date)
    if year_match:
        pub_year = int(year_match.group(1))

    return BookMetadata(
        title=title,
        authors=authors,
        pub_year=pub_year,
        publisher=md.get("creator") or None,
        language="en",  # caller can override; auto-detect deferred to Phase 2
        page_count=doc.page_count,
    )


def _chapters_from_toc(doc, *, max_chapters: int = 200) -> list[Chapter]:
    """Build chapter list from PDF outline (toc).

    Top-level outline entries (level 1) become chapters. Level 2+ entries
    under a chapter become section_anchors. Sub-sub entries are dropped.
    """
    toc = doc.get_toc()  # list of [level, title, page] (1-based pages)
    if not toc:
        return []

    chapters: list[Chapter] = []
    current: Chapter | None = None
    for level, title, page in toc:
        title = (title or "").strip()
        if not title:
            continue
        if level == 1:
            if current is not None:
                # finalize previous chapter (page_end set when next ch1 starts)
                current.page_end = max(current.page_end, page - 1)
                chapters.append(current)
            current = Chapter(
                index=len(chapters) + 1,
                title=title,
                page_start=page,
                page_end=page,  # tentative, fixed at next iteration
                section_anchors=[],
            )
            if len(chapters) >= max_chapters:
                break
        elif level == 2 and current is not None:
            current.section_anchors.append(title)

    if current is not None:
        current.page_end = max(current.page_end, doc.page_count)
        chapters.append(current)

    # Tidy: ensure page_end of each chapter matches start of next - 1
    for i, ch in enumerate(chapters):
        if i + 1 < len(chapters):
            ch.page_end = max(ch.page_start, chapters[i + 1].page_start - 1)
        else:
            ch.page_end = doc.page_count

    return chapters


def _chapters_from_yaml(toc_yaml_path: Path) -> list[Chapter]:
    """Load manual chapter boundaries from a YAML file.

    Expected schema::

        chapters:
          - index: 1
            title: "Introduction"
            page_start: 1
            page_end: 25
            section_anchors: ["1.1 Foo", "1.2 Bar"]
          - ...
    """
    import yaml

    raw = yaml.safe_load(toc_yaml_path.read_text(encoding="utf-8")) or {}
    chapters_raw = raw.get("chapters") or []
    chapters: list[Chapter] = []
    for entry in chapters_raw:
        chapters.append(
            Chapter(
                index=int(entry["index"]),
                title=str(entry["title"]),
                page_start=int(entry["page_start"]),
                page_end=int(entry["page_end"]),
                section_anchors=list(entry.get("section_anchors") or []),
            )
        )
    return chapters


_CHAPTER_HEADING_RE = re.compile(r"^\s*(?:Chapter|第)\s*(\d+)\b", re.IGNORECASE)


def _chapters_from_regex(doc) -> list[Chapter]:
    """Last-resort chapter detection by scanning page text for headings."""
    candidates: list[tuple[int, int, str]] = []  # (chapter_num, page, line)
    for page_idx in range(doc.page_count):
        page = doc.load_page(page_idx)
        text = page.get_text("text") or ""
        # only consider top-of-page lines (within first 5 non-empty)
        lines: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
            if len(lines) >= 5:
                break
        for line in lines:
            m = _CHAPTER_HEADING_RE.match(line)
            if m:
                candidates.append((int(m.group(1)), page_idx + 1, line))
                break

    # Deduplicate by chapter number, keep earliest page hit
    by_num: dict[int, tuple[int, str]] = {}
    for chnum, page, line in candidates:
        if chnum not in by_num:
            by_num[chnum] = (page, line)

    sorted_nums = sorted(by_num)
    chapters: list[Chapter] = []
    for i, chnum in enumerate(sorted_nums):
        page_start, line = by_num[chnum]
        page_end = by_num[sorted_nums[i + 1]][0] - 1 if i + 1 < len(sorted_nums) else doc.page_count
        chapters.append(
            Chapter(
                index=chnum,
                title=line,
                page_start=page_start,
                page_end=page_end,
                section_anchors=[],
            )
        )
    return chapters


def _pdf_chapter_markdown(doc, page_indices: list[int]) -> str:
    """Render a PDF page range as markdown via ``pymupdf4llm`` (preserves
    tables; ADR-011 §3.4.2 / A-9). Falls back to raw ``page.get_text()`` if
    pymupdf4llm raises on an exotic layout."""
    if not page_indices:
        return ""
    try:
        import pymupdf4llm  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover — declared dep, but stay defensive
        return _pdf_chapter_plain_text(doc, page_indices)

    try:
        return pymupdf4llm.to_markdown(
            doc,
            pages=page_indices,
            write_images=False,
            show_progress=False,
        ).strip()
    except TypeError:
        # Older pymupdf4llm signatures (no write_images / show_progress kwargs)
        try:
            return pymupdf4llm.to_markdown(doc, pages=page_indices).strip()
        except Exception:
            return _pdf_chapter_plain_text(doc, page_indices)
    except Exception:
        return _pdf_chapter_plain_text(doc, page_indices)


def _pdf_chapter_plain_text(doc, page_indices: list[int]) -> str:
    """Fallback PDF text extraction — used only when pymupdf4llm is unavailable
    or fails (table information is not preserved here)."""
    page_texts: list[str] = []
    for page_idx in page_indices:
        if 0 <= page_idx < doc.page_count:
            page_texts.append(doc.load_page(page_idx).get_text("text") or "")
    return "\n\n".join(page_texts).strip()


def _pdf_chapter_figures(doc, chapter_index: int, page_indices: list[int]) -> list[ChapterFigure]:
    """Extract images embedded in the given PDF page range as ``ChapterFigure``s.

    PDF lacks a reliable association between an image's position in markdown
    and its position in the page layout, so callers should append placeholders
    at chapter end (``## Figures (extracted, awaiting Vision describe)``)
    rather than try to inline-splice. Each xref is exported once even when it
    appears on multiple pages.
    """
    figures: list[ChapterFigure] = []
    seen_xrefs: set[int] = set()
    for page_idx in page_indices:
        if not (0 <= page_idx < doc.page_count):
            continue
        page = doc.load_page(page_idx)
        try:
            images = page.get_images(full=True)
        except Exception:
            continue
        for img_info in images:
            xref = img_info[0] if img_info else 0
            if not xref or xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            try:
                extracted = doc.extract_image(xref)
            except Exception:
                continue
            if not extracted:
                continue
            img_bytes = extracted.get("image")
            ext_name = extracted.get("ext") or "png"
            if not img_bytes:
                continue
            ref = f"fig-{chapter_index}-{len(figures) + 1}"
            figures.append(
                ChapterFigure(
                    ref=ref,
                    binary=img_bytes,
                    extension=f".{ext_name.lstrip('.')}",
                    alt="",
                    caption="",
                    tied_to_section="",  # PDF: no easy section attribution
                    placeholder=_FIG_PLACEHOLDER.format(ref=ref),
                )
            )
    return figures


def _export_chapter_texts(
    doc,
    chapters: list[Chapter],
    out_dir: Path,
    *,
    attachments_base_dir: Path | None = None,
) -> None:
    """Write each chapter as ``ch{n}.md`` under ``out_dir`` and (optionally)
    figures under ``{attachments_base_dir}/ch{n}/``.

    PDF chapter markdown is rendered via :func:`_pdf_chapter_markdown` so
    tables survive (ADR-011 P3); raw images get appended as placeholder lines
    for the downstream Vision describe pass to splice descriptions back in.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for ch in chapters:
        page_indices = list(range(ch.page_start - 1, ch.page_end))
        body = _pdf_chapter_markdown(doc, page_indices)
        figures = _pdf_chapter_figures(doc, ch.index, page_indices)
        ch.figures = figures  # mutate so caller sees the same artefacts

        if figures:
            body += "\n\n## Figures (extracted, awaiting Vision describe)\n\n"
            body += "\n\n".join(f.placeholder for f in figures)

        target = out_dir / f"ch{ch.index}.md"
        target.write_text(
            f"# Chapter {ch.index} — {ch.title}\n\n"
            f"<!-- page_range: {ch.page_start}-{ch.page_end} -->\n\n"
            f"{body}\n",
            encoding="utf-8",
        )

        if attachments_base_dir is not None:
            _export_chapter_attachments(ch, attachments_base_dir / f"ch{ch.index}")


# ----------------------------------------------------------------------
# EPUB path (primary) — uses ebooklib + BeautifulSoup
# ----------------------------------------------------------------------


_EPUB_WORDS_PER_PAGE = 250  # synthetic page estimate (EPUBs are reflowable)


def _epub_metadata(book) -> BookMetadata:
    """Pull metadata from EPUB OPF Dublin Core fields."""

    def _first(field: str) -> str:
        items = book.get_metadata("DC", field)
        if not items:
            return ""
        # ebooklib returns list of (value, attrs); value is the str
        return (items[0][0] or "").strip()

    def _all(field: str) -> list[str]:
        items = book.get_metadata("DC", field)
        return [(v or "").strip() for v, _attrs in items if (v or "").strip()]

    title = _first("title")
    authors = _all("creator")
    language = _first("language") or "en"
    publisher = _first("publisher") or None
    date_str = _first("date")
    pub_year = None
    year_match = re.search(r"(\d{4})", date_str)
    if year_match:
        pub_year = int(year_match.group(1))

    # Page count: estimate from total spine word count
    page_count = 0  # finalized after spine walk in _epub_chapters

    return BookMetadata(
        title=title,
        authors=authors,
        pub_year=pub_year,
        publisher=publisher,
        language=language,
        page_count=page_count,
    )


_FIG_PLACEHOLDER = "<<FIG:{ref}>>"
_TAB_PLACEHOLDER = "<<TAB:{ref}>>"

# Used to derive an extension when resolved via EPUB media-type
_IMAGE_EXT_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


def _clean_cell_text(cell_tag) -> str:
    """Render a ``<th>`` / ``<td>`` cell as a single-line markdown-safe string."""
    text = cell_tag.get_text(strip=True, separator=" ")
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", "").strip()


def _row_cells_with_spans(tr) -> list[tuple[str, int, int]]:
    """Return ``[(text, rowspan, colspan), ...]`` for direct ``<th>``/``<td>`` of *tr*."""
    out: list[tuple[str, int, int]] = []
    for c in tr.find_all(["th", "td"]):
        try:
            rs = int(c.get("rowspan", 1) or 1)
        except (TypeError, ValueError):
            rs = 1
        try:
            cs = int(c.get("colspan", 1) or 1)
        except (TypeError, ValueError):
            cs = 1
        out.append((_clean_cell_text(c), max(1, rs), max(1, cs)))
    return out


def _expand_rows_to_grid(row_tags) -> list[list[str]]:
    """Expand a list of ``<tr>`` tags into a 2D grid honoring rowspan/colspan.

    Cells with ``rowspan>1`` are replicated downward into subsequent rows;
    cells with ``colspan>1`` are replicated rightward. Empty rows (no cells
    after expansion) are dropped.
    """
    grid: list[list[str]] = []
    pending: dict[int, tuple[str, int]] = {}
    for tr in row_tags:
        cells = _row_cells_with_spans(tr)
        if not cells and not pending:
            continue
        row: list[str] = []
        col = 0
        cell_idx = 0
        while cell_idx < len(cells) or col in pending:
            if col in pending:
                text, remaining = pending[col]
                row.append(text)
                if remaining > 1:
                    pending[col] = (text, remaining - 1)
                else:
                    del pending[col]
                col += 1
                continue
            text, rs, cs = cells[cell_idx]
            cell_idx += 1
            for k in range(cs):
                row.append(text)
                if rs > 1:
                    pending[col + k] = (text, rs - 1)
            col += cs
        # Drain any remaining pending cells past the explicit ones
        while col in pending:
            text, remaining = pending[col]
            row.append(text)
            if remaining > 1:
                pending[col] = (text, remaining - 1)
            else:
                del pending[col]
            col += 1
        if row:
            grid.append(row)
    return grid


def _html_table_to_markdown(table_tag) -> tuple[str, str]:
    """Convert a BS4 ``<table>`` to GFM markdown; return ``(markdown, caption)``.

    Header detection: explicit ``<thead>`` rows take precedence; otherwise the
    first ``<tr>`` containing any ``<th>`` cell is treated as the header. If
    no header can be detected, an empty header row is synthesised so source
    data rows are preserved (GFM tables require a header separator line).

    Handles ``rowspan`` / ``colspan`` by replicating cells across the implied
    grid positions. Filters ``<tr>`` whose nearest ``<table>`` ancestor is not
    *table_tag* so a nested table's rows are not absorbed into the outer
    table.

    Empty tables yield ``("", caption)`` so the caller can emit a placeholder.
    """
    cap_tag = table_tag.find("caption")
    caption_text = cap_tag.get_text(strip=True) if cap_tag is not None else ""

    # All <tr> whose nearest <table> ancestor is *this* table — drops nested
    # tables' rows (find_all is recursive by default).
    own_trs = [tr for tr in table_tag.find_all("tr") if tr.find_parent("table") is table_tag]

    # <thead> rows whose nearest table ancestor is *this* table.
    thead_trs: list = []
    for thead in table_tag.find_all("thead"):
        if thead.find_parent("table") is not table_tag:
            continue
        for tr in thead.find_all("tr"):
            if tr.find_parent("table") is table_tag:
                thead_trs.append(tr)

    if thead_trs:
        header_rows = thead_trs
        body_rows = [tr for tr in own_trs if tr not in thead_trs]
    elif own_trs and own_trs[0].find("th") is not None:
        header_rows = [own_trs[0]]
        body_rows = own_trs[1:]
    else:
        header_rows = []
        body_rows = own_trs

    header_grid = _expand_rows_to_grid(header_rows) if header_rows else []
    body_grid = _expand_rows_to_grid(body_rows)

    if not header_grid and not body_grid:
        return "", caption_text

    n_cols = max((len(r) for r in header_grid + body_grid), default=0)
    if n_cols == 0:
        return "", caption_text

    def _pad(r: list[str]) -> list[str]:
        return (r + [""] * (n_cols - len(r)))[:n_cols]

    md_lines: list[str] = []
    # Multi-row thead: emit all but the last as preceding header rows; the
    # last is the canonical header line that pairs with the separator.
    if header_grid:
        for extra in header_grid[:-1]:
            md_lines.append("| " + " | ".join(_pad(extra)) + " |")
        md_lines.append("| " + " | ".join(_pad(header_grid[-1])) + " |")
    else:
        md_lines.append("| " + " | ".join([""] * n_cols) + " |")
    md_lines.append("| " + " | ".join(["---"] * n_cols) + " |")
    for row in body_grid:
        md_lines.append("| " + " | ".join(_pad(row)) + " |")

    return "\n".join(md_lines), caption_text


def _walk_mathml(node) -> str:
    """Convert common MathML elements to LaTeX, recursing into children.

    Handles the elements that account for the bulk of textbook math:
    ``mfrac`` → ``\\frac{n}{d}``; ``msup`` → ``base^{exp}``; ``msub`` →
    ``base_{sub}``; ``msubsup`` → ``base_{sub}^{sup}``; ``msqrt`` →
    ``\\sqrt{...}``; ``mroot`` → ``\\sqrt[n]{...}``. Leaf elements
    (``mn``/``mi``/``mo``/``mtext``) emit their text content. Containers
    (``mrow``/``math``/unknown) walk their children. Strings pass through.
    """
    from bs4 import NavigableString, Tag

    if isinstance(node, NavigableString):
        return str(node)
    if not isinstance(node, Tag):
        return ""

    name = (node.name or "").lower()
    children = [c for c in node.children if isinstance(c, Tag)]

    if name == "mfrac" and len(children) >= 2:
        num = _walk_mathml(children[0]).strip()
        den = _walk_mathml(children[1]).strip()
        return f"\\frac{{{num}}}{{{den}}}"
    if name == "msup" and len(children) >= 2:
        base = _walk_mathml(children[0]).strip()
        sup = _walk_mathml(children[1]).strip()
        return f"{base}^{{{sup}}}"
    if name == "msub" and len(children) >= 2:
        base = _walk_mathml(children[0]).strip()
        sub = _walk_mathml(children[1]).strip()
        return f"{base}_{{{sub}}}"
    if name == "msubsup" and len(children) >= 3:
        base = _walk_mathml(children[0]).strip()
        sub = _walk_mathml(children[1]).strip()
        sup = _walk_mathml(children[2]).strip()
        return f"{base}_{{{sub}}}^{{{sup}}}"
    if name == "msqrt":
        inner = "".join(_walk_mathml(c) for c in children).strip()
        return f"\\sqrt{{{inner}}}"
    if name == "mroot" and len(children) >= 2:
        radicand = _walk_mathml(children[0]).strip()
        index = _walk_mathml(children[1]).strip()
        return f"\\sqrt[{index}]{{{radicand}}}"

    if name in ("mn", "mi", "mo", "mtext"):
        return node.get_text(strip=False)

    # Containers (math/mrow/mstyle/unknown): concatenate children's output
    return "".join(_walk_mathml(c) for c in node.children)


def _html_math_to_latex(math_tag) -> str:
    """Convert a MathML ``<math>`` tag to inline LaTeX ``$$...$$``.

    ADR-011 §3.4.1 originally proposed wrapping the ``mathml2latex`` PyPI
    package; that package is effectively abandoned (v0.1.0 ships an empty
    public ``__init__`` and a brittle internal API). Per the deviation
    feedback principle, we ship the lighter alttext-first path instead:

    1. ``<math alttext="\\frac{1}{2}">`` — most modern textbook EPUBs include
       the official accessibility ``alttext`` attribute carrying LaTeX or
       readable text. Use it verbatim.
    2. Walk the common MathML subset (``mfrac``/``msup``/``msub``/``msqrt``
       etc.) so structure survives even when *alttext* is missing — see
       :func:`_walk_mathml`.
    3. Empty or whitespace-only output → empty string (caller decides what
       to do with the now-empty placeholder).

    Future: a richer MathML→LaTeX converter (e.g. a maintained dep) can
    replace the walker; the function signature stays the same.
    """
    alt = (math_tag.get("alttext") or "").strip()
    if alt:
        return f"$${alt}$$"
    latex = _walk_mathml(math_tag).strip()
    return f"$${latex}$$" if latex else ""


def _extract_figure(
    tag,
    chapter_index: int,
    fig_index: int,
    tied_to_section: str,
    image_resolver,
) -> ChapterFigure | None:
    """Build a ``ChapterFigure`` from an ``<img>``, ``<svg>``, or ``<figure>`` tag.

    Returns ``None`` if no usable image can be resolved (no ``src``, resolver
    declined, or unsupported nesting). Caller should drop the originating tag
    when ``None`` is returned.

    SVG nodes are serialised inline as bytes (vector preserved, downstream
    Vision pass can still describe them).
    """
    name = (tag.name or "").lower()

    img_tag = None
    figcaption_text = ""

    if name == "figure":
        img_tag = tag.find(["img", "svg"])
        cap = tag.find("figcaption")
        if cap is not None:
            figcaption_text = cap.get_text(strip=True)
    elif name in ("img", "svg"):
        img_tag = tag

    if img_tag is None:
        return None

    if (img_tag.name or "").lower() == "svg":
        binary = str(img_tag).encode("utf-8")
        extension = ".svg"
        alt = img_tag.get("aria-label") or img_tag.get("title") or ""
    else:
        src = img_tag.get("src", "")
        alt = img_tag.get("alt", "")
        if not src or image_resolver is None:
            return None
        resolved = image_resolver(src)
        if resolved is None:
            return None
        binary, extension = resolved

    ref = f"fig-{chapter_index}-{fig_index}"
    caption = figcaption_text or alt
    return ChapterFigure(
        ref=ref,
        binary=binary,
        extension=extension,
        alt=alt,
        caption=caption,
        tied_to_section=tied_to_section,
        placeholder=_FIG_PLACEHOLDER.format(ref=ref),
    )


def _walk_epub_html(
    html_bytes: bytes,
    *,
    chapter_index: int = 0,
    image_resolver=None,
) -> tuple[str, list[str], list[ChapterFigure], list[ChapterTable]]:
    """Walk EPUB chapter HTML extracting text + headings + figures + tables.

    ADR-011 §3.4.1 — replaces the legacy ``BeautifulSoup.get_text()`` flatten
    that dropped ``<img>`` / ``<table>`` / ``<math>``. Special elements are
    replaced with placeholders so the chapter source page can splice in
    Vision-generated descriptions or markdown table files post-ingest.

    ``image_resolver(src) -> (binary, extension) | None`` is invoked for each
    ``<img src>`` to pull binary image data out of the EPUB zip. When ``None``,
    figures are skipped — useful for text-only smoke tests where image
    extraction is irrelevant.

    Note: ``<svg>`` elements are serialised inline as bytes (no resolver
    needed) so vector graphics survive intact.
    """
    from bs4 import BeautifulSoup, Tag

    soup = BeautifulSoup(html_bytes, "html.parser")
    for tag in soup(["script", "style", "nav"]):
        tag.decompose()

    section_anchors: list[str] = []
    figures: list[ChapterFigure] = []
    tables: list[ChapterTable] = []

    state = {"section": "", "fig_n": 0, "tab_n": 0}

    def _walk(parent: Tag) -> None:
        for child in list(parent.children):
            if not isinstance(child, Tag):
                continue
            name = (child.name or "").lower()

            if name in ("h2", "h3"):
                anchor = child.get_text(strip=True)
                if anchor:
                    section_anchors.append(anchor)
                    state["section"] = anchor
                    # Inject markdown heading marker so chapter body
                    # retains heading structure after soup.get_text
                    # flattening (chapter-summary prompt locates per-
                    # section verbatim quotes by `## `/`### ` markers).
                    marker = "## " if name == "h2" else "### "
                    child.replace_with(soup.new_string(f"\n\n{marker}{anchor}\n\n"))
                else:
                    child.decompose()
                continue

            if name in ("img", "svg", "figure"):
                state["fig_n"] += 1
                fig = _extract_figure(
                    child,
                    chapter_index,
                    state["fig_n"],
                    state["section"],
                    image_resolver,
                )
                if fig is not None:
                    figures.append(fig)
                    placeholder = soup.new_string(f"\n{fig.placeholder}\n")
                    child.replace_with(placeholder)
                else:
                    state["fig_n"] -= 1  # roll back: nothing exported
                    if name == "figure":
                        # <figure> with no extractable image — eg. some
                        # publishers wrap a <table> (or <math>, plain
                        # text) in <figure> for layout. Recurse so the
                        # nested first-class elements are processed by
                        # their own branches, then unwrap the <figure>
                        # shell so any remaining inline children
                        # (figcaption text, etc.) survive in body text.
                        _walk(child)
                        child.unwrap()
                    else:
                        child.decompose()
                continue

            if name == "table":
                state["tab_n"] += 1
                ref = f"tab-{chapter_index}-{state['tab_n']}"
                md, caption = _html_table_to_markdown(child)
                placeholder_str = _TAB_PLACEHOLDER.format(ref=ref)
                tables.append(
                    ChapterTable(
                        ref=ref,
                        markdown=md,
                        caption=caption,
                        tied_to_section=state["section"],
                        placeholder=placeholder_str,
                    )
                )
                placeholder = soup.new_string(f"\n{placeholder_str}\n")
                child.replace_with(placeholder)
                continue

            if name == "math":
                latex = _html_math_to_latex(child)
                # Replace MathML node with inline LaTeX text (or empty if no
                # alt/textContent could be extracted)
                child.replace_with(soup.new_string(latex))
                continue

            _walk(child)

    _walk(soup)

    body_text = soup.get_text(separator="\n", strip=True)
    return body_text, section_anchors, figures, tables


def _epub_html_to_text(html_bytes: bytes) -> tuple[str, list[str]]:
    """Backwards-compatible wrapper: return ``(text, section_anchors)`` only.

    New callers should use :func:`_walk_epub_html` directly so figures and
    tables are preserved as first-class artefacts.
    """
    body, anchors, _figs, _tabs = _walk_epub_html(html_bytes)
    return body, anchors


def _build_epub_image_resolver(book, chapter_href: str):
    """Return a callable resolving ``<img src>`` (relative to ``chapter_href``)
    to ``(binary, extension)`` by looking up matching ``ITEM_IMAGE`` items.

    EPUB src attributes are typically relative (``../Images/fig.png``); we try
    both the literal src and a normalised path against the chapter directory
    to handle the common conventions.
    """
    from posixpath import join, normpath

    from ebooklib import ITEM_IMAGE

    items_by_name = {item.get_name(): item for item in book.get_items_of_type(ITEM_IMAGE)}
    chapter_dir = "/".join(chapter_href.split("/")[:-1])

    def _resolve(src: str):
        cleaned = (src or "").split("#")[0].split("?")[0]
        if not cleaned:
            return None

        candidates: list[str] = []
        candidates.append(cleaned.lstrip("/"))
        if chapter_dir:
            candidates.append(normpath(join(chapter_dir, cleaned)).lstrip("/"))

        seen: set[str] = set()
        for cand in candidates:
            if cand in seen:
                continue
            seen.add(cand)
            item = items_by_name.get(cand)
            if item is None:
                # Try matching by suffix (some EPUBs flatten all images to root)
                matches = [v for k, v in items_by_name.items() if k.endswith(cand)]
                item = matches[0] if matches else None
            if item is None:
                continue
            content = item.get_content()
            media_type = (getattr(item, "media_type", "") or "").lower()
            extension = _IMAGE_EXT_BY_MIME.get(media_type)
            if not extension:
                suffix = Path(cand).suffix.lower()
                extension = suffix if suffix else ".bin"
            return content, extension
        return None

    return _resolve


def _epub_chapters(
    book, *, max_chapters: int = 200
) -> tuple[list[Chapter], list[tuple[Chapter, str]], int]:
    """Walk EPUB TOC + spine; return chapters + per-chapter text + total pages.

    Each ``Chapter`` carries figures/tables extracted by :func:`_walk_epub_html`
    so callers can export attachments after the spine walk completes.
    """
    from ebooklib import ITEM_DOCUMENT

    # Build href → spine_index map (reading order)
    spine_hrefs: dict[str, int] = {}
    for spine_idx, (idref, _linear) in enumerate(book.spine):
        item = book.get_item_with_id(idref)
        if item is not None:
            href = item.get_name()  # e.g. "Text/chapter01.xhtml"
            spine_hrefs[href] = spine_idx

    # Flatten top-level TOC entries (skip nested sub-sections — those become
    # section_anchors via h2/h3 within the chapter HTML)
    from ebooklib.epub import Link, Section

    top_level: list[tuple[str, str]] = []  # (title, href without fragment)
    for entry in book.toc or []:
        if isinstance(entry, Link):
            href = entry.href.split("#")[0]
            top_level.append((entry.title or href, href))
        elif isinstance(entry, tuple) and len(entry) == 2:
            section, children = entry
            # Use the Section's first child Link's href as the chapter anchor;
            # the Section's title is the chapter title
            section_title = section.title if isinstance(section, Section) else str(section)
            first_link_href = ""
            for child in children:
                if isinstance(child, Link):
                    first_link_href = child.href.split("#")[0]
                    break
            if first_link_href:
                top_level.append((section_title, first_link_href))

    if not top_level:
        # Fallback: every spine document is a chapter
        for spine_idx, (idref, _linear) in enumerate(book.spine):
            item = book.get_item_with_id(idref)
            if item is None:
                continue
            top_level.append((f"Chapter {spine_idx + 1}", item.get_name()))

    # Build chapters in spine order
    chapters: list[Chapter] = []
    chapter_texts: list[tuple[Chapter, str]] = []
    cumulative_words = 0

    items_by_href = {item.get_name(): item for item in book.get_items_of_type(ITEM_DOCUMENT)}

    # Sort top-level entries by spine position so chapters reflect reading order
    sortable = []
    for title, href in top_level:
        spine_idx = spine_hrefs.get(href, 10**6)  # missing → push to end
        sortable.append((spine_idx, title, href))
    sortable.sort()

    for index, (_spine_idx, title, href) in enumerate(sortable, start=1):
        if index > max_chapters:
            break
        item = items_by_href.get(href)
        if item is None:
            # Some EPUBs reference TOC entries to mid-chapter fragments only;
            # try matching by stem
            matches = [v for k, v in items_by_href.items() if k.endswith(href)]
            item = matches[0] if matches else None
        if item is None:
            continue
        chapter_href = item.get_name()
        image_resolver = _build_epub_image_resolver(book, chapter_href)
        body_text, section_anchors, figures, tables = _walk_epub_html(
            item.get_content(),
            chapter_index=index,
            image_resolver=image_resolver,
        )
        word_count = len(body_text.split())
        page_start = cumulative_words // _EPUB_WORDS_PER_PAGE + 1
        cumulative_words += word_count
        page_end = max(page_start, cumulative_words // _EPUB_WORDS_PER_PAGE)
        chapter = Chapter(
            index=index,
            title=(title or f"Chapter {index}").strip(),
            page_start=page_start,
            page_end=page_end,
            section_anchors=section_anchors,
            figures=figures,
            tables=tables,
        )
        chapters.append(chapter)
        chapter_texts.append((chapter, body_text))

    total_pages = max(1, cumulative_words // _EPUB_WORDS_PER_PAGE)
    return chapters, chapter_texts, total_pages


_SAFE_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*$")
_SAFE_EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,8}$")


def _validate_attachment_ref(ref: str, *, kind: str) -> None:
    if not _SAFE_REF_RE.match(ref or ""):
        raise ValueError(f"unsafe {kind} ref {ref!r}: must match {_SAFE_REF_RE.pattern}")


def _validate_attachment_extension(extension: str) -> None:
    if not _SAFE_EXT_RE.match(extension or ""):
        raise ValueError(f"unsafe attachment extension {extension!r}")


def _export_chapter_attachments(chapter: Chapter, attachments_dir: Path) -> None:
    """Write figures + tables for a single chapter under ``attachments_dir``.

    Layout follows ADR-011 §3.4.1:

    - Figures → ``{ref}{extension}`` (e.g. ``fig-1-3.png`` / ``fig-1-3.svg``)
    - Tables → ``{ref}.md`` (e.g. ``tab-1-2.md``) — caption-prefixed markdown

    Each ``ref`` and ``extension`` is regex-validated before joining with
    *attachments_dir* so a hostile or buggy upstream cannot cause a path
    traversal write outside *attachments_dir*.
    """
    if not chapter.figures and not chapter.tables:
        return
    attachments_dir.mkdir(parents=True, exist_ok=True)
    for fig in chapter.figures:
        _validate_attachment_ref(fig.ref, kind="figure")
        _validate_attachment_extension(fig.extension)
        target = attachments_dir / f"{fig.ref}{fig.extension}"
        target.write_bytes(fig.binary)
    for tab in chapter.tables:
        _validate_attachment_ref(tab.ref, kind="table")
        target = attachments_dir / f"{tab.ref}.md"
        body = tab.markdown.strip() if tab.markdown else ""
        if tab.caption and body:
            body = f"_{tab.caption}_\n\n{body}"
        elif tab.caption:
            body = f"_{tab.caption}_\n\n_(空表格)_"
        elif not body:
            body = "_(空表格)_"
        target.write_text(body + "\n", encoding="utf-8")


def _export_epub_chapter_texts(
    chapter_texts: list[tuple[Chapter, str]],
    out_dir: Path,
    *,
    attachments_base_dir: Path | None = None,
) -> None:
    """Write each EPUB chapter as ``ch{n}.md`` under ``out_dir``.

    When ``attachments_base_dir`` is supplied, also writes per-chapter
    figures / tables to ``{attachments_base_dir}/ch{n}/``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for ch, body in chapter_texts:
        target = out_dir / f"ch{ch.index}.md"
        target.write_text(
            f"# Chapter {ch.index} — {ch.title}\n\n"
            f"<!-- estimated_page_range: {ch.page_start}-{ch.page_end} "
            f"(EPUB reflowable, {_EPUB_WORDS_PER_PAGE} words/page) -->\n\n"
            f"{body}\n",
            encoding="utf-8",
        )
        if attachments_base_dir is not None:
            _export_chapter_attachments(ch, attachments_base_dir / f"ch{ch.index}")


def _parse_epub(
    epub_path: Path,
    *,
    export_chapters_dir: Path | None = None,
    attachments_base_dir: Path | None = None,
    max_chapters: int = 200,
) -> Outline:
    try:
        from ebooklib import epub
    except ImportError as e:
        raise RuntimeError("ebooklib 未安裝。請執行：pip install ebooklib beautifulsoup4") from e

    if not epub_path.exists():
        raise FileNotFoundError(f"EPUB 檔案不存在：{epub_path}")

    book = epub.read_epub(str(epub_path))
    warnings: list[str] = []

    book_metadata = _epub_metadata(book)
    chapters, chapter_texts, total_pages = _epub_chapters(book, max_chapters=max_chapters)
    book_metadata.page_count = total_pages

    if not chapters:
        return Outline(
            status="needs_manual",
            strategy="none",
            book_metadata=book_metadata,
            chapters=[],
            warnings=warnings + ["EPUB 未發現可解析的章節（spine + TOC 都空）"],
        )

    # Warn on oversize chapters by word count (> 50_000 words ≈ 200 pages
    # ≈ likely too big for one Opus turn)
    for ch, text in chapter_texts:
        word_count = len(text.split())
        if word_count > 50_000:
            est_pages = word_count // _EPUB_WORDS_PER_PAGE
            warnings.append(
                f"ch{ch.index} ({ch.title!r}) 約 {word_count:,} 字（~{est_pages} 頁），建議手動切細"
            )

    if export_chapters_dir is not None:
        _export_epub_chapter_texts(
            chapter_texts,
            export_chapters_dir,
            attachments_base_dir=attachments_base_dir,
        )
    elif attachments_base_dir is not None:
        # Caller wants attachments but not chapter md → still export
        for ch in chapters:
            _export_chapter_attachments(ch, attachments_base_dir / f"ch{ch.index}")

    return Outline(
        status="ok",
        strategy="epub_nav",
        book_metadata=book_metadata,
        chapters=chapters,
        warnings=warnings,
    )


# ----------------------------------------------------------------------
# PDF path (fallback) + dispatcher
# ----------------------------------------------------------------------


def _parse_pdf(
    pdf_path: Path,
    *,
    toc_yaml: Path | None = None,
    export_chapters_dir: Path | None = None,
    attachments_base_dir: Path | None = None,
    max_chapters: int = 200,
) -> Outline:
    """Build an outline for the given PDF, exporting per-chapter texts if asked."""
    try:
        import pymupdf  # noqa: F401  # provides fitz alias historically
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz  # legacy entry point
        except ImportError as e:
            raise RuntimeError("pymupdf 未安裝。請執行：pip install pymupdf") from e

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 檔案不存在：{pdf_path}")

    doc = fitz.open(str(pdf_path))
    warnings: list[str] = []
    book_metadata = _extract_book_metadata(doc)

    chapters: list[Chapter] = []
    strategy = ""

    if toc_yaml is not None:
        chapters = _chapters_from_yaml(toc_yaml)
        strategy = "manual_toc"
    else:
        chapters = _chapters_from_toc(doc, max_chapters=max_chapters)
        if chapters:
            strategy = "pdf_outline"
        else:
            chapters = _chapters_from_regex(doc)
            if chapters:
                strategy = "regex_fallback"
                warnings.append("PDF outline 不存在，退回 regex 偵測；建議用 --toc-yaml 手動覆寫")

    if not chapters:
        return Outline(
            status="needs_manual",
            strategy="none",
            book_metadata=book_metadata,
            chapters=[],
            warnings=warnings
            + [
                "無法偵測章節邊界。請建立 manual TOC YAML：",
                "chapters:",
                "  - index: 1",
                '    title: "Introduction"',
                "    page_start: 1",
                "    page_end: 25",
                "    section_anchors: []",
            ],
        )

    # Warn on oversize chapters (> 200 pages → likely too big for one Opus turn)
    for ch in chapters:
        size = ch.page_end - ch.page_start + 1
        if size > 200:
            warnings.append(f"ch{ch.index} ({ch.title!r}) 共 {size} 頁，建議手動切細")

    if export_chapters_dir is not None:
        _export_chapter_texts(
            doc,
            chapters,
            export_chapters_dir,
            attachments_base_dir=attachments_base_dir,
        )
    elif attachments_base_dir is not None:
        # Attachments-only path: extract figures without writing chapter md
        for ch in chapters:
            page_indices = list(range(ch.page_start - 1, ch.page_end))
            ch.figures = _pdf_chapter_figures(doc, ch.index, page_indices)
            _export_chapter_attachments(ch, attachments_base_dir / f"ch{ch.index}")

    doc.close()

    return Outline(
        status="ok",
        strategy=strategy,
        book_metadata=book_metadata,
        chapters=chapters,
        warnings=warnings,
    )


def parse_book(
    book_path: Path,
    *,
    toc_yaml: Path | None = None,
    export_chapters_dir: Path | None = None,
    attachments_base_dir: Path | None = None,
    max_chapters: int = 200,
) -> Outline:
    """Dispatch on file extension; EPUB primary, PDF fallback."""
    suffix = book_path.suffix.lower()
    if suffix == ".epub":
        if toc_yaml is not None:
            raise ValueError("--toc-yaml only supported for PDF; EPUB has authoritative nav")
        return _parse_epub(
            book_path,
            export_chapters_dir=export_chapters_dir,
            attachments_base_dir=attachments_base_dir,
            max_chapters=max_chapters,
        )
    if suffix == ".pdf":
        return _parse_pdf(
            book_path,
            toc_yaml=toc_yaml,
            export_chapters_dir=export_chapters_dir,
            attachments_base_dir=attachments_base_dir,
            max_chapters=max_chapters,
        )
    raise ValueError(f"unsupported file extension: {suffix} (expected .epub or .pdf)")


def _figure_to_dict(fig: ChapterFigure) -> dict:
    """Serialise a ``ChapterFigure`` to JSON (without binary)."""
    return {
        "ref": fig.ref,
        "extension": fig.extension,
        "alt": fig.alt,
        "caption": fig.caption,
        "tied_to_section": fig.tied_to_section,
        "placeholder": fig.placeholder,
    }


def _table_to_dict(tab: ChapterTable) -> dict:
    """Serialise a ``ChapterTable`` to JSON (without markdown body — that lives
    on disk under ``Attachments/.../{ref}.md``)."""
    return {
        "ref": tab.ref,
        "caption": tab.caption,
        "tied_to_section": tab.tied_to_section,
        "placeholder": tab.placeholder,
    }


def _chapter_to_dict(ch: Chapter) -> dict:
    """Serialise a ``Chapter`` to JSON, including figures/tables metadata."""
    return {
        "index": ch.index,
        "title": ch.title,
        "page_start": ch.page_start,
        "page_end": ch.page_end,
        "section_anchors": ch.section_anchors,
        "figures": [_figure_to_dict(f) for f in ch.figures],
        "tables": [_table_to_dict(t) for t in ch.tables],
    }


def _outline_to_dict(outline: Outline) -> dict:
    return {
        "status": outline.status,
        "strategy": outline.strategy,
        "book_metadata": asdict(outline.book_metadata),
        "chapters": [_chapter_to_dict(ch) for ch in outline.chapters],
        "warnings": outline.warnings,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Extract textbook chapter outline from EPUB (preferred) or PDF"
    )
    p.add_argument("--path", required=True, help="path to .epub (preferred) or .pdf file")
    p.add_argument("--out", required=True, help="output JSON path")
    p.add_argument(
        "--toc-yaml",
        default=None,
        help="manual chapter boundaries (bypasses detection)",
    )
    p.add_argument(
        "--export-chapters-dir",
        default=None,
        help="if set, write per-chapter .md files here for downstream Read tool use",
    )
    p.add_argument(
        "--attachments-base-dir",
        default=None,
        help=(
            "if set, write per-chapter figures/tables under "
            "{attachments-base-dir}/ch{n}/ (target = vault Attachments/Books/{book_id}/)"
        ),
    )
    p.add_argument(
        "--max-chapters",
        type=int,
        default=200,
        help="cap on chapter count (safety)",
    )
    args = p.parse_args()

    outline = parse_book(
        Path(args.path),
        toc_yaml=Path(args.toc_yaml) if args.toc_yaml else None,
        export_chapters_dir=(Path(args.export_chapters_dir) if args.export_chapters_dir else None),
        attachments_base_dir=(
            Path(args.attachments_base_dir) if args.attachments_base_dir else None
        ),
        max_chapters=args.max_chapters,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(_outline_to_dict(outline), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"status={outline.status} strategy={outline.strategy}")
    print(f"chapters={len(outline.chapters)} warnings={len(outline.warnings)}")
    fig_total = sum(len(ch.figures) for ch in outline.chapters)
    tab_total = sum(len(ch.tables) for ch in outline.chapters)
    print(f"figures={fig_total} tables={tab_total}")
    if outline.warnings:
        for w in outline.warnings:
            print(f"  [warn] {w}")
    print(f"wrote: {out_path}")

    return 0 if outline.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
