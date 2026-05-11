"""EPUB → Raw Markdown converter (ADR-020 Phase 0).

Converts an EPUB to a single-file raw markdown document for ``KB/Raw/Books/``.
Images are extracted to ``Attachments/Books/{book_id}/``; the returned markdown
uses standard Markdown image syntax with vault-relative paths.

No LLM calls. No chapter splitting. Lossless source layer.
Phase 1 reads this output for verbatim body extraction + chapter boundary identification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub
from markdownify import markdownify as html_to_md

from shared.log import get_logger

logger = get_logger("nakama.shared.raw_ingest")

_CONVERTER_TOOL = "ebooklib+markdownify"


class EPUBConversionError(ValueError):
    """Raised when EPUB cannot be read or contains no spine content."""


@dataclass
class RawIngestResult:
    """Return value of epub_to_raw_markdown."""

    markdown: str
    book_id: str
    title: str | None
    images_extracted: list[str] = field(default_factory=list)
    """Vault-relative paths, e.g. ``"Attachments/Books/bse-2024/fig1.png"``."""


def epub_to_raw_markdown(
    epub_path: Path | str,
    book_id: str,
    *,
    attachments_dir: Path | str,
) -> RawIngestResult:
    """Convert an EPUB file to lossless raw markdown.

    Side-effects: writes image files to ``attachments_dir / book_id / {filename}``.
    The returned markdown embeds images as ``![alt](Attachments/Books/{book_id}/{filename})``.

    Args:
        epub_path:       Path to the source ``.epub`` file.
        book_id:         Slug identifier (e.g. ``"biochemistry-sport-exercise-2024"``).
        attachments_dir: Base directory; images land at ``{attachments_dir}/{book_id}/``.

    Returns:
        RawIngestResult with frontmatter + body markdown and extraction metadata.

    Raises:
        FileNotFoundError:   If ``epub_path`` does not exist.
        EPUBConversionError: If the EPUB cannot be parsed or has no spine content.
    """
    epub_path = Path(epub_path)
    if not epub_path.exists():
        raise FileNotFoundError(f"EPUB not found: {epub_path}")

    attachments_dir = Path(attachments_dir)

    try:
        book = epub.read_epub(str(epub_path), options={"ignore_ncx": True})
    except Exception as exc:
        raise EPUBConversionError(f"Cannot read EPUB '{epub_path.name}'") from exc

    image_name_map, images_extracted = _extract_images(
        book, book_id=book_id, attachments_dir=attachments_dir
    )
    md_parts = _convert_spine(book, book_id=book_id, image_name_map=image_name_map)

    if not md_parts:
        raise EPUBConversionError(f"No content found in EPUB '{epub_path.name}'")

    title_meta = book.get_metadata("DC", "title")
    title_str: str | None = title_meta[0][0] if title_meta else None

    frontmatter = _make_frontmatter(
        title=title_str or book_id,
        book_id=book_id,
        source_epub_path=str(epub_path.resolve()),
        converter_tool=_CONVERTER_TOOL,
        converter_version=_get_tool_version(),
    )
    body = "\n\n".join(md_parts)
    full_markdown = frontmatter + "\n\n" + body + "\n"

    logger.info(
        "Converted EPUB '%s': %d spine items, %d images",
        epub_path.name,
        len(md_parts),
        len(images_extracted),
    )
    return RawIngestResult(
        markdown=full_markdown,
        book_id=book_id,
        title=title_str,
        images_extracted=images_extracted,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_images(
    book: epub.EpubBook,
    *,
    book_id: str,
    attachments_dir: Path,
) -> tuple[dict[str, str], list[str]]:
    """Extract all image items from manifest to disk.

    Returns:
        (image_name_map, vault_paths) where image_name_map maps the manifest
        internal path to the output basename, and vault_paths is a list of
        vault-relative paths for the caller's metadata.
    """
    image_name_map: dict[str, str] = {}
    vault_paths: list[str] = []

    book_attach = attachments_dir / book_id
    book_attach.mkdir(parents=True, exist_ok=True)

    for item in book.get_items():
        if item.get_type() not in (ebooklib.ITEM_IMAGE, ebooklib.ITEM_COVER):
            continue
        internal = item.get_name()
        filename = Path(internal).name
        (book_attach / filename).write_bytes(item.get_content())
        image_name_map[internal] = filename
        vault_path = f"Attachments/Books/{book_id}/{filename}"
        vault_paths.append(vault_path)
        logger.debug("Extracted image: %s", vault_path)

    return image_name_map, vault_paths


def _convert_spine(
    book: epub.EpubBook,
    *,
    book_id: str,
    image_name_map: dict[str, str],
) -> list[str]:
    """Convert spine items in reading order to markdown chunks."""
    manifest: dict[str, epub.EpubItem] = {item.id: item for item in book.get_items()}
    chunks: list[str] = []

    for idref, _linear in book.spine:
        item = manifest.get(idref)
        if item is None or item.get_type() != ebooklib.ITEM_DOCUMENT:
            continue
        html = item.get_content().decode("utf-8", errors="replace")
        chunk = _chapter_to_md(
            html,
            book_id=book_id,
            item_name=item.get_name(),
            image_name_map=image_name_map,
        )
        if chunk.strip():
            chunks.append(chunk)

    return chunks


def _chapter_to_md(
    html: str,
    *,
    book_id: str,
    item_name: str,
    image_name_map: dict[str, str],
) -> str:
    """Convert a single xHTML chapter to clean ATX markdown."""
    soup = BeautifulSoup(html, "lxml")

    for tag in soup.find_all(["head", "nav"]):
        tag.decompose()

    for img in soup.find_all("img"):
        src = img.get("src", "")
        if not src:
            continue
        resolved = _resolve_epub_href(item_name, src)
        filename = image_name_map.get(resolved)
        if filename:
            img["src"] = f"Attachments/Books/{book_id}/{filename}"

    body_tag = soup.find("body")
    target = str(body_tag) if body_tag else str(soup)
    md = html_to_md(target, heading_style="ATX", strip=["html", "body", "head"])
    return _clean_md(md)


def _resolve_epub_href(base_item_name: str, href: str) -> str:
    """Resolve a document-relative href to its manifest key.

    Example::

        _resolve_epub_href("Text/ch1.xhtml", "../Images/fig1.png")
        # → "Images/fig1.png"
    """
    base = PurePosixPath(base_item_name).parent
    parts: list[str] = []
    for segment in (base / PurePosixPath(href)).parts:
        if segment == ".":
            continue
        if segment == "..":
            if parts:
                parts.pop()
        else:
            parts.append(segment)
    return "/".join(parts)


def _clean_md(text: str) -> str:
    """Strip XML/DOCTYPE artifacts and collapse excess blank lines."""
    text = re.sub(r"<\?xml[^?]*\?>", "", text)
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _make_frontmatter(
    *,
    title: str,
    book_id: str,
    source_epub_path: str,
    converter_tool: str,
    converter_version: str,
) -> str:
    today = date.today().isoformat()

    def esc(s: str) -> str:
        """Escape for a double-quoted YAML scalar."""
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")

    return (
        "---\n"
        f'title: "{esc(title)}"\n'
        f'book_id: "{esc(book_id)}"\n'
        f'source_epub_path: "{esc(source_epub_path)}"\n'
        f"converted_date: {today}\n"
        f'converter_tool: "{esc(converter_tool)}"\n'
        f'converter_version: "{esc(converter_version)}"\n'
        "---"
    )


def _get_tool_version() -> str:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as pkg_version

    try:
        eb_ver = pkg_version("EbookLib")
    except PackageNotFoundError:
        eb_ver = "unknown"
    try:
        mk_ver = pkg_version("markdownify")
    except PackageNotFoundError:
        mk_ver = "unknown"
    return f"ebooklib/{eb_ver}+markdownify/{mk_ver}"
