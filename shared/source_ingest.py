"""Phase 1 walker for ADR-020 textbook ingest v3.

Reads a Raw markdown file produced by Phase 0 (``shared.raw_ingest``) and
returns per-chapter slices with metadata. No LLM calls — purely structural.

The caller passes each :class:`ChapterPayload` to an LLM with the
``chapter-source.md`` prompt to produce the final source page (verbatim body +
structured wrappers). This module guarantees paragraph-level verbatim fidelity;
the invariant is measured by :func:`verbatim_paragraph_match_pct`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from shared.log import get_logger

logger = get_logger("nakama.shared.source_ingest")

_RE_H1 = re.compile(r"^# (.+)$")
_RE_H2 = re.compile(r"^## (.+)$")
# Real chapters in textbooks typically begin "1 Title", "2 Title", "10 Title".
# When ≥2 H1s in the book match this shape, we switch to "prefix mode" and
# treat any H1 without a numeric prefix as front/back matter (Preface, Index,
# Acknowledgments, License) and drop it. Avoids the 2026-05 BSE staging bug
# where Preface + book-title H1 were counted as ordinals 1-2, offsetting every
# real chapter_index by +2.
_RE_CHAPTER_PREFIX = re.compile(r"^(\d+)\s+(.+)$")
_RE_FIGURE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_RE_TABLE_ROW = re.compile(r"^\|")
_RE_BOLD_CAPTION = re.compile(r"^\*\*(.+)\*\*$")

# EPUB internal links:
#   [text](chapter12.xhtml)            — chapter file
#   [text](chapter12.xhtml#sec-1)      — chapter file with anchor
#   [text](#c10-bib-0048)              — in-page anchor (bibliography ref)
# Negative lookbehind on `!` keeps figure syntax `![alt](path)` untouched.
_RE_EPUB_FILE_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\([^)]*\.x?html(?:#[^)]*)?\)")
_RE_EPUB_ANCHOR_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(#[^)]+\)")


class RawMarkdownParseError(ValueError):
    """Raised when the raw markdown file is missing required structure."""


@dataclass
class FigureRef:
    """A figure link found in the raw markdown body."""

    vault_path: str
    """Vault-relative path, e.g. ``"Attachments/Books/bse-2024/fig1-1.png"``."""
    alt_text: str
    """Original alt text from the markdown image syntax."""


@dataclass
class InlineTable:
    """A pipe table found in the raw markdown body."""

    markdown: str
    """Full pipe-table content, rows joined by newlines."""
    caption: str
    """Bold caption line immediately above the table, or empty string."""


@dataclass
class ChapterPayload:
    """Per-chapter slice of a raw markdown book file.

    ``verbatim_body`` is the exact text slice from the raw file, starting at
    the H1 heading and ending just before the next H1 (or EOF). The LLM
    prompt receives this field unchanged and must not paraphrase it.
    """

    book_id: str
    raw_path: str
    chapter_index: int
    chapter_title: str
    verbatim_body: str
    section_anchors: list[str] = field(default_factory=list)
    figures: list[FigureRef] = field(default_factory=list)
    tables: list[InlineTable] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def walk_book_to_chapters(raw_markdown_path: Path | str) -> list[ChapterPayload]:
    """Split a Phase 0 raw markdown file into per-chapter payloads.

    Args:
        raw_markdown_path: Path to a ``.md`` file produced by
            :func:`shared.raw_ingest.epub_to_raw_markdown`.

    Returns:
        One :class:`ChapterPayload` per H1 heading found after the frontmatter.
        Returns an empty list if the file has no H1 headings.

    Raises:
        FileNotFoundError:      If the file does not exist.
        RawMarkdownParseError:  If the file has no frontmatter or is missing
                                the required ``book_id`` field.
    """
    raw_path = Path(raw_markdown_path)
    if not raw_path.exists():
        raise FileNotFoundError(f"Raw markdown not found: {raw_path}")

    content = raw_path.read_text(encoding="utf-8")
    book_id, body = _parse_raw_file(content, raw_path)

    chapter_slices = _split_into_chapters(body)
    if not chapter_slices:
        logger.info("No H1 chapters found in %s", raw_path.name)
        return []

    numbered = [
        (m.group(1), title, body)
        for (title, body) in chapter_slices
        for m in [_RE_CHAPTER_PREFIX.match(title)]
        if m
    ]
    use_prefix_mode = len(numbered) >= 2

    payloads = []
    if use_prefix_mode:
        dropped = len(chapter_slices) - len(numbered)
        if dropped:
            logger.info(
                "Prefix mode: dropping %d non-chapter H1(s) from %s (Preface/Index/etc.)",
                dropped,
                raw_path.name,
            )
        for prefix, title, chapter_body in numbered:
            payload = _build_payload(
                book_id=book_id,
                raw_path=str(raw_path),
                chapter_index=int(prefix),
                chapter_title=title,
                verbatim_body=chapter_body,
            )
            payloads.append(payload)
    else:
        for idx, (title, chapter_body) in enumerate(chapter_slices, start=1):
            payload = _build_payload(
                book_id=book_id,
                raw_path=str(raw_path),
                chapter_index=idx,
                chapter_title=title,
                verbatim_body=chapter_body,
            )
            payloads.append(payload)

    logger.info("Walked %d chapters from %s", len(payloads), raw_path.name)
    return payloads


def verbatim_paragraph_match_pct(source: str, extracted: str) -> float:
    """Return the percentage of paragraphs from *source* present in *extracted*.

    A paragraph is a non-empty block of text separated by blank lines.
    The metric is used to verify the walker's verbatim-body invariant:
    the acceptance threshold is ≥ 99 %.

    Args:
        source:    Reference text (the raw chapter slice or original content).
        extracted: Text to check against (the ``ChapterPayload.verbatim_body``).

    Returns:
        Float in [0.0, 100.0].
    """
    source_paras = [p.strip() for p in re.split(r"\n\n+", source) if p.strip()]
    if not source_paras:
        return 100.0
    found = sum(1 for p in source_paras if p in extracted)
    return found / len(source_paras) * 100.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_raw_file(content: str, raw_path: Path) -> tuple[str, str]:
    """Extract (book_id, body_text) from raw markdown content."""
    if not content.startswith("---\n"):
        raise RawMarkdownParseError(
            f"No YAML frontmatter found in '{raw_path.name}'. Expected file to start with '---\\n'."
        )

    end = content.find("\n---\n", 4)
    if end == -1:
        raise RawMarkdownParseError(f"Unclosed frontmatter block in '{raw_path.name}'.")

    fm = yaml.safe_load(content[4:end])
    book_id = fm.get("book_id") if fm else None
    if not book_id:
        raise RawMarkdownParseError(
            f"'book_id' field missing from frontmatter in '{raw_path.name}'."
        )

    body = content[end + 5 :]  # skip closing "\n---\n"
    body = _strip_epub_internal_links(body)
    return str(book_id), body


def _strip_epub_internal_links(text: str) -> str:
    """Remove EPUB internal markdown links so Obsidian doesn't auto-stub them.

    Replaces ``[text](chapter12.xhtml)`` and ``[text](#bib-0048)`` with the
    bare link text. Image syntax ``![alt](path)`` is preserved (negative
    lookbehind on ``!``).
    """
    text = _RE_EPUB_FILE_LINK.sub(r"\1", text)
    text = _RE_EPUB_ANCHOR_LINK.sub(r"\1", text)
    return text


def _split_into_chapters(body: str) -> list[tuple[str, str]]:
    """Split body text into (title, verbatim_body) pairs at each H1 boundary."""
    lines = body.splitlines(keepends=True)
    chapter_starts: list[tuple[int, str]] = []

    for i, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        m = _RE_H1.match(stripped)
        if m:
            chapter_starts.append((i, m.group(1).strip()))

    if not chapter_starts:
        return []

    result: list[tuple[str, str]] = []
    for idx, (start_line, title) in enumerate(chapter_starts):
        end_line = chapter_starts[idx + 1][0] if idx + 1 < len(chapter_starts) else len(lines)
        chapter_body = "".join(lines[start_line:end_line]).rstrip()
        result.append((title, chapter_body))

    return result


def _build_payload(
    *,
    book_id: str,
    raw_path: str,
    chapter_index: int,
    chapter_title: str,
    verbatim_body: str,
) -> ChapterPayload:
    lines = verbatim_body.splitlines()
    return ChapterPayload(
        book_id=book_id,
        raw_path=raw_path,
        chapter_index=chapter_index,
        chapter_title=chapter_title,
        verbatim_body=verbatim_body,
        section_anchors=_extract_section_anchors(lines),
        figures=_extract_figures(verbatim_body),
        tables=_extract_tables(lines),
    )


def _extract_section_anchors(lines: list[str]) -> list[str]:
    anchors = []
    for line in lines:
        m = _RE_H2.match(line.rstrip("\r\n"))
        if m:
            anchors.append(m.group(1).strip())
    return anchors


def _extract_figures(body: str) -> list[FigureRef]:
    return [
        FigureRef(vault_path=m.group(2), alt_text=m.group(1)) for m in _RE_FIGURE.finditer(body)
    ]


def _extract_tables(lines: list[str]) -> list[InlineTable]:
    tables: list[InlineTable] = []
    i = 0
    while i < len(lines):
        if _RE_TABLE_ROW.match(lines[i].rstrip()):
            start = i
            while i < len(lines) and _RE_TABLE_ROW.match(lines[i].rstrip()):
                i += 1
            table_md = "\n".join(ln.rstrip() for ln in lines[start:i])

            caption = ""
            # Look back past any blank lines to find a bold caption.
            look = start - 1
            while look >= 0 and not lines[look].rstrip():
                look -= 1
            if look >= 0:
                prev = lines[look].rstrip()
                cap_m = _RE_BOLD_CAPTION.match(prev)
                if cap_m:
                    caption = cap_m.group(1)

            tables.append(InlineTable(markdown=table_md, caption=caption))
        else:
            i += 1
    return tables
