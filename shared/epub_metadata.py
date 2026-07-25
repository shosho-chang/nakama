"""Extract structured metadata from an EPUB blob.

Parses content.opf for Dublin Core fields (title / author / language / ISBN /
date) and the manifest for cover path; reads nav.xhtml for the TOC structure.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from pathlib import PurePosixPath

from shared.schemas.books import BookMetadata, TocEntry

_NS_CONTAINER = "urn:oasis:names:tc:opendocument:xmlns:container"
_NS_OPF = "http://www.idpf.org/2007/opf"
_NS_DC = "http://purl.org/dc/elements/1.1/"
_NS_XHTML = "http://www.w3.org/1999/xhtml"
_NS_EPUB = "http://www.idpf.org/2007/ops"


class MalformedEPUBError(ValueError):
    """Raised when the OCF/OPF structure cannot be parsed."""


def extract_metadata(blob: bytes) -> BookMetadata:
    """Parse content.opf + nav.xhtml; missing fields are None.

    Raises MalformedEPUBError if the OCF / OPF cannot be parsed.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = set(zf.namelist())
            if "META-INF/container.xml" not in names:
                raise MalformedEPUBError("Missing META-INF/container.xml")

            opf_path = _find_opf_path(zf.read("META-INF/container.xml"))
            opf_root = _parse_xml(zf.read(opf_path))
            opf_dir = str(PurePosixPath(opf_path).parent)

            title = _dc_text(opf_root, "title")
            author = _dc_text(opf_root, "creator")
            lang = _dc_text(opf_root, "language")
            isbn = _extract_isbn(opf_root)
            published_year = _extract_year(opf_root)
            cover_path = _extract_cover_path(opf_root, opf_dir)

            toc: list[TocEntry] = []
            nav_path = _find_nav_path(opf_root, opf_dir)
            if nav_path and nav_path in names:
                toc = _extract_toc(zf.read(nav_path))

            return BookMetadata(
                title=title,
                author=author,
                lang=lang,
                isbn=isbn,
                published_year=published_year,
                cover_path=cover_path,
                toc=toc,
            )
    except zipfile.BadZipFile as exc:
        raise MalformedEPUBError("Not a valid zip") from exc


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_xml(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data.decode("utf-8", errors="replace"))
    except ET.ParseError as exc:
        raise MalformedEPUBError("Malformed XML") from exc


def _find_opf_path(container_xml: bytes) -> str:
    root = _parse_xml(container_xml)
    for rf in root.iter(f"{{{_NS_CONTAINER}}}rootfile"):
        path = rf.get("full-path")
        if path:
            return path
    raise MalformedEPUBError("No rootfile found in container.xml")


def _dc_text(opf_root: ET.Element, field: str) -> str | None:
    elem = opf_root.find(f".//{{{_NS_DC}}}{field}")
    if elem is not None and elem.text:
        return elem.text.strip() or None
    return None


def _extract_isbn(opf_root: ET.Element) -> str | None:
    for elem in opf_root.findall(f".//{{{_NS_DC}}}identifier"):
        text = (elem.text or "").strip()
        if text.startswith("urn:isbn:"):
            return text[len("urn:isbn:") :]
    return None


def _extract_year(opf_root: ET.Element) -> int | None:
    elem = opf_root.find(f".//{{{_NS_DC}}}date")
    if elem is not None and elem.text:
        try:
            return int(elem.text.strip()[:4])
        except (ValueError, IndexError):
            return None
    return None


def _manifest(opf_root: ET.Element) -> ET.Element | None:
    return opf_root.find(f"{{{_NS_OPF}}}manifest")


def _resolve(opf_dir: str, href: str) -> str:
    return f"{opf_dir}/{href}" if opf_dir else href


def _extract_cover_path(opf_root: ET.Element, opf_dir: str) -> str | None:
    mf = _manifest(opf_root)
    if mf is None:
        return None

    # Priority 1: manifest item with properties="cover-image"
    for item in mf:
        if "cover-image" in item.get("properties", ""):
            href = item.get("href")
            if href:
                return _resolve(opf_dir, href)

    # Priority 2: <meta name="cover" content="item-id"> in metadata
    metadata = opf_root.find(f"{{{_NS_OPF}}}metadata")
    if metadata is not None:
        for meta in metadata:
            if meta.get("name") == "cover":
                item_id = meta.get("content")
                if item_id:
                    cover_item = mf.find(f".//*[@id='{item_id}']")
                    if cover_item is not None:
                        href = cover_item.get("href")
                        if href:
                            return _resolve(opf_dir, href)

    return None


def _find_nav_path(opf_root: ET.Element, opf_dir: str) -> str | None:
    mf = _manifest(opf_root)
    if mf is None:
        return None
    for item in mf:
        if "nav" in item.get("properties", ""):
            href = item.get("href")
            if href:
                return _resolve(opf_dir, href)
    return None


def _extract_toc(nav_xml: bytes) -> list[TocEntry]:
    try:
        root = ET.fromstring(nav_xml.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return []

    for nav in root.iter(f"{{{_NS_XHTML}}}nav"):
        epub_type = nav.get(f"{{{_NS_EPUB}}}type", "")
        if "toc" in epub_type or nav.get("id") == "toc":
            ol = nav.find(f"{{{_NS_XHTML}}}ol")
            if ol is not None:
                return _parse_ol(ol)

    # Fallback: first <ol> in document
    for ol in root.iter(f"{{{_NS_XHTML}}}ol"):
        return _parse_ol(ol)

    return []


def _parse_ol(ol: ET.Element) -> list[TocEntry]:
    entries: list[TocEntry] = []
    for li in ol:
        if _local(li.tag) != "li":
            continue
        a = li.find(f"{{{_NS_XHTML}}}a")
        if a is None:
            continue
        title = (a.text or "").strip()
        href = a.get("href", "")
        nested_ol = li.find(f"{{{_NS_XHTML}}}ol")
        children = _parse_ol(nested_ol) if nested_ol is not None else []
        entries.append(TocEntry(title=title, href=href, children=children))
    return entries


def _local(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


# ── Spine / TOC public helpers（自 agents.robin.promotion.source_map_builder 遷入）──────────
# source_map_builder（promotion 領域，agents/robin/promotion/）與
# literature_writer（shared/）都需要 spine 抽取 + TOC 標題映射。EPUB 結構
# 解析的正宗家在這裡 — 領域模組不該各自持有一份 OPF 解析。


def build_toc_title_map(toc: list[TocEntry]) -> dict[str, str]:
    """Flatten a (possibly nested) TocEntry list into ``{href_stem: title}``.

    href_stem is the file-name component of TOC ``href`` (TOC may carry
    fragments like ``ch1.xhtml#sec1``; we want ``ch1.xhtml``).
    """
    out: dict[str, str] = {}

    def walk(entries: list[TocEntry]) -> None:
        for entry in entries:
            href = entry.href.split("#", 1)[0]
            stem = PurePosixPath(href).name
            if stem and entry.title and stem not in out:
                out[stem] = entry.title.strip()
            walk(entry.children)

    walk(toc)
    return out


def extract_spine_items(blob: bytes) -> list[tuple[str, str]]:
    """Return ``[(href_in_zip, raw_xhtml_text), ...]`` in spine order.

    Pure stdlib (``zipfile`` + ``xml.etree``). Returns the *raw* XHTML so
    callers can lift ``<title>`` / ``<h1>`` cheaply; whitespace stripping is
    a downstream concern. Failures propagate (``MalformedEPUBError`` for
    OCF/OPF structure problems); callers wrap them per their own error model.
    """
    out: list[tuple[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = set(zf.namelist())
        if "META-INF/container.xml" not in names:
            raise MalformedEPUBError("Missing META-INF/container.xml")
        opf_path = _find_opf_path(zf.read("META-INF/container.xml"))
        opf_root = _parse_xml(zf.read(opf_path))
        opf_dir = str(PurePosixPath(opf_path).parent)
        if opf_dir == ".":
            # Root-level OPF (PurePosixPath("content.opf").parent is ".").
            # Truthy "." would prefix manifest hrefs with "./" which won't
            # match zipfile.namelist() keys; normalize to "" so _resolve
            # skips the prefix entirely.
            opf_dir = ""

        mf = _manifest(opf_root)
        manifest_map: dict[str, str] = {}
        if mf is not None:
            for item in mf:
                item_id = item.get("id")
                href = item.get("href")
                if not item_id or not href:
                    continue
                manifest_map[item_id] = _resolve(opf_dir, href)

        spine = opf_root.find(f"{{{_NS_OPF}}}spine")
        if spine is None:
            return []
        for itemref in spine:
            idref = itemref.get("idref")
            if not idref or idref not in manifest_map:
                continue
            href = manifest_map[idref]
            if href not in names:
                continue
            try:
                xhtml = zf.read(href).decode("utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                # Skip unreadable spine items; structural errors still raise.
                continue
            out.append((href, xhtml))
    return out
