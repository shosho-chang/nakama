"""Integration test for ``agents/robin/zotero_sync.sync_zotero_item`` (Slice 1 #389).

Single happy-path test exercising the full HTML pipeline: fixture SQLite +
real on-disk snapshot + real Trafilatura → IngestResult ready for inbox
writer.
"""

from __future__ import annotations

from pathlib import Path

from agents.robin.zotero_sync import sync_zotero_item
from tests.agents.robin._zotero_fixture import (
    add_html_snapshot,
    add_journal_article,
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
