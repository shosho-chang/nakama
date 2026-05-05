"""Zotero sync orchestrator (Slice 1 #389, Slice 2 #390).

Composes :mod:`agents.robin.zotero_reader` (SQLite + attachment selection),
:mod:`agents.robin.zotero_assets` (asset copy + figure extraction + image
rewrite), Trafilatura extraction, :func:`shared.pdf_parser.parse_pdf`
(pymupdf4llm + pdfplumber), and the :class:`shared.schemas.IngestResult`
schema into a single ``sync_zotero_item`` entry point that the URL dispatcher
can call.

HTML snapshot path: Slice 1.  PDF fallback path: Slice 2.
"""

from __future__ import annotations

from pathlib import Path

import trafilatura

from agents.robin.zotero_assets import copy_assets, extract_pdf_figures, rewrite_image_paths
from agents.robin.zotero_reader import ZoteroReader
from shared.pdf_parser import parse_pdf
from shared.schemas.ingest_result import IngestResult
from shared.utils import slugify


def sync_zotero_item(
    item_key: str,
    *,
    zotero_root: Path,
    vault_root: Path,
) -> tuple[IngestResult, str]:
    """Run the full Zotero sync pipeline for a single item.

    Dispatches to the HTML snapshot path or the PDF fallback path based on
    the primary attachment selected by
    :class:`~agents.robin.zotero_reader.ZoteroReader` (HTML preferred, PDF
    fallback when HTML is absent).

    Returns ``(result, slug)`` — caller (``url_dispatcher`` / ``inbox_writer``)
    consumes both.

    Raises:
        KeyError: ``item_key`` not in the local Zotero library.
        NoAttachmentError: item exists but has no usable attachment.
    """
    item = ZoteroReader(zotero_root).get_item(item_key)
    slug = slugify(item.title)
    vault_assets_dir = vault_root / "KB" / "Attachments" / "zotero" / slug / "_assets"
    vault_prefix = f"KB/Attachments/zotero/{slug}/_assets"

    if item.attachment_type == "text/html":
        # Trafilatura on the local snapshot file. Mirrors the option set of
        # ``shared.web_scraper._scrape_trafilatura`` plus ``include_images`` /
        # ``include_links`` — Zotero snapshot ingest specifically wants the
        # snapshot's figures preserved (the existing URL-scrape path drops them
        # because PR #355 image first-class delegates to a separate downloader).
        html = item.attachment_path.read_text(encoding="utf-8")
        md = (
            trafilatura.extract(
                html,
                output_format="markdown",
                include_comments=False,
                include_tables=True,
                include_images=True,
                include_links=True,
                favor_recall=True,
            )
            or ""
        )

        copy_assets(item.attachment_path, vault_assets_dir)
        md = rewrite_image_paths(md, vault_prefix)

        return IngestResult(
            status="ready",
            fulltext_layer="zotero_html_snapshot",
            fulltext_source="Zotero HTML snapshot",
            markdown=md,
            title=item.title,
            original_url=f"zotero://select/library/items/{item_key}",
            zotero_item_key=item_key,
            zotero_attachment_path=str(item.attachment_path),
            attachment_type=item.attachment_type,
        ), slug

    # PDF fallback path (Slice 2 #390).
    md = parse_pdf(item.attachment_path, with_tables=True)
    extract_pdf_figures(item.attachment_path, vault_assets_dir)

    return IngestResult(
        status="ready",
        fulltext_layer="zotero_pdf",
        fulltext_source="Zotero PDF",
        markdown=md,
        title=item.title,
        original_url=f"zotero://select/library/items/{item_key}",
        zotero_item_key=item_key,
        zotero_attachment_path=str(item.attachment_path),
        attachment_type=item.attachment_type,
    ), slug
