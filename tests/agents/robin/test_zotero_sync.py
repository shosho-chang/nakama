"""Integration tests for ``agents/robin/zotero_sync.sync_zotero_item`` (Slice 1 #389, Slice 2 #390).

Covers:
- Slice 1 HTML happy path (fixture SQLite + snapshot + Trafilatura → IngestResult)
- Slice 2 PDF fallback path (fixture SQLite + real PDF + parse_pdf → IngestResult)
- Regression: item with both HTML+PDF → HTML is chosen
"""

from __future__ import annotations

from pathlib import Path

from agents.robin.zotero_sync import sync_zotero_item
from tests.agents.robin._zotero_fixture import (
    add_html_snapshot,
    add_journal_article,
    add_pdf_attachment,
    init_zotero_lib,
)

_REAL_SNAPSHOT_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>A Sample Paper on Mitochondria</title>
</head>
<body>
  <article>
    <h1>A Sample Paper on Mitochondria</h1>
    <p>This is the introduction paragraph. It contains enough words to pass
    the Trafilatura minimum-length heuristic so the extractor returns a real
    body rather than empty content. Mitochondria are double-membraned
    organelles found in most eukaryotic cells.</p>
    <p>The second paragraph extends the discussion further with additional
    biological context about cellular respiration and oxidative
    phosphorylation. Each paragraph keeps adding mass so Trafilatura is
    confident this is the main content area, not navigation chrome.</p>
    <figure>
      <img src="_assets/fig1.png" alt="Mitochondrion diagram">
      <figcaption>Figure 1.</figcaption>
    </figure>
  </article>
</body>
</html>
"""


def test_sync_zotero_item_html_path_end_to_end(tmp_path: Path):
    """Full pipeline: SQLite read → Trafilatura → asset copy → IngestResult."""
    fixture = init_zotero_lib(tmp_path / "Zotero")
    parent_id = add_journal_article(
        fixture, item_key="ABC12345", title="A Sample Paper on Mitochondria"
    )
    add_html_snapshot(
        fixture,
        parent_item_id=parent_id,
        attachment_key="HTML0001",
        body=_REAL_SNAPSHOT_HTML,
    )
    # Fake the snapshot's _assets/ sibling with one image.
    (fixture.storage_dir / "HTML0001" / "_assets").mkdir()
    (fixture.storage_dir / "HTML0001" / "_assets" / "fig1.png").write_bytes(
        b"\x89PNG\r\n\x1a\n fake png bytes"
    )

    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    result, slug = sync_zotero_item(
        "ABC12345",
        zotero_root=fixture.zotero_root,
        vault_root=vault_root,
    )

    # Slug derived from title via shared.utils.slugify (case preserved).
    assert slug == "A-Sample-Paper-on-Mitochondria"

    # IngestResult shape.
    assert result.status == "ready"
    assert result.fulltext_layer == "zotero_html_snapshot"
    assert result.title == "A Sample Paper on Mitochondria"
    assert result.zotero_item_key == "ABC12345"
    assert result.attachment_type == "text/html"
    assert "Mitochondria are double-membraned" in result.markdown

    # Image src rewritten to vault-relative path (KB/Attachments convention,
    # consistent with existing inbox/pubmed image storage).
    expected_prefix = f"KB/Attachments/zotero/{slug}/_assets"
    assert expected_prefix in result.markdown

    # Assets copied to vault.
    asset = vault_root / "KB" / "Attachments" / "zotero" / slug / "_assets" / "fig1.png"
    assert asset.exists()
    assert asset.read_bytes().startswith(b"\x89PNG")

    # original_url is the zotero:// URI form.
    assert result.original_url == "zotero://select/library/items/ABC12345"


# ---------------------------------------------------------------------------
# Slice 2: PDF fallback path (#390)
# ---------------------------------------------------------------------------


def _make_real_pdf_bytes(text: str) -> bytes:
    """Build minimal real PDF bytes containing ``text`` using PyMuPDF."""
    import fitz

    doc = fitz.Document()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 100), text)
    return doc.tobytes()


def test_sync_zotero_item_pdf_path_end_to_end(tmp_path: Path):
    """PDF-only item → IngestResult with fulltext_layer='zotero_pdf'."""
    pdf_bytes = _make_real_pdf_bytes(
        "Sleep research paper. Contains scientific content on circadian rhythms."
    )

    fixture = init_zotero_lib(tmp_path / "Zotero")
    parent_id = add_journal_article(fixture, item_key="PDF12345", title="A PDF Paper on Sleep")
    add_pdf_attachment(
        fixture,
        parent_item_id=parent_id,
        attachment_key="PDF00001",
        body=pdf_bytes,
    )

    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    result, slug = sync_zotero_item(
        "PDF12345",
        zotero_root=fixture.zotero_root,
        vault_root=vault_root,
    )

    assert slug == "A-PDF-Paper-on-Sleep"
    assert result.status == "ready"
    assert result.fulltext_layer == "zotero_pdf"
    assert result.fulltext_source == "Zotero PDF"
    assert result.attachment_type == "application/pdf"
    assert result.zotero_item_key == "PDF12345"
    assert result.original_url == "zotero://select/library/items/PDF12345"
    assert len(result.markdown) > 0


def test_sync_zotero_item_html_preferred_over_pdf_no_regression(tmp_path: Path):
    """Item with both HTML snapshot and PDF → HTML is still chosen (Q1 lock from Slice 1)."""
    fixture = init_zotero_lib(tmp_path / "Zotero")
    parent_id = add_journal_article(fixture, item_key="BOTH1234", title="A Paper on Mitochondria")
    add_html_snapshot(
        fixture,
        parent_item_id=parent_id,
        attachment_key="HTML0001",
        body=_REAL_SNAPSHOT_HTML,
    )
    add_pdf_attachment(
        fixture,
        parent_item_id=parent_id,
        attachment_key="PDF00002",
    )
    # Fake _assets/ image for the HTML snapshot path.
    (fixture.storage_dir / "HTML0001" / "_assets").mkdir()
    (fixture.storage_dir / "HTML0001" / "_assets" / "fig1.png").write_bytes(
        b"\x89PNG\r\n\x1a\n fake png bytes"
    )

    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    result, slug = sync_zotero_item(
        "BOTH1234",
        zotero_root=fixture.zotero_root,
        vault_root=vault_root,
    )

    assert result.attachment_type == "text/html"
    assert result.fulltext_layer == "zotero_html_snapshot"
