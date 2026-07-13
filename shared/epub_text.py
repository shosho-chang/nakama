"""Flatten an EPUB blob into spine-ordered plain text — for KB ingest (route B).

The KB ingest pipeline (``agents/robin/ingest.py``) consumes a **single text
file** (``read_text`` over a ``.md`` / ``.vtt``). Books are stored as EPUB
binaries (a multi-file zip, outside the vault). This module flattens an EPUB
into spine-ordered plain text so a book can go through the *same* article /
video ingest path (summary → concept extraction → KB/Wiki), rather than needing
a separate book-only pipeline.

Parses the OCF container → OPF spine (reading order) → reads each content
document in order → strips XHTML tags to text. Tolerates malformed parts
(skip the part, don't sink the book). This is a faithful-enough text dump for
summarization + concept extraction, NOT a layout-preserving renderer.

Mirrors the OCF/OPF parsing in ``shared.epub_metadata`` (kept separate:
metadata extraction vs full-text flattening are different responsibilities).
"""

from __future__ import annotations

import html
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import PurePosixPath

_NS_CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
_NS_OPF = "http://www.idpf.org/2007/opf"


class EPUBTextError(ValueError):
    """Raised when the EPUB's OCF/OPF structure can't be parsed."""


def extract_text(blob: bytes, *, max_chars: int | None = None) -> str:
    """EPUB bytes → spine-ordered plain text.

    Raises :class:`EPUBTextError` if the blob isn't a valid OCF package (the
    caller should mark the queue row failed). Individual unreadable spine parts
    are skipped, not fatal.

    ``max_chars`` caps total output (token-cost guard for very large books; the
    ingest pipeline's map-reduce handles large inputs but an unbounded 500-page
    book is wasteful). ``None`` = no cap. The caller logs truncation.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise EPUBTextError("Not a valid zip/EPUB") from exc

    with zf:
        names = set(zf.namelist())
        if "META-INF/container.xml" not in names:
            raise EPUBTextError("Missing META-INF/container.xml — not a valid EPUB")
        opf_path = _find_opf_path(zf.read("META-INF/container.xml"))
        opf_root = _parse_xml(zf.read(opf_path))
        opf_dir = str(PurePosixPath(opf_path).parent)
        if opf_dir == ".":  # OPF at zip root → PurePosixPath.parent is "."; treat as no prefix
            opf_dir = ""

        parts: list[str] = []
        total = 0
        for href in _spine_hrefs(opf_root, opf_dir):
            if href not in names:
                continue
            try:
                text = _xhtml_to_text(zf.read(href))
            except Exception:  # noqa: BLE001 — one bad chapter shouldn't sink the book
                continue
            if not text.strip():
                continue
            parts.append(text)
            total += len(text) + 2
            if max_chars is not None and total >= max_chars:
                break

    out = "\n\n".join(parts)
    if max_chars is not None and len(out) > max_chars:
        out = out[:max_chars].rstrip()
    return out


# ---------------------------------------------------------------------------
# OCF / OPF parsing (mirrors shared.epub_metadata)
# ---------------------------------------------------------------------------


def _parse_xml(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data.decode("utf-8", errors="replace"))
    except ET.ParseError as exc:
        raise EPUBTextError("Malformed XML in EPUB OPF/container") from exc


def _find_opf_path(container_xml: bytes) -> str:
    root = _parse_xml(container_xml)
    for rf in root.iter(f"{{{_NS_CONTAINER}}}rootfile"):
        path = rf.get("full-path")
        if path:
            return path
    raise EPUBTextError("No rootfile found in container.xml")


def _spine_hrefs(opf_root: ET.Element, opf_dir: str) -> list[str]:
    """OPF ``<spine>`` itemrefs → content hrefs in reading order (manifest id→href)."""
    manifest = opf_root.find(f"{{{_NS_OPF}}}manifest")
    spine = opf_root.find(f"{{{_NS_OPF}}}spine")
    if manifest is None or spine is None:
        return []
    id_to_href: dict[str, str] = {}
    for item in manifest:
        iid = item.get("id")
        href = item.get("href")
        if iid and href:
            id_to_href[iid] = href
    hrefs: list[str] = []
    for itemref in spine:
        idref = itemref.get("idref")
        if not idref:
            continue
        href = id_to_href.get(idref)
        if not href:
            continue
        hrefs.append(f"{opf_dir}/{href}" if opf_dir else href)
    return hrefs


# ---------------------------------------------------------------------------
# XHTML → text
# ---------------------------------------------------------------------------

_BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.I | re.S)
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_BLOCK_END_RE = re.compile(r"</(p|div|h[1-6]|li|tr|section|article|blockquote)\s*>", re.I)
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_INLINE_WS_RE = re.compile(r"[ \t ]+")
_MULTI_NL_RE = re.compile(r"\n\s*\n\s*\n+")


def _xhtml_to_text(data: bytes) -> str:
    """Lossy XHTML → text: drop script/style, block tags → newlines, unescape entities.

    Regex-based (not a strict XML parse) so malformed real-world chapters still
    yield usable text instead of crashing.
    """
    s = data.decode("utf-8", errors="replace")
    body = _BODY_RE.search(s)  # drop <head> (title/meta) — only body is reading content
    if body:
        s = body.group(1)
    s = _SCRIPT_STYLE_RE.sub(" ", s)
    s = _BLOCK_END_RE.sub("\n", s)
    s = _BR_RE.sub("\n", s)
    s = _TAG_RE.sub("", s)
    s = html.unescape(s)
    # normalize whitespace: collapse inline runs, cap blank-line runs at one.
    s = "\n".join(_INLINE_WS_RE.sub(" ", line).strip() for line in s.splitlines())
    s = _MULTI_NL_RE.sub("\n\n", s)
    return s.strip()
