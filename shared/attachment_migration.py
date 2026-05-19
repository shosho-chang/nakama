"""Inbox → KB attachment migration (ADR-028 §7, PR-A1).

When a markdown file is promoted from ``{inbox}/{slug}.md`` to
``KB/Raw/Articles/{slug}.md`` (or ``KB/Wiki/Sources/{slug}/...md``), the
News Coo image fetcher (``extensions/news-coo/src/vault/imageFetcher.ts``)
has already written companion images to ``{inbox}/attachments/{slug}/``
and rewritten markdown refs to ``attachments/{slug}/img-N.ext``.

Without migration, the promoted markdown's image refs become broken: they
still point at ``attachments/{slug}/...`` which only exists adjacent to the
Inbox file, not adjacent to the KB destination.

This module owns the migration contract:

1. Move ``{inbox}/attachments/{slug}/*`` → ``{vault}/KB/Attachments/{slug}/*``
2. Rewrite markdown refs ``attachments/{slug}/...`` →
   ``KB/Attachments/{slug}/...`` in caller-supplied target markdown files.

Idempotent: re-running over already-migrated state is a no-op (no overwrite,
no failure). Caller invokes once per promoted slug.

Boundaries:
- No vault layout assumptions beyond ``KB/Attachments/{slug}/``.
- No frontmatter parsing — operates on raw markdown bytes.
- No network. No LLM. No subprocess.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from shared.log import get_logger

_logger = get_logger("nakama.shared.attachment_migration")


_REF_RE_TEMPLATE = r"attachments/{slug}/"
"""Match the legacy ref prefix written by News Coo. Slug is escaped into
the regex per call; the surrounding character class is intentionally
permissive (markdown image refs may appear inside ``![alt](...)`` or
``[link](...)``)."""


@dataclass
class AttachmentMigrationResult:
    """Outcome of one slug migration."""

    slug: str
    moved_files: list[str] = field(default_factory=list)
    """Vault-relative posix paths of files now under KB/Attachments/{slug}/."""

    skipped_files: list[str] = field(default_factory=list)
    """Vault-relative posix paths that already existed at target with same
    sha256 (idempotent re-run)."""

    rewritten_markdown: list[str] = field(default_factory=list)
    """Vault-relative paths of markdown files whose refs were rewritten."""

    source_missing: bool = False
    """True iff ``{inbox}/attachments/{slug}/`` did not exist — no-op result."""


def migrate_slug_attachments(
    slug: str,
    inbox_dir: Path,
    vault_root: Path,
    *,
    rewrite_in_files: list[Path] | None = None,
) -> AttachmentMigrationResult:
    """Move attachments for ``slug`` from Inbox to KB and rewrite refs.

    Args:
        slug: source slug (markdown file stem).
        inbox_dir: directory holding the inbox markdown file (e.g.
            ``vault/Inbox/kb`` or ``vault/Inbox/web``). The attachments
            folder is looked up at ``inbox_dir / "attachments" / slug``.
        vault_root: vault root. Target is ``vault_root / "KB" / "Attachments" / slug``.
        rewrite_in_files: target markdown files whose ``attachments/{slug}/``
            refs should be rewritten to ``KB/Attachments/{slug}/``. Files
            that do not exist are silently skipped (logged).

    Returns:
        :class:`AttachmentMigrationResult` describing what moved / was skipped.
        On missing source (``inbox_dir/attachments/{slug}/`` not present)
        returns a result with ``source_missing=True`` and still rewrites
        refs in target files (safe: regex is path-shaped, won't match if
        absent).
    """
    result = AttachmentMigrationResult(slug=slug)
    src_dir = inbox_dir / "attachments" / slug
    dst_dir = vault_root / "KB" / "Attachments" / slug

    if not src_dir.exists() or not src_dir.is_dir():
        result.source_missing = True
        _logger.debug("no attachments to migrate for slug=%r (src=%s)", slug, src_dir)
    else:
        dst_dir.mkdir(parents=True, exist_ok=True)
        for src_file in sorted(src_dir.iterdir()):
            if not src_file.is_file():
                continue
            dst_file = dst_dir / src_file.name
            rel = _vault_rel(vault_root, dst_file)
            if dst_file.exists() and _same_content(src_file, dst_file):
                # Idempotent: target already holds identical bytes. Remove
                # the orphan source file to complete the move semantics.
                try:
                    src_file.unlink()
                except OSError:
                    pass
                result.skipped_files.append(rel)
                continue
            if dst_file.exists():
                # Different content at target — refuse to overwrite. This
                # is the conservative choice; surfacing as a skip lets the
                # caller decide.
                _logger.warning(
                    "attachment migration: dst exists with different content,"
                    " skipping src=%s dst=%s",
                    src_file,
                    dst_file,
                )
                result.skipped_files.append(rel)
                continue
            shutil.copy2(src_file, dst_file)
            try:
                src_file.unlink()
            except OSError as exc:
                _logger.warning("failed to unlink migrated source %s: %s", src_file, exc)
            result.moved_files.append(rel)
        # Try to remove the now-empty source directory; ignore if not empty
        # (other files were written there after we listed).
        try:
            src_dir.rmdir()
        except OSError:
            pass

    if rewrite_in_files:
        pattern = re.compile(_REF_RE_TEMPLATE.format(slug=re.escape(slug)))
        replacement = f"KB/Attachments/{slug}/"
        for md_path in rewrite_in_files:
            if not md_path.exists() or not md_path.is_file():
                _logger.debug("rewrite target missing, skipping: %s", md_path)
                continue
            try:
                text = md_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                _logger.warning("failed to read %s for ref rewrite: %s", md_path, exc)
                continue
            new_text, n = pattern.subn(replacement, text)
            if n > 0:
                tmp = md_path.with_suffix(md_path.suffix + ".tmp")
                tmp.write_text(new_text, encoding="utf-8")
                tmp.replace(md_path)
                rel = _vault_rel(vault_root, md_path)
                result.rewritten_markdown.append(rel)

    return result


def _same_content(a: Path, b: Path) -> bool:
    return _sha256(a) == _sha256(b)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _vault_rel(vault_root: Path, full: Path) -> str:
    """Return posix-style vault-relative path. Falls back to absolute path
    if ``full`` is not under ``vault_root`` (defensive — should not happen
    in normal use)."""
    try:
        rel = full.resolve().relative_to(vault_root.resolve())
    except ValueError:
        return full.as_posix()
    return rel.as_posix()
