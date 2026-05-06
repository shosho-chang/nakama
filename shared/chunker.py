"""Parent-Child semantic hierarchical chunker for ADR-020 S6.

parent_child_chunks(chapter_text, *, book_id, chapter_index):
  Splits a source page into Parent + Child chunks per section (## heading).

  Parent chunk = section heading + concept maps (mermaid) + wikilinks introduced.
  Child chunk  = sliding window of verbatim paragraphs within the section
                 (window=4 paras, overlap=1 para).

  Small-to-big retrieval pattern: dense retrieval on child text → parent text
  pulled into LLM context for full section scope.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from shared.log import get_logger

logger = get_logger("nakama.shared.chunker")

_RE_H2 = re.compile(r"^(##\s+.+)$", re.MULTILINE)
_RE_MERMAID = re.compile(r"```mermaid\n.*?```", re.DOTALL)
_RE_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
_RE_FIGURE_EMBED = re.compile(r"!\[\[Attachments/[^\]]+/(fig-\d+-\d+[^/\]]*)\]\]")
_RE_TABLE_EMBED = re.compile(r"!\[\[Attachments/[^\]]+/(tab-\d+-\d+[^/\]]*)\]\]")
_RE_WIKILINKS_SECTION = re.compile(
    r"###\s+Wikilinks introduced\s*\n(.*?)(?=\n###|\n##|$)", re.DOTALL | re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ParentChunk:
    """Section-level metadata chunk: heading + concept maps + wikilinks introduced.

    Never contains verbatim body text.
    """

    chunk_id: str
    book_id: str
    chapter_index: int
    section_anchor: str
    text: str
    child_ids: list[str] = field(default_factory=list)


@dataclass
class ChildChunk:
    """Verbatim-paragraph window within a section.

    ``parent_id`` points to the enclosing ParentChunk for small-to-big retrieval.
    """

    chunk_id: str
    parent_id: str
    book_id: str
    chapter_index: int
    section_anchor: str
    text: str
    paragraph_range: tuple[int, int]
    figures_referenced: list[str] = field(default_factory=list)
    tables_referenced: list[str] = field(default_factory=list)
    concepts_introduced: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parent_child_chunks(
    chapter_text: str,
    *,
    book_id: str,
    chapter_index: int,
    child_window: int = 4,
    child_overlap: int = 1,
) -> tuple[list[ParentChunk], list[ChildChunk]]:
    """Split a chapter source page into parent/child chunk pairs.

    Args:
        chapter_text:   Full text of the chapter source page (markdown).
        book_id:        Book slug, e.g. ``"bse-2024"``.
        chapter_index:  Chapter number.
        child_window:   Number of verbatim paragraphs per child chunk.
        child_overlap:  Overlap between consecutive child windows (para count).

    Returns:
        (parents, children) — parallel lists; children reference parents via
        ``parent_id``.  Returns ([], []) if no H2 sections found.
    """
    sections = _split_by_h2(chapter_text)
    if not sections:
        return [], []

    parents: list[ParentChunk] = []
    children: list[ChildChunk] = []

    for heading_line, section_body in sections:
        section_anchor = heading_line.lstrip("#").strip()
        section_slug = _to_slug(section_anchor)
        parent_id = f"{book_id}/ch{chapter_index}/{section_slug}"

        # -- parent text --
        mermaid_blocks = _extract_mermaid(section_body)
        wikilinks_section = _extract_wikilinks_section_text(section_body)
        parent_text_parts = [heading_line]
        if mermaid_blocks:
            parent_text_parts.extend(mermaid_blocks)
        if wikilinks_section:
            parent_text_parts.append(wikilinks_section.strip())
        parent_text = "\n\n".join(parent_text_parts)

        parent = ParentChunk(
            chunk_id=parent_id,
            book_id=book_id,
            chapter_index=chapter_index,
            section_anchor=section_anchor,
            text=parent_text,
        )
        parents.append(parent)

        # -- children --
        verbatim_paras = _extract_verbatim_paragraphs(section_body)
        if not verbatim_paras:
            continue

        section_wikilinks = _extract_wikilinks_from_text(wikilinks_section)

        window_start = 0
        child_idx = 0
        while window_start < len(verbatim_paras):
            window_end = min(window_start + child_window, len(verbatim_paras))
            window_paras = verbatim_paras[window_start:window_end]
            child_text = "\n\n".join(window_paras)
            inline_wikilinks = _extract_wikilinks_from_text(child_text)
            all_concepts = list(dict.fromkeys(inline_wikilinks + section_wikilinks))

            child = ChildChunk(
                chunk_id=f"{parent_id}/child-{child_idx}",
                parent_id=parent_id,
                book_id=book_id,
                chapter_index=chapter_index,
                section_anchor=section_anchor,
                text=child_text,
                paragraph_range=(window_start, window_end - 1),
                figures_referenced=_extract_figures(child_text),
                tables_referenced=_extract_tables(child_text),
                concepts_introduced=all_concepts,
            )
            children.append(child)
            parent.child_ids.append(child.chunk_id)

            child_idx += 1
            if window_end >= len(verbatim_paras):
                break
            window_start = window_end - child_overlap

    return parents, children


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _split_by_h2(text: str) -> list[tuple[str, str]]:
    """Split text by H2 headings. Returns list of (heading_line, body_after)."""
    matches = list(_RE_H2.finditer(text))
    if not matches:
        return []

    result: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        heading = m.group(1)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end]
        result.append((heading, body))

    return result


def _to_slug(text: str) -> str:
    """Convert section heading text to a URL-safe slug."""
    slug = text.lower()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = slug.strip("-")
    return slug[:80]


def _extract_mermaid(text: str) -> list[str]:
    return _RE_MERMAID.findall(text)


def _extract_wikilinks_section_text(text: str) -> str:
    m = _RE_WIKILINKS_SECTION.search(text)
    if m:
        header = "### Wikilinks introduced"
        return header + "\n" + m.group(1).strip()
    return ""


def _extract_verbatim_paragraphs(text: str) -> list[str]:
    """Extract verbatim paragraphs: exclude mermaid blocks and wikilinks sections."""
    without_mermaid = _RE_MERMAID.sub("", text)
    without_wikilinks = _RE_WIKILINKS_SECTION.sub("", without_mermaid)

    paras = re.split(r"\n\n+", without_wikilinks)
    result = []
    for para in paras:
        stripped = para.strip()
        if not stripped:
            continue
        if stripped.startswith("###"):
            continue
        result.append(stripped)
    return result


def _extract_figures(text: str) -> list[str]:
    return [m.group(1) for m in _RE_FIGURE_EMBED.finditer(text)]


def _extract_tables(text: str) -> list[str]:
    return [m.group(1) for m in _RE_TABLE_EMBED.finditer(text)]


def _extract_wikilinks_from_text(text: str) -> list[str]:
    return [m.group(1) for m in _RE_WIKILINK.finditer(text)]
