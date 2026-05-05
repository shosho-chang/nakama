"""Tests for ``agents/robin/zotero_assets.py`` — Slice 1 #389 Cycles 8-11.

Pure-function module: copy snapshot ``_assets/`` into vault + rewrite MD
image src to vault-relative paths.
"""

from __future__ import annotations

from pathlib import Path

from agents.robin.zotero_assets import copy_assets, rewrite_image_paths


# ---------------------------------------------------------------------------
# copy_assets
# ---------------------------------------------------------------------------


def _seed_snapshot_with_assets(snapshot_dir: Path) -> Path:
    """Build a fake Zotero snapshot folder with 3 asset files.

    Returns the snapshot.html path. ``snapshot_dir`` looks like:
        {snapshot_dir}/snapshot.html
        {snapshot_dir}/_assets/fig1.png
        {snapshot_dir}/_assets/fig2.png
        {snapshot_dir}/_assets/style.css
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_html = snapshot_dir / "snapshot.html"
    snapshot_html.write_text("<html><body>Test</body></html>", encoding="utf-8")
    assets_src = snapshot_dir / "_assets"
    assets_src.mkdir()
    (assets_src / "fig1.png").write_bytes(b"\x89PNG\r\nfake1")
    (assets_src / "fig2.png").write_bytes(b"\x89PNG\r\nfake2")
    (assets_src / "style.css").write_text("body{font:16px}", encoding="utf-8")
    return snapshot_html


def test_copy_assets_copies_all_files_verbatim(tmp_path: Path):
    """Source ``_assets/`` content lands byte-identical in target dir."""
    snapshot_html = _seed_snapshot_with_assets(tmp_path / "zotero_storage" / "ABC123")
    target = tmp_path / "vault" / "Attachments" / "zotero" / "my-slug" / "_assets"

    count = copy_assets(snapshot_html, target)

    assert count == 3
    assert (target / "fig1.png").read_bytes() == b"\x89PNG\r\nfake1"
    assert (target / "fig2.png").read_bytes() == b"\x89PNG\r\nfake2"
    assert (target / "style.css").read_text(encoding="utf-8") == "body{font:16px}"


def test_copy_assets_is_idempotent(tmp_path: Path):
    """Re-running on the same target produces the same state."""
    snapshot_html = _seed_snapshot_with_assets(tmp_path / "zotero_storage" / "ABC123")
    target = tmp_path / "vault" / "Attachments" / "zotero" / "my-slug" / "_assets"

    copy_assets(snapshot_html, target)
    copy_assets(snapshot_html, target)  # second invocation must not error

    files = sorted(p.name for p in target.iterdir())
    assert files == ["fig1.png", "fig2.png", "style.css"]


def test_copy_assets_handles_missing_assets_dir(tmp_path: Path):
    """Snapshot without sibling ``_assets/`` (e.g. SingleFile mode) is a no-op."""
    snapshot_dir = tmp_path / "zotero_storage" / "ABC123"
    snapshot_dir.mkdir(parents=True)
    snapshot_html = snapshot_dir / "snapshot.html"
    snapshot_html.write_text("<html><body>SingleFile inline</body></html>", encoding="utf-8")

    target = tmp_path / "vault" / "Attachments" / "zotero" / "my-slug" / "_assets"

    count = copy_assets(snapshot_html, target)

    assert count == 0
    assert not target.exists()  # no spurious empty dirs


# ---------------------------------------------------------------------------
# rewrite_image_paths
# ---------------------------------------------------------------------------


def test_rewrite_image_paths_replaces_relative_assets_with_vault_prefix():
    """``![](_assets/x.png)`` → ``![](Attachments/zotero/my-slug/_assets/x.png)``."""
    md = "See ![](_assets/fig1.png) and ![](_assets/fig2.png)."
    out = rewrite_image_paths(md, "Attachments/zotero/my-slug/_assets")
    assert out == (
        "See ![](Attachments/zotero/my-slug/_assets/fig1.png) "
        "and ![](Attachments/zotero/my-slug/_assets/fig2.png)."
    )


def test_rewrite_image_paths_leaves_external_urls_alone():
    """https://, http://, data: URLs are not rewritten."""
    md = (
        "Local: ![](_assets/local.png)\n"
        "External: ![](https://example.com/cdn.png)\n"
        "Inline: ![](data:image/png;base64,iVBOR...)"
    )
    out = rewrite_image_paths(md, "Attachments/zotero/my-slug/_assets")
    assert "https://example.com/cdn.png" in out
    assert "data:image/png;base64,iVBOR..." in out
    assert "Attachments/zotero/my-slug/_assets/local.png" in out


def test_rewrite_image_paths_handles_html_img_src_form():
    """Some Trafilatura outputs keep raw ``<img src="_assets/...">`` HTML — rewrite those too."""
    md = '<img src="_assets/fig1.png" alt="figure 1">'
    out = rewrite_image_paths(md, "Attachments/zotero/my-slug/_assets")
    assert 'src="Attachments/zotero/my-slug/_assets/fig1.png"' in out


def test_rewrite_image_paths_no_change_when_no_assets_refs():
    """MD without any ``_assets/`` references is returned verbatim."""
    md = "# Heading\n\nPlain text body, no images."
    assert rewrite_image_paths(md, "Attachments/zotero/my-slug/_assets") == md
