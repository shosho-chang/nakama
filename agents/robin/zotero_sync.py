"""Zotero sync orchestrator (Slice 1 #389).

Composes :mod:`agents.robin.zotero_reader` (SQLite + attachment selection),
:mod:`agents.robin.zotero_assets` (asset copy + image rewrite), Trafilatura
extraction, and the :class:`shared.schemas.IngestResult` schema into a
single ``sync_zotero_item`` entry point that the URL dispatcher can call.

PDF path is Slice 2 (#390); HTML happy path is Slice 1.
"""

from __future__ import annotations

from pathlib import Path

import trafilatura

from agents.robin.zotero_assets import copy_assets, rewrite_image_paths
from agents.robin.zotero_reader import ZoteroReader
from shared.schemas.ingest_result import IngestResult
from shared.utils import slugify


def sync_zotero_item(
    item_key: str,
    *,
    zotero_root: Path,
    vault_root: Path,
) -> tuple[IngestResult, str]:
    """Run the full Zotero HTML-snapshot sync pipeline for a single item.

    Steps:

    1. Read item metadata + chosen attachment from local Zotero SQLite.
    2. Trafilatura-extract the snapshot HTML to clean markdown.
    3. Copy snapshot's ``_assets/`` sibling folder into vault.
    4. Rewrite ``_assets/`` image references to vault-relative paths.
    5. Compose :class:`IngestResult` (with Zotero-specific fields populated).

    Returns ``(result, slug)`` — caller (``url_dispatcher`` / ``inbox_writer``)
    consumes both.

    Raises:
        KeyError: ``item_key`` not in the local Zotero library.
        NoAttachmentError: item exists but has no usable attachment.
        NotImplementedError: PDF-only items (Slice 2 #390 lands the PDF path).
    """
    item = ZoteroReader(zotero_root).get_item(item_key)

    if item.attachment_type != "text/html":
        raise NotImplementedError(
            f"PDF-only item {item_key} requires Slice 2 (#390) — not yet implemented"
        )

    slug = slugify(item.title)

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

    # Copy snapshot's _assets/ to vault; rewrite image src.
    vault_assets_dir = vault_root / "KB" / "Attachments" / "zotero" / slug / "_assets"
    copy_assets(item.attachment_path, vault_assets_dir)
    vault_prefix = f"KB/Attachments/zotero/{slug}/_assets"
    md = rewrite_image_paths(md, vault_prefix)

    result = IngestResult(
        status="ready",
        fulltext_layer="zotero_html_snapshot",
        fulltext_source="Zotero HTML snapshot",
        markdown=md,
        title=item.title,
        original_url=f"zotero://select/library/items/{item_key}",
        zotero_item_key=item_key,
        zotero_attachment_path=str(item.attachment_path),
        attachment_type=item.attachment_type,
    )
    return result, slug
