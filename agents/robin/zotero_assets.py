"""Pure asset-handling for Zotero sync (Slice 1 #389).

Two responsibilities, both pure / pure-fs:

- ``copy_assets(snapshot_html_path, vault_assets_dir)`` copies the snapshot's
  sibling ``_assets/`` folder verbatim into the vault. Idempotent.
- ``rewrite_image_paths(md, vault_prefix)`` rewrites Trafilatura's
  ``_assets/foo.png`` references to vault-relative paths so Reader / Obsidian
  can resolve them.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def copy_assets(snapshot_html_path: Path, vault_assets_dir: Path) -> int:
    """Copy ``_assets/`` sibling-folder of ``snapshot.html`` into the vault.

    The source layout is the standard Zotero browser-extension snapshot:
    ``Zotero/storage/{itemKey}/snapshot.html`` + ``Zotero/storage/{itemKey}/_assets/``.

    If no ``_assets/`` directory exists (e.g. Zotero 7+ SingleFile mode where
    everything is inlined as data URIs), the call is a no-op — no target dir
    is created, returns 0.

    Idempotent: re-invocation overwrites existing files in place.

    Returns the number of files copied.
    """
    src_assets = snapshot_html_path.parent / "_assets"
    if not src_assets.is_dir():
        return 0

    vault_assets_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for src_file in src_assets.iterdir():
        if src_file.is_file():
            shutil.copy2(src_file, vault_assets_dir / src_file.name)
            count += 1
    return count


def rewrite_image_paths(md: str, vault_prefix: str) -> str:
    """Rewrite ``_assets/foo.png`` → ``{vault_prefix}/foo.png`` in MD body.

    Handles both markdown image syntax ``![alt](_assets/x.png)`` and raw HTML
    ``<img src="_assets/x.png">`` (Trafilatura sometimes preserves the latter
    when alt text is absent or formatting is complex).

    External URLs (``https://``, ``data:``, etc.) are left alone — only the
    relative ``_assets/`` prefix is replaced, not bare occurrences elsewhere
    in body text.
    """
    return md.replace("](_assets/", f"]({vault_prefix}/").replace(
        'src="_assets/', f'src="{vault_prefix}/'
    )
