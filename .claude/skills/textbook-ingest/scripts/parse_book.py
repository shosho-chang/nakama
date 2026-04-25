#!/usr/bin/env python3
"""PDF outline extractor for the ``textbook-ingest`` skill.

Extracts chapter boundaries from a PDF and emits a JSON outline that
the LLM-driven skill uses to drive per-chapter ingestion.

Strategies (in order, first one to succeed wins):

1. PDF outline / bookmarks (most textbooks have one) — `doc.get_toc()`
2. Manual override via ``--toc-yaml`` (caller-supplied YAML with chapter
   boundaries; bypasses detection entirely)
3. Heading regex fallback — scan first page of each candidate chapter
   for ``^(Chapter|第)\\s*\\d+`` patterns

If none succeed, the script writes ``status: needs_manual`` and exits
non-zero so the skill can surface the failure to the user.

Usage:

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
from dataclasses import asdict, dataclass
from pathlib import Path

# sys.path shim for ``shared.*`` imports (per
# memory/claude/feedback_skill_scaffolding_pitfalls.md, pitfall #1)
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@dataclass
class Chapter:
    index: int
    title: str
    page_start: int  # 1-based
    page_end: int  # 1-based, inclusive
    section_anchors: list[str]


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


def _export_chapter_texts(doc, chapters: list[Chapter], out_dir: Path) -> None:
    """Write each chapter as ``ch{n}.md`` under ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for ch in chapters:
        page_texts: list[str] = []
        for page_idx in range(ch.page_start - 1, ch.page_end):
            if 0 <= page_idx < doc.page_count:
                page_texts.append(doc.load_page(page_idx).get_text("text") or "")
        body = "\n\n".join(page_texts).strip()
        target = out_dir / f"ch{ch.index}.md"
        target.write_text(
            f"# Chapter {ch.index} — {ch.title}\n\n"
            f"<!-- page_range: {ch.page_start}-{ch.page_end} -->\n\n"
            f"{body}\n",
            encoding="utf-8",
        )


def parse_book(
    pdf_path: Path,
    *,
    toc_yaml: Path | None = None,
    export_chapters_dir: Path | None = None,
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
        _export_chapter_texts(doc, chapters, export_chapters_dir)

    doc.close()

    return Outline(
        status="ok",
        strategy=strategy,
        book_metadata=book_metadata,
        chapters=chapters,
        warnings=warnings,
    )


def _outline_to_dict(outline: Outline) -> dict:
    return {
        "status": outline.status,
        "strategy": outline.strategy,
        "book_metadata": asdict(outline.book_metadata),
        "chapters": [asdict(ch) for ch in outline.chapters],
        "warnings": outline.warnings,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Extract textbook chapter outline from PDF")
    p.add_argument("--path", required=True, help="path to PDF file")
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
    if outline.warnings:
        for w in outline.warnings:
            print(f"  [warn] {w}")
    print(f"wrote: {out_path}")

    return 0 if outline.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
